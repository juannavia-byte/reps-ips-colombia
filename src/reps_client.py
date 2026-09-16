"""
Cliente de la consulta pública del REPS (Ministerio de Salud).

Por qué existe este archivo en vez de usar el dataset de datos.gov.co
---------------------------------------------------------------------
El dataset abierto `c36g-9fc2` tiene fecha de corte 12-mar-2026 y NO expone
servicios habilitados, capacidad instalada, representante legal, fechas de
habilitación, nivel ni carácter territorial. La consulta web sí, y su corte
es del día. Ver docs/00-fuentes.md.

Mecánica del portal (ASP.NET WebForms, descubierta por ingeniería inversa)
--------------------------------------------------------------------------
1. Login público con usuario/contraseña `invitado`. Genera ASP.NET_SessionId.
2. Las seis "pestañas" NO son paneles: son páginas .aspx distintas. Hacer
   postback del botón de pestaña sobre habilitados_reps.aspx y luego buscar
   resetea el grid a PRESTADORES. Hay que ir a la URL propia de cada una.
3. El querystring `pageTitle` debe ir codificado con `+`, no con `%20`, o
   `Page_Load` lanza FormatException.
4. Al hacer postback hay que enviar ÚNICAMENTE los <input type=hidden>.
   Serializar el formulario completo rompe la validación de eventos de
   ASP.NET porque el <select> de municipio se renderiza sin opciones y el
   valor vacío no está registrado.
5. Buscar sin filtros devuelve el universo nacional en UNA sola respuesta,
   sin paginación. No hay que iterar por departamento.
"""
from __future__ import annotations

import gzip
import hashlib
import html
import http.cookiejar
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

BASE = "https://prestadores.minsalud.gov.co/habilitacion/"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
PREFIX = "_ctl0:ContentPlaceHolder1:"

# El separador por defecto del portal es ';', que colisiona con el punto y coma
# embebido en campos de texto libre (observaciones, direcciones). El pipe es
# mucho más raro en texto castellano. El load valida el conteo de campos por
# fila y registra las que no cuadren, sea cual sea el separador.
SEPARATOR = "|"


@dataclass(frozen=True)
class Consulta:
    """Una pestaña del REPS: su página, el nombre del archivo y su grano."""

    clave: str
    pagina: str
    descripcion: str
    grano: str


CONSULTAS = (
    Consulta("prestadores", "habilitados_reps.aspx",
             "Registro actual de prestadores", "un prestador (código de habilitación)"),
    Consulta("sedes", "sedes_reps.aspx",
             "Sedes de prestadores", "una sede"),
    Consulta("servicios", "serviciossedes_reps.aspx",
             "Servicios habilitados por sede", "un servicio por sede"),
    Consulta("capacidad", "capacidadesinstaladas_reps.aspx",
             "Capacidad instalada por sede", "un concepto de capacidad por sede"),
)


class RepsError(RuntimeError):
    pass


def _error_aspnet(texto: str) -> str | None:
    if "Server Error" not in texto:
        return None
    m = re.search(r"<h2>\s*<i>([^<]+)</i>", texto)
    return html.unescape(m.group(1)) if m else "error no identificado"


