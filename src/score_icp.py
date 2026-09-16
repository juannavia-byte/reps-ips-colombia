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
  -- Estructura organizativa, toda dato duro del REPS. Es lo que distingue a una
  -- clínica de 300 personas en una sede de una red de 300 en cuarenta.
  SELECT r.prestador_id,
         count(DISTINCT s.id) n_sedes,
         count(DISTINCT s.municipio) n_mun,
         count(DISTINCT s.departamento) n_dep
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
         count(DISTINCT sv.grupo_nombre)                                            n_grupos,
         bool_or(sv.servicio_nombre ILIKE '%IMAGEN%' OR sv.servicio_nombre ILIKE '%RADIOLOG%'
              OR sv.servicio_nombre ILIKE '%RESONANCIA%' OR sv.servicio_nombre ILIKE '%TOMOGRAF%') imagenologia,
         bool_or(sv.servicio_nombre ILIKE '%LABORATORIO%')                          laboratorio,
         bool_or(sv.servicio_nombre ILIKE '%FARMAC%' OR sv.servicio_nombre ILIKE '%MEDICAMENT%') farmacia,
         bool_or(sv.servicio_nombre ILIKE '%DIALISIS%' OR sv.servicio_nombre ILIKE '%DIÁLISIS%'
              OR sv.servicio_nombre ILIKE '%QUIMIOTERAP%' OR sv.servicio_nombre ILIKE '%RADIOTERAP%') alto_costo,
         bool_or(sv.grupo_nombre = 'Internación')                                   internacion,
         bool_or(sv.grupo_nombre = 'Atención Inmediata')                             urgencias,
         count(*) FILTER (WHERE sv.complejidad_alta)                                n_compl_alta,
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
       coalesce(g.n_sedes, 1), coalesce(g.n_mun, 1), coalesce(g.n_dep, 1),
       coalesce(c.tel, false), coalesce(c.mail, false), coalesce(c.rep, false),
       coalesce(ri.quirurgico, false), coalesce(ri.compl_alta, false),
       coalesce(ri.oncologico, false), coalesce(ri.quemados, false),
       coalesce(ri.salud_mental, false), coalesce(ri.trasplante, false),
       coalesce(ri.n_grupos, 0), coalesce(ri.n_compl_alta, 0),
       coalesce(ri.imagenologia,false), coalesce(ri.laboratorio,false),
       coalesce(ri.farmacia,false), coalesce(ri.alto_costo,false),
       coalesce(ri.internacion,false), coalesce(ri.urgencias,false),
       (p.naturaleza_juridica = 'Pública'),
       coalesce(u.camas_uci, 0),
       coalesce(nv.n, 0),
       f.patrimonio, f.ingresos, f.costos,
       v.camas, v.salas_cirugia, v.consultorios, v.ambulancias, v.n_servicios
FROM reps.prestador p
JOIN reps.estimacion_personal e ON e.prestador_id = p.id
JOIN reps.v_prestador_completo v ON v.id = p.id
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


def log2p(x: float) -> float:
    """log2(1+x). Crece rápido al principio y se aplana después.

    Es la forma correcta de tratar estructura: pasar de 1 a 4 sedes cambia la
    venta mucho más que pasar de 40 a 43. Y absorbe el ruido de los datos: que
    la planta esté mal por un factor de 2 mueve el término en 1, no en 100.
    """
    return math.log2(1.0 + max(0.0, x))


