"""
Reporte final: volúmenes, cruce financiero, proxy de tamaño, calidad y
desfase entre el dataset abierto y la consulta web en vivo.

Uso:  python src/report.py --dsn ... --raw data/raw [--salida docs/reporte.md]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg


def q1(cx, sql, args=()):
    with cx.cursor() as cur:
        cur.execute(sql, args)
        r = cur.fetchone()
        return r[0] if r else None


def qa(cx, sql, args=()):
    with cx.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def tabla(filas, cabeceras) -> str:
    out = ["| " + " | ".join(cabeceras) + " |",
           "|" + "|".join("---" for _ in cabeceras) + "|"]
    for f in filas:
        out.append("| " + " | ".join(
            f"{v:,}" if isinstance(v, int) else ("" if v is None else str(v)) for v in f) + " |")
    return "\n".join(out)


def desfase(cx, raw: Path) -> str:
    """Compara el dataset abierto de datos.gov.co contra lo cargado del portal vivo."""
    cand = sorted(raw.glob("datosgov_c36g-9fc2_*.json"))
    if not cand:
        return "_No se descargó el dataset abierto; no hay comparación._"
    man = json.loads(sorted(raw.glob("manifiesto_datosgov_*.json"))[-1].read_text())
    abiertos = json.loads(cand[-1].read_text())
    cod_abierto = {f["codigoprestador"] for f in abiertos if f.get("codigoprestador")}
    cod_vivo = {r[0] for r in qa(cx, "SELECT codigo_habilitacion FROM reps.registro_habilitacion")}

    solo_vivo = cod_vivo - cod_abierto
    solo_abierto = cod_abierto - cod_vivo
    corte_vivo = q1(cx, "SELECT fecha_corte_declarada FROM reps.extraccion "
                        "WHERE fuente='reps' ORDER BY id LIMIT 1")
    return f"""
| | Dataset abierto `c36g-9fc2` | Consulta web en vivo |
|---|---|---|
| Corte declarado por la fuente | `{man['fecha_corte_declarada']}` | `{corte_vivo}` |
| Última actualización del portal | {man['rows_updated_at'][:10]} | — |
| Filas de sedes | {man['filas']:,} | {q1(cx, 'SELECT count(*) FROM reps.sede'):,} |
| Códigos de prestador distintos | {len(cod_abierto):,} | {len(cod_vivo):,} |
| Servicios habilitados | **no expone el campo** | {q1(cx, 'SELECT count(*) FROM reps.sede_servicio'):,} |
| Capacidad instalada | **no expone el campo** | {q1(cx, 'SELECT count(*) FROM reps.sede_capacidad'):,} |
| Representante legal | **no expone el campo** | {q1(cx, "SELECT count(*) FROM reps.registro_habilitacion WHERE representante_legal IS NOT NULL"):,} |

**Diferencia de cobertura entre las dos fuentes:**

- {len(solo_vivo):,} códigos de prestador están en la consulta web y **no** en el dataset abierto (altas posteriores al 12-mar-2026).
- {len(solo_abierto):,} códigos están en el dataset abierto y **no** en la consulta web (bajas, cierres o pérdida de habilitación desde entonces).
- Movimiento neto del registro en ~6 meses: {len(solo_vivo) - len(solo_abierto):+,} prestadores.

Se cargó la consulta web. El dataset abierto se usó solo para recuperar
`tipo_identificacion`, que el export web trae vacío.
""".strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--salida", default="docs/reporte.md")
    args = ap.parse_args()
    raw = Path(args.raw)
    cx = psycopg.connect(args.dsn)

    total_prest = q1(cx, "SELECT count(*) FROM reps.prestador")
    total_ips = q1(cx, "SELECT count(*) FROM reps.prestador WHERE es_ips")
    fin_total = q1(cx, "SELECT count(*) FROM reps.prestador_financiero")
    fin_cruz = q1(cx, "SELECT count(*) FROM reps.prestador_financiero WHERE prestador_id IS NOT NULL")
    prest_con_fin = q1(cx, "SELECT count(DISTINCT prestador_id) FROM reps.prestador_financiero "
                           "WHERE prestador_id IS NOT NULL")

    secciones = []
    secciones.append(f"""# Reporte de construcción · universo de prestadores de salud de Colombia

Generado: {datetime.now(timezone.utc).isoformat(timespec='seconds')}
Base: PostgreSQL 16, esquema `reps`.

## 1. Volúmenes cargados

