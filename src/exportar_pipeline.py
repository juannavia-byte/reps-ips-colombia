"""
Exporta un lote de cuentas de Ariad para cargarlas en HubSpot.

    python src/exportar_pipeline.py --dsn "$DSN" --cuantas 20
    python src/exportar_pipeline.py --dsn "$DSN" --cuantas 20 --prioridad alta \
        --departamento Atlántico --salida dist/pipeline-arl.json

Escribe un JSON que consume `scripts/importar-pipeline-arl.mjs` en el repo
`proactivos-hubspot`. No habla con HubSpot: Ariad no tiene por qué saber la
forma de otro sistema, y así el mismo archivo sirve si mañana el destino
cambia.

Qué sale por cada cuenta
------------------------
La empresa con lo que HubSpot necesita para decidir a quién llamar primero, y
las personas que el motor de enriquecimiento ya encontró — que es el punto de
partida sobre el que después se enriquece a mano el celular del SST y las
redes. El campo `evidencia` viaja para que la nota del registro diga de dónde
salió cada cosa en vez de aparecer como un dato caído del cielo.

Lo que NO sale
--------------
Los correos adivinados por patrón. Un correo que nadie publicó no debe entrar
al CRM: una vez dentro, nadie lo distingue de uno real y acaba en una secuencia
quemando el dominio desde el que se escribe. Se quedan en Ariad, marcados.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import psycopg

# `proa_rol_decision` en HubSpot: gerencia | cfo | rrhh | sst | compras |
# juridico | otro. Se traduce desde la categoría de cargo de Ariad para que el
# motor sepa a quién va cada toque sin que nadie lo clasifique a mano.
ROL = {
    "representante_legal": "gerencia", "gerente_general": "gerencia",
    "director_general": "gerencia", "presidente": "gerencia",
    "propietario": "gerencia", "subgerente": "gerencia",
    "gerente_financiero": "cfo", "ordenador_del_gasto": "cfo",
    "talento_humano": "rrhh",
    "sst": "sst", "hseq": "sst", "calidad": "sst",
    "juridica": "juridico",
}

CONSULTA = """
WITH sede_ref AS (
  SELECT DISTINCT ON (r.prestador_id) r.prestador_id, s.departamento, s.municipio
  FROM reps.sede s JOIN reps.registro_habilitacion r ON r.id = s.registro_id
  ORDER BY r.prestador_id, (s.es_principal IS TRUE) DESC, s.id
)
SELECT p.id, p.numero_identificacion, p.digito_verificacion, p.razon_social,
       p.naturaleza_juridica, (p.es_ese IS TRUE) AS es_ese,
       sr.departamento, sr.municipio,
       sc.score, sc.prioridad,
       e.personal_estimado, e.fuerza_laboral_bajo, e.fuerza_laboral_alto,
       e.crecimiento_pct, e.tendencia,
       v.n_sedes, v.n_servicios, v.camas, v.salas_cirugia, v.consultorios,
       v.ambulancias, v.fin_ingresos,
       (SELECT count(DISTINCT s2.municipio)
        FROM reps.sede s2 JOIN reps.registro_habilitacion r2 ON r2.id = s2.registro_id
        WHERE r2.prestador_id = p.id) AS n_municipios
FROM reps.prestador p
JOIN reps.v_prestador_completo v ON v.id = p.id
JOIN reps.score_icp sc ON sc.prestador_id = p.id
LEFT JOIN sede_ref sr ON sr.prestador_id = p.id
LEFT JOIN reps.estimacion_personal e ON e.prestador_id = p.id
WHERE p.clase_prestador <> 'Profesional Independiente'
  AND (%(prioridad)s::text IS NULL OR sc.prioridad = %(prioridad)s::text)
  AND (%(departamento)s::text IS NULL OR sr.departamento ILIKE %(departamento)s::text)
  AND NOT EXISTS (SELECT 1 FROM enriquecimiento.exclusion x WHERE x.prestador_id = p.id)
ORDER BY sc.score DESC
LIMIT %(cuantas)s
"""

# Canales de la EMPRESA, sin persona detrás: el conmutador y el buzón de
# contratación. Son la puerta del toque del día 1 —la llamada a recepción para
# rutear hacia el SST— así que sin ellos la secuencia no puede ni empezar.
CANALES_EMPRESA = """
SELECT c.prestador_id, c.tipo, c.valor, c.ambito, c.estado, c.confianza
FROM enriquecimiento.canal c
WHERE c.prestador_id = ANY(%s) AND c.persona_id IS NULL
  AND EXISTS (SELECT 1 FROM enriquecimiento.evidencia ev
              WHERE ev.canal_id = c.id AND ev.fuente <> 'patron')
