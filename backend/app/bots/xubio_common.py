"""
Funciones compartidas por bot_xubio.py y bot_xubio_liq.py: login en Xubio
(via connect.visma.com) y cambio de empresa dentro de la sesión.

Se centralizaron acá porque antes vivían duplicadas en ambos archivos.
Cada vez que Xubio cambia el HTML de su selector de compañía (ya pasó más
de una vez, ver claude/diagnostico-bug-fecha-anual.md), alcanza con
corregir este único archivo en vez de dos copias que pueden desalinearse.
"""

import time
from typing import Callable

from ..core.errors import EmpresaNoEncontrada


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


def login_xubio(page, usuario, password, matar_popup, log: Callable[[str], None] = print):
    """ Login completo contra connect.visma.com / Xubio. """
    log(f"   -> Iniciando sesión en Xubio con el usuario {usuario}...")
    page.goto("https://connect.visma.com/?returnUrl=%2Fconnect%2Fauthorize%2Fcallback%3Fredirect_uri%3Dhttps%253A%252F%252Fxubio.com%252FNXV%252FvismaConnect%252Fcallback%26response_type%3Dcode%2520id_token%26scope%3Dopenid%2520email%26response_mode%3Dform_post%26client_id%3Dxubio%26state%3Dxubio%26nonce%3D6890436f8d7e8befea5fd4302e574b5d")
    cookies = page.locator("#onetrust-accept-btn-handler")

    def aceptar_cookies():
        try:
            if cookies.is_visible():
                cookies.click(timeout=2000)
        except Exception:
            pass

    # Sin esperas fijas: se sigue apenas aparece el campo de usuario (o el cartel de cookies).
    page.locator("#Username").or_(cookies).first.wait_for(timeout=30000)
    aceptar_cookies()
    page.fill("#Username", usuario)
    try:
        page.click(".form-login-button-email", timeout=5000)
    except Exception:
        # El cartel de cookies llegó tarde y tapa el botón.
        aceptar_cookies()
        page.click(".form-login-button-email")
    page.wait_for_selector("#Password", state="visible")
    page.fill("#Password", password)
    page.click(".form-login-button-password")

    # Se espera lo que llegue primero: «Continuar inicio» (a veces) o Xubio ya adentro.
    adentro = page.locator("#resaltarCompania")
    continuar = page.locator('button:has-text("Continuar inicio")')
    adentro.or_(continuar).first.wait_for(timeout=60000)
    if continuar.is_visible():
        continuar.click()
    adentro.first.wait_for(state="visible", timeout=60000)
    matar_popup()


def cambiar_empresa(page, nombre_cliente, matar_popup, log: Callable[[str], None] = print):
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
        log(f"   -> La empresa activa ya es '{empresa_actual}'.")
        return

    log(f"   -> Cambiando de empresa: '{empresa_actual}' -> '{nombre_cliente}'...")
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
        opciones = page.locator('#compania-lista li.licomp a')
        # El filtro de Xubio es JS: se revisa la lista cada 150 ms hasta 3 s en vez de esperar fijo.
        for _ in range(20):
            page.wait_for_timeout(150)
            for i in range(opciones.count()):
                opcion = opciones.nth(i)
                try:
                    texto = opcion.inner_text(timeout=500).strip()
                except Exception:
                    continue
                if texto and (nombre_cliente.lower() in texto.lower() or texto.lower() in nombre_cliente.lower()):
                    return opcion
        return None

    objetivo = filtrar_y_ubicar(nombre_cliente)
    if objetivo is None:
        objetivo = filtrar_y_ubicar(nombre_cliente.split()[0])

    if objetivo is None:
        raise EmpresaNoEncontrada(
            f"No se encontró la empresa '{nombre_cliente}' en el buscador de compañías de "
            f"Xubio. Verificar que el nombre en config.json coincida con el nombre real en Xubio.",
            detalle={"cliente": nombre_cliente, "empresa_actual": empresa_actual},
        )

    texto_objetivo = objetivo.inner_text().strip().lower()
    try:
        objetivo.click(timeout=4000)
    except Exception:
        # Si falla, es porque el popup interceptó el click en ese preciso milisegundo
        log("      [AVISO]: Popup interceptó el click. Matando y reintentando...")
        matar_popup()
        objetivo.click(timeout=10000)

    # 4) Esperar a que la barra muestre la nueva empresa (en vez de «red tranquila» + 2 s fijos,
    # que en Xubio casi siempre agotaba los 15 s porque la página nunca deja de pedir datos).
    # Se pregunta desde acá (no con wait_for_function) porque elegir empresa recarga la página.
    limite = time.monotonic() + 30
    while time.monotonic() < limite:
        try:
            actual = page.locator('#resaltarCompania').first.inner_text(timeout=1000).strip().lower()
            if actual and (texto_objetivo in actual or actual in texto_objetivo):
                break
        except Exception:
            pass
        page.wait_for_timeout(250)
    else:
        log("      [AVISO]: Xubio tardó en mostrar la empresa nueva; se sigue igual.")
    matar_popup()
