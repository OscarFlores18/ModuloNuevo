"""Los datos de una factura a partir de su texto (PDF digital u OCR).

Pensado para el formato de las facturas de ARCA («Comprobantes en línea» y la
mayoría de los sistemas de facturación): «Punto de Venta: 00002 Comp. Nro:
00000123», «CUIT:», «CAE N°:», «Importe Total: $ 1.210,00», «IVA 21%: $ 210,00».
Lo que no se encuentra queda vacío y se completa en la pantalla de revisión.
"""

from __future__ import annotations

import re

from .modelo import (
    EXENTO, IVAS, NETO_TOTAL, NO_GRAVADO, OTROS, TOTAL,
    Comprobante, Parte, codigo_tipo, cuit_valido, fecha, importe, norm,
)

# «1.210,00», «1,210.00» o «1210,00» (en ese orden, para no cortar el número).
_IMPORTE = r"\$?\s*(-?\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?(?![\d.])|-?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?(?![\d,])|-?\d+(?:[.,]\d{1,2})?(?!\d))"
_CUIT = re.compile(r"\b(\d{2})[-\s.]?(\d{8})[-\s.]?(\d)\b")


def _buscar(patron: str, texto: str, flags=re.IGNORECASE) -> re.Match | None:
    return re.search(patron, texto, flags)


def _importe_de(etiqueta: str, texto: str) -> float | None:
    m = _buscar(r"(?:" + etiqueta + r")\s*[:.]?\s*" + _IMPORTE, texto)
    return importe(m.group(1)) if m else None


def cuits(texto: str) -> list[str]:
    """Los CUIT válidos del texto, en orden y sin repetir."""
    vistos: list[str] = []
    for m in _CUIT.finditer(texto):
        c = "".join(m.groups())
        if cuit_valido(c) and c not in vistos:
            vistos.append(c)
    return vistos


def _razones_sociales(texto: str) -> list[str]:
    salida = []
    for m in re.finditer(r"(?:Raz\S{1,3}n\s+Social|Apellido\s+y\s+Nombre\s*/\s*Raz\S{1,3}n\s+Social|Se[ñn]or(?:es)?)\s*:\s*([^\n]+)", texto, re.IGNORECASE):
        nombre = re.split(r"\s{2,}|\s+(?:CUIT|Domicilio|Fecha|Condici[oó]n)\b", m.group(1).strip(), flags=re.IGNORECASE)[0].strip(" :-")
        if nombre and nombre not in salida:
            salida.append(nombre)
    return salida


def _tipo(texto: str) -> int | None:
    if m := _buscar(r"C[OÓ]D(?:IGO)?\.?\s*(?:N[°º.*o]?\s*)?:?\s*(\d{2,3})\b", texto):
        return int(m.group(1))
    n = norm(texto)
    letra = None
    # La letra suele estar sola en una línea, en el recuadro de arriba.
    if m := re.search(r"(?m)^\s*([ABCEM])\s*$", texto):
        letra = m.group(1)
    clase = None
    if re.search(r"nota de credito", n):
        clase = "nota de credito"
    elif re.search(r"nota de debito", n):
        clase = "nota de debito"
    elif "factura" in n:
        clase = "factura"
    elif "recibo" in n:
        clase = "recibo"
    if clase and not letra:
        if m := re.search(clase + r"\s+([abcem])\b", n):
            letra = m.group(1).upper()
    return codigo_tipo(f"{clase} {letra}") if clase and letra else None


