"""
Estimación de trabajadores por prestador y de su crecimiento 2019 → 2021.

QUÉ ES Y QUÉ NO ES
------------------
`prestador.numero_empleados` sigue NULL: ninguna fuente pública colombiana
publica la planta de una IPS. Esto no lo cambia. Lo derivado vive aparte, en
`estimacion_personal` y `estimacion_personal_anual`, con su método y su banda.

MÉTODO 1 · NÓMINA
-----------------
    trabajadores(año) ≈ gasto de nómina(año) / costo por trabajador(año)

El costo por trabajador se calibra **año por año** contra SIHO, que publica la
planta nacional de las ESE en su informe "Distribución Recurso Humano":

    año   planta ESE    nómina ESE agregada     costo/trabajador
    2019     46.783     (de los EEFF del año)   se calcula al correr
    2020     48.864
    2021     48.671

Calibrar por año es lo que hace comparable el crecimiento: si se usara un solo
costo para los tres años, la inflación salarial aparecería como contratación.

CUENTAS DE NÓMINA — corrección importante
-----------------------------------------
Una primera versión sumaba solo 5101 + 5104 + 5108 y dejaba fuera 5102, 5103 y
5107 (contribuciones imputadas y efectivas, y prestaciones sociales), que son
una parte grande del costo de un trabajador. Con el conjunto completo el ancla
2021 pasa de $21,5 M a **$30,9 M por persona/año** (~2,84 SMMLV de 2021 con
carga prestacional), que es lo que se espera de una planta de salud.

Los archivos usan tres convenciones distintas para las mismas cuentas:
  ESE, todas las vigencias   '......5101 Sueldos y salarios'
  privadas 2019 y 2020       '5101' (código pelado como cabecera)
  privadas 2021              'SUELDOS Y SALARIOS' (nombre NIIF)
Se resuelven por código cuando lo hay y por nombre cuando no, sin sumar dos veces.

MÉTODO 2 · CAPACIDAD
--------------------
Para quien no reporta financieros: regresión no negativa, sin intercepto y
ajustada solo sobre IPS, de la planta contra camas, camillas, consultorios,
salas, sillas, ambulancias, sedes y servicios. Con intercepto le asignaba ~7
trabajadores de base a cualquier consultorio. R² ≈ 0,42 en holdout: sirve de
orden de magnitud, no de cifra.

A los profesionales independientes no se les estima. Son personas naturales;
la ausencia de fila significa "no estimable", no cero.

QUÉ MIDE — leer antes de usarlo
-------------------------------
Mide **planta formal en nómina**, no fuerza laboral total. Las ESE gastaron en
2021 $11,4 billones en la cuenta 6310 "Servicios de salud" —servicios
comprados— contra $1,5 billones de nómina propia: 7,5 a 1. En salud, buena
parte del personal entra por prestación de servicios o por bolsas de empleo y
no pasa por las cuentas de nómina del contratante.

Contra 47 IPS de la Costa con conteo conocido, la fuerza laboral real fue en
mediana ×3 la planta en nómina (p25–p75 ×1,25–×5,24; extremos ×0,04 y ×47). Por
eso `fuerza_laboral_bajo/alto` es banda y no cifra.

Para ARL la distinción es el negocio: la afiliación sigue al empleador de
registro, así que la planta en nómina es lo que esa IPS afilia por su cuenta y
a los tercerizados los afilia la bolsa que los contrata.

POR QUÉ NO SE PROYECTA A 2026
-----------------------------
Los financieros terminan en 2021. Extrapolar cinco años una tendencia medida
entre 2019 y 2021 —pandemia de por medio y con la crisis de las EPS después—
sería inventar. Se entrega la tendencia observada; proyectarla es decisión de
quien la lea.
"""
from __future__ import annotations

import argparse
import json
import re

import numpy as np
import psycopg