{tabla(qa(cx, '''
  SELECT 'prestadores (entidades únicas)', count(*) FROM reps.prestador
  UNION ALL SELECT 'códigos de habilitación', count(*) FROM reps.registro_habilitacion
  UNION ALL SELECT 'sedes', count(*) FROM reps.sede
  UNION ALL SELECT 'servicios habilitados', count(*) FROM reps.sede_servicio
  UNION ALL SELECT 'registros de capacidad', count(*) FROM reps.sede_capacidad
  UNION ALL SELECT 'filas financieras', count(*) FROM reps.prestador_financiero
'''), ["Tabla", "Filas"])}

### Por clase de prestador

{tabla(qa(cx, '''
  SELECT clase_prestador, count(*) AS entidades,
         (SELECT count(*) FROM reps.registro_habilitacion r WHERE r.prestador_id IN
            (SELECT id FROM reps.prestador p2 WHERE p2.clase_prestador = p.clase_prestador)) AS codigos
  FROM reps.prestador p GROUP BY clase_prestador ORDER BY 2 DESC
'''), ["Clase", "Entidades", "Códigos"])}

### Por naturaleza jurídica

{tabla(qa(cx, '''SELECT coalesce(naturaleza_juridica,'(sin dato)'), count(*)
                 FROM reps.prestador GROUP BY 1 ORDER BY 2 DESC'''), ["Naturaleza", "Entidades"])}
""")

    secciones.append(f"""## 2. Cruce con Supersalud

Vigencia cargada: **2021** — la última que publica la Superintendencia.

{tabla(qa(cx, '''
  SELECT origen, count(*) AS filas,
         count(prestador_id) AS cruzadas,
         count(*) - count(prestador_id) AS sin_match,
         round(100.0*count(prestador_id)/count(*),1) AS pct
  FROM reps.prestador_financiero GROUP BY origen ORDER BY origen
'''), ["Origen", "Filas", "Cruzadas", "Sin match", "% cruce"])}

- Filas financieras totales: **{fin_total:,}**, de las cuales **{fin_cruz:,}** cruzaron contra el REPS (**{100*fin_cruz/fin_total:.1f} %**).
- Prestadores del REPS con estado financiero: **{prest_con_fin:,}** de {total_prest:,} ({100*prest_con_fin/total_prest:.2f} %).
- Prestadores **sin** estado financiero: **{total_prest - prest_con_fin:,}**. Quedan con los campos financieros en NULL; **ninguno se descartó**.
- Las {fin_total - fin_cruz:,} filas sin contraparte se conservan con `prestador_id` NULL y quedan registradas en `qa_inconsistencia` con tipo `sin_match_reps`.

**Por qué no cruza el 100 %:** el archivo de Supersalud es de 2021 y el REPS es de hoy.
Una IPS que cerró, se fusionó o perdió la habilitación entre 2021 y 2026 aparece en el
financiero y no en el registro vigente. La llave de cruce es el NIT, como estaba previsto.
""")

    camas = q1(cx, "SELECT coalesce(sum(cantidad),0) FROM reps.sede_capacidad WHERE grupo_capacidad='CAMAS'")
    secciones.append(f"""## 3. Proxy de tamaño

**`numero_empleados` está vacío en las {total_prest:,} filas y así queda.** Ninguna fuente
pública colombiana lo expone por institución: no está en REPS, ni en los archivos de
Supersalud, ni en RUES, ni PILA se publica a nivel de entidad. No se estimó.

Lo que sí hay, por sede y con cantidad:

{tabla(qa(cx, '''
  SELECT grupo_capacidad, count(*) AS registros, sum(cantidad) AS unidades,
         count(DISTINCT sede_id) AS sedes
  FROM reps.sede_capacidad GROUP BY 1 ORDER BY 3 DESC NULLS LAST
'''), ["Grupo", "Registros", "Unidades", "Sedes"])}

Camas totales del país en el registro: **{camas:,}**.

### Conceptos con más unidades

{tabla(qa(cx, '''
  SELECT grupo_capacidad, concepto_nombre, sum(cantidad) AS unidades
  FROM reps.sede_capacidad GROUP BY 1,2 ORDER BY 3 DESC NULLS LAST LIMIT 15
'''), ["Grupo", "Concepto", "Unidades"])}

### Cobertura del proxy

{tabla(qa(cx, '''
  SELECT CASE WHEN p.es_ips THEN 'IPS' ELSE 'resto' END AS grupo,
         count(*) AS entidades,
         count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM reps.sede_capacidad c JOIN reps.sede s ON s.id=c.sede_id
            JOIN reps.registro_habilitacion r ON r.id=s.registro_id WHERE r.prestador_id=p.id
         )) AS con_capacidad
  FROM reps.prestador p GROUP BY 1 ORDER BY 2 DESC
'''), ["Grupo", "Entidades", "Con capacidad declarada"])}
""")

    dedup = q1(cx, "SELECT count(*) FROM reps.qa_dedup")
    secciones.append(f"""## 4. Deduplicación