ORDER BY c.tipo, c.confianza DESC
"""

PERSONAS = """
SELECT pe.prestador_id, pe.nombre, pe.cargo, pe.cargo_categoria, pe.tier,
       pe.estado, pe.confianza,
       (SELECT jsonb_agg(jsonb_build_object(
                 'tipo', c.tipo, 'valor', c.valor, 'ambito', c.ambito,
                 'estado', c.estado, 'confianza', c.confianza)
               ORDER BY c.confianza DESC)
        FROM enriquecimiento.canal c
        WHERE c.persona_id = pe.id
          -- Los adivinados por patrón no salen: ver el encabezado.
          AND EXISTS (SELECT 1 FROM enriquecimiento.evidencia ev
                      WHERE ev.canal_id = c.id AND ev.fuente <> 'patron')) AS canales,
       (SELECT string_agg(DISTINCT ev.fuente, ',')
        FROM enriquecimiento.evidencia ev WHERE ev.persona_id = pe.id) AS fuentes
FROM enriquecimiento.persona pe
WHERE pe.prestador_id = ANY(%s) AND pe.estado <> 'obsoleto'
  AND NOT EXISTS (SELECT 1 FROM enriquecimiento.exclusion x
                  WHERE x.prestador_id = pe.prestador_id
                     OR x.nombre_clave = pe.nombre_clave)
ORDER BY coalesce(pe.tier, 9), pe.confianza DESC
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--cuantas", type=int, default=20)
    ap.add_argument("--prioridad", choices=["alta", "media", "baja"])
    ap.add_argument("--departamento")
    ap.add_argument("--salida", default="dist/pipeline-arl.json")
    args = ap.parse_args()

    cx = psycopg.connect(args.dsn)
    with cx.cursor() as cur:
        cur.execute(CONSULTA, {"prioridad": args.prioridad,
                               "departamento": args.departamento,
                               "cuantas": args.cuantas})
        cols = [d.name for d in cur.description]
        cuentas = [dict(zip(cols, f)) for f in cur.fetchall()]

        ids = [c["id"] for c in cuentas]
        cur.execute(PERSONAS, (ids,))
        pcols = [d.name for d in cur.description]
        gente = [dict(zip(pcols, f)) for f in cur.fetchall()]

        cur.execute(CANALES_EMPRESA, (ids,))
        ccols = [d.name for d in cur.description]
        canales_emp = [dict(zip(ccols, f)) for f in cur.fetchall()]
    cx.close()

    por_emp_canal: dict[int, list] = {}
    for c in canales_emp:
        por_emp_canal.setdefault(c.pop("prestador_id"), []).append(c)

    por_empresa: dict[int, list] = {}
    for g in gente:
        por_empresa.setdefault(g.pop("prestador_id"), []).append(g)

    salida = []
    for c in cuentas:
        personas = por_empresa.get(c["id"], [])
        for p in personas:
            p["rol_decision"] = ROL.get(p.get("cargo_categoria") or "", "otro")
            p["canales"] = p.get("canales") or []
        salida.append({
            "nit": c["numero_identificacion"],
            "dv": c["digito_verificacion"],
            "razon_social": c["razon_social"],
            "municipio": c["municipio"],
            "departamento": c["departamento"],
            "naturaleza": c["naturaleza_juridica"],
            "es_ese": c["es_ese"],
            "score_icp": None if c["score"] is None else round(float(c["score"]), 1),
            "prioridad": c["prioridad"],
            "trabajadores": c["personal_estimado"],
            "fuerza_laboral": [c["fuerza_laboral_bajo"], c["fuerza_laboral_alto"]],
            "crecimiento_pct": None if c["crecimiento_pct"] is None else round(float(c["crecimiento_pct"]), 1),
            "tendencia": c["tendencia"],
            "capacidad": {
                "sedes": c["n_sedes"], "municipios": c["n_municipios"],
                "servicios": c["n_servicios"], "camas": c["camas"],
                "quirofanos": c["salas_cirugia"], "consultorios": c["consultorios"],
                "ambulancias": c["ambulancias"],
            },
            "ingresos_reportados": None if c["fin_ingresos"] is None else int(c["fin_ingresos"]),
            # El conmutador y el buzón de área. La llamada del día 1 sale de acá.
            "canales_empresa": por_emp_canal.get(c["id"], []),
            "personas": personas,
        })

    destino = Path(args.salida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(
        {"generado": date.today().isoformat(), "origen": "ariad", "cuentas": salida},
        ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    con_gente = sum(1 for s in salida if s["personas"])
    con_canal = sum(1 for s in salida if any(p["canales"] for p in s["personas"]))
    print(f"· {len(salida)} cuentas → {destino}")
    print(f"· {con_gente} con alguna persona · {con_canal} con al menos un canal observado")
    con_conmutador = sum(1 for s in salida
                         if any(x["tipo"] == "telefono" for x in s["canales_empresa"]))
    print(f"· {sum(len(s['personas']) for s in salida)} personas en total")
    print(f"· {con_conmutador} con teléfono de empresa para la llamada del día 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
