"""Control de Facturas como app de escritorio de Windows.

Un solo proceso: FastAPI (que además sirve el build de Angular) corre en un hilo
en 127.0.0.1 (siempre el mismo puerto, si está libre), y pywebview abre una ventana nativa (WebView2,
el motor de Edge que ya trae Windows) apuntando a ese servidor. Al cerrar la
ventana se apaga el servidor (los bots y el worker se detienen limpio) y termina
el proceso.

La ventana aparece enseguida con «Abriendo…» y el backend (pandas, Playwright)
se carga detrás; cuando responde, la ventana pasa a la app. Si ya hay una
abierta, un segundo doble clic la trae al frente en vez de abrir otra.

    Desarrollo:   python desktop.py          (antes: cd frontend && npx ng build)
    Empaquetado:  .\\build_app.ps1  ->  dist\\ControlFCT\\ControlFCT.exe

Empaquetado (PyInstaller), los datos van a Documentos\\ControlFCT (ver
backend/app/core/config.py), nunca a la carpeta del .exe.
"""

from __future__ import annotations

import multiprocessing
import os
import socket
import sys
import threading
import time
import urllib.request

TITULO = "Control de Facturas"
ESPERA_MAX_S = 90
# Un mutex con nombre por sesión de Windows: si ya existe, hay otra ventana abierta.
MUTEX = "Local\\ControlFCT-una-sola-ventana"

