#!/usr/bin/env bash
# Corrida completa: extrae, carga y reporta.
set -euo pipefail
DSN="${DSN:-postgresql://reps:reps@localhost:55432/reps}"
PY="${PY:-.venv/bin/python}"

echo "==> extracción"
$PY src/extract_reps.py        --salida data/raw
$PY src/extract_datos_gov.py   --salida data/raw
$PY src/extract_supersalud.py  --salida data/raw

echo "==> carga"
$PY src/load.py            --dsn "$DSN" --raw data/raw --schema src/schema.sql
$PY src/load_supersalud.py --dsn "$DSN" --raw data/raw

echo "==> score de ICP"
$PY src/score_icp.py --dsn "$DSN"

echo "==> reporte"
$PY src/report.py --dsn "$DSN" --raw data/raw --salida docs/reporte.md
