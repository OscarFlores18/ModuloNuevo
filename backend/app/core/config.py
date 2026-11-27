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
import sys
from pathlib import Path

# backend/app/core/config.py -> backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]

# Empaquetado con PyInstaller (app de escritorio): el código vive en una carpeta
# temporal o de instalación que puede borrarse o no tener permiso de escritura.
# Ahí no se guarda nada: los datos van a Documentos\ControlFCT del usuario.
EMPAQUETADO = bool(getattr(sys, "frozen", False))
CARPETA_USUARIO = Path(os.path.expanduser("~")) / "Documents" / "ControlFCT"


def _path_env(nombre: str, default: Path) -> Path:
    valor = os.getenv(nombre)
    return Path(valor).expanduser().resolve() if valor else default


_BASE_DATOS = CARPETA_USUARIO if EMPAQUETADO else BACKEND_DIR

DATA_DIR = _path_env("MODULONUEVO_DATA_DIR", _BASE_DATOS / "data")
OUTPUT_DIR = _path_env("MODULONUEVO_OUTPUT_DIR", _BASE_DATOS / "salidas")
LOG_DIR = _path_env("MODULONUEVO_LOG_DIR", _BASE_DATOS / "logs")

CONFIG_PATH = DATA_DIR / "config.json"
XUBIO_CRED_PATH = DATA_DIR / "xubio_credenciales.json"

# Pausa entre un cliente y el siguiente (el front muestra la cuenta regresiva).
PAUSA_ENTRE_CLIENTES_S = int(os.getenv("MODULONUEVO_PAUSA_S", "3"))

# Keep-alive de ARCA: el portal cierra la sesión por inactividad (~15 min).
# Se consulta el portal cada ARCA_PING_MIN minutos y se renueva el login a los
# ARCA_RENOVAR_MIN minutos, antes de que el portal la venza por su cuenta.
ARCA_PING_MIN = int(os.getenv("MODULONUEVO_ARCA_PING_MIN", "9"))
# Antes de reabrir Mis Comprobantes cuando ARCA tarda en responder.
ARCA_ESPERA_REINTENTO_S = float(os.getenv("MODULONUEVO_ARCA_ESPERA_REINTENTO_S", "5"))
ARCA_RENOVAR_MIN = int(os.getenv("MODULONUEVO_ARCA_RENOVAR_MIN", "50"))

# Navegador de los bots. Por defecto corre oculto (headless): los controles no
# abren ventanas de Chrome en la pantalla. MODULONUEVO_HEADLESS=0 las vuelve a
# mostrar (útil para ver qué hace un bot o completar un captcha a mano).
# «Cargar en Xubio» siempre abre la ventana: ahí la persona aprieta «Importar».
# En servidores sin Chrome (Docker/Render) se deja MODULONUEVO_CHROME_CHANNEL
# vacío para lanzar el Chromium que instala Playwright.
PLAYWRIGHT_HEADLESS = os.getenv("MODULONUEVO_HEADLESS", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)

# Resolución fija de la página de los bots (ancho x alto, en píxeles): las
# pantallas de ARCA y Xubio se ven igual en cualquier PC y en modo oculto.
def _tamano(valor: str, por_defecto: tuple[int, int]) -> tuple[int, int]:
    try:
        ancho, alto = (int(x) for x in valor.lower().replace(" ", "").split("x"))
        if ancho >= 800 and alto >= 600:
            return ancho, alto
    except ValueError:
        pass
    return por_defecto


PLAYWRIGHT_VIEWPORT = _tamano(os.getenv("MODULONUEVO_VIEWPORT", ""), (1366, 768))
# Cuánto esperan los bots a que ARCA o Xubio carguen una página o abran una
# pestaña. Se probó con 60 s y no ayudó: lo que no llegaba en 30 s no llegaba nunca
# (sesión vencida, corte de internet) y cada falla tardaba el doble. Esas fallas
# ahora se detectan al toque y el cliente se reintenta al final del control.
PLAYWRIGHT_ESPERA_MS = max(10, int(os.getenv("MODULONUEVO_ESPERA_S", "30") or 30)) * 1000
# Las descargas sí pueden tardar (un mes con muchos comprobantes).
PLAYWRIGHT_DESCARGA_MS = max(PLAYWRIGHT_ESPERA_MS, 90_000)
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
    else os.name == "nt" and not BASIC_AUTH
)

