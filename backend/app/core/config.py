"""Configuración del backend.

Todo se puede sobreescribir por variables de entorno para no atar el código
a una máquina concreta (requisito de la migración a sitio web).

    MODULONUEVO_DATA_DIR    -> dónde viven config.json / xubio_credenciales.json
    MODULONUEVO_OUTPUT_DIR  -> carpeta base por defecto de las salidas
    MODULONUEVO_LOG_DIR     -> logs del backend
    MODULONUEVO_CORS        -> orígenes permitidos, separados por coma
    MODULONUEVO_MASTER_KEY  -> secreto para cifrar credenciales en reposo. Si
                               no se define, se genera DATA_DIR/fernet.key
    MODULONUEVO_DIALOGOS    -> 1/0: ventanas de Windows para guardar archivos
                               (por defecto, solo en Windows local)
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

# Pausa entre un cliente y el siguiente (el front muestra la cuenta regresiva).
PAUSA_ENTRE_CLIENTES_S = int(os.getenv("MODULONUEVO_PAUSA_S", "15"))

# Keep-alive de ARCA: el portal cierra la sesión por inactividad (~15 min).
# Se consulta el portal cada ARCA_PING_MIN minutos y se renueva el login a los
# ARCA_RENOVAR_MIN minutos, antes de que el portal la venza por su cuenta.
ARCA_PING_MIN = int(os.getenv("MODULONUEVO_ARCA_PING_MIN", "9"))
ARCA_RENOVAR_MIN = int(os.getenv("MODULONUEVO_ARCA_RENOVAR_MIN", "50"))

# Navegador de los bots. Por defecto abre el Chrome visible del sistema, como
# la app de escritorio. En servidores sin pantalla (Docker/Render) se usa
# MODULONUEVO_HEADLESS=1 y MODULONUEVO_CHROME_CHANNEL vacío para lanzar el
# Chromium que instala Playwright.
PLAYWRIGHT_HEADLESS = os.getenv("MODULONUEVO_HEADLESS", "").strip().lower() in (
    "1",
    "true",
    "si",
    "sí",
    "yes",
)
PLAYWRIGHT_CHANNEL = (os.getenv("MODULONUEVO_CHROME_CHANNEL", "chrome") or "").strip() or None

# Protección opcional para despliegues públicos: "usuario:clave".
# Vacío = sin autenticación (modo local).
BASIC_AUTH = os.getenv("MODULONUEVO_BASIC_AUTH", "").strip()

# Ventanas de Windows («Guardar como», elegir carpeta) que abre el backend en su
# propia PC. Solo tienen sentido si el backend corre en la PC de quien usa la
# página: en un servidor (Docker/Render, o con BASIC_AUTH) quedan apagadas y el
# front baja los archivos por el navegador (uno suelto o un .zip con todo).
_DIALOGOS_ENV = os.getenv("MODULONUEVO_DIALOGOS", "").strip().lower()
DIALOGOS_LOCALES = (
    _DIALOGOS_ENV in ("1", "true", "si", "sí", "yes")
    if _DIALOGOS_ENV
    else os.name == "nt" and not PLAYWRIGHT_HEADLESS and not BASIC_AUTH
)

DEFAULT_CORS_ORIGINS = (
    "http://localhost:4200",
    "http://127.0.0.1:4200",
    "http://localhost:4210",
    "http://127.0.0.1:4210",
)

# Build del frontend que sirve este backend (si existe).
FRONT_DIST = _path_env(
    "MODULONUEVO_FRONT_DIST",
    BACKEND_DIR.parent / "frontend" / "dist" / "control-fct-frontend" / "browser",
)


def cors_origins() -> list[str]:
    crudo = os.getenv("MODULONUEVO_CORS", "")
    if not crudo:
        return list(DEFAULT_CORS_ORIGINS)
    return [origen.strip() for origen in crudo.split(",") if origen.strip()]


def ensure_dirs() -> None:
    for carpeta in (DATA_DIR, OUTPUT_DIR, LOG_DIR):
        carpeta.mkdir(parents=True, exist_ok=True)
