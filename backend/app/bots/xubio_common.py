"""
Funciones compartidas por bot_xubio.py y bot_xubio_liq.py: login en Xubio
(via connect.visma.com) y cambio de empresa dentro de la sesión.

Se centralizaron acá porque antes vivían duplicadas en ambos archivos.
Cada vez que Xubio cambia el HTML de su selector de compañía (ya pasó más
de una vez, ver claude/diagnostico-bug-fecha-anual.md), alcanza con
corregir este único archivo en vez de dos copias que pueden desalinearse.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher, get_close_matches
from pathlib import Path
from typing import Callable

from ..core import config
from ..core.errors import EmpresaNoEncontrada, SelectorEmpresaNoDisponible


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


# --- Selector de compañía -----------------------------------------------------

# Sufijos societarios que no aportan al comparar nombres ("ACME S.R.L." vs "Acme").
_SUFIJOS = re.compile(r"\b(s a s|s a|sas|sa|s r l|srl|ltda|s c|sc)\b")


def _normalizar(texto: str) -> str:
    """Nombre comparable: minúsculas, sin acentos, sin puntuación ni sufijos."""
    sin_acentos = "".join(
        c
        for c in unicodedata.normalize("NFKD", str(texto or ""))
        if not unicodedata.combining(c)
    )
    base = re.sub(r"[^a-z0-9]+", " ", sin_acentos.lower())
    base = _SUFIJOS.sub(" ", base)
    return re.sub(r"\s+", " ", base).strip()


def _coincide(buscado: str, candidato: str) -> bool:
    """True si los dos nombres refieren a la misma empresa (a ojo humano)."""
    a, b = _normalizar(buscado), _normalizar(candidato)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    # Tolera diferencias de tipeo chicas entre config.json y Xubio.
    return SequenceMatcher(None, a, b).ratio() >= 0.86


def _texto_seguro(locator) -> str:
    try:
        return locator.inner_text(timeout=1000).strip()
    except Exception:
        return ""


def _esperar_buscador(page, timeout_ms: int):
    """El buscador del desplegable si llegó a mostrarse, o None."""
    buscador = page.locator("#buscador-empresa")
    try:
        buscador.wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        return None
    return buscador


def _guardar_diagnostico(page, log: Callable[[str], None]) -> None:
    """Captura + HTML cuando el selector no abre, para poder diagnosticarlo."""
    try:
        carpeta = Path(config.LOG_DIR) / "diagnostico"
        carpeta.mkdir(parents=True, exist_ok=True)
        sello = f"{datetime.now():%Y%m%d-%H%M%S}"
        page.screenshot(path=str(carpeta / f"xubio-selector-{sello}.png"), full_page=True)
        (carpeta / f"xubio-selector-{sello}.html").write_text(
            page.content(), encoding="utf-8"
        )
        log(f"      [INFO]: Captura y HTML del selector guardados en {carpeta}")
    except Exception as exc:
        log(f"      [AVISO]: No se pudo guardar el diagnóstico: {exc}")


def _abrir_selector_compania(page, matar_popup, log: Callable[[str], None]):
    """Abre el desplegable de compañías y devuelve el buscador ya visible.

    Xubio cambió varias veces cómo se abre este menú: hay builds donde alcanza
    con hover, otros donde hace falta click y otros donde un click de más sobre
    un menú abierto por hover lo vuelve a cerrar. Por eso se prueba **una**
    estrategia por vez y se verifica después de cada intento si el buscador
    quedó visible, en vez de asumir que hover+click siempre abre.
    """
    contenedor = page.locator(".compania").first
    nombre = page.locator("#resaltarCompania").first
    fila = nombre.locator("xpath=ancestor::li[1]").first
    icono = page.locator(".compania .fa, .compania i, .compania svg").first

    def hover_y_click():
        contenedor.hover(timeout=4000)
        contenedor.click(timeout=4000)

    estrategias = (
        ("hover sobre la compañía activa", lambda: contenedor.hover(timeout=4000)),
        ("click sobre la compañía activa", lambda: contenedor.click(timeout=4000)),
        ("click sobre el nombre de la compañía", lambda: nombre.click(timeout=4000)),
        ("hover + click sobre la compañía activa", hover_y_click),
        ("click sobre la fila de la compañía", lambda: fila.click(timeout=4000)),
        ("click sobre el icono del selector", lambda: icono.click(timeout=4000)),
    )

    for descripcion, accion in estrategias:
        matar_popup()
        try:
            accion()
        except Exception as exc:
            primero = str(exc).splitlines()[0][:140] if str(exc) else type(exc).__name__
            log(f"      [INFO]: No se pudo {descripcion}: {primero}")
            continue
        buscador = _esperar_buscador(page, 2500)
        if buscador is not None:
            log(f"      [INFO]: Selector de compañías abierto ({descripcion}).")
            return buscador
        # Se cerró o no abrió: se sale del menú antes de probar la próxima.
        page.mouse.move(2, 2)
        page.wait_for_timeout(300)

    _guardar_diagnostico(page, log)
    raise SelectorEmpresaNoDisponible(
        "No se pudo abrir el selector de compañías de Xubio después de varios "
        "intentos (hover, click y click en la fila). Puede ser que Xubio haya "
        "cambiado el menú o que la sesión quedó a medio cargar. Se guardó una "
        "captura y el HTML de la pantalla en logs/diagnostico: reintentá el "
        "control y, si vuelve a pasar, revisá esos archivos.",
        detalle={"empresa_actual": _texto_seguro(nombre), "url": page.url},
    )


def _nombres_companias(page, limite: int = 60) -> list[str]:
    """Nombres que muestra el desplegable (sin filtrar), para poder avisar."""
    opciones = page.locator("#compania-lista li.licomp a")
    nombres: list[str] = []
    try:
        total = opciones.count()
    except Exception:
        return nombres
    for i in range(min(total, limite)):
        try:
            texto = opciones.nth(i).inner_text().strip()
        except Exception:
            continue
        if texto and texto not in nombres:
            nombres.append(texto)
    return nombres


def _buscar_compania(page, buscador, texto: str, log: Callable[[str], None]):
    """Filtra la lista y devuelve el link de la empresa que coincide, o None.

    El filtro de Xubio es JS: se revisa la lista cada 150 ms hasta 3 s en vez
    de esperar un tiempo fijo.
    """
    buscador.click()
    buscador.fill("")
    buscador.fill(texto)
    opciones = page.locator("#compania-lista li.licomp a")
    for _ in range(20):  # 20 × 150 ms
        page.wait_for_timeout(150)
        try:
            total = opciones.count()
        except Exception:
            return None
        for i in range(total):
            opcion = opciones.nth(i)
            try:
                if not opcion.is_visible():
                    continue
                nombre = opcion.inner_text(timeout=500).strip()
            except Exception:
                continue
            if nombre and _coincide(texto, nombre):
                return opcion
    return None


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

    Si la empresa no aparece, el error incluye la lista de empresas que Xubio
    está mostrando: casi siempre el problema es que el nombre del cliente en la
    app no coincide con el nombre real de la compañía en Xubio.
    """
    # 1) Leer la empresa actualmente activa
    page.wait_for_selector('#resaltarCompania', state="visible", timeout=30000)
    empresa_actual = _texto_seguro(page.locator('#resaltarCompania').first)

    if _coincide(nombre_cliente, empresa_actual):
        log(f"   -> La empresa activa ya es '{empresa_actual}'.")
        return

    log(f"   -> Cambiando de empresa: '{empresa_actual}' -> '{nombre_cliente}'...")
    matar_popup()

    # 2) Abrir el selector de compañía (ver _abrir_selector_compania).
    buscador = _abrir_selector_compania(page, matar_popup, log)

    # La lista completa se lee antes de filtrar: sirve para el mensaje de error.
    disponibles = _nombres_companias(page)

    # 3) Filtrar por nombre y ubicar el resultado en la lista filtrada.
    # Si el nombre completo no trae resultados (diferencias de puntuación,
    # mayúsculas o espacios contra el nombre mostrado en Xubio), más abajo se
    # reintenta filtrando solo por la primera palabra.
    objetivo = _buscar_compania(page, buscador, nombre_cliente, log)
    if objetivo is None:
        # Reintento: el nombre en Xubio puede estar abreviado o tener el
        # nombre de fantasía adelante ("Estudio: PICCININI...").
        normalizado = _normalizar(nombre_cliente)
        primera = normalizado.split(" ")[0] if normalizado else ""
        if primera and primera != normalizado:
            log(f"      [INFO]: Sin resultados con el nombre completo, se busca por '{primera}'.")
            objetivo = _buscar_compania(page, buscador, primera, log)

    if objetivo is None:
        normalizados = {_normalizar(n): n for n in disponibles}
        parecidas = [
            normalizados[p]
            for p in get_close_matches(
                _normalizar(nombre_cliente), list(normalizados), n=3, cutoff=0.5
            )
        ]
        mensaje = (
            f"No se encontró la empresa '{nombre_cliente}' en el selector de "
            "compañías de Xubio."
        )
        if disponibles:
            mensaje += " Empresas visibles: " + ", ".join(disponibles[:15]) + "."
        if parecidas:
            mensaje += " ¿Quisiste decir '" + "' o '".join(parecidas) + "'?"
        mensaje += (
            " El nombre del cliente en la app tiene que coincidir con el nombre "
            "de la compañía en Xubio."
        )
        log(
            "      [INFO]: Empresas visibles en Xubio: "
            + (", ".join(disponibles[:15]) or "ninguna")
        )
        raise EmpresaNoEncontrada(
            mensaje,
            detalle={
                "cliente": nombre_cliente,
                "empresa_actual": empresa_actual,
                "disponibles": disponibles[:40],
            },
        )

    texto_objetivo = objetivo.inner_text().strip().lower()
    try:
        objetivo.click(timeout=4000)
    except Exception:
        # Si falla, es porque el popup interceptó el click en ese preciso milisegundo
        log("      [AVISO]: Popup interceptó el click. Matando y reintentando...")
        matar_popup()
        objetivo.click(timeout=10000)

    # 4) Esperar a que la barra muestre la nueva empresa. No se usa
    #    «networkidle»: Xubio casi nunca deja de pedir datos y ese wait agotaba
    #    los 15 s siempre. Se consulta el nombre hasta que coincida (30 s).
    for _ in range(120):  # 120 × 250 ms
        nueva = _texto_seguro(page.locator("#resaltarCompania").first)
        if nueva and _coincide(nombre_cliente, nueva):
            log(f"      [OK]: Empresa activa: '{nueva}'.")
            break
        page.wait_for_timeout(250)
    else:
        nueva = _texto_seguro(page.locator("#resaltarCompania").first)
        if nueva:
            log(
                f"      [AVISO]: Se eligió '{nombre_cliente}' pero Xubio sigue "
                f"mostrando '{nueva}' como empresa activa."
            )
        else:
            log("      [AVISO]: Xubio tardó en mostrar la empresa nueva; se sigue igual.")
    matar_popup()
