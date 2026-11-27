"""Paso 1, leer: el archivo (foto, captura, PDF, planilla) a una `Lectura`.
Fotos: QR de ARCA, Gemini y, si Gemini no está o falla, el OCR local.
"""

from __future__ import annotations
import difflib
import io
import itertools
import logging
import re
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, field_validator
from ..modelo import TIPOS, cuit_valido, fecha as fecha_iso, importe, nombre_tipo, norm
from ..texto_factura import _tipo as tipo_del_texto
from ..texto_factura import cuits as cuits_del_texto
from .datos import Hoja, IMAGENES, Lectura, MAX_FILAS, MAX_PAGINAS, PLANILLAS
from .normalizar import MOTORES, Modo, _aplicar_qr, _celda, _desde_json, _desde_texto, completar_desglose, corregir_descripciones, corregir_importes_items, sugerir_productos
from .validar import _cierra_bien, verificar

logger = logging.getLogger(__name__)


def _imagen_a_lectura(nombre: str, imagen, lectura: Lectura, pasadas_extra: bool = True) -> Lectura:
    """La imagen leída con Tesseract (tesseract.extract_with_tesseract)."""
    from ..ocr import OCRNoDisponible
    from ..tesseract import extract_with_tesseract

    lectura.metodo = "ocr"
    try:
        datos = extract_with_tesseract(imagen, pasadas_extra=pasadas_extra)
    except OCRNoDisponible as exc:
        lectura.avisos.append(str(exc))
        return lectura
    except Exception as exc:
        logger.warning("OCR falló en %s: %s", nombre, exc)
        lectura.avisos.append(f"Tesseract no pudo leer el texto de la imagen ({str(exc)[:160] or type(exc).__name__}).")
        return lectura
    return _desde_json(lectura, datos, "ocr")


def modo_ocr(pedido: str | None = None) -> Modo:
    """Quién lee las imágenes: lo pedido en la petición; si no, FORCE_TESSERACT=true,
    u OCR_MODE (variable de entorno o .env): auto, gemini_only, paddle o tesseract_only
    (también sin el «_only»)."""
    from ..entorno import valor

    if pedido and pedido.lower() in MOTORES:
        return MOTORES[pedido.lower()]
    if valor("FORCE_TESSERACT").lower() in ("1", "true", "si", "sí", "yes"):
        return "tesseract"
    return MOTORES.get(valor("OCR_MODE").lower(), "auto")


def es_captura_limpia(imagen, contenido: bytes = b"") -> bool:
    """Una captura de pantalla (fondo blanco puro, pocos grises), no una foto: ahí
    el OCR local lee bien en menos de un segundo y no hace falta el modelo de visión."""
    exif = getattr(imagen, "getexif", lambda: {})()
    if exif and (exif.get(0x010F) or exif.get(0x0110)):  # marca / modelo de cámara
        return False
    chica = imagen.convert("L")
    chica.thumbnail((500, 500))
    histograma = chica.histogram()
    total = sum(histograma) or 1
    blanco = sum(histograma[248:]) / total
    grises = sum(histograma[70:200]) / total
    return blanco >= 0.55 and grises <= 0.10


def _con_gemini(nombre: str, imagen, lectura: Lectura, plazo_s: float | None) -> Lectura | None:
    """Gemini, o None (con el aviso) si no estuvo disponible a tiempo."""
    from .. import gemini

    try:
        return _desde_json(lectura, gemini.leer_imagen(imagen, plazo_s=plazo_s), "ia")
    except gemini.GeminiNoDisponible as exc:
        lectura.avisos.append(f"{exc}")
        return None


def hay_ocr_local() -> bool:
    from .. import ocr, paddle

    return paddle.disponible() or ocr.disponible()


def _leer_local(nombre: str, imagen, lectura: Lectura, modo: Modo = "auto", pasadas_extra: bool = True) -> Lectura:
    """El OCR local: PaddleOCR si está instalado (opcional, más preciso); si no, o si
    falla, Tesseract."""
    from .. import paddle

    if modo in ("auto", "paddle") and paddle.disponible():
        try:
            return _desde_json(lectura, paddle.extract_with_paddleocr(imagen), "ocr")
        except Exception as exc:
            logger.warning("PaddleOCR falló en %s: %s", nombre, exc)
            lectura.avisos.append("PaddleOCR no pudo leer la imagen: se leyó con Tesseract.")
    elif modo == "paddle":
        lectura.avisos.append("PaddleOCR no está instalado (pip install paddleocr paddlepaddle): se leyó con Tesseract.")
    return _imagen_a_lectura(nombre, imagen, lectura, pasadas_extra=pasadas_extra)


