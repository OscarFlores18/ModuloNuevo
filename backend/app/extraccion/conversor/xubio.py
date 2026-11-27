"""Paso 4 (salida a Xubio): la `Lectura` revisada → un `Comprobante` → el .zip de
«Importar desde Mis Comprobantes (ARCA)» que carga el bot de Xubio.

Solo arma el archivo: no abre Xubio. Usa lo mismo que «Subir a Xubio»
(`servicio.preparar`), así el paso del bot no cambia.

Lo que va (una fila por comprobante, como el CSV de ARCA): fecha, tipo, punto de
venta, número, CAE, CUIT y nombre de la contraparte, neto e IVA de cada alícuota,
no gravado, exento, otros tributos (todos juntos), total IVA y total. El detalle
de ítems no va: esa importación no lo lleva.
"""

from __future__ import annotations

import re

from ..modelo import EXENTO, IVAS, NETO_TOTAL, NETOS, NO_GRAVADO, OTROS, TOTAL, TOTAL_IVA, Comprobante, Parte, completar, validar
from .datos import Lectura
from .normalizar import totales_efectivos

_METODOS = {"ia": "ocr", "ocr": "ocr", "pdf": "pdf", "planilla": "planilla"}


def _clave_alicuota(a: float | None) -> str | None:
    """21 → «21», 10.5 → «10,5» (las claves de NETOS / IVAS)."""
    if a is None:
        return None
    return f"{a:g}".replace(".", ",")


def _fecha_iso(texto: str) -> str:
    m = re.fullmatch(r"\s*(\d{2})/(\d{2})/(\d{4})\s*", texto or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def _redondo(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def a_comprobante(lectura: Lectura, operacion: str, empresa: str) -> Comprobante:
    """La lectura vista desde la empresa del estudio, con los importes en las columnas
    del CSV de Mis Comprobantes."""
    e = lectura.encabezado
    t = totales_efectivos(lectura)
    tipo = int(m.group(1)) if (m := re.match(r"\s*(\d+)", e.get("tipo", ""))) else None
    pv, _, nro = e.get("numero", "").partition("-")

    importes: dict[str, float | None] = {}
    for fila in lectura.iva_alicuotas:
        clave = _clave_alicuota(fila.alicuota)
        if clave in NETOS and fila.base is not None:
            importes[NETOS[clave]] = _redondo((importes.get(NETOS[clave]) or 0) + fila.base)
        if clave in IVAS and fila.importe is not None:
            importes[IVAS[clave]] = _redondo((importes.get(IVAS[clave]) or 0) + fila.importe)
    otros = [t.get(k) for k in ("impuestos_internos", "percepcion_iibb", "otros_tributos") if t.get(k)]
    importes.update({
        NETO_TOTAL: t.get("neto_gravado"),
        NO_GRAVADO: t.get("no_gravado"),
        EXENTO: t.get("exento"),
        OTROS: _redondo(sum(otros)) if otros else None,
        TOTAL_IVA: t.get("iva"),
        TOTAL: t.get("total"),
    })

    receptor = Parte(doc_nro=e.get("receptor_cuit", ""), nombre=e.get("receptor", ""))
    if not receptor.doc_nro and operacion == "Ventas":
        receptor.doc_tipo = 99  # consumidor final: sin documento
    c = Comprobante(
        archivo=lectura.archivo,
        metodo=_METODOS.get(lectura.metodo, "manual"),
        operacion=operacion,
        cliente=empresa,
        fecha=_fecha_iso(e.get("fecha", "")),
        tipo=tipo,
        punto_venta=int(pv) if pv.isdigit() else None,
        numero=int(nro) if nro.isdigit() else None,
        cae=e.get("cae", ""),
        emisor=Parte(doc_nro=e.get("emisor_cuit", ""), nombre=e.get("emisor", "")),
        receptor=receptor,
        importes={k: v for k, v in importes.items() if v is not None},
    )
    return completar(c)


def preparar(lectura: Lectura, operacion: str, empresa: str) -> dict:
    """El .zip para «Importar desde Mis Comprobantes (ARCA)», sin abrir Xubio.
    Devuelve {listo, avisos, zip: {nombre, contenido (bytes)} | None, fila}."""
    from ..modelo import fila_arca
    from ..servicio import preparar as preparar_zips

    c = a_comprobante(lectura, operacion, empresa)
    avisos = validar(c)
    resultado = preparar_zips([c])[0]
    if resultado.get("error"):
        avisos.append(resultado["error"])
    zip_ = resultado.get("zips", {}).get(operacion)
    return {"listo": bool(zip_) and not resultado.get("error"), "avisos": avisos, "zip": zip_, "fila": fila_arca(c)}