# Planta nacional de ESE según SIHO · Distribución Recurso Humano (apoyo + operativo).
SIHO_PLANTA = {2019: 46_783, 2020: 48_864, 2021: 48_671}
SIHO_URL = ("https://prestadores.minsalud.gov.co/siho/informes/recursohumano.aspx"
            "?pageTitle=Distribución+Recurso+Humano")
BANDA = 0.35  # ±35 % sobre el costo por trabajador

# Factor de tercerización medido contra 47 IPS de la Costa con conteo conocido.
TERCERIZACION_P25, TERCERIZACION_P75 = 1.25, 5.24

# Las seis cuentas que componen el costo de un trabajador.
CODIGOS_NOMINA = {"5101", "5102", "5103", "5104", "5107", "5108"}
NOMBRES_NOMINA = {
    "SUELDOS Y SALARIOS", "CONTRIBUCIONES IMPUTADAS", "CONTRIBUCIONES EFECTIVAS",
    "APORTES SOBRE LA NOMINA", "PRESTACIONES SOCIALES", "GASTOS DE PERSONAL DIVERSOS",
}
_CODIGO = re.compile(r"^\.*\s*(\d{4})\b")
# El loader desambigua nombres de columna repetidos con " (índice)". Aquí se
# quita el sufijo para volver a agregar por nombre base: las cuentas de personal
# vienen dos veces —gastos de administración y costos de operación— y las dos
# son nómina del año.
_SUFIJO = re.compile(r"\s*\(\d+\)\s*$")

RASGOS = ("camas", "camillas", "consultorios", "salas", "sillas",
          "ambulancias", "sedes", "servicios")

VIGENCIA_ACTUAL = 2021  # la última que publica Supersalud

DDL = """
DROP TABLE IF EXISTS reps.estimacion_personal_anual;
DROP TABLE IF EXISTS reps.estimacion_personal;

CREATE TABLE reps.estimacion_personal (
    prestador_id        bigint PRIMARY KEY REFERENCES reps.prestador(id) ON DELETE CASCADE,
    personal_estimado   integer NOT NULL,   -- planta formal en nómina, vigencia más reciente
    rango_bajo          integer NOT NULL,
    rango_alto          integer NOT NULL,
    fuerza_laboral_bajo integer NOT NULL,   -- banda incluyendo tercerizados
    fuerza_laboral_alto integer NOT NULL,
    metodo              text    NOT NULL,   -- 'nomina' | 'capacidad'
    confianza           text    NOT NULL,   -- 'media' | 'baja'
    -- Crecimiento observado. NULL cuando no hay dos vigencias comparables.
    personal_2019       integer,
    personal_2020       integer,
    personal_2021       integer,
    crecimiento_pct     numeric(8,2),       -- 2019 -> 2021, en %
    crecimiento_anual   numeric(8,2),       -- CAGR equivalente, en %
    tendencia           text,               -- creciendo | estable | decreciendo | sin_serie
    insumos             jsonb   NOT NULL,
    nota                text    NOT NULL
);
COMMENT ON COLUMN reps.estimacion_personal.personal_estimado IS
  'Planta formal EN NÓMINA. Es la que la propia IPS afilia a ARL; los tercerizados '
  'los afilia la bolsa de empleo que los contrata.';
COMMENT ON COLUMN reps.estimacion_personal.crecimiento_pct IS
  'Variación 2019->2021 de la planta estimada, con costo por trabajador calibrado '
  'año por año contra SIHO para que no la contamine la inflación salarial.';

CREATE TABLE reps.estimacion_personal_anual (
    prestador_id      bigint  NOT NULL REFERENCES reps.prestador(id) ON DELETE CASCADE,
    vigencia          smallint NOT NULL,
    nomina_cop        numeric(20,2) NOT NULL,
    costo_trabajador  numeric(20,2) NOT NULL,
    personal          integer NOT NULL,
    PRIMARY KEY (prestador_id, vigencia)
);
COMMENT ON TABLE reps.estimacion_personal_anual IS
  'Serie por vigencia. El costo por trabajador es el del año, calibrado contra SIHO.';

CREATE INDEX ix_est_tendencia ON reps.estimacion_personal (tendencia);
CREATE INDEX ix_est_crec ON reps.estimacion_personal (crecimiento_pct);
"""