La llave de identidad es **(tipo_identificacion, numero_identificacion)**, no el NIT solo:
con el universo completo entran decenas de miles de profesionales independientes
identificados con cédula, y un número de cédula puede coincidir con un NIT.

- Entidades únicas: **{total_prest:,}**
- Códigos de habilitación: **{q1(cx, 'SELECT count(*) FROM reps.registro_habilitacion'):,}**
- Entidades con más de un código fusionadas: **{dedup:,}** (registradas en `qa_dedup` con los códigos, las razones sociales vistas y el criterio)

Criterio de razón social canónica: la del registro con `fecha_radicacion` más reciente;
a igualdad de fecha, la cadena más larga.

### Las diez entidades con más códigos de habilitación

{tabla(qa(cx, '''
  SELECT p.numero_identificacion, left(p.razon_social,44), count(r.id) AS codigos,
         (SELECT count(*) FROM reps.sede s JOIN reps.registro_habilitacion r2 ON r2.id=s.registro_id
          WHERE r2.prestador_id=p.id) AS sedes
  FROM reps.prestador p JOIN reps.registro_habilitacion r ON r.prestador_id=p.id
  GROUP BY p.id, p.numero_identificacion, p.razon_social
  ORDER BY 3 DESC LIMIT 10
'''), ["NIT", "Razón social", "Códigos", "Sedes"])}
""")

    secciones.append(f"""## 5. Calidad e inconsistencias

{tabla(qa(cx, '''SELECT tipo, count(*) FROM reps.qa_inconsistencia
                 GROUP BY 1 ORDER BY 2 DESC'''), ["Tipo", "Casos"])}

Lectura de cada tipo:

- `sin_match_reps` — NIT que reportó a Supersalud en 2021 y no está en el REPS vigente.
- `dv_invalido` — NIT cuyo dígito de verificación no valida con el algoritmo DIAN. Se carga igual; se marca.
- `nit_mal_formado` — identificación con caracteres no numéricos.
- `fila_malformada` — fila cuyo número de campos no coincide con la cabecera, por separador embebido en texto libre. El portal no escapa los delimitadores.
- `servicio_sin_sede` / `capacidad_sin_sede` / `sede_sin_registro` — referencias a sedes ausentes del export correspondiente. Los cuatro exports se tomaron con minutos de diferencia y el registro se mueve; son deriva de snapshot, no error de modelo.
- `sede_duplicada` — par (código de sede, número de sede) repetido.
- `tipo_documento_derivado` — no es una incidencia cargada sino un conteo del resumen: {json.loads((raw.parent/'resumen_carga.json').read_text())['prestadores']['tipo_documento_derivado']:,} registros cuyo tipo de documento no estaba en el dataset abierto y se dedujo de `clase_persona`.

## 6. Desfase entre el dataset abierto y la consulta web

{desfase(cx, raw)}

## 7. Trazabilidad

Cada tabla lleva `extraccion_id`. La tabla `extraccion` registra por archivo: URL,
fecha de descarga, corte declarado por la fuente, sha256, bytes, filas y separador.

{tabla(qa(cx, '''SELECT fuente, recurso, fecha_corte_declarada, tamano_bytes, filas_crudas,
                        left(sha256,12)
                 FROM reps.extraccion ORDER BY id'''),
       ["Fuente", "Recurso", "Corte declarado", "Bytes", "Filas", "sha256"])}

## 8. Limitaciones

1. **Supersalud va cinco años atrasado.** La última vigencia publicada es 2021. No es una limitación del pipeline: la entidad no ha publicado cortes posteriores pese a que la obligación de reporte por Circular Única sigue vigente.
2. **No hay número de empleados en ninguna fuente pública.** El campo queda NULL. El sustituto es la capacidad instalada.
3. **El `Registro Actual` del REPS solo trae prestadores habilitados.** Los cerrados o con habilitación vencida no aparecen; por eso una parte del financiero de 2021 no cruza.
4. **El dataset abierto de datos.gov.co no sirve como fuente primaria**: corte de marzo de 2026 y sin servicios, capacidad, representante legal ni fechas de habilitación.
5. **El portal no escapa los delimitadores** en campos de texto libre. Las filas afectadas están contadas y registradas una por una.
6. **`tido_codigo` viene vacío** en el export web; el tipo de documento se recuperó del dataset abierto y, donde no cruzó, se derivó de `clase_persona`.
7. **SIHO (Decreto 2193)** tiene planta de personal de las ESE, pero su módulo público de invitado no expone esa consulta. Quedó sin verificar como vía de descarga.
""")

    texto = "\n\n".join(secciones)
    destino = Path(args.salida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto)
    print(texto)
    print(f"\n-> {destino}")
    cx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
