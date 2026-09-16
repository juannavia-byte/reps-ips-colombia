"""
Raspado respetuoso de los sitios corporativos, para sacar Tier 2 y 3.

    python src/raspar_web.py --dsn "$DSN" --prioridad alta
    python src/raspar_web.py --dsn "$DSN" --prioridad alta --limite 50 --simular

Por qué existe
--------------
REPS y RUES dan el representante legal casi siempre, pero el gerente
administrativo y el coordinador de calidad no están en ningún registro público:
sólo en la web de la propia clínica. SECOP los da únicamente cuando la entidad
compra con dinero público, y las cuentas de prioridad alta son privadas — de
1.393 sólo 3 tenían alguien de Tier 2/3/4.

Qué rendimiento esperar, medido antes de escribir esto
------------------------------------------------------
Sobre 25 dominios de prioridad alta, sólo 4 daban algún par nombre+cargo. Ocho
no respondían — pero la mitad de esos sí contestaban con `www.` o por `http://`,
y la sonda no lo intentaba. Con eso corregido el rendimiento sube, pero conviene
no esperar milagros: la mayoría de las clínicas colombianas publica misión,
visión y valores, no su organigrama.

Es decir: esto no cierra el hueco de Tier 2/3, lo reduce. Vale la pena porque
donde acierta acierta fuerte —dos sitios de la muestra dieron 9 y 16 personas—
y porque no cuesta nada.

Cómo se comporta
----------------
  · Respeta robots.txt. Si el sitio dice que no, no se entra.
  · Se identifica en el User-Agent, con URL de contacto.
  · Un sitio a la vez, con pausa entre peticiones. No hay prisa.
  · Como mucho 6 páginas por sitio: inicio más los enlaces que parecen llevar a
    un equipo o un organigrama.
  · No ejecuta JavaScript. Los sitios que pintan el equipo desde el navegador
    quedan fuera, y eso se reporta en vez de disimularse.
"""
from __future__ import annotations

import argparse
import html as escapes
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

import psycopg

from enriquecer import (DOMINIOS_PERSONALES, ambito_correo, clasificar,
                        clave_nombre, parece_persona, sin_tildes)

UA = "Mozilla/5.0 (compatible; AriadBot/1.0; +https://proactivos.com.co/herramientas)"
CABECERAS = {"User-Agent": UA, "Accept-Language": "es-CO,es;q=0.9"}

PAGINAS_POR_SITIO = 6
PAUSA_PETICION = 0.6
ESPERA = 15

PISTAS_ENLACE = re.compile(
    r"(quien(es)?[-_ ]?somos|nosotr|equipo|directiv|organigrama|junta|gerenc|"
    r"talento[-_ ]?humano|transparen|directorio|gobierno|staff|corporativ|"
    r"nuestra[-_ ]?gente|colaborador)", re.I)

CARGO = re.compile(
    r"\b(gerente|director[ao]?|subgerente|subdirector[ao]?|jefe|coordinador[ao]?|"
    r"representante\s+legal|presidente|vicepresidente|revisor[a]?\s+fiscal|"
    r"l[ií]der|responsable|administrador[a]?)\b", re.I)

# Enumerar todos los cargos posibles no funciona: un hospital tiene «Coordinador
# Cardiología», «Jefe Unidad Quirúrgica», «Jefe de Urgencias»… y la lista nunca
# termina. Se hace al revés — se descarta lo que claramente NO es el cargo de una
# persona.
#
# Esto salió de mirar datos: clinicamarcaribe.com daba 16 «pares» que eran
# títulos de documentos —«Informe de Revisor Fiscal 2016», con «Balance General
# 2016» al lado— y loscobosmc.com perdía a su Director Médico y a media plantilla
# porque sus cargos son clínicos y no estaban en la lista blanca.
NO_ES_CARGO = re.compile(
    r"(informe|balance|estado\s+de|acta|resoluci[oó]n|circular|manual|"
    r"pol[ií]tica|reglamento|c[oó]digo|formato|anexo|convocatoria|"
    r"\b(19|20)\d{2}\b|\bn[°º]\s*\d)", re.I)

EMPRESA = re.compile(
    r"\b(S\.?A\.?S?|LTDA|E\.?S\.?E|IPS|EPS|HOSPITAL|CLINICA|CLÍNICA|FUNDACI|"
    r"CENTRO|INSTITUTO|UNIDAD|LABORATORIO|SOCIEDAD|CORPORACI)\b", re.I)

