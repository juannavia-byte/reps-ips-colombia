#!/usr/bin/env bash
# Empuja el esquema `reps` de Postgres local a Supabase.
#
#   SUPABASE_DSN='postgresql://postgres:CLAVE@db.XXXX.supabase.co:5432/postgres' ./push_supabase.sh
#
# Dos cosas que hacen fallar esto y no son obvias:
#
# 1. La conexión debe ser la DIRECTA (puerto 5432), no el pooler de
#    transacciones (6543). pg_restore necesita una sesión y el pooler la corta
#    a mitad del restore, dejando el esquema a medias.
#    Supabase > Project Settings > Database > Connection string > URI.
#
# 2. El esquema pesa ~638 MB, por encima del límite de 500 MB del plan free.
#    De esos, 339 MB son las llaves "NO" repetidas en dos columnas jsonb de
#    sede_servicio. COMPACTAR=1 las omite al enviar (queda en ~311 MB) sin
#    perder información: la ausencia de una llave equivale a "NO", y los únicos
#    valores que existen en la fuente son "SI" y "NO" (verificado).
#    Si el proyecto es de pago (8 GB), no hace falta: envía tal cual.
#
#   COMPACTAR=1 SUPABASE_DSN='...' ./push_supabase.sh
set -euo pipefail
: "${SUPABASE_DSN:?Falta SUPABASE_DSN. Ver el encabezado de este archivo.}"
COMPACTAR="${COMPACTAR:-0}"
CONTENEDOR=reps-pg
REG=/tmp/push_supabase.log

case "$SUPABASE_DSN" in
  *:6543/*) echo "✗ Esa es la cadena del pooler (6543). pg_restore necesita la"
            echo "  conexión directa (5432) o el restore se corta a la mitad."; exit 1;;
esac

q() { docker exec "$CONTENEDOR" psql -U reps -d reps -At -c "$1"; }
r() { docker exec -i "$CONTENEDOR" psql -v ON_ERROR_STOP=1 "$SUPABASE_DSN" -At -c "$1"; }

echo "· comprobando que el destino responde…"
destino_ver=$(r "SELECT current_setting('server_version');")
echo "  Supabase responde, Postgres $destino_ver"

echo "· volcando el esquema desde el contenedor local…"
if [ "$COMPACTAR" = "1" ]; then
  # sede_servicio va aparte, con las dos jsonb compactadas; el resto tal cual.
  docker exec "$CONTENEDOR" pg_dump -U reps -d reps -n reps \
    --no-owner --no-privileges -Fc --exclude-table-data=reps.sede_servicio \
    -f /tmp/reps.dump
else
  docker exec "$CONTENEDOR" pg_dump -U reps -d reps -n reps \
    --no-owner --no-privileges -Fc -f /tmp/reps.dump
fi
echo "  $(docker exec "$CONTENEDOR" sh -c 'ls -lh /tmp/reps.dump' | awk '{print $5}')"

echo "· restaurando (los errores quedan en $REG)…"
docker exec -i "$CONTENEDOR" pg_restore \
  --no-owner --no-privileges --clean --if-exists \
  -d "$SUPABASE_DSN" /tmp/reps.dump 2>&1 | tee "$REG" || true
fallos=$(grep -c "^pg_restore: error" "$REG" || true)
[ "$fallos" -gt 0 ] && echo "  ⚠ $fallos errores durante el restore, revisa $REG"

if [ "$COMPACTAR" = "1" ]; then
  echo "· enviando sede_servicio compactada…"
  docker exec "$CONTENEDOR" psql -U reps -d reps -c "\copy (
     SELECT id, sede_id, grupo_codigo, grupo_nombre, servicio_codigo, servicio_nombre,
            complejidad_baja, complejidad_media, complejidad_alta, ambulatorio,
            hospitalario, unidad_movil, domiciliario, centro_referencia,
            institucion_remisora,
            (SELECT jsonb_object_agg(k,v) FROM jsonb_each_text(modalidades) t(k,v)     WHERE v<>'NO'),
            (SELECT jsonb_object_agg(k,v) FROM jsonb_each_text(especificidades) t(k,v) WHERE v<>'NO'),
            numero_distintivo, fecha_apertura, fecha_cierre, version_norma, extraccion_id
     FROM reps.sede_servicio) TO '/tmp/ss.csv' CSV"
  docker exec -i "$CONTENEDOR" psql -v ON_ERROR_STOP=1 "$SUPABASE_DSN" \
    -c "\copy reps.sede_servicio FROM '/tmp/ss.csv' CSV"
  echo "  ⚠ En Supabase, una llave AUSENTE en modalidades/especificidades significa \"NO\"."
  echo "    Las consultas del tipo ->>'x' = 'SI' siguen dando el mismo resultado."
fi

echo "· verificando fila por fila contra lo local…"
# n_live_tup NO sirve aquí: es una estimación del recolector de estadísticas y
# sale en cero recién restaurado. Hay que contar de verdad en ambos lados.
mal=0
for t in $(q "SELECT table_name FROM information_schema.tables
              WHERE table_schema='reps' AND table_type='BASE TABLE' ORDER BY 1;"); do
  local_n=$(q  "SELECT count(*) FROM reps.\"$t\";")
  remoto_n=$(r "SELECT count(*) FROM reps.\"$t\";" 2>/dev/null || echo "FALTA")
  if [ "$local_n" = "$remoto_n" ]; then estado="ok"
  else estado="✗ DESCUADRA"; mal=$((mal+1)); fi
  printf "  %-28s local %10s   remoto %10s   %s\n" "$t" "$local_n" "$remoto_n" "$estado"
done

vistas=$(r "SELECT count(*) FROM information_schema.views WHERE table_schema='reps';")
echo "  vistas restauradas: $vistas"
echo "  tamaño en Supabase: $(r "SELECT pg_size_pretty(sum(pg_total_relation_size(c.oid)))
   FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='reps' AND c.relkind IN ('r','m');")"

if [ "$mal" -gt 0 ]; then
  echo "✗ $mal tabla(s) no cuadran. La carga NO está completa."; exit 1
fi
echo "✓ Todas las tablas cuadran."
echo
echo "Nota sobre acceso: las tablas quedan en el esquema 'reps', que PostgREST no"
echo "expone por defecto. Si luego lo expones en Settings > API, activa RLS antes,"
echo "o quedarán legibles con la clave anónima."
