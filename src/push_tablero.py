"""
Sube a Supabase el payload que la herramienta necesita para arrancar.

Uso:  python src/push_tablero.py --dsn "$DSN" --supabase "$SUPABASE_DSN"

Reconstruye el mismo payload que `build_tablero.py` embebe en dist/tablero.html
—misma consulta, mismas columnas— y lo escribe como una sola fila en
`portal.tablero`. El sitio lo lee de ahí con la sesión del usuario.

No duplica la consulta: la importa de build_tablero para que no puedan
divergir. Si mañana se agrega una columna al tablero, este script la sube sin
tocarlo.
"""
from __future__ import annotations

import argparse
import json
from datetime import date

import psycopg

from build_tablero import COLS, CONSULTA


def construir_payload(dsn: str) -> dict:
    cx = psycopg.connect(dsn)
    with cx.cursor() as cur:
        cur.execute(CONSULTA)
        crudas = cur.fetchall()
    cx.close()

    def num(x, div=1):
        return None if x is None else round(float(x) / div)

    filas = []
    for r in crudas:
        f = list(r)
        for i in (16, 17, 18, 19):
            f[i] = num(f[i])
        f[26] = None if f[26] is None else round(float(f[26]), 1)
        f[29] = num(f[29], 1e6)
        f[30] = num(f[30], 1e6)
        i = COLS.index("score")
        for j in (i, i + 2, i + 3, i + 5):
            f[j] = None if f[j] is None else round(float(f[j]), 3)
        filas.append(f)

    from pathlib import Path
    pesos = json.loads(
        Path(__file__).resolve().parent.parent
        .joinpath("config/pesos_icp.json").read_text(encoding="utf-8"))

    return {"generado": date.today().isoformat(), "cols": COLS,
            "rows": filas, "pesos_icp": pesos}


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
    with cx.cursor() as cur:
        cur.execute("""
            SELECT filas,
                   jsonb_array_length(payload->'rows'),
                   jsonb_array_length(payload->'cols'),
                   pg_size_pretty(pg_column_size(payload)::bigint)
            FROM portal.tablero WHERE id = 1
        """)
        filas, n_rows, n_cols, peso = cur.fetchone()
    cx.close()

    ok = filas == n_rows == len(payload["rows"]) and n_cols == len(COLS)
    print(f"· en Supabase: {n_rows:,} filas · {n_cols} columnas · {peso}")
    print("✓ cuadra" if ok else "✗ NO cuadra con lo enviado")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
