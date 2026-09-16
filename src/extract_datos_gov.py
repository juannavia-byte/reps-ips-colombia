"""
Descarga el dataset abierto del REPS en datos.gov.co (`c36g-9fc2`).

NO es la fuente primaria. Se usa para dos cosas concretas:

1. Recuperar `tipo_identificacion` (NI/CC/CE/PT). La exportación de la consulta
   web trae la columna `tido_codigo` VACÍA en las 61.179 filas, y ese campo es
   necesario para que la llave de deduplicación distinga una cédula de un NIT
   con el mismo número. El tipo de documento no cambia con el tiempo, así que
   usar un corte más viejo para este campo puntual es seguro.

2. Medir el desfase entre el dataset abierto y la consulta web en vivo, que es
   un entregable pedido explícitamente.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RECURSO = "c36g-9fc2"
API = f"https://www.datos.gov.co/resource/{RECURSO}.json"
META = f"https://www.datos.gov.co/api/views/{RECURSO}.json"
PAGINA = 50_000


def _json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "reps-pipeline/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default="data/raw")
    args = ap.parse_args()
    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    sello = datetime.now(timezone.utc).strftime("%Y%m%d")

    meta = _json(META)
    actualizado = datetime.fromtimestamp(meta["rowsUpdatedAt"], timezone.utc).isoformat()
    print(f"· dataset '{meta['name']}'")
    print(f"  rowsUpdatedAt: {actualizado}")

    filas, offset = [], 0
    while True:
        lote = _json(f"{API}?$limit={PAGINA}&$offset={offset}&$order=:id")
        if not lote:
            break
        filas.extend(lote)
        offset += PAGINA
        print(f"  {len(filas):,} filas…", flush=True)
        if len(lote) < PAGINA:
            break

    destino = salida / f"datosgov_{RECURSO}_{sello}.json"
    crudo = json.dumps(filas, ensure_ascii=False).encode()
    destino.write_bytes(crudo)

    cortes = sorted({f.get("fecha_corte_reps", "") for f in filas})
    manifiesto = {
        "extraido_en": datetime.now(timezone.utc).isoformat(),
        "fuente": "datos.gov.co · Registro Especial de Prestadores y Sedes (c36g-9fc2)",
        "url": API,
        "archivo": str(destino),
        "sha256": hashlib.sha256(crudo).hexdigest(),
        "tamano_bytes": len(crudo),
        "filas": len(filas),
        "rows_updated_at": actualizado,
        "fecha_corte_declarada": cortes[0] if cortes else None,
        "cortes_distintos": cortes,
        "uso": "enriquecimiento de tipo_identificacion + comparación de desfase",
    }
    ruta = salida / f"manifiesto_datosgov_{sello}.json"
    ruta.write_text(json.dumps(manifiesto, indent=2, ensure_ascii=False))
    print(f"  {len(filas):,} filas · corte declarado: {manifiesto['fecha_corte_declarada']}")
    print(f" manifiesto -> {ruta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
