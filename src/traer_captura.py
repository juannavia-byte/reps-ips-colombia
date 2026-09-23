"""
Trae a Ariad lo que el equipo capturó desde el navegador.

    PYTHONPATH=src python src/traer_captura.py --dsn "$DSN" \
        --supabase "$SUPABASE_DSN" --simular
    PYTHONPATH=src python src/traer_captura.py --dsn "$DSN" --supabase "$SUPABASE_DSN"

Es la mitad que hace que Ariad siga siendo la fuente de verdad. El formulario
escribe en `captura.persona` y `captura.canal` de Supabase porque tiene que
guardar en el acto, desde un navegador, sin depender de que el Postgres local
esté encendido. Pero ahí no vive la verdad: ahí vive lo último que alguien
tecleó. La verdad se arma acá, cruzada con el REPS y con la lista de
exclusión.

─────────────────────────────────────────────────────────────────────────────
NO SE CONFÍA EN LO QUE MANDÓ EL NAVEGADOR

`valor_norm` lo calcula el cliente para poder deduplicar mientras se escribe,
y acá se vuelve a calcular desde cero con `canonico()`. No es desconfianza del
equipo: es que el navegador es un sitio donde el código se puede cambiar, y un
`valor_norm` mal formado se convertiría en un duplicado permanente en la base
buena. Lo que se guarda es lo que decide el motor.
─────────────────────────────────────────────────────────────────────────────

Qué se marca y qué no
---------------------
Lo bajado se marca con `bajado_en` en Supabase, y no se borra. Borrarlo haría
que dos corridas seguidas no pudieran distinguir «ya lo bajé» de «nunca
existió», y se perdería el rastro de quién capturó qué — que es justo lo que
hay que poder contestar si alguien pregunta por su dato.

Volver a correrlo es seguro: lo ya bajado se salta, y lo que cambió en
Supabase desde entonces vuelve a entrar por el ON CONFLICT.
"""
from __future__ import annotations

import argparse
import json

import psycopg

# La normalización vive en `importar_hoja` y se importa en vez de copiarse.
# Son las mismas reglas —el mismo `profile.php?id=`, el mismo
# `co.linkedin.com`— y tenerlas dos veces garantiza que un día diverjan y que
# el mismo Facebook entre distinto según por dónde se capturó.
from importar_hoja import canonico
from enriquecer import (ambito_correo, clasificar, clave_nombre, parece_persona,
                        unir_cargos)

# Ámbito por tipo, cuando el capturador no dijo otra cosa. El correo no está:
# lo deduce `ambito_correo` mirando el buzón.
AMBITO = {
    "telefono": "area", "whatsapp": "personal", "linkedin": "profesional",
    "facebook": "personal", "instagram": "personal", "x": "personal",
    "tiktok": "personal", "telegram": "personal", "web": "profesional",
}


