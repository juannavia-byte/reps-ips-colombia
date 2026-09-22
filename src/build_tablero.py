"""
Genera los datos del tablero y arma dos salidas:

  tablero/datos.js            datos sueltos, para servir index.html + datos.js
  dist/tablero.html           archivo ÚNICO con los datos embebidos, que abre
                              con doble clic sin servidor ni internet

Uso:  python src/build_tablero.py --dsn ... [--salida dist]

El tablero no aplica ningún filtro de segmento ni scoring: entrega el universo
y deja que quien lo use decida. Solo excluye a los profesionales
independientes, que son personas naturales sin planta que estimar.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import psycopg

# La tabla de cargos del motor, para que la pantalla de captura sugiera
# exactamente los que el clasificador sabe reconocer. Obliga a correr esto con
# PYTHONPATH=src, igual que `raspar_web.py`.
from enriquecer import CARGOS

CONSULTA = """
WITH sede_ref AS (
  -- Una sola sede de referencia: la principal; si no hay, la más antigua.
  -- Tomar departamento y municipio de agregaciones distintas los descuadraba.
  SELECT DISTINCT ON (r.prestador_id) r.prestador_id, s.departamento, s.municipio
  FROM reps.sede s JOIN reps.registro_habilitacion r ON r.id = s.registro_id
  ORDER BY r.prestador_id, (s.es_principal IS TRUE) DESC, s.id
), geo AS (
  SELECT r.prestador_id,
         count(DISTINCT s.municipio) n_mun, count(DISTINCT s.departamento) n_dep,
         string_agg(DISTINCT s.departamento, '|') deps,
         string_agg(DISTINCT s.municipio, '|') muns
  FROM reps.sede s JOIN reps.registro_habilitacion r ON r.id = s.registro_id
  GROUP BY r.prestador_id
), contacto AS (
  SELECT prestador_id, max(representante_legal) rep, max(telefono) tel,
         max(email) mail, max(nivel_atencion) nivel
  FROM reps.registro_habilitacion GROUP BY prestador_id
), amplitud AS (
  -- Insumos del score que el navegador necesita para recalcular con los sliders.
  SELECT r.prestador_id, count(DISTINCT sv.grupo_nombre) grupos,
         count(*) FILTER (WHERE sv.complejidad_alta) compl_alta,
         bool_or(sv.servicio_nombre ILIKE '%IMAGEN%' OR sv.servicio_nombre ILIKE '%RADIOLOG%'
              OR sv.servicio_nombre ILIKE '%RESONANCIA%' OR sv.servicio_nombre ILIKE '%TOMOGRAF%')::int imagen,
         bool_or(sv.servicio_nombre ILIKE '%LABORATORIO%')::int labo,
         bool_or(sv.servicio_nombre ILIKE '%FARMAC%' OR sv.servicio_nombre ILIKE '%MEDICAMENT%')::int farma,
         bool_or(sv.servicio_nombre ILIKE '%DIALISIS%' OR sv.servicio_nombre ILIKE '%DIÁLISIS%'
              OR sv.servicio_nombre ILIKE '%QUIMIOTERAP%' OR sv.servicio_nombre ILIKE '%RADIOTERAP%')::int altocosto,
         bool_or(sv.grupo_nombre='Internación')::int intern,
         bool_or(sv.grupo_nombre='Atención Inmediata')::int urg
  FROM reps.sede_servicio sv
  JOIN reps.sede s ON s.id = sv.sede_id
  JOIN reps.registro_habilitacion r ON r.id = s.registro_id
  GROUP BY 1
), presencia AS (
  -- Cuántas sedes tiene en cada municipio, para poder abrir el "+36" de la
  -- tabla y ver de qué está hecho.
  --
  -- El departamento va junto al municipio y no aparte: hay nombres repetidos
  -- entre departamentos —Candelaria existe en Atlántico y en Valle del Cauca—
  -- y sin él las dos filas se fundirían en una.
  --
  -- Se ordena por número de sedes y no alfabéticamente: quien abre esto quiere
  -- saber dónde está el peso de la operación, no recorrer una lista.
  SELECT prestador_id,
         string_agg(municipio || '~' || departamento || '~' || n, '|'
                    ORDER BY n DESC, municipio) sedes_mun
  FROM (SELECT r.prestador_id, s.municipio, s.departamento, count(*) n
        FROM reps.sede s
        JOIN reps.registro_habilitacion r ON r.id = s.registro_id
        GROUP BY 1, 2, 3) t
  GROUP BY prestador_id
), nuevos AS (
  SELECT r.prestador_id, count(*) n
  FROM reps.sede_servicio sv
  JOIN reps.sede s ON s.id = sv.sede_id
  JOIN reps.registro_habilitacion r ON r.id = s.registro_id
  WHERE sv.fecha_apertura >= (current_date - interval '12 months')
  GROUP BY r.prestador_id
)
SELECT p.numero_identificacion, p.digito_verificacion, p.razon_social,
       left(p.clase_prestador, 4), p.naturaleza_juridica,
       (p.es_ese IS TRUE)::int,
       sr.departamento, sr.municipio, g.n_mun, g.n_dep,
       c.nivel, c.rep, c.tel, c.mail,
       v.n_sedes, v.n_servicios, v.camas, v.salas_cirugia, v.consultorios, v.ambulancias,
       e.personal_estimado, e.fuerza_laboral_bajo, e.fuerza_laboral_alto,
       left(e.metodo, 3),
       e.personal_2019, e.personal_2021, e.crecimiento_pct, e.tendencia,
       CASE WHEN e.personal_2019 IS NOT NULL AND e.personal_2021 IS NOT NULL
            THEN e.personal_2021 - e.personal_2019 END AS delta_personas,
       v.fin_ingresos, v.fin_activos, coalesce(nv.n, 0),
       g.deps, g.muns,
       sc.score, sc.prioridad, sc.ltv, sc.cac,
       -- Insumos crudos del score, para que el panel de pesos pueda recalcular
       -- en el navegador sin volver a consultar la base.
       (jsonb_array_length(sc.desglose->'multiplicadores'->'senales_alto_riesgo') > 0)::int senal_riesgo,
       (sc.desglose->'multiplicadores'->>'deterioro_financiero')::numeric deterioro,
       coalesce(am.grupos, 0), coalesce(am.compl_alta, 0),
       coalesce(am.imagen,0), coalesce(am.labo,0), coalesce(am.farma,0),
       coalesce(am.altocosto,0), coalesce(am.intern,0), coalesce(am.urg,0),
       (p.naturaleza_juridica='Pública')::int,
       coalesce((SELECT sum(c.cantidad) FROM reps.sede_capacidad c
                 JOIN reps.sede sd ON sd.id=c.sede_id
                 JOIN reps.registro_habilitacion rr ON rr.id=sd.registro_id
                 WHERE rr.prestador_id=p.id AND c.concepto_nombre ILIKE 'Intensiva%'),0),
       pr.sedes_mun
