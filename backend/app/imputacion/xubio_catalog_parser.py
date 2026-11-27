"""El catálogo de productos/servicios de una empresa en Xubio, desde su XML interno.

Formato::

    <root>
      <fields><field fieldID="PRODUCTOID">PRODUCTOID</field>...</fields>
      <data><![CDATA[-97,Producto al 21%25,PROD21,;1234,HONORARIOS  ,HON,;...]]></data>
    </root>

- Registros separados por ``;`` y campos por ``,`` (en ese orden: primero se corta y
  después se decodifica, porque una coma dentro de un valor viene como ``%2C``).
- Los valores traen URL-encoding (``%25`` = ``%``) y a veces espacios de más.
- Los IDs negativos son los ítems que Xubio trae por defecto («Producto al 21%»).

El ``nombre`` se guarda tal cual (decodificado, sin tocar espacios) porque es el que
hay que devolverle a Xubio; ``nombre_limpio`` es para mostrar y buscar.
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from urllib.parse import unquote

from pydantic import BaseModel

CAMPOS_POR_DEFECTO = ("PRODUCTOID", "NOMBRE", "USRCODE", "BARCODE")

# Tasas de IVA que se reconocen en el nombre («Producto al 10,5%», «IVA 21»).
TASAS = (27.0, 21.0, 10.5, 5.0, 2.5)


class CatalogoInvalido(ValueError):
    """El XML no tiene el formato del catálogo de Xubio."""


class ProductoXubio(BaseModel):
    producto_id: int
    nombre: str  # exacto, para exportar a Xubio
    nombre_limpio: str
    usrcode: str = ""
    barcode: str = ""
    alicuota_iva: float | None = None
    es_generico: bool = False

    @property
    def es_sistema(self) -> bool:
        return self.producto_id < 0


def limpiar(texto: str) -> str:
    """Sin espacios al principio ni al final y sin espacios dobles."""
    return re.sub(r"\s+", " ", texto or "").strip()


def _sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def _clave(texto: str) -> str:
    return _sin_acentos(limpiar(texto)).casefold()


_RE_TASA = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%|\biva\s*(\d{1,2}(?:[.,]\d{1,2})?)\b")
_RE_EXENTO = re.compile(r"\b(exento|exenta|exentos|no gravad[oa]s?)\b")


def inferir_alicuota(nombre: str) -> float | None:
    """21.0, 10.5, 27.0… si el nombre trae la tasa; 0.0 si dice «exento» o «no
    gravado»; None si no dice nada."""
    clave = _clave(nombre)
    if _RE_EXENTO.search(clave):
        return 0.0
    for m in _RE_TASA.finditer(clave):
        tasa = float((m.group(1) or m.group(2)).replace(",", "."))
        if tasa in TASAS:
            return tasa
    return None


_RE_GENERICO = re.compile(
    r"^(producto|productos|servicio|servicios|item)\s+(al\s+\d|exent|no gravad)"
    r"|^gastos?\s+varios\b|^varios\b|^otros\s+gastos\b"
)


def es_generico(nombre: str) -> bool:
    """Los comodines: «Producto al 21%», «Producto Exento», «GASTOS VARIOS»…"""
    return bool(_RE_GENERICO.search(_clave(nombre)))


def _nombres_de_campos(raiz: ET.Element) -> list[str]:
    campos = [(f.get("fieldID") or (f.text or "")).strip().upper() for f in raiz.iter("field")]
    return [c for c in campos if c] or list(CAMPOS_POR_DEFECTO)


def parsear_catalogo(xml: str | bytes) -> list[ProductoXubio]:
    """El XML del catálogo → los productos, en el orden en que vienen. Los registros
    sin PRODUCTOID numérico o sin nombre se saltean; un ID repetido queda una vez."""
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise CatalogoInvalido(f"El catálogo no es un XML válido: {exc}") from exc
    datos = raiz.find("data") if raiz.tag != "data" else raiz
    if datos is None:
        raise CatalogoInvalido("El catálogo no tiene el bloque <data>.")
    campos = _nombres_de_campos(raiz)
    if "PRODUCTOID" not in campos or "NOMBRE" not in campos:
        raise CatalogoInvalido("El catálogo no trae las columnas PRODUCTOID y NOMBRE.")

    productos: dict[int, ProductoXubio] = {}
    for registro in (datos.text or "").split(";"):
        if not registro.strip():
            continue
        valores = [unquote(v) for v in registro.split(",")]
        valores += [""] * (len(campos) - len(valores))
        fila = dict(zip(campos, valores))
        try:
            producto_id = int(fila["PRODUCTOID"].strip())
        except ValueError:
            continue
        nombre = fila["NOMBRE"].lstrip("\r\n")
        if not limpiar(nombre) or producto_id in productos:
            continue
        productos[producto_id] = ProductoXubio(
            producto_id=producto_id,
            nombre=nombre,
            nombre_limpio=limpiar(nombre),
            usrcode=limpiar(fila.get("USRCODE", "")),
            barcode=limpiar(fila.get("BARCODE", "")),
            alicuota_iva=inferir_alicuota(nombre),
            es_generico=es_generico(nombre),
        )
    if not productos:
        raise CatalogoInvalido("El catálogo no tiene ningún producto.")
    return list(productos.values())
