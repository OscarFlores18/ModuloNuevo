"""Configuración del backend.

Todo se puede sobreescribir por variables de entorno para no atar el código
a una máquina concreta (requisito de la migración a sitio web).

    MODULONUEVO_DATA_DIR    -> dónde viven config.json / xubio_credenciales.json
    MODULONUEVO_OUTPUT_DIR  -> carpeta base por defecto de las salidas
    MODULONUEVO_LOG_DIR     -> logs del backend
    MODULONUEVO_CORS        -> orígenes permitidos, separados por coma
"""

from __future__ import annotations

import os
from pathlib import Path

# backend/app/core/config.py -> backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]


def _path_env(nombre: str, default: Path) -> Path:
    valor = os.getenv(nombre)
    return Path(valor).expanduser().resolve() if valor else default


DATA_DIR = _path_env("MODULONUEVO_DATA_DIR", BACKEND_DIR / "data")
OUTPUT_DIR = _path_env("MODULONUEVO_OUTPUT_DIR", BACKEND_DIR / "salidas")
LOG_DIR = _path_env("MODULONUEVO_LOG_DIR", BACKEND_DIR / "logs")

CONFIG_PATH = DATA_DIR / "config.json"
XUBIO_CRED_PATH = DATA_DIR / "xubio_credenciales.json"

DEFAULT_CORS_ORIGINS = ("http://localhost:4200", "http://127.0.0.1:4200")


def cors_origins() -> list[str]:
    crudo = os.getenv("MODULONUEVO_CORS", "")
    if not crudo:
        return list(DEFAULT_CORS_ORIGINS)
    return [origen.strip() for origen in crudo.split(",") if origen.strip()]


def ensure_dirs() -> None:
    for carpeta in (DATA_DIR, OUTPUT_DIR, LOG_DIR):
        carpeta.mkdir(parents=True, exist_ok=True)
