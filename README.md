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
| `estimacion_personal` | 10.646 | trabajadores estimados, con método y banda |
| `score_icp` | 10.646 | score LTV:CAC con el desglose de qué lo empujó |

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
src/estimar_personal.py   estimación de trabajadores (nómina + capacidad)
src/score_icp.py          score de ICP por cociente LTV:CAC
src/recalibrar.py         compara el score contra cierres reales
config/pesos_icp.json     pesos y umbrales del score · lo único editable a mano
src/report.py             reporte de volúmenes, cruce, calidad y limitaciones
tablero/index.html        tablero de filtrado y exportación a CSV
docs/00-fuentes.md        qué se verificó de cada fuente y qué no existe
docs/02-score-icp.md      cómo ajustar pesos y cómo registrar un cierre
docs/reporte.md           salida del último run
```

## Lo que hay que saber antes de tocar esto

- **La fuente primaria es la consulta web del REPS, no datos.gov.co.** El dataset
  abierto `c36g-9fc2` tiene corte de marzo de 2026 y no expone servicios,
  capacidad instalada, representante legal ni fechas de habilitación.
- **`numero_empleados` está vacío a propósito.** Ninguna fuente pública lo
  publica por institución. El dato observado no se mezcla con el derivado: la
  estimación vive en la tabla aparte `estimacion_personal`.
- **Supersalud llega hasta 2021.** Es lo último que publicó la entidad.
- **Ninguna fila del REPS se descarta por no tener financiero.**

Detalle completo de fuentes, trampas del portal y limitaciones en
[`docs/00-fuentes.md`](docs/00-fuentes.md) y [`docs/reporte.md`](docs/reporte.md).


## Estimación de trabajadores

`prestador.numero_empleados` sigue NULL porque ninguna fuente lo publica. Aparte,
`reps.estimacion_personal` guarda un número **derivado** con su método y su banda.

**Método nómina** (4.061 prestadores, confianza media). El gasto de nómina de los
estados financieros 2021 dividido por el costo anual por trabajador. Ese costo se
calibra, no se inventa: SIHO publica la planta nacional de las ESE para 2021
(48.671 personas) y los mismos estados financieros dan su nómina agregada
($1,047 billones). El cociente es **$21.520.000 por persona/año**, ~1,97 SMMLV de
2021 contando carga prestacional — coherente con una planta pública.

**Método capacidad** (6.585 prestadores, confianza baja). Regresión no negativa
sobre camas, salas, sedes y ambulancias, ajustada contra las estimaciones de
nómina. Sin intercepto y solo sobre IPS: con intercepto le asignaba ~7
trabajadores de base a cualquier consultorio. R² de 0,42 en holdout.

**A los 46.987 profesionales independientes no se les estima.** Son personas
naturales; la ausencia de fila significa "no estimable", no cero.

### Lo que el número mide y lo que no

Mide **planta formal en nómina**. Contra 47 IPS de la Costa con conteo conocido,
la fuerza laboral real resultó ×3 la planta en mediana, con cuartiles en ×1,25 y
×5,24 y extremos de ×0,04 a ×47 — por eso `fuerza_laboral_bajo/alto` es banda.

La brecha es tercerización, y no es ruido: en salud una parte grande del personal
entra por prestación de servicios o por bolsas de empleo. **Para ARL las dos
cifras son dos clientes distintos**: la planta en nómina la afilia la IPS; a los
tercerizados los afilia la bolsa que los contrata.

## Tablero

**En vivo: https://juannavia-byte.github.io/reps-ips-colombia/**

Se republica con `./deploy_pages.sh` (regenera los datos y empuja la rama
`gh-pages`). También queda `dist/tablero.html`, un archivo único de ~3 MB que
funciona con doble clic, sin servidor ni internet.


`tablero/index.html` + `tablero/datos.js` (generado, no versionado). Filtra por
geografía —por presencia real, no por sede principal—, capacidad, tamaño estimado
y señales como "habilitó servicio nuevo en 12 meses", y exporta a CSV con
separador `;` y BOM para que Excel en español respete los acentos.

Se regenera con `python src/build_tablero.py --dsn "$DSN"`.

## Llevar la base a Supabase

    SUPABASE_DSN='postgresql://postgres:CLAVE@db.XXXX.supabase.co:5432/postgres' ./push_supabase.sh

Usa la conexión **directa** (puerto 5432), no el pooler de transacciones (6543):
`pg_restore` necesita sesión y el pooler la corta.


## Score de ICP

Cociente **LTV : CAC** por prestador, donde el CAC es esfuerzo y tiempo —
interlocutores, ciclo, investigación previa, distancia a quien decide — y el LTV es
el valor de la cuenta completa a 24 meses, no solo la ARL de entrada.

El score es el **percentil** del cociente: 93 significa "está en el 7 % mejor".
Reparto actual: 10 % alta · 25 % media · 65 % baja.

Los pesos viven en `config/pesos_icp.json` y **no hay ninguno escrito en el código**.
En el tablero hay un panel de sliders para probarlos en vivo; para dejarlos fijos se
edita el JSON y se regenera.

> Los pesos iniciales **no están calibrados con datos**: salieron de la lógica del
> método comercial porque todavía no hay historial de cierres. Se corrigen
> registrando cierres en `datos/cierres.csv` y corriendo `src/recalibrar.py`, que
> señala qué peso parece mal puesto sin ajustarlo solo.

Detalle completo en [`docs/02-score-icp.md`](docs/02-score-icp.md).
