"""«Consultar al cliente»: pega solo (Ctrl+V) el mail copiado en el Gmail recién abierto.

El compose web de Gmail no acepta HTML en la URL, así que la página copia el
mail con la tabla al portapapeles y abre Gmail. Desde el navegador no se puede
escribir en otra pestaña; el backend, que corre en la misma PC (modo escritorio),
sí puede apretar Ctrl+V a nivel de Windows:

1. espera a que la ventana de adelante sea Gmail (el título de la pestaña lo dice),
2. espera a que el título se quede quieto (Gmail terminó de cargar el compose),
3. si Gmail sigue adelante, aprieta Ctrl+V.

Con destinatario cargado, Gmail deja el cursor en el cuerpo del mail. Si en el
medio la persona cambia de ventana, no se pega nada (nunca se pega en otra app).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

_VK_CONTROL = 0x11
_VK_V = 0x56
_KEYUP = 0x0002

_lock = threading.Lock()


def _titulo_adelante() -> str:
    """Título de la ventana en primer plano (en Windows); vacío si no se puede leer."""
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    largo = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(largo + 1)
    user32.GetWindowTextW(hwnd, buf, largo + 1)
    return buf.value


def _ctrl_v() -> None:
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.keybd_event(_VK_CONTROL, 0, 0, 0)
    user32.keybd_event(_VK_V, 0, 0, 0)
    user32.keybd_event(_VK_V, 0, _KEYUP, 0)
    user32.keybd_event(_VK_CONTROL, 0, _KEYUP, 0)


def esperar_y_pegar(
    *,
    titulo: Callable[[], str] = _titulo_adelante,
    pegar: Callable[[], None] = _ctrl_v,
    dormir: Callable[[float], None] = time.sleep,
    reloj: Callable[[], float] = time.monotonic,
    limite_s: float = 30.0,
    quieto_s: float = 2.0,
    minimo_s: float = 3.0,
    paso_s: float = 0.25,
) -> bool:
    """Espera a Gmail adelante y aprieta Ctrl+V. True si pegó."""
    inicio = reloj()
    visto: float | None = None  # cuándo apareció Gmail adelante
    ultimo = ""
    cambio = inicio  # cuándo cambió el título por última vez
    while reloj() - inicio < limite_s:
        t = titulo()
        ahora = reloj()
        if t != ultimo:
            ultimo, cambio = t, ahora
        if "gmail" in t.lower():
            if visto is None:
                visto = ahora
            if ahora - visto >= minimo_s and ahora - cambio >= quieto_s:
                pegar()
                return True
        else:
            # Se fue a otra ventana (o Gmail todavía no abrió): empezar de nuevo.
            visto = None
        dormir(paso_s)
    log.info("Pegado automático: Gmail no quedó adelante a tiempo (último título: %r)", ultimo)
    return False


def pegar_en_gmail() -> bool:
    """Lanza la espera en segundo plano (una a la vez). False si no se puede en esta PC."""
    if os.name != "nt":
        return False

    def correr() -> None:
        if not _lock.acquire(blocking=False):
            return
        try:
            esperar_y_pegar()
        except Exception:  # noqa: BLE001 - un fallo acá no debe tirar el backend
            log.exception("Pegado automático en Gmail")
        finally:
            _lock.release()

    threading.Thread(target=correr, name="pegar-gmail", daemon=True).start()
    return True
