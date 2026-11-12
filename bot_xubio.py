import os
from playwright.sync_api import sync_playwright, expect
from bot_xubio_common import crear_matar_popup, login_xubio, cambiar_empresa

def procesar_informe_individual(page, tipo_reporte, url_menu, selector_iframe, nombre_cliente, fecha_desde, fecha_hasta, f_desde_limpia, f_hasta_limpia, ruta_descarga):
    print(f"   -> Navegando al Libro {tipo_reporte}...")
    page.locator('a', has_text='Impuestos').first.hover()
    page.wait_for_timeout(1000) 
    page.click(f'a[href="{url_menu}"]')
    page.mouse.move(0, 0)
    
    page.wait_for_selector(f"{selector_iframe} iframe", timeout=20000)
    iframe = page.frame_locator(f"{selector_iframe} iframe")

    # Fechas
    # Inyección de fechas blindada vía JS (value + eventos input/change), en
    # vez de Control+A + Backspace + fill(). El método anterior dependía de
    # que Control+A cayera justo en el campo correcto: es una tecla global de
    # `page`, no atada a un input puntual, así que su efecto depende del foco
    # real del navegador en ese instante exacto. En una PC más lenta (o con
    # más latencia de render) ese timing podía fallar y una de las dos fechas
    # quedaba vacía o a medio escribir mientras el foco ya se movía al otro
    # campo. Mismo método ya probado en bot_xubio_liq.py (llenar_fechas_fragmentadas).
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
                if not esta_tildado: opcion.evaluate("nodo => nodo.click()"); page.wait_for_timeout(200)
            else:
                if esta_tildado: opcion.evaluate("nodo => nodo.click()"); page.wait_for_timeout(200)
        
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
    except Exception as e:
        print(f"      [AVISO]: Falló configuración del filtro default. Detalle: {e}")

    # Checkbox por DOM
    try:
        iframe.locator("mat-checkbox input[type='checkbox']").first.uncheck(force=True, timeout=2000)
    except:
        pass 

    iframe.locator("button:has-text('Aplicar')").click()
    
    # Exportación Inteligente
    print(f"   -> Esperando respuesta de Xubio para {tipo_reporte}...")
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
        print(f"      ¡Datos encontrados! Descargando Excel de {tipo_reporte}...")
        with page.expect_download() as download_info:
            boton_exportar.click() 
            
        download = download_info.value
        nombre_empresa_seguro = "".join(c for c in nombre_cliente if c.isalnum() or c in "._- ").strip()
        nombre_final_archivo = f"IVA {tipo_reporte.upper()} - {nombre_empresa_seguro} - {f_desde_limpia} al {f_hasta_limpia}.xlsx"
        ruta_final = os.path.join(ruta_descarga, nombre_final_archivo)
        
        download.save_as(ruta_final)
        print(f"      ✅ Guardado: {nombre_final_archivo}")
        
    elif esta_vacio:
        print(f"      [INFO]: Sin registros en {tipo_reporte}. Omitiendo.")
    else:
        print(f"      [ERROR]: Tiempo excedido (3 min) en {tipo_reporte}. Omitiendo.")

def ejecutar_extraccion(usuario, password, nombre_cliente, cliente_url, fecha_desde, fecha_hasta, hacer_ventas, hacer_compras, ruta_descarga):
    """ Función principal que será llamada por el Orquestador """
    f_desde_limpia = fecha_desde.replace("/", "-")
    f_hasta_limpia = fecha_hasta.replace("/", "-")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        matar_popup = crear_matar_popup(page)

        try:
            login_xubio(page, usuario, password, matar_popup)
            cambiar_empresa(page, nombre_cliente, matar_popup)

            # Ejecución de Reportes (Con escudos independientes)
            if hacer_ventas:
                try:
                    procesar_informe_individual(page, "Ventas", "/NXV/impuestos/libro-iva-ventas", "#libro-iva-ventas", nombre_cliente, fecha_desde, fecha_hasta, f_desde_limpia, f_hasta_limpia, ruta_descarga)
                except Exception as e:
                    print(f"      [ERROR]: Falló extracción Xubio Ventas: {e}")
                    
            if hacer_compras:
                try:
                    procesar_informe_individual(page, "Compras", "/NXV/impuestos/libro-iva-compras", "#libro-iva-compras", nombre_cliente, fecha_desde, fecha_hasta, f_desde_limpia, f_hasta_limpia, ruta_descarga)
                except Exception as e:
                    print(f"      [ERROR]: Falló extracción Xubio Compras: {e}")

        finally:
            browser.close()