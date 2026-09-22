"""
Planta real de las entidades públicas, desde SIGEP II.

    python src/empleo_publico.py --dsn "$DSN"
    python src/empleo_publico.py --dsn "$DSN" --simular   # sólo contar

Carga el dataset «Caracterización del Empleo Público» de Función Pública
(datos.gov.co `h8rs-jxum`) en `reps.planta_entidad`, y lo cruza con
`reps.prestador` por NIT.

─────────────────────────────────────────────────────────────────────────────
ESTO NO LLENA `prestador.numero_empleados`, Y ES A PROPÓSITO

Esa columna dice «SIEMPRE NULL» y el comentario explica por qué: ninguna
fuente pública expone la planta POR PRESTADOR. Sigue siendo cierto. Lo que
publica SIGEP es la planta de la ENTIDAD, que es otro grano:

    MUNICIPIO DE VILLAVICENCIO   SIGEP 2.457   estimación del prestador 3
    SENA                         SIGEP 9.432   estimación del prestador 90
    ASSBASALUD ESE               SIGEP    86   estimación del prestador 89

Donde la entidad ES el prestador —una ESE— las dos cifras coinciden. Donde el
prestador es una dependencia de algo más grande —el consultorio de una
alcaldía, la unidad de salud del SENA— miden cosas distintas y ninguna está
mal. Meterlas en la misma columna destruiría esa distinción justo cuando más
importa.

Por eso van en su propia tabla, con su fuente y su corte, y el que consulta
decide cuál usa.

─────────────────────────────────────────────────────────────────────────────
PARA QUÉ SIRVE, CONCRETAMENTE

El dataset no trae sólo un conteo. Trae la distribución por edad, el nivel
educativo y el SALARIO PROMEDIO. Con eso se cotiza un colectivo antes de
llamar:

  · Vida grupo se tarifica por edad y valor asegurado. Acá están los cinco
    tramos etarios y el salario promedio.
  · ARL se liquida sobre la nómina. nómina ≈ empleados × salario promedio,
    que esta tabla calcula.
  · Salud y bienestar dependen de la pirámide de edad, que viene desagregada.

Lo que NO trae son personas: ni nombres, ni cargos, ni correos. Para eso
siguen estando los datasets que cada entidad publica por la Ley 1712, que son
cientos y sin esquema común.
"""
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request

import psycopg

DATASET = "h8rs-jxum"
SOCRATA = "https://www.datos.gov.co/resource/%s.json?%s"

