"""
Score de ICP por cociente LTV:CAC, donde el CAC es esfuerzo y tiempo, no dinero.

Uso:  python src/score_icp.py --dsn "$DSN" [--pesos config/pesos_icp.json]

Sigue el mismo patrón que `estimacion_personal`: tabla derivada aparte, con el
desglose de cómo se formó el número. No se agregan columnas a `prestador`.

NINGÚN PESO ESTÁ ESCRITO EN ESTE ARCHIVO. Todos salen de config/pesos_icp.json.
Si al leer el código falta un número, está en el JSON: así se puede cambiar el
criterio comercial sin tocar Python.

Fórmula
-------
    CAC   = esfuerzo_base
          + p.interlocutores        × nº de personas a convencer
          + p.ciclo_meses           × meses estimados de ciclo
          + p.perfilamiento_faltante× datos de contacto que faltan (0-3)
          + p.red_nacional          × (1 si decide un corporativo fuera de la sede)

    LTV   = ( p.tamano   × norm(planta)
            + p.contratistas × norm(fuerza laboral tercerizada)
            + p.crecimiento  × norm(crecimiento 19-21) )
            × mult.reclasificación × mult.novedad_reps × mult.tipo × deterioro

    score = 100 × (LTV / CAC) / referencia_ratio, recortado a 0-100

Por qué logaritmo en el tamaño: una IPS de 2.000 personas no vale el doble que
una de 1.000 para este negocio — vale más, pero no el doble. Sin log, tres redes
nacionales se quedarían con todo el ranking y el resto quedaría plano.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import psycopg

DDL = """
DROP TABLE IF EXISTS reps.score_icp;
CREATE TABLE reps.score_icp (
    prestador_id  bigint PRIMARY KEY REFERENCES reps.prestador(id) ON DELETE CASCADE,
    score         numeric(6,2) NOT NULL,
    prioridad     text         NOT NULL,   -- alta | media | baja
    ltv           numeric(10,4) NOT NULL,
    cac           numeric(10,4) NOT NULL,
    ratio         numeric(10,4) NOT NULL,
    -- Por qué le dio ese número: aporte de cada variable, en las mismas
    -- unidades en que entra a la fórmula.
    desglose      jsonb        NOT NULL,
    version_pesos text         NOT NULL
);
COMMENT ON TABLE reps.score_icp IS
  'Cociente LTV:CAC por prestador. CAC es esfuerzo y tiempo, no dinero. '
  'Los pesos viven en config/pesos_icp.json; aquí solo queda el resultado.';
COMMENT ON COLUMN reps.score_icp.desglose IS
  'Aporte de cada variable al LTV y al CAC, más las señales que lo dispararon.';

