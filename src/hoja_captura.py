"""
La hoja de captura: se investiga una empresa y se escriben ahí TODAS las
personas y TODOS sus medios de contacto.

    python src/hoja_captura.py --entrada dist/pipeline-arl.json \
        --salida dist/captura.csv
    python src/hoja_captura.py --nit 900123456,891800330   # sólo esas dos

Y cuando esté llena, vuelve a Ariad:

    python src/importar_hoja.py --dsn "$DSN" --entrada dist/captura.csv --simular
    python src/importar_hoja.py --dsn "$DSN" --entrada dist/captura.csv

Por qué esta hoja y no `hoja_investigacion.py`
----------------------------------------------
Aquélla busca UNA cosa: el responsable de SST, para poder llamarlo. Tiene
cinco columnas de captura y ninguna ruta de vuelta — lo que se escribe ahí se
queda en el archivo.

Ésta es lo contrario: se recorre la empresa entera y se guarda todo el que
aparezca, con todos los medios por los que se le pueda escribir. El criterio
es el del esquema, no el de la campaña de hoy: «un cargo que hoy parece
irrelevante es la puerta de entrada de mañana». El gerente que hoy no sirve
para ARL es quien firma el colectivo de vida el año que viene.

Por qué una fila por PERSONA
----------------------------
Una fila por canal sería más fiel a la tabla `canal` —cada dato con su propia
fuente y su confianza— y es inviable de teclear: cuatro filas para una sola
persona, repitiendo empresa, NIT y nombre en cada una. Con once columnas de
canal en una fila se tabula de izquierda a derecha sin soltar el teclado, y el
importador se encarga de repartir cada columna a su tipo y su ámbito.

El contexto y las búsquedas van repetidos en todas las filas de una empresa, a
propósito: así se puede llenar cualquiera de ellas sin tener que subir a
buscar de qué empresa era. Si una empresa da más personas que filas, se
duplica una fila y se cambia el nombre — el importador agrupa por NIT, no por
posición.

Qué va en cada bloque
---------------------
  CONTEXTO    lo que Ariad ya sabe. No se edita: está para no volver a buscar
              lo que ya está y para no apuntar dos veces a la misma persona.
  BÚSQUEDAS   un clic por fuente. Son enlaces públicos para abrir y mirar con
              los ojos; no hay automatización de LinkedIn.
  CAPTURA     la persona y todos sus medios de contacto.

La columna `Confirmado`
-----------------------
Es la que decide con qué confianza entra la fila, y por eso no es opcional.

  si       lo vi publicado o me lo dijeron            → verificado, confianza 90
  no       lo deduje (patrón de correo, un homónimo)  → inferido, confianza 40

El esquema es explícito en que un correo no se marca verificado por parecer
correcto, y la disciplina se cae completa si todo lo tecleado a mano entra
como verificado por el solo hecho de haberlo tecleado. Vacío se trata como
`si`: lo normal es apuntar lo que se vio.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import urllib.parse
from pathlib import Path

# El orden es el del trabajo: dónde estoy, dónde busco, qué encuentro.
CONTEXTO = ["Estado", "Empresa", "Ciudad", "NIT", "Ya en la ficha", "Web", "Conmutador"]
BUSQUEDAS = ["1· LinkedIn", "2· Google → LinkedIn", "3· Sitio web", "4· Google general"]
CAPTURA = [
    "Nombre", "Cargo",
    "Correo", "Celular", "Fijo", "WhatsApp",
    "LinkedIn", "Facebook", "Instagram", "X", "TikTok", "Telegram",
    "Confirmado", "Fuente", "Notas",
]
COLUMNAS = CONTEXTO + BUSQUEDAS + CAPTURA

ESTADOS = ["pendiente", "investigando", "lista", "no aparece", "descartada"]

GENERICOS = {"gmail.com", "hotmail.com", "hotmail.es", "outlook.com",
             "outlook.es", "yahoo.com", "yahoo.es", "live.com"}

# Sin acotar a SST: esta hoja recoge a todo el que aparezca. Los cargos que
# más rinden en una IPS, en el orden en que suelen estar publicados.
CARGOS = ('"gerente" OR "director" OR "representante legal" OR '
          '"seguridad y salud en el trabajo" OR "SST" OR "talento humano"')


def q(texto: str) -> str:
    return urllib.parse.quote_plus(texto)


def dominio_de(c: dict) -> str:
    """
    El dominio propio, si lo hay. Un gmail no sirve para buscar la web.

    El REPS guarda las webs en mayúsculas («HTTPS://WWW.CLINICA.COM»), así que
    el esquema se quita con una expresión insensible a mayúsculas. Recortarlo
    con un `replace("https://", ...)` a secas no casa, y la columna acaba
    diciendo `https://HTTPS://WWW.CLINICA.COM` — un enlace muerto y una
    búsqueda `site:` que no devuelve nada.
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
    ap.add_argument("--salida", default="dist/captura.csv")
    ap.add_argument("--nit", help="sólo estas empresas, separadas por coma")
    ap.add_argument("--filas-por-empresa", type=int, default=3,
                    help="filas en blanco por empresa (por defecto 3)")
    args = ap.parse_args()

    cuentas = json.loads(Path(args.entrada).read_text(encoding="utf-8"))["cuentas"]

    if args.nit:
        pedidos = {n.strip() for n in args.nit.split(",") if n.strip()}
        cuentas = [c for c in cuentas if c["nit"] in pedidos]
        faltan = pedidos - {c["nit"] for c in cuentas}
        if faltan:
            print(f"⚠ sin cuenta en {args.entrada}: {', '.join(sorted(faltan))}")

    # Por ciudad y dentro de ella por score: se investiga una plaza entera de
    # corrido, que es como se acumula contexto sobre un mercado.
    cuentas.sort(key=lambda c: ((c.get("municipio") or "zzz"), -(c.get("score_icp") or 0)))

    filas, sin_web = [], 0
    for c in cuentas:
        nom = nombre_corto(c["razon_social"])
        dom = dominio_de(c)
        if not dom:
            sin_web += 1
        conmutador = next((x["valor"] for x in c.get("canales_empresa", [])
                           if x["tipo"] == "telefono"), "")

        # Todas las que ya están, no las tres primeras: el punto de esta
        # columna es no volver a apuntar a alguien que Ariad ya tiene, y con
        # un corte en tres se apunta igual al cuarto.
        ya = "; ".join(f"{p['nombre']} ({p.get('cargo') or '?'})"
                       for p in c["personas"]) or "nadie"

        contexto = {
            "Estado": "pendiente",
            "Empresa": c["razon_social"],
            "Ciudad": c.get("municipio") or "",
            "NIT": c["nit"],
            "Ya en la ficha": ya,
            "Web": ("https://" + dom) if dom else "",
            "Conmutador": conmutador,
            "1· LinkedIn": "https://www.linkedin.com/search/results/people/?keywords=" + q(nom),
            "2· Google → LinkedIn": "https://www.google.com/search?q="
                                    + q(f'site:linkedin.com/in "{nom}"'),
            "3· Sitio web": ("https://www.google.com/search?q="
                             + q(f"site:{dom} (equipo OR directivos OR organigrama OR "
                                 f"transparencia OR contacto OR \"quienes somos\")")) if dom else "",
            "4· Google general": "https://www.google.com/search?q=" + q(f'"{nom}" ({CARGOS})'),
        }
        for _ in range(max(1, args.filas_por_empresa)):
            filas.append({**contexto, **{k: "" for k in CAPTURA}})

    destino = Path(args.salida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig porque Excel abre el utf-8 pelado como latin-1 y parte cada
    # tilde en dos caracteres. El ';' es el separador que espera en es-CO.
    with destino.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS, delimiter=";")
        w.writeheader()
        w.writerows(filas)

    empresas = len(filas) // max(1, args.filas_por_empresa)
    print(f"· {empresas} empresas · {len(filas)} filas → {destino}")
    print(f"· {empresas - sin_web} con dominio propio · {sin_web} sin web "
          f"(la columna 3 va vacía en esas)")
    print(f"· estados válidos: {' | '.join(ESTADOS)}")
    print(f"· al terminar: python src/importar_hoja.py --dsn \"$DSN\" "
          f"--entrada {destino} --simular")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
