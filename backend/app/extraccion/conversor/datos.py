"""El formato único de datos del conversor: todo archivo que entra (foto, PDF,
planilla) termina en una `Lectura`; la pantalla y el Excel solo hablan con ella.
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

logger = logging.getLogger(__name__)


PLANILLAS = {".xlsx", ".xlsm", ".xls", ".csv", ".txt"}


IMAGENES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


FORMATOS = PLANILLAS | IMAGENES | {".pdf"}


MAX_FILAS = 20000


MAX_PAGINAS = 20


TOLERANCIA = 0.05


ALICUOTAS = (21.0, 10.5, 27.0, 5.0, 2.5)


TOTALES = ("neto_gravado", "no_gravado", "exento", "iva", "impuestos_internos", "percepcion_iibb", "otros_tributos", "total")


ETIQUETAS_TOTALES = {
    "neto_gravado": "Neto gravado",
    "no_gravado": "Neto no gravado",
    "exento": "Exento",
    "iva": "IVA",
    "impuestos_internos": "Impuestos internos",
    "percepcion_iibb": "Percepción IIBB Bs. As.",
    "otros_tributos": "Otros tributos",
    "total": "Total",
}


ETIQUETAS_ENCABEZADO = {
    "emisor": "Emisor",
    "emisor_cuit": "CUIT del emisor",
    "receptor": "Receptor",
    "receptor_cuit": "CUIT del receptor",
    "tipo": "Tipo de comprobante",
    "numero": "Número",
    "fecha": "Fecha",
    "vencimiento": "Vencimiento",
}


Nivel = Literal["ok", "aviso", "error"]


def _cortar(valor, campo):
    """El texto, sin pasar del max_length del campo."""
    if not isinstance(valor, str):
        return valor
    largo = next((m.max_length for m in campo.metadata if getattr(m, "max_length", None)), None)
    return valor[:largo].rstrip() if largo and len(valor) > largo else valor


class Item(BaseModel):
    codigo: str = Field(default="", max_length=40)
    descripcion: str = Field(default="", max_length=300)
    cantidad: float | None = None
    precio: float | None = None
    alicuota: float | None = None
    importe: float | None = None
    impuestos_internos: float | None = None
    # Producto o servicio sugerido para Xubio (lo propone la IA o las reglas de abajo).
    producto: str = Field(default="", max_length=60)
    # Lo que decía antes de autocorregirlo (vacío si no se tocó).
    leido: str = Field(default="", max_length=300)
    # El importe que se leyó antes de autocorregirlo (None si no se tocó).
    importe_leido: float | None = None

    @field_validator("codigo", "descripcion", "producto", "leido", mode="before")
    @classmethod
    def _recortar(cls, v, info):
        # Un OCR que leyó basura de corrido no hace fallar toda la lectura: se corta.
        return _cortar(v, cls.model_fields[info.field_name])


class AlicuotaIva(BaseModel):
    """Una fila del desglose de IVA: la alícuota (21, 10.5…), su base imponible y el IVA."""

    alicuota: float | None = None
    base: float | None = None
    importe: float | None = None


class Tributo(BaseModel):
    """Una fila de «otros tributos»: percepción, impuesto interno, tasa…"""

    codigo: str = Field(default="", max_length=10)
    descripcion: str = Field(default="", max_length=120)
    base: float | None = None
    importe: float | None = None

    @field_validator("codigo", "descripcion", mode="before")
    @classmethod
    def _recortar(cls, v, info):
        return _cortar(v, cls.model_fields[info.field_name])


class Hoja(BaseModel):
    nombre: str = Field(default="Hoja1", max_length=31)
    filas: list[list[str | float | int | None]] = Field(default_factory=list, max_length=MAX_FILAS)


class Verificacion(BaseModel):
    campo: str
    nivel: Nivel
    mensaje: str
    sugerencia: str | float | None = None
    # Para mostrarlo corto: un título, los números que importan y, si hay dudas, los valores para elegir.
    titulo: str = ""
    datos: list[str] = Field(default_factory=list)
    opciones: list[str] = Field(default_factory=list)


class Lectura(BaseModel):
    """Lo leído de un archivo. `tipo` comprobante: encabezado, items y totales;
    tabla: las hojas."""

    archivo: str = Field(default="", max_length=260)
    tipo: Literal["comprobante", "tabla"] = "tabla"
    # «ia»: imagen leída con Gemini (ver gemini.py).
    metodo: Literal["planilla", "pdf", "ocr", "ia"] = "planilla"
    # Qué leyó la imagen: «gemini», «paddleocr_local», «tesseract_local»,
    # con «qr+» adelante si se usó el QR de ARCA.
    motor: str = Field(default="", max_length=40)
    # Cuánto tardó la lectura de la imagen (para comparar motores).
    duracion_ms: int | None = None
    encabezado: dict[str, str] = Field(default_factory=dict)
    items: list[Item] = Field(default_factory=list, max_length=2000)
    totales: dict[str, float | None] = Field(default_factory=dict)
    # El IVA por alícuota; totales["iva"] es siempre la suma de los importes.
    iva_alicuotas: list[AlicuotaIva] = Field(default_factory=list, max_length=20)
    # Otros tributos uno por uno; con filas, totales["otros_tributos"] es su suma.
    tributos: list[Tributo] = Field(default_factory=list, max_length=30)
    hojas: list[Hoja] = Field(default_factory=list, max_length=60)
    texto: str = Field(default="", max_length=400_000)
    # Lo que cada pasada de OCR leyó distinto: campo -> valores leídos.
    dudas: dict[str, list[str]] = Field(default_factory=dict)
    # Lo que se autocorrigió solo: campo -> lo que se había leído (se avisa y va en naranja).
    corregidos: dict[str, str] = Field(default_factory=dict)
    avisos: list[str] = Field(default_factory=list)
    verificaciones: list[Verificacion] = Field(default_factory=list)
