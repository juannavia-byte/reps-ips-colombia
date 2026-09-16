#!/usr/bin/env bash
# Empuja el esquema `reps` completo de Postgres local a Supabase.
#
#   SUPABASE_DSN='postgresql://postgres:CLAVE@db.XXXX.supabase.co:5432/postgres' ./push_supabase.sh
#
# IMPORTANTE: usa la conexión DIRECTA (puerto 5432), no el pooler de
# transacciones (6543). pg_restore necesita sesión, y el pooler la corta.
# La cadena está en Supabase > Project Settings > Database > Connection string.
set -euo pipefail
: "${SUPABASE_DSN:?Falta SUPABASE_DSN. Ver el encabezado de este archivo.}"

echo "· volcando el esquema reps desde el contenedor local…"
docker exec reps-pg pg_dump -U reps -d reps -n reps \
  --no-owner --no-privileges -Fc -f /tmp/reps.dump

echo "· restaurando en Supabase…"
docker exec -i reps-pg pg_restore \
  --no-owner --no-privileges --clean --if-exists --exit-on-error \
  -d "$SUPABASE_DSN" /tmp/reps.dump

echo "· verificando…"
docker exec -i reps-pg psql "$SUPABASE_DSN" -c \
  "SELECT relname, n_live_tup FROM pg_stat_user_tables
   WHERE schemaname='reps' ORDER BY n_live_tup DESC;"
