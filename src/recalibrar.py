"""
Contrasta el score que el modelo le dio a cada cuenta contra lo que pasó de verdad.

Uso:  python src/recalibrar.py --dsn "$DSN" [--cierres datos/cierres.csv]

NO AJUSTA NADA. Solo muestra dónde el modelo se quedó corto o se pasó, para que
la decisión de mover un peso la tome una persona. Un ajuste automático sobre
doce cierres produciría pesos peores que los de partida, con la apariencia de
estar calibrados.

Cómo registrar un cierre
------------------------
Se agrega una línea a datos/cierres.csv, separada por `;`:

  nit                    NIT sin dígito de verificación, para cruzar con la base
  razon_social           solo para poder leer el archivo
  fecha_cierre           AAAA-MM-DD
  segmento_real          lo que resultó ser: ips_grande | ips_mediana | ips_pequena | ese | otro
  canal_entrada          referido | frio_llamada | frio_correo | linkedin | visita | secop | otro
  ciclo_dias             días entre el primer contacto y la firma
  interlocutores_reales  cuántas personas hubo que convencer
  ramos_activados        cuántos ramos quedaron activos a la fecha
  hubo_reclasificacion   si | no  (si se movió la clase de riesgo de ARL)
  notas                  texto libre

Qué compara
-----------
1. CAC: el ciclo y los interlocutores que el modelo supuso contra los reales.
   Si el modelo supone de menos de forma sistemática, su peso está subestimado.
2. LTV: los ramos que de verdad se activaron contra el score que se le dio.
   Si cuentas de score bajo activan muchos ramos, algo del LTV está mal pesado.
3. Reclasificación: si la señal acertó o no.
4. Canal: si los cierres llegan por un canal que el score no modela, lo dice.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
from pathlib import Path

import psycopg

MINIMO_RECOMENDADO = 12


def leer_cierres(ruta: Path) -> list[dict]:
    if not ruta.exists():
        return []
    with ruta.open(encoding="utf-8") as fh:
        return [f for f in csv.DictReader(fh, delimiter=";") if (f.get("nit") or "").strip()]


def entero(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def señal(nombre: str, diagnostico: str, accion: str) -> str:
    return f"  ▸ {nombre}\n      {diagnostico}\n      → {accion}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--cierres", default="datos/cierres.csv")
    ap.add_argument("--pesos", default="config/pesos_icp.json")
    args = ap.parse_args()

    cierres = leer_cierres(Path(args.cierres))
    if not cierres:
        print(f"No hay cierres registrados en {args.cierres}.")
        print("Agrega una línea por cada cuenta que cierres y vuelve a correr esto.")
        print("El encabezado del archivo ya trae el orden de las columnas.")
        return 0

    cfg = json.loads(Path(args.pesos).read_text(encoding="utf-8"))
    cx = psycopg.connect(args.dsn)

    filas = []
    sin_cruce = []
    with cx.cursor() as cur:
        for c in cierres:
            nit = "".join(ch for ch in c["nit"] if ch.isdigit())
            cur.execute("""
                SELECT p.razon_social, s.score, s.prioridad, s.desglose, e.personal_estimado
                FROM reps.prestador p
                JOIN reps.score_icp s ON s.prestador_id = p.id
                LEFT JOIN reps.estimacion_personal e ON e.prestador_id = p.id
                WHERE p.numero_identificacion = %s AND p.tipo_identificacion = 'NI'
            """, (nit,))
            row = cur.fetchone()
            if not row:
                sin_cruce.append(c.get("razon_social") or nit)
                continue
            razon, score, prioridad, desglose, planta = row
            filas.append({**c, "nit": nit, "razon_base": razon, "score": float(score),
                          "prioridad": prioridad, "desglose": desglose, "planta": planta})
    cx.close()

    print(f"══ Recalibración · {len(filas)} cierres cruzados de {len(cierres)} registrados")
    if sin_cruce:
        print(f"   Sin cruce en la base ({len(sin_cruce)}): {', '.join(sin_cruce[:6])}")
    if len(filas) < MINIMO_RECOMENDADO:
        print(f"   ⚠ Con menos de {MINIMO_RECOMENDADO} cierres esto es una lectura, no evidencia.")
    if not filas:
        return 0

    print("\n── Lo que cerró, contra lo que el modelo predijo")
    print(f"   {'cuenta':34s} {'score':>6s} {'prio':>6s} {'ciclo r/e':>11s} {'inter r/e':>10s} {'ramos':>6s}")
    dif_ciclo, dif_inter, ramos_por_prio = [], [], {}
    for f in filas:
        d = f["desglose"]["cac"]
        ciclo_est_dias = d["ciclo_meses_estimado"] * 30
        inter_est = d["interlocutores_estimados"]
        ciclo_real = entero(f.get("ciclo_dias"))
        inter_real = entero(f.get("interlocutores_reales"))
        ramos = entero(f.get("ramos_activados")) or 0
        ramos_por_prio.setdefault(f["prioridad"], []).append(ramos)
        if ciclo_real:
            dif_ciclo.append(ciclo_real - ciclo_est_dias)
        if inter_real:
            dif_inter.append(inter_real - inter_est)
        print(f"   {(f['razon_base'] or '')[:34]:34s} {f['score']:6.1f} {f['prioridad']:>6s} "
              f"{(str(ciclo_real or '·') + '/' + str(ciclo_est_dias)):>11s} "
              f"{(str(inter_real or '·') + '/' + str(inter_est)):>10s} {ramos:>6d}")

    print("\n── Qué parece mal pesado")
    avisos = []

    if dif_ciclo:
        med = st.median(dif_ciclo)
        if abs(med) >= 20:
            avisos.append(señal(
                "cac.pesos.ciclo_meses",
                f"El ciclo real va {'POR ENCIMA' if med > 0 else 'POR DEBAJO'} del estimado en "
                f"{abs(med):.0f} días de mediana ({len(dif_ciclo)} casos).",
                "Subir el peso." if med > 0 else "Bajarlo: el modelo está castigando ciclos que no son tan largos."))

    if dif_inter:
        med = st.median(dif_inter)
        if abs(med) >= 1:
            avisos.append(señal(
                "cac.pesos.interlocutores",
                f"Hubo {abs(med):.0f} interlocutor(es) {'más' if med > 0 else 'menos'} "
                f"de los supuestos, en mediana ({len(dif_inter)} casos).",
                "Revisar los tramos de `cac.escalas.tramos_planta`, no solo el peso: "
                "el error está en el proxy de tamaño."))

    if len(ramos_por_prio) > 1:
        medias = {k: st.mean(v) for k, v in ramos_por_prio.items() if v}
        orden_esperado = ["alta", "media", "baja"]
        presentes = [k for k in orden_esperado if k in medias]
        valores = [medias[k] for k in presentes]
        if len(valores) > 1 and valores != sorted(valores, reverse=True):
            avisos.append(señal(
                "ltv (el bloque entero)",
                "Las cuentas de prioridad más baja están activando MÁS ramos que las de "
                f"prioridad alta: {', '.join(f'{k}={medias[k]:.1f}' for k in presentes)}.",
                "El LTV está mirando la variable equivocada. Sospechar primero de "
                "`ltv.pesos.contratistas` y del `piso_operativo`."))

    reclas = [(f["prioridad"], (f.get("hubo_reclasificacion") or "").strip().lower(),
               bool(f["desglose"]["multiplicadores"]["senales_alto_riesgo"])) for f in filas]
    aciertos = sum(1 for _, real, pred in reclas if (real == "si") == pred)
    if reclas and aciertos / len(reclas) < 0.6:
        falsos_pos = sum(1 for _, real, pred in reclas if pred and real != "si")
        avisos.append(señal(
            "ltv.multiplicadores.reclasificacion_riesgo",
            f"La señal acertó en {aciertos} de {len(reclas)} cierres "
            f"({falsos_pos} falsos positivos).",
            "Bajar el multiplicador, o afinar qué servicios cuentan como alto riesgo "
            "en la consulta `riesgo` de src/score_icp.py."))

    canales = {}
    for f in filas:
        canales[(f.get("canal_entrada") or "sin dato").strip()] = \
            canales.get((f.get("canal_entrada") or "sin dato").strip(), 0) + 1
    dominante = max(canales, key=canales.get)
    if canales[dominante] / len(filas) >= 0.5 and dominante in ("referido", "secop"):
        avisos.append(señal(
            "el score completo",
            f"{canales[dominante]} de {len(filas)} cierres entraron por «{dominante}», "
            "que el score no modela.",
            "El score ordena outbound en frío. Si el negocio entra por referido, "
            "la lista de alta sirve para decidir A QUIÉN pedir el referido, no a quién llamar."))

    bajas_que_cerraron = [f for f in filas if f["prioridad"] == "baja"]
    if len(bajas_que_cerraron) / len(filas) >= 0.4:
        avisos.append(señal(
            "umbrales",
            f"{len(bajas_que_cerraron)} de {len(filas)} cierres venían de prioridad baja.",
            "Antes de tocar pesos, bajar `umbrales.media`: puede que el corte esté "
            "demasiado arriba y no que el modelo ordene mal."))

    if avisos:
        print("\n".join(avisos))
    else:
        print("  Nada llamativo. Con esta muestra, los pesos actuales aguantan.")

    print(f"\n── Los pesos se editan en {args.pesos} (versión {cfg['version']}).")
    print("   Después: python src/score_icp.py --dsn ... && python src/build_tablero.py --dsn ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
