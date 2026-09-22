"""
La hoja de llamadas: una fila por empresa, para enriquecer con el teléfono en
la oreja.

    python src/hoja_enriquecimiento.py --entrada dist/pipeline-arl.json \
        --salida dist/llamadas.csv

Por qué una fila por EMPRESA y no por persona
---------------------------------------------
La primera versión de esta hoja iba por persona: 54 filas para 19 cuentas. Está
mal. Nadie enriquece personas — se hacen llamadas, y de cada llamada sale UNA
persona: el responsable de SST. Con una fila por persona hay que llenar una y
saltar dos, y el orden de la hoja deja de coincidir con el orden del trabajo.

Una llamada, una fila. Se baja por la columna de estado y se sabe dónde se
quedó uno sin leer nada más.

Por qué una hoja y no la interfaz de HubSpot
--------------------------------------------
Llenar siete campos por persona a través de los paneles de propiedades de un
CRM son dos clics y una búsqueda por campo. Con el teléfono en la oreja, eso se
traduce en datos que no se apuntan. En una hoja se tabula.

La hoja no sustituye a HubSpot: sustituye al formulario. La relación comercial
sigue viviendo en el CRM, y lo que se escriba acá termina allá y en Ariad.

Qué va en cada bloque
---------------------
  CONTEXTO   lo que ya se sabe. No se edita: está para no preguntar dos veces
             ni presentarse como si no supiéramos nada de la clínica.
  LLENAR     lo que sale de la llamada.
  OPCIONAL   la segunda persona, si la dan. La mayoría de las veces queda vacía
             y no pasa nada.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

# El orden es el de la conversación: primero dónde estoy, luego a quién llamo,
# luego lo que consigo. Las de contexto van primero para poder congelarlas.
COLUMNAS = [
    # ── contexto, no se edita
    "Estado", "Empresa", "Ciudad", "Conmutador", "Ya en la ficha", "NIT",
    # ── lo que sale de la llamada
    "SST · Nombre completo", "SST · Cargo exacto", "SST · Celular", "SST · Correo",
    "ARL actual", "Clase de riesgo", "Mes aniversario ARL",
    # ── si dan más
    "Otra persona · Nombre", "Otra persona · Cargo", "Otra persona · Celular",
    "Notas",
]

ESTADOS = ["pendiente", "contactado", "no contesta", "volver a llamar",
           "rechazó", "ya tiene ARL con nosotros"]
ARLS = ["sura", "positiva", "colmena", "bolivar", "equidad", "otra", "desconocida"]
CLASES = ["I", "II", "III", "IV", "V"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

GUION = """GUIÓN DE LA LLAMADA — el mismo para las 19

1. «Buenos días, le habla [tu nombre] de Proactivos. Estoy buscando a la persona
   encargada de seguridad y salud en el trabajo. ¿Me puede ayudar con el nombre
   y un celular donde ubicarla?»

2. Si preguntan de qué se trata:
   «Es sobre la clasificación de riesgo de la ARL. Queremos revisar con ella si
   la que tienen asignada corresponde a los servicios que prestan hoy.»

3. Si pasan al SST, tres preguntas cortas:
   · ¿Con qué ARL están hoy?
   · ¿Saben en qué clase de riesgo los tienen clasificados?
   · ¿En qué mes cumplen año con esa ARL?

   La tercera es la más valiosa: dispara la reactivación 75 días antes y no
   está en ningún registro público.

Si solo consigues UNA cosa, que sea el celular del SST.
"""


def canal(persona, tipo):
    for c in persona.get("canales") or []:
        if c["tipo"] == tipo:
            return c["valor"]
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entrada", default="dist/pipeline-arl.json")
    ap.add_argument("--salida", default="dist/llamadas.csv")
    args = ap.parse_args()

    cuentas = json.loads(Path(args.entrada).read_text(encoding="utf-8"))["cuentas"]

    # Por ciudad: cuatro llamadas seguidas a Cartagena se hacen mejor que cuatro
    # ciudades distintas, porque la conversación se afina con la repetición.
    cuentas.sort(key=lambda c: ((c.get("municipio") or "zzz"), -(c.get("score_icp") or 0)))

    filas = []
    for c in cuentas:
        conocidos = "; ".join(
            f"{p['nombre']} ({p.get('cargo') or 'sin cargo'})" for p in c["personas"][:3]
        ) or "nadie"
        conmutador = next((x["valor"] for x in c.get("canales_empresa", [])
                           if x["tipo"] == "telefono"), "SIN TELÉFONO")
        filas.append({
            "Estado": "pendiente",
            "Empresa": c["razon_social"],
            "Ciudad": c.get("municipio") or "",
            "Conmutador": conmutador,
            "Ya en la ficha": conocidos,
            "NIT": c["nit"],
            "SST · Nombre completo": "", "SST · Cargo exacto": "",
            "SST · Celular": "", "SST · Correo": "",
            "ARL actual": "", "Clase de riesgo": "", "Mes aniversario ARL": "",
            "Otra persona · Nombre": "", "Otra persona · Cargo": "",
            "Otra persona · Celular": "", "Notas": "",
        })

    destino = Path(args.salida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS, delimiter=";")
        w.writeheader()
        w.writerows(filas)

    # El guion y los valores válidos, aparte: dentro del CSV estorbarían al
    # importar y fuera se pueden tener abiertos al lado mientras se llama.
    ayuda = destino.with_name(destino.stem + "-guion.txt")
    ayuda.write_text(
        GUION
        + "\n\nVALORES VÁLIDOS (escribir tal cual)\n"
        + f"  Estado          {' | '.join(ESTADOS)}\n"
        + f"  ARL actual      {' | '.join(ARLS)}\n"
        + f"  Clase de riesgo {' | '.join(CLASES)}\n"
        + f"  Mes aniversario {' | '.join(MESES)}\n",
        encoding="utf-8")

    sin_tel = sum(1 for f in filas if f["Conmutador"] == "SIN TELÉFONO")
    print(f"· {len(filas)} llamadas → {destino}")
    print(f"· guion y valores válidos → {ayuda}")
    if sin_tel:
        print(f"· ⚠ {sin_tel} sin teléfono: buscarlo antes de llamar")
    ciudades = {}
    for f in filas:
        ciudades[f["Ciudad"]] = ciudades.get(f["Ciudad"], 0) + 1
    juntas = [f"{c} ({n})" for c, n in sorted(ciudades.items(), key=lambda x: -x[1]) if n > 1]
    if juntas:
        print(f"· agrupadas por ciudad — para encadenar: {', '.join(juntas)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