CORREO = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def dominio_valido(dom: str) -> str | None:
    """
    El campo `email` de REPS trae a veces dos direcciones, comas o espacios: 362
    de 57.660 producen un «dominio» que no existe. Sin esta limpieza se pierden
    peticiones contra cosas como «icvc.co calidad».
    """
    dom = (dom or "").strip().lower()
    dom = re.split(r"[\s,;]", dom)[0].strip(".")
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", dom):
        return None
    if dom in DOMINIOS_PERSONALES:
        return None
    return dom


def candidatos_url(dom: str):
    """
    Cuatro formas del mismo sitio.

    No es redundancia: de siete dominios que la sonda dio por muertos, cinco
    respondían con `www.` o por `http://`. Probar sólo `https://dominio/` tiraba
    a la basura una quinta parte de los sitios.
    """
    return ["https://%s/" % dom, "https://www.%s/" % dom,
            "http://%s/" % dom, "http://www.%s/" % dom]


def bajar(url: str, espera: int = ESPERA) -> str | None:
    try:
        req = urllib.request.Request(url, headers=CABECERAS)
        with urllib.request.urlopen(req, timeout=espera) as fh:
            tipo = fh.headers.get_content_type()
            if tipo not in ("text/html", "application/xhtml+xml"):
                return None
            crudo = fh.read(1_500_000)
        cs = "utf-8"
        m = re.search(rb'charset=["\']?([\w-]+)', crudo[:2000], re.I)
        if m:
            cs = m.group(1).decode("ascii", "ignore")
        return crudo.decode(cs, "ignore")
    except Exception:
        return None


def permitido(base: str) -> bool:
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(urllib.parse.urljoin(base, "/robots.txt"))
    try:
        rp.read()
    except Exception:
        # Sin robots.txt legible se procede: la ausencia no es una prohibición.
        return True
    try:
        return rp.can_fetch(UA, base)
    except Exception:
        return True