FROM reps.prestador p
JOIN reps.v_prestador_completo v ON v.id = p.id
LEFT JOIN sede_ref sr ON sr.prestador_id = p.id
LEFT JOIN geo g       ON g.prestador_id = p.id
LEFT JOIN contacto c  ON c.prestador_id = p.id
LEFT JOIN nuevos nv   ON nv.prestador_id = p.id
LEFT JOIN reps.estimacion_personal e ON e.prestador_id = p.id
LEFT JOIN reps.score_icp sc ON sc.prestador_id = p.id
LEFT JOIN amplitud am ON am.prestador_id = p.id
LEFT JOIN presencia pr ON pr.prestador_id = p.id
WHERE p.clase_prestador <> 'Profesional Independiente'
ORDER BY coalesce(sc.score, -1) DESC
"""

COLS = ["nit", "dv", "razon_social", "clase", "naturaleza", "ese",
        "departamento", "municipio", "n_mun", "n_dep", "nivel",
        "rep_legal", "telefono", "email", "sedes", "servicios",
        "camas", "salas_cirugia", "consultorios", "ambulancias",
        "planta", "fl_bajo", "fl_alto", "metodo",
        "planta_2019", "planta_2021", "crecimiento", "tendencia", "delta_personas",
        "ingresos_mm", "activos_mm", "serv_nuevos_12m", "deps", "muns",
        "score", "prioridad", "ltv", "cac", "senal_riesgo", "deterioro",
        "grupos", "compl_alta", "imagenologia", "laboratorio", "farmacia",
        "alto_costo", "internacion", "urgencias", "es_publica", "camas_uci",
        "sedes_mun"]


CONSULTA_CONTACTOS = """
SELECT p.numero_identificacion,
  jsonb_agg(jsonb_build_array(
      pe.nombre, pe.cargo, pe.tier, pe.estado, pe.confianza,
      coalesce((SELECT jsonb_agg(jsonb_build_array(
                         ca.tipo, ca.valor, ca.ambito, ca.estado, ca.confianza)
                       ORDER BY ca.confianza DESC)
                FROM enriquecimiento.canal ca
                WHERE ca.persona_id = pe.id), '[]'::jsonb),
      -- Va AL FINAL y no junto al cargo, aunque ahí encajaría mejor de leer:
      -- el índice 5 es el array de vías y el tablero lo busca por posición.
      -- Meterla en medio desplazaría las vías un puesto y el perfil dejaría
      -- de pintar los contactos sin que ninguna prueba lo detectara.
      --
      -- La necesita la pantalla de captura para saber qué cargos ya están
      -- cubiertos. Sin ella habría que reconocer el cargo desde su texto en
      -- JavaScript, duplicando los quince patrones del motor en otro
      -- lenguaje, y la lista de «lo que falta» empezaría a discrepar de lo
      -- que el clasificador decide al guardar.
      pe.cargo_categoria)
    ORDER BY coalesce(pe.tier, 9), pe.confianza DESC) AS gente
