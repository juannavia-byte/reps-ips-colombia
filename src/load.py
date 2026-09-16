"""
Carga REPS + Supersalud en Postgres, con deduplicación, QA y trazabilidad.

Uso:  python src/load.py --dsn postgresql://reps:reps@localhost:55432/reps \
                        --raw data/raw [--schema src/schema.sql]

Decisiones que no son obvias y conviene no revertir sin leer esto
-----------------------------------------------------------------
· El separador se DETECTA por archivo. El portal respeta `tbSeparator` en
  habilitados_reps.aspx pero lo ignora en sedes/servicios/capacidad, que
  siempre salen con ';'. Fijarlo por configuración rompería tres de cuatro.

· Las líneas se parten con \r\n | \r | \n. Los exports usan \r suelto.

· `tido_codigo` viene VACÍO en el export web, así que el tipo de documento se
  recupera del dataset abierto c36g-9fc2 por código de prestador. Lo que no
  cruza se deriva de clase_persona (JURIDICO->NI, NATURAL->CC) y queda
  registrado en qa_inconsistencia como derivado, no como dato de fuente.

· Servicios y capacidad se extrajeron minutos después que sedes. Si apuntan a
  una sede que no está en el export de sedes, la fila se registra en
  qa_inconsistencia y se omite: inventar la sede sería fabricar un dato.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import psycopg

SEPARADORES = ("|", ";")
SALTOS = re.compile(r"\r\n|\r|\n")


# --------------------------------------------------------------- utilidades


def detectar_separador(ruta: Path) -> str:
    with ruta.open("rb") as fh:
        cabecera = SALTOS.split(fh.read(65536).decode("cp1252", "ignore"))[0]
    return max(SEPARADORES, key=cabecera.count)


def leer_csv(ruta: Path):
    """Devuelve (columnas, generador de filas, estadisticas)."""
    sep = detectar_separador(ruta)
    texto = ruta.read_bytes().decode("cp1252", "ignore")
    lineas = [l for l in SALTOS.split(texto) if l.strip()]
    columnas = [c.strip() for c in lineas[0].split(sep)]
    n = len(columnas)
    stats = {"separador": sep, "columnas": n, "filas": 0, "malformadas": 0}

    def filas():
        for i, linea in enumerate(lineas[1:], start=2):
            partes = linea.split(sep)
            # El portal agrega un separador final: la fila trae n+1 campos.
            if len(partes) == n + 1 and partes[-1] == "":
                partes = partes[:n]
            if len(partes) != n:
                stats["malformadas"] += 1
                yield i, None, linea
                continue
            stats["filas"] += 1
            yield i, dict(zip(columnas, (p.strip() for p in partes))), linea

    return columnas, filas, stats


def fecha(v: str | None):
    """AAAAMMDD -> date. El REPS usa ese formato en todos sus campos de fecha."""
    if not v:
        return None
    v = v.strip()
    if len(v) == 8 and v.isdigit():
        try:
            return datetime.strptime(v, "%Y%m%d").date()
        except ValueError:
            return None
    return None


def booleano(v: str | None):
    if v is None:
        return None
    v = v.strip().upper()
    return True if v == "SI" else False if v == "NO" else None


def entero(v: str | None):
    if not v:
        return None
    v = v.strip()
    return int(v) if v.lstrip("-").isdigit() else None


def texto(v: str | None):
    v = (v or "").strip()
    return v or None


def dv_correcto(nit: str, dv: str | None) -> bool | None:
    """Dígito de verificación del NIT (algoritmo DIAN). None si no aplica."""
    if not nit or not nit.isdigit() or dv is None or not dv.strip().isdigit():
        return None
    pesos = [3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71]
    total = sum(int(d) * pesos[i] for i, d in enumerate(reversed(nit)) if i < len(pesos))
    resto = total % 11
    esperado = 0 if resto in (0, 1) else 11 - resto
    return esperado == int(dv.strip())


# ------------------------------------------------------------------- carga


class Cargador:
    def __init__(self, dsn: str, raw: Path):
        self.cx = psycopg.connect(dsn, autocommit=False)
        self.raw = raw
        self.incidencias: list[tuple] = []
        self.resumen: dict = {}

    def cerrar(self):
        self.cx.close()

    def incidencia(self, ext_id, tabla, clave, tipo, detalle, crudo=None):
        self.incidencias.append((ext_id, tabla, clave, tipo, detalle, (crudo or "")[:500]))

    def volcar_incidencias(self):
        if not self.incidencias:
            return
        with self.cx.cursor() as cur, cur.copy(
            "COPY reps.qa_inconsistencia (extraccion_id, tabla, clave, tipo, detalle, valor_crudo) FROM STDIN"
        ) as cp:
            for fila in self.incidencias:
                cp.write_row(fila)
        self.incidencias.clear()

    # ------------------------------------------------------------- linaje

    def registrar_extraccion(self, fuente, recurso, url, extraido_en, corte,
                             archivo, sha, tam, filas, sep, notas=None) -> int:
        with self.cx.cursor() as cur:
            cur.execute(
                """INSERT INTO reps.extraccion
                   (fuente, recurso, url, extraido_en, fecha_corte_declarada, archivo,
                    sha256, tamano_bytes, filas_crudas, separador, notas)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (fuente, recurso, url, extraido_en, corte, archivo, sha, tam, filas, sep, notas),
            )
            return cur.fetchone()[0]

    # -------------------------------------------------------- prestadores

    def tipos_documento(self) -> dict[str, str]:
        """codigo_prestador -> tipo de identificación, desde el dataset abierto."""
        candidatos = sorted(self.raw.glob("datosgov_c36g-9fc2_*.json"))
        if not candidatos:
            return {}
        datos = json.loads(candidatos[-1].read_text())
        return {
            f["codigoprestador"]: f.get("tipoid", "").strip()
            for f in datos
            if f.get("codigoprestador") and f.get("tipoid")
        }

    def cargar_prestadores(self, ruta: Path, ext_id: int) -> dict[str, int]:
        cols, filas, stats = leer_csv(ruta)
        tipos = self.tipos_documento()
        derivados = 0

        # 1ª pasada: agrupar registros por entidad.
        entidades: dict[tuple, dict] = {}
        registros: list[dict] = []
        for nlinea, fila, crudo in filas():
            if fila is None:
                self.incidencia(ext_id, "prestador", f"linea {nlinea}", "fila_malformada",
                                f"campos != {stats['columnas']}", crudo)
                continue

            codigo = fila["codigo_habilitacion"]
            numero = fila["nits_nit"]
            clase_persona = fila.get("clase_persona", "")
            tipo = tipos.get(codigo)
            if not tipo:
                tipo = "NI" if clase_persona == "JURIDICO" else "CC"
                derivados += 1
            llave = (tipo, numero)

            if not numero:
                self.incidencia(ext_id, "prestador", codigo, "identificacion_vacia",
                                "nits_nit sin valor", crudo)
                continue
            if not numero.isdigit():
                self.incidencia(ext_id, "prestador", codigo, "nit_mal_formado",
                                f"no numérico: {numero!r}", crudo)
            if tipo == "NI" and dv_correcto(numero, fila.get("dv")) is False:
                self.incidencia(ext_id, "prestador", codigo, "dv_invalido",
                                f"NIT {numero} con DV {fila.get('dv')}", None)

            ent = entidades.setdefault(llave, {
                "tipo": tipo, "numero": numero, "dv": texto(fila.get("dv")),
                "nombres": [], "codigos": [], "fila": fila,
            })
            ent["nombres"].append((fecha(fila.get("fecha_radicacion")),
                                   texto(fila.get("razon_social")) or texto(fila.get("nombre_prestador"))))
            ent["codigos"].append(codigo)
            # La fila más reciente define los atributos canónicos de la entidad.
            actual = fecha(ent["fila"].get("fecha_radicacion"))
            nueva = fecha(fila.get("fecha_radicacion"))
            if nueva and (not actual or nueva > actual):
                ent["fila"] = fila
            registros.append(fila)

        # 2ª pasada: insertar entidades.
        prestador_ids: dict[tuple, int] = {}
        with self.cx.cursor() as cur:
            for llave, ent in entidades.items():
                f = ent["fila"]
                nombres = [n for _, n in ent["nombres"] if n]
                # Se elige el nombre del registro más reciente; si empatan, el más largo.
                elegido = max(
                    (x for x in ent["nombres"] if x[1]),
                    key=lambda x: (x[0] or datetime.min.date(), len(x[1])),
                    default=(None, None),
                )[1] or f"SIN NOMBRE ({ent['numero']})"
                clase = texto(f.get("clpr_nombre"))
                cur.execute(
                    """INSERT INTO reps.prestador
                       (tipo_identificacion, numero_identificacion, digito_verificacion,
                        razon_social, clase_persona, naturaleza_juridica, es_ese,
                        clase_prestador, es_ips, numero_empleados, extraccion_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s) RETURNING id""",
                    (ent["tipo"], ent["numero"], ent["dv"], elegido,
                     texto(f.get("clase_persona")), texto(f.get("naju_nombre")),
                     booleano(f.get("ese")), clase,
                     bool(clase and clase.startswith("Instituciones")), ext_id),
                )
                prestador_ids[llave] = cur.fetchone()[0]

                if len(set(ent["codigos"])) > 1:
                    cur.execute(
                        """INSERT INTO reps.qa_dedup
                           (tipo_identificacion, numero_identificacion, motivo,
                            codigos_fusionados, razones_sociales, razon_social_elegida, criterio)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (ent["tipo"], ent["numero"],
                         "un mismo documento con varios códigos de habilitación "
                         "(el REPS registra un código por territorio)",
                         sorted(set(ent["codigos"])), sorted(set(nombres)), elegido,
                         "razón social del registro con fecha_radicacion más reciente; "
                         "a igualdad de fecha, la cadena más larga"),
                    )

        # 3ª pasada: registros de habilitación.
        registro_ids: dict[str, int] = {}
        with self.cx.cursor() as cur:
            for f in registros:
                codigo = f["codigo_habilitacion"]
                tipo = tipos.get(codigo) or ("NI" if f.get("clase_persona") == "JURIDICO" else "CC")
                pid = prestador_ids.get((tipo, f["nits_nit"]))
                if pid is None:
                    continue
                cur.execute(
                    """INSERT INTO reps.registro_habilitacion
                       (prestador_id, codigo_habilitacion, nombre_prestador, representante_legal,
                        gerente, nivel_atencion, caracter_territorial, habilitado,
                        fecha_radicacion, fecha_vencimiento, fecha_cierre, departamento,
                        municipio, numero_sede_principal, telefono, email, direccion, extraccion_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING id""",
                    (pid, codigo, texto(f.get("nombre_prestador")), texto(f.get("rep_legal")),
                     texto(f.get("gerente")), entero(f.get("nivel")), texto(f.get("caracter")),
                     booleano(f.get("habilitado")), fecha(f.get("fecha_radicacion")),
                     fecha(f.get("fecha_vencimiento")), fecha(f.get("fecha_cierre")),
                     texto(f.get("depa_nombre")), texto(f.get("muni_nombre")),
                     texto(f.get("numero_sede_principal")), texto(f.get("telefono")),
                     texto(f.get("email")), texto(f.get("direccion")), ext_id),
                )
                registro_ids[codigo] = cur.fetchone()[0]

        self.cx.commit()
        self.resumen["prestadores"] = {
            **stats,
            "entidades": len(entidades),
            "registros": len(registro_ids),
            "tipo_documento_derivado": derivados,
        }
        return registro_ids

    # --------------------------------------------------------------- sedes

    def cargar_sedes(self, ruta: Path, ext_id: int, registro_ids: dict) -> dict:
        cols, filas, stats = leer_csv(ruta)
        sede_ids: dict[tuple, int] = {}
        huerfanas = 0
        dias = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
        with self.cx.cursor() as cur:
            for nlinea, f, crudo in filas():
                if f is None:
                    self.incidencia(ext_id, "sede", f"linea {nlinea}", "fila_malformada",
                                    f"campos != {stats['columnas']}", crudo)
                    continue
                # El prestador se ubica por `codigo_prestador`; `codigo_habilitacion`
                # es el código propio de la sede y es el que usan servicios y capacidad.
                codigo_prestador = f.get("codigo_prestador")
                codigo_sede = f.get("codigo_habilitacion") or codigo_prestador
                rid = registro_ids.get(codigo_prestador)
                if rid is None:
                    huerfanas += 1
                    self.incidencia(ext_id, "sede", f"{codigo_prestador}-{f.get('numero_sede')}",
                                    "sede_sin_registro",
                                    "código de prestador ausente del export de prestadores", None)
                    continue
                horarios = {d: texto(f.get(f"horario_{d}")) for d in dias}
                cur.execute(
                    """INSERT INTO reps.sede
                       (registro_id, codigo_habilitacion_sede, numero_sede, nombre, es_principal,
                        departamento, municipio, direccion, barrio, zona, centro_poblado,
                        telefono, email, fecha_apertura, fecha_cierre, habilitada,
                        horarios, extraccion_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (codigo_habilitacion_sede, numero_sede) DO NOTHING
                       RETURNING id""",
                    (rid, codigo_sede, f.get("numero_sede"), texto(f.get("nombre")),
                     booleano(f.get("sede_principal")), texto(f.get("departamento")),
                     texto(f.get("municipio")), texto(f.get("direccion")), texto(f.get("barrio")),
                     texto(f.get("tipo_zona")), texto(f.get("centro_poblado")),
                     texto(f.get("telefono")), texto(f.get("email")),
                     fecha(f.get("fecha_apertura")), fecha(f.get("fecha_cierre")),
                     booleano(f.get("habilitado")), json.dumps(horarios, ensure_ascii=False), ext_id),
                )
                row = cur.fetchone()
                if row:
                    sede_ids[(codigo_sede, f.get("numero_sede"))] = row[0]
                else:
                    self.incidencia(ext_id, "sede", f"{codigo_sede}-{f.get('numero_sede')}",
                                    "sede_duplicada",
                                    "par (codigo_habilitacion_sede, numero_sede) repetido", None)
        self.cx.commit()
        self.resumen["sedes"] = {**stats, "insertadas": len(sede_ids), "huerfanas": huerfanas}
        return sede_ids

    # ---------------------------------------------------------- servicios

    def cargar_servicios(self, ruta: Path, ext_id: int, sede_ids: dict) -> None:
        cols, filas, stats = leer_csv(ruta)
        mods = [c for c in cols if c.lower().startswith("modalidad")]
        espec = [c for c in cols if c.lower().startswith("especificidad")]
        huerfanos = 0
        insertados = 0
        with self.cx.cursor() as cur, cur.copy(
            """COPY reps.sede_servicio
               (sede_id, grupo_codigo, grupo_nombre, servicio_codigo, servicio_nombre,
                complejidad_baja, complejidad_media, complejidad_alta, ambulatorio,
                hospitalario, unidad_movil, domiciliario, centro_referencia,
                institucion_remisora, modalidades, especificidades, numero_distintivo,
                fecha_apertura, fecha_cierre, version_norma, extraccion_id) FROM STDIN"""
        ) as cp:
            for nlinea, f, crudo in filas():
                if f is None:
                    self.incidencia(ext_id, "sede_servicio", f"linea {nlinea}",
                                    "fila_malformada", f"campos != {stats['columnas']}", crudo)
                    continue
                clave = (f.get("codigo_habilitacion"), f.get("numero_sede"))
                sid = sede_ids.get(clave)
                if sid is None:
                    huerfanos += 1
                    if huerfanos <= 500:
                        self.incidencia(ext_id, "sede_servicio", f"{clave[0]}-{clave[1]}",
                                        "servicio_sin_sede",
                                        "la sede no está en el export de sedes", None)
                    continue
                cp.write_row((
                    sid, texto(f.get("grse_codigo")), texto(f.get("grse_nombre")),
                    texto(f.get("serv_codigo")), texto(f.get("serv_nombre")),
                    booleano(f.get("complejidad_baja")), booleano(f.get("complejidad_media")),
                    booleano(f.get("complejidad_alta")), booleano(f.get("ambulatorio")),
                    booleano(f.get("hospitalario")), booleano(f.get("unidad_movil")),
                    booleano(f.get("domiciliario")), booleano(f.get("centro_referencia")),
                    booleano(f.get("institucion_remisora")),
                    json.dumps({c: f.get(c) for c in mods if texto(f.get(c))}, ensure_ascii=False),
                    json.dumps({c: f.get(c) for c in espec if texto(f.get(c))}, ensure_ascii=False),
                    texto(f.get("numero_distintivo")), fecha(f.get("fecha_apertura")),
                    fecha(f.get("fecha_cierre")), texto(f.get("version_norma")), ext_id,
                ))
                insertados += 1
        self.cx.commit()
        self.resumen["servicios"] = {**stats, "insertados": insertados, "huerfanos": huerfanos}

    # ---------------------------------------------------------- capacidad

    def cargar_capacidad(self, ruta: Path, ext_id: int, sede_ids: dict) -> None:
        cols, filas, stats = leer_csv(ruta)
        huerfanos = insertados = 0
        with self.cx.cursor() as cur, cur.copy(
            """COPY reps.sede_capacidad
               (sede_id, grupo_capacidad, concepto_codigo, concepto_nombre, cantidad,
                placa, modalidad, modelo, numero_tarjeta, extraccion_id) FROM STDIN"""
        ) as cp:
            for nlinea, f, crudo in filas():
                if f is None:
                    self.incidencia(ext_id, "sede_capacidad", f"linea {nlinea}",
                                    "fila_malformada", f"campos != {stats['columnas']}", crudo)
                    continue
                clave = (f.get("codigo_habilitacion"), f.get("numero_sede"))
                sid = sede_ids.get(clave)
                if sid is None:
                    huerfanos += 1
                    if huerfanos <= 500:
                        self.incidencia(ext_id, "sede_capacidad", f"{clave[0]}-{clave[1]}",
                                        "capacidad_sin_sede",
                                        "la sede no está en el export de sedes", None)
                    continue
                cp.write_row((
                    sid, texto(f.get("grupo_capacidad")), texto(f.get("coca_codigo")),
                    texto(f.get("coca_nombre")), entero(f.get("cantidad")),
                    texto(f.get("numero_placa")), texto(f.get("modalidad")),
                    texto(f.get("modelo")), texto(f.get("numero_tarjeta")), ext_id,
                ))
                insertados += 1
        self.cx.commit()
        self.resumen["capacidad"] = {**stats, "insertados": insertados, "huerfanos": huerfanos}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--schema", default="src/schema.sql")
    args = ap.parse_args()
    raw = Path(args.raw)

    print("· aplicando esquema…", flush=True)
    with psycopg.connect(args.dsn, autocommit=True) as cx:
        cx.execute(Path(args.schema).read_text())

    c = Cargador(args.dsn, raw)
    man = json.loads(sorted(raw.glob("manifiesto_reps_*.json"))[-1].read_text())
    por_clave = {x["clave"]: x for x in man["consultas"]}

    ids = {}
    for clave in ("prestadores", "sedes", "servicios", "capacidad"):
        m = por_clave[clave]
        ruta = Path(m["archivo"])
        ids[clave] = c.registrar_extraccion(
            "reps", clave, m["url"], man["extraido_en"], m["fecha_corte_declarada"],
            str(ruta), m["sha256"], m["tamano_bytes"], None, detectar_separador(ruta),
            "consulta pública del REPS; universo nacional sin filtros",
        )
    c.cx.commit()

    print("· prestadores…", flush=True)
    registro_ids = c.cargar_prestadores(Path(por_clave["prestadores"]["archivo"]), ids["prestadores"])
    print(f"  {c.resumen['prestadores']}", flush=True)

    print("· sedes…", flush=True)
    sede_ids = c.cargar_sedes(Path(por_clave["sedes"]["archivo"]), ids["sedes"], registro_ids)
    print(f"  {c.resumen['sedes']}", flush=True)

    print("· servicios…", flush=True)
    c.cargar_servicios(Path(por_clave["servicios"]["archivo"]), ids["servicios"], sede_ids)
    print(f"  {c.resumen['servicios']}", flush=True)

    print("· capacidad…", flush=True)
    c.cargar_capacidad(Path(por_clave["capacidad"]["archivo"]), ids["capacidad"], sede_ids)
    print(f"  {c.resumen['capacidad']}", flush=True)

    c.volcar_incidencias()
    with c.cx.cursor() as cur:
        cur.execute("UPDATE reps.extraccion e SET filas_crudas = s.n FROM ("
                    "  SELECT %s::int id, %s::int n UNION ALL SELECT %s,%s "
                    "  UNION ALL SELECT %s,%s UNION ALL SELECT %s,%s) s WHERE e.id = s.id",
                    (ids["prestadores"], c.resumen["prestadores"]["filas"],
                     ids["sedes"], c.resumen["sedes"]["filas"],
                     ids["servicios"], c.resumen["servicios"]["filas"],
                     ids["capacidad"], c.resumen["capacidad"]["filas"]))
    c.cx.commit()

    (raw.parent / "resumen_carga.json").write_text(
        json.dumps({"cargado_en": datetime.now(timezone.utc).isoformat(), **c.resumen},
                   indent=2, ensure_ascii=False))
    c.cerrar()
    print("\n REPS cargado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
