"""Lectura local de comprobantes con Tesseract (Conversor De Facturas).

Devuelve el mismo JSON que Gemini (ver gemini.PROMPT), así la pantalla
«Verificá lo que leí» y las verificaciones no distinguen de dónde vino:

1. `preprocess_for_ocr`: OpenCV deja el papel térmico blanco y el texto negro
   (gris → agrandar si es chica → suavizar el ruido → umbral adaptativo).
2. `texto_tesseract`: Tesseract `--oem 3 --psm 6` en español.
3. `json_de_texto`: el texto al JSON fiscal (encabezado, ítems, importes, IVA por
   alícuota, otros tributos), con el CUIT corregido por módulo 11 (0↔8) y en
   `validaciones` el motor usado y lo que no se pudo validar con las cuentas.

`extract_with_tesseract` hace todo: una pasada sobre la imagen preprocesada y, si
las cuentas no cierran, dos más con otros tamaños (ocr.texto_de_imagen_variante)
para votar entre lecturas.

Sin OpenCV, se usa el preparado con Pillow de ocr.py. Sin Tesseract, lanza
ocr.OCRNoDisponible.
"""

from __future__ import annotations

import logging
import re
import time

from . import ocr

logger = logging.getLogger(__name__)

MOTOR = "tesseract_local"
# Más angosta que esto, se agranda: Tesseract lee mejor con letra de ~30 px.
ANCHO_MINIMO = 1400
LADO_MAXIMO = 4000
# Si la primera pasada no cierra, otras dos (lado mayor en px, modo de página).
PASADAS_EXTRA = ((4000, 6), (2600, 6))
CONFIG = "--oem 3 --psm 6"


# --- Imagen --------------------------------------------------------------------------


