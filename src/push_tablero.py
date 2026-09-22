"""
Sube a Supabase el payload que la herramienta necesita para arrancar.

Uso:  python src/push_tablero.py --dsn "$DSN" --supabase "$SUPABASE_DSN"

Escribe en `portal.tablero` el MISMO payload que `build_tablero.py` embebe en
dist/tablero.html, llamando a su `construir_payload`. El sitio lo lee de ahí
con la sesión del usuario.

─────────────────────────────────────────────────────────────────────────────
ANTES ESTE ARCHIVO TENÍA SU PROPIA COPIA, Y DIVERGIÓ

El docstring prometía que no podían divergir «porque importa la consulta de
build_tablero». Importaba la consulta y duplicaba el ensamblado del payload,
que es donde ocurrió: al añadir `cargos` para la pantalla de captura, el
archivo local los llevaba y lo que subía a Supabase no. El portal servía un
payload sin cargos —la sección de captura salía vacía— y los dos comandos
seguían diciendo que todo había ido bien.

Compartir un trozo no evita divergir. Ahora se llama a la misma función.
─────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import argparse
import json

import psycopg

from build_tablero import construir_payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True, help="Postgres local")
    ap.add_argument("--supabase", required=True, help="DSN de Supabase (5432)")
    args = ap.parse_args()

    payload = construir_payload(args.dsn)
    crudo = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    print(f"· payload: {len(payload['rows']):,} filas, {len(crudo)/1e6:.2f} MB")

    cx = psycopg.connect(args.supabase)
    with cx.cursor() as cur:
        # Un upsert sobre la única fila: el sitio nunca ve la tabla vacía a
        # mitad de una actualización, que es lo que pasaría con delete+insert.
        cur.execute("""
            INSERT INTO portal.tablero (id, payload, filas, generado, actualizado)
            VALUES (1, %s::jsonb, %s, %s, now())
            ON CONFLICT (id) DO UPDATE
              SET payload = excluded.payload,
                  filas = excluded.filas,
                  generado = excluded.generado,
                  actualizado = now()
        """, (crudo, len(payload["rows"]), payload["generado"]))
    cx.commit()

    # Verificar contra lo que quedó guardado, no contra lo que se envió.
    #
    # Se cuentan también `cargos` y `contactos`, y no sólo las filas: el
    # payload que servía el portal tenía las 10.652 filas correctas y ningún
    # cargo, así que una comprobación de filas y columnas lo daba por bueno
    # mientras la pantalla de captura salía vacía. Lo que rompe en silencio es
    # justo lo que hay que contar.
    with cx.cursor() as cur:
        cur.execute("""
            SELECT filas,
                   jsonb_array_length(payload->'rows'),
                   jsonb_array_length(payload->'cols'),
                   coalesce(jsonb_array_length(payload->'cargos'), 0),
                   (SELECT count(*) FROM jsonb_object_keys(payload->'contactos')),
                   pg_size_pretty(pg_column_size(payload)::bigint)
            FROM portal.tablero WHERE id = 1
        """)
        filas, n_rows, n_cols, n_cargos, n_contactos, peso = cur.fetchone()
    cx.close()

    esperado = (len(payload["rows"]), len(payload["cols"]),
                len(payload["cargos"]), len(payload["contactos"]))
    obtenido = (n_rows, n_cols, n_cargos, n_contactos)
    ok = filas == n_rows and esperado == obtenido

    print(f"· en Supabase: {n_rows:,} filas · {n_cols} columnas · "
          f"{n_contactos:,} empresas con contactos · {n_cargos} cargos · {peso}")
    if ok:
        print("✓ cuadra con lo enviado")
    else:
        print("✗ NO cuadra con lo enviado")
        print(f"   enviado:  filas={esperado[0]} cols={esperado[1]} "
              f"cargos={esperado[2]} contactos={esperado[3]}")
        print(f"   guardado: filas={obtenido[0]} cols={obtenido[1]} "
              f"cargos={obtenido[2]} contactos={obtenido[3]}")
    if ok and not n_cargos:
        # No es un fallo del push: es que build_tablero no los armó. Sin este
        # aviso, el portal volvería a servir una pantalla de captura vacía.
        print("⚠ el payload no lleva cargos: la pantalla de captura saldrá vacía")
        print("  ¿corriste build_tablero con PYTHONPATH=src?")
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
