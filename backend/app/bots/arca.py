"""Bot de ARCA (ex AFIP) - "Mis Comprobantes".

Reglas del bot:
- No escribe en consola ni en GUI: reporta con `log(mensaje)`.
- No devuelve `None`: devuelve un `BotResult` con los archivos generados.
- Si AFIP no entrega un rango, segmenta fechas automáticamente.
- Si ni a 15 días hay datos, falla con `TimeoutDescarga` (error tipado).
- Nunca deja el navegador abierto: el `finally` cierra el browser.

Orquestación de sesión (para no loguearse dos veces por cliente):

    with SesionArca(cuit_login, clave, log=log) as sesion:
        servicio = sesion.abrir_mis_comprobantes(cuit_representado)
        sesion.descargar(servicio, "Emitidos", ...)
        sesion.descargar(servicio, "Recibidos", ...)

El login se hace una sola vez y la misma ventana de Chrome sirve para los dos
tipos de comprobante (y para varios clientes con el mismo CUIT de ingreso).

ARCA cierra la sesión por inactividad (~15 minutos). Antes de cada operación se
consulta el portal con las cookies de la sesión; si venció, se vuelve a entrar
sin cerrar el navegador. Es el mismo enfoque que usa `retenciones-app`.
"""

import os
import re
import time
from datetime import datetime, timedelta
from typing import Callable

import openpyxl
from playwright.sync_api import Page, sync_playwright

from ..core import config
from ..core.errors import BotError, LoginError, SesionVencida, TimeoutDescarga
from ..core.models import BotResult
from .navegador import capturas_permitidas, como_fuente, inyectar, lanzar, nuevo_contexto, oculto

# Marcas con las que ARCA contesta cuando la sesión ya no vale.
MARCAS_SESION_VENCIDA = (
    "MISSING_AUTHENTICATION_INFO",
    "HTTP Status 401",
    "F1:username",
    # Mis Comprobantes con la sesión vencida: «Error No se encuentra logueado al
    # sistema o su sesión expiró» (antes se tomaba por «falta el filtro de fechas»).
    "No se encuentra logueado",
)

URL_LOGIN = "https://auth.afip.gob.ar/contribuyente_/login.xhtml"
URL_PORTAL = "https://portalcf.cloud.afip.gob.ar/portal/app/"
SERVICIO = "Mis Comprobantes"

# Login: cuánto se espera cada pantalla (CUIT -> clave -> portal). Si ARCA pide
# algo a la persona (captcha, código, cambio de clave) se le da más tiempo para
# completarlo en la ventana de Chrome del bot.
ESPERA_LOGIN_S = 45
ESPERA_PERSONA_S = 180

# Textos con los que ARCA avisa en el login que algo está mal (en minúsculas).
_PALABRAS_ERROR_LOGIN = (
    "incorrect", "inválid", "invalid", "no existe", "no es válid", "bloquead",
    "inhabilitad", "vencid", "no se pudo", "error", "intente nuevamente",
    "intentá nuevamente", "demasiados intentos",
)

# Lo que la persona tiene que hacer en la ventana de Chrome (en minúsculas).
_PALABRAS_PIDE_PERSONA = (
    "no soy un robot", "captcha", "verificá que sos", "verifique que es",
    "código de verificación", "codigo de verificacion", "segundo factor", "token",
    "cambiar tu clave", "cambio de clave", "nueva clave", "modificar clave",
    "términos y condiciones", "terminos y condiciones", "datos de contacto",
)

# Lee el texto visible del login sin tocar los campos: los mensajes de error y
# el texto general de la pantalla. Nunca lee el valor de un input (la clave).
_JS_PANTALLA_LOGIN = r"""() => {
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const sel = '[role=alert], .alert, .alert-danger, .ui-messages-error, .ui-message-error, '
    + '[class*=error], [class*=mensaje], [id*=msg], [id*=Msg], [id*=error], .text-danger';
  const mensajes = [...document.querySelectorAll(sel)]
    .filter(visible)
    .map((el) => (el.innerText || '').replace(/\s+/g, ' ').trim())
    .filter((s) => s && s.length < 300);
  const captcha = [...document.querySelectorAll('iframe')].some(
    (f) => visible(f) && /recaptcha|hcaptcha|challenges\.cloudflare/.test(f.src || ''));
  return {
    mensajes: [...new Set(mensajes)].slice(0, 3),
    captcha,
    texto: (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').trim().slice(0, 600),
    url: location.host + location.pathname,
  };
}"""


class PeriodoEquivocado(TimeoutDescarga):
    """ARCA devolvió comprobantes de otro período que el pedido (varias veces)."""


# Cuántas veces se vuelve a buscar si ARCA trae otro período.
REINTENTOS_PERIODO = 3


class FiltroNoDisponible(TimeoutDescarga):
    """ARCA no mostró el filtro de fechas tras elegir Emitidos/Recibidos.

    Quien abre el servicio puede cerrarlo, volver a abrirlo y reintentar una vez.
    """


def formatear_mes_anio(fecha_str):
    partes = fecha_str.split('/')
    if len(partes) == 3:
        return f"{partes[1]}-{partes[2][-2:]}"
    return fecha_str.replace('/', '-')

def limpiar_nombre(texto):
    return re.sub(r'[\\/*?:"<>|]', "", texto)

def restar_un_mes(fecha_str):
    d = datetime.strptime(fecha_str, "%d/%m/%Y")
    primer_dia_mes_actual = d.replace(day=1)
    ultimo_dia_mes_anterior = primer_dia_mes_actual - timedelta(days=1)
    return ultimo_dia_mes_anterior.strftime("%d/%m/%Y")