DDL = """
CREATE SCHEMA IF NOT EXISTS reps;

CREATE TABLE IF NOT EXISTS reps.planta_entidad (
    nit                 text PRIMARY KEY,
    codigo_sigep        text,
    nombre_entidad      text NOT NULL,

    -- Clasificación de Función Pública. Sirve para segmentar: una ESE
    -- municipal y un ministerio se venden distinto.
    orden               text,   -- NACIONAL | TERRITORIAL
    clasificacion       text,   -- MUNICIPAL | DEPARTAMENTAL | RAMA EJECUTIVA…
    sector              text,
    nivel               text,   -- CENTRALIZADO | DESCENTRALIZADO
    naturaleza_juridica text,

    -- Corte del dato. SIGEP publica por año y mes; se guarda para saber
    -- cuándo se midió y no dar por vigente algo de hace tres años.
    anio                smallint NOT NULL,
    mes                 smallint NOT NULL,

    -- El total sale de sumar los tres géneros y no de un campo «total»,
    -- porque el dataset no trae uno.
    total_empleados     integer NOT NULL,
    genero_hombre       integer NOT NULL DEFAULT 0,
    genero_mujer        integer NOT NULL DEFAULT 0,
    genero_no_binario   integer NOT NULL DEFAULT 0,

    -- La pirámide de edad. Es lo que tarifica vida grupo: un colectivo con
    -- 40 % por encima de 50 años cuesta otra cosa que uno de veinteañeros.
    edad_hasta_29       integer NOT NULL DEFAULT 0,
    edad_30_39          integer NOT NULL DEFAULT 0,
    edad_40_49          integer NOT NULL DEFAULT 0,
    edad_50_62          integer NOT NULL DEFAULT 0,
    edad_63_y_mas       integer NOT NULL DEFAULT 0,

    educ_primaria       integer NOT NULL DEFAULT 0,
    educ_secundaria     integer NOT NULL DEFAULT 0,
    educ_tecnica        integer NOT NULL DEFAULT 0,
    educ_profesional    integer NOT NULL DEFAULT 0,
    educ_especializacion integer NOT NULL DEFAULT 0,
    educ_maestria       integer NOT NULL DEFAULT 0,
    educ_doctorado      integer NOT NULL DEFAULT 0,

    -- Cuántas unidades de SIGEP se sumaron bajo este NIT. Un 6 en el
    -- Ministerio de Defensa avisa de que el dato agrega varias dependencias.
    unidades            smallint NOT NULL DEFAULT 1,

    salario_promedio    numeric(14,2),
    -- Derivado, no observado: empleados × salario promedio. Es la base de
    -- liquidación de ARL y el orden de magnitud de un vida grupo.
    nomina_mensual_est  numeric(18,2),

    cargado_en          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS planta_entidad_total_idx
    ON reps.planta_entidad(total_empleados DESC);

COMMENT ON TABLE reps.planta_entidad IS
    'Planta de las entidades públicas según SIGEP II (Función Pública, '
    'dataset h8rs-jxum). El grano es la ENTIDAD, no el prestador: para una '
    'ESE coinciden, para el consultorio de una alcaldía no. No se mezcla con '
    'reps.estimacion_personal por esa razón.';

COMMENT ON COLUMN reps.planta_entidad.nomina_mensual_est IS
    'Derivado: total_empleados * salario_promedio. Orden de magnitud para '
    'liquidar ARL y dimensionar un vida grupo, no una cifra contable.';
"""

# Campo del dataset → columna. Los nombres vienen truncados y con la tilde
# codificada por Socrata; se mapean explícitamente en vez de transformarlos,
# porque un cambio de nombre arriba tiene que fallar aquí y no en silencio.
NUMERICOS = {
    "genero_hombre": "genero_hombre",
    "genero_mujer": "genero_mujer",
    "genero_no_binario": "genero_no_binario",
    "edad_menor_o_igual_a_29": "edad_hasta_29",
    "edad_30_39": "edad_30_39",
    "edad_40_49": "edad_40_49",
    "edad_50_62": "edad_50_62",
    "edad_63_y_mas": "edad_63_y_mas",
    "nivel_educativo_primaria": "educ_primaria",
    "nivel_educativo_secundaria": "educ_secundaria",
    "nivel_educativo_t_cnica_tecnol": "educ_tecnica",
    "nivel_educativo_profesional": "educ_profesional",
    "nivel_educativo_especializaci": "educ_especializacion",
    "nivel_educativo_maestr_a": "educ_maestria",
    "nivel_educativo_doctorado": "educ_doctorado",
}
TEXTOS = {
    "c_digo_sigep": "codigo_sigep",
    "nombre_de_la_entidad": "nombre_entidad",
    "orden": "orden",
    "clasificaci_n_org_nica": "clasificacion",
    "sector": "sector",
    "nivel": "nivel",
    "naturaleza_jur_dica": "naturaleza_juridica",
}


def traer() -> list[dict]:
    """
    El dataset entero. Son ~4.150 filas: cabe en una petición y no hay que
    paginar. Se pide con $limit alto y se comprueba que no llegue justo en el
    tope, que sería la señal de que Socrata cortó.
    """
    tope = 20000
    url = SOCRATA % (DATASET, urllib.parse.urlencode({"$limit": tope}))
    with urllib.request.urlopen(url, timeout=120) as fh:
        filas = json.load(fh)
    if len(filas) >= tope:
        raise SystemExit(f"⚠ llegaron {len(filas)} filas, el tope era {tope}: "
                         "el dataset creció y hay que paginar")
    return filas