def confianza(confirmado: bool):
    """
    Un dato que alguien vio entra verificado con 90; uno que dedujo, inferido
    con 40. Esta traducción vive sólo acá a propósito: si el navegador
    guardara ya la confianza, habría dos respuestas posibles a la misma
    pregunta según por dónde entró el dato.
    """
    return ("verificado", 90) if confirmado else ("inferido", 40)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True, help="Postgres local")
    ap.add_argument("--supabase", required=True, help="conexión directa a Supabase")
    ap.add_argument("--todo", action="store_true",
                    help="reprocesa también lo ya bajado")
    ap.add_argument("--simular", action="store_true")
    args = ap.parse_args()

    # `retirado is false` en las dos consultas: lo que alguien quitó desde el
    # navegador no se baja. Lo que ya se había bajado ANTES de retirarlo se
    # resuelve aparte, marcándolo obsoleto en Ariad — ver más abajo.
    filtro = ("where retirado is false" if args.todo
              else "where bajado_en is null and retirado is false")
    sb = psycopg.connect(args.supabase)
    with sb.cursor() as cur:
        cur.execute(f"""
            SELECT id, nit, nombre_clave, nombre, cargo, confirmado, fuente, notas
              FROM captura.persona {filtro} ORDER BY creado
        """)
        personas = cur.fetchall()
        cur.execute(f"""
            SELECT id, persona_id, nit, tipo, valor, ambito, confirmado
              FROM captura.canal {filtro} ORDER BY creado
        """)
        canales = cur.fetchall()
        # Lo retirado DESPUÉS de haberse bajado. Ariad ya lo tiene, así que
        # saltárselo no basta: hay que ir a decirle que ya no vale.
        cur.execute("""
            SELECT nit, nombre_clave, nombre FROM captura.persona
             WHERE retirado is true AND bajado_en is not null
        """)
        retiradas_p = cur.fetchall()
        cur.execute("""
            SELECT nit, tipo, valor FROM captura.canal
             WHERE retirado is true AND bajado_en is not null
        """)
        retirados_c = cur.fetchall()

    print(f"── Supabase: {len(personas)} personas · {len(canales)} canales"
          f"{'' if args.todo else ' sin bajar'}"
          + (f" · {len(retiradas_p) + len(retirados_c)} retirados ya bajados"
             if retiradas_p or retirados_c else ""))
    if not personas and not canales and not retiradas_p and not retirados_c:
        sb.close()
        return 0

    cx = psycopg.connect(args.dsn)
    with cx.cursor() as cur:
        cur.execute("SELECT numero_identificacion, id FROM reps.prestador")
        por_nit = dict(cur.fetchall())
        cur.execute("SELECT coalesce(prestador_id,0), coalesce(nombre_clave,''), "
                    "coalesce(valor_norm,'') FROM enriquecimiento.exclusion")
        ex_pid, ex_nom, ex_val = set(), set(), set()
        for p, n, v in cur.fetchall():
            if p:
                ex_pid.add(p)
            if n:
                ex_nom.add(n)
            if v:
                ex_val.add(v)

    nuevas = nuevos = obsoletas = obsoletos = 0
    ids_persona: dict[str, int] = {}     # uuid de Supabase → id local
    sin_cruce, excluidos, ilegibles, no_persona = set(), 0, [], []
    bajadas_p, bajados_c = [], []

    with cx.cursor() as cur:
        for uid, nit, nclave, nombre, cargo, conf, fuente, notas in personas:
            pid = por_nit.get((nit or "").strip())
            if pid is None:
                sin_cruce.add((nombre, nit))
                continue
            if pid in ex_pid:
                excluidos += 1
                continue
            # El navegador ya calcula la clave, y acá se recalcula: es la que
            # decide si dos grafías son la misma persona, y de esa decisión
            # depende que la base no se llene de duplicados.
            k = clave_nombre(nombre) or (nclave or "").strip()
            if not k or k in ex_nom:
                excluidos += 1
                continue
            if not parece_persona(nombre):
                no_persona.append(nombre)
                continue

            estado, c = confianza(conf)

            # 🔴 EL CARGO NO REEMPLAZA AL QUE YA HAY: SE UNE.
            #
            # La misma persona es representante legal Y gerente general en
            # media Colombia, y el formulario del tablero ya lo guarda así
            # («A · B»). Si acá se reemplazara —que es lo que hacía
            # `cargo = coalesce(nullif(excluded.cargo,''), …)` con un cargo
            # suelto— bajar la captura borraría el que encontró el motor, y la
            # ficha perdería la mitad de lo que sabe de esa persona sin que
            # nada lo dijera.
            #
            # `clasificar` recorre los patrones en orden de tier y devuelve el
            # primero que casa, así que la unión se clasifica por el cargo más
            # alto: «Gerente general · Jefe de calidad» es tier 1, no tier 3.
            cur.execute("""SELECT id, cargo FROM enriquecimiento.persona
                           WHERE prestador_id=%s AND nombre_clave=%s""", (pid, k))
            hay = cur.fetchone()
            cargo = unir_cargos(hay[1] if hay else None, cargo)
            tier, cat = clasificar(cargo or "")

            if args.simular:
                nuevas += 0 if hay else 1
                if hay:
                    ids_persona[str(uid)] = hay[0]
                continue

            cur.execute("""
                INSERT INTO enriquecimiento.persona
                  (prestador_id, nombre_clave, nombre, cargo, cargo_categoria,
                   tier, estado, confianza, ultima_verificacion, notas)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now(), %s)
                ON CONFLICT (prestador_id, nombre_clave) DO UPDATE SET
                  -- La mejor grafía gana: REPS y RUES escriben en mayúsculas
                  -- y es lo que se lee en la ficha y encabeza un correo.
                  nombre = CASE
                    WHEN excluded.nombre = upper(excluded.nombre)
                     AND enriquecimiento.persona.nombre
                         <> upper(enriquecimiento.persona.nombre)
                    THEN enriquecimiento.persona.nombre
                    ELSE excluded.nombre END,
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
            """, (pid, k, nombre, cargo, cat, tier, estado, c, notas))
            lid, insertada = cur.fetchone()
            ids_persona[str(uid)] = lid
            nuevas += 1 if insertada else 0
            cur.execute("""INSERT INTO enriquecimiento.evidencia
                (persona_id, fuente, referencia, crudo) VALUES (%s,'manual',%s,%s)""",
                (lid, fuente or "captura en Ariad",
                 json.dumps({"nombre": nombre, "cargo": cargo,
                             "captura_id": str(uid)}, ensure_ascii=False)))
            bajadas_p.append(uid)

        for cid, per_uid, nit, tipo, valor, ambito, conf in canales:
            pid = por_nit.get((nit or "").strip())
            if pid is None:
                sin_cruce.add(("(canal)", nit))
                continue
            if pid in ex_pid:
                excluidos += 1
                continue
            mostrado, vn = canonico(tipo, valor)
            if not vn:
                ilegibles.append(f"{tipo}: {valor}")
                continue
            if vn in ex_val:
                excluidos += 1
                continue
            amb = ambito or (ambito_correo(mostrado) if tipo == "correo"
                             else AMBITO.get(tipo, "profesional"))
            estado, c = confianza(conf)
            lid = ids_persona.get(str(per_uid)) if per_uid else None

            if args.simular:
                cur.execute("""SELECT id FROM enriquecimiento.canal
                               WHERE prestador_id=%s AND tipo=%s AND valor_norm=%s""",
                            (pid, tipo, vn))
                nuevos += 0 if cur.fetchone() else 1
                continue

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
                RETURNING id, (xmax = 0) AS insertado
            """, (lid, pid, tipo, mostrado, vn, amb, estado, c))
            canal_id, insertado = cur.fetchone()
            nuevos += 1 if insertado else 0
            cur.execute("""INSERT INTO enriquecimiento.evidencia
                (canal_id, fuente, referencia, crudo) VALUES (%s,'manual',%s,%s)""",
                (canal_id, "captura en Ariad",
                 json.dumps({tipo: valor, "captura_id": str(cid)}, ensure_ascii=False)))
            bajados_c.append(cid)

        # ── Lo retirado que YA estaba bajado ────────────────────────────────
        #
        # Saltárselo no basta: Ariad ya lo tiene, y seguiría saliendo en la
        # ficha y en las exportaciones como si nadie lo hubiera quitado. Se
        # marca `obsoleto`, que es el estado que las dos tablas ya tenían para
        # esto y que `build_tablero.py` filtra al construir el payload.
        #
        # No se borra, por lo mismo que no se borra en Supabase: un DELETE
        # dejaría la evidencia apuntando a una fila inexistente y perdería el
        # rastro de que el dato existió y alguien lo quitó. Un borrado de
        # verdad —el que pide alguien invocando habeas data— pasa por la lista
        # de exclusión, que es donde queda constancia de la solicitud.
        for r_nit, r_clave, r_nombre in retiradas_p:
            r_pid = por_nit.get((r_nit or "").strip())
            if r_pid is None:
                continue
            k = clave_nombre(r_nombre) or (r_clave or "").strip()
            if not k:
                continue
            # El simulacro cuenta lo que CAMBIARÍA, no los candidatos: lo que
            # ya está obsoleto de una corrida anterior no vuelve a contarse, o
            # el simulacro diría siempre más de lo que la corrida real hace.
            if args.simular:
                cur.execute("""SELECT 1 FROM enriquecimiento.persona
                                WHERE prestador_id=%s AND nombre_clave=%s
                                  AND estado <> 'obsoleto'""", (r_pid, k))
                obsoletas += 1 if cur.fetchone() else 0
                continue
            cur.execute("""UPDATE enriquecimiento.persona
                              SET estado='obsoleto', ultima_verificacion=now()
                            WHERE prestador_id=%s AND nombre_clave=%s
                              AND estado <> 'obsoleto'""", (r_pid, k))
            obsoletas += cur.rowcount

        for r_nit, r_tipo, r_valor in retirados_c:
            r_pid = por_nit.get((r_nit or "").strip())
            if r_pid is None:
                continue
            _, r_vn = canonico(r_tipo, r_valor)
            if not r_vn:
                continue
            if args.simular:
                cur.execute("""SELECT 1 FROM enriquecimiento.canal
                                WHERE prestador_id=%s AND tipo=%s AND valor_norm=%s
                                  AND estado <> 'obsoleto'""", (r_pid, r_tipo, r_vn))
                obsoletos += 1 if cur.fetchone() else 0
                continue
            cur.execute("""UPDATE enriquecimiento.canal
                              SET estado='obsoleto', ultima_verificacion=now()
                            WHERE prestador_id=%s AND tipo=%s AND valor_norm=%s
                              AND estado <> 'obsoleto'""", (r_pid, r_tipo, r_vn))
            obsoletos += cur.rowcount

    if not args.simular:
        cx.commit()
        # Sólo después de que el commit local haya ido bien. Marcarlo antes
        # dejaría datos marcados como bajados que no llegaron a ninguna parte
        # si el commit fallara, y nadie volvería a mirarlos.
        with sb.cursor() as cur:
            if bajadas_p:
                cur.execute("UPDATE captura.persona SET bajado_en = now() "
                            "WHERE id = ANY(%s)", (bajadas_p,))
            if bajados_c:
                cur.execute("UPDATE captura.canal SET bajado_en = now() "
                            "WHERE id = ANY(%s)", (bajados_c,))
        sb.commit()
        with cx.cursor() as cur:
            cur.execute("""
                INSERT INTO enriquecimiento.corrida
                  (paso, alcance, empresas, personas_nuevas, canales_nuevos,
                   peticiones, terminada)
                VALUES ('traer_captura','supabase',%s,%s,%s,0, now())
            """, (len({p[1] for p in personas}), nuevas, nuevos))
        cx.commit()

    cx.close()
    sb.close()

    marca = "[simulacro] " if args.simular else ""
    print(f"\n── {marca}resultado")
    print(f"   personas nuevas en Ariad   {nuevas}")
    print(f"   canales nuevos en Ariad    {nuevos}")
    if obsoletas or obsoletos:
        print(f"   retirados → obsoletos      {obsoletas} personas · {obsoletos} canales")
    if excluidos:
        print(f"   saltados por exclusión     {excluidos}")
    if no_persona:
        print(f"\n⚠ {len(no_persona)} no parecen nombres de persona, no se bajaron:")
        for n in no_persona[:10]:
            print(f"   {n}")
    if ilegibles:
        print(f"\n⚠ {len(ilegibles)} valores que no se pudieron normalizar:")
        for v in ilegibles[:10]:
            print(f"   {v}")
    if sin_cruce:
        print(f"\n⚠ {len(sin_cruce)} sin prestador en el REPS:")
        for nom, nit in sorted(sin_cruce)[:10]:
            print(f"   {nit}  {nom}")
    if not args.simular:
        print("\n   para verlo en Ariad: python src/build_tablero.py --dsn \"$DSN\" "
              "&& python src/push_tablero.py …")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
