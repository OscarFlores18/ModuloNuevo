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
from typing import Callable

from playwright.sync_api import Page, sync_playwright

from ..core.errors import TimeoutDescarga
from ..core.models import BotResult
from .xubio_common import cambiar_empresa, crear_matar_popup, login_xubio


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
    page.locator('a', has_text='Impuestos').first.hover()
    page.wait_for_timeout(1000)
    page.click(f'a[href="{url_menu}"]')
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
        page.wait_for_timeout(300)

    input_desde = iframe.locator("input[formcontrolname='fechaDesdeControl']")
    escribir_fecha(input_desde, fecha_desde)

    input_hasta = iframe.locator("input[formcontrolname='fechaHastaControl']")
    escribir_fecha(input_hasta, fecha_hasta)

    # Combobox "Default"
    try:
        iframe.locator("mat-select[formcontrolname='selectorControl']").click(timeout=5000)
        page.wait_for_timeout(800)
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
                    page.wait_for_timeout(200)
            else:
                if esta_tildado:
                    opcion.evaluate("nodo => nodo.click()")
                    page.wait_for_timeout(200)

        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
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

    for _ in range(180):
        if boton_exportar.is_enabled():
            hay_datos = True
            break
        if texto_vacio.is_visible():
            esta_vacio = True
            break
        page.wait_for_timeout(1000)

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
    ) -> None:
        self.usuario = usuario or ""
        self.password = password or ""
        self.log = log
        self._pw = None
        self._browser = None
        self._context = None
        self._page: Page | None = None
        self._matar_popup = None

    def abrir(self) -> "SesionXubio":
        if self._browser is not None:
            return self
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=False, channel="chrome")
            self._context = self._browser.new_context(accept_downloads=True)
            self._page = self._context.new_page()
            self._matar_popup = crear_matar_popup(self._page)
            login_xubio(
                self._page,
                self.usuario,
                self.password,
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
            self._browser = None
            self._context = None
            self._page = None
            self._matar_popup = None
            if self._pw is not None:
                try:
                    self._pw.stop()
                except Exception:
                    pass
                self._pw = None

    def __enter__(self) -> "SesionXubio":
        return self.abrir()

    def __exit__(self, *exc) -> None:
        self.cerrar()

    def cambiar_empresa(self, nombre_cliente: str) -> None:
        if self._page is None:
            raise RuntimeError("La sesión de Xubio no está abierta.")
        cambiar_empresa(
            self._page, nombre_cliente, self._matar_popup, log=self.log
        )

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