def _bandera(nombre: str, default: bool) -> bool:
    valor = os.getenv(nombre, "").strip().lower()
    if not valor:
        return default
    return valor in ("1", "true", "si", "sí", "yes")


# --- Seguridad de credenciales -------------------------------------------------

# Rotación automática de la KEK (días). 0 = no rotar sola.
KEK_ROTACION_DIAS = int(os.getenv("MODULONUEVO_KEK_ROTACION_DIAS", "90"))

# Minutos que dura el consentimiento dado al iniciar una ejecución (JIT).
CONSENTIMIENTO_MIN = int(os.getenv("MODULONUEVO_CONSENTIMIENTO_MIN", "240"))

# Filtro de salida del navegador de los bots: "bloquear" (por defecto),
# "auditar" (solo avisa) u "off".
EGRESS_MODO = (os.getenv("MODULONUEVO_EGRESS", "bloquear") or "bloquear").strip().lower()

# Dominios permitidos para el navegador de los bots (y sus subdominios). Un
# valor con "/" limita además el camino (google.com solo para el captcha).
EGRESS_DOMINIOS = (
    "afip.gob.ar",
    "arca.gob.ar",
    "xubio.com",
    "visma.com",
    "visma.net",
    # Cartel de cookies y captcha que usan los portales en el login.
    "cookielaw.org",
    "onetrust.com",
    "recaptcha.net",
    "google.com/recaptcha/",
    "gstatic.com",
    # Desafío anti-bots de Cloudflare que ARCA muestra a veces en el login: si se
    # bloquea, el portal nunca termina de cargar (#buscadorInput no aparece).
    "challenges.cloudflare.com",
    "fonts.googleapis.com",
)
EGRESS_EXTRA = tuple(
    d.strip().lower().lstrip(".")
    for d in os.getenv("MODULONUEVO_EGRESS_EXTRA", "").split(",")
    if d.strip()
)

# No bajar imágenes ni video en el navegador de los bots (más rápido).
BLOQUEAR_RECURSOS = _bandera("MODULONUEVO_BLOQUEAR_RECURSOS", True)

# Capturas + HTML de diagnóstico cuando falla el selector de Xubio. Apagado
# por defecto: nunca se capturan pantallas durante el login.
CAPTURAS_DIAGNOSTICO = _bandera("MODULONUEVO_CAPTURAS_DIAGNOSTICO", False)


DEFAULT_CORS_ORIGINS = (
    "http://localhost:4200",
    "http://127.0.0.1:4200",
    "http://localhost:4210",
    "http://127.0.0.1:4210",
)

# Build del frontend que sirve este backend (si existe). Empaquetado, PyInstaller
# lo copia (--add-data) a «frontend» dentro de sys._MEIPASS.
FRONT_DIST = _path_env(
    "MODULONUEVO_FRONT_DIST",
    Path(getattr(sys, "_MEIPASS", "")) / "frontend"
    if EMPAQUETADO
    else BACKEND_DIR.parent / "frontend" / "dist" / "control-fct-frontend" / "browser",
)


def cors_origins() -> list[str]:
    crudo = os.getenv("MODULONUEVO_CORS", "")
    if not crudo:
        return list(DEFAULT_CORS_ORIGINS)
    return [origen.strip() for origen in crudo.split(",") if origen.strip()]


def ensure_dirs() -> None:
    for carpeta in (DATA_DIR, OUTPUT_DIR, LOG_DIR):
        carpeta.mkdir(parents=True, exist_ok=True)