def calcular(fila: dict, cfg: dict) -> dict:
    """Devuelve score, ltv, cac y el desglose. Función pura: entra dato, sale número."""
    cac_cfg, ltv_cfg = cfg["cac"], cfg["ltv"]
    pc = cac_cfg["pesos"]
    esc = cac_cfg["estructura"]

    # ── CAC: cuánto trabajo cuesta llegar a la firma
    #
    # La versión anterior derivaba todo de una tabla de cuatro tramos de planta.
    # Medido sobre las 10.646 filas, eso dejaba al 84 % de las empresas con el
    # MISMO CAC, porque el 91 % cae en el tramo más bajo. Ahora manda la
    # estructura: sedes y municipios son dato duro del REPS y sí varían dentro
    # de cada tamaño (de 1 a 48 sedes entre las de menos de 50 personas).
    sedes = max(1, fila["n_sedes"] or 1)
    municipios = max(1, fila["n_mun"] or 1)
    deptos = max(1, fila["n_dep"] or 1)
    planta = fila["planta"] or 0
    tam = planta / esc["ancla_planta"]

    interlocutores = 1.0 + esc["k_sedes"] * log2p(sedes) + esc["k_tamano"] * log2p(tam)
    ciclo = esc["ciclo_base"] + esc["c_dispersion"] * log2p(municipios) + esc["c_tamano"] * log2p(tam)
    if fila["es_ese"]:
        interlocutores += esc["recargo_ese"]["interlocutores"]
        ciclo += esc["recargo_ese"]["ciclo"]

    faltantes = sum(1 for k in ("tel", "mail", "rep") if not fila[k])

    aportes_cac = {
        "interlocutores": pc["interlocutores"]["peso"] * interlocutores,
        "ciclo": pc["ciclo"]["peso"] * ciclo,
        "dispersion_geografica": pc["dispersion_geografica"]["peso"] * log2p(deptos),
        "perfilamiento_faltante": pc["perfilamiento_faltante"]["peso"] * faltantes,
    }
    cac = cac_cfg["esfuerzo_base"] + sum(aportes_cac.values())

    # ── LTV: valor de la cuenta completa a 24 meses
    # ── LTV: comisión esperada de la escalera completa de ramos a 24 meses.
    #
    # El criterio ya no es "qué predice los ingresos de la IPS" sino "cuánto de
    # esto se traduce en pólizas que podemos colocar". La prima de seguros no
    # escala con la facturación sino con la EXPOSICIÓN: una unidad renal factura
    # mucho y expone poco; una clínica con quirófanos, estrangulada por la
    # cartera de las EPS, factura poco para lo que expone. La regresión
    # castigaba a la segunda, que es el mejor cliente para un corredor.
    VARIABLES = {
        "planta": planta,
        "sedes": fila["n_sedes"] or 1,
        "camas": fila["camas"] or 0,
        "camas_uci": fila["camas_uci"] or 0,
        "salas_cirugia": fila["salas_cirugia"] or 0,
        "consultorios": fila["consultorios"] or 0,
        "ambulancias": fila["ambulancias"] or 0,
        "servicios": fila["n_servicios"] or 0,
        "grupos": fila["n_grupos"] or 0,
        "compl_alta": fila["n_compl_alta"] or 0,
        "serv_nuevos": fila["serv_nuevos"] or 0,
        "imagenologia": 1 if fila["imagenologia"] else 0,
        "laboratorio": 1 if fila["laboratorio"] else 0,
        "farmacia": 1 if fila["farmacia"] else 0,
        "alto_costo": 1 if fila["alto_costo"] else 0,
        "internacion": 1 if fila["internacion"] else 0,
        "urgencias": 1 if fila["urgencias"] else 0,
        "es_publica": 1 if fila["es_publica"] else 0,
    }

    def valor_ramo(cfg_ramo):
        expo = sum(VARIABLES.get(k, 0) * coef for k, coef in cfg_ramo["exposicion"].items())
        n = norm_potencia(expo, cfg_ramo["referencia"], cfg_ramo["exponente"])
        return cfg_ramo["valor_relativo"] * cfg_ramo["probabilidad_24m"] * n

    ramos = {}
    subtotal = {}
    for grupo in ("corporativos", "personas"):
        peso_g = ltv_cfg["peso_grupo"]["corporativo" if grupo == "corporativos" else "personas"]
        total_g = 0.0
        for nombre, c in ltv_cfg[grupo].items():
            v = valor_ramo(c)
            if v > 0:
                ramos[nombre] = round(v, 2)
            total_g += v
        subtotal[grupo] = round(peso_g * total_g, 2)

    crec_cfg = ltv_cfg["crecimiento"]
    norm_crec = (ltv_cfg["neutro_sin_crecimiento"] if fila["crecimiento"] is None
                 else norm_lineal(float(fila["crecimiento"]),
                                  crec_cfg["piso_pct"], crec_cfg["techo_pct"]))
    # El crecimiento multiplica: una IPS que crece tendrá más de todo en 24 meses.
    factor_crec = 1.0 + crec_cfg["peso"] * (norm_crec - ltv_cfg["neutro_sin_crecimiento"])
    base_ltv = (subtotal["corporativos"] + subtotal["personas"]) * factor_crec

    aportes_ltv = {"corporativo": subtotal["corporativos"],
                   "personas": subtotal["personas"],
                   "factor_crecimiento": round(factor_crec, 3)}

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
    # exponente_cac permite decidir cuánto pesa el esfuerzo frente al valor.
    # En 1 es un cociente normal; subirlo castiga más a las cuentas difíciles.
    exp_cac = cfg["escala"].get("exponente_cac", 1.0)
    ratio = ltv / (cac ** exp_cac) if cac else 0.0

    return {
        "ltv": round(ltv, 4), "cac": round(cac, 4), "ratio": ratio,
        "desglose": {
            "cac": {**{k: round(v, 3) for k, v in aportes_cac.items()},
                    "base": cac_cfg["esfuerzo_base"],
                    "interlocutores_estimados": round(interlocutores, 2),
                    "ciclo_meses_estimado": round(ciclo, 2),
                    "datos_de_contacto_faltantes": faltantes,
                    "estructura": {"sedes": sedes, "municipios": municipios,
                                   "departamentos": deptos,
                                   "personas_por_sede": round(planta / sedes, 1)}},
            "ltv": {**aportes_ltv, "base": round(base_ltv, 3),
                    "por_ramo": dict(sorted(ramos.items(), key=lambda x: -x[1])),
                    "insumos": {k: v for k, v in VARIABLES.items() if v}},
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
    modo = esc.get("modo", "lineal")

    if modo == "z_log":
        # El cociente es log-normal, así que se estandariza su logaritmo. A
        # diferencia del percentil, esto conserva CUÁNTO mejor es una cuenta que
        # otra, y no se deforma porque el universo esté lleno de prestadores
        # diminutos que nunca se van a trabajar.
        import statistics as _st
        logs = [math.log(max(r["ratio"], 1e-9)) for r in resultados]
        obs_media, obs_desv = _st.mean(logs), (_st.pstdev(logs) or 1.0)
        centro = esc["centro"]
        disp = esc["dispersion"] or 1.0
        for r, lr in zip(resultados, logs):
            z = (lr - centro) / disp
            r["score"] = round(max(0.0, min(100.0,
                esc["centro_score"] + esc["puntos_por_sigma"] * z)), 2)
        desfase = abs(obs_media - centro) / disp
        if desfase > 0.5:
            print(f"  ⚠ calibración: ln(cociente) observado tiene media {obs_media:.3f} y "
                  f"desviación {obs_desv:.3f}; el archivo dice {centro} y {disp}.")
            print(f"    Está desfasado {desfase:.2f} sigmas. Considera actualizar "
                  f"`escala.centro` y `escala.dispersion` en config/pesos_icp.json.")
        else:
            print(f"  calibración ok · ln(cociente) observado: media {obs_media:.3f}, "
                  f"desviación {obs_desv:.3f}")
    elif modo == "percentil":
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
    ap.add_argument("--autocalibrar", action="store_true",
                    help="reescribe escala.centro y escala.dispersion con lo observado "
                         "y vuelve a puntuar. Úsalo tras cambiar pesos de forma grande.")
    args = ap.parse_args()

    cfg = json.loads(Path(args.pesos).read_text(encoding="utf-8"))
    print(f"· pesos: {args.pesos} (versión {cfg['version']})")

    cx = psycopg.connect(args.dsn)
    cx.execute(DDL)
    cx.commit()

    with cx.cursor() as cur:
        cur.execute(CONSULTA)
        columnas = ["id", "clase", "es_ese", "planta", "fl_alto", "crecimiento",
                    "n_sedes", "n_mun", "n_dep",
                    "tel", "mail", "rep", "quirurgico", "compl_alta", "oncologico",
                    "quemados", "salud_mental", "trasplante", "n_grupos", "n_compl_alta",
                    "imagenologia", "laboratorio", "farmacia", "alto_costo",
                    "internacion", "urgencias", "es_publica",
                    "camas_uci", "serv_nuevos",
                    "patrimonio", "ingresos", "costos",
                    "camas", "salas_cirugia", "consultorios", "ambulancias", "n_servicios"]
        filas = [dict(zip(columnas, r)) for r in cur]
    print(f"· prestadores con estimación de planta: {len(filas):,}")

    resultados = [calcular(f, cfg) for f in filas]

    if args.autocalibrar and cfg["escala"].get("modo") == "z_log":
        # Cambiar un peso mueve la escala del cociente y deja obsoletas las
        # constantes. Esto las vuelve a fijar sobre lo observado y guarda el
        # archivo, para no tener que perseguirlas a mano.
        import statistics as st
        logs = [math.log(max(r["ratio"], 1e-9)) for r in resultados]
        cfg["escala"]["centro"] = round(st.mean(logs), 3)
        cfg["escala"]["dispersion"] = round(st.pstdev(logs) or 1.0, 3)
        Path(args.pesos).write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"· autocalibrado: centro {cfg['escala']['centro']}, "
              f"dispersión {cfg['escala']['dispersion']} (guardado en {args.pesos})")

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
