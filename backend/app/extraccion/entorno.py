"""Configuración del Conversor De Facturas por variable de entorno o archivo .env.

Un valor sale de, en este orden: la variable de entorno; un ``.env`` en la carpeta
de datos de la app (``Documentos\\ControlFCT\\data`` instalada; ``backend/data`` en
desarrollo); un ``.env`` en ``backend/``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..core import config


def _de_archivo_env(ruta: Path, nombre: str) -> str:
    try:
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            m = re.match(rf"\s*(?:export\s+)?{nombre}\s*=\s*(.*)$", linea)
            if m:
                return m.group(1).split(" #")[0].strip().strip("'\"")
    except OSError:
        pass
    return ""


def valor(nombre: str) -> str:
    if v := os.getenv(nombre, "").strip():
        return v
    for carpeta in (config.DATA_DIR, config.BACKEND_DIR):
        if v := _de_archivo_env(Path(carpeta) / ".env", nombre):
            return v
    return ""


def numero(nombre: str, por_defecto: float, minimo: float = 1.0) -> float:
    try:
        return max(minimo, float(valor(nombre) or por_defecto))
    except ValueError:
        return por_defecto
