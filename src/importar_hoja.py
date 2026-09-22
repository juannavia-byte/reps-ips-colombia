"""
La ruta de vuelta: de la hoja de captura llena a Ariad.

    PYTHONPATH=src python src/importar_hoja.py --dsn "$DSN" \
        --entrada dist/captura.csv --simular
    PYTHONPATH=src python src/importar_hoja.py --dsn "$DSN" \
        --entrada dist/captura.csv

Es la mitad que faltaba. `hoja_investigacion.py` y `hoja_enriquecimiento.py`
generan hojas que nadie lee de vuelta: lo que se escribe en ellas vive en un
CSV en `dist/` y desaparece el día que se regenera. El celular que costó tres
llamadas tiene que terminar en la base o no existe.

─────────────────────────────────────────────────────────────────────────────
IDEMPOTENTE, COMO EL RESTO DEL MOTOR

Volver a correrlo sobre la misma hoja actualiza en vez de duplicar, por
(prestador_id, nombre_clave) en personas y por (prestador_id, tipo,
valor_norm) en canales. Se puede importar una hoja a medio llenar, seguir
llenándola y volver a importarla sin limpiar nada.
─────────────────────────────────────────────────────────────────────────────

Qué decide la confianza
-----------------------
La columna `Confirmado` y nada más:

  si (o vacío)   verificado, confianza 90 — se vio publicado o lo dijeron
  no             inferido,   confianza 40 — se dedujo

Teclear un dato no lo verifica. El esquema es explícito en que un correo no se
marca verificado por parecer correcto, y si todo lo escrito a mano entrara
como verificado, «verificado» dejaría de significar algo en la base entera.

Filas sin nombre
----------------
No se descartan: sus canales entran a nivel de empresa, con `persona_id` nulo.
El conmutador y el buzón de contratación existen sin que se sepa quién los
atiende y son perfectamente utilizables — la tabla `canal` lo contempla desde
el primer día y sería un desperdicio tirarlos por no traer persona.

Lo que este importador rechaza
------------------------------
- NIT que no cruza con ningún prestador del REPS: se lista al final, no se
  inventa la empresa.
- Nombres que son razones sociales («CLINICA DEL NORTE SAS»), por la misma
  vía que `enriquecer.py` los filtra de SECOP.
- Todo lo que esté en la lista de exclusión, por NIT, por nombre o por dato.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

import psycopg

from enriquecer import ambito_correo, clasificar, clave_nombre, parece_persona

# Columna de la hoja → (tipo de canal, ámbito). El ámbito va fijo por columna
# salvo en el correo, donde se deduce del buzón: `contratacion@` es de área
# aunque lo haya apuntado alguien junto a una persona.
#
# `Celular` y `Fijo` caen los dos en `telefono` — el tipo dice POR DÓNDE se
# contacta y el ámbito dice quién atiende. Es la misma decisión que toma el
# puente de HubSpot con `phone` y `mobilephone`.
CANALES = {
    "Correo":    ("correo", None),
    "Celular":   ("telefono", "personal"),
    "Fijo":      ("telefono", "area"),
    "WhatsApp":  ("whatsapp", "personal"),
    "LinkedIn":  ("linkedin", "profesional"),
    "Facebook":  ("facebook", "personal"),
    "Instagram": ("instagram", "personal"),
    "X":         ("x", "personal"),
    "TikTok":    ("tiktok", "personal"),
    "Telegram":  ("telegram", "personal"),
}

# Dónde vive cada handle. Se guarda la URL completa y no el «@usuario» porque
# el perfil de Ariad pinta el canal como enlace: un handle suelto no es
# clicable y obliga a copiar, pegar y adivinar la red.
SITIOS = {
    "linkedin":  "https://www.linkedin.com/in/%s",
    "facebook":  "https://www.facebook.com/%s",
    "instagram": "https://www.instagram.com/%s",
    "x":         "https://x.com/%s",
    "tiktok":    "https://www.tiktok.com/@%s",
    "telegram":  "https://t.me/%s",
}

# Los dominios de cada red, para reconocer una URL pegada. LinkedIn lleva el
# `/in/` aparte porque `/company/` es otra cosa y no es una persona.
DOMINIOS = {
    "linkedin":  r"linkedin\.com/in/",
    "facebook":  r"(?:facebook|fb)\.com/",
    "instagram": r"instagram\.com/",
    "x":         r"(?:twitter|x)\.com/",
    "tiktok":    r"tiktok\.com/@?",
    "telegram":  r"(?:t\.me|telegram\.me)/",
}

SI = {"si", "sí", "s", "yes", "y", "1", "true", "x", ""}
NO = {"no", "n", "0", "false"}


def handle(tipo: str, v: str) -> str:
    """
    El identificador dentro de la red, venga como venga.

    Se apunta de tres formas distintas según lo que haya a mano: «@anaruiz»
    copiado del perfil, «facebook.com/anaruiz» tecleado de memoria, o la URL
    entera con sus parámetros de campaña pegada desde el navegador. Las tres
    son la misma persona y tienen que colapsar en la misma clave, o el mismo
    Facebook entra tres veces en la base.
    """
    v = (v or "").strip()
    if not v:
        return ""
    v = re.sub(r"[?#].*$", "", v)                      # utm_source y compañía
    v = re.sub(r"^https?://", "", v, flags=re.I)
    v = re.sub(r"^www\.", "", v, flags=re.I)
    dom = DOMINIOS.get(tipo)
    if dom:
        v = re.sub(r"^" + dom, "", v, flags=re.I)
    return v.strip("/@ ").lower()


def canonico(tipo: str, v: str):
    """(valor a mostrar, valor normalizado para deduplicar). ('','') si no sirve."""
    v = (v or "").strip()
    if not v:
        return "", ""
    if tipo == "correo":
        v = v.lower()
        # Sintaxis, no existencia. Que el buzón exista no lo prueba nada de
        # lo que se pueda hacer desde acá sin quemar el dominio propio.
        return (v, v) if re.match(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", v) else ("", "")
    if tipo in ("telefono", "whatsapp"):
        d = re.sub(r"\D", "", v)
        if len(d) < 7:                                 # ni un fijo cabe en menos
            return "", ""
        return v, d[-10:]
    h = handle(tipo, v)
    if not h:
        return "", ""
    return SITIOS[tipo] % h, h


def leer(ruta: Path) -> list[dict]:
    with ruta.open(newline="", encoding="utf-8-sig") as fh:
        muestra = fh.read(4096)
        fh.seek(0)
        # La hoja sale con ';' pero vuelve de Google Sheets con ','. Adivinarlo
        # evita que una exportación distinta produzca una sola columna gigante
        # y cero filas importadas sin decir por qué.
        sep = ";" if muestra.count(";") >= muestra.count(",") else ","
        return list(csv.DictReader(fh, delimiter=sep))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--entrada", default="dist/captura.csv")
    ap.add_argument("--simular", action="store_true")
    args = ap.parse_args()

    filas = leer(Path(args.entrada))
    if not filas:
        print(f"⚠ {args.entrada} no tiene filas")
        return 1
    if "NIT" not in filas[0]:
        print(f"⚠ {args.entrada} no tiene columna NIT. ¿Es una hoja de captura?")
        return 1

    cx = psycopg.connect(args.dsn)
    with cx.cursor() as cur:
        cur.execute("SELECT numero_identificacion, id FROM reps.prestador")
        por_nit = dict(cur.fetchall())
        cur.execute("SELECT coalesce(prestador_id,0), coalesce(nombre_clave,''), "
                    "coalesce(valor_norm,'') FROM enriquecimiento.exclusion")
        excl_pid, excl_nombre, excl_valor = set(), set(), set()
        for p, n, v in cur.fetchall():
            if p:
                excl_pid.add(p)
            if n:
                excl_nombre.add(n)
            if v:
                excl_valor.add(v)

    personas_nuevas = personas_tocadas = 0
    canales_nuevos = canales_tocados = canales_empresa = 0
    sin_cruce, no_persona, excluidas, ilegibles = set(), [], 0, []
    # Un canal ya tomado por otra persona de la misma empresa. Pasa con el
    # conmutador: es real, y hay que decirlo en vez de contarlo como nuevo.
    compartidos = []
    vacias = 0

    # Sólo para el simulacro. Sin escribir nada, la segunda fila de la misma
    # persona vuelve a no encontrarla en la base y se cuenta otra vez: la misma
    # persona apuntada con las dos grafías —«ANA LUCIA RUIZ» y «RUIZ ANA
    # LUCIA»— salía como dos nuevas. El simulacro es justo lo que se mira para
    # decidir si vale la pena importar; inflado hacia arriba no sirve de nada.
    vistos_persona, vistos_canal = set(), set()

    with cx.cursor() as cur:
        for n, fila in enumerate(filas, start=2):      # 2 = primera fila tras el encabezado
            nit = (fila.get("NIT") or "").strip()
            empresa = (fila.get("Empresa") or "").strip()
            nombre = (fila.get("Nombre") or "").strip()
            datos = {col: (fila.get(col) or "").strip() for col in CANALES}

            if not nit or not (nombre or any(datos.values())):
                vacias += 1
                continue
            pid = por_nit.get(nit)
            if pid is None:
                sin_cruce.add((empresa, nit))
                continue
            if pid in excl_pid:
                excluidas += 1
                continue

            conf = (fila.get("Confirmado") or "").strip().lower()
            if conf in NO:
                estado, confianza = "inferido", 40
            elif conf in SI:
                estado, confianza = "verificado", 90
            else:
                ilegibles.append((n, fila.get("Confirmado")))
                estado, confianza = "inferido", 40

            fuente_txt = (fila.get("Fuente") or "").strip() or "hoja de captura"
            crudo = {k: v for k, v in fila.items()
                     if v and not k.startswith(("1·", "2·", "3·", "4·"))}

            # ── La persona
            #
            # `hubo_persona` no es `pers_id is not None`: en simulacro no se
            # inserta nada, así que una persona nueva y legítima deja pers_id
            # en None. Sin el flag, el simulacro reportaría todos sus canales
            # como si fueran a quedar colgando de la empresa.
            pers_id = None
            hubo_persona = False
            if nombre:
                k = clave_nombre(nombre)
                if not k:
                    no_persona.append((n, nombre))
                elif k in excl_nombre:
                    excluidas += 1
                    continue
                elif not parece_persona(nombre):
                    # Una razón social en la columna del nombre. Guardarla
                    # ensucia justo la lista que luego se mira a ojo para
                    # decidir a quién llamar.
                    no_persona.append((n, nombre))
                else:
                    cargo = (fila.get("Cargo") or "").strip() or None
                    tier, cat = clasificar(cargo or "")
                    hubo_persona = True
                    if args.simular:
                        cur.execute("""SELECT id FROM enriquecimiento.persona
                                       WHERE prestador_id=%s AND nombre_clave=%s""", (pid, k))
                        hay = cur.fetchone()
                        if (pid, k) not in vistos_persona:
                            vistos_persona.add((pid, k))
                            personas_tocadas += 1
                            personas_nuevas += 0 if hay else 1
                        pers_id = hay[0] if hay else None
                    else:
                        cur.execute("""
                            INSERT INTO enriquecimiento.persona
                              (prestador_id, nombre_clave, nombre, cargo,
                               cargo_categoria, tier, estado, confianza,
                               ultima_verificacion, notas)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now(), %s)
                            ON CONFLICT (prestador_id, nombre_clave) DO UPDATE SET
                              -- `nombre` es «la mejor grafía vista», dice el
                              -- esquema, y las fuentes oficiales gritan:
                              -- REPS y RUES escriben «RUIZ BELTRAN ANA
                              -- LUCIA». Si lo nuevo viene todo en mayúsculas
                              -- y lo guardado no, se conserva lo guardado.
                              -- Es lo que se lee en la ficha y lo que se usa
                              -- para encabezar un correo.
                              nombre = CASE
                                WHEN excluded.nombre = upper(excluded.nombre)
                                 AND enriquecimiento.persona.nombre
                                     <> upper(enriquecimiento.persona.nombre)
                                THEN enriquecimiento.persona.nombre
                                ELSE excluded.nombre END,
                              -- El cargo NO sigue esa regla: acá el último sí
                              -- gana, porque volver a importar corrigiendo un
                              -- cargo es precisamente para que gane.
                              cargo = coalesce(nullif(excluded.cargo,''),
                                               enriquecimiento.persona.cargo),
                              cargo_categoria = coalesce(excluded.cargo_categoria,
                                                         enriquecimiento.persona.cargo_categoria),
                              tier = coalesce(excluded.tier, enriquecimiento.persona.tier),
                              estado = excluded.estado,
                              confianza = greatest(enriquecimiento.persona.confianza,
                                                   excluded.confianza),
                              notas = coalesce(nullif(excluded.notas,''),
                                               enriquecimiento.persona.notas),
                              ultima_verificacion = now()
                            RETURNING id, (xmax = 0) AS insertada
                        """, (pid, k, nombre, cargo, cat, tier, estado, confianza,
                              (fila.get("Notas") or "").strip() or None))
                        pers_id, insertada = cur.fetchone()
                        personas_tocadas += 1
                        personas_nuevas += 1 if insertada else 0
                        cur.execute("""INSERT INTO enriquecimiento.evidencia
                            (persona_id, fuente, referencia, crudo)
                            VALUES (%s,'manual',%s,%s)""",
                            (pers_id, fuente_txt, json.dumps(crudo, ensure_ascii=False)))

            # ── Los canales
            for col, (tipo, ambito_fijo) in CANALES.items():
                bruto = datos[col]
                if not bruto:
                    continue
                valor, vn = canonico(tipo, bruto)
                if not vn:
                    ilegibles.append((n, f"{col}: {bruto}"))
                    continue
                if vn in excl_valor:
                    excluidas += 1
                    continue
                ambito = ambito_fijo or ambito_correo(valor)
                nuevo_en_la_hoja = (pid, tipo, vn) not in vistos_canal
                if not hubo_persona and nuevo_en_la_hoja:
                    canales_empresa += 1

                if args.simular:
                    if nuevo_en_la_hoja:
                        vistos_canal.add((pid, tipo, vn))
                        cur.execute("""SELECT id FROM enriquecimiento.canal
                                       WHERE prestador_id=%s AND tipo=%s AND valor_norm=%s""",
                                    (pid, tipo, vn))
                        canales_tocados += 1
                        canales_nuevos += 0 if cur.fetchone() else 1
                    continue
                vistos_canal.add((pid, tipo, vn))

                cur.execute("""
                    INSERT INTO enriquecimiento.canal
                      (persona_id, prestador_id, tipo, valor, valor_norm, ambito,
                       estado, confianza, ultima_verificacion)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now())
                    ON CONFLICT (prestador_id, tipo, valor_norm) DO UPDATE SET
                      persona_id = coalesce(enriquecimiento.canal.persona_id,
                                            excluded.persona_id),
                      ambito = excluded.ambito,
                      estado = excluded.estado,
                      confianza = greatest(enriquecimiento.canal.confianza,
                                           excluded.confianza),
                      ultima_verificacion = now()
                    RETURNING id, (xmax = 0) AS insertado, persona_id
                """, (pers_id, pid, tipo, valor, vn, ambito, estado, confianza))
                cid, insertado, duenio = cur.fetchone()
                canales_tocados += 1
                canales_nuevos += 1 if insertado else 0
                # El UNIQUE de `canal` no incluye persona_id: un mismo número
                # no puede colgar de dos personas. Se queda con la primera y
                # acá se deja constancia en vez de fingir que se guardó.
                if pers_id is not None and duenio != pers_id:
                    compartidos.append((n, nombre, col, valor))
                cur.execute("""INSERT INTO enriquecimiento.evidencia
                    (canal_id, fuente, referencia, crudo) VALUES (%s,'manual',%s,%s)""",
                    (cid, fuente_txt, json.dumps({col: bruto}, ensure_ascii=False)))

        if not args.simular:
            cur.execute("""
                INSERT INTO enriquecimiento.corrida
                  (paso, alcance, empresas, personas_nuevas, canales_nuevos,
                   peticiones, terminada, notas)
                VALUES ('importar_hoja', %s, %s, %s, %s, 0, now(), %s)
            """, (args.entrada,
                  len({f.get("NIT") for f in filas if (f.get("NIT") or "").strip()}),
                  personas_nuevas, canales_nuevos,
                  f"{len(filas)} filas leídas"))
            cx.commit()

    cx.close()

    marca = "[simulacro] " if args.simular else ""
    print(f"\n── {marca}resultado")
    print(f"   filas leídas                {len(filas)}  ({vacias} en blanco)")
    print(f"   personas escritas           {personas_tocadas}  ({personas_nuevas} nuevas)")
    print(f"   canales escritos            {canales_tocados}  ({canales_nuevos} nuevos)")
    if canales_empresa:
        print(f"   canales sin persona         {canales_empresa}  (quedan a nivel de empresa)")
    if excluidas:
        print(f"   saltadas por exclusión      {excluidas}")

    if no_persona:
        print(f"\n⚠ {len(no_persona)} nombres que no parecen de una persona — no se "
              f"guardaron como tal, pero sus canales sí entraron a nivel de empresa:")
        for n, nom in no_persona[:10]:
            print(f"   fila {n}: {nom}")
    if ilegibles:
        print(f"\n⚠ {len(ilegibles)} valores ilegibles, se saltaron:")
        for n, txt in ilegibles[:10]:
            print(f"   fila {n}: {txt}")
    if compartidos:
        print(f"\n⚠ {len(compartidos)} canales ya colgaban de otra persona de la "
              f"misma empresa (el conmutador, casi siempre). Siguen con la primera:")
        for n, nom, col, val in compartidos[:10]:
            print(f"   fila {n}: {col} {val} — se pidió para {nom}")
    if sin_cruce:
        print(f"\n⚠ {len(sin_cruce)} NIT sin prestador en el REPS, no se importaron:")
        for emp, nit in sorted(sin_cruce)[:10]:
            print(f"   {nit}  {emp}")

    if not args.simular:
        print("\n   para verlo en Ariad: python src/build_tablero.py --dsn \"$DSN\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