def reparar(t):
    """
    El dataset llega con UTF-8 leído como cp1252: «ITAGÃœI» por «ITAGÜI»,
    «ESTABLECIMIENTO PÃšBLICO» por «PÚBLICO». Se deshace el doble encoding.

    Si el texto no es reparable —porque ya está bien— se devuelve tal cual:
    intentarlo y fallar es la forma de distinguir los dos casos sin tener que
    adivinar cuál de los dos vino.
    """
    if not isinstance(t, str):
        return t
    # Ni cp1252 ni latin-1 solos alcanzan, y por motivos opuestos:
    #   «ITAGÃœI»  trae «œ» (U+0153), que latin-1 no tiene → falla latin-1
    #   «ALCALDÃ\x8dA» trae U+008D, un hueco sin asignar en cp1252 → falla cp1252
    # Se codifica carácter a carácter probando cp1252 primero y cayendo a
    # latin-1 para los controles, que es exactamente donde difieren.
    crudo = bytearray()
    for ch in t:
        for codec in ("cp1252", "latin-1"):
            try:
                crudo += ch.encode(codec)
                break
            except UnicodeEncodeError:
                continue
        else:
            return t      # un carácter que no viene de este doble encoding
    try:
        return crudo.decode("utf-8")
    except UnicodeDecodeError:
        return t          # no estaba mal codificado; se deja como vino


