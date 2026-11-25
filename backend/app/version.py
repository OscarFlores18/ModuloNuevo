"""Versión de la app, para no confundir instalaciones.

El número sale del archivo VERSION de la raíz del repo. build_app.ps1 además
escribe `_compilacion.py` con la fecha y hora del armado (va adentro del .exe y
se borra al terminar), así dos instaladores del mismo número se distinguen.
"""

from pathlib import Path

try:
    from ._compilacion import COMPILADO, VERSION
except ImportError:  # corriendo desde el código (web, desarrollo)
    _archivo = Path(__file__).resolve().parents[2] / "VERSION"
    VERSION = _archivo.read_text(encoding="utf-8").strip() if _archivo.exists() else "dev"
    COMPILADO = None