FROM enriquecimiento.persona pe
JOIN reps.prestador p ON p.id = pe.prestador_id
WHERE pe.estado <> 'obsoleto'
  AND NOT EXISTS (SELECT 1 FROM enriquecimiento.exclusion x
                  WHERE x.prestador_id = pe.prestador_id
                     OR x.nombre_clave = pe.nombre_clave)
GROUP BY 1
"""


def leer_contactos(cx) -> dict:
    """
    Personas y canales por NIT, para que el perfil los muestre sin consultar.

    Va en el payload y no en una llamada aparte por dos razones: la herramienta
    tiene que seguir funcionando como archivo suelto sin red, y son 478 kB sobre
    3,9 MB — más barato que montar un endpoint y su autenticación.

    Los obsoletos y los excluidos NO salen. La lista de exclusión se aplica acá
    además de en la exportación: si alguien pidió no ser contactado, tampoco
    tiene por qué seguir apareciendo en pantalla.
    """
    try:
        with cx.cursor() as cur:
            cur.execute(CONSULTA_CONTACTOS)
            return {nit: gente for nit, gente in cur.fetchall()}
    except Exception:
        # El esquema de enriquecimiento puede no existir todavía: el tablero
        # tiene que poder generarse igual, sólo que sin la sección de contactos.
        cx.rollback()
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--salida", default="dist")
    args = ap.parse_args()

    cx = psycopg.connect(args.dsn)
    with cx.cursor() as cur:
        cur.execute(CONSULTA)
        crudas = cur.fetchall()
    cx.close()

    def num(x, div=1):
        return None if x is None else round(float(x) / div)

    filas = []
    for r in crudas:
        f = list(r)
        for i in (16, 17, 18, 19):          # capacidades
            f[i] = num(f[i])
        f[26] = None if f[26] is None else round(float(f[26]), 1)   # crecimiento %
        f[29] = num(f[29], 1e6)             # ingresos a millones
        f[30] = num(f[30], 1e6)             # activos a millones
        i = COLS.index("score")
        for j in (i, i + 2, i + 3, i + 5):   # score, ltv, cac, deterioro
            f[j] = None if f[j] is None else round(float(f[j]), 3)
        filas.append(f)

    pesos = json.loads((raiz_cfg := Path(__file__).resolve().parent.parent)
                       .joinpath("config/pesos_icp.json").read_text(encoding="utf-8"))
    cx2 = psycopg.connect(args.dsn)
    contactos = leer_contactos(cx2)
    cx2.close()

    # Los cargos que la pantalla de captura sugiere, con su tier. Salen de la
    # misma tabla `CARGOS` que usa el motor para clasificar, y no de una lista
    # escrita a mano en el HTML: con dos listas, el día que se añada un cargo
    # al motor la pantalla seguiría sugiriendo los viejos, y un cargo sugerido
    # que el clasificador no reconoce entra a la base sin tier.
    #
    # Se colapsan por categoría conservando el tier más alto (el número más
    # bajo): `gerente_general` aparece una vez, no una por cada patrón.
    # Las etiquetas SÍ van escritas acá, y sólo ellas. La categoría es una
    # clave de máquina —`sst`, `hseq`— y capitalizarla da «Sst» y «Hseq», que
    # no es cómo nadie llama a ese cargo. Lo que no se duplica es la LISTA:
    # un cargo que el motor conozca y no esté acá sale igual, con su nombre
    # de máquina, en vez de desaparecer de la pantalla sin que nadie lo note.
    ETIQUETAS = {
        "representante_legal": "Representante legal",
        "gerente_general": "Gerente general",
        "director_general": "Director general",
        "presidente": "Presidente",
        "propietario": "Propietario o socio",
        "gerente_financiero": "Gerente administrativo o financiero",
        "ordenador_del_gasto": "Ordenador del gasto",
        "subgerente": "Subgerente o subdirector",
        "sst": "Responsable de SST",
        "hseq": "Jefe de HSEQ",
        "calidad": "Jefe de calidad",
        "supervisor_contrato": "Supervisor de contrato",
        "director_medico": "Director médico o científico",
        "juridica": "Jurídica",
        "talento_humano": "Talento humano",
    }
    sugeridos: dict[str, dict] = {}
    for tier, categoria, _ in CARGOS:
        if categoria not in sugeridos or tier < sugeridos[categoria]["tier"]:
            sugeridos[categoria] = {
                "cat": categoria, "tier": tier,
                "etiqueta": ETIQUETAS.get(categoria,
                                          categoria.replace("_", " ").capitalize()),
            }
    cargos = sorted(sugeridos.values(), key=lambda c: (c["tier"], c["etiqueta"]))

    payload = {"generado": date.today().isoformat(), "cols": COLS, "rows": filas,
               "pesos_icp": pesos, "contactos": contactos, "cargos": cargos}
    crudo = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    raiz = raiz_cfg
    (raiz / "tablero" / "datos.js").write_text("window.DATOS=" + crudo + ";", encoding="utf-8")

    plantilla = (raiz / "tablero" / "index.html").read_text(encoding="utf-8")
    inline = plantilla.replace(
        '<script src="datos.js"></script>',
        "<script>window.DATOS=" + crudo + ";</script>",
    )
    # El archivo suelto se sirve con charset; el de doble clic se abre con
    # file:// y sin cabeceras, así que necesita declarar el suyo.
    unico = ('<!doctype html><html lang="es"><head><meta charset="utf-8">'
             '<meta name="viewport" content="width=device-width,initial-scale=1">'
             "</head><body>\n" + inline + "\n</body></html>")

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    destino = salida / "tablero.html"
    destino.write_text(unico, encoding="utf-8")

    print(f"· {len(filas):,} prestadores · {len(contactos):,} con contactos")
    print(f"· tablero/datos.js      {(raiz / 'tablero' / 'datos.js').stat().st_size/1e6:.2f} MB")
    print(f"· {destino}  {destino.stat().st_size/1e6:.2f} MB  (archivo único, doble clic)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