def entero(f: dict, clave: str) -> int:
    v = f.get(clave)
    try:
        return int(float(v)) if v not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--simular", action="store_true")
    args = ap.parse_args()

    filas = traer()
    print(f"── {len(filas):,} filas en {DATASET}")

    listas, sin_nit, sin_gente = [], 0, 0
    for f in filas:
        nit = (f.get("nit") or "").strip()
        # El dataset trae la cadena «NULL» literal en 497 filas, no un nulo de
        # verdad. Un `if not nit` la deja pasar y crea una entidad fantasma con
        # NIT «NULL» que acumularía la planta de media administración central.
        if not nit or nit.upper() in ("NULL", "N/A", "NA", "0"):
            sin_nit += 1
            continue
        total = sum(entero(f, k) for k in
                    ("genero_hombre", "genero_mujer", "genero_no_binario"))
        if total <= 0:
            sin_gente += 1
            continue
        fila = {col: reparar(f.get(campo)) or None for campo, col in TEXTOS.items()}
        fila.update({col: entero(f, campo) for campo, col in NUMERICOS.items()})
        salario = None
        try:
            s = f.get("salario_mensual_promedio")
            salario = float(s) if s not in (None, "") else None
        except (TypeError, ValueError):
            salario = None
        fila.update({
            "nit": nit,
            "anio": entero(f, "a_o"),
            "mes": entero(f, "mes"),
            "total_empleados": total,
            "salario_promedio": salario,
            "nomina_mensual_est": round(total * salario, 2) if salario else None,
        })
        listas.append(fila)

    # ── Agregación por NIT ──────────────────────────────────────────────────
    #
    # Varias unidades comparten NIT porque son el mismo empleador jurídico: la
    # Alcaldía de Medellín y su Concejo, el Ministerio de Defensa con sus seis
    # dependencias. Se SUMAN, no se elige una.
    #
    # Quedarse con una arbitraria era el defecto de la primera versión: para el
    # NIT 890905211 podía reportar los 20 del Concejo en vez de los 6.428 de la
    # Alcaldía. Para vender un colectivo el asegurado es la planta del NIT, que
    # es quien firma el contrato y paga la nómina.
    CONTEOS = list(NUMERICOS.values()) + ["total_empleados"]
    grupos: dict[str, list[dict]] = {}
    for f in listas:
        grupos.setdefault(f["nit"], []).append(f)
    colapsadas = len(listas) - len(grupos)

    listas = []
    for nit, us in grupos.items():
        # La unidad con más planta da el nombre y la clasificación:
        # «ALCALDIA DE MEDELLIN» describe al empleador mejor que «CONCEJO».
        principal = max(us, key=lambda x: x["total_empleados"])
        a = {campo: principal[campo] for campo in TEXTOS.values()}
        a["nit"] = nit
        a["anio"] = max(x["anio"] for x in us)
        a["mes"] = max(x["mes"] for x in us)
        a["unidades"] = len(us)
        for c in CONTEOS:
            a[c] = sum(x[c] for x in us)

        # Salario ponderado por planta. Promediar a secas daría el mismo peso a
        # una alcaldía de 6.408 personas que a un concejo de 20.
        gente_con_salario = sum(x["total_empleados"] for x in us if x["salario_promedio"])
        if gente_con_salario:
            a["salario_promedio"] = round(
                sum(x["salario_promedio"] * x["total_empleados"]
                    for x in us if x["salario_promedio"]) / gente_con_salario, 2)
        else:
            a["salario_promedio"] = None
        a["nomina_mensual_est"] = (round(a["total_empleados"] * a["salario_promedio"], 2)
                                   if a["salario_promedio"] else None)
        listas.append(a)

    print(f"   con NIT y al menos un empleado   {len(listas) + colapsadas:,}")
    if sin_nit:
        print(f"   descartadas sin NIT              {sin_nit:,}")
    if sin_gente:
        print(f"   descartadas con planta cero      {sin_gente:,}")
    if colapsadas:
        # No es un descarte silencioso: varias unidades comparten NIT —una
        # alcaldía y su concejo, por ejemplo— y sólo cabe una por clave.
        print(f"   colapsadas por NIT repetido      {colapsadas:,}")
    print(f"   entidades a cargar               {len(listas):,}")
    cortes = sorted({(f["anio"], f["mes"]) for f in listas})
    print(f"   cortes presentes                 {', '.join(f'{a}-{m:02d}' for a, m in cortes)}")

    cx = psycopg.connect(args.dsn)
    with cx.cursor() as cur:
        cur.execute(DDL)
        if not args.simular:
            cols = list(listas[0].keys())
            cur.execute("TRUNCATE reps.planta_entidad")
            cur.executemany(
                f"INSERT INTO reps.planta_entidad ({','.join(cols)}) "
                f"VALUES ({','.join('%%(%s)s' % c for c in cols)})",
                listas)
            cx.commit()

        # El cruce, que es el punto de todo esto. En simulacro se mide contra
        # lo que se acaba de leer y no contra la tabla —que está vacía— porque
        # si no el simulacro informaría cero cruces siempre y no serviría para
        # decidir nada.
        if args.simular:
            cur.execute("""
                SELECT count(*), count(*) FILTER (WHERE p.es_ips),
                       count(*) FILTER (WHERE p.es_ese), coalesce(sum(d.total),0)
                FROM unnest(%s::text[], %s::int[]) AS d(nit, total)
                JOIN reps.prestador p ON p.numero_identificacion = d.nit
            """, ([f["nit"] for f in listas], [f["total_empleados"] for f in listas]))
        else:
            cur.execute("""
                SELECT count(*) FILTER (WHERE p.id IS NOT NULL),
                       count(*) FILTER (WHERE p.es_ips),
                       count(*) FILTER (WHERE p.es_ese),
                       coalesce(sum(pe.total_empleados) FILTER (WHERE p.id IS NOT NULL), 0)
                FROM reps.planta_entidad pe
                LEFT JOIN reps.prestador p ON p.numero_identificacion = pe.nit
            """)
        cruzan, ips, ese, empleados = cur.fetchone()
    cx.close()

    marca = "[simulacro] " if args.simular else ""
    print(f"\n── {marca}cruce con reps.prestador")
    print(f"   entidades que cruzan por NIT     {cruzan:,}")
    print(f"     de ellas IPS                   {ips:,}")
    print(f"     de ellas ESE                   {ese:,}")
    print(f"   empleados cubiertos              {empleados:,}")
    if not args.simular:
        print("\n   para verlo en Ariad: PYTHONPATH=src python src/build_tablero.py --dsn \"$DSN\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
