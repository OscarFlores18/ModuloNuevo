"""
Funciones compartidas por bot_xubio.py y bot_xubio_liq.py: login en Xubio
(via connect.visma.com) y cambio de empresa dentro de la sesión.

Se centralizaron acá porque antes vivían duplicadas en ambos archivos.
Cada vez que Xubio cambia el HTML de su selector de compañía (ya pasó más
de una vez, ver claude/diagnostico-bug-fecha-anual.md), alcanza con
corregir este único archivo en vez de dos copias que pueden desalinearse.
"""


def crear_matar_popup(page):
    """
    Devuelve una función que cierra el popup de Xubio (el botón "x" que a
    veces tapa la pantalla) si está visible en ese momento. Se pasa como
    callback a login_xubio() y cambiar_empresa() porque el popup puede
    reaparecer en distintos puntos del flujo.
    """
    def matar_popup():
        try:
            boton_x = page.locator('span:text-is("x")').first
            if boton_x.is_visible(timeout=1000):
                boton_x.click()
        except:
            pass
    return matar_popup


def login_xubio(page, usuario, password, matar_popup):
    """ Login completo contra connect.visma.com / Xubio. """
    page.goto("https://connect.visma.com/?returnUrl=%2Fconnect%2Fauthorize%2Fcallback%3Fredirect_uri%3Dhttps%253A%252F%252Fxubio.com%252FNXV%252FvismaConnect%252Fcallback%26response_type%3Dcode%2520id_token%26scope%3Dopenid%2520email%26response_mode%3Dform_post%26client_id%3Dxubio%26state%3Dxubio%26nonce%3D6890436f8d7e8befea5fd4302e574b5d")
    try:
        boton_cookies = page.wait_for_selector("#onetrust-accept-btn-handler", timeout=5000)
        if boton_cookies:
            boton_cookies.click()
    except:
        pass

    page.wait_for_selector("#Username")
    page.fill("#Username", usuario)
    page.click(".form-login-button-email")
    page.wait_for_selector("#Password", state="visible")
    page.fill("#Password", password)
    page.click(".form-login-button-password")

    try:
        boton_continuar_inicio = page.wait_for_selector('button:has-text("Continuar inicio")', timeout=4000)
        if boton_continuar_inicio:
            boton_continuar_inicio.click()
    except:
        pass

    # ESPERA MÁS ROBUSTA PARA PCs LENTAS (Espera a que el tráfico de red se calme)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except:
        pass
    page.wait_for_timeout(2000)
    matar_popup()


def cambiar_empresa(page, nombre_cliente, matar_popup):
    """
    Verifica la empresa activa en Xubio y, si no coincide con nombre_cliente,
    la cambia usando el buscador "Buscar Empresa" del selector de compañía
    (arriba a la izquierda, junto al nombre de la empresa actual).

    ANTES: se clickeaba directamente `a[href="{cliente_url}"]`, matcheando
    la URL guardada en config.json contra el href real del link en el
    desplegable. Se cambió a búsqueda por nombre por dos motivos:
      1) Xubio rediseñó el selector de compañía y agregó un campo de
         búsqueda adentro del desplegable; el desplegable dejó de abrirse
         de forma confiable solo con hover, lo que hacía que el bot se
         quedara esperando sin avanzar (timeout esperando el click).
      2) Una URL vieja/desactualizada en config.json para un cliente
         puntual (ya pasó) rompe el cambio de empresa SOLO para ese
         cliente, de forma silenciosa. Buscar por nombre no depende de que
         esa URL siga vigente.
    """
    # 1) Leer la empresa actualmente activa
    page.wait_for_selector('#resaltarCompania', state="visible", timeout=30000)
    empresa_actual = page.locator('#resaltarCompania').first.inner_text().strip()

    ya_esta_correcta = (
        nombre_cliente.lower() in empresa_actual.lower()
        or empresa_actual.lower() in nombre_cliente.lower()
    )
    if ya_esta_correcta:
        return

    matar_popup()

    # 2) Abrir el selector de compañía.
    # Se combina hover + click porque no se puede confirmar de antemano si
    # Xubio lo dejó como CSS :hover puro o si ahora requiere click (por el
    # campo de búsqueda interactivo que agregó adentro del desplegable).
    # Hacer ambos cubre los dos casos sin efectos secundarios: el link
    # #resaltarCompania no tiene href propio, así que un click de más no
    # dispara ninguna navegación.
    selector_compania = page.locator('.compania').first
    selector_compania.hover()
    selector_compania.click()
    page.wait_for_timeout(500)

    buscador = page.locator('#buscador-empresa')
    try:
        buscador.wait_for(state="visible", timeout=8000)
    except:
        # Reintento: por si el primer click no llegó a abrir el desplegable
        # (por ejemplo, el popup promocional interceptó el click)
        matar_popup()
        selector_compania.hover()
        selector_compania.click()
        buscador.wait_for(state="visible", timeout=10000)

    # 3) Filtrar por nombre y ubicar el resultado en la lista filtrada.
    # Si el nombre completo no trae resultados (diferencias de puntuación,
    # mayúsculas o espacios contra el nombre mostrado en Xubio), se
    # reintenta filtrando solo por la primera palabra del nombre.
    def filtrar_y_ubicar(texto_busqueda):
        buscador.click()
        buscador.fill("")
        buscador.fill(texto_busqueda)
        page.wait_for_timeout(800)  # espera a que el filtro JS actualice la lista
        opciones = page.locator('#compania-lista li.licomp a')
        for i in range(opciones.count()):
            opcion = opciones.nth(i)
            texto = opcion.inner_text().strip()
            if texto and (nombre_cliente.lower() in texto.lower() or texto.lower() in nombre_cliente.lower()):
                return opcion
        return None

    objetivo = filtrar_y_ubicar(nombre_cliente)
    if objetivo is None:
        objetivo = filtrar_y_ubicar(nombre_cliente.split()[0])

    if objetivo is None:
        raise Exception(
            f"No se encontró la empresa '{nombre_cliente}' en el buscador de compañías de "
            f"Xubio. Verificar que el nombre en config.json coincida con el nombre real en Xubio."
        )

    try:
        objetivo.click(timeout=4000)
    except:
        # Si falla, es porque el popup interceptó el click en ese preciso milisegundo
        print("      [AVISO]: Popup interceptó el click. Matando y reintentando...")
        matar_popup()
        objetivo.click(timeout=10000)

    # 4) Esperar a que cargue la nueva empresa
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except:
        pass
    page.wait_for_timeout(2000)
    matar_popup()
