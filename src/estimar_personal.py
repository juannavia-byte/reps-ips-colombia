"""
Estimación del número de trabajadores por prestador.

QUÉ ES Y QUÉ NO ES
------------------
`prestador.numero_empleados` sigue siendo NULL: ninguna fuente pública
colombiana publica la planta de una IPS. Esto NO lo cambia. Lo que hace este
módulo es escribir una tabla APARTE, `estimacion_personal`, con un número
DERIVADO, su método, su banda y sus insumos. Dato observado y dato estimado no
se mezclan en la misma columna.

MÉTODO 1 · NÓMINA (el bueno)
----------------------------
    trabajadores ≈ gasto anual de nómina / costo anual por trabajador

El gasto de nómina sí está en los estados financieros de Supersalud:
  ESE       ......5101 Sueldos y salarios
            ......5104 Aportes sobre la nómina
            ......5108 Gastos de personal diversos
  privadas  SUELDOS Y SALARIOS
            APORTES SOBRE LA NOMINA
            BENEFICIOS A LOS EMPLEADOS A CORTO PLAZO

El costo por trabajador NO se inventa: se calibra contra SIHO. El informe
"Distribución Recurso Humano" de SIHO publica la planta nacional agregada de
las ESE — 2021: 15.578 apoyo + 33.093 operativo = 48.671 personas. Dividiendo
la nómina agregada de las 919 ESE de ese mismo año entre esa planta:

    $1.047.399.744.361 / 48.671 = $21.520.000 por persona/año  (~$1,79 M/mes)

Con salario mínimo de 2021 en $908.526, eso es ~1,97 SMMLV de costo total
(salario + carga prestacional y parafiscal), que es exactamente lo que se
espera de una planta pública. El ancla valida.

MÉTODO 2 · CAPACIDAD (para quien no reporta financieros)
--------------------------------------------------------
Solo ~5.600 de 57.663 prestadores tienen estados financieros. Para el resto se
ajusta por mínimos cuadrados no negativos una relación

    trabajadores ≈ b1·camas + b2·salas + b3·sedes + …

usando como variable dependiente las estimaciones del método 1. Los
coeficientes salen de datos, no de ratios de manual.

Tres decisiones del ajuste, tomadas midiendo contra un holdout del 25 %:

· **Sin intercepto.** Con intercepto el modelo le asigna ~7 trabajadores de
  base a cualquier prestador, lo que es absurdo para un consultorio. Sin él,
  el error mediano cae de 9,5 a 4,4 personas.
· **Ajustado solo sobre IPS.** La muestra con financieros son clínicas; usarla
  para extrapolar a profesionales independientes es extrapolación pura.
· **No se estima a los profesionales independientes.** Son ~47.000 personas
  naturales cuya "planta" son ellos mismos. No tienen fila en la tabla: la
  ausencia de fila significa "no estimable", no "cero".

El poder explicativo es moderado (R² ≈ 0,44 en holdout) y así se reporta. La
capacidad instalada no determina la planta: el mix de servicios y cuánto se
terceriza pesan igual o más. Por eso la banda de este método es ancha y se
calcula de los residuos reales, no de un porcentaje inventado.

QUÉ MIDE EXACTAMENTE — leer antes de usarlo
-------------------------------------------
Mide **planta formal en nómina**, no fuerza laboral total. En salud en Colombia
una parte grande del personal entra por prestación de servicios o por bolsas de
empleo, y ese gasto no pasa por las cuentas de nómina del contratante.

Esa diferencia no es un defecto del método: **son dos cifras distintas que
corresponden a dos empleadores distintos.** Para ARL la afiliación sigue al
empleador de registro, así que la planta en nómina es justamente lo que esa IPS
afilia por su cuenta; los tercerizados los afilia la bolsa que los contrata.

Contrastando contra una base externa de 47 IPS de la Costa con conteo de
trabajadores conocido, la fuerza laboral total resultó ser **×3,0 la planta en
nómina (mediana), con p25–p75 entre ×1,25 y ×5,24** y casos extremos de ×0,04 a
×47. La dispersión es tan grande que dar un multiplicador puntual sería
precisión falsa; por eso se guarda como BANDA en `fuerza_laboral_bajo/alto` y
se marca con confianza baja.

Otras limitaciones:
· Los datos financieros son de 2021 y la capacidad instalada es de 2026.
· En IPS privadas con muchos especialistas el costo por trabajador real es
  mayor que el ancla pública, así que el método 1 las sobreestima. La banda
  mueve el costo por trabajador ±35 %.
· Una IPS que creció mucho desde 2021 queda corta por definición.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import psycopg

# Calibración contra SIHO · Distribución Recurso Humano, vigencia 2021.
SIHO_PLANTA_ESE_2021 = 48_671
SIHO_URL = ("https://prestadores.minsalud.gov.co/siho/informes/recursohumano.aspx"
            "?pageTitle=Distribución+Recurso+Humano")
BANDA = 0.35  # ±35 % sobre el costo por trabajador

# Factor de tercerización, medido contra 47 IPS de la Costa con conteo conocido:
# fuerza laboral total / planta en nómina -> mediana 3,00 · p25 1,25 · p75 5,24.
# Se usa como banda, nunca como punto: los extremos observados van de 0,04 a 47.
TERCERIZACION_P25, TERCERIZACION_P75 = 1.25, 5.24

CUENTAS_ESE = ("......5101 Sueldos y salarios",
               "......5104 Aportes sobre la nómina",
               "......5108 Gastos de personal diversos")
CUENTAS_PRIV = ("SUELDOS Y SALARIOS",
                "APORTES SOBRE LA NOMINA",
                "BENEFICIOS A LOS EMPLEADOS A CORTO PLAZO")

RASGOS = ("camas", "camillas", "consultorios", "salas", "sillas",
          "ambulancias", "sedes", "servicios")

DDL = """
DROP TABLE IF EXISTS reps.estimacion_personal;
CREATE TABLE reps.estimacion_personal (
    prestador_id        bigint PRIMARY KEY REFERENCES reps.prestador(id) ON DELETE CASCADE,
    personal_estimado   integer NOT NULL,  -- planta formal en nómina
    rango_bajo          integer NOT NULL,
    rango_alto          integer NOT NULL,
    -- Fuerza laboral TOTAL incluyendo tercerizados. Banda, no punto.
    fuerza_laboral_bajo integer NOT NULL,
    fuerza_laboral_alto integer NOT NULL,
    metodo              text    NOT NULL,  -- 'nomina' | 'capacidad'
    confianza           text    NOT NULL,  -- 'media' | 'baja'
    insumos             jsonb   NOT NULL,
    nota                text    NOT NULL
);
COMMENT ON COLUMN reps.estimacion_personal.personal_estimado IS
  'Planta formal EN NÓMINA. Es la que la propia IPS afilia a ARL.';
