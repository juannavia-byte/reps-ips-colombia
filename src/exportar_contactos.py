"""
Exporta los contactos enriquecidos.

    python src/exportar_contactos.py --dsn "$DSN" --prioridad alta
    python src/exportar_contactos.py --dsn "$DSN" --prioridad alta --formato hubspot
    python src/exportar_contactos.py --dsn "$DSN" --min-confianza 60 --sin-adivinados

Dos formatos:

  ariad     una fila por persona, con todos sus canales y su evidencia. Es para
            mirar y decidir a quién contactar.
  hubspot   una fila por persona con los nombres de propiedad que HubSpot espera
            en una importación de contactos asociados a empresa.

El formato hubspot NO llama a la API. Deja el archivo listo para subir a mano o
para que un paso posterior lo empuje. Se hizo así a propósito: cargar contactos
en el CRM es irreversible en la práctica —quedan en secuencias, en listas, en
informes— y conviene mirar el archivo antes.

La lista de exclusión se aplica SIEMPRE, en los dos formatos. Es el punto donde
de verdad importa: da igual lo que haya en la base si lo que sale hacia una
campaña ya está filtrado.
"""
from __future__ import annotations

import argparse
import csv
import sys

import psycopg

CONSULTA = """
WITH excluidos AS (
  SELECT coalesce(nombre_clave,'') nk, coalesce(valor_norm,'') vn,
         coalesce(prestador_id, -1) pid
  FROM enriquecimiento.exclusion
),
canal_origen AS (
  SELECT c.*, EXISTS (
    SELECT 1 FROM enriquecimiento.evidencia e
    WHERE e.canal_id = c.id AND e.fuente <> 'patron') AS observado
  FROM enriquecimiento.canal c
)
SELECT
  p.numero_identificacion AS nit, p.digito_verificacion AS dv, p.razon_social,
  s.score, s.prioridad,
  sr.departamento, sr.municipio,
  pe.nombre, pe.documento, pe.cargo, pe.cargo_categoria, pe.tier,
  pe.estado, pe.confianza, pe.ultima_verificacion,
  -- Los canales se agregan en texto para que una fila sea una persona. Cada
  -- uno lleva su ámbito, su estado y su confianza pegados: sin eso, quien lee
  -- el CSV no puede distinguir un correo publicado de uno adivinado.
  (SELECT string_agg(
      c.tipo || ':' || c.valor || ' [' || c.ambito || '/' || c.estado || '/' || c.confianza || ']',
      ' | ' ORDER BY c.observado DESC, c.confianza DESC)
   FROM canal_origen c
   WHERE c.persona_id = pe.id
     AND (%(sin_adivinados)s::boolean IS NOT TRUE OR c.observado)
     AND c.confianza >= %(min_conf)s::int) AS canales_persona,
  (SELECT string_agg(
      c.tipo || ':' || c.valor || ' [' || c.ambito || ']',
      ' | ' ORDER BY c.confianza DESC)
   FROM canal_origen c
   WHERE c.prestador_id = p.id AND c.persona_id IS NULL AND c.observado) AS canales_empresa,
  (SELECT string_agg(DISTINCT e.fuente, ',')
   FROM enriquecimiento.evidencia e WHERE e.persona_id = pe.id) AS fuentes
FROM enriquecimiento.persona pe
JOIN reps.prestador p ON p.id = pe.prestador_id
JOIN reps.score_icp s ON s.prestador_id = p.id
LEFT JOIN LATERAL (
  SELECT sd.departamento, sd.municipio
  FROM reps.sede sd JOIN reps.registro_habilitacion r ON r.id = sd.registro_id
  WHERE r.prestador_id = p.id
  ORDER BY (sd.es_principal IS TRUE) DESC, sd.id LIMIT 1) sr ON TRUE
WHERE pe.confianza >= %(min_conf)s::int
  AND (%(prioridad)s::text IS NULL OR s.prioridad = %(prioridad)s::text)
  AND (%(tier_max)s::int IS NULL OR pe.tier <= %(tier_max)s::int)
  AND pe.estado <> 'obsoleto'
  AND NOT EXISTS (SELECT 1 FROM excluidos x
                  WHERE x.nk = pe.nombre_clave OR x.pid = p.id)
ORDER BY s.score DESC, coalesce(pe.tier, 9), pe.confianza DESC
"""


def fila_hubspot(r: dict) -> dict:
    """
    Nombres de propiedad de HubSpot. `email` va vacío si no hay un correo
    OBSERVADO de la persona: importar una conjetura al CRM la convierte en un
    dato que después nadie distingue de uno real, y rebota contra el dominio
    desde el que se envía.
    """
    correo = ""
    telefono = ""
    # `canales_persona` ya viene ordenado por observado y luego por confianza, y
    # la consulta ya excluyó los adivinados cuando se pidió --sin-adivinados.
    # Así que basta con tomar el primero de cada tipo: el mejor está arriba.
    for trozo in (r.get("canales_persona") or "").split(" | "):
        if not trozo:
            continue
        tipo, _, resto = trozo.partition(":")
        valor = resto.split(" [")[0]
        if tipo == "correo" and not correo:
            correo = valor
        elif tipo in ("telefono", "whatsapp") and not telefono:
            telefono = valor
    nombre = (r.get("nombre") or "").split()
    return {
        "email": correo,
        "firstname": nombre[0] if nombre else "",
        "lastname": " ".join(nombre[1:]) if len(nombre) > 1 else "",
        "jobtitle": r.get("cargo") or "",
        "phone": telefono,
        "company": r.get("razon_social") or "",
        "ariad_nit": r.get("nit") or "",
        "ariad_score_icp": r.get("score"),
        "ariad_tier_contacto": r.get("tier"),
        "ariad_confianza": r.get("confianza"),
        "ariad_estado_dato": r.get("estado"),
        "ariad_fuentes": r.get("fuentes") or "",
        "ariad_verificado_en": r.get("ultima_verificacion"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--prioridad", choices=["alta", "media", "baja"])
    ap.add_argument("--tier-max", type=int)
    ap.add_argument("--min-confianza", type=int, default=0)
    ap.add_argument("--sin-adivinados", action="store_true",
                    help="omite los correos generados por patrón")
    ap.add_argument("--formato", choices=["ariad", "hubspot"], default="ariad")
    ap.add_argument("--salida")
    args = ap.parse_args()

    cx = psycopg.connect(args.dsn)
    with cx.cursor() as cur:
        cur.execute(CONSULTA, {"prioridad": args.prioridad, "tier_max": args.tier_max,
                               "min_conf": args.min_confianza,
                               "sin_adivinados": args.sin_adivinados})
        cols = [d.name for d in cur.description]
        filas = [dict(zip(cols, f)) for f in cur.fetchall()]
    cx.close()

    if args.formato == "hubspot":
        filas = [fila_hubspot(f) for f in filas]
        cols = list(filas[0].keys()) if filas else []

    destino = open(args.salida, "w", newline="", encoding="utf-8-sig") if args.salida else sys.stdout
    # `;` y BOM: es lo que Excel en español abre sin romper los acentos.
    w = csv.DictWriter(destino, fieldnames=cols, delimiter=";", extrasaction="ignore")
    w.writeheader()
    for f in filas:
        w.writerow(f)
    if args.salida:
        destino.close()
        print("· %d filas → %s" % (len(filas), args.salida))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