CREATE INDEX ix_score_valor ON reps.score_icp (score DESC);
CREATE INDEX ix_score_prioridad ON reps.score_icp (prioridad);
"""

# Los insumos del score. Todo lo que se usa aquí ya existe en el modelo: no se
# recalcula nada que otra parte del pipeline ya haya calculado.
CONSULTA = """
WITH geo AS (
  SELECT r.prestador_id, count(DISTINCT s.departamento) n_dep
  FROM reps.sede s JOIN reps.registro_habilitacion r ON r.id = s.registro_id
  GROUP BY 1
), contacto AS (
  SELECT prestador_id,
         bool_or(telefono IS NOT NULL) tel,
         bool_or(email IS NOT NULL) mail,
         bool_or(representante_legal IS NOT NULL) rep
  FROM reps.registro_habilitacion GROUP BY 1
), riesgo AS (
  -- Señales de alto riesgo que probablemente NO están reflejadas en la clase
  -- de riesgo con la que la IPS cotiza ARL.
  SELECT r.prestador_id,
         bool_or(sv.grupo_nombre = 'Quirúrgicos')                                   quirurgico,
         bool_or(sv.complejidad_alta)                                               compl_alta,
         bool_or(sv.especificidades->>'especificidad_oncologico' = 'SI')            oncologico,
         bool_or(sv.especificidades->>'especificidad_atencion_paciente_quemado' = 'SI') quemados,
         bool_or(sv.especificidades->>'especificidad_salud_mental' = 'SI')          salud_mental,
         bool_or(sv.especificidades->>'especificidad_trasplante_renal' = 'SI'
              OR sv.especificidades->>'especificidad_trasplante_osteomuscular' = 'SI'
              OR sv.especificidades->>'especificidad_trasplante_piel' = 'SI'
              OR sv.especificidades->>'especificidad_trasplante_cardiovascular' = 'SI'
              OR sv.especificidades->>'especificidad_trasplante_tejido_ocular' = 'SI'
              OR sv.especificidades->>'especificidad_trasplante_celulas_progenitoras_hematopoyeticas' = 'SI')
                                                                                    trasplante
  FROM reps.sede_servicio sv
  JOIN reps.sede s ON s.id = sv.sede_id
  JOIN reps.registro_habilitacion r ON r.id = s.registro_id
  GROUP BY 1
), uci AS (
  SELECT r.prestador_id, sum(c.cantidad) camas_uci
  FROM reps.sede_capacidad c
  JOIN reps.sede s ON s.id = c.sede_id
  JOIN reps.registro_habilitacion r ON r.id = s.registro_id
  WHERE c.concepto_nombre ILIKE 'Intensiva%'
  GROUP BY 1
), nuevos AS (
  SELECT r.prestador_id, count(*) n
  FROM reps.sede_servicio sv
  JOIN reps.sede s ON s.id = sv.sede_id
  JOIN reps.registro_habilitacion r ON r.id = s.registro_id
  WHERE sv.fecha_apertura >= (current_date - interval '12 months')
  GROUP BY 1
)
SELECT p.id, p.clase_prestador, coalesce(p.es_ese, false),
       e.personal_estimado, e.fuerza_laboral_alto, e.crecimiento_pct,
       coalesce(g.n_dep, 1),
       coalesce(c.tel, false), coalesce(c.mail, false), coalesce(c.rep, false),
       coalesce(ri.quirurgico, false), coalesce(ri.compl_alta, false),
       coalesce(ri.oncologico, false), coalesce(ri.quemados, false),
       coalesce(ri.salud_mental, false), coalesce(ri.trasplante, false),
       coalesce(u.camas_uci, 0),
       coalesce(nv.n, 0),
       f.patrimonio, f.ingresos, f.costos