def _leer_imagen(nombre: str, contenido: bytes, motor: str | None = None) -> Lectura:
    """En cascada: el QR de ARCA (exacto) y después, según el modo:
    - gemini: solo Gemini, sin plazo ni respaldo;
    - paddle / tesseract: solo ese OCR local;
    - auto: las capturas limpias, primero el OCR local en una pasada: si cierra,
      listo. Si no, Gemini con plazo (GEMINI_PLAZO_S, 15 s); si no hay clave, falla,
      da 429/503 o tarda, el OCR local (PaddleOCR o Tesseract)."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        imagen = Image.open(io.BytesIO(contenido))
        imagen.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"«{nombre}» no es una imagen válida.") from exc
    captura = es_captura_limpia(imagen, contenido)
    imagen = ImageOps.exif_transpose(imagen).convert("RGB")
    return _leer_pil(nombre, imagen, motor, captura)


def _leer_pil(nombre: str, imagen, motor: str | None = None, captura: bool = False, lectura: Lectura | None = None) -> Lectura:
    """La cascada de una imagen ya abierta (foto, captura o página de un PDF escaneado):
    QR de ARCA, Gemini y, si no está, falla o tarda, el OCR local."""
    from .. import gemini
    from ..qr_afip import leer_qrs

    inicio = time.perf_counter()
    modo = modo_ocr(motor)
    qrs = leer_qrs(imagen)
    lectura = lectura or Lectura(archivo=nombre)

    if modo == "tesseract":
        lectura = _imagen_a_lectura(nombre, imagen, lectura)
    elif modo == "paddle":
        lectura = _leer_local(nombre, imagen, lectura, "paddle")
    elif modo == "gemini":
        if gemini.disponible():
            lectura = _con_gemini(nombre, imagen, lectura, None) or lectura
        else:
            lectura.avisos.append("OCR_MODE=gemini_only pero no hay clave de Gemini (GEMINI_API_KEY).")
    else:
        local = hay_ocr_local()
        if captura and local:
            rapida = _leer_local(nombre, imagen, Lectura(archivo=nombre), pasadas_extra=False)
            if _cierra_bien(rapida):
                lectura = rapida
            elif gemini.disponible():
                lectura.avisos.append("La captura leída con OCR local no cerraba: se leyó con Gemini.")
        if not lectura.motor and gemini.disponible():
            # El plazo corto sólo tiene sentido si hay un OCR local para leer después:
            # sin él, se espera a Gemini lo que haga falta.
            if (con_ia := _con_gemini(nombre, imagen, lectura, gemini.plazo_auto() if local else None)) is not None:
                lectura = con_ia
            elif local:
                lectura.avisos[-1] += " Se leyó con el OCR local."
                lectura = _leer_local(nombre, imagen, lectura)
            else:
                lectura.avisos[-1] += " Tampoco hay OCR local (Tesseract) instalado para leerla: probá de nuevo en un rato."
        elif not lectura.motor:
            lectura = _leer_local(nombre, imagen, lectura)
            if not lectura.texto:
                lectura.avisos.append(
                    "Para leer imágenes con IA, poné la clave de Gemini (GEMINI_API_KEY) en el archivo .env "
                    "de la carpeta de datos de la app y volvé a subir la imagen."
                )
    if qrs:
        lectura = _aplicar_qr(lectura, qrs[0])
    lectura.duracion_ms = round((time.perf_counter() - inicio) * 1000)
    return lectura


def _leer_pdf(nombre: str, contenido: bytes) -> Lectura:
    import pypdfium2 as pdfium

    lectura = Lectura(archivo=nombre, metodo="pdf")
    try:
        pdf = pdfium.PdfDocument(contenido)
    except Exception as exc:
        raise ValueError(f"«{nombre}» no es un PDF válido o tiene contraseña.") from exc
    try:
        total = len(pdf)
        if total > MAX_PAGINAS:
            lectura.avisos.append(f"Tiene {total} páginas: se leyeron las primeras {MAX_PAGINAS}.")
        textos = []
        for i in range(min(total, MAX_PAGINAS)):
            pagina = pdf[i]
            try:
                tp = pagina.get_textpage()
                textos.append(tp.get_text_range())
                tp.close()
            finally:
                pagina.close()
        texto = "\n".join(textos)
        if len(texto.strip()) >= 40:
            if total > 1:
                lectura.avisos.append(f"Tiene {min(total, MAX_PAGINAS)} páginas: se leyeron como un solo documento.")
            return _desde_texto(lectura, [texto])
        # Escaneado: OCR de la primera página (las demás suelen ser copias).
        pagina = pdf[0]
        try:
            imagen = pagina.render(scale=300 / 72).to_pil()
        finally:
            pagina.close()
        if total > 1:
            lectura.avisos.append(f"PDF escaneado de {total} páginas: se leyó la primera.")
        # Igual que una foto: Gemini primero y, si no está o falla, el OCR local.
        return _leer_pil(nombre, imagen.convert("RGB"), lectura=lectura)
    finally:
        pdf.close()


def _leer_planilla(nombre: str, contenido: bytes) -> Lectura:
    import pandas as pd

    from ..planillas import _leer_csv

    lectura = Lectura(archivo=nombre, metodo="planilla")
    sufijo = Path(nombre).suffix.lower()
    if sufijo in (".csv", ".txt"):
        tablas = {"Hoja1": _leer_csv(contenido)}
    else:
        motor = "xlrd" if sufijo == ".xls" else "openpyxl"
        try:
            with pd.ExcelFile(io.BytesIO(contenido), engine=motor) as libro:
                tablas = {h: pd.read_excel(libro, sheet_name=h, header=None, dtype=object) for h in libro.sheet_names[:60]}
        except Exception as exc:
            raise ValueError(f"«{nombre}» no se pudo abrir como planilla.") from exc
    for hoja, df in tablas.items():
        filas = [[_celda(v) for v in fila] for fila in df.itertuples(index=False, name=None)]
        filas = [f for f in filas if any(c is not None for c in f)]
        # Sin columnas vacías al final.
        ancho = max((max((i + 1 for i, c in enumerate(f) if c is not None), default=0) for f in filas), default=0)
        if len(filas) > MAX_FILAS:
            lectura.avisos.append(f"La hoja «{hoja}» tiene {len(filas)} filas: se pasan las primeras {MAX_FILAS}.")
        lectura.hojas.append(Hoja(nombre=str(hoja)[:31] or "Hoja", filas=[f[:ancho] for f in filas[:MAX_FILAS]]))
    lectura.tipo = "tabla"
    return lectura


def leer(nombre: str, contenido: bytes, motor: str | None = None) -> Lectura:
    """`motor` (opcional, para probar): auto, gemini, paddle o tesseract; si no,
    OCR_MODE."""
    nombre = Path(nombre).name
    sufijo = Path(nombre).suffix.lower()
    if sufijo in PLANILLAS:
        lectura = _leer_planilla(nombre, contenido)
    elif sufijo == ".pdf":
        lectura = _leer_pdf(nombre, contenido)
    elif sufijo in IMAGENES:
        lectura = _leer_imagen(nombre, contenido, motor)
    else:
        raise ValueError(f"«{nombre}»: formato no soportado (foto, captura, PDF, CSV o Excel).")
    if lectura.tipo == "comprobante":
        corregir_descripciones(lectura.items)
        corregir_importes_items(lectura.items, lectura.totales)
        sugerir_productos(lectura.items)
    completar_desglose(lectura)
    lectura.verificaciones = verificar(lectura)
    return lectura


def extract_invoice_data(ruta, motor: str | None = None) -> Lectura:
    """Un archivo del disco (PDF, foto, captura, planilla) → la Lectura, ya
    verificada. Lo mismo que hace el endpoint con lo que se sube."""
    from pathlib import Path

    ruta = Path(ruta)
    return leer(ruta.name, ruta.read_bytes(), motor)