NOTA_NOMINA = ("nómina de Supersalud dividida por el costo anual por trabajador, "
               "calibrado año por año contra la planta nacional de ESE que publica SIHO")
NOTA_CAPACIDAD = ("regresión no negativa sobre camas, camillas, consultorios, salas, sillas, "
                  "ambulancias, sedes y servicios, ajustada contra las estimaciones de nómina")


def nomina(cuentas: dict) -> float:
    """Suma las cuentas de personal del AÑO, resolviendo las tres convenciones.

    Solo cuentas de resultado. Quedan fuera a propósito las de balance
    ('BENEFICIOS A LOS EMPLEADOS' y sus variantes), que son lo que la entidad
    le DEBE a sus trabajadores, no lo que les pagó en el año. Confundirlas
    infla la nómina y con ella el número de personas.
    """
    por_codigo = 0.0
    por_nombre = 0.0
    for k, v in cuentas.items():
        if not isinstance(v, (int, float)):
            continue
        arriba = _SUFIJO.sub("", k.strip().upper())
        m = _CODIGO.match(arriba)
        if m:
            if m.group(1) in CODIGOS_NOMINA:
                por_codigo += v
            continue
        if arriba in NOMBRES_NOMINA:
            por_nombre += v
    # Cada archivo usa una sola convención; nunca se suman las dos.
    return por_codigo if por_codigo else por_nombre


def nnls(A: np.ndarray, y: np.ndarray, iteraciones: int = 50) -> np.ndarray:
    """Mínimos cuadrados con coeficientes no negativos, por conjunto activo simple.

    Un coeficiente negativo diría "más camas, menos gente", que no significa nada.
    """
    activas = np.ones(A.shape[1], dtype=bool)
    coef = np.zeros(A.shape[1])
    for _ in range(iteraciones):
        if not activas.any():
            break
        sol, *_ = np.linalg.lstsq(A[:, activas], y, rcond=None)
        if (sol >= 0).all():
            coef[:] = 0
            coef[activas] = sol
            return coef
        idx = np.where(activas)[0]
        activas[idx[sol < 0]] = False
    if activas.any():
        sol, *_ = np.linalg.lstsq(A[:, activas], y, rcond=None)
        coef[activas] = np.clip(sol, 0, None)
    return coef


