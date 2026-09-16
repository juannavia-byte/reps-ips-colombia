"""
Descarga los cuatro exports nacionales del REPS y deja un manifiesto de extracción.

Uso:  python src/extract_reps.py [--salida data/raw]

Cada corrida escribe:
  data/raw/reps_<clave>_<AAAAMMDD>.csv   el crudo, tal cual lo entrega el portal
  data/raw/manifiesto_reps_<AAAAMMDD>.json  sha256, bytes, fecha de corte, duración

El manifiesto es lo que después alimenta la tabla `fuente_extraccion`: sin él no
hay trazabilidad de qué corrida produjo cada fila.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reps_client import CONSULTAS, RepsClient, RepsError  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default="data/raw")
    ap.add_argument("--pausa", type=float, default=1.5,
                    help="segundos entre peticiones; subir si el portal responde lento")
    ap.add_argument("--solo", nargs="*", default=None,
                    help="claves a extraer (prestadores sedes servicios capacidad)")
    args = ap.parse_args()

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    sello = datetime.now(timezone.utc).strftime("%Y%m%d")

    cliente = RepsClient(pausa=args.pausa)
    print("· autenticando como invitado…", flush=True)
    cliente.login()
    print("  sesión establecida", flush=True)

    manifiesto = {
        "extraido_en": datetime.now(timezone.utc).isoformat(),
        "fuente": "REPS · consulta pública (prestadores.minsalud.gov.co/habilitacion)",
        "consultas": [],
        "errores": [],
    }

    for consulta in CONSULTAS:
        if args.solo and consulta.clave not in args.solo:
            continue
        print(f"· {consulta.clave:12s} {consulta.descripcion}…", flush=True)
        try:
            res = cliente.exportar(consulta)
        except RepsError as e:
            print(f"  ERROR: {e}", flush=True)
            manifiesto["errores"].append({"clave": consulta.clave, "error": str(e)})
            continue

        destino = salida / f"reps_{consulta.clave}_{sello}.csv"
        destino.write_bytes(res["bytes"])
        # El portal mezcla terminadores: la mayoría de los exports usan \r suelto
        # y solo unas pocas líneas traen \r\n. Con `or` en cadena se tomaba el
        # primer conteo distinto de cero y salía un número absurdo. El conteo
        # definitivo lo recalcula el loader al parsear.
        crudo = res["bytes"]
        filas = max(crudo.count(b"\r\n"), crudo.count(b"\n"), crudo.count(b"\r"))

        entrada = {k: v for k, v in res.items() if k != "bytes"}
        entrada.update({
            "archivo": str(destino),
            "grano": consulta.grano,
            "filas_aprox": max(filas - 1, 0),
        })
        manifiesto["consultas"].append(entrada)
        print(
            f"  {entrada['tamano_bytes']:,} bytes · ~{entrada['filas_aprox']:,} filas · "
            f"{entrada['segundos']}s · corte: {entrada['fecha_corte_declarada']}",
            flush=True,
        )

    ruta = salida / f"manifiesto_reps_{sello}.json"
    ruta.write_text(json.dumps(manifiesto, indent=2, ensure_ascii=False))
    print(f"\n manifiesto -> {ruta}")
    return 1 if manifiesto["errores"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
