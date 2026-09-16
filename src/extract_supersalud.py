"""
Descarga los estados financieros de IPS publicados por la Superintendencia
Nacional de Salud.

Hallazgo que condiciona este módulo: Supersalud NO publica información
financiera en datos.gov.co (solo tiene tres datasets, todos de transparencia).
Lo único disponible son archivos Excel anuales colgados en docs.supersalud.gov.co,
y **la última vigencia publicada es 2021**, pese a que la obligación de reporte
por Circular Única sigue vigente. Ver docs/00-fuentes.md.

Estructura de los archivos (verificada, no supuesta):

  IPS privadas — Catalogo_Cuentas_Diciembre_2021_IPS.xlsx
     hoja FT001-01  Grupo 1 NIIF (plenas)      167 IPS · 390 columnas · cabecera fila 14
     hoja FT001-02  Grupo 2 NIIF (pymes)     4.097 IPS · 351 columnas · cabecera fila 14
     hoja FT001-03  Grupo 3 (microempresas)  1.493 IPS · 266 columnas · cabecera fila 11
     llave: NIT + RAZÓN SOCIAL. No trae código de habilitación.

  IPS públicas — EEFF_IPS_Públicas_2021.xlsx
     hoja IPS_PUBLICAS_DIC_2021   920 ESE · 167 columnas · cabecera fila 9
     llave: Nit Y Código Habilitación -> cruce directo contra REPS.
     fuente declarada dentro del archivo: SIHO (Decreto 2193).

Los tres grupos NIIF tienen planes de cuentas DISTINTOS. Por eso el load guarda
las cuentas en JSONB y solo promueve a columnas tipadas las cifras comparables.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = ("https://docs.supersalud.gov.co/PortalWeb/SupervisionInstitucional/"
        "SituacionFinancieraPrestadoresServicios/")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

# clave -> (archivo remoto, vigencia, naturaleza)
ARCHIVOS = {
    "privadas_2021": ("Catalogo_Cuentas_Diciembre_2021_IPS.xlsx", 2021, "privada"),
    "publicas_2021": ("EEFF_IPS_P%C3%BAblicas_2021.xlsx", 2021, "publica"),
    "privadas_2020": ("EEFF%20IPS%20Privadas%202020.xlsx", 2020, "privada"),
    "publicas_2020": ("EEFF%20IPS%20Publicas%202020.xlsx", 2020, "publica"),
    "privadas_2019": ("EF%20IPS%20PRIVADAS%202019.xlsx", 2019, "privada"),
    "publicas_2019": ("EF%20ESES%202019%20V3.xlsx", 2019, "publica"),
}


def descargar(url: str, destino: Path) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=600) as r, destino.open("wb") as fh:
        total = 0
        while chunk := r.read(1 << 16):
            fh.write(chunk)
            total += len(chunk)
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default="data/raw")
    ap.add_argument("--claves", nargs="*", default=list(ARCHIVOS))
    args = ap.parse_args()

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    sello = datetime.now(timezone.utc).strftime("%Y%m%d")

    manifiesto = {
        "extraido_en": datetime.now(timezone.utc).isoformat(),
        "fuente": "Superintendencia Nacional de Salud · Estadísticas Financieras IPS",
        "nota": "Última vigencia publicada por la entidad: 2021. No hay cortes posteriores.",
        "archivos": [],
        "errores": [],
    }

    for clave in args.claves:
        remoto, vigencia, naturaleza = ARCHIVOS[clave]
        destino = salida / f"supersalud_{clave}.xlsx"
        print(f"· {clave} …", flush=True)
        try:
            n = descargar(BASE + remoto, destino)
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR: {e}", flush=True)
            manifiesto["errores"].append({"clave": clave, "error": str(e)})
            continue
        sha = hashlib.sha256(destino.read_bytes()).hexdigest()
        manifiesto["archivos"].append({
            "clave": clave,
            "url": BASE + remoto,
            "archivo": str(destino),
            "vigencia": vigencia,
            "naturaleza": naturaleza,
            "tamano_bytes": n,
            "sha256": sha,
        })
        print(f"  {n:,} bytes · vigencia {vigencia} · {naturaleza}", flush=True)

    ruta = salida / f"manifiesto_supersalud_{sello}.json"
    ruta.write_text(json.dumps(manifiesto, indent=2, ensure_ascii=False))
    print(f"\n manifiesto -> {ruta}")
    return 1 if manifiesto["errores"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
