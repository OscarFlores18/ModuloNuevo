"""Bot de Xubio - Libro IVA Ventas / Compras.

Reglas del bot:
- No escribe en consola: reporta con `log(mensaje)`.
- No devuelve `None`: devuelve un `BotResult`.
- Ventas y Compras son independientes: si una falla, la otra sigue y el
  error queda informado en `BotResult.errores` (resultado parcial).
- Nunca deja el navegador abierto: el `finally` cierra el browser.

La sesión se abre una sola vez por job y se reutiliza para todos los clientes:
solo se cambia de empresa antes de bajar cada libro.
"""

import os
import re
import time
from typing import Callable
from urllib.parse import urljoin

from playwright.sync_api import Page, sync_playwright

from ..core import config
from ..core.errors import TimeoutDescarga
from ..core.models import BotResult
from .navegador import como_fuente, lanzar, nuevo_contexto
from .xubio_common import cambiar_empresa, crear_matar_popup, enlace_empresa, login_xubio


def procesar_informe_individual(
    page,
    tipo_reporte,
    url_menu,
    selector_iframe,
    nombre_cliente,
    fecha_desde,
    fecha_hasta,
    f_desde_limpia,
    f_hasta_limpia,
    ruta_descarga,
    log: Callable[[str], None] = print,
):
    """Descarga un libro IVA. Devuelve la ruta del Excel o `None` si no hay datos.

    Si Xubio no responde en 3 minutos se lanza `TimeoutDescarga`: eso no es
    "sin datos", es un fallo que el job debe informar.
    """
    log(f"   -> Navegando al Libro {tipo_reporte}...")
    try:
        page.locator('a', has_text='Impuestos').first.hover(timeout=5000)
        page.click(f'a[href="{url_menu}"]', timeout=5000)
    except Exception:
        # El menú no se desplegó (popup encima, ventana chica): se va directo.
        log("      [INFO]: Menú de Impuestos no disponible, se abre el libro por URL.")
        page.goto(urljoin(page.url, url_menu))
    page.mouse.move(0, 0)

    page.wait_for_selector(f"{selector_iframe} iframe", timeout=20000)
    iframe = page.frame_locator(f"{selector_iframe} iframe")

    def escribir_fecha(input_locator, valor):
        input_locator.click()
        input_locator.evaluate(
            "(nodo, valor) => { nodo.value = ''; nodo.value = valor; "
            "nodo.dispatchEvent(new Event('input', { bubbles: true })); "
            "nodo.dispatchEvent(new Event('change', { bubbles: true })); }",
            valor,
        )
        page.wait_for_timeout(100)

    input_desde = iframe.locator("input[formcontrolname='fechaDesdeControl']")
    escribir_fecha(input_desde, fecha_desde)

    input_hasta = iframe.locator("input[formcontrolname='fechaHastaControl']")
    escribir_fecha(input_hasta, fecha_hasta)

    # Combobox "Default"
    try:
        iframe.locator("mat-select[formcontrolname='selectorControl']").click(timeout=5000)
        iframe.locator("mat-option").first.wait_for(timeout=5000)
        cantidad_opciones = iframe.locator("mat-option").count()
        for i in range(cantidad_opciones):
            opcion = iframe.locator("mat-option").nth(i)
            texto = opcion.inner_text().strip()
            if not texto or "Buscar" in texto or "Seleccionar todos" in texto:
                continue

            esta_tildado = opcion.get_attribute("aria-selected") == "true"
            if texto == "default":
                if not esta_tildado:
                    opcion.evaluate("nodo => nodo.click()")
                    page.wait_for_timeout(100)
            else:
                if esta_tildado:
                    opcion.evaluate("nodo => nodo.click()")
                    page.wait_for_timeout(100)

        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
    except Exception as e:
        log(f"      [AVISO]: Falló configuración del filtro default. Detalle: {e}")

    # Checkbox por DOM
    try:
        iframe.locator("mat-checkbox input[type='checkbox']").first.uncheck(
            force=True, timeout=2000
        )
    except Exception:
        pass

    iframe.locator("button:has-text('Aplicar')").click()

    # Exportación Inteligente
    log(f"   -> Esperando respuesta de Xubio para {tipo_reporte}...")
    boton_exportar = iframe.locator('[id="exportar aut_exportar"]')
    texto_vacio = iframe.locator('text="No hay registros para mostrar"')

    hay_datos = False
    esta_vacio = False

    # Se revisa cada 0,25 s hasta 3 min. `count()` primero: `is_enabled()` sobre un
    # botón que todavía no existe se queda esperando 30 s antes de contestar.
    limite = time.monotonic() + 180
    while time.monotonic() < limite:
        if boton_exportar.count() and boton_exportar.is_enabled():
            hay_datos = True
            break
        if texto_vacio.count() and texto_vacio.is_visible():
            esta_vacio = True
            break
        page.wait_for_timeout(250)

    if hay_datos:
        log(f"      [INFO]: Datos encontrados. Descargando Excel de {tipo_reporte}...")
        with page.expect_download() as download_info:
            boton_exportar.click()

        download = download_info.value
        nombre_empresa_seguro = "".join(
            c for c in nombre_cliente if c.isalnum() or c in "._- "
        ).strip()
        nombre_final_archivo = (
            f"IVA {tipo_reporte.upper()} - {nombre_empresa_seguro} - "
            f"{f_desde_limpia} al {f_hasta_limpia}.xlsx"
        )
        ruta_final = os.path.join(ruta_descarga, nombre_final_archivo)

        download.save_as(ruta_final)
        log(f"      [OK]: Guardado: {nombre_final_archivo}")
        return ruta_final

    if esta_vacio:
        log(f"      [INFO]: Sin registros en {tipo_reporte}. Omitiendo.")
        return None

    raise TimeoutDescarga(
        f"Tiempo excedido (3 min) esperando el reporte de {tipo_reporte} en Xubio.",
        detalle={"tipo": tipo_reporte, "cliente": nombre_cliente},
    )


