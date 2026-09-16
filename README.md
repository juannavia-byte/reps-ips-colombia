# Universo de prestadores de servicios de salud de Colombia

Pipeline versionado que construye desde cero una base de datos relacional con
**todos los prestadores de servicios de salud del país**, a partir de fuentes
públicas oficiales: el REPS del Ministerio de Salud y los estados financieros
de la Superintendencia Nacional de Salud.

No aplica filtros de segmento, scoring ni priorización. Es el universo crudo,
modelado y trazable.

## Qué queda cargado

| Tabla | Filas | Grano |
|---|---|---|
| `prestador` | 57.663 | una entidad, por (tipo de documento, número) |
| `registro_habilitacion` | 61.177 | un código de habilitación |
| `sede` | 77.009 | una sede |
| `sede_servicio` | 228.039 | un servicio habilitado por sede |
| `sede_capacidad` | 97.549 | un concepto de capacidad por sede |
| `prestador_financiero` | 6.634 | un NIT por vigencia y origen |

De las 57.663 entidades, **8.972 son IPS**; el resto son profesionales
independientes, objeto social diferente y transporte especial de pacientes.

## Uso

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Postgres local
docker run -d --name reps-pg -e POSTGRES_PASSWORD=reps -e POSTGRES_USER=reps \
  -e POSTGRES_DB=reps -p 55432:5432 -v reps_pgdata:/var/lib/postgresql/data postgres:16

./run_all.sh          # extrae, carga y genera docs/reporte.md
```

O por pasos:

```bash
.venv/bin/python src/extract_reps.py        --salida data/raw
.venv/bin/python src/extract_datos_gov.py   --salida data/raw
.venv/bin/python src/extract_supersalud.py  --salida data/raw
.venv/bin/python src/load.py            --dsn "$DSN" --raw data/raw --schema src/schema.sql
.venv/bin/python src/load_supersalud.py --dsn "$DSN" --raw data/raw
.venv/bin/python src/report.py          --dsn "$DSN" --raw data/raw --salida docs/reporte.md
```

Toda la extracción del REPS tarda unos 2,5 minutos: **los cuatro exports
nacionales salen en una petición cada uno, sin paginar**.

## Archivos

```
src/reps_client.py        cliente ASP.NET del REPS (login invitado, exports)
src/extract_reps.py       los 4 exports nacionales + manifiesto
src/extract_datos_gov.py  dataset abierto: tipo de documento + medición de desfase
src/extract_supersalud.py estados financieros 2021
src/schema.sql            DDL comentado con las decisiones de modelado
src/load.py               parseo, deduplicación, QA y carga de REPS
src/load_supersalud.py    carga financiera y cruce por NIT
src/report.py             reporte de volúmenes, cruce, calidad y limitaciones
docs/00-fuentes.md        qué se verificó de cada fuente y qué no existe
docs/reporte.md           salida del último run
```

## Lo que hay que saber antes de tocar esto

- **La fuente primaria es la consulta web del REPS, no datos.gov.co.** El dataset
  abierto `c36g-9fc2` tiene corte de marzo de 2026 y no expone servicios,
  capacidad instalada, representante legal ni fechas de habilitación.
- **`numero_empleados` está vacío a propósito.** Ninguna fuente pública lo
  publica por institución. El proxy de tamaño es `sede_capacidad.cantidad`.
- **Supersalud llega hasta 2021.** Es lo último que publicó la entidad.
- **Ninguna fila del REPS se descarta por no tener financiero.**

Detalle completo de fuentes, trampas del portal y limitaciones en
[`docs/00-fuentes.md`](docs/00-fuentes.md) y [`docs/reporte.md`](docs/reporte.md).
