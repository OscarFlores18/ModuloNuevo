"""Bot de ARCA (ex AFIP) - "Mis Comprobantes".

Reglas del bot:
- No escribe en consola ni en GUI: reporta con `log(mensaje)`.
- No devuelve `None`: devuelve un `BotResult` con los archivos generados.
- Si AFIP no entrega un rango, segmenta fechas automáticamente.
- Si ni a 15 días hay datos, falla con `TimeoutDescarga` (error tipado).
- Nunca deja el navegador abierto: el `finally` cierra el browser.
"""

import os
import re
from datetime import datetime, timedelta
from typing import Callable

import openpyxl
from playwright.sync_api import sync_playwright

from ..core.errors import BotError, TimeoutDescarga
from ..core.models import BotResult


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
    """Descarga los comprobantes de ARCA y devuelve los archivos generados.

    `tipo_comprobante` es "Emitidos" o "Recibidos". El resultado incluye las
    rutas absolutas de los archivos consolidados y `sin_datos=True` cuando el
    período no tenía comprobantes.
    """
    resultado = BotResult()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome", args=["--start-maximized"])
        context = browser.new_context(no_viewport=True)
        context.accept_downloads = True
        page = context.new_page()

        def manejar_dialogo(dialog):
            # Si ARCA muestra un diálogo nativo del navegador (alert/confirm),
            # Playwright se queda esperando una respuesta manual para siempre
            # y el bot parece "colgado" sin ningún error en el log. Esto lo
            # cierra solo apenas aparece, en cualquier pestaña.
            log(f"      [AVISO]: ARCA mostró un diálogo del navegador ('{dialog.message}'). Se cerró automáticamente.")
            try:
                dialog.dismiss()
            except:
                pass
        page.on("dialog", manejar_dialogo)

        try:
            # Login ARCA
            page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml")
            page.fill("id=F1:username", cuit_login)
            page.click("id=F1:btnSiguiente")
            page.fill("id=F1:password", clave)
            page.click("id=F1:btnIngresar")
            
            page.wait_for_selector("#buscadorInput", timeout=30000)
            page.wait_for_timeout(500)
            
            buscador = page.locator("#buscadorInput")
            buscador.click()
            buscador.press_sequentially("Mis Comprobantes", delay=30)
            
            page.wait_for_timeout(800)
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(200)
            
            with context.expect_page() as nueva_pestana_info:
                page.keyboard.press("Enter")
            nueva_pestana = nueva_pestana_info.value
            nueva_pestana.on("dialog", manejar_dialogo)

            # Selección de perfil
            try:
                titulo_eleccion = nueva_pestana.get_by_text("Elegí una persona para ingresar", exact=False)
                titulo_eleccion.wait_for(state="visible", timeout=2500)
                cuit_formateado = f"{cuit_representado[:2]}-{cuit_representado[2:10]}-{cuit_representado[10:]}"
                tarjeta_empresa = nueva_pestana.locator(f"a:has-text('{cuit_formateado}'), a:has-text('{cuit_representado}')").first
                tarjeta_empresa.click()
                page.wait_for_timeout(500)
            except Exception as e:
                # Antes esto se tragaba en silencio (except: pass). Si la
                # pantalla de selección de perfil aparece pero no se pudo
                # completar (ej. el CUIT representado no matchea ninguna
                # tarjeta), ahora al menos queda un rastro en el log.
                log(f"      [INFO]: Selección de perfil no aplicó o no se pudo completar: {e}")

            # Botón Emitidos/Recibidos.
            # Se sacó la espera "inteligente" (click con timeout largo +
            # wait_for_selector de #fechaEmision con timeout largo): en la
            # práctica esos timeouts generosos se estaban consumiendo casi
            # por completo en vez de resolver rápido, y se sentía como una
            # espera fija de ~20-30 segundos. Ahora es un click corto, con
            # un único respaldo por JS si falla, y una pausa fija breve de
            # 1,5 seg antes de seguir — nada que "detectar", como se pidió.
            boton_id = "#btnEmitidos" if tipo_comprobante == "Emitidos" else "#btnRecibidos"
            boton_tipo = nueva_pestana.locator(boton_id).first
            log(f"   -> Apretando botón '{tipo_comprobante}'...")
            try:
                boton_tipo.click(timeout=5000)
            except Exception as e:
                log(f"      [AVISO]: Click normal no llegó a tiempo ({e}). Forzando con JS...")
                try:
                    boton_tipo.evaluate("el => el.click()", timeout=3000)
                except Exception as e2:
                    log(f"      [ERROR]: No se pudo apretar el botón '{tipo_comprobante}': {e2}")

            nueva_pestana.wait_for_timeout(1500)

            # NOTA: "#fechaEmision" es un campo de filtro que está presente
            # tanto en la vista de Emitidos como en la de Recibidos (es el
            # mismo formulario de búsqueda para las dos), así que no sirve
            # para confirmar que el click cambió de pestaña. Este log de
            # diagnóstico queda para poder confirmar en el log si el botón
            # realmente quedó marcado como activo, sin bloquear la ejecución.
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
                log(f"      [DEBUG]: Estado de '{tipo_comprobante}' tras click: {info_boton}")
            except Exception:
                pass

            fecha_desde_actual = fecha_desde
            fecha_hasta_actual = fecha_hasta
            excels_parciales = []
            csv_parciales = []
            primer_vuelta = True

            while datetime.strptime(fecha_desde_actual, "%d/%m/%Y") <= datetime.strptime(fecha_hasta, "%d/%m/%Y"):

                # El botón Emitidos/Recibidos ya se apretó una vez antes de
                # entrar acá (con su verificación y reintento); en las
                # vueltas siguientes del loop (siguiente tramo de fechas) no
                # hace falta volver a tocarlo, alcanza con esperar el campo
                # de fecha ya visible.
                if not primer_vuelta:
                    nueva_pestana.wait_for_selector("#fechaEmision", timeout=20000)
                primer_vuelta = False
                rango_fechas = f"{fecha_desde_actual} - {fecha_hasta_actual}"
                log(f"   -> Solicitando periodo en ARCA: {rango_fechas}")
                
                # ========================================================================
                # TÁCTICA DEL TRIPLE CLIC Y ESPERA INTELIGENTE (LA QUE FUNCIONÓ BIEN)
                # Total de esperas de este bloque (tipeo de fechas) fijado en
                # 2 segundos a pedido, repartido en 4 pausas de checkpoint en
                # vez de 6 más finitas. Si alguna fecha vuelve a cargar mal o
                # incompleta, avisar para reveer estos tiempos puntuales.
                # ========================================================================
                caja_fecha = nueva_pestana.locator("#fechaEmision")

                # 1. Clic simple para poner el foco y esperar a que AFIP escupa su texto por default
                caja_fecha.click(timeout=3000)
                page.wait_for_timeout(800)

                # 2. Triple clic (selecciona todo el texto cargado) y borrado
                caja_fecha.click(click_count=3, timeout=3000)
                page.wait_for_timeout(200)
                page.keyboard.press("Backspace")
                page.wait_for_timeout(200)

                # 3. Disparamos el texto como metralleta para que no clone
                caja_fecha.press_sequentially(rango_fechas, delay=5)
                page.wait_for_timeout(400)

                # 4. Sellamos con el Enter
                caja_fecha.press("Enter")
                page.wait_for_timeout(400)

                # 6. Scroll y Buscar (UN SOLO CLIC LIMPIO)
                boton_buscar = nueva_pestana.locator("#buscarComprobantes")
                boton_buscar.scroll_into_view_if_needed()
                page.wait_for_timeout(150)

                boton_buscar.click(timeout=3000)
                page.wait_for_timeout(700)
                # ========================================================================
                
                # --- PROCESO DE DESCARGA ---
                if bajar_csv and not bajar_excel:
                    try:
                        boton_csv = nueva_pestana.wait_for_selector('button[title="Exportar como CSV"], button:has-text("CSV")', state="visible", timeout=25000)
                        with nueva_pestana.expect_download() as download_info:
                            boton_csv.click()
                        download = download_info.value
                        nombre_temporal = f"Parcial_{len(csv_parciales)+1}_{download.suggested_filename}"
                        ruta_parcial = os.path.join(directorio_guardado, nombre_temporal)
                        download.save_as(ruta_parcial)
                        csv_parciales.append(ruta_parcial)
                        
                        fecha_desde_actual = sumar_un_dia(fecha_hasta_actual)
                        fecha_hasta_actual = fecha_hasta
                        if datetime.strptime(fecha_desde_actual, "%d/%m/%Y") > datetime.strptime(fecha_hasta, "%d/%m/%Y"):
                            break
                    except Exception as e:
                        raise TimeoutDescarga(
                            f"Error descargando CSV en ARCA: {e}", detalle={"tipo": tipo_comprobante}
                        ) from e
                        
                    nueva_pestana.click('a[href="#tabConsulta"]')
                    page.wait_for_timeout(1000)
                    continue

                try:
                    boton_excel = nueva_pestana.wait_for_selector("button.buttons-excel", state="visible", timeout=20000)
                    
                    with nueva_pestana.expect_download() as download_info:
                        boton_excel.click()
                    download = download_info.value
                    nombre_temp_excel = f"ParcialExcel_{len(excels_parciales)+1}_{download.suggested_filename}"
                    ruta_parcial_excel = os.path.join(directorio_guardado, nombre_temp_excel)
                    download.save_as(ruta_parcial_excel)
                    excels_parciales.append(ruta_parcial_excel)
                    
                    if bajar_csv:
                        boton_csv = nueva_pestana.locator('button[title="Exportar como CSV"], button:has-text("CSV")').first
                        if boton_csv.is_visible():
                            with nueva_pestana.expect_download() as download_info_csv:
                                boton_csv.click()
                            download_c = download_info_csv.value
                            nombre_temp_csv = f"ParcialCSV_{len(csv_parciales)+1}_{download_c.suggested_filename}"
                            ruta_parcial_csv = os.path.join(directorio_guardado, nombre_temp_csv)
                            download_c.save_as(ruta_parcial_csv)
                            csv_parciales.append(ruta_parcial_csv)
                    
                    fecha_desde_actual = sumar_un_dia(fecha_hasta_actual)
                    fecha_hasta_actual = fecha_hasta
                    
                    if datetime.strptime(fecha_desde_actual, "%d/%m/%Y") > datetime.strptime(fecha_hasta, "%d/%m/%Y"):
                        break 
                        
                    nueva_pestana.click('a[href="#tabConsulta"]')
                    page.wait_for_timeout(1000)
                    
                except Exception:
                    d_desde = datetime.strptime(fecha_desde_actual, "%d/%m/%Y")
                    d_hasta = datetime.strptime(fecha_hasta_actual, "%d/%m/%Y")
                    dias_rango = (d_hasta - d_desde).days + 1
                    
                    log(f"      [INFO]: ARCA congestionado (Rango: {dias_rango} días). Segmentando...")
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

                    if datetime.strptime(fecha_hasta_actual, "%d/%m/%Y") < datetime.strptime(fecha_desde_actual, "%d/%m/%Y"):
                        raise BotError(
                            "Fallo en reducción matemática de fechas.",
                            detalle={
                                "tipo": tipo_comprobante,
                                "desde": fecha_desde_actual,
                                "hasta": fecha_hasta_actual,
                            },
                        )
                        
                    nueva_pestana.click('a[href="#tabConsulta"]')
                    page.wait_for_timeout(1000)

            # --- UNIFICACIÓN ---
            mes_anio_desde = formatear_mes_anio(fecha_desde)
            mes_anio_hasta = formatear_mes_anio(fecha_hasta)
            cliente_limpio = limpiar_nombre(nombre_cliente)
            
            if bajar_excel and excels_parciales:
                nombre_archivo_final = f"Mis comprobantes - {tipo_comprobante} - {cliente_limpio} - {mes_anio_desde} al {mes_anio_hasta}.xlsx"
                ruta_final = os.path.join(directorio_guardado, nombre_archivo_final)
                unificar_excels(excels_parciales, ruta_final)
                resultado.archivos.append(ruta_final)
                log(f"      [OK]: Consolidado: {nombre_archivo_final}")

            if bajar_csv and csv_parciales:
                nombre_csv_final = f"Mis comprobantes - {tipo_comprobante} - {cliente_limpio} - {mes_anio_desde} al {mes_anio_hasta}.csv"
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
                log(f"      [INFO]: ARCA no devolvió comprobantes ({tipo_comprobante}).")

            # Limpieza de basura
            for archivo_temp in excels_parciales + csv_parciales:
                try:
                    if os.path.exists(archivo_temp): os.remove(archivo_temp)
                except: pass

            page.wait_for_timeout(1500)

        finally:
            browser.close()

    return resultado
