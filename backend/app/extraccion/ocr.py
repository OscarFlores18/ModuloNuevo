"""OCR local con Tesseract, para PDF escaneados, fotos y capturas.

Tesseract es un programa aparte (no un paquete de Python). Se busca en este orden:
1. `MODULONUEVO_TESSERACT` (ruta al tesseract.exe).
2. La carpeta `tesseract` que el instalador deja al lado de la app (build_app.ps1).
3. El PATH y las carpetas donde lo deja su instalador oficial.
Sin Tesseract la app sigue funcionando: de las fotos se lee solo el QR y el resto
se carga a mano en la revisión.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


class OCRNoDisponible(Exception):
    pass


def _candidatos() -> list[Path]:
    rutas = []
    if env := os.getenv("MODULONUEVO_TESSERACT", "").strip():
        rutas.append(Path(env))
    for base in (getattr(sys, "_MEIPASS", None), Path(sys.executable).parent if getattr(sys, "frozen", False) else None):
        if base:
            rutas.append(Path(base) / "tesseract" / "tesseract.exe")
    if encontrado := shutil.which("tesseract"):
        rutas.append(Path(encontrado))
    for raiz in (os.getenv("ProgramFiles"), os.getenv("ProgramFiles(x86)"), os.path.join(os.getenv("LOCALAPPDATA", ""), "Programs")):
        if raiz:
            rutas.append(Path(raiz) / "Tesseract-OCR" / "tesseract.exe")
    return rutas


@lru_cache(maxsize=1)
def ejecutable() -> str | None:
    for ruta in _candidatos():
        if ruta.is_file():
            return str(ruta)
    return None


@lru_cache(maxsize=1)
def _idioma() -> str:
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = ejecutable() or "tesseract"
    try:
        idiomas = set(pytesseract.get_languages(config=""))
    except Exception:
        return "eng"
    return "spa" if "spa" in idiomas else "eng"


def disponible() -> bool:
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return ejecutable() is not None


def preparar_imagen(imagen):
    """Gris, derecha (EXIF de las fotos de celular) y con resolución suficiente."""
    from PIL import ImageOps

    img = ImageOps.exif_transpose(imagen)
    img = img.convert("L")
    # Tesseract lee mejor con letra de ~30 px: las capturas chicas se agrandan.
    if max(img.size) < 1800:
        escala = 1800 / max(img.size)
        img = img.resize((int(img.width * escala), int(img.height * escala)))
    elif max(img.size) > 4000:
        escala = 4000 / max(img.size)
        img = img.resize((int(img.width * escala), int(img.height * escala)))
    return ImageOps.autocontrast(img, cutoff=1)


def texto_de_imagen(imagen) -> str:
    """El texto de la imagen (PIL). Lanza OCRNoDisponible si no hay Tesseract."""
    if not disponible():
        raise OCRNoDisponible("Tesseract no está instalado: de las imágenes solo se lee el QR de ARCA.")
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = ejecutable()
    # psm 6: un bloque de texto uniforme, lo que mejor separa las líneas de una factura.
    return pytesseract.image_to_string(preparar_imagen(imagen), lang=_idioma(), config="--psm 6", timeout=90)


def texto_de_imagen_variante(imagen, lado: int, psm: int) -> str:
    """Otra pasada de OCR con otro tamaño y otro modo de página. Leer la misma
    imagen de dos o tres formas y comparar es lo que permite avisar qué datos
    pueden estar mal leídos (y elegir el que cierra con las cuentas)."""
    if not disponible():
        raise OCRNoDisponible("Tesseract no está instalado: no se puede leer el texto de las imágenes.")
    import pytesseract
    from PIL import Image, ImageOps

    pytesseract.pytesseract.tesseract_cmd = ejecutable()
    img = ImageOps.exif_transpose(imagen).convert("L")
    escala = lado / max(img.size)
    img = img.resize((int(img.width * escala), int(img.height * escala)), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=1)
    return pytesseract.image_to_string(img, lang=_idioma(), config=f"--psm {psm}", timeout=90)