class RepsClient:
    """Sesión autenticada contra el REPS con reintentos y pausa entre peticiones."""

    def __init__(self, pausa: float = 1.5, reintentos: int = 3, timeout: int = 1800):
        self.pausa = pausa
        self.reintentos = reintentos
        self.timeout = timeout
        self._cj = http.cookiejar.CookieJar()
        self._op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cj)
        )
        self._op.addheaders = [
            ("User-Agent", UA),
            ("Accept-Language", "es-CO,es;q=0.9"),
            ("Accept", "text/html,application/xhtml+xml,*/*;q=0.8"),
        ]
        self._autenticado = False

    # ---------------------------------------------------------------- http

    def _leer(self, resp) -> bytes:
        crudo = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            crudo = gzip.decompress(crudo)
        return crudo

    def _get(self, url: str) -> str:
        ultimo = None
        for intento in range(self.reintentos):
            try:
                time.sleep(self.pausa)
                with self._op.open(url, timeout=self.timeout) as r:
                    return self._leer(r).decode("cp1252", "ignore")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                ultimo = e
                time.sleep(2 ** intento * 3)
        raise RepsError(f"GET {url} falló tras {self.reintentos} intentos: {ultimo}")

    def _post(self, url: str, campos: dict) -> tuple[bytes, dict]:
        cuerpo = urllib.parse.urlencode(campos, encoding="cp1252", errors="ignore").encode()
        ultimo = None
        for intento in range(self.reintentos):
            try:
                time.sleep(self.pausa)
                req = urllib.request.Request(url, data=cuerpo)
                req.add_header("Content-Type", "application/x-www-form-urlencoded")
                req.add_header("Referer", url)
                with self._op.open(req, timeout=self.timeout) as r:
                    return self._leer(r), dict(r.headers)
            except urllib.error.HTTPError as e:
                # 500 suele ser validación de eventos: no se reintenta, se reporta.
                return e.read(), dict(e.headers or {})
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                ultimo = e
                time.sleep(2 ** intento * 3)
        raise RepsError(f"POST {url} falló tras {self.reintentos} intentos: {ultimo}")

    # ------------------------------------------------------------- aspnet

    @staticmethod
    def _ocultos(texto: str) -> dict:
        """Solo los <input type=hidden>. Ver nota 4 del docstring del módulo."""
        campos = {}
        for tag in re.findall(r'<input[^>]+type="hidden"[^>]*>', texto, re.I):
            nombre = re.search(r'name="([^"]+)"', tag)
            valor = re.search(r'value="([^"]*)"', tag)
            if nombre:
                campos[nombre.group(1)] = html.unescape(valor.group(1)) if valor else ""
        return campos

    def login(self) -> None:
        texto = self._get(BASE + "work.aspx")
        campos = self._ocultos(texto)
        campos.update(
            {"tbid_usuario": "invitado", "tbcontrasena": "invitado", "Button1": "Ingresar"}
        )
        crudo, _ = self._post(BASE + "work.aspx", campos)
        cuerpo = crudo.decode("cp1252", "ignore")
        if "Usuario Invitado" not in cuerpo:
            raise RepsError("el login como invitado no quedó establecido")
        self._autenticado = True

    # ------------------------------------------------------------ export

    def exportar(self, consulta: Consulta) -> dict:
        """Busca sin filtros (= universo nacional) y devuelve el CSV crudo."""
        if not self._autenticado:
            self.login()

        url = f"{BASE}consultas/{consulta.pagina}"
        t0 = time.time()

        pagina = self._get(url)
        if (err := _error_aspnet(pagina)):
            raise RepsError(f"{consulta.clave}: al abrir la página -> {err}")

        # Buscar sin ningún filtro. El portal interpreta eso como "todos".
        buscar = self._ocultos(pagina)
        buscar["_ctl0:ibBuscarHdr.x"] = "12"
        buscar["_ctl0:ibBuscarHdr.y"] = "9"
        crudo, _ = self._post(url, buscar)
        resultados = crudo.decode("cp1252", "ignore")
        if (err := _error_aspnet(resultados)):
            raise RepsError(f"{consulta.clave}: al buscar -> {err}")

        # Exportar el grid completo a texto delimitado.
        export = self._ocultos(resultados)
        export[PREFIX + "tbSeparator"] = SEPARATOR
        export[PREFIX + "ibText.x"] = "8"
        export[PREFIX + "ibText.y"] = "8"
        datos, cabeceras = self._post(url, export)

        muestra = datos[:400].decode("cp1252", "ignore")
        if (err := _error_aspnet(muestra)):
            raise RepsError(f"{consulta.clave}: al exportar -> {err}")
        if "Content-Disposition" not in cabeceras:
            raise RepsError(
                f"{consulta.clave}: el servidor devolvió HTML en vez de una descarga "
                f"({len(datos)} bytes). El grid no quedó poblado."
            )

        texto = datos.decode("cp1252", "ignore")
        corte = re.search(r"Fecha corte REPS:\s*([^|;\r\n]+)", texto)
        return {
            "clave": consulta.clave,
            "url": url,
            "bytes": datos.encode() if isinstance(datos, str) else datos,
            "sha256": hashlib.sha256(datos).hexdigest(),
            "tamano_bytes": len(datos),
            "segundos": round(time.time() - t0, 1),
            "archivo_servidor": cabeceras.get("Content-Disposition", ""),
            "fecha_corte_declarada": corte.group(1).strip() if corte else None,
            "separador": SEPARATOR,
        }
