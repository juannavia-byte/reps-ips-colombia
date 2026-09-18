"""
Trae de vuelta a Ariad lo que se enriqueció a mano en HubSpot.

    python src/traer_hubspot.py --dsn "$DSN" --token "$HUBSPOT_TOKEN"
    python src/traer_hubspot.py --dsn "$DSN" --token "$HUBSPOT_TOKEN" --simular

Es la mitad que hace que Ariad siga siendo la fuente de verdad. Sin esto, el
celular del SST que costó tres llamadas vive solo en el CRM, y el día que se
cambie de CRM se pierde.

─────────────────────────────────────────────────────────────────────────────
NO SE REIMPORTA LO QUE SALIÓ DE ACÁ

El empujón lleva a HubSpot los canales que Ariad ya tenía. Si se leyeran de
vuelta sin más, entrarían marcados como `manual` — y un dato de SECOP acabaría
figurando como si alguien lo hubiera conseguido llamando. Se compara contra lo
que ya existe: si el valor ya está, se ignora; si es nuevo, es enriquecimiento
de verdad.

Por eso el conteo de «nuevos» es el único que importa, y suele ser mucho menor
que el de canales leídos.
─────────────────────────────────────────────────────────────────────────────

Un dato escrito por una persona entra con confianza 90 y estado `verificado`:
alguien lo confirmó hablando, que es más de lo que puede decir cualquier
fuente automática de este repo.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import psycopg

API = "https://api.hubapi.com"

# Propiedad de HubSpot → tipo de canal en Ariad. `mobilephone` y `phone` caen
# los dos en `telefono`: en Ariad el tipo dice POR DÓNDE se contacta, no qué
# etiqueta le puso HubSpot.
CANALES = {
    "email": ("correo", "profesional"),
    "phone": ("telefono", "area"),
    "mobilephone": ("telefono", "personal"),
    "hs_whatsapp_phone_number": ("whatsapp", "personal"),
    "hs_linkedin_url": ("linkedin", "profesional"),
    "hs_facebookid": ("facebook", "personal"),
    "proa_instagram": ("instagram", "personal"),
}

PROPS_CONTACTO = ["firstname", "lastname", "jobtitle", "proa_rol_decision",
                  "lastmodifieddate", *CANALES]
PROPS_EMPRESA = ["name", "proa_nit", "proa_arl_actual", "proa_clase_riesgo_arl",
                 "proa_ariad_mes_aniversario_arl", "lifecyclestage"]


def hubspot(token: str, ruta: str, metodo: str = "GET", cuerpo=None, reintentos: int = 4):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(API + ruta, data=datos, method=metodo, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json"})
    for intento in range(reintentos):
        try:
            with urllib.request.urlopen(req, timeout=40) as fh:
                return json.load(fh)
        except urllib.error.HTTPError as e:
            # 100 req/10 s en Starter. El 429 es esperable y se reintenta.
            if e.code in (429, 502, 503) and intento < reintentos - 1:
                time.sleep(2 ** intento)
                continue
            raise
    return {}


def sin_tildes(s: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()


def clave_nombre(s: str) -> str:
    """La misma normalización que usa `enriquecer.py`: tokens ordenados.

    Tiene que ser idéntica o una persona escrita a mano en HubSpot entraría como
    un duplicado de la que ya está en Ariad.
    """
    return " ".join(sorted(re.findall(r"[a-z]{2,}", sin_tildes(s or "").lower())))


def norm_valor(tipo: str, v: str) -> str:
    v = (v or "").strip().lower()
    if tipo in ("telefono", "whatsapp"):
        d = re.sub(r"\D", "", v)
        return d[-10:] if len(d) >= 10 else d
    return v.rstrip("/")


def traer_empresas(token: str) -> list[dict]:
    salida, despues = [], None
    while True:
        cuerpo = {"limit": 100, "properties": PROPS_EMPRESA,
                  "filterGroups": [{"filters": [
                      {"propertyName": "proa_nit", "operator": "HAS_PROPERTY"}]}]}
        if despues:
            cuerpo["after"] = despues
        d = hubspot(token, "/crm/v3/objects/companies/search", "POST", cuerpo)
        salida.extend(d.get("results", []))
        despues = (d.get("paging") or {}).get("next", {}).get("after")
        if not despues:
            return salida
        time.sleep(0.2)


def contactos_de(token: str, empresa_id: str) -> list[dict]:
    d = hubspot(token, f"/crm/v4/objects/companies/{empresa_id}/associations/contacts?limit=100")
    ids = [r["toObjectId"] for r in d.get("results", [])]
    if not ids:
        return []
    r = hubspot(token, "/crm/v3/objects/contacts/batch/read", "POST",
                {"properties": PROPS_CONTACTO, "inputs": [{"id": i} for i in ids]})
    return r.get("results", [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--simular", action="store_true")
    args = ap.parse_args()

    cx = psycopg.connect(args.dsn)
    with cx.cursor() as cur:
        cur.execute("""SELECT numero_identificacion, id FROM reps.prestador
                       WHERE clase_prestador <> 'Profesional Independiente'""")
        por_nit = dict(cur.fetchall())

    empresas = traer_empresas(args.token)
    print(f"── {len(empresas)} empresas en HubSpot con NIT")

    sin_cruce = []
    nuevas_personas = nuevos_canales = comerciales = 0
    leidos = 0

    for emp in empresas:
        p = emp["properties"]
        nit = (p.get("proa_nit") or "").strip()
        pid = por_nit.get(nit)
        if pid is None:
            sin_cruce.append((p.get("name"), nit))
            continue

        with cx.cursor() as cur:
            # Lo que Ariad ya tiene, para no reimportar el viaje de ida.
            cur.execute("""SELECT tipo, valor_norm FROM enriquecimiento.canal
                           WHERE prestador_id = %s""", (pid,))
            ya = {(t, v) for t, v in cur.fetchall()}
            cur.execute("""SELECT nombre_clave, id FROM enriquecimiento.persona
                           WHERE prestador_id = %s""", (pid,))
            personas = dict(cur.fetchall())

            # ── Datos comerciales de la cuenta
            arl = p.get("proa_arl_actual")
            clase = p.get("proa_clase_riesgo_arl")
            mes = p.get("proa_ariad_mes_aniversario_arl")
            if any((arl, clase, mes)) and not args.simular:
                cur.execute("""
                    INSERT INTO enriquecimiento.cuenta_comercial
                      (prestador_id, arl_actual, clase_riesgo, mes_aniversario,
                       fuente, referencia, actualizado)
                    VALUES (%s,%s,%s,%s,'hubspot',%s, now())
                    ON CONFLICT (prestador_id) DO UPDATE SET
                      arl_actual = coalesce(excluded.arl_actual, enriquecimiento.cuenta_comercial.arl_actual),
                      clase_riesgo = coalesce(excluded.clase_riesgo, enriquecimiento.cuenta_comercial.clase_riesgo),
                      mes_aniversario = coalesce(excluded.mes_aniversario, enriquecimiento.cuenta_comercial.mes_aniversario),
                      actualizado = now()
                """, (pid, arl, clase, mes, "companies/" + emp["id"]))
            if any((arl, clase, mes)):
                comerciales += 1

            # ── Personas y canales
            for c in contactos_de(args.token, emp["id"]):
                cp = c["properties"]
                nombre = " ".join(filter(None, [cp.get("firstname"), cp.get("lastname")])).strip()
                k = clave_nombre(nombre)
                if not k:
                    continue

                pers_id = personas.get(k)
                if pers_id is None and not args.simular:
                    cur.execute("""
                        INSERT INTO enriquecimiento.persona
                          (prestador_id, nombre_clave, nombre, cargo, estado, confianza)
                        VALUES (%s,%s,%s,%s,'verificado',90)
                        ON CONFLICT (prestador_id, nombre_clave) DO UPDATE
                          SET cargo = coalesce(nullif(excluded.cargo,''), enriquecimiento.persona.cargo),
                              ultima_verificacion = now()
                        RETURNING id
                    """, (pid, k, nombre, cp.get("jobtitle")))
                    pers_id = cur.fetchone()[0]
                    personas[k] = pers_id
                    cur.execute("""INSERT INTO enriquecimiento.evidencia
                        (persona_id, fuente, referencia, crudo) VALUES (%s,'manual',%s,%s)""",
                        (pers_id, "contacts/" + c["id"],
                         json.dumps({"nombre": nombre, "cargo": cp.get("jobtitle")})))
                    nuevas_personas += 1
                elif pers_id is None:
                    nuevas_personas += 1

                for prop, (tipo, ambito) in CANALES.items():
                    valor = (cp.get(prop) or "").strip()
                    if not valor:
                        continue
                    leidos += 1
                    vn = norm_valor(tipo, valor)
                    if not vn or (tipo, vn) in ya:
                        continue      # es el viaje de ida, no enriquecimiento
                    nuevos_canales += 1
                    ya.add((tipo, vn))
                    if args.simular or pers_id is None:
                        continue
                    cur.execute("""
                        INSERT INTO enriquecimiento.canal
                          (persona_id, prestador_id, tipo, valor, valor_norm, ambito,
                           estado, confianza)
                        VALUES (%s,%s,%s,%s,%s,%s,'verificado',90)
                        ON CONFLICT (prestador_id, tipo, valor_norm) DO UPDATE
                          SET persona_id = coalesce(enriquecimiento.canal.persona_id, excluded.persona_id),
                              ultima_verificacion = now()
                        RETURNING id
                    """, (pers_id, pid, tipo, valor, vn, ambito))
                    cid = cur.fetchone()[0]
                    cur.execute("""INSERT INTO enriquecimiento.evidencia
                        (canal_id, fuente, referencia, crudo) VALUES (%s,'manual',%s,%s)""",
                        (cid, "contacts/" + c["id"], json.dumps({prop: valor})))
        if not args.simular:
            cx.commit()

    cx.close()
    marca = "[simulacro] " if args.simular else ""
    print(f"\n── {marca}resultado")
    print(f"   canales leídos en HubSpot   {leidos}")
    print(f"   personas nuevas para Ariad  {nuevas_personas}")
    print(f"   canales nuevos para Ariad   {nuevos_canales}")
    print(f"   cuentas con datos de ARL    {comerciales}")
    if sin_cruce:
        # No es un fallo: HubSpot tiene lo que Proactivos trabaja, y Ariad tiene
        # el universo del REPS. Una empresa que no está habilitada como prestador
        # —o que se trabaja por otro ramo— no tiene por qué estar en Ariad.
        print(f"\n   {len(sin_cruce)} empresas de HubSpot que no son prestadores del REPS")
        print(f"   (normal: Ariad solo conoce el universo REPS)")
        for n, nit in sin_cruce[:8]:
            print(f"      · {n} — {nit}")
    if args.simular:
        print("\nNada se escribió. Repite sin --simular para ejecutar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