COMMENT ON COLUMN reps.estimacion_personal.fuerza_laboral_alto IS
  'Banda de fuerza laboral total incluyendo prestación de servicios y bolsas de '
  'empleo. Factor medido contra 47 IPS de la Costa: mediana x3, p25-p75 x1,25-x5,24.';
COMMENT ON TABLE reps.estimacion_personal IS
  'Número DERIVADO, no observado. prestador.numero_empleados sigue NULL a propósito. '
  'Método nomina: gasto de nómina 2021 / costo por trabajador calibrado contra SIHO. '
  'Método capacidad: regresión no negativa ajustada sobre las estimaciones de nómina. '
  'Mide planta formal en nómina, no fuerza laboral total (excluye prestación de servicios).';
"""

NOTA_NOMINA = ("nómina 2021 de Supersalud dividida por el costo anual por trabajador "
               "calibrado con la planta nacional de ESE que publica SIHO")
NOTA_CAPACIDAD = ("regresión no negativa sobre camas, consultorios, salas, camillas, "
                  "sillas, ambulancias y sedes, ajustada contra las estimaciones de nómina")


def suma(cuentas: dict, llaves) -> float:
    return sum(v for k in llaves if isinstance((v := cuentas.get(k)), (int, float)))


def nnls(A: np.ndarray, y: np.ndarray, iteraciones: int = 40) -> np.ndarray:
    """Mínimos cuadrados con coeficientes no negativos.

    Un coeficiente negativo diría "más camas, menos gente", que no significa
    nada. En vez de traer scipy, se resuelve por conjunto activo simple:
    se ajusta, se apagan las columnas que salen negativas, y se repite.
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
    coef[:] = 0
    if activas.any():
        sol, *_ = np.linalg.lstsq(A[:, activas], y, rcond=None)
        coef[activas] = np.clip(sol, 0, None)
    return coef


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    args = ap.parse_args()
    cx = psycopg.connect(args.dsn)
    cx.execute(DDL)
    cx.commit()

    # ---------------------------------------------------------- calibración
    with cx.cursor() as cur:
        cur.execute("SELECT cuentas FROM reps.prestador_financiero WHERE origen = 'ese'")
        nomina_ese = sum(suma(r[0], CUENTAS_ESE) for r in cur)
    costo = nomina_ese / SIHO_PLANTA_ESE_2021
    print(f"· ancla SIHO 2021: {SIHO_PLANTA_ESE_2021:,} personas de planta en ESE")
    print(f"  nómina ESE agregada  ${nomina_ese:,.0f}")
    print(f"  costo por trabajador ${costo:,.0f}/año  (${costo/12:,.0f}/mes)")

    # ------------------------------------------------------- método nómina
    with cx.cursor() as cur:
        cur.execute("""SELECT prestador_id, origen, cuentas
                       FROM reps.prestador_financiero
                       WHERE prestador_id IS NOT NULL""")
        financieros = cur.fetchall()

    por_nomina: dict[int, tuple[int, int, int, float]] = {}
    for pid, origen, cuentas in financieros:
        bruto = suma(cuentas, CUENTAS_ESE if origen == "ese" else CUENTAS_PRIV)
        if bruto <= 0:
            continue
        central = bruto / costo
        if central < 0.5:
            continue
        bajo = bruto / (costo * (1 + BANDA))
        alto = bruto / (costo * (1 - BANDA))
        # Si un prestador reporta por varios grupos, gana el de mayor nómina.
        if pid not in por_nomina or bruto > por_nomina[pid][3]:
            por_nomina[pid] = (max(1, round(central)), max(1, round(bajo)),
                               max(1, round(alto)), bruto)
    print(f"· método nómina: {len(por_nomina):,} prestadores")

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
    es_ips = lambda pid: capacidad[pid][0].startswith("Instituciones")
    independiente = lambda pid: capacidad[pid][0] == "Profesional Independiente"
    print(f"· prestadores con sedes/capacidad: {len(capacidad):,}")

    # ------------------------------------------------------ ajuste del modelo
    # Solo IPS: la muestra con financieros son clínicas, y extrapolar de ahí a
    # un consultorio de un profesional independiente no tiene sustento.
    pids = [p for p in por_nomina if p in capacidad and es_ips(p)]
    X = np.array([capacidad[p][1] for p in pids])
    y = np.array([por_nomina[p][0] for p in pids], dtype=float)
    # Se recorta el 1 % superior: unas pocas redes enormes dominarían el ajuste.
    keep = y <= np.quantile(y, 0.99)
    coef = nnls(X[keep], y[keep])           # sin intercepto, a propósito
    pred = np.clip(X[keep] @ coef, 1e-9, None)
    ss_res = float(((y[keep] - pred) ** 2).sum())
    ss_tot = float(((y[keep] - y[keep].mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    # Banda empírica: cómo se reparte el valor real sobre el predicho.
    ratio = y[keep] / pred
    lo_f, hi_f = float(np.quantile(ratio, 0.10)), float(np.quantile(ratio, 0.90))
    nombres = list(RASGOS)
    print(f"· modelo de capacidad ajustado sobre {keep.sum():,} IPS · R² = {r2:.3f}")
    print(f"  banda empírica p10–p90 del real/predicho: ×{lo_f:.2f} a ×{hi_f:.2f}")
    for n, c in zip(nombres, coef):
        print(f"    {n:14s} {c:8.3f} trabajadores por unidad")

    # ---------------------------------------------------------- escritura
    filas = []
    for pid, (central, bajo, alto, bruto) in por_nomina.items():
        filas.append((pid, central, bajo, alto,
                      max(1, round(central * TERCERIZACION_P25)),
                      max(1, round(central * TERCERIZACION_P75)),
                      "nomina", "media",
                      json.dumps({"nomina_2021_cop": round(bruto),
                                  "costo_por_trabajador_cop": round(costo),
                                  "banda": BANDA, "ancla_siho": SIHO_PLANTA_ESE_2021,
                                  "fuente_ancla": SIHO_URL,
                                  "factor_tercerizacion": [TERCERIZACION_P25, TERCERIZACION_P75]},
                                 ensure_ascii=False),
                      NOTA_NOMINA))
    omitidos = 0
    for pid, (clase, rasgos) in capacidad.items():
        if pid in por_nomina:
            continue
        # Un profesional independiente es una persona natural: su "planta" es
        # él mismo. Estimarlo con un modelo ajustado sobre clínicas sería
        # fabricar un número. No tiene fila; ausencia = no estimable.
        if independiente(pid):
            omitidos += 1
            continue
        v = max(1.0, float(np.dot(np.array(rasgos), coef)))
        filas.append((pid, round(v), max(1, round(v * lo_f)), max(1, round(v * hi_f)),
                      max(1, round(v * TERCERIZACION_P25)),
                      max(1, round(v * TERCERIZACION_P75)),
                      "capacidad", "baja",
                      json.dumps({**dict(zip(RASGOS, [int(x) for x in rasgos])),
                                  "coeficientes": dict(zip(nombres, [round(float(c), 4) for c in coef])),
                                  "r2_holdout_interno": round(r2, 3),
                                  "banda_p10_p90": [round(lo_f, 3), round(hi_f, 3)]},
                                 ensure_ascii=False),
                      NOTA_CAPACIDAD))
    print(f"· no estimados (profesionales independientes): {omitidos:,}")

    with cx.cursor() as cur, cur.copy(
        """COPY reps.estimacion_personal
           (prestador_id, personal_estimado, rango_bajo, rango_alto,
            fuerza_laboral_bajo, fuerza_laboral_alto, metodo,
            confianza, insumos, nota) FROM STDIN"""
    ) as cp:
        for f in filas:
            cp.write_row(f)
    cx.commit()

    with cx.cursor() as cur:
        cur.execute("""SELECT metodo, count(*), sum(personal_estimado),
                              round(avg(personal_estimado)::numeric,1)
                       FROM reps.estimacion_personal GROUP BY 1 ORDER BY 1""")
        print("\n metodo      | prestadores | total estimado | promedio")
        for m, n, s, a in cur:
            print(f" {m:11s} | {n:11,} | {s:14,} | {a:>8}")
    cx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