# --- Importación «Desde Mis Comprobantes (ARCA)» ----------------------------------
#
# Camino, igual que a mano: <Compras|Ventas> > Comprobantes de <Compra|Venta> >
# Acciones > Importar > Desde Mis Comprobantes (ARCA). La pantalla trae un iframe
# (app.xubio.com/.../importacion-csv-afip/...) con «Seleccionar archivo» (.zip).
#
# Por ahora el bot sube el .zip y se detiene: NO aprieta «Importar» (prueba).

_IMPORTACION = {
    "Compras": ("Compras", "Comprobantes de Compra", "/NXV/compras/importacion-mis-comprobantes"),
    "Ventas": ("Ventas", "Comprobantes de Venta", "/NXV/ventas/importacion-mis-comprobantes"),
}


def preparar_importacion_arca(
    page: Page,
    operacion: str,
    ruta_zip: str,
    log: Callable[[str], None] = print,
    captura: str | None = None,
) -> dict:
    """Abre la importación de Mis Comprobantes de la operación y carga el .zip,
    sin confirmar. Devuelve {"archivo_cargado": bool, "boton_importar_habilitado": bool}."""
    if operacion not in _IMPORTACION:
        raise ValueError(f"Operación desconocida: {operacion}")
    menu, listado, _ = _IMPORTACION[operacion]

    log(f"   -> Xubio: {menu} > {listado} > Acciones > Importar > Desde Mis Comprobantes (ARCA)...")
    try:
        page.get_by_role("button", name="Rechazarlas todas").click(timeout=1500)
    except Exception:
        pass
    page.locator("a:visible", has_text=re.compile(rf"^\s*{menu}\s*$")).first.click(timeout=10000)
    page.locator("a:visible", has_text=re.compile(rf"^\s*{listado}\s*$", re.I)).first.click(timeout=10000)
    page.mouse.move(0, 0)

    acciones = page.locator("button:visible, a:visible", has_text=re.compile(r"^\s*Acciones\s*$"))
    acciones.first.wait_for(timeout=30000)
    acciones.first.click()
    page.locator("a:visible", has_text=re.compile(r"^\s*Importar\s*$")).first.click(timeout=10000)
    page.locator("li:visible", has_text=re.compile(r"Desde Mis Comprobantes", re.I)).first.click(timeout=10000)

    # El formulario vive en un iframe de app.xubio.com.
    page.wait_for_selector("iframe[src*='importacion-csv-afip']", timeout=30000)
    marco = page.frame_locator("iframe[src*='importacion-csv-afip']")
    entrada = marco.locator("input[type='file']")
    entrada.wait_for(state="attached", timeout=30000)
    entrada.set_input_files(ruta_zip)
    log(f"      [OK]: Archivo cargado: {os.path.basename(ruta_zip)}")

    nombre = os.path.basename(ruta_zip)
    cargado = False
    limite = time.monotonic() + 15
    while time.monotonic() < limite:
        if marco.get_by_text(nombre, exact=False).count():
            cargado = True
            break
        page.wait_for_timeout(250)
    boton = marco.locator("button", has_text=re.compile(r"^\s*Importar\s*$")).first
    habilitado = bool(boton.count()) and boton.is_enabled()
    if captura:
        try:
            page.screenshot(path=captura)
        except Exception:
            pass
    log(
        "      [INFO]: Listo para importar: el bot NO aprieta «Importar» (prueba)."
        if habilitado
        else "      [AVISO]: El botón «Importar» no se habilitó: revisá el archivo en la pantalla."
    )
    return {"archivo_cargado": cargado, "boton_importar_habilitado": habilitado}