def limitar_a_hoy(fecha_str: str, hoy: datetime | None = None) -> str:
    """«30/11/2026» -> la fecha de hoy si todavía no llegó; si no, igual."""
    hoy = (hoy or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    fecha = datetime.strptime(fecha_str, "%d/%m/%Y")
    return (hoy if fecha > hoy else fecha).strftime("%d/%m/%Y")


def sumar_un_dia(fecha_str):
    d = datetime.strptime(fecha_str, "%d/%m/%Y") + timedelta(days=1)
    return d.strftime("%d/%m/%Y")

def contar_comprobantes(ruta) -> int:
    """Filas con datos debajo del título y los encabezados del Excel de ARCA."""
    wb = openpyxl.load_workbook(ruta, read_only=True)
    try:
        return sum(
            1
            for fila in wb.active.iter_rows(min_row=3, values_only=True)
            if any(celda is not None for celda in fila)
        )
    finally:
        wb.close()


def fechas_fuera_de_rango(ruta, fecha_desde: str, fecha_hasta: str) -> list[str]:
    """Las fechas del Excel de ARCA (columna Fecha) que no caen en el rango pedido.

    A veces el filtro muestra el período bien escrito pero ARCA busca con su
    rango por defecto (el mes en curso) y el Excel trae otro período.
    """
    desde = datetime.strptime(fecha_desde, "%d/%m/%Y").date()
    hasta = datetime.strptime(fecha_hasta, "%d/%m/%Y").date()
    fuera: list[str] = []
    wb = openpyxl.load_workbook(ruta, read_only=True)
    try:
        for fila in wb.active.iter_rows(min_row=3, max_col=1, values_only=True):
            valor = fila[0] if fila else None
            if valor in (None, ""):
                continue
            if isinstance(valor, datetime):
                fecha = valor.date()
            else:
                try:
                    fecha = datetime.strptime(str(valor).strip()[:10], "%d/%m/%Y").date()
                except ValueError:
                    try:
                        fecha = datetime.strptime(str(valor).strip()[:10], "%Y-%m-%d").date()
                    except ValueError:
                        continue  # no es una fecha: no se puede juzgar
            if not desde <= fecha <= hasta:
                fuera.append(fecha.strftime("%d/%m/%Y"))
    finally:
        wb.close()
    return fuera


def unificar_excels(archivos_parciales, ruta_final):
    if not archivos_parciales:
        return
    wb_master = openpyxl.load_workbook(archivos_parciales[0])
    ws_master = wb_master.active

    for archivo in archivos_parciales[1:]:
        wb_temp = openpyxl.load_workbook(archivo)
        ws_temp = wb_temp.active
        for row in ws_temp.iter_rows(min_row=3, values_only=True):
            if any(cell is not None for cell in row):
                ws_master.append(row)
        wb_temp.close()

    wb_master.save(ruta_final)
    wb_master.close()


class SesionArca:
    """Una sesión de Chrome ya logueada en ARCA.

    El login y el keep-alive viven acá. El navegador se abre una sola vez y se
    reutiliza para todos los clientes y comprobantes del job. Se debe usar desde
    el mismo hilo que la creó (limitación de Playwright sync).
    """

    def __init__(
        self,
        cuit_login: str,
        clave: str,
        log: Callable[[str], None] = print,
        *,
        intervalo_ping_s: int | None = None,
        renovar_s: int | None = None,
        playwright=None,
    ) -> None:
        # Playwright sync admite uno solo por hilo: si viene de afuera, no es nuestro.
        self._pw_ajeno = playwright
        self.cuit_login = (cuit_login or "").strip()
        # La clave no se guarda como str: es una fuente que entrega un Secreto
        # en el momento de escribirlo (JIT) y que se borra después.
        self._fuente_clave, self._clave_jit = como_fuente(clave)
        self.log = log
        self.intervalo_ping_s = intervalo_ping_s or config.ARCA_PING_MIN * 60
        self.renovar_s = renovar_s or config.ARCA_RENOVAR_MIN * 60

        self._pw = None
        self._browser = None
        self._context = None
        self._portal: Page | None = None
        self._login_ts = 0.0
        self._ultimo_ping = 0.0

    # -- ciclo de vida -----------------------------------------------------
    def abrir(self) -> "SesionArca":
        if self._browser is not None:
            return self
        self._pw = self._pw_ajeno or sync_playwright().start()
        try:
            self._browser = lanzar(self._pw, maximizado=not config.PLAYWRIGHT_HEADLESS)
            self._context = nuevo_contexto(
                self._browser, self.log, no_viewport=not config.PLAYWRIGHT_HEADLESS
            )
            self._context.accept_downloads = True
            self._portal = self._context.new_page()
            self._portal.on("dialog", self._manejar_dialogo)
            self._login()
        except Exception:
            self.cerrar()
            raise
        return self

    def cerrar(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if not self._clave_jit:
                self._fuente_clave().borrar()
            self._browser = None
            self._context = None
            self._portal = None
            if self._pw is not None and self._pw is not self._pw_ajeno:
                try:
                    self._pw.stop()
                except Exception:
                    pass
                self._pw = None

    def __enter__(self) -> "SesionArca":
        return self.abrir()

    def __exit__(self, *exc) -> None:
        self.cerrar()

    # -- login / keep-alive ------------------------------------------------
    def _manejar_dialogo(self, dialog) -> None:
        self.log(
            f"      [AVISO]: ARCA mostró un diálogo del navegador "
            f"('{dialog.message}'). Se cerró automáticamente."
        )
        try:
            dialog.dismiss()
        except Exception:
            pass

    def _ir(self, page: Page, url: str) -> None:
        """Abre una página de ARCA sin esperar a que bajen todos sus recursos.

        Con el `load` por defecto, cuando ARCA está lenta se pasaba de los 30 s
        aunque el formulario ya estuviera a la vista. Se espera el HTML
        (`domcontentloaded`), con 60 s de margen y un reintento.
        """
        for intento in (1, 2):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                return
            except Exception as exc:
                if intento == 2:
                    raise BotError(
                        "ARCA no respondió: la página no cargó en dos intentos de 60 s. "
                        "Puede estar caída o muy lenta; probá de nuevo en unos minutos.",
                        detalle={"url": url, "error": str(exc)[:300]},
                    ) from exc
                self.log("      [AVISO]: ARCA tarda en responder. Se reintenta...")

    def _login(self) -> None:
        if self._portal is None or self._portal.is_closed():
            self._portal = self._context.new_page()
            self._portal.on("dialog", self._manejar_dialogo)

        page = self._portal
        self.log("   -> Iniciando sesión en ARCA...")
        self._ir(page, URL_LOGIN)
        self._esperar_login(page, "id=F1:username", "la pantalla de ingreso")
        page.fill("id=F1:username", self.cuit_login)
        page.click("id=F1:btnSiguiente")
        self._esperar_login(page, "id=F1:password", "el campo de la clave (después del CUIT)")
        inyectar(page, "id=F1:password", self._fuente_clave, self._clave_jit)
        page.click("id=F1:btnIngresar")

        self._esperar_login(page, "#buscadorInput", "el portal (después de la clave)")
        self._login_ts = time.monotonic()
        self._ultimo_ping = self._login_ts
        self.log("      [OK]: Sesión de ARCA iniciada.")

    def _pantalla_login(self, page: Page) -> dict:
        try:
            return page.evaluate(_JS_PANTALLA_LOGIN) or {}
        except Exception:
            return {}

    def _esperar_login(self, page: Page, selector: str, que: str) -> None:
        """Espera la próxima pantalla del login y, si no llega, dice por qué.

        - Si ARCA muestra un mensaje de error (CUIT o clave mal, clave vencida o
          bloqueada), corta enseguida con ese mensaje.
        - Si pide algo a la persona (captcha, código, cambio de clave), lo avisa
          en el registro y espera hasta ``ESPERA_PERSONA_S`` a que se complete en
          la ventana de Chrome del bot.
        - Si no pasa nada, corta a los ``ESPERA_LOGIN_S`` con lo que decía la
          pantalla, en vez de un timeout de Playwright.
        """
        inicio = time.monotonic()
        avisado = False
        while True:
            try:
                page.wait_for_selector(selector, state="visible", timeout=2500)
                if avisado:
                    self.log("      [OK]: Listo, se sigue con el ingreso a ARCA.")
                return
            except Exception:
                pass
            pantalla = self._pantalla_login(page)
            # Los errores de login solo cuentan en la pantalla de ingreso: una vez
            # en el portal, cartelitos como «Ocurrió un error al cargar tus
            # vencimientos» son de los widgets y no dicen nada de la clave.
            url = (pantalla.get("url") or getattr(page, "url", "") or "").lower()
            en_ingreso = not url or "auth.afip" in url
            mensajes = [
                m for m in pantalla.get("mensajes") or []
                if en_ingreso and any(p in m.lower() for p in _PALABRAS_ERROR_LOGIN)
            ]
            if mensajes:
                raise LoginError(
                    f"ARCA no dejó entrar: «{mensajes[0]}». "
                    "Revisá el CUIT de ingreso y la clave fiscal del cliente.",
                    detalle={"pantalla": pantalla.get("url"), "esperaba": que},
                )
            texto = (pantalla.get("texto") or "").lower()
            pide = bool(pantalla.get("captcha")) or any(p in texto for p in _PALABRAS_PIDE_PERSONA)
            if pide and oculto():
                raise LoginError(
                    "ARCA pidió un captcha o un paso extra y el navegador corre oculto. "
                    "Volvé a intentar en unos minutos; si sigue pidiéndolo, abrí la app con "
                    "MODULONUEVO_HEADLESS=0 para completarlo en la ventana de Chrome.",
                    detalle={"pantalla": pantalla.get("url"), "esperaba": que},
                )
            if pide and not avisado:
                avisado = True
                que_pide = "un captcha" if pantalla.get("captcha") else "un paso extra"
                self.log(
                    f"      [AVISO]: ARCA pide {que_pide} antes de seguir. Completalo en la "
                    f"ventana de Chrome del bot: se espera hasta {ESPERA_PERSONA_S // 60} minutos."
                )
            transcurrido = time.monotonic() - inicio
            if transcurrido > (ESPERA_PERSONA_S if pide else ESPERA_LOGIN_S):
                resumen = (pantalla.get("texto") or "").strip()
                resumen = (resumen[:220] + "…") if len(resumen) > 220 else resumen
                motivo = (
                    "no se completó lo que pedía ARCA (captcha, código o cambio de clave)"
                    if pide
                    else "puede ser un CUIT o una clave fiscal incorrectos, o ARCA muy lenta"
                )
                raise LoginError(
                    f"No se pudo iniciar sesión en ARCA: no apareció {que} en "
                    f"{int(transcurrido)} s ({motivo})."
                    + (f" La pantalla decía: «{resumen}»" if resumen else ""),
                    detalle={"pantalla": pantalla.get("url"), "esperaba": que},
                )

    def _ping(self) -> bool:
        """Consulta el portal con las cookies de la sesión.

        Devuelve False si el portal contesta con la pantalla de login (sesión
        vencida) o si no se pudo consultar.

        Se compara sobre los bytes de la respuesta: ARCA contesta en Latin-1 y
        `respuesta.text()` falla con la primera tilde (lo mismo que le pasaba a
        `retenciones-app`).
        """
        if self._context is None:
            return False
        try:
            # ignore_https_errors: el pedido va por fuera de Chrome y ARCA no manda la
            # cadena de certificados completa («unable to verify the first
            # certificate»): sin esto el chequeo fallaba siempre y se re-logueaba de más.
            respuesta = self._context.request.get(URL_PORTAL, timeout=15000, ignore_https_errors=True)
            if respuesta.status in (401, 403):
                return False
            cuerpo = respuesta.body()
            return not any(
                marca.encode("utf-8") in cuerpo for marca in MARCAS_SESION_VENCIDA
            )
        except Exception as exc:
            self.log(f"      [AVISO]: No se pudo verificar la sesión de ARCA: {exc}")
            return False

    def asegurar_sesion(self, *, forzar: bool = False) -> None:
        """Mantiene viva la sesión y la renueva si el portal ya la venció."""
        if self._browser is None:
            self.abrir()
            return

        ahora = time.monotonic()
        vencida_por_tiempo = (ahora - self._login_ts) >= self.renovar_s
        toca_ping = (ahora - self._ultimo_ping) >= self.intervalo_ping_s

        if forzar or vencida_por_tiempo or toca_ping:
            if not self._ping():
                self.log("      [AVISO]: La sesión de ARCA venció. Se vuelve a entrar...")
                self._login()
            else:
                self._ultimo_ping = time.monotonic()

    def renovar(self) -> None:
        """Fuerza un login nuevo conservando el navegador."""
        if self._browser is None:
            self.abrir()
        else:
            self._login()

    def _es_sesion_vencida(self, page: Page) -> bool:
        try:
            url = (page.url or "").lower()
            if any(m in url for m in ("login", "auth.afip", "expiredsession", "/logout")):
                return True
            html = page.content()
            return any(marca in html for marca in MARCAS_SESION_VENCIDA)
        except Exception:
            return False

    # -- entrada al servicio ----------------------------------------------
    def _abrir_servicio(self, cuit_representado: str) -> Page:
        self.asegurar_sesion()
        portal = self._portal
        if portal is None or portal.is_closed():
            raise SesionVencida("La ventana del portal de ARCA se cerró.")

        if portal.locator("#buscadorInput").count() == 0:
            self._ir(portal, URL_PORTAL)
            # El buscador del portal o, si ARCA venció la sesión, su pantalla de
            # login: antes se esperaba el buscador el tiempo entero aunque ARCA ya
            # hubiera mandado al login (1 min perdido y el cliente con error).
            login = portal.locator("[id='F1:username']")
            try:
                portal.locator("#buscadorInput").or_(login).first.wait_for(
                    state="visible", timeout=config.PLAYWRIGHT_ESPERA_MS
                )
            except Exception as exc:
                if self._es_sesion_vencida(portal):
                    raise SesionVencida("ARCA pidió volver a iniciar sesión en el portal.") from exc
                raise
            if login.count() or self._es_sesion_vencida(portal):
                raise SesionVencida("ARCA pidió volver a iniciar sesión en el portal.")

        buscador = portal.locator("#buscadorInput")
        buscador.click()
        buscador.fill("")
        buscador.press_sequentially(SERVICIO, delay=10)

        # Tiempo para que el buscador del portal muestre la sugerencia.
        portal.wait_for_timeout(800)
        portal.keyboard.press("ArrowDown")
        portal.wait_for_timeout(200)

        with self._context.expect_page() as nueva_pestana_info:
            portal.keyboard.press("Enter")
        servicio = nueva_pestana_info.value
        servicio.on("dialog", self._manejar_dialogo)

        # Selección de perfil (si ARCA la pide). Se espera lo que llegue primero:
        # la pantalla de perfiles o el servicio ya abierto. Antes se esperaban
        # 2,5 s fijos aunque ARCA no pidiera perfil.
        titulo_eleccion = servicio.get_by_text(
            "Elegí una persona para ingresar", exact=False
        )
        try:
            servicio.locator("#btnEmitidos").or_(titulo_eleccion).first.wait_for(
                state="visible", timeout=config.PLAYWRIGHT_ESPERA_MS
            )
        except Exception:
            pass
        try:
            if not titulo_eleccion.is_visible():
                raise LookupError("ARCA no pidió elegir perfil.")
            cuit_limpio = (cuit_representado or "").strip()
            cuit_formateado = (
                f"{cuit_limpio[:2]}-{cuit_limpio[2:10]}-{cuit_limpio[10:]}"
                if len(cuit_limpio) == 11
                else cuit_limpio
            )
            tarjetas = servicio.locator(
                f"a:has-text('{cuit_formateado}'), a:has-text('{cuit_limpio}')"
            )
            if tarjetas.count() == 0:
                raise RuntimeError(
                    f"ARCA no muestra el CUIT {cuit_formateado} entre los perfiles."
                )
            # La tarjeta a veces nunca queda «visible y estable» para Playwright
            # (animación del panel o un duplicado oculto) y el clic esperaba 30 s
            # para nada. Se prueba un clic normal corto sobre la visible y, si no,
            # se dispara su onclick (envía el formulario de selección).
            visibles = tarjetas.locator("visible=true")
            tarjeta_empresa = visibles.first if visibles.count() else tarjetas.first
            try:
                tarjeta_empresa.click(timeout=5000)
            except Exception:
                # El clic pudo haber andado igual (la página ya está navegando):
                # solo se fuerza si seguimos en la pantalla de perfiles.
                if titulo_eleccion.is_visible():
                    tarjeta_empresa.evaluate("nodo => nodo.click()", timeout=5000)
            try:
                servicio.wait_for_load_state("domcontentloaded", timeout=config.PLAYWRIGHT_ESPERA_MS)
            except Exception:
                pass  # Lo que importa es que aparezca «Emitidos», que se espera abajo.
            servicio.wait_for_timeout(500)
        except LookupError:
            pass
        except Exception as exc:
            self.log(
                f"      [INFO]: Selección de perfil no aplicó o no se pudo "
                f"completar: {exc}"
            )

        try:
            servicio.wait_for_selector("#btnEmitidos", state="visible", timeout=config.PLAYWRIGHT_ESPERA_MS)
        except Exception as exc:
            if self._es_sesion_vencida(servicio):
                raise SesionVencida(
                    "ARCA pidió volver a iniciar sesión al abrir Mis Comprobantes."
                ) from exc
            raise

        if self._es_sesion_vencida(servicio):
            raise SesionVencida("ARCA pidió volver a iniciar sesión.")
        return servicio

    def abrir_mis_comprobantes(self, cuit_representado: str = "") -> Page:
        """Abre el servicio; si la sesión venció, re-loguea y reintenta una vez."""
        for intento in (1, 2):
            try:
                return self._abrir_servicio(cuit_representado)
            except SesionVencida:
                if intento == 2:
                    raise
                self.log("      [AVISO]: Se renueva la sesión de ARCA y se reintenta...")
                self.renovar()
        raise SesionVencida("No se pudo entrar a Mis Comprobantes de ARCA.")

    # -- descarga ----------------------------------------------------------
    def _apretar_tipo(self, servicio: Page, boton_tipo, tipo_comprobante: str) -> None:
        """Aprieta Emitidos/Recibidos hasta que aparezca el filtro de fechas.

        Si se aprieta apenas el botón se ve, ARCA todavía no terminó de cargar
        su JS y el click no hace nada. Se espera la carga completa y, si el
        filtro no aparece en unos segundos, se vuelve a apretar (hasta 3 veces).
        """
        try:
            servicio.wait_for_load_state("load", timeout=config.PLAYWRIGHT_ESPERA_MS)
        except Exception:
            pass
        fecha = servicio.locator("#fechaEmision")
        for intento in (1, 2, 3):
            if intento > 1 and boton_tipo.count() == 0:
                # El click anterior sí navegó (el botón ya no está) pero el filtro
                # no apareció: volver a apretar no sirve, hay que reabrir el servicio.
                break
            try:
                if intento == 1:
                    boton_tipo.click(timeout=5000)
                else:
                    self.log(
                        f"      [AVISO]: ARCA no respondió al botón '{tipo_comprobante}'. "
                        f"Reintento {intento}/3..."
                    )
                    boton_tipo.evaluate("el => el.click()", timeout=3000)
            except Exception as exc:
                self.log(f"      [AVISO]: No se pudo apretar '{tipo_comprobante}': {exc}")
            try:
                fecha.wait_for(state="visible", timeout=6000 if intento < 3 else 15000)
                return
            except Exception:
                if self._es_sesion_vencida(servicio):
                    raise SesionVencida(
                        "ARCA pidió volver a iniciar sesión al elegir el tipo de comprobante."
                    ) from None
        if servicio.get_by_text("Elegí una persona para ingresar", exact=False).first.is_visible():
            raise FiltroNoDisponible(
                f"ARCA pidió elegir la empresa representada al apretar '{tipo_comprobante}' "
                "y no se pudo elegir. Revisá que el CUIT de ingreso represente a esta "
                "empresa en Mis Comprobantes y que el CUIT representado esté bien cargado.",
                detalle={"tipo": tipo_comprobante, "url": servicio.url},
            )
        if self._es_sesion_vencida(servicio):
            raise SesionVencida(
                f"ARCA venció la sesión al apretar '{tipo_comprobante}'."
            )
        pantalla = self._resumen_pantalla(servicio)
        self._guardar_diagnostico(servicio, f"arca-{tipo_comprobante.lower()}")
        raise FiltroNoDisponible(
            f"ARCA no mostró el filtro de fechas después de apretar '{tipo_comprobante}'. "
            + (f"En pantalla había: «{pantalla}». " if pantalla else "")
            + "Puede estar lento: probá de nuevo en unos minutos.",
            detalle={"tipo": tipo_comprobante, "url": servicio.url},
        )

    @staticmethod
    def _resumen_pantalla(page: Page, largo: int = 160) -> str:
        """Las primeras palabras visibles de la página, para saber dónde quedó."""
        try:
            texto = page.locator("body").inner_text(timeout=2000)
        except Exception:
            return ""
        texto = " ".join(texto.split())
        return texto if len(texto) <= largo else texto[:largo].rstrip() + "…"

    def _guardar_diagnostico(self, page: Page, nombre: str) -> None:
        """Captura + HTML de la pantalla (solo si están habilitadas las capturas)."""
        if not capturas_permitidas():
            return
        try:
            carpeta = os.path.join(config.LOG_DIR, "diagnostico")
            os.makedirs(carpeta, exist_ok=True)
            sello = f"{datetime.now():%Y%m%d-%H%M%S}"
            page.screenshot(path=os.path.join(carpeta, f"{nombre}-{sello}.png"), full_page=True)
            with open(os.path.join(carpeta, f"{nombre}-{sello}.html"), "w", encoding="utf-8") as f:
                f.write(page.content())
            self.log(f"      [INFO]: Captura y HTML de la pantalla guardados en {carpeta}")
        except Exception as exc:
            self.log(f"      [AVISO]: No se pudo guardar el diagnóstico: {exc}")

    def _escribir_rango(self, servicio: Page, caja_fecha, rango_fechas: str) -> None:
        """Escribe el rango en el filtro y confirma que ARCA lo tomó.

        Con esperas cortas el selector de fechas a veces no registraba el texto
        y ARCA buscaba con su fecha por defecto: el Excel salía vacío. Se usan
        las esperas originales y se verifica el valor (hasta 3 intentos).
        """
        for intento in (1, 2, 3):
            try:
                caja_fecha.wait_for(state="visible", timeout=config.PLAYWRIGHT_ESPERA_MS)
                caja_fecha.click(timeout=10000)
                servicio.wait_for_timeout(800)
                caja_fecha.click(click_count=3, timeout=10000)
            except Exception as exc:
                if self._es_sesion_vencida(servicio):
                    raise SesionVencida(
                        "ARCA pidió volver a iniciar sesión al cargar el período."
                    ) from exc
                causa = (str(exc).splitlines() or [type(exc).__name__])[0][:160]
                if intento == 3:
                    self._guardar_diagnostico(servicio, "arca-filtro-fechas")
                    # Filtro trabado (ARCA lento o algo encima): quien abrió el
                    # servicio lo cierra, lo vuelve a abrir y reintenta.
                    raise FiltroNoDisponible(
                        "El filtro de fechas de ARCA no respondió después de 3 intentos. "
                        "ARCA puede estar lento: probá de nuevo en unos minutos.",
                        detalle={"rango": rango_fechas, "causa": causa},
                    ) from exc
                self.log(
                    f"      [AVISO]: El filtro de fechas de ARCA todavía no estaba listo ({causa}). "
                    f"Reintento {intento + 1}/3..."
                )
                servicio.wait_for_timeout(2000)
                continue
            servicio.wait_for_timeout(200)
            servicio.keyboard.press("Backspace")
            servicio.wait_for_timeout(200)
            caja_fecha.press_sequentially(rango_fechas, delay=5)
            servicio.wait_for_timeout(400)
            caja_fecha.press("Enter")
            servicio.wait_for_timeout(400)
            try:
                valor = (caja_fecha.input_value(timeout=2000) or "").strip()
            except Exception:
                valor = ""
            # Se comparan solo los dígitos: no importa si ARCA cambia espacios o guiones.
            if re.sub(r"\D", "", valor) == re.sub(r"\D", "", rango_fechas):
                return
            self.log(
                f"      [AVISO]: ARCA no tomó el período (quedó «{valor or 'vacío'}»). "
                f"Reintento {intento}/3..."
            )
        raise TimeoutDescarga(
            f"ARCA no aceptó el período {rango_fechas} en el filtro de fechas.",
            detalle={"rango": rango_fechas},
        )

    def descargar(
        self,
        servicio: Page,
        tipo_comprobante: str,
        fecha_desde: str,
        fecha_hasta: str,
        nombre_cliente: str,
        directorio_guardado: str,
        *,
        bajar_excel: bool = True,
        bajar_csv: bool = False,
    ) -> BotResult:
        """Descarga un tipo de comprobante dentro de una sesión ya abierta."""
        resultado = BotResult()
        os.makedirs(directorio_guardado, exist_ok=True)

        # Volver a la pestaña de consulta por si la descarga anterior dejó otra.
        try:
            tab_consulta = servicio.locator('a[href="#tabConsulta"]')
            if tab_consulta.count() and tab_consulta.first.is_visible():
                tab_consulta.first.click()
                servicio.wait_for_timeout(500)
        except Exception:
            pass

        boton_id = "#btnEmitidos" if tipo_comprobante == "Emitidos" else "#btnRecibidos"
        boton_tipo = servicio.locator(boton_id).first
        self.log(f"   -> Apretando botón '{tipo_comprobante}'...")
        self._apretar_tipo(servicio, boton_tipo, tipo_comprobante)
        # El filtro aparece antes de que ARCA termine de prepararlo: la espera
        # original. Sin ella, el click en la fecha a veces no llegaba.
        servicio.wait_for_timeout(1500)

        # ARCA no acepta fechas futuras: si el mes no terminó, hasta hoy.
        fecha_hasta = limitar_a_hoy(fecha_hasta)
        fecha_desde_actual = fecha_desde
        fecha_hasta_actual = fecha_hasta
        excels_parciales: list[str] = []
        csv_parciales: list[str] = []
        # Lo que exporta ARCA con «CSV» (un .zip), guardado tal cual: es lo que se sube a Xubio.
        originales_csv: list[str] = []
        primer_vuelta = True
        reintentos_periodo = 0

        while datetime.strptime(fecha_desde_actual, "%d/%m/%Y") <= datetime.strptime(
            fecha_hasta, "%d/%m/%Y"
        ):
            if not primer_vuelta:
                servicio.wait_for_selector("#fechaEmision", timeout=config.PLAYWRIGHT_ESPERA_MS)
            primer_vuelta = False
            rango_fechas = f"{fecha_desde_actual} - {fecha_hasta_actual}"
            self.log(f"   -> Solicitando periodo en ARCA: {rango_fechas}")

            caja_fecha = servicio.locator("#fechaEmision")
            self._escribir_rango(servicio, caja_fecha, rango_fechas)

            boton_buscar = servicio.locator("#buscarComprobantes")
            boton_buscar.scroll_into_view_if_needed()
            servicio.wait_for_timeout(150)
            boton_buscar.click(timeout=3000)
            # Que ARCA termine de traer los resultados antes de exportar: si se
            # exporta antes, el Excel sale con los encabezados y sin comprobantes.
            servicio.wait_for_timeout(700)
            try:
                servicio.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            # --- PROCESO DE DESCARGA ---
            if bajar_csv and not bajar_excel:
                try:
                    boton_csv = servicio.wait_for_selector(
                        'button[title="Exportar como CSV"], button:has-text("CSV")',
                        state="visible",
                        timeout=config.PLAYWRIGHT_ESPERA_MS,
                    )
                    with servicio.expect_download(timeout=config.PLAYWRIGHT_DESCARGA_MS) as download_info:
                        boton_csv.click()
                    download = download_info.value
                    nombre_temporal = f"Parcial_{len(csv_parciales)+1}_{download.suggested_filename}"
                    ruta_parcial = os.path.join(directorio_guardado, nombre_temporal)
                    download.save_as(ruta_parcial)
                    csv_parciales.append(ruta_parcial)

                    fecha_desde_actual = sumar_un_dia(fecha_hasta_actual)
                    fecha_hasta_actual = fecha_hasta
                    if datetime.strptime(fecha_desde_actual, "%d/%m/%Y") > datetime.strptime(
                        fecha_hasta, "%d/%m/%Y"
                    ):
                        break
                except Exception as exc:
                    if self._es_sesion_vencida(servicio):
                        raise SesionVencida(
                            "ARCA pidió volver a iniciar sesión durante la descarga."
                        ) from exc
                    raise TimeoutDescarga(
                        f"Error descargando CSV en ARCA: {exc}",
                        detalle={"tipo": tipo_comprobante},
                    ) from exc

                servicio.click('a[href="#tabConsulta"]')
                servicio.wait_for_timeout(1000)
                continue

            try:
                boton_excel = servicio.wait_for_selector(
                    "button.buttons-excel", state="visible", timeout=config.PLAYWRIGHT_ESPERA_MS
                )

                with servicio.expect_download(timeout=config.PLAYWRIGHT_DESCARGA_MS) as download_info:
                    boton_excel.click()
                download = download_info.value
                nombre_temp_excel = f"ParcialExcel_{len(excels_parciales)+1}_{download.suggested_filename}"
                ruta_parcial_excel = os.path.join(directorio_guardado, nombre_temp_excel)
                download.save_as(ruta_parcial_excel)

                # ¿ARCA buscó el período pedido? Si el filtro no se aplicó, trae
                # su rango por defecto (el mes en curso): se descarta y se busca
                # de nuevo, sin avanzar.
                fuera = fechas_fuera_de_rango(ruta_parcial_excel, fecha_desde_actual, fecha_hasta_actual)
                if fuera:
                    try:
                        os.remove(ruta_parcial_excel)
                    except OSError:
                        pass
                    reintentos_periodo += 1
                    ejemplo = ", ".join(sorted(set(fuera))[:3])
                    if reintentos_periodo >= REINTENTOS_PERIODO:
                        raise PeriodoEquivocado(
                            f"ARCA devolvió comprobantes de otro período ({ejemplo}) aunque se pidió "
                            f"{rango_fechas}, {REINTENTOS_PERIODO} veces seguidas. Volvé a correr el control "
                            "en unos minutos.",
                            detalle={"tipo": tipo_comprobante, "rango": rango_fechas, "fechas": sorted(set(fuera))[:10]},
                        )
                    self.log(
                        f"      [AVISO]: ARCA trajo comprobantes de otro período ({ejemplo}) en vez de "
                        f"{rango_fechas}. Se descarta y se vuelve a buscar "
                        f"({reintentos_periodo + 1}/{REINTENTOS_PERIODO})..."
                    )
                    servicio.click('a[href="#tabConsulta"]')
                    servicio.wait_for_timeout(1500)
                    continue
                reintentos_periodo = 0
                excels_parciales.append(ruta_parcial_excel)

                if bajar_csv:
                    # El CSV es para «Subir a Xubio»: si no baja, el control sigue
                    # (con el Excel alcanza para auditar).
                    try:
                        boton_csv = servicio.locator(
                            'button[title="Exportar como CSV"], button:has-text("CSV")'
                        ).first
                        if boton_csv.is_visible():
                            with servicio.expect_download(timeout=config.PLAYWRIGHT_DESCARGA_MS) as download_info_csv:
                                boton_csv.click()
                            download_c = download_info_csv.value
                            # Con el nombre y el contenido que puso ARCA: Xubio no acepta otro.
                            ruta_original = _ruta_libre(directorio_guardado, download_c.suggested_filename)
                            download_c.save_as(ruta_original)
                            originales_csv.append(ruta_original)
                        else:
                            self.log("      [AVISO]: ARCA no mostró el botón «CSV»: no se bajó el CSV para Xubio.")
                    except Exception as exc:
                        if self._es_sesion_vencida(servicio):
                            raise SesionVencida("ARCA pidió volver a iniciar sesión durante la descarga.") from exc
                        self.log(f"      [AVISO]: No se pudo bajar el CSV de ARCA (para Xubio): {exc}")

                fecha_desde_actual = sumar_un_dia(fecha_hasta_actual)
                fecha_hasta_actual = fecha_hasta

                if datetime.strptime(fecha_desde_actual, "%d/%m/%Y") > datetime.strptime(
                    fecha_hasta, "%d/%m/%Y"
                ):
                    break

                servicio.click('a[href="#tabConsulta"]')
                servicio.wait_for_timeout(1000)

            except PeriodoEquivocado:
                raise
            except Exception as exc:
                if self._es_sesion_vencida(servicio):
                    raise SesionVencida(
                        "ARCA pidió volver a iniciar sesión durante la descarga."
                    ) from exc

                d_desde = datetime.strptime(fecha_desde_actual, "%d/%m/%Y")
                d_hasta = datetime.strptime(fecha_hasta_actual, "%d/%m/%Y")
                dias_rango = (d_hasta - d_desde).days + 1

                self.log(
                    f"      [INFO]: ARCA congestionado (Rango: {dias_rango} días). "
                    f"Segmentando..."
                )
                if dias_rango > 31:
                    fecha_hasta_actual = restar_un_mes(fecha_hasta_actual)
                elif 15 < dias_rango <= 31:
                    mitad_mes = d_desde + timedelta(days=14)
                    fecha_hasta_actual = mitad_mes.strftime("%d/%m/%Y")
                else:
                    raise TimeoutDescarga(
                        "Límite crítico: ARCA no entrega datos ni a 15 días.",
                        detalle={
                            "tipo": tipo_comprobante,
                            "desde": fecha_desde_actual,
                            "hasta": fecha_hasta_actual,
                        },
                    )

                if datetime.strptime(fecha_hasta_actual, "%d/%m/%Y") < datetime.strptime(
                    fecha_desde_actual, "%d/%m/%Y"
                ):
                    raise BotError(
                        "Fallo en reducción matemática de fechas.",
                        detalle={
                            "tipo": tipo_comprobante,
                            "desde": fecha_desde_actual,
                            "hasta": fecha_hasta_actual,
                        },
                    )

                servicio.click('a[href="#tabConsulta"]')
                servicio.wait_for_timeout(1000)

        # --- UNIFICACIÓN ---
        mes_anio_desde = formatear_mes_anio(fecha_desde)
        mes_anio_hasta = formatear_mes_anio(fecha_hasta)
        cliente_limpio = limpiar_nombre(nombre_cliente)

        if bajar_excel and excels_parciales:
            nombre_archivo_final = (
                f"Mis comprobantes - {tipo_comprobante} - {cliente_limpio} - "
                f"{mes_anio_desde} al {mes_anio_hasta}.xlsx"
            )
            ruta_final = os.path.join(directorio_guardado, nombre_archivo_final)
            unificar_excels(excels_parciales, ruta_final)
            resultado.archivos.append(ruta_final)
            cantidad = contar_comprobantes(ruta_final)
            if cantidad:
                self.log(
                    f"      [OK]: Consolidado: {nombre_archivo_final} "
                    f"({cantidad} comprobante(s))"
                )
            else:
                # No es un error seguro (puede no haber movimientos), pero si el
                # período debería tener datos, la auditoría va a salir vacía.
                self.log(
                    f"      [AVISO]: ARCA devolvió 0 comprobantes {tipo_comprobante} "
                    f"para {fecha_desde} al {fecha_hasta}. Si el período tiene "
                    "movimientos, volvé a correr el control."
                )

        if originales_csv:
            resultado.archivos.extend(originales_csv)
            if len(originales_csv) > 1:
                self.log(
                    f"      [AVISO]: ARCA entregó el CSV {tipo_comprobante} en {len(originales_csv)} partes "
                    "(el período se pidió en tramos): para Xubio hay que subir cada una."
                )

        if bajar_csv and csv_parciales:
            nombre_csv_final = (
                f"Mis comprobantes - {tipo_comprobante} - {cliente_limpio} - "
                f"{mes_anio_desde} al {mes_anio_hasta}.csv"
            )
            ruta_final_csv = os.path.join(directorio_guardado, nombre_csv_final)
            # Las partes tal cual las mandó ARCA, con el encabezado una sola vez:
            # «Subir a Xubio» sube las líneas de este archivo sin reescribirlas.
            from ..auditor.csv_original import unir_partes

            partes = []
            for archivo_f in csv_parciales:
                with open(archivo_f, "rb") as f_in:
                    partes.append(f_in.read())
            with open(ruta_final_csv, "wb") as master_csv:
                master_csv.write(unir_partes(partes))
            resultado.archivos.append(ruta_final_csv)

        if not resultado.archivos:
            resultado.sin_datos = True
            self.log(f"      [INFO]: ARCA no devolvió comprobantes ({tipo_comprobante}).")

        for archivo_temp in excels_parciales + csv_parciales:
            try:
                if os.path.exists(archivo_temp):
                    os.remove(archivo_temp)
            except Exception:
                pass

        servicio.wait_for_timeout(1500)
        return resultado


def _ruta_libre(carpeta: str, nombre: str) -> str:
    """``carpeta/nombre``; si ya existe (dos tramos en el mismo minuto), «nombre (2)»."""
    ruta = os.path.join(carpeta, nombre)
    base, ext = os.path.splitext(nombre)
    n = 2
    while os.path.exists(ruta):
        ruta = os.path.join(carpeta, f"{base} ({n}){ext}")
        n += 1
    return ruta


def ejecutar_extraccion(
    cuit_login,
    clave,
    cuit_representado,
    fecha_desde,
    fecha_hasta,
    tipo_comprobante,
    nombre_cliente,
    directorio_guardado,
    bajar_excel=True,
    bajar_csv=False,
    log: Callable[[str], None] = print,
) -> BotResult:
    """Descarga un tipo de comprobante en una sesión propia.

    Se mantiene por compatibilidad (app de escritorio y tests). El pipeline web
    reutiliza `SesionArca` para no repetir el login.
    """
    with SesionArca(cuit_login, clave, log=log) as sesion:
        servicio = sesion.abrir_mis_comprobantes(cuit_representado)
        try:
            return sesion.descargar(
                servicio,
                tipo_comprobante,
                fecha_desde,
                fecha_hasta,
                nombre_cliente,
                directorio_guardado,
                bajar_excel=bajar_excel,
                bajar_csv=bajar_csv,
            )
        finally:
            try:
                servicio.close()
            except Exception:
                pass
