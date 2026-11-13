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
from ..core.errors import BotError, SesionVencida, TimeoutDescarga
from ..core.models import BotResult

# Marcas con las que ARCA contesta cuando la sesión ya no vale.
MARCAS_SESION_VENCIDA = ("MISSING_AUTHENTICATION_INFO", "HTTP Status 401", "F1:username")

URL_LOGIN = "https://auth.afip.gob.ar/contribuyente_/login.xhtml"
URL_PORTAL = "https://portalcf.cloud.afip.gob.ar/portal/app/"
SERVICIO = "Mis Comprobantes"


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

def sumar_un_dia(fecha_str):
    d = datetime.strptime(fecha_str, "%d/%m/%Y") + timedelta(days=1)
    return d.strftime("%d/%m/%Y")

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
        self.clave = clave or ""
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
            opciones: dict[str, object] = {"headless": config.PLAYWRIGHT_HEADLESS}
            if config.PLAYWRIGHT_CHANNEL:
                opciones["channel"] = config.PLAYWRIGHT_CHANNEL
            if not config.PLAYWRIGHT_HEADLESS:
                opciones["args"] = ["--start-maximized"]
            self._browser = self._pw.chromium.launch(**opciones)
            self._context = self._browser.new_context(
                no_viewport=not config.PLAYWRIGHT_HEADLESS
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

    def _login(self) -> None:
        if self._portal is None or self._portal.is_closed():
            self._portal = self._context.new_page()
            self._portal.on("dialog", self._manejar_dialogo)

        page = self._portal
        self.log("   -> Iniciando sesión en ARCA...")
        page.goto(URL_LOGIN)
        page.fill("id=F1:username", self.cuit_login)
        page.click("id=F1:btnSiguiente")
        page.fill("id=F1:password", self.clave)
        page.click("id=F1:btnIngresar")

        page.wait_for_selector("#buscadorInput", timeout=30000)
        page.wait_for_timeout(500)
        self._login_ts = time.monotonic()
        self._ultimo_ping = self._login_ts
        self.log("      [OK]: Sesión de ARCA iniciada.")

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
            respuesta = self._context.request.get(URL_PORTAL, timeout=15000)
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
            if "login" in url or "auth.afip" in url:
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
            portal.goto(URL_PORTAL)
            portal.wait_for_selector("#buscadorInput", timeout=30000)
            portal.wait_for_timeout(500)

        buscador = portal.locator("#buscadorInput")
        buscador.click()
        buscador.press_sequentially(SERVICIO, delay=30)

        portal.wait_for_timeout(800)
        portal.keyboard.press("ArrowDown")
        portal.wait_for_timeout(200)

        with self._context.expect_page() as nueva_pestana_info:
            portal.keyboard.press("Enter")
        servicio = nueva_pestana_info.value
        servicio.on("dialog", self._manejar_dialogo)

        # Selección de perfil (si ARCA la pide).
        try:
            titulo_eleccion = servicio.get_by_text(
                "Elegí una persona para ingresar", exact=False
            )
            titulo_eleccion.wait_for(state="visible", timeout=2500)
            cuit_limpio = (cuit_representado or "").strip()
            cuit_formateado = (
                f"{cuit_limpio[:2]}-{cuit_limpio[2:10]}-{cuit_limpio[10:]}"
                if len(cuit_limpio) == 11
                else cuit_limpio
            )
            tarjeta_empresa = servicio.locator(
                f"a:has-text('{cuit_formateado}'), a:has-text('{cuit_limpio}')"
            ).first
            tarjeta_empresa.click()
            servicio.wait_for_timeout(500)
        except Exception as exc:
            self.log(
                f"      [INFO]: Selección de perfil no aplicó o no se pudo "
                f"completar: {exc}"
            )

        try:
            servicio.wait_for_selector("#btnEmitidos", state="visible", timeout=20000)
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
        try:
            boton_tipo.click(timeout=5000)
        except Exception as exc:
            self.log(f"      [AVISO]: Click normal no llegó a tiempo ({exc}). Forzando con JS...")
            try:
                boton_tipo.evaluate("el => el.click()", timeout=3000)
            except Exception as exc2:
                self.log(
                    f"      [ERROR]: No se pudo apretar el botón "
                    f"'{tipo_comprobante}': {exc2}"
                )

        servicio.wait_for_timeout(1500)

        try:
            info_boton = boton_tipo.evaluate(
                """(el) => {
                    const li = el.closest('li');
                    const contenedor = li || el;
                    return JSON.stringify({
                        clases_boton: el.className,
                        aria_selected: el.getAttribute('aria-selected'),
                        clases_contenedor: contenedor.className,
                    });
                }""",
                timeout=2000,
            )
            self.log(f"      [DEBUG]: Estado de '{tipo_comprobante}' tras click: {info_boton}")
        except Exception:
            pass

        fecha_desde_actual = fecha_desde
        fecha_hasta_actual = fecha_hasta
        excels_parciales: list[str] = []
        csv_parciales: list[str] = []
        primer_vuelta = True

        while datetime.strptime(fecha_desde_actual, "%d/%m/%Y") <= datetime.strptime(
            fecha_hasta, "%d/%m/%Y"
        ):
            if not primer_vuelta:
                servicio.wait_for_selector("#fechaEmision", timeout=20000)
            primer_vuelta = False
            rango_fechas = f"{fecha_desde_actual} - {fecha_hasta_actual}"
            self.log(f"   -> Solicitando periodo en ARCA: {rango_fechas}")

            caja_fecha = servicio.locator("#fechaEmision")

            caja_fecha.click(timeout=3000)
            servicio.wait_for_timeout(800)

            caja_fecha.click(click_count=3, timeout=3000)
            servicio.wait_for_timeout(200)
            servicio.keyboard.press("Backspace")
            servicio.wait_for_timeout(200)

            caja_fecha.press_sequentially(rango_fechas, delay=5)
            servicio.wait_for_timeout(400)

            caja_fecha.press("Enter")
            servicio.wait_for_timeout(400)

            boton_buscar = servicio.locator("#buscarComprobantes")
            boton_buscar.scroll_into_view_if_needed()
            servicio.wait_for_timeout(150)

            boton_buscar.click(timeout=3000)
            servicio.wait_for_timeout(700)

            # --- PROCESO DE DESCARGA ---
            if bajar_csv and not bajar_excel:
                try:
                    boton_csv = servicio.wait_for_selector(
                        'button[title="Exportar como CSV"], button:has-text("CSV")',
                        state="visible",
                        timeout=25000,
                    )
                    with servicio.expect_download() as download_info:
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
                    "button.buttons-excel", state="visible", timeout=20000
                )

                with servicio.expect_download() as download_info:
                    boton_excel.click()
                download = download_info.value
                nombre_temp_excel = f"ParcialExcel_{len(excels_parciales)+1}_{download.suggested_filename}"
                ruta_parcial_excel = os.path.join(directorio_guardado, nombre_temp_excel)
                download.save_as(ruta_parcial_excel)
                excels_parciales.append(ruta_parcial_excel)

                if bajar_csv:
                    boton_csv = servicio.locator(
                        'button[title="Exportar como CSV"], button:has-text("CSV")'
                    ).first
                    if boton_csv.is_visible():
                        with servicio.expect_download() as download_info_csv:
                            boton_csv.click()
                        download_c = download_info_csv.value
                        nombre_temp_csv = f"ParcialCSV_{len(csv_parciales)+1}_{download_c.suggested_filename}"
                        ruta_parcial_csv = os.path.join(directorio_guardado, nombre_temp_csv)
                        download_c.save_as(ruta_parcial_csv)
                        csv_parciales.append(ruta_parcial_csv)

                fecha_desde_actual = sumar_un_dia(fecha_hasta_actual)
                fecha_hasta_actual = fecha_hasta

                if datetime.strptime(fecha_desde_actual, "%d/%m/%Y") > datetime.strptime(
                    fecha_hasta, "%d/%m/%Y"
                ):
                    break

                servicio.click('a[href="#tabConsulta"]')
                servicio.wait_for_timeout(1000)

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
            self.log(f"      [OK]: Consolidado: {nombre_archivo_final}")

        if bajar_csv and csv_parciales:
            nombre_csv_final = (
                f"Mis comprobantes - {tipo_comprobante} - {cliente_limpio} - "
                f"{mes_anio_desde} al {mes_anio_hasta}.csv"
            )
            ruta_final_csv = os.path.join(directorio_guardado, nombre_csv_final)
            with open(ruta_final_csv, "wb") as master_csv:
                for idx, archivo_f in enumerate(csv_parciales):
                    with open(archivo_f, "rb") as f_in:
                        contenido = f_in.read()
                        if idx == 0:
                            master_csv.write(contenido)
                        else:
                            lineas = contenido.split(b"\n")
                            if len(lineas) > 2:
                                master_csv.write(b"\n".join(lineas[2:]))
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
