"""Ventana de Windows para elegir una carpeta, abierta en la computadora del servidor.

El backend corre en la misma PC que usa el navegador, así que el diálogo le
aparece a quien está trabajando. Se usa tkinter (viene con Python) y la ventana
queda arriba de todo para que no se esconda detrás de Chrome.
"""

from __future__ import annotations

import threading
from pathlib import Path

_lock = threading.Lock()


def elegir_carpeta(titulo: str = "Elegí dónde guardar los archivos") -> Path | None:
    """La carpeta elegida, o None si se cerró la ventana sin elegir."""
    import tkinter
    from tkinter import filedialog

    with _lock:  # una ventana por vez
        raiz = tkinter.Tk()
        try:
            raiz.withdraw()
            raiz.attributes("-topmost", True)
            raiz.update()
            elegida = filedialog.askdirectory(
                parent=raiz, title=titulo, mustexist=True,
                initialdir=str(Path.home() / "Downloads"),
            )
        finally:
            raiz.destroy()
    return Path(elegida) if elegida else None