class SesionXubio:
    """Una sesión de Chrome ya logueada en Xubio.

    Se abre una vez por job. Cambiar de empresa no vuelve a loguear: solo usa el
    selector de compañía. Se debe usar desde el hilo que la creó.
    """

    def __init__(
        self,
        usuario: str,
        password: str,
        log: Callable[[str], None] = print,
        playwright=None,
    ) -> None:
        self.usuario = usuario or ""
        self._fuente_pass, self._pass_jit = como_fuente(password)
        self.log = log
        # Playwright sync admite uno solo por hilo: si viene de afuera, no es nuestro.
        self._pw_ajeno = playwright
        self._pw = None
        self._browser = None
        self._context = None
        self._page: Page | None = None
        self._matar_popup = None
        self.url_empresa: str | None = None

    def abrir(self) -> "SesionXubio":
        if self._browser is not None:
            return self
        self._pw = self._pw_ajeno or sync_playwright().start()
        try:
            self._browser = lanzar(self._pw)
            self._context = nuevo_contexto(self._browser, self.log, accept_downloads=True)
            self._page = self._context.new_page()
            self._matar_popup = crear_matar_popup(self._page)
            login_xubio(
                self._page,
                self.usuario,
                (self._fuente_pass, self._pass_jit),
                self._matar_popup,
                log=self.log,
            )
        except Exception:
            self.cerrar()
            raise
        return self

    def cerrar(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if not self._pass_jit:
                self._fuente_pass().borrar()
            self._browser = None
            self._context = None
            self._page = None
            self._matar_popup = None
            if self._pw is not None and self._pw is not self._pw_ajeno:
                try:
                    self._pw.stop()
                except Exception:
                    pass
                self._pw = None

    def __enter__(self) -> "SesionXubio":
        return self.abrir()

    def __exit__(self, *exc) -> None:
        self.cerrar()

    def cambiar_empresa(self, nombre_cliente: str) -> str | None:
        """Cambia a la empresa y devuelve su link de Xubio (para «Corregir en Xubio»)."""
        if self._page is None:
            raise RuntimeError("La sesión de Xubio no está abierta.")
        cambiar_empresa(
            self._page, nombre_cliente, self._matar_popup, log=self.log
        )
        try:
            self.url_empresa = enlace_empresa(self._page, nombre_cliente)
        except Exception:
            self.url_empresa = None
        return self.url_empresa

    def preparar_importacion_arca(self, operacion: str, ruta_zip: str, captura: str | None = None) -> dict:
        """En la empresa activa: abre «Importar desde Mis Comprobantes (ARCA)» y
        carga el .zip, sin confirmar."""
        if self._page is None:
            raise RuntimeError("La sesión de Xubio no está abierta.")
        return preparar_importacion_arca(self._page, operacion, ruta_zip, log=self.log, captura=captura)

    def descargar(
        self,
        nombre_cliente: str,
        fecha_desde: str,
        fecha_hasta: str,
        hacer_ventas: bool,
        hacer_compras: bool,
        ruta_descarga: str,
    ) -> BotResult:
        resultado = BotResult()
        if self._page is None:
            raise RuntimeError("La sesión de Xubio no está abierta.")

        f_desde_limpia = fecha_desde.replace("/", "-")
        f_hasta_limpia = fecha_hasta.replace("/", "-")
        os.makedirs(ruta_descarga, exist_ok=True)

        for tipo, url, selector, hacer in (
            ("Ventas", "/NXV/impuestos/libro-iva-ventas", "#libro-iva-ventas", hacer_ventas),
            ("Compras", "/NXV/impuestos/libro-iva-compras", "#libro-iva-compras", hacer_compras),
        ):
            if not hacer:
                continue
            try:
                ruta = procesar_informe_individual(
                    self._page,
                    tipo,
                    url,
                    selector,
                    nombre_cliente,
                    fecha_desde,
                    fecha_hasta,
                    f_desde_limpia,
                    f_hasta_limpia,
                    ruta_descarga,
                    log=self.log,
                )
            except Exception as e:
                self.log(f"      [ERROR]: Falló extracción Xubio {tipo}: {e}")
                resultado.errores.append(
                    {
                        "code": getattr(e, "code", "bot_error"),
                        "mensaje": str(e),
                        "paso": "xubio",
                        "detalle": {"tipo": tipo, "cliente": nombre_cliente},
                    }
                )
                continue

            if ruta:
                resultado.archivos.append(ruta)

        resultado.sin_datos = not resultado.archivos and not resultado.errores
        return resultado


def ejecutar_extraccion(
    usuario,
    password,
    nombre_cliente,
    cliente_url,
    fecha_desde,
    fecha_hasta,
    hacer_ventas,
    hacer_compras,
    ruta_descarga,
    log: Callable[[str], None] = print,
) -> BotResult:
    """Descarga los libros IVA pedidos en una sesión propia.

    Se mantiene por compatibilidad (app de escritorio y tests). El pipeline web
    reutiliza `SesionXubio` para no repetir el login por cliente.
    """
    with SesionXubio(usuario, password, log=log) as sesion:
        sesion.cambiar_empresa(nombre_cliente)
        return sesion.descargar(
            nombre_cliente,
            fecha_desde,
            fecha_hasta,
            hacer_ventas,
            hacer_compras,
            ruta_descarga,
        )