def clasificar(pct: float | None) -> str:
    if pct is None:
        return "sin_serie"
    if pct >= 10:
        return "creciendo"
    if pct <= -10:
        return "decreciendo"
    return "estable"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    args = ap.parse_args()
    cx = psycopg.connect(args.dsn)
    cx.execute(DDL)
    cx.commit()

    # ------------------------------------------------- calibración por año
    with cx.cursor() as cur:
        cur.execute("SELECT vigencia, cuentas FROM reps.prestador_financiero WHERE origen = 'ese'")
        ese = cur.fetchall()
    nomina_ese: dict[int, float] = {}
    for vig, cuentas in ese:
        nomina_ese[vig] = nomina_ese.get(vig, 0.0) + nomina(cuentas)

    costo: dict[int, float] = {}
    print("· calibración contra SIHO (planta nacional de ESE)")
    for vig in sorted(SIHO_PLANTA):
        if vig not in nomina_ese:
            continue
        costo[vig] = nomina_ese[vig] / SIHO_PLANTA[vig]
        print(f"  {vig}: planta {SIHO_PLANTA[vig]:>7,} · nómina ${nomina_ese[vig]:>18,.0f}"
              f" · costo ${costo[vig]:>12,.0f}/año (${costo[vig]/12:>10,.0f}/mes)")

    # ------------------------------------------------------ serie por año
    with cx.cursor() as cur:
        cur.execute("""SELECT prestador_id, vigencia, cuentas
                       FROM reps.prestador_financiero
                       WHERE prestador_id IS NOT NULL""")
        registros = cur.fetchall()

    # (prestador, vigencia) -> mayor nómina reportada (un NIT puede salir en dos grupos)
    serie: dict[tuple[int, int], float] = {}
    for pid, vig, cuentas in registros:
        if vig not in costo:
            continue
        v = nomina(cuentas)
        if v <= 0:
            continue
        llave = (pid, vig)
        if v > serie.get(llave, 0):
            serie[llave] = v

    anual = []
    personal: dict[int, dict[int, int]] = {}
    for (pid, vig), bruto in serie.items():
        n = bruto / costo[vig]
        if n < 0.5:
            continue
        n = max(1, round(n))
        personal.setdefault(pid, {})[vig] = n
        anual.append((pid, vig, round(bruto, 2), round(costo[vig], 2), n))

    with cx.cursor() as cur, cur.copy(
        """COPY reps.estimacion_personal_anual
           (prestador_id, vigencia, nomina_cop, costo_trabajador, personal) FROM STDIN"""
    ) as cp:
        for fila in anual:
            cp.write_row(fila)
    cx.commit()
    print(f"· serie anual: {len(anual):,} filas sobre {len(personal):,} prestadores")

    # --------------------------------------------------- rasgos de capacidad
    with cx.cursor() as cur:
        cur.execute("""
            SELECT r.prestador_id, p.clase_prestador,
                   coalesce(sum(c.cantidad) FILTER (WHERE c.grupo_capacidad='CAMAS'),0),
                   coalesce(sum(c.cantidad) FILTER (WHERE c.grupo_capacidad='CAMILLAS'),0),
                   coalesce(sum(c.cantidad) FILTER (WHERE c.grupo_capacidad='CONSULTORIOS'),0),
                   coalesce(sum(c.cantidad) FILTER (WHERE c.grupo_capacidad='SALAS'),0),
                   coalesce(sum(c.cantidad) FILTER (WHERE c.grupo_capacidad='SILLAS'),0),
                   coalesce(sum(c.cantidad) FILTER (WHERE c.grupo_capacidad='AMBULANCIAS'),0),
                   count(DISTINCT s.id),
                   (SELECT count(*) FROM reps.sede_servicio v
                      JOIN reps.sede s2 ON s2.id = v.sede_id
                      JOIN reps.registro_habilitacion r2 ON r2.id = s2.registro_id
                     WHERE r2.prestador_id = r.prestador_id)
            FROM reps.registro_habilitacion r
            JOIN reps.prestador p ON p.id = r.prestador_id
            JOIN reps.sede s ON s.registro_id = r.id
            LEFT JOIN reps.sede_capacidad c ON c.sede_id = s.id
            GROUP BY r.prestador_id, p.clase_prestador""")
        capacidad = {r[0]: (r[1], [float(x) for x in r[2:]]) for r in cur}

    actual = {pid: v[VIGENCIA_ACTUAL] for pid, v in personal.items() if VIGENCIA_ACTUAL in v}
    es_ips = lambda pid: capacidad[pid][0].startswith("Instituciones")

    # ------------------------------------------------------ ajuste del modelo
    pids = [p for p in actual if p in capacidad and es_ips(p)]
    X = np.array([capacidad[p][1] for p in pids])
    y = np.array([actual[p] for p in pids], dtype=float)
    keep = y <= np.quantile(y, 0.99)
    coef = nnls(X[keep], y[keep])
    pred = np.clip(X[keep] @ coef, 1e-9, None)
    ss_res = float(((y[keep] - pred) ** 2).sum())
    ss_tot = float(((y[keep] - y[keep].mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    ratio = y[keep] / pred
    lo_f, hi_f = float(np.quantile(ratio, 0.10)), float(np.quantile(ratio, 0.90))
    print(f"· modelo de capacidad sobre {keep.sum():,} IPS · R² = {r2:.3f} · "
          f"banda ×{lo_f:.2f}–×{hi_f:.2f}")
    for n_, c in zip(RASGOS, coef):
        print(f"    {n_:14s} {c:8.3f}")

    # ---------------------------------------------------------- escritura
    filas = []
    for pid, n in actual.items():
        serie_pid = personal[pid]
        p19, p20, p21 = serie_pid.get(2019), serie_pid.get(2020), serie_pid.get(2021)
        pct = cagr = None
        # Base mínima de 3 personas: sobre 1 o 2, un cambio de una persona es 50–100 %.
        if p19 and p21 and p19 >= 3:
            pct = (p21 / p19 - 1) * 100
            cagr = ((p21 / p19) ** 0.5 - 1) * 100
        filas.append((
            pid, n, max(1, round(n / (1 + BANDA))), max(1, round(n / (1 - BANDA))),
            max(1, round(n * TERCERIZACION_P25)), max(1, round(n * TERCERIZACION_P75)),
            "nomina", "media", p19, p20, p21,
            None if pct is None else round(pct, 2),
            None if cagr is None else round(cagr, 2),
            clasificar(pct),
            json.dumps({"nomina_cop": round(serie.get((pid, VIGENCIA_ACTUAL), 0)),
                        "costo_por_trabajador_cop": round(costo[VIGENCIA_ACTUAL]),
                        "vigencia": VIGENCIA_ACTUAL, "banda": BANDA,
                        "ancla_siho": SIHO_PLANTA, "fuente_ancla": SIHO_URL,
                        "factor_tercerizacion": [TERCERIZACION_P25, TERCERIZACION_P75]},
                       ensure_ascii=False),
            NOTA_NOMINA))

    omitidos = 0
    for pid, (clase, rasgos) in capacidad.items():
        if pid in actual:
            continue
        if clase == "Profesional Independiente":
            omitidos += 1
            continue
        v = max(1.0, float(np.dot(np.array(rasgos), coef)))
        filas.append((
            pid, round(v), max(1, round(v * lo_f)), max(1, round(v * hi_f)),
            max(1, round(v * TERCERIZACION_P25)), max(1, round(v * TERCERIZACION_P75)),
            "capacidad", "baja", None, None, None, None, None, "sin_serie",
            json.dumps({**dict(zip(RASGOS, [int(x) for x in rasgos])),
                        "coeficientes": dict(zip(RASGOS, [round(float(c), 4) for c in coef])),
                        "r2": round(r2, 3), "banda_p10_p90": [round(lo_f, 3), round(hi_f, 3)]},
                       ensure_ascii=False),
            NOTA_CAPACIDAD))

    with cx.cursor() as cur, cur.copy(
        """COPY reps.estimacion_personal
           (prestador_id, personal_estimado, rango_bajo, rango_alto,
            fuerza_laboral_bajo, fuerza_laboral_alto, metodo, confianza,
            personal_2019, personal_2020, personal_2021,
            crecimiento_pct, crecimiento_anual, tendencia, insumos, nota) FROM STDIN"""
    ) as cp:
        for f in filas:
            cp.write_row(f)
    cx.commit()
    print(f"· no estimados (profesionales independientes): {omitidos:,}")

    with cx.cursor() as cur:
        cur.execute("""SELECT metodo, count(*), sum(personal_estimado)
                       FROM reps.estimacion_personal GROUP BY 1 ORDER BY 1""")
        print("\n metodo      | prestadores | planta sumada")
        for m, c, s in cur:
            print(f" {m:11s} | {c:11,} | {s:13,}")
        cur.execute("""SELECT tendencia, count(*), round(avg(crecimiento_pct),1)
                       FROM reps.estimacion_personal
                       WHERE tendencia <> 'sin_serie' GROUP BY 1 ORDER BY 2 DESC""")
        print("\n tendencia    | prestadores | crecimiento medio %")
        for t, c, a in cur:
            print(f" {t:12s} | {c:11,} | {a!s:>18}")
    cx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
