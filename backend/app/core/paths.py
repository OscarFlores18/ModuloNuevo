"""Rutas de salida y saneamiento de nombres."""

from __future__ import annotations

import re
from pathlib import Path

SUBCARPETA_CONTROL = "Control de Facturas"

_CARACTERES_INVALIDOS = re.compile(r'[\\/*?:"<>|]')


def limpiar_nombre(texto: str) -> str:
    return _CARACTERES_INVALIDOS.sub("", texto).strip()


def nombre_carpeta(texto: str) -> str:
    """Windows no admite carpetas que terminen en punto o espacio («S.A.»): las
    crea sin el punto y después Python y el navegador no apuntan al mismo lugar."""
    return limpiar_nombre(texto).rstrip(". ") or "Cliente"


def carpeta_cliente(base: Path, cliente: str, mes: str, anio: str) -> Path:
    """<base>/<Cliente>/<MMAAAA>/Control de Facturas"""
    destino = Path(base) / nombre_carpeta(cliente) / f"{mes}{anio}" / SUBCARPETA_CONTROL
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def dentro_de(base: Path, ruta: Path) -> bool:
    """True si `ruta` está contenida en `base` (protección contra path traversal)."""
    try:
        Path(ruta).resolve().relative_to(Path(base).resolve())
        return True
    except ValueError:
        return False
