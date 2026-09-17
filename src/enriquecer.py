"""
Motor de enriquecimiento de contactos de Ariad.

Uso:
    python src/enriquecer.py --dsn "$DSN" --prioridad alta
    python src/enriquecer.py --dsn "$DSN" --todas --limite 500
    python src/enriquecer.py --dsn "$DSN" --prioridad alta --simular

Cruza cuatro fuentes colombianas, todas gratuitas y oficiales:

    reps               el registro de habilitación, ya en la base
    rues               datos.gov.co c82u-588k · 9,4 M de matrículas mercantiles
    secop_proveedores  datos.gov.co qmzu-gj57 · quién vende al Estado
    secop_contratos    datos.gov.co jbjy-vk9h · ordenador del gasto y supervisor

Por qué no hay fuentes pagas
---------------------------
Apollo, Hunter, Lusha y compañía se probaron y no tienen datos de IPS
colombianas, ni de las más grandes. Su cobertura se construye sobre LinkedIn y
bases anglosajonas, donde el tejido de prestadores colombianos no figura. Las
cuatro fuentes de arriba cubren más, y son oficiales.

Qué NO hace, a propósito
------------------------
No marca un correo como «verificado» por haber comprobado su sintaxis ni su
registro MX. MX prueba que el dominio recibe correo, no que el buzón exista.
Sondear el buzón por SMTP sí lo probaría, pero hace que los servidores marquen
la IP que sondea, y el precio de eso lo paga el dominio desde el que después se
escribe. Un correo sólo pasa a «verificado» si dos fuentes independientes lo
traen igual.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

import psycopg

SOCRATA = "https://www.datos.gov.co/resource/%s.json?%s"
RUES = "c82u-588k"
SECOP_PROV = "qmzu-gj57"
SECOP_CONT = "jbjy-vk9h"

# Socrata sin token es estricto. 50 NIT por petición mantiene la URL por debajo
# del límite de longitud y baja 10.652 cuentas a ~213 llamadas por fuente.
LOTE = 50
PAUSA = 0.4

BUZONES_DE_AREA = (
    "contratacion", "contratos", "gerencia", "info", "contacto", "notificacion",
    "notificaciones", "facturacion", "cartera", "juridica", "juridico",
    "cotizaciones", "ventas", "comercial", "administracion", "recepcion",
    "atencionalusuario", "servicioalcliente", "calidad", "talentohumano",
    "gestionhumana", "rrhh", "sistemas", "compras", "tesoreria", "direccion",
    "secretaria", "correspondencia", "pqrs", "citas", "admisiones",
)

DOMINIOS_PERSONALES = (
    "gmail.com", "hotmail.com", "hotmail.es", "outlook.com", "outlook.es",
    "yahoo.com", "yahoo.es", "live.com", "icloud.com", "protonmail.com",
)

# El tier ordena el esfuerzo; NO filtra. Toda persona encontrada se guarda,
# tenga o no un cargo de esta tabla: un cargo que hoy parece irrelevante es la
# puerta de entrada de mañana, y el brief pide poder elegir sobre la lista
# completa.
CARGOS = [
    (1, "representante_legal", r"REPRESENTANTE\s+LEGAL"),
    (1, "gerente_general",     r"\bGERENTE\s+(GENERAL|DE\s+LA\s+ESE)\b|\bGERENTE\b(?!\s+(ADMIN|FINAN|COMERC|TALENT|CALID))"),
    (1, "director_general",    r"DIRECTOR(A)?\s+(GENERAL|EJECUTIV)"),
    (1, "presidente",          r"\bPRESIDENTE\b"),
    (1, "propietario",         r"PROPIETARI|DUE[ÑN]O|SOCIO\s+GESTOR"),
    (2, "gerente_financiero",  r"(GERENTE|DIRECTOR(A)?|JEFE)\s+(ADMINISTRATIV|FINANCIER)"),
    (2, "ordenador_del_gasto", r"ORDENADOR(A)?\s+DEL\s+GASTO"),
    (2, "subgerente",          r"SUBGERENTE|SUBDIRECTOR"),
    (3, "sst",                 r"\bSG[\s-]?SST\b|SEGURIDAD\s+Y\s+SALUD\s+EN\s+EL\s+TRABAJO|\bSST\b"),
    (3, "hseq",                r"\bHSEQ\b|\bHSE\b"),
    (3, "calidad",             r"(JEFE|COORDINADOR(A)?|DIRECTOR(A)?)\s+DE\s+CALIDAD|GESTI[OÓ]N\s+DE\s+CALIDAD"),
    (3, "supervisor_contrato", r"SUPERVISOR"),
    (3, "director_medico",     r"DIRECTOR(A)?\s+(M[EÉ]DIC|CIENT[IÍ]FIC)|GERENTE\s+M[EÉ]DIC"),
    (4, "juridica",            r"JUR[IÍ]DIC"),
    (4, "talento_humano",      r"TALENTO\s+HUMANO|GESTI[OÓ]N\s+HUMANA|RECURSOS\s+HUMANOS"),
]

# 🔴 EL `\b` FINAL ERA EL FALLO. «FUNDACI\b» NO casa con «FUNDACIÓN»: la Ó es
# letra, así que ahí no hay frontera de palabra. Ocho razones sociales entraron
# como personas por eso —«FUNDACIÓN MUJER Y FUTURO», «FUNDACION AMOR Y VIDA»—
# y el defecto estaba desde el primer día, invisible porque las formas sin
# tilde sí casaban.
#
# Las raíces van sin frontera final (son prefijos a propósito) y las siglas la
# conservan, para que «IPS» no case dentro de otra palabra.
SUFIJOS_EMPRESA = re.compile(
    r"(\b(S\.?A\.?S?|LTDA|E\.?S\.?E|E\.?U|IPS|EPS|S\.?C\.?A)\b"
    r"|\b(SOCIEDAD|FUNDACI|CORPORACI|ASOCIACI|COOPERATIV|HOSPITAL|CLINIC|"
    r"CL[IÍ]NIC|CENTRO|INSTITUT|UNIDAD|LABORATORI|EMPRESA|CAJA|SUCURSAL|"
    r"HOGAR|CASA\s+DE|UNIVERSIDAD|COLEGIO|ESCUELA|COMITE|COMIT[EÉ]|"
    r"CONSORCIO|UNION\s+TEMPORAL|MISION|MISI[OÓ]N|ONG))", re.I)


# ── Utilidades ───────────────────────────────────────────────────────────────

def sin_tildes(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()


def clave_nombre(s: str) -> str:
    """
    Tokens en minúscula, sin tildes y ORDENADOS alfabéticamente.

    RUES escribe «GOMEZ DIAZ CARMEN IRENE» y REPS «CARMEN IRENE GOMEZ DIAZ».
    Sin ordenar, son dos personas distintas y la base se llena de duplicados
    que además nunca llegan a «verificado», porque el cruce entre fuentes es
    justamente lo que da esa confianza.
    """
    toks = re.findall(r"[a-z]{2,}", sin_tildes(s or "").lower())
    return " ".join(sorted(toks))


def parece_persona(nombre: str) -> bool:
    """
    SECOP mete la razón social en el campo del representante legal con
    frecuencia («JUNICAL MEDICAL SAS JUNICA»). Guardar eso como persona
    ensucia la lista que luego hay que mirar a ojo para elegir a quién llamar.
    """
    if not nombre:
        return False
    limpio = nombre.strip()
    if len(limpio) < 6 or SUFIJOS_EMPRESA.search(limpio):
        return False
    toks = re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{2,}", limpio)
    return 2 <= len(toks) <= 6


def clasificar(cargo: str):
    """Devuelve (tier, categoria). None si el cargo no encaja: se guarda igual."""
    t = sin_tildes(cargo or "").upper()
    for tier, cat, patron in CARGOS:
        if re.search(patron, t):
            return tier, cat
    return None, None


def norm_valor(tipo: str, v: str) -> str:
    v = (v or "").strip().lower()
    if tipo == "correo":
        return v
    if tipo in ("telefono", "whatsapp"):
        d = re.sub(r"\D", "", v)
        return d[-10:] if len(d) >= 10 else d
    return v.rstrip("/")


def ambito_correo(correo: str) -> str:
    correo = (correo or "").lower()
    if "@" not in correo:
        return "area"
    local, dom = correo.split("@", 1)
    if dom in DOMINIOS_PERSONALES:
        return "personal"
    base = re.sub(r"[^a-z]", "", local)
    if any(base.startswith(b) or base == b for b in BUZONES_DE_AREA):
        return "area"
    # nombre.apellido@dominio, japellido@dominio → nominal
    if re.match(r"^[a-z]+[._-][a-z]+$", local) or re.match(r"^[a-z]{1,2}[._-]?[a-z]{4,}$", local):
        return "profesional"
    return "area"


_mx_cache: dict[str, bool] = {}


def dominio_recibe_correo(dom: str) -> bool:
    """
    ¿El dominio tiene MX? Prueba que ALGUIEN recibe correo ahí, no que el buzón
    exista. Se usa sólo para subir la confianza, nunca para marcar «verificado».
    """
    dom = (dom or "").lower().strip()
    if not dom or "." not in dom:
        return False
    if dom in _mx_cache:
        return _mx_cache[dom]
    try:
        r = subprocess.run(["dig", "+short", "+time=3", "+tries=1", "MX", dom],
                           capture_output=True, text=True, timeout=8)
        ok = bool(r.stdout.strip())
    except Exception:
        ok = False
    _mx_cache[dom] = ok
    return ok


def socrata(dataset: str, params: dict, reintentos: int = 3):
    q = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = SOCRATA % (dataset, q)
    for intento in range(reintentos):
        try:
            with urllib.request.urlopen(url, timeout=60) as fh:
                return json.load(fh)
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and intento < reintentos - 1:
                time.sleep(2 ** intento * 2)
                continue
            raise
        except Exception:
            if intento < reintentos - 1:
                time.sleep(2)
                continue
            raise
    return []


def en_lotes(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def clausula_in(campo: str, valores) -> str:
    return "%s in (%s)" % (campo, ", ".join("'%s'" % v.replace("'", "") for v in valores))


# ── Acumulador ───────────────────────────────────────────────────────────────

class Empresa:
    """
    Lo que sabemos de una empresa mientras se la enriquece.

    Las personas se acumulan por `clave_nombre`, así que la misma persona vista
    en REPS, RUES y SECOP es UNA entrada con tres evidencias — que es
    exactamente lo que después permite marcarla «verificado».
    """

    def __init__(self, pid: int, nit: str, razon: str):
        self.pid, self.nit, self.razon = pid, nit, razon
        self.personas: dict[str, dict] = {}
        self.canales: dict[tuple, dict] = {}
        self.obsoleta = False          # RUES dice matrícula cancelada
        self.dominio: str | None = None

    def persona(self, nombre, cargo, fuente, referencia, crudo, documento=None):
        if not parece_persona(nombre):
            return None
        k = clave_nombre(nombre)
        if not k:
            return None
        p = self.personas.get(k)
        if p is None:
            p = {"nombre": nombre.strip(), "clave": k, "cargos": [], "documento": None,
                 "fuentes": set(), "evidencias": []}
            self.personas[k] = p
        # Se queda la grafía más legible: la que tiene minúsculas suele venir de
        # un formulario escrito a mano, no de un volcado en mayúscula fija.
        if nombre.strip() != nombre.strip().upper() and p["nombre"] == p["nombre"].upper():
            p["nombre"] = nombre.strip()
        if cargo and cargo not in p["cargos"]:
            p["cargos"].append(cargo)
        if documento and not p["documento"]:
            p["documento"] = re.sub(r"\D", "", str(documento))
        p["fuentes"].add(fuente)
        p["evidencias"].append((fuente, referencia, crudo))
        return p

    def canal(self, tipo, valor, fuente, referencia, crudo, persona=None, ambito=None):
        if not valor:
            return
        valor = str(valor).strip()
        if tipo == "correo":
            if "@" not in valor or " " in valor:
                return
            valor = valor.lower()
        if tipo in ("telefono", "whatsapp") and len(re.sub(r"\D", "", valor)) < 7:
            return
        vn = norm_valor(tipo, valor)
        if not vn:
            return
        k = (tipo, vn)
        c = self.canales.get(k)
        if c is None:
            c = {"tipo": tipo, "valor": valor, "norm": vn, "persona": persona,
                 "ambito": ambito or (ambito_correo(valor) if tipo == "correo" else "area"),
                 "fuentes": set(), "evidencias": []}
            self.canales[k] = c
        if persona and not c["persona"]:
            c["persona"] = persona
        c["fuentes"].add(fuente)
        c["evidencias"].append((fuente, referencia, crudo))


def confianza_persona(p: dict, obsoleta: bool):
    """
    Dos fuentes oficiales independientes que coinciden en el nombre es la señal
    más fuerte que hay sin llamar por teléfono. Una sola fuente es un indicio.

    Medido sobre 12 cuentas de prioridad alta: 11 coincidencias entre REPS y
    RUES, 1 discrepancia. La discrepancia no es ruido — suele ser rotación, y el
    dato más nuevo gana; por eso RUES, que trae año de renovación, pesa.
    """
    n = len(p["fuentes"])
    base = 45 if n == 1 else 75 if n == 2 else 88
    if p["documento"]:
        base += 7
    if p["cargos"]:
        base += 3
    if obsoleta:
        return "obsoleto", min(base, 40)
    estado = "verificado" if n >= 2 else "inferido"
    return estado, min(base, 97)


def confianza_canal(c: dict) -> tuple:
    """
    Un correo ADIVINADO no vale lo que uno OBSERVADO, aunque los dos tengan una
    sola fuente y el dominio resuelva.

    Esto empezó mal: la primera versión daba 60 a un candidato de patrón, la
    misma nota que a un correo publicado en SECOP. Con eso, «96 % de empresas
    con correo nominal» resultaba ser casi todo conjeturas, y esa cifra es justo
    la que alguien miraría para decidir si la lista sirve para escribir.

    Un patrón que además aparece en otra fuente sí sube, y mucho: ahí la
    conjetura dejó de serlo porque algo independiente la confirmó.
    """
    fuentes = c["fuentes"]
    n = len(fuentes)
    solo_patron = fuentes == {"patron"}

    if solo_patron:
        base = 25          # es una hipótesis, y así se reporta
    else:
        base = 40 if n == 1 else 70

    if c["tipo"] == "correo":
        dom = c["valor"].split("@")[-1]
        if dominio_recibe_correo(dom):
            base += 10
        else:
            base -= 20      # dominio sin MX: ahí no llega nada
        if c["ambito"] == "profesional" and not solo_patron:
            base += 5

    # `verificado` exige dos fuentes independientes. El MX sube la confianza pero
    # no prueba que el buzón exista, así que por sí solo nunca cambia el estado.
    estado = "verificado" if n >= 2 else "inferido"
    return estado, max(0, min(base, 95))


# ── Fuentes ──────────────────────────────────────────────────────────────────

def desde_reps(cx, empresas: dict):
    """El registro de habilitación, que ya está en la base. Gratis y al 99,9 %."""
    with cx.cursor() as cur:
        cur.execute("""
            SELECT p.id, max(r.representante_legal), max(r.email), max(r.telefono)
            FROM reps.prestador p
            JOIN reps.registro_habilitacion r ON r.prestador_id = p.id
            WHERE p.id = ANY(%s)
            GROUP BY p.id
        """, (list(empresas.keys()),))
        for pid, rep, mail, tel in cur.fetchall():
            e = empresas[pid]
            crudo = {"representante_legal": rep, "email": mail, "telefono": tel}
            per = e.persona(rep, "Representante Legal", "reps",
                            "reps.registro_habilitacion", crudo)
            e.canal("correo", mail, "reps", "reps.registro_habilitacion", crudo)
            e.canal("telefono", tel, "reps", "reps.registro_habilitacion", crudo)
            if mail and "@" in mail:
                dom = mail.split("@")[-1].lower()
                if dom not in DOMINIOS_PERSONALES:
                    e.dominio = dom


def desde_rues(empresas: dict, por_nit: dict, stats):
    """
    RUES por NIT. Puede haber varias matrículas por NIT (sedes, renovaciones
    viejas); se toma la más recientemente renovada, y si la elegida está
    cancelada se marca la empresa como obsoleta.
    """
    nits = list(por_nit.keys())
    for lote in en_lotes(nits, LOTE):
        filas = socrata(RUES, {
            "$select": "nit,razon_social,representante_legal,"
                       "num_identificacion_representante_legal,estado_matricula,"
                       "ultimo_ano_renovado,camara_comercio",
            "$where": clausula_in("nit", lote),
            "$limit": 5000,
        })
        stats["peticiones"] += 1
        mejor: dict[str, dict] = {}
        for f in filas:
            nit = str(f.get("nit") or "")
            anio = int(f.get("ultimo_ano_renovado") or 0)
            activa = (f.get("estado_matricula") or "").upper() == "ACTIVA"
            # Una activa siempre gana a una que no lo esté, aunque sea más vieja.
            puntaje = (1 if activa else 0, anio)
            if nit not in mejor or puntaje > mejor[nit]["_p"]:
                f["_p"] = puntaje
                mejor[nit] = f
        for nit, f in mejor.items():
            pid = por_nit.get(nit)
            if pid is None:
                continue
            e = empresas[pid]
            estado = (f.get("estado_matricula") or "").upper()
            if estado in ("CANCELADA", "NO MATRICULADO"):
                e.obsoleta = True
            e.persona(f.get("representante_legal"), "Representante Legal", "rues",
                      "datos.gov.co/%s nit=%s" % (RUES, nit), f,
                      documento=f.get("num_identificacion_representante_legal"))
        time.sleep(PAUSA)


def desde_secop_proveedores(empresas: dict, por_nit: dict, stats):
    """Quien vende al Estado deja correo y teléfono de contacto, y a veces web."""
    for lote in en_lotes(list(por_nit.keys()), LOTE):
        filas = socrata(SECOP_PROV, {
            "$select": "nit,nombre,nombre_representante_legal,correo_representante_legal,"
                       "telefono_representante_legal,correo,telefono,sitio_web,esta_activa",
            "$where": clausula_in("nit", lote),
            "$limit": 5000,
        })
        stats["peticiones"] += 1
        for f in filas:
            pid = por_nit.get(str(f.get("nit") or ""))
            if pid is None:
                continue
            e = empresas[pid]
            ref = "datos.gov.co/%s nit=%s" % (SECOP_PROV, f.get("nit"))
            per = e.persona(f.get("nombre_representante_legal"), "Representante Legal",
                            "secop_proveedores", ref, f)
            # El correo del RL en SECOP es casi siempre un buzón de área
            # (contratacion@, gerencia@). Se ata a la persona sólo si su forma
            # dice que es nominal; si no, queda como canal de la empresa.
            crl = f.get("correo_representante_legal")
            amb = ambito_correo(crl or "")
            e.canal("correo", crl, "secop_proveedores", ref, f,
                    persona=per if amb == "profesional" else None, ambito=amb)
            e.canal("telefono", f.get("telefono_representante_legal"),
                    "secop_proveedores", ref, f, persona=per)
            e.canal("correo", f.get("correo"), "secop_proveedores", ref, f)
            e.canal("telefono", f.get("telefono"), "secop_proveedores", ref, f)
            web = (f.get("sitio_web") or "").strip()
            if web and web.upper() not in ("NO PROVISTO", "NO APLICA", "N/A"):
                e.canal("web", web, "secop_proveedores", ref, f)
        time.sleep(PAUSA)


def desde_secop_contratos(empresas: dict, por_nit: dict, stats):
    """
    Aquí aparecen los Tier 2 y 3 de las entidades públicas: el ordenador del
    gasto y el supervisor del contrato son funcionarios de la propia entidad.

    Medido sobre 2.000 contratos de 2026: ordenador del gasto viene en el 85,8 %
    y supervisor en el 78,8 %. Pero NO está repartido parejo — hay entidades que
    los dejan todos en «No definido», así que la cobertura real por empresa
    varía y el informe la reporta tal cual.
    """
    for lote in en_lotes(list(por_nit.keys()), LOTE):
        filas = socrata(SECOP_CONT, {
            "$select": "nit_entidad,nombre_entidad,nombre_ordenador_del_gasto,"
                       "nombre_supervisor,fecha_de_firma,id_contrato,urlproceso",
            "$where": clausula_in("nit_entidad", lote) +
                      " AND fecha_de_firma > '2024-01-01'",
            "$order": "fecha_de_firma DESC",
            "$limit": 5000,
        })
        stats["peticiones"] += 1
        vistos = defaultdict(set)
        for f in filas:
            pid = por_nit.get(str(f.get("nit_entidad") or ""))
            if pid is None:
                continue
            e = empresas[pid]
            ref = "datos.gov.co/%s contrato=%s" % (SECOP_CONT, f.get("id_contrato"))
            for campo, cargo in (("nombre_ordenador_del_gasto", "Ordenador del Gasto"),
                                 ("nombre_supervisor", "Supervisor de contrato")):
                v = (f.get(campo) or "").strip()
                if not v or v.lower() in ("no definido", "no provisto", "no aplica"):
                    continue
                k = clave_nombre(v)
                # Un mismo funcionario firma decenas de contratos. Una evidencia
                # por persona basta; guardar 40 sólo infla la tabla.
                if k in vistos[pid]:
                    continue
                vistos[pid].add(k)
                e.persona(v, cargo, "secop_contratos", ref, f)
        time.sleep(PAUSA)


def por_patron(empresas: dict):
    """
    Con dominio propio y nombre completo se generan candidatos de correo.

    Quedan SIEMPRE como «inferido», nunca «verificado»: sin sondear el buzón no
    hay forma de saber si existe, y sondearlo quema la reputación de la IP. Se
    marcan con confianza baja para que una secuencia de outreach pueda
    excluirlos o tratarlos aparte.
    """
    for e in empresas.values():
        if not e.dominio or not dominio_recibe_correo(e.dominio):
            continue
        for p in list(e.personas.values()):
            # Sólo para quien decide o controla presupuesto. Generar tres
            # candidatos para cada persona encontrada llenaba la tabla de
            # hipótesis sobre gente a la que nadie va a escribir: 684 de 907
            # canales en la primera prueba, ahogando a los correos reales.
            tier, _ = clasificar("; ".join(p["cargos"]))
            if tier not in (1, 2):
                continue
            toks = re.findall(r"[a-z]+", sin_tildes(p["nombre"]).lower())
            if len(toks) < 2:
                continue
            nombre, apellido = toks[0], toks[-1] if len(toks) == 2 else toks[-2]
            for cand in ("%s.%s" % (nombre, apellido),
                         "%s%s" % (nombre[0], apellido),
                         "%s" % nombre):
                e.canal("correo", "%s@%s" % (cand, e.dominio), "patron",
                        "patron sobre %s" % e.dominio,
                        {"patron": cand, "dominio": e.dominio},
                        persona=p, ambito="profesional")


# ── Guardado ─────────────────────────────────────────────────────────────────

def guardar(cx, empresas: dict, stats):
    """
    Escribe personas, canales y evidencia.

    Es idempotente por (prestador_id, nombre_clave) y por (prestador_id, tipo,
    valor): volver a correrlo actualiza en vez de duplicar, que es lo que hace
    posible la re-verificación periódica sin limpiar antes.
    """
    nuevas = nuevos_canales = 0
    with cx.cursor() as cur:
        # Exclusiones primero: si alguien pidió no ser contactado, su dato no
        # vuelve a entrar por mucho que la fuente lo siga publicando.
        cur.execute("SELECT coalesce(nombre_clave,''), coalesce(valor_norm,'') "
                    "FROM enriquecimiento.exclusion")
        excl_nombres, excl_valores = set(), set()
        for nk, vn in cur.fetchall():
            if nk:
                excl_nombres.add(nk)
            if vn:
                excl_valores.add(vn)

        for e in empresas.values():
            ids_persona = {}
            for k, p in e.personas.items():
                if k in excl_nombres:
                    continue
                cargo = "; ".join(p["cargos"]) if p["cargos"] else None
                tier, cat = clasificar(cargo or "")
                estado, conf = confianza_persona(p, e.obsoleta)
                cur.execute("""
                    INSERT INTO enriquecimiento.persona
                      (prestador_id, nombre_clave, nombre, documento, cargo,
                       cargo_categoria, tier, estado, confianza, ultima_verificacion)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                    ON CONFLICT (prestador_id, nombre_clave) DO UPDATE SET
                      nombre = excluded.nombre,
                      documento = coalesce(enriquecimiento.persona.documento, excluded.documento),
                      cargo = excluded.cargo,
                      cargo_categoria = excluded.cargo_categoria,
                      tier = excluded.tier,
                      estado = excluded.estado,
                      confianza = excluded.confianza,
                      ultima_verificacion = now()
                    RETURNING id, (xmax = 0) AS insertada
                """, (e.pid, k, p["nombre"], p["documento"], cargo, cat, tier, estado, conf))
                pid_persona, insertada = cur.fetchone()
                ids_persona[k] = pid_persona
                if insertada:
                    nuevas += 1
                for fuente, ref, crudo in p["evidencias"]:
                    cur.execute("""
                        INSERT INTO enriquecimiento.evidencia
                          (persona_id, fuente, referencia, crudo)
                        VALUES (%s,%s,%s,%s)
                    """, (pid_persona, fuente, ref, json.dumps(crudo, default=str)))

            for (tipo, vn), c in e.canales.items():
                if vn in excl_valores:
                    continue
                estado, conf = confianza_canal(c)
                per = c["persona"]
                per_id = ids_persona.get(per["clave"]) if per else None
                cur.execute("""
                    INSERT INTO enriquecimiento.canal
                      (persona_id, prestador_id, tipo, valor, valor_norm, ambito,
                       estado, confianza, ultima_verificacion)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now())
                    ON CONFLICT (prestador_id, tipo, valor_norm) DO UPDATE SET
                      persona_id = coalesce(enriquecimiento.canal.persona_id, excluded.persona_id),
                      ambito = excluded.ambito,
                      estado = excluded.estado,
                      confianza = excluded.confianza,
                      ultima_verificacion = now()
                    RETURNING id, (xmax = 0) AS insertado
                """, (per_id, e.pid, tipo, c["valor"], vn, c["ambito"], estado, conf))
                cid, insertado = cur.fetchone()
                if insertado:
                    nuevos_canales += 1
                for fuente, ref, crudo in c["evidencias"]:
                    cur.execute("""
                        INSERT INTO enriquecimiento.evidencia
                          (canal_id, fuente, referencia, crudo)
                        VALUES (%s,%s,%s,%s)
                    """, (cid, fuente, ref, json.dumps(crudo, default=str)))
    cx.commit()
    stats["personas_nuevas"] = nuevas
    stats["canales_nuevos"] = nuevos_canales


# ── Informe ──────────────────────────────────────────────────────────────────

def informe(cx, ids):
    with cx.cursor() as cur:
        cur.execute("""
            WITH e AS (SELECT unnest(%s::bigint[]) AS pid),
            p AS (
              SELECT e.pid,
                     count(pe.id) AS personas,
                     count(*) FILTER (WHERE pe.tier = 1) AS t1,
                     count(*) FILTER (WHERE pe.tier = 1 AND pe.estado='verificado') AS t1v,
                     count(*) FILTER (WHERE pe.tier BETWEEN 2 AND 4) AS t234,
                     count(*) FILTER (WHERE pe.estado='obsoleto') AS obs
              FROM e LEFT JOIN enriquecimiento.persona pe ON pe.prestador_id = e.pid
              GROUP BY e.pid),
            c AS (
              -- `observado` separa lo que una fuente publicó de lo que nosotros
              -- adivinamos por patrón. Contarlos juntos daba «96 de cada 100 con
              -- correo nominal» cuando tres de cada cuatro eran hipótesis.
              -- (Sin símbolo de porcentaje: psycopg lo leería como marcador
              -- de parámetro aunque esté dentro de un comentario SQL.)
              SELECT e.pid,
                     count(*) FILTER (WHERE ca.tipo='correo' AND ca.observado) AS correos,
                     count(*) FILTER (WHERE ca.tipo='correo' AND ca.observado
                                        AND ca.ambito='profesional') AS nominales,
                     count(*) FILTER (WHERE ca.tipo='correo' AND NOT ca.observado) AS adivinados,
                     count(*) FILTER (WHERE ca.tipo='telefono') AS telefonos
              FROM e LEFT JOIN (
                SELECT ca.*, EXISTS (
                  SELECT 1 FROM enriquecimiento.evidencia ev
                  WHERE ev.canal_id = ca.id AND ev.fuente <> 'patron') AS observado
                FROM enriquecimiento.canal ca) ca ON ca.prestador_id = e.pid
              GROUP BY e.pid)
            SELECT count(*) AS empresas,
                   count(*) FILTER (WHERE p.personas > 0) AS con_persona,
                   count(*) FILTER (WHERE p.t1 > 0) AS con_tier1,
                   count(*) FILTER (WHERE p.t1v > 0) AS con_tier1_verificado,
                   count(*) FILTER (WHERE p.personas >= 2) AS con_2_o_mas,
                   count(*) FILTER (WHERE p.t234 > 0) AS con_tier234,
                   count(*) FILTER (WHERE p.personas = 0) AS sin_nadie,
                   count(*) FILTER (WHERE c.correos > 0) AS con_correo,
                   count(*) FILTER (WHERE c.nominales > 0) AS con_correo_nominal,
                   count(*) FILTER (WHERE c.telefonos > 0) AS con_telefono,
                   count(*) FILTER (WHERE c.adivinados > 0) AS con_correo_adivinado,
                   count(*) FILTER (WHERE p.obs > 0) AS con_obsoleto
            FROM p JOIN c USING (pid)
        """, (list(ids),))
        f = cur.fetchone()
    campos = ["empresas", "con al menos 1 persona", "con Tier 1", "con Tier 1 VERIFICADO",
              "con 2 o más personas", "con Tier 2/3/4", "sin nadie",
              "con correo observado", "· de ellos nominal", "con teléfono",
              "con correo SOLO adivinado", "con alguien obsoleto"]
    total = f[0] or 1
    print("\n── Cobertura")
    for nombre, v in zip(campos, f):
        if nombre == "empresas":
            print("   %-24s %6d" % (nombre, v))
        else:
            print("   %-24s %6d   %5.1f %%" % (nombre, v, 100.0 * v / total))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--prioridad", choices=["alta", "media", "baja"])
    ap.add_argument("--todas", action="store_true")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--simular", action="store_true",
                    help="cuenta cuántas empresas y peticiones haría, sin tocar la red")
    args = ap.parse_args()

    cx = psycopg.connect(args.dsn)
    cond = "p.clase_prestador <> 'Profesional Independiente'"
    params = []
    if args.prioridad:
        cond += " AND s.prioridad = %s"
        params.append(args.prioridad)
    elif not args.todas:
        print("Elige --prioridad alta|media|baja o --todas.")
        return 2
    sql = ("SELECT p.id, p.numero_identificacion, p.razon_social "
           "FROM reps.prestador p JOIN reps.score_icp s ON s.prestador_id = p.id "
           "WHERE " + cond + " ORDER BY s.score DESC")
    if args.limite:
        sql += " LIMIT %d" % args.limite
    with cx.cursor() as cur:
        cur.execute(sql, params)
        filas = cur.fetchall()

    empresas = {pid: Empresa(pid, nit, rz) for pid, nit, rz in filas}
    por_nit = {nit: pid for pid, nit, _ in filas}
    lotes = (len(por_nit) + LOTE - 1) // LOTE

    print("── Alcance: %d empresas · %d peticiones estimadas · costo 0,00 USD"
          % (len(empresas), lotes * 3))
    if args.simular:
        print("   (simulación: no se tocó la red ni la base)")
        return 0

    stats = {"peticiones": 0, "personas_nuevas": 0, "canales_nuevos": 0}
    with cx.cursor() as cur:
        cur.execute("""INSERT INTO enriquecimiento.corrida (paso, alcance, empresas)
                       VALUES ('gratis', %s, %s) RETURNING id""",
                    (args.prioridad or "todas", len(empresas)))
        corrida = cur.fetchone()[0]
    cx.commit()

    print("· REPS (local)…");            desde_reps(cx, empresas)
    print("· RUES…");                    desde_rues(empresas, por_nit, stats)
    print("· SECOP proveedores…");       desde_secop_proveedores(empresas, por_nit, stats)
    print("· SECOP contratos…");         desde_secop_contratos(empresas, por_nit, stats)
    print("· patrón de correo…");        por_patron(empresas)
    print("· guardando…");               guardar(cx, empresas, stats)

    with cx.cursor() as cur:
        cur.execute("""UPDATE enriquecimiento.corrida
                       SET personas_nuevas=%s, canales_nuevos=%s, peticiones=%s,
                           terminada=now()
                       WHERE id=%s""",
                    (stats["personas_nuevas"], stats["canales_nuevos"],
                     stats["peticiones"], corrida))
    cx.commit()

    print("\n   personas nuevas: %d · canales nuevos: %d · peticiones: %d · costo: 0,00 USD"
          % (stats["personas_nuevas"], stats["canales_nuevos"], stats["peticiones"]))
    informe(cx, empresas.keys())
    cx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
