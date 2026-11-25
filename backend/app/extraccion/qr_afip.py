"""El QR de las facturas electrónicas de ARCA (RG 4892).

Es una URL `https://www.afip.gob.ar/fe/qr/?p=<base64>` (o arca.gob.ar) con un JSON:
fecha, CUIT del emisor, punto de venta, tipo, número, importe total, moneda,
cotización, documento del receptor y CAE. Si se lee, esos datos son exactos: no
hace falta OCR para ellos. No trae el desglose de IVA ni las razones sociales.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from urllib.parse import parse_qs, urlparse

from .modelo import Comprobante, Parte, fecha, moneda

logger = logging.getLogger(__name__)


def datos_del_qr(texto: str) -> dict | None:
    """El JSON del QR de ARCA, o None si el texto no es uno."""
    if not texto:
        return None
    url = urlparse(texto.strip())
    if not re.search(r"(afip|arca)\.gob\.ar$", url.netloc or ""):
        return None
    p = (parse_qs(url.query).get("p") or [""])[0]
    if not p:
        return None
    p = p.replace(" ", "+")
    p += "=" * (-len(p) % 4)
    for decodificar in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            datos = json.loads(decodificar(p).decode("utf-8"))
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
        if isinstance(datos, dict) and "cuit" in datos:
            return datos
    return None


def comprobante_del_qr(datos: dict, archivo: str, pagina: int | None = None) -> Comprobante:
    def numero(clave):
        v = datos.get(clave)
        try:
            return int(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    total = datos.get("importe")
    try:
        total = round(float(total), 2) if total not in (None, "") else None
    except (TypeError, ValueError):
        total = None
    try:
        cotizacion = float(datos.get("ctz") or 1)
    except (TypeError, ValueError):
        cotizacion = 1.0
    doc_rec = numero("nroDocRec")
    return Comprobante(
        archivo=archivo,
        pagina=pagina,
        metodo="qr",
        fecha=fecha(datos.get("fecha")),
        tipo=numero("tipoCmp"),
        punto_venta=numero("ptoVta"),
        numero=numero("nroCmp"),
        cae=str(datos.get("codAut") or ""),
        emisor=Parte(doc_tipo=80, doc_nro=str(datos.get("cuit") or "")),
        receptor=Parte(doc_tipo=numero("tipoDocRec"), doc_nro=str(doc_rec) if doc_rec else ""),
        moneda=moneda(datos.get("moneda")),
        tipo_cambio=cotizacion or 1.0,
        importes={"Imp. Total": total} if total is not None else {},
    )


def leer_qrs(imagen) -> list[dict]:
    """Los QR de ARCA que haya en una imagen (PIL). Vacío si no hay o no se pueden leer."""
    try:
        import zxingcpp
    except ImportError:  # pragma: no cover - dependencia opcional
        logger.warning("zxing-cpp no está instalado: no se leen los QR.")
        return []
    encontrados: list[dict] = []
    candidatas = [imagen]
    # En fotos grandes el QR queda chico respecto del total: se prueba también achicada.
    if max(imagen.size) > 2400:
        escala = 2000 / max(imagen.size)
        candidatas.append(imagen.resize((int(imagen.width * escala), int(imagen.height * escala))))
    for img in candidatas:
        try:
            resultados = zxingcpp.read_barcodes(img, formats=zxingcpp.BarcodeFormat.QRCode)
        except Exception as exc:  # pragma: no cover - imagen rara
            logger.info("No se pudo buscar el QR: %s", exc)
            continue
        for r in resultados:
            datos = datos_del_qr(r.text)
            if datos and datos not in encontrados:
                encontrados.append(datos)
        if encontrados:
            break
    return encontrados