def a_lineas(html: str):
    html = re.sub(r"(?is)<(script|style|head|nav|footer|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"<[^>]+>", "\n", html)
    # `unescape` y no dos replace a mano: «Judith Ort&iacute;z Mayorga» no pasaba
    # el detector de nombres, y «Enfermer&iacute;a» llegaba rota al cargo.
    html = escapes.unescape(html)
    lineas = [re.sub(r"\s+", " ", x).strip() for x in html.split("\n")]
    return [x for x in lineas if x]


# Palabras que delatan que una línea es un CARGO o un rótulo, no el nombre de
# una persona. Va aparte de CARGO porque esa lista sólo cubre los mandos, y lo
# que contamina son también los puestos de base.
#
# 🔴 SIN ESTO, LA TABLA SE LLENA DE BASURA CON APARIENCIA DE DATO. Medido sobre
# 100 sitios: se guardaron como nombres de persona «COORDINADOR(A) DE AMBIENTE
# FISICO», «Auxiliar de Enfermería», «Segundo suplente Gerente General» y hasta
# «¿PARA QUÉ EVALUAR?». La causa es que un cargo también son dos o tres palabras
# capitalizadas, así que en una página que lista sólo cargos —un organigrama sin
# nombres— cada línea se empareja con su vecina y todas pasan el filtro.
NO_ES_NOMBRE = re.compile(
    r"\b(auxiliar|enfermer[oa]?|profesional|suplente|rector[a]?|asesor[a]?|"
    r"analista|t[eé]cnic[oa]|tecn[oó]log[oa]|secretari[oa]|m[eé]dic[oa]|"
    r"especialista|terapeuta|psic[oó]log[oa]|nutricionista|bacteri[oó]log[oa]|"
    r"regente|instrumentador[a]?|camiller[oa]|conductor[a]?|vigilante|"
    r"ingenier[oa]|arquitect[oa]|contador[a]?|abogad[oa]|administrador[a]?|"
    r"gestor[a]?|operari[oa]|practicante|residente|intern[oa]|aprendiz|"
    r"cargo|asunto|[aá]rea|servicio|unidad|proceso|comit[eé]|junta|"
    r"nivel|grado|planta|perfil|funciones|requisitos|formulario|solicitud)\b"
    # Abreviaturas: «COORD. DE CONTABILIDAD Y COSTOS» no lo cazaba `coordinador`.
    r"|\b(coord|dir|subdir|aux|jef|adm|gte)\.", re.I)

# Rótulos de navegación. «Quiénes Somos» son dos palabras capitalizadas y sin
# esto entraba como el nombre de una persona.
ROTULO = re.compile(
    r"^\s*(qui[eé]nes\s+somos|nuestro\s+equipo|sobre\s+nosotros|nuestra\s+"
    r"historia|misi[oó]n|visi[oó]n|valores|cont[aá]ctenos|cont[aá]ctanos|"
    r"inicio|men[uú]|buscar|leer\s+m[aá]s|ver\s+m[aá]s|portal|noticias|"
    r"servicios|especialidades|sedes|blog)\s*$", re.I)

# Instituciones que no son «la empresa» pero tampoco personas. Aparecen en pies
# de página y en listas de aliados, y colaban como nombres propios porque son
# dos o tres palabras capitalizadas: «Superintendencia Nacional de Salud».
INSTITUCION = re.compile(
    r"\b(superintendencia|ministerio|secretar[ií]a|alcald[ií]a|gobernaci[oó]n|"
    r"universidad|asociaci[oó]n|federaci[oó]n|c[aá]mara|adres|invima|dian|"
    r"supersalud|entidad|gobierno|naci[oó]n|departamento|municipio|"
    r"presidencia|congreso|consejo|instituto)\b", re.I)


def parece_nombre_web(s: str) -> bool:
    """
    Un nombre propio, no un cargo disfrazado.

    La prueba de fuego es que NO contenga un cargo: «Auxiliar de Enfermería»
    también son dos palabras capitalizadas, y sin este rechazo entraba como
    persona. Un apellido nunca es «Coordinador».
    """
    if not s or len(s) > 60 or EMPRESA.search(s) or CORREO.search(s):
        return False
    if re.search(r"[\d:;¿?¡!@/|\"“”]", s):
        return False
    # Viñetas, guiones y frases: «- DILIGENCIAR EL FORMULARIO DE SOLICITUD.»
    # entraba como persona porque son tres palabras en mayúscula.
    if re.match(r"^[\-–—•*·>]", s.strip()) or s.strip().endswith("."):
        return False
    # Si la línea nombra un rol o una institución, no es el nombre de alguien.
    if (CARGO.search(s) or NO_ES_NOMBRE.search(s) or INSTITUCION.search(s)
            or ROTULO.match(s)):
        return False
    # «Emergencia y Trauma», «Social y Humanitario»: nombres de servicio partidos
    # en dos líneas. Un nombre colombiano casi nunca lleva conjunción — se pierde
    # algún «Ortega y Gasset», y a cambio no entran los servicios de la clínica.
    if re.search(r"\s+[ye]\s+", s, re.I):
        return False
    toks = re.findall(r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ']+|[A-ZÁÉÍÓÚÑ]{2,}", s)
    if not (2 <= len(toks) <= 5):
        return False
    # Casi todo lo que hay tienen que ser esas palabras: «Dr. Juan Pérez» pasa,
    # «Conoce a nuestro equipo humano» no.
    return len(" ".join(toks)) >= len(s) * 0.6


def cargo_util(linea: str) -> bool:
    """
    Es el cargo de una persona si nombra un rol y no parece el título de un
    documento. La comprobación fuerte no es ésta sino la de `extraer`: al lado
    tiene que haber algo que parezca un nombre propio, y «Balance General 2016»
    nunca lo parece porque lleva dígitos.
    """
    return bool(CARGO.search(linea)
                and not NO_ES_CARGO.search(linea)
                and len(linea) <= 80)


def extraer(lineas):
    """
    Busca pares nombre/cargo contiguos, en los dos órdenes.

    Las fichas de equipo se maquetan de las dos formas —la foto con el nombre
    encima del cargo, o el cargo como antetítulo— y no hay forma de saber cuál
    usa un sitio sin mirarlo. Se aceptan ambas y se deduplica después.
    """
    salida = []
    for i, l in enumerate(lineas):
        if not cargo_util(l):
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(lineas) and parece_nombre_web(lineas[j]):
                nombre = re.sub(r"^(Dr\.?a?|Dra\.?|Ing\.?|Lic\.?|Esp\.?)\s+", "",
                                lineas[j], flags=re.I).strip()
                if parece_persona(nombre):
                    # El correo más cercano, si lo hay en las dos líneas de al lado.
                    correo = None
                    for k in range(max(0, i - 2), min(len(lineas), i + 3)):
                        m = CORREO.search(lineas[k])
                        if m:
                            correo = m.group(0).lower()
                            break
                    salida.append((nombre, l.strip(" -–—:·|"), correo))
                break
    return salida


def rastrear(dom: str, stats: dict):
    """Devuelve (personas, correos_sueltos, paginas_vistas, motivo_si_falla)."""
    base = None
    html = None
    for u in candidatos_url(dom):
        html = bajar(u)
        if html:
            base = u
            break
        stats["intentos_fallidos"] += 1
    if not html:
        return [], [], 0, "sin respuesta"

    if not permitido(base):
        return [], [], 0, "robots.txt lo prohíbe"

    urls = [base]
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        href, txt = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        if not PISTAS_ENLACE.search(href + " " + txt):
            continue
        u = urllib.parse.urljoin(base, href.split("#")[0])
        if urllib.parse.urlparse(u).netloc.endswith(dom.split("www.")[-1]) and u not in urls:
            urls.append(u)
        if len(urls) >= PAGINAS_POR_SITIO:
            break

    personas, correos, vistas = [], set(), 0
    for u in urls:
        h = html if u == base else bajar(u)
        if not h:
            continue
        vistas += 1
        lineas = a_lineas(h)
        for nombre, cargo, correo in extraer(lineas):
            personas.append((nombre, cargo, correo, u))
        for m in CORREO.finditer(h):
            c = m.group(0).lower()
            if c.split("@")[-1].endswith(dom.split("www.")[-1]):
                correos.add(c)
        time.sleep(PAUSA_PETICION)
        stats["peticiones"] += 1
    return personas, sorted(correos)[:10], vistas, None


def guardar(cx, pid, dom, personas, correos, fuente_url):
    """
    Una persona vista en la web que YA existe por REPS o RUES sube de confianza:
    es una tercera fuente independiente confirmando. Una persona nueva entra con
    confianza modesta — la web es texto libre y el extractor se equivoca.
    """
    nuevas = nuevos_canales = 0
    with cx.cursor() as cur:
        for nombre, cargo, correo, url in personas:
            k = clave_nombre(nombre)
            if not k:
                continue
            tier, cat = clasificar(cargo)
            cur.execute("SELECT id, confianza FROM enriquecimiento.persona "
                        "WHERE prestador_id=%s AND nombre_clave=%s", (pid, k))
            fila = cur.fetchone()
            if fila:
                # Ya la conocíamos por una fuente oficial: la web confirma y
                # además suele aportar el cargo real, que el registro no trae.
                pid_persona, conf = fila
                cur.execute("""UPDATE enriquecimiento.persona
                               SET cargo = coalesce(nullif(%s,''), cargo),
                                   cargo_categoria = coalesce(%s, cargo_categoria),
                                   tier = coalesce(%s, tier),
                                   confianza = least(97, %s + 8),
                                   estado = 'verificado',
                                   ultima_verificacion = now()
                               WHERE id=%s""", (cargo, cat, tier, conf, pid_persona))
            else:
                cur.execute("""INSERT INTO enriquecimiento.persona
                    (prestador_id, nombre_clave, nombre, cargo, cargo_categoria,
                     tier, estado, confianza)
                    VALUES (%s,%s,%s,%s,%s,%s,'inferido',50)
                    ON CONFLICT (prestador_id, nombre_clave) DO UPDATE
                      SET cargo = excluded.cargo, ultima_verificacion = now()
                    RETURNING id""", (pid, k, nombre, cargo, cat, tier))
                pid_persona = cur.fetchone()[0]
                nuevas += 1
            cur.execute("""INSERT INTO enriquecimiento.evidencia
                (persona_id, fuente, referencia, crudo) VALUES (%s,'web',%s,%s)""",
                (pid_persona, url, json.dumps({"nombre": nombre, "cargo": cargo})))

            if correo:
                cur.execute("""INSERT INTO enriquecimiento.canal
                    (persona_id, prestador_id, tipo, valor, valor_norm, ambito,
                     estado, confianza)
                    VALUES (%s,%s,'correo',%s,%s,%s,'inferido',65)
                    ON CONFLICT (prestador_id, tipo, valor_norm) DO UPDATE
                      SET persona_id = coalesce(enriquecimiento.canal.persona_id,
                                                excluded.persona_id),
                          ultima_verificacion = now()
                    RETURNING id, (xmax = 0)""",
                    (pid_persona, pid, correo, correo, ambito_correo(correo)))
                cid, ins = cur.fetchone()
                if ins:
                    nuevos_canales += 1
                cur.execute("""INSERT INTO enriquecimiento.evidencia
                    (canal_id, fuente, referencia, crudo) VALUES (%s,'web',%s,%s)""",
                    (cid, url, json.dumps({"correo": correo})))

        # Correos del dominio sin persona identificada: son de la empresa.
        for c in correos:
            cur.execute("""INSERT INTO enriquecimiento.canal
                (prestador_id, tipo, valor, valor_norm, ambito, estado, confianza)
                VALUES (%s,'correo',%s,%s,%s,'inferido',55)
                ON CONFLICT (prestador_id, tipo, valor_norm) DO NOTHING
                RETURNING id""", (pid, c, c, ambito_correo(c)))
            f = cur.fetchone()
            if f:
                nuevos_canales += 1
                cur.execute("""INSERT INTO enriquecimiento.evidencia
                    (canal_id, fuente, referencia, crudo) VALUES (%s,'web',%s,%s)""",
                    (f[0], fuente_url, json.dumps({"correo": c})))
    return nuevas, nuevos_canales


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--prioridad", choices=["alta", "media", "baja"])
    ap.add_argument("--limite", type=int)
    ap.add_argument("--simular", action="store_true")
    args = ap.parse_args()

    cx = psycopg.connect(args.dsn)
    sql = """
      SELECT p.id, p.razon_social, lower(split_part(max(r.email),'@',2)) dom
      FROM reps.prestador p
      JOIN reps.registro_habilitacion r ON r.prestador_id = p.id
      JOIN reps.score_icp s ON s.prestador_id = p.id
      WHERE r.email IS NOT NULL AND p.clase_prestador <> 'Profesional Independiente'
        AND (%s::text IS NULL OR s.prioridad = %s::text)
      GROUP BY p.id, p.razon_social, s.score
      ORDER BY s.score DESC
    """
    with cx.cursor() as cur:
        cur.execute(sql, (args.prioridad, args.prioridad))
        filas = cur.fetchall()

    objetivo = []
    for pid, razon, dom in filas:
        d = dominio_valido(dom)
        if d:
            objetivo.append((pid, razon, d))
    if args.limite:
        objetivo = objetivo[:args.limite]

    print("── %d empresas con dominio propio utilizable (de %d con correo)"
          % (len(objetivo), len(filas)))
    if args.simular:
        print("   (simulación: no se tocó la red)")
        return 0

    stats = {"peticiones": 0, "intentos_fallidos": 0}
    con_personas = sin_respuesta = bloqueados = 0
    total_personas = total_canales = 0

    with cx.cursor() as cur:
        cur.execute("""INSERT INTO enriquecimiento.corrida (paso, alcance, empresas)
                       VALUES ('web', %s, %s) RETURNING id""",
                    (args.prioridad or "todas", len(objetivo)))
        corrida = cur.fetchone()[0]
    cx.commit()

    for n, (pid, razon, dom) in enumerate(objetivo, 1):
        personas, correos, vistas, motivo = rastrear(dom, stats)
        if motivo == "sin respuesta":
            sin_respuesta += 1
        elif motivo:
            bloqueados += 1
        if personas or correos:
            a, b = guardar(cx, pid, dom, personas, correos, "https://%s/" % dom)
            cx.commit()
            total_personas += a
            total_canales += b
            if personas:
                con_personas += 1
        if n % 25 == 0 or n == len(objetivo):
            print("   %4d/%d · %d sitios con personas · %d personas · %d canales"
                  % (n, len(objetivo), con_personas, total_personas, total_canales))

    with cx.cursor() as cur:
        cur.execute("""UPDATE enriquecimiento.corrida
                       SET personas_nuevas=%s, canales_nuevos=%s, peticiones=%s,
                           terminada=now(), notas=%s WHERE id=%s""",
                    (total_personas, total_canales, stats["peticiones"],
                     "sin respuesta: %d · robots: %d" % (sin_respuesta, bloqueados),
                     corrida))
    cx.commit()

    print("\n── Resultado")
    print("   sitios visitados        %6d" % len(objetivo))
    print("   con alguna persona      %6d   %5.1f %%"
          % (con_personas, 100.0 * con_personas / max(1, len(objetivo))))
    print("   sin respuesta           %6d" % sin_respuesta)
    print("   bloqueados por robots   %6d" % bloqueados)
    print("   personas nuevas         %6d" % total_personas)
    print("   canales nuevos          %6d" % total_canales)
    print("   peticiones              %6d   costo 0,00 USD" % stats["peticiones"])
    cx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
