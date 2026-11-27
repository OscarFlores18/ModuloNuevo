"""Lectura local de comprobantes con PaddleOCR (Conversor De Facturas).

Respaldo local de Gemini (gemini.py), opcional: devuelve el mismo JSON,
así la pantalla «Verificá lo que leí» no distingue de dónde vino.

1. `preparar`: OpenCV, gris y contraste suave (CLAHE), sin binarizar: PaddleOCR
   lee mejor con los bordes de la letra intactos.
2. `cajas_de`: PaddleOCR (español, con el clasificador de orientación de renglón)
   devuelve cada pedazo de texto con su caja.
3. `renglones`: las cajas se agrupan por altura (Y) y se ordenan por X, así el
   precio queda en el mismo renglón que su descripción y no se mezclan columnas.
4. tesseract.json_de_texto: el mismo parser que Tesseract (CUIT por módulo 11 con
   el cambio 0↔8, IVA por alícuota, otros tributos, ítems).

PaddleOCR es opcional (``pip install paddleocr paddlepaddle``; paddlepaddle todavía no
tiene ruedas para Python 3.14): si no está, la app usa Tesseract.
"""

from __future__ import annotations

import importlib.util
import logging
import statistics
import threading
import time

logger = logging.getLogger(__name__)

MOTOR = "paddleocr_local"
# Más grande no lee mejor y tarda más; las fotos de celular se achican a esto.
LADO_MAXIMO = 2500

_lector = None
_candado = threading.Lock()


def disponible() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("paddleocr", "paddle"))


def preparar(imagen):
    """PIL → BGR (numpy) en gris con el contraste emparejado (CLAHE)."""
    import cv2
    import numpy as np

    gris = cv2.cvtColor(np.array(imagen.convert("RGB")), cv2.COLOR_RGB2GRAY)
    lado = max(gris.shape)
    if lado > LADO_MAXIMO:
        escala = LADO_MAXIMO / lado
        gris = cv2.resize(gris, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)
    gris = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gris)
    return cv2.cvtColor(gris, cv2.COLOR_GRAY2BGR)


def _paddle():
    """El lector, creado una sola vez (cargar los modelos tarda unos segundos)."""
    global _lector
    with _candado:
        if _lector is None:
            from paddleocr import PaddleOCR

            try:  # PaddleOCR 3.x
                _lector = PaddleOCR(lang="es", use_textline_orientation=True,
                                    use_doc_orientation_classify=False, use_doc_unwarping=False)
            except (TypeError, ValueError):  # 2.x
                _lector = PaddleOCR(use_angle_cls=True, lang="es", show_log=False)
        return _lector


Caja = tuple[float, float, float, float, str]  # x0, y0, x1, y1, texto


def _de_puntos(puntos) -> tuple[float, float, float, float]:
    xs = [float(p[0]) for p in puntos]
    ys = [float(p[1]) for p in puntos]
    return min(xs), min(ys), max(xs), max(ys)


def cajas_de(resultado) -> list[Caja]:
    """La salida de PaddleOCR (3.x: rec_texts/rec_polys; 2.x: [[puntos, (texto, conf)]])
    a cajas con su texto."""
    cajas: list[Caja] = []
    for pagina in resultado or []:
        if pagina is None:
            continue
        if hasattr(pagina, "get") and pagina.get("rec_texts") is not None:
            polys = pagina.get("rec_polys")
            if polys is None:
                polys = [((b[0], b[1]), (b[2], b[3])) for b in pagina.get("rec_boxes", [])]
            for texto, poly in zip(pagina["rec_texts"], polys):
                if str(texto).strip():
                    cajas.append((*_de_puntos(poly), str(texto).strip()))
            continue
        for renglon in pagina:
            puntos, (texto, _conf) = renglon[0], renglon[1]
            if str(texto).strip():
                cajas.append((*_de_puntos(puntos), str(texto).strip()))
    return cajas


def renglones(cajas: list[Caja]) -> list[str]:
    """Agrupa las cajas en renglones por su centro vertical y ordena cada uno por X.
    Entre dos cajas muy separadas (columnas) van dos espacios."""
    if not cajas:
        return []
    alto = statistics.median(c[3] - c[1] for c in cajas) or 1.0
    grupos: list[list[Caja]] = []
    for caja in sorted(cajas, key=lambda c: (c[1] + c[3]) / 2):
        centro = (caja[1] + caja[3]) / 2
        if grupos:
            ultimo = grupos[-1]
            centro_grupo = sum((c[1] + c[3]) / 2 for c in ultimo) / len(ultimo)
            if abs(centro - centro_grupo) <= alto * 0.5:
                ultimo.append(caja)
                continue
        grupos.append([caja])
    lineas = []
    for grupo in grupos:
        grupo.sort(key=lambda c: c[0])
        linea = grupo[0][4]
        for antes, caja in zip(grupo, grupo[1:]):
            linea += ("  " if caja[0] - antes[2] > alto else " ") + caja[4]
        lineas.append(linea)
    return lineas


def texto_paddle(imagen) -> str:
    lector = _paddle()
    preparada = preparar(imagen)
    resultado = lector.predict(preparada) if hasattr(lector, "predict") else lector.ocr(preparada, cls=True)
    return "\n".join(renglones(cajas_de(resultado)))


def extract_with_paddleocr(imagen) -> dict:
    """La imagen (PIL o ruta) → el JSON fiscal, leído con PaddleOCR. `validaciones.ms`:
    cuánto tardó."""
    from .tesseract import json_de_texto

    if isinstance(imagen, (str, bytes)) or hasattr(imagen, "__fspath__"):
        from PIL import Image, ImageOps

        imagen = ImageOps.exif_transpose(Image.open(imagen)).convert("RGB")
    inicio = time.perf_counter()
    datos = json_de_texto([texto_paddle(imagen)], motor=MOTOR)
    datos.setdefault("validaciones", {})["ms"] = round((time.perf_counter() - inicio) * 1000)
    return datos