def comprobante_del_texto(texto: str, archivo: str, pagina: int | None, metodo: str) -> Comprobante | None:
    """None si el texto no parece un comprobante (ni número, ni total, ni CUIT)."""
    if not texto or not texto.strip():
        return None
    c = Comprobante(archivo=archivo, pagina=pagina, metodo=metodo)
    c.tipo = _tipo(texto)

    if m := _buscar(r"Punto\s+de\s+Venta\s*:?\s*(\d{1,5})\s*(?:Comp(?:robante)?\.?\s*)?N(?:ro|°|º|úmero)?\.?\s*:?\s*(\d{1,8})", texto):
        c.punto_venta, c.numero = int(m.group(1)), int(m.group(2))
    elif m := _buscar(r"\bN(?:ro|°|º|úmero)?\.?\s*:?\s*(\d{4,5})\s*-\s*(\d{8})\b", texto) or _buscar(r"\b(\d{4,5})\s*-\s*(\d{8})\b", texto):
        c.punto_venta, c.numero = int(m.group(1)), int(m.group(2))
    else:
        if m := _buscar(r"Punto\s+de\s+Venta\s*:?\s*(\d{1,5})", texto):
            c.punto_venta = int(m.group(1))
        if m := _buscar(r"Comp(?:robante)?\.?\s*N(?:ro|°|º)\.?\s*:?\s*(\d{1,8})", texto):
            c.numero = int(m.group(1))

    if m := _buscar(r"Fecha\s+(?:de\s+)?Emisi\S{1,3}n\s*:?\s*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})", texto):
        c.fecha = fecha(m.group(1))
    elif m := _buscar(r"Fecha\s*:?\s*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})", texto) or _buscar(r"\b(\d{2}/\d{2}/\d{4})\b", texto):
        c.fecha = fecha(m.group(1))

    if m := _buscar(r"C\.?\s?A\.?\s?E\.?\s*(?:N[°º.*o]?)?\s*:?\s*(\d{14})\b", texto):
        c.cae = m.group(1)

    encontrados = cuits(texto)
    nombres = _razones_sociales(texto)
    if encontrados:
        c.emisor = Parte(doc_tipo=80, doc_nro=encontrados[0], nombre=nombres[0] if nombres else "")
    if len(encontrados) > 1:
        c.receptor = Parte(doc_tipo=80, doc_nro=encontrados[1], nombre=nombres[1] if len(nombres) > 1 else "")
    elif len(nombres) > 1:
        c.receptor = Parte(nombre=nombres[1])
    if re.search(r"consumidor\s+final", texto, re.IGNORECASE) and len(encontrados) < 2:
        c.receptor = Parte(doc_tipo=99, doc_nro="0", nombre=c.receptor.nombre or "Consumidor Final")

    imp: dict[str, float] = {}
    for a, col in IVAS.items():
        etiqueta = r"IVA\s*" + a.replace(",", r"[.,]") + r"\s*%"
        if (v := _importe_de(etiqueta, texto)) is not None and v:
            imp[col] = v
    for etiqueta, col in (
        (r"Importe\s+Neto\s+Gravado|Neto\s+Gravado|Subtotal\s+Gravado", NETO_TOTAL),
        (r"(?:Importe\s+)?(?:Neto\s+)?No\s+Gravado", NO_GRAVADO),
        (r"(?:Importe\s+)?Exento", EXENTO),
        (r"(?:Importe\s+)?Otros\s+Tributos", OTROS),
        (r"Importe\s+Total|Total\s+a\s+Pagar|\bTOTAL\b", TOTAL),
    ):
        if (v := _importe_de(etiqueta, texto)) is not None:
            imp[col] = v
    # Factura B/C: sin IVA discriminado, el «Subtotal» es el total.
    if TOTAL not in imp and (v := _importe_de(r"Subtotal", texto)) is not None:
        imp[TOTAL] = v
    c.importes = {k: v for k, v in imp.items() if not (k != TOTAL and v == 0)}

    if not (c.numero or c.importes.get(TOTAL) or encontrados):
        return None
    return c


def combinar(base: Comprobante, extra: Comprobante | None) -> Comprobante:
    """`base` (por ejemplo el del QR, exacto) completado con lo que trae `extra`."""
    if extra is None:
        return base
    for campo in ("fecha", "tipo", "punto_venta", "numero", "cae"):
        if not getattr(base, campo):
            setattr(base, campo, getattr(extra, campo))
    for rol in ("emisor", "receptor"):
        b, e = getattr(base, rol), getattr(extra, rol)
        if not b.doc_nro:
            b.doc_nro, b.doc_tipo = e.doc_nro, e.doc_tipo
        if not b.nombre:
            # El nombre es del mismo CUIT, o del otro si el texto los dio vuelta.
            otro = extra.receptor if rol == "emisor" else extra.emisor
            b.nombre = e.nombre if (not e.doc_nro or e.doc_nro == b.doc_nro) else (otro.nombre if otro.doc_nro == b.doc_nro else "")
    for k, v in extra.importes.items():
        if base.importes.get(k) is None:
            base.importes[k] = v
    return base
