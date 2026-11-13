"""Ventanas de Windows para elegir carpeta o «Guardar como», abiertas en la computadora del servidor.

El backend corre en la misma PC que usa el navegador, así que el diálogo le
aparece a quien está trabajando. Se usa tkinter (viene con Python) y la ventana
queda arriba de todo para que no se esconda detrás de Chrome.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

_lock = threading.Lock()


def _con_ventana(abrir: Callable[[object], str]) -> Path | None:
    """Abre un diálogo de tkinter (uno por vez) y devuelve lo elegido, o None si se cerró."""
    import tkinter

    with _lock:
        raiz = tkinter.Tk()
        try:
            raiz.withdraw()
            raiz.attributes("-topmost", True)
            raiz.update()
            elegida = abrir(raiz)
        finally:
            raiz.destroy()
    return Path(elegida) if elegida else None


def _descargas() -> str:
    return str(Path.home() / "Downloads")


def elegir_carpeta(titulo: str = "Elegí dónde guardar los archivos") -> Path | None:
    from tkinter import filedialog

    return _con_ventana(
        lambda raiz: filedialog.askdirectory(parent=raiz, title=titulo, mustexist=True, initialdir=_descargas())
    )


def guardar_como(nombre: str, titulo: str = "Guardar archivo") -> Path | None:
    from tkinter import filedialog

    ext = Path(nombre).suffix
    tipos = [(f"Archivo {ext}", f"*{ext}"), ("Todos", "*.*")] if ext else [("Todos", "*.*")]
    return _con_ventana(
        lambda raiz: filedialog.asksaveasfilename(
            parent=raiz, title=titulo, initialfile=nombre, initialdir=_descargas(),
            defaultextension=ext, filetypes=tipos,
        )
    )
