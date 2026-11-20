"""Comprobantes desde PDF (digital o escaneado) e imágenes (JPG, PNG, capturas).

Por página o imagen:
1. El QR de ARCA, si se puede leer (datos exactos).
2. El texto: el del PDF si es digital; si no, OCR con Tesseract.
3. Se combinan: lo del QR manda y el texto completa razón social e IVA.
Las páginas repetidas (ORIGINAL / DUPLICADO / TRIPLICADO) quedan una sola vez.
"""

from __future__ import annotations

import io
import logging

from .modelo import Comprobante, clave
from .ocr import OCRNoDisponible, texto_de_imagen
from .qr_afip import comprobante_del_qr, leer_qrs
from .texto_factura import combinar, comprobante_del_texto

logger = logging.getLogger(__name__)

MAX_PAGINAS = 40
# Menos de esto en una página = escaneada (o solo una imagen): va por OCR.
MIN_TEXTO_DIGITAL = 40


def _de_pagina(imagen, texto: str | None, archivo: str, pagina: int | None, avisos: list[str]) -> list[Comprobante]:
    """Los comprobantes de una página: los del QR completados con el texto."""
    metodo = "pdf"
    if texto is None or len(texto.strip()) < MIN_TEXTO_DIGITAL:
        metodo = "ocr"
        try:
            texto = texto_de_imagen(imagen)
        except OCRNoDisponible as exc:
            if str(exc) not in avisos:
                avisos.append(str(exc))
            texto = ""
        except Exception as exc:  # Tesseract colgado o imagen rara: se sigue sin texto.
            logger.warning("OCR falló en %s: %s", archivo, exc)
            avisos.append(f"No se pudo leer el texto{f' de la página {pagina}' if pagina else ''}.")
            texto = ""
    del_texto = comprobante_del_texto(texto, archivo, pagina, metodo)
    qrs = leer_qrs(imagen)
    if not qrs:
        return [del_texto] if del_texto else []
    # Con un solo QR, el texto es de ese comprobante; con varios, solo el QR.
    return [combinar(comprobante_del_qr(d, archivo, pagina), del_texto if len(qrs) == 1 else None) for d in qrs]


def _sin_repetidos(comprobantes: list[Comprobante]) -> list[Comprobante]:
    salida: dict[tuple, Comprobante] = {}
    sueltos: list[Comprobante] = []
    for c in comprobantes:
        k = clave(c)
        if not c.numero:
            sueltos.append(c)
        elif k in salida:
            combinar(salida[k], c)
        else:
            salida[k] = c
    return list(salida.values()) + sueltos


def extraer_pdf(nombre: str, contenido: bytes) -> tuple[list[Comprobante], list[str]]:
    import pypdfium2 as pdfium

    avisos: list[str] = []
    try:
        pdf = pdfium.PdfDocument(contenido)
    except Exception as exc:
        raise ValueError(f"«{nombre}» no es un PDF válido o tiene contraseña.") from exc
    comprobantes: list[Comprobante] = []
    try:
        total = len(pdf)
        if total > MAX_PAGINAS:
            avisos.append(f"Tiene {total} páginas: se leyeron las primeras {MAX_PAGINAS}.")
        for i in range(min(total, MAX_PAGINAS)):
            pagina = pdf[i]
            try:
                textpage = pagina.get_textpage()
                texto = textpage.get_text_range()
                textpage.close()
                digital = len(texto.strip()) >= MIN_TEXTO_DIGITAL
                # 300 ppp para OCR; para el QR alcanza con menos.
                imagen = pagina.render(scale=(150 if digital else 300) / 72).to_pil()
                comprobantes += _de_pagina(imagen, texto if digital else None, nombre, i + 1, avisos)
            finally:
                pagina.close()
    finally:
        pdf.close()
    return _sin_repetidos(comprobantes), avisos


def extraer_imagen(nombre: str, contenido: bytes) -> tuple[list[Comprobante], list[str]]:
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        imagen = Image.open(io.BytesIO(contenido))
        imagen.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"«{nombre}» no es una imagen válida.") from exc
    imagen = ImageOps.exif_transpose(imagen).convert("RGB")
    avisos: list[str] = []
    return _sin_repetidos(_de_pagina(imagen, None, nombre, None, avisos)), avisos
