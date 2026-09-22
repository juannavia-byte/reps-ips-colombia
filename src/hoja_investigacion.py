"""
La hoja de investigación: una fila por empresa, con las búsquedas ya armadas.

    python src/hoja_investigacion.py --entrada dist/pipeline-arl.json \
        --salida dist/investigar.csv

Por qué esta hoja y no la de llamadas
-------------------------------------
La hoja anterior asumía que se llama al conmutador para que ruteen hacia el
responsable de SST. Eso cambió: primero se investiga, se confirma quién es, y
se le llama directo.

Es una decisión mejor y cambia el trabajo entero. Llamar a recepción a ciegas
gasta la primera impresión en un filtro; llegar sabiendo el nombre convierte la
llamada en una conversación. Pero el cuello de botella se mueve: ya no es el
teléfono, es teclear la misma consulta veinte veces.

Por eso las búsquedas vienen construidas. Un clic por fuente, no una consulta
escrita a mano.

Las fuentes, en el orden en que rinden
--------------------------------------
  1. LinkedIn por empresa y cargo — lo más directo cuando la persona existe ahí
  2. Google acotado a perfiles de LinkedIn — encuentra lo que la búsqueda
     interna de LinkedIn esconde tras su login
  3. La web de la clínica — «quiénes somos», «equipo», «transparencia»
  4. Google general por el cargo — notas de prensa, actas, boletines gremiales

⚠ Son búsquedas públicas, para abrir en el navegador y mirar con los ojos. No
hay automatización de LinkedIn: raspar su sitio viola sus términos y arriesga
la cuenta desde la que después se escribe.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import urllib.parse
from pathlib import Path

COLUMNAS = [
    "Estado", "Empresa", "Ciudad", "NIT",
    "Ya en la ficha", "Web", "Conmutador",
    "1· LinkedIn", "2· Google → LinkedIn", "3· Sitio web", "4· Google general",
    "SST · Nombre", "SST · Cargo", "SST · Perfil LinkedIn",
    "SST · Celular", "SST · Correo",
    "¿Confirmado?", "Notas",
]

ESTADOS = ["pendiente", "investigando", "persona confirmada",
           "no aparece", "descartada"]

GENERICOS = {"gmail.com", "hotmail.com", "hotmail.es", "outlook.com",
             "outlook.es", "yahoo.com", "yahoo.es", "live.com"}

CARGOS = '"seguridad y salud en el trabajo" OR "SST" OR "SG-SST" OR "HSEQ"'


def q(texto: str) -> str:
    return urllib.parse.quote_plus(texto)


def dominio_de(c: dict) -> str:
    """
    El dominio propio, si lo hay. Un gmail no sirve para buscar la web.

    El REPS guarda las webs en mayúsculas, y un `replace("https://", ...)` a
    secas no casa con «HTTPS://»: la columna acababa en
    `https://HTTPS://WWW.CLINICA.COM` y la búsqueda `site:` no devolvía nada.
    """
    for x in c.get("canales_empresa", []):
        if x["tipo"] == "web":
            return re.sub(r"^https?://", "", x["valor"].strip(), flags=re.I).strip("/").lower()
    for x in c.get("canales_empresa", []):
        if x["tipo"] == "correo":
            d = x["valor"].split("@")[-1].lower()
            if d not in GENERICOS:
                return d
    return ""


def nombre_corto(razon: str) -> str:
    """
    Sin la forma societaria. «CLINICA CASANARE S.A» busca peor que «CLINICA
    CASANARE»: el S.A ensucia la consulta y no aporta nada.
    """
    n = re.sub(r"\b(S\.?A\.?S?|LTDA|E\.?S\.?E|E\.?U|S\.?C\.?A)\.?\b", "", razon, flags=re.I)
    return re.sub(r"\s+", " ", n).strip(" .,-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entrada", default="dist/pipeline-arl.json")
    ap.add_argument("--salida", default="dist/investigar.csv")
    args = ap.parse_args()

    cuentas = json.loads(Path(args.entrada).read_text(encoding="utf-8"))["cuentas"]
    cuentas.sort(key=lambda c: ((c.get("municipio") or "zzz"), -(c.get("score_icp") or 0)))

    filas, sin_web = [], 0
    for c in cuentas:
        nom = nombre_corto(c["razon_social"])
        dom = dominio_de(c)
        if not dom:
            sin_web += 1
        conmutador = next((x["valor"] for x in c.get("canales_empresa", [])
                           if x["tipo"] == "telefono"), "")

        filas.append({
            "Estado": "pendiente",
            "Empresa": c["razon_social"],
            "Ciudad": c.get("municipio") or "",
            "NIT": c["nit"],
            "Ya en la ficha": "; ".join(
                f"{p['nombre']} ({p.get('cargo') or '?'})" for p in c["personas"][:3]) or "nadie",
            "Web": ("https://" + dom) if dom else "",
            "Conmutador": conmutador,
            # 1 · La búsqueda de personas de LinkedIn, por empresa y cargo.
            "1· LinkedIn": "https://www.linkedin.com/search/results/people/?keywords="
                           + q(f"{nom} seguridad y salud en el trabajo"),
            # 2 · Google acotado a perfiles. Encuentra lo que LinkedIn esconde
            #     tras su login, y es la vía que el brief pedía usar.
            "2· Google → LinkedIn": "https://www.google.com/search?q="
                                    + q(f'site:linkedin.com/in "{nom}" ({CARGOS})'),
            # 3 · La web de la clínica, acotada a las páginas donde se publica
            #     el equipo. Sin dominio propio esta columna va vacía.
            "3· Sitio web": ("https://www.google.com/search?q="
                             + q(f"site:{dom} (equipo OR directivos OR organigrama OR "
                                 f"transparencia OR \"quienes somos\")")) if dom else "",
            # 4 · Prensa, actas, boletines. Donde aparecen los que no usan redes.
            "4· Google general": "https://www.google.com/search?q="
                                 + q(f'"{nom}" ({CARGOS})'),
            "SST · Nombre": "", "SST · Cargo": "", "SST · Perfil LinkedIn": "",
            "SST · Celular": "", "SST · Correo": "",
            "¿Confirmado?": "", "Notas": "",
        })

    destino = Path(args.salida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS, delimiter=";")
        w.writeheader()
        w.writerows(filas)

    print(f"· {len(filas)} cuentas por investigar → {destino}")
    print(f"· {len(filas) - sin_web} con dominio propio · {sin_web} sin web "
          f"(la columna 3 va vacía en esas)")
    print(f"· estados válidos: {' | '.join(ESTADOS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