PANTALLA_CARGA = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><style>
  html,body{height:100%;margin:0}
  body{display:flex;align-items:center;justify-content:center;flex-direction:column;gap:18px;
    font-family:"Segoe UI",system-ui,sans-serif;color:#fff;
    background:linear-gradient(135deg,#0f2a5c 0%,#1e40af 55%,#0e7490 100%)}
  h1{font-size:26px;font-weight:600;margin:0}
  p{margin:0;opacity:.8;font-size:14px}
  .r{width:38px;height:38px;border:4px solid rgba(255,255,255,.25);border-top-color:#fff;
    border-radius:50%;animation:g 0.9s linear infinite}
  @keyframes g{to{transform:rotate(360deg)}}
</style></head><body>
  <div class="r"></div>
  <h1>Control de Facturas</h1>
  <p>Abriendo…</p>
</body></html>"""


def _salidas_sin_consola() -> None:
    """Con --noconsole, sys.stdout/stderr son None y cualquier print o log a
    consola rompe (uvicorn, Playwright). Se mandan a nul."""
    nulo = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - vive lo que vive el proceso
    if sys.stdout is None:
        sys.stdout = nulo
    if sys.stderr is None:
        sys.stderr = nulo


def _ya_hay_una_abierta() -> bool:
    """True si otra instancia tiene el mutex; en ese caso trae su ventana al frente.

    El handle del mutex queda abierto (en `_MUTEX_HANDLE`) mientras vive el
    proceso; Windows lo libera solo al salir."""
    if os.name != "nt":
        return False
    import ctypes

    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    global _MUTEX_HANDLE
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, MUTEX)
    if kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        return False
    hwnd = user32.FindWindowW(None, TITULO)
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE: si estaba minimizada
        user32.SetForegroundWindow(hwnd)
    return True


_MUTEX_HANDLE = None


# Siempre el mismo puerto: la ventana guarda lo del navegador (localStorage: el
# recorrido ya visto, filtros, tildes) por dirección, y con un puerto distinto en
# cada apertura se perdía todo. Si está ocupado, prueba los siguientes y, como
# último recurso, uno cualquiera.
PUERTOS_FIJOS = (48517, 48518, 48519)


def _puerto_libre() -> int:
    """El primer puerto fijo libre en 127.0.0.1; si no hay, uno que dé el sistema."""
    for puerto in PUERTOS_FIJOS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", puerto))
            except OSError:
                continue
            return puerto
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _esperar_servidor(url: str, servidor_vivo) -> bool:
    """True cuando /health responde; False si el servidor murió o pasó el tiempo."""
    limite = time.monotonic() + ESPERA_MAX_S
    while time.monotonic() < limite:
        if not servidor_vivo():
            return False
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.15)
    return False


def _avisar_error(texto: str) -> None:
    """Sin consola no hay dónde leer un error: se muestra una ventana de Windows."""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, texto, TITULO, 0x10)
    except Exception:
        print(texto, file=sys.stderr)


class _ApiVentana:
    """Lo que la página puede pedirle a la ventana (``window.pywebview.api``)."""

    def avisar(self, titulo: str = "") -> bool:
        """Terminó un control: si la ventana no está al frente, el ícono de la
        barra de tareas parpadea hasta que se la abre (FlashWindowEx)."""
        if os.name != "nt":
            return False
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, TITULO)
        if not hwnd or user32.GetForegroundWindow() == hwnd:
            return False

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("hwnd", wintypes.HWND),
                ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT),
                ("dwTimeout", wintypes.DWORD),
            ]

        # FLASHW_ALL | FLASHW_TIMERNOFG: parpadea hasta que la ventana pasa al frente.
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x3 | 0xC, 0, 0)
        user32.FlashWindowEx(ctypes.byref(info))
        return True


class _Backend:
    """FastAPI en un hilo. Se arranca con la ventana ya visible (`arrancar`)."""

    def __init__(self) -> None:
        self.servidor = None
        self.hilo: threading.Thread | None = None

    def arrancar(self, ventana) -> None:
        # Importar recién acá (tarda: pandas, Playwright) con la pantalla de
        # carga ya a la vista. config.py decide las carpetas (Documentos\ControlFCT).
        try:
            import uvicorn

            from backend.app.core import config
            from backend.app.main import app
        except Exception as exc:  # noqa: BLE001 - sin consola, se avisa y se cierra
            _avisar_error(f"No se pudo iniciar Control de Facturas.\n\n{exc}")
            ventana.destroy()
            return

        puerto = _puerto_libre()
        url = f"http://127.0.0.1:{puerto}"
        self.servidor = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=puerto,
                log_config=None,  # sin handlers de consola: el backend ya escribe en LOG_DIR
                log_level="warning",
                access_log=False,
                lifespan="on",
            )
        )
        self.hilo = threading.Thread(target=self.servidor.run, name="uvicorn", daemon=True)
        self.hilo.start()

        if not _esperar_servidor(url, self.hilo.is_alive):
            _avisar_error(
                "No se pudo iniciar Control de Facturas.\n\n"
                f"Revisá el registro en:\n{config.LOG_DIR}"
            )
            ventana.destroy()
            return
        ventana.load_url(url)

    def detener(self) -> None:
        if self.servidor is not None:
            self.servidor.should_exit = True
        if self.hilo is not None:
            self.hilo.join(timeout=15)


def _carpeta_webview():
    """Misma carpeta de datos que el backend, sin importarlo (eso es lo lento)."""
    from pathlib import Path

    env = os.getenv("MODULONUEVO_DATA_DIR")
    if env:
        return Path(env) / "webview"
    if getattr(sys, "frozen", False):
        return Path.home() / "Documents" / "ControlFCT" / "data" / "webview"
    return Path(__file__).resolve().parent / "backend" / "data" / "webview"


def main() -> int:
    _salidas_sin_consola()
    if _ya_hay_una_abierta():
        return 0

    import webview

    # Descargas de la página (Excel, .zip) y links externos (Gmail) en el navegador.
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

    backend = _Backend()
    ventana = webview.create_window(
        TITULO,
        html=PANTALLA_CARGA,
        width=1280,
        height=800,
        min_size=(1024, 680),
        text_select=True,
        js_api=_ApiVentana(),
    )
    # Bloquea hasta que se cierra la ventana; `arrancar` corre en otro hilo con
    # la ventana ya abierta. private_mode=False: sessionStorage y localStorage
    # (tildes, filtros) duran entre aperturas.
    webview.start(
        backend.arrancar,
        ventana,
        private_mode=False,
        storage_path=str(_carpeta_webview()),
    )

    # Ventana cerrada: se apaga el servidor (lifespan detiene el worker y los bots).
    backend.detener()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    code = main()
    import logging

    logging.shutdown()  # vuelca el log antes de salir
    # Un hilo de un bot colgado no puede retener el proceso después de cerrar la ventana.
    os._exit(code)