def preprocess_for_ocr(imagen):
    """PIL → imagen binaria (numpy, texto negro sobre blanco) lista para Tesseract.

    El umbral adaptativo saca el gris y las sombras del papel térmico sin comerse
    los huecos de los dígitos: así el 0 tachado no se cierra en un 8."""
    import cv2
    import numpy as np

    gris = cv2.cvtColor(np.array(imagen.convert("RGB")), cv2.COLOR_RGB2GRAY)
    alto, ancho = gris.shape
    if ancho < ANCHO_MINIMO:
        escala = ANCHO_MINIMO / ancho
        gris = cv2.resize(gris, None, fx=escala, fy=escala, interpolation=cv2.INTER_CUBIC)
    elif max(alto, ancho) > LADO_MAXIMO:
        escala = LADO_MAXIMO / max(alto, ancho)
        gris = cv2.resize(gris, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)
    # Suave: un desenfoque grande junta los trazos del 0 tachado y del 8.
    gris = cv2.GaussianBlur(gris, (3, 3), 0)
    # El bloque, en proporción al ancho (≈ el alto de dos renglones).
    bloque = max(15, (gris.shape[1] // 45) | 1)
    return cv2.adaptiveThreshold(gris, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, bloque, 15)


def _preparada(imagen):
    try:
        return preprocess_for_ocr(imagen)
    except ImportError:
        logger.info("Sin OpenCV: se prepara la imagen con Pillow.")
        return ocr.preparar_imagen(imagen)


def texto_tesseract(imagen) -> str:
    """El texto de la imagen preprocesada. Lanza ocr.OCRNoDisponible sin Tesseract."""
    if not ocr.disponible():
        raise ocr.OCRNoDisponible("Tesseract no está instalado: no se puede leer el texto de las imágenes.")
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = ocr.ejecutable()
    return pytesseract.image_to_string(_preparada(imagen), lang=ocr._idioma(), config=CONFIG, timeout=60)


# --- Texto → JSON ----------------------------------------------------------------------

_DECIMAL = re.compile(r"-?\d{1,3}(?:[.,]\d{3})*[.,]\d{2}(?!\d)|-?\d+[.,]\d{2}(?!\d)")
_PORCENTAJE = re.compile(r"\(?\s*\d{1,2}(?:[.,]\d{1,2})?\s*\)?\s*%|\(\s*\d{1,2}[.,]\d{2}\s*\)")
# «IVA 21%», «21% IVA», «IVA (21.00)» o, en los tiques, «Alícuota: 21.00%» (OCR: «Alicuote;»).
_ALICUOTA_IVA = re.compile(
    r"\biva\W{0,3}(\d{1,2}(?:[.,]\d{1,2})?)\s*%|\b(\d{1,2}(?:[.,]\d{1,2})?)\s*%\s*iva\b"
    r"|\biva\s*\(\s*(\d{1,2}[.,]\d{1,2})\s*\)|\bal[il1]cuot\w*\W{0,3}(\d{1,2}(?:[.,]\d{1,2})?)\s*%"
)


def _importes(texto: str) -> list[float]:
    """Los importes con dos decimales de un texto (1.234,56 o 1234.56)."""
    from .modelo import importe

    # «63974, 75»: el OCR mete un espacio después de la coma decimal.
    texto = re.sub(r"(\d)([.,])\s(\d{2})(?!\d)", r"\1\2\3", texto)
    return [v for v in (importe(m.group(0)) for m in _DECIMAL.finditer(texto)) if v is not None]


def desglose_iva_de_texto(lineas: list[str], neto: float | None = None) -> list[dict]:
    """«IVA 21.00%  210.00», «IVA 21%: $ 210,00» o «21% IVA 1.000,00 210,00»: una fila
    por alícuota (la base, si está en la misma línea)."""
    from .a_excel import ALICUOTAS
    from .modelo import importe, norm

    filas: dict[float, dict] = {}
    for linea in lineas:
        n = norm(linea)
        if re.search(r"percep|retenc|responsable|inscripto|exento|condici|total|\bcond\b|no grav", n):
            continue
        m = _ALICUOTA_IVA.search(n)
        if not m:
            continue
        alicuota = importe(next(g for g in m.groups() if g))
        if alicuota not in ALICUOTAS or alicuota in filas:
            continue
        valores = _importes(n[m.end():])  # lo que sigue a la alícuota (los números no cambian con norm)
        if not valores:
            continue
        filas[alicuota] = {
            "alicuota": alicuota,
            "base_imponible": valores[-2] if len(valores) >= 2 else None,
            "importe_iva": valores[-1],
        }
    salida = list(filas.values())
    if len(salida) == 1 and salida[0]["base_imponible"] is None and neto:
        salida[0]["base_imponible"] = neto
    return salida


# La leyenda del Régimen de Transparencia Fiscal (Ley 27.743): «Precio s/imp: … -
# IVA cont: …», «Imp.Int: …», «Perc. IIBB: …». Repite lo de arriba.
_ES_LEYENDA = re.compile(r"transp|27[.,]?743|s/\s?imp|iva cont")

_TRIBUTO = ("percepcion", "impuestos internos", "impuesto interno", "ingresos brutos", "retencion", "tasa municipal")


def tributos_de_texto(lineas: list[str]) -> list[dict]:
    """«07 - PERCEPCION IIBB BS AS  83974.76  6717.98»: código, descripción, base e
    importe de cada tributo que no es IVA."""
    from .a_excel import tiene
    from .modelo import norm

    salida = []
    for linea in lineas:
        n = norm(linea)
        if _ES_LEYENDA.search(n):
            break  # lo que sigue repite los importes: no son tributos nuevos
        if re.search(r"\btotal\b|otros tributos", n):
            continue
        if not (any(tiene(linea, t) for t in _TRIBUTO) or re.search(r"\biibb\b|\bimp\.?\s*int", n)):
            continue
        sin_porcentajes = _PORCENTAJE.sub(" ", linea)
        valores = _importes(sin_porcentajes)
        if not valores:
            continue  # «Ing. Brutos: 30-70986401-3» en el encabezado
        codigo = ""
        texto = linea.strip()
        if m := re.match(r"^\s*(\d{1,3})\s*[-–—~]\s*(.+)$", texto):  # «07 - …», «10 ~ …»
            codigo, texto = m.group(1), m.group(2)
        descripcion = re.split(r"\s*[:$]|\s+-?\d", texto, maxsplit=1)[0].strip(" .-")
        salida.append({
            "codigo": codigo or None,
            "descripcion": re.sub(r"\s{2,}", " ", descripcion)[:120],
            "base_imponible": valores[-2] if len(valores) >= 2 else None,
            "importe": valores[-1],
        })
    return salida


def _advertencias(e: dict, items: list, t: dict, iva: list[dict]) -> tuple[dict, list[str]]:
    """Lo que se pudo validar con las cuentas y lo que no."""
    from .a_excel import TOLERANCIA, _cierra
    from .modelo import cuit_valido

    advertencias = []
    cuit_e, cuit_r = e.get("emisor_cuit", ""), e.get("receptor_cuit", "")
    if not cuit_e:
        advertencias.append("No se encontró el CUIT del emisor.")
    elif not cuit_valido(cuit_e):
        advertencias.append(f"El CUIT del emisor {cuit_e} no pasa el módulo 11.")
    if cuit_r and len(cuit_r) == 11 and not cuit_valido(cuit_r):
        advertencias.append(f"El CUIT del receptor {cuit_r} no pasa el módulo 11.")
    totales = dict(t)
    if iva:
        totales["iva"] = round(sum(f["importe_iva"] or 0 for f in iva), 2)
    total_ok = _cierra(totales)
    if totales.get("total") is None:
        advertencias.append("No se encontró el total.")
    elif not total_ok:
        advertencias.append("Neto + no gravado + exento + IVA + otros tributos no da el total.")
    suma = round(sum(x.importe or 0 for x in items), 2)
    objetivo = [v for v in (t.get("neto_gravado"), t.get("total")) if v is not None]
    items_ok = bool(items) and any(abs(suma - v) <= max(TOLERANCIA, 0.01 * len(items)) for v in objetivo)
    if not items:
        advertencias.append("No se pudo leer el detalle de ítems.")
    elif not items_ok:
        advertencias.append(f"Los ítems suman {suma:.2f} y no dan el neto gravado ni el total.")
    for campo, nombre in (("tipo", "el tipo de comprobante"), ("numero", "el número"), ("fecha", "la fecha")):
        if not e.get(campo):
            advertencias.append(f"No se encontró {nombre}.")
    validaciones = {
        "cuit_emisor_valido": bool(cuit_e) and cuit_valido(cuit_e),
        "cuit_receptor_valido": not cuit_r or len(cuit_r) != 11 or cuit_valido(cuit_r),
        "suma_items_coincide": items_ok,
        "total_coincide": total_ok,
    }
    return validaciones, advertencias


def json_de_texto(pasadas: list[str], motor: str = MOTOR) -> dict:
    """Una o más lecturas del mismo comprobante → el JSON de gemini.PROMPT. `motor`
    va en validaciones.motor_utilizado (PaddleOCR también pasa por acá)."""
    from .a_excel import _combinar, cuits_cambiando_0_y_8, hoja_de_texto, parece_comprobante
    from .modelo import cuit_valido

    texto, e, items, t, dudas = _combinar(pasadas)
    if not parece_comprobante(e, t):
        return {"tipo": "tabla", "filas": hoja_de_texto(texto).filas, "texto": texto,
                "validaciones": {"motor_utilizado": motor, "advertencias": ["No parece un comprobante: se pasa como tabla."]}}
    correcciones = []
    for rol in ("emisor", "receptor"):
        cuit = e.get(f"{rol}_cuit", "")
        if len(cuit) == 11 and not cuit_valido(cuit) and len(arreglo := cuits_cambiando_0_y_8(cuit)) == 1:
            correcciones.append(f"CUIT del {rol}: se leyó {cuit}; cambiando 0 y 8 pasa el módulo 11 ({arreglo[0]}).")
            e[f"{rol}_cuit"] = arreglo[0]
    lineas = [l for l in texto.splitlines() if l.strip()]
    iva = desglose_iva_de_texto(lineas, t.get("neto_gravado"))
    tributos = tributos_de_texto(lineas)
    pv, _, nro = (e.get("numero") or "").partition("-")
    validaciones, advertencias = _advertencias(e, items, t, iva)
    return {
        "tipo": "comprobante",
        "encabezado": {
            "emisor_razon_social": e.get("emisor"),
            "emisor_cuit": e.get("emisor_cuit"),
            "receptor_razon_social": e.get("receptor"),
            "receptor_cuit": e.get("receptor_cuit"),
            "tipo_comprobante": e.get("tipo"),
            "punto_venta": pv or None,
            "numero_comprobante": nro or None,
            "fecha": e.get("fecha"),
        },
        "importes": {
            "neto_gravado": t.get("neto_gravado"),
            "no_gravado": t.get("no_gravado"),
            "exento": t.get("exento"),
            "desglose_iva": iva,
            "desglose_tributos": tributos,
            "otros_tributos": t.get("otros_tributos"),
            "total": t.get("total"),
        },
        "items": [
            {"codigo": x.codigo or None, "descripcion": x.descripcion, "cantidad": x.cantidad,
             "precio_unitario": x.precio, "alicuota_iva": x.alicuota, "importe": x.importe}
            for x in items
        ],
        "validaciones": {**validaciones, "motor_utilizado": motor, "advertencias": correcciones + advertencias},
        "dudas": dudas,
        "texto": texto,
    }


def extract_with_tesseract(imagen, pasadas_extra: bool = True) -> dict:
    """La imagen (PIL) → el JSON fiscal, leído solo con Tesseract. `validaciones.ms`:
    cuánto tardó. Con `pasadas_extra`, si la primera lectura no cierra se lee otras
    dos veces con otros tamaños y se vota."""
    inicio = time.perf_counter()
    pasadas = [texto_tesseract(imagen)]
    datos = json_de_texto(pasadas)
    v = datos.get("validaciones", {})
    if pasadas_extra and not (v.get("total_coincide") and v.get("cuit_emisor_valido")):
        for lado, psm in PASADAS_EXTRA:
            try:
                pasadas.append(ocr.texto_de_imagen_variante(imagen, lado, psm))
            except ocr.OCRNoDisponible:
                raise
            except Exception as exc:  # una pasada rara no corta las demás
                logger.warning("Pasada de OCR (%s px, psm %s) falló: %s", lado, psm, exc)
        datos = json_de_texto(pasadas)
    datos.setdefault("validaciones", {})["ms"] = round((time.perf_counter() - inicio) * 1000)
    datos["validaciones"]["pasadas"] = len(pasadas)
    return datos