FROM reps.prestador p
JOIN reps.estimacion_personal e ON e.prestador_id = p.id
LEFT JOIN geo g      ON g.prestador_id = p.id
LEFT JOIN contacto c ON c.prestador_id = p.id
LEFT JOIN riesgo ri  ON ri.prestador_id = p.id
LEFT JOIN uci u      ON u.prestador_id = p.id
LEFT JOIN nuevos nv  ON nv.prestador_id = p.id
LEFT JOIN LATERAL (
    SELECT patrimonio, ingresos, costos FROM reps.prestador_financiero pf
    WHERE pf.prestador_id = p.id ORDER BY pf.vigencia DESC LIMIT 1
) f ON true
"""

CLASE_A_CLAVE = {
    "Instituciones Prestadoras de Servicios de Salud - IPS": "ips",
    "Objeto Social Diferente a la Prestación de Servicios de Salud": "objeto_social_distinto",
    "Transporte Especial de Pacientes": "transporte_pacientes",
}


def norm_potencia(valor: float | None, referencia: float, exponente: float) -> float:
    """Lleva un conteo a 0-1 con curva de potencia. `referencia` es el valor que da 1.

    Con logaritmo el extremo bajo quedaba inflado: una IPS de 38 personas salía
    con la mitad del valor de una de 1.000. Con exponente 0,70 la curva sigue
    frenando a los gigantes sin regalarle valor a los muy pequeños.
    """
    if not valor or valor <= 0:
        return 0.0
    return min(1.0, (valor / referencia) ** exponente)


def norm_lineal(valor: float, piso: float, techo: float) -> float:
    if techo == piso:
        return 0.0
    return max(0.0, min(1.0, (valor - piso) / (techo - piso)))


def tramo(planta: int | None, tramos: list[dict]) -> dict:
    p = planta or 0
    for t in tramos:
        if t["hasta"] is None or p <= t["hasta"]:
            return t
    return tramos[-1]


def calcular(fila: dict, cfg: dict) -> dict:
    """Devuelve score, ltv, cac y el desglose. Función pura: entra dato, sale número."""
    cac_cfg, ltv_cfg = cfg["cac"], cfg["ltv"]
    pc, pl = cac_cfg["pesos"], ltv_cfg["pesos"]
    esc = cac_cfg["escalas"]

    # ── CAC: cuánto trabajo cuesta llegar a la firma
    t = tramo(fila["planta"], esc["tramos_planta"])
    interlocutores = t["interlocutores"]
    ciclo = t["ciclo_meses"]
    if fila["es_ese"]:
        interlocutores += esc["recargo_ese"]["interlocutores"]
        ciclo += esc["recargo_ese"]["ciclo_meses"]

    faltantes = sum(1 for k in ("tel", "mail", "rep") if not fila[k])
    red = 1 if fila["n_dep"] > esc["umbral_red_nacional_deptos"] else 0

    aportes_cac = {
        "interlocutores": pc["interlocutores"]["peso"] * interlocutores,
        "ciclo_meses": pc["ciclo_meses"]["peso"] * ciclo,
        "perfilamiento_faltante": pc["perfilamiento_faltante"]["peso"] * faltantes,
        "red_nacional": pc["red_nacional"]["peso"] * red,
    }
    cac = cac_cfg["esfuerzo_base"] + sum(aportes_cac.values())

    # ── LTV: valor de la cuenta completa a 24 meses
    planta = fila["planta"] or 0
    # Los tercerizados son la diferencia entre la fuerza laboral y la nómina
    # propia: ahí está el volumen de contratistas del ancla de RC.
    contratistas = max(0, (fila["fl_alto"] or 0) - planta)
    crec = fila["crecimiento"]
    norm_crec = (ltv_cfg["neutro_sin_crecimiento"] if crec is None
                 else norm_lineal(float(crec), pl["crecimiento"]["piso_pct"],
                                  pl["crecimiento"]["techo_pct"]))

    aportes_ltv = {
        "tamano_planta": pl["tamano_planta"]["peso"] * norm_potencia(
            planta, pl["tamano_planta"]["referencia"], pl["tamano_planta"]["exponente"]),
        "contratistas": pl["contratistas"]["peso"] * norm_potencia(
            contratistas, pl["contratistas"]["referencia"], pl["contratistas"]["exponente"]),
        "crecimiento": pl["crecimiento"]["peso"] * norm_crec,
    }
    base_ltv = sum(aportes_ltv.values())

    mult = ltv_cfg["multiplicadores"]
    senales = [k for k in ("quirurgico", "compl_alta", "oncologico", "quemados",
                           "salud_mental", "trasplante") if fila[k]]
    if fila["camas_uci"] > 0:
        senales.append("camas_uci")
    m_reclas = mult["reclasificacion_riesgo"]["valor"] if senales else 1.0
    m_novedad = mult["novedad_reps"]["valor"] if fila["serv_nuevos"] > 0 else 1.0

    clave_tipo = "ese" if fila["es_ese"] else CLASE_A_CLAVE.get(fila["clase"], "ips")
    m_tipo = mult["tipo_prestador"].get(clave_tipo, 1.0)

    det = cfg["deterioro_financiero"]
    m_det, motivo_det = 1.0, None
    if fila["patrimonio"] is not None and float(fila["patrimonio"]) < 0:
        m_det *= det["factor_patrimonio_negativo"]
        motivo_det = "patrimonio negativo"
    elif (fila["ingresos"] is not None and fila["costos"] is not None
          and float(fila["costos"]) > float(fila["ingresos"])):
        m_det *= det["factor_perdida_operacional"]
        motivo_det = "costos por encima de ingresos"

    # Piso operativo: por debajo del tamaño mínimo que el método considera
    # servible, la cuenta no compensa aunque sea facilísima de cerrar.
    piso = ltv_cfg["piso_operativo"]
    bajo_umbral = planta < piso["umbral_planta"]
    m_piso = piso["factor"] if bajo_umbral else 1.0

    ltv = base_ltv * m_reclas * m_novedad * m_tipo * m_det * m_piso
    ratio = ltv / cac if cac else 0.0

    return {
        "ltv": round(ltv, 4), "cac": round(cac, 4), "ratio": ratio,
        "desglose": {
            "cac": {**{k: round(v, 3) for k, v in aportes_cac.items()},
                    "base": cac_cfg["esfuerzo_base"],
                    "interlocutores_estimados": interlocutores,
                    "ciclo_meses_estimado": ciclo,
                    "datos_de_contacto_faltantes": faltantes,
                    "decide_corporativo_fuera_de_sede": bool(red)},
            "ltv": {**{k: round(v, 3) for k, v in aportes_ltv.items()},
                    "base": round(base_ltv, 3)},
            "multiplicadores": {
                "reclasificacion_riesgo": m_reclas,
                "senales_alto_riesgo": senales,
                "novedad_reps": m_novedad,
                "tipo_prestador": {clave_tipo: m_tipo},
                "deterioro_financiero": m_det,
                "motivo_deterioro": motivo_det,
                "piso_operativo": m_piso,
                "bajo_tamano_minimo": bajo_umbral,
            },
        },
    }


def escalar(resultados: list[dict], cfg: dict) -> None:
    """Asigna score y prioridad a partir del cociente. Modifica en sitio.

    En modo percentil el score es la posición relativa dentro del universo, que
    es lo que un equipo comercial necesita para priorizar: "está en el 7 %
    mejor" se entiende sin explicar la fórmula. El cociente crudo queda guardado
    aparte para quien quiera la distancia absoluta.
    """
    esc, u = cfg["escala"], cfg["umbrales"]
    if esc.get("modo") == "percentil":
        n = len(resultados)
        # El cociente se redondea antes de ordenar y los empates reciben TODOS el
        # mismo percentil (el promedio del bloque). Sin esto, dos cuentas con
        # exactamente los mismos insumos podían quedar en percentiles distintos
        # según el orden en que llegaran, y el espejo del navegador no coincidía.
        for r in resultados:
            r["ratio"] = round(r["ratio"], 6)
        orden = sorted(range(n), key=lambda i: resultados[i]["ratio"])
        i = 0
        while i < n:
            j = i
            while j + 1 < n and resultados[orden[j + 1]]["ratio"] == resultados[orden[i]]["ratio"]:
                j += 1
            puesto = (i + j) / 2.0
            valor = round(100.0 * puesto / (n - 1), 2) if n > 1 else 100.0
            for k in range(i, j + 1):
                resultados[orden[k]]["score"] = valor
            i = j + 1
    else:
        ref = esc["referencia_ratio"]
        for r in resultados:
            r["score"] = round(max(0.0, min(100.0, 100.0 * r["ratio"] / ref)), 2)
    for r in resultados:
        r["prioridad"] = ("alta" if r["score"] >= u["alta"]
                          else "media" if r["score"] >= u["media"] else "baja")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--pesos", default="config/pesos_icp.json")
    args = ap.parse_args()

    cfg = json.loads(Path(args.pesos).read_text(encoding="utf-8"))
    print(f"· pesos: {args.pesos} (versión {cfg['version']})")

    cx = psycopg.connect(args.dsn)
    cx.execute(DDL)
    cx.commit()

    with cx.cursor() as cur:
        cur.execute(CONSULTA)
        columnas = ["id", "clase", "es_ese", "planta", "fl_alto", "crecimiento", "n_dep",
                    "tel", "mail", "rep", "quirurgico", "compl_alta", "oncologico",
                    "quemados", "salud_mental", "trasplante", "camas_uci", "serv_nuevos",
                    "patrimonio", "ingresos", "costos"]
        filas = [dict(zip(columnas, r)) for r in cur]
    print(f"· prestadores con estimación de planta: {len(filas):,}")

    resultados = [calcular(f, cfg) for f in filas]
    escalar(resultados, cfg)
    print(f"· escala: {cfg['escala'].get('modo', 'lineal')}")
    salida = [(f["id"], r["score"], r["prioridad"], r["ltv"], r["cac"], r["ratio"],
               json.dumps(r["desglose"], ensure_ascii=False), cfg["version"])
              for f, r in zip(filas, resultados)]

    with cx.cursor() as cur, cur.copy(
        """COPY reps.score_icp (prestador_id, score, prioridad, ltv, cac, ratio,
                                desglose, version_pesos) FROM STDIN"""
    ) as cp:
        for fila in salida:
            cp.write_row(fila)
    cx.commit()

    with cx.cursor() as cur:
        cur.execute("""SELECT prioridad, count(*), round(avg(score),1),
                              round(min(score),1), round(max(score),1)
                       FROM reps.score_icp GROUP BY 1
                       ORDER BY CASE prioridad WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END""")
        print("\n prioridad | prestadores | score medio | rango")
        for p, n, a, mn, mx in cur:
            print(f" {p:9s} | {n:11,} | {a:>11} | {mn}–{mx}")
        cur.execute("""SELECT count(*) FROM reps.score_icp s
                       JOIN reps.prestador p ON p.id = s.prestador_id
                       WHERE p.es_ips AND s.prioridad = 'alta'""")
        print(f"\n IPS en prioridad alta: {cur.fetchone()[0]:,}")
    cx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
