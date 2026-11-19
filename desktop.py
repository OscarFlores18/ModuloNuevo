"""Control de Facturas como app de escritorio de Windows.

Un solo proceso: FastAPI (que además sirve el build de Angular) corre en un hilo
en 127.0.0.1 con un puerto libre, y pywebview abre una ventana nativa (WebView2,
el motor de Edge que ya trae Windows) apuntando a ese servidor. Al cerrar la
ventana se apaga el servidor (los bots y el worker se detienen limpio) y termina
el proceso.

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
ESPERA_MAX_S = 60


def _salidas_sin_consola() -> None:
    """Con --noconsole, sys.stdout/stderr son None y cualquier print o log a
    consola rompe (uvicorn, Playwright). Se mandan a nul."""
    nulo = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - vive lo que vive el proceso
    if sys.stdout is None:
        sys.stdout = nulo
    if sys.stderr is None:
        sys.stderr = nulo


def _puerto_libre() -> int:
    """Pide al sistema un puerto libre en 127.0.0.1 (bind al puerto 0)."""
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


def main() -> int:
    _salidas_sin_consola()

    # Importar recién acá: config.py decide las carpetas (Documentos\ControlFCT si está empaquetado).
    import uvicorn
    import webview

    from backend.app.core import config
    from backend.app.main import app

    puerto = _puerto_libre()
    url = f"http://127.0.0.1:{puerto}"

    servidor = uvicorn.Server(
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
    hilo = threading.Thread(target=servidor.run, name="uvicorn", daemon=True)
    hilo.start()

    if not _esperar_servidor(url, hilo.is_alive):
        _avisar_error(
            "No se pudo iniciar Control de Facturas.\n\n"
            f"Revisá el registro en:\n{config.LOG_DIR}"
        )
        return 1

    # Descargas de la página (Excel, .zip) y links externos (Gmail) en el navegador.
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

    webview.create_window(
        TITULO,
        url,
        width=1280,
        height=800,
        min_size=(1024, 680),
        text_select=True,
    )
    # Bloquea hasta que se cierra la ventana. private_mode=False: el sessionStorage
    # y localStorage (tildes, filtros) duran entre aperturas.
    webview.start(private_mode=False, storage_path=str(config.DATA_DIR / "webview"))

    # Ventana cerrada: se apaga el servidor (lifespan detiene el worker y los bots).
    servidor.should_exit = True
    hilo.join(timeout=15)
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    code = main()
    import logging

    logging.shutdown()  # vuelca el log antes de salir
    # Un hilo de un bot colgado no puede retener el proceso después de cerrar la ventana.
    os._exit(code)
