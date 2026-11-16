"""Contraseña liviana para la sección Clientes.

No es un login de la app: solo protege ver y editar los clientes (que guardan
claves fiscales). «Nuevo control» sigue funcionando sin pedirla.

- La primera vez que se entra a Clientes, la contraseña que se escriba queda
  como la contraseña de la sección. Se guarda **solo su hash** (scrypt con sal)
  en ``DATA_DIR/acceso_clientes.json``.
- Si se define ``MODULONUEVO_CLIENTES_CLAVE`` (servidor), esa manda y no se
  puede crear otra desde la página.
- Entrar devuelve un token de sesión (8 h, en memoria del backend) que el
  front manda en ``X-Clientes-Token``.
- Tras 5 intentos fallidos se bloquea 30 s.
- Mientras no haya contraseña configurada, la sección queda abierta.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
import threading
import time
from pathlib import Path

from . import config

DURACION_S = 8 * 3600
MIN_LARGO = 4
MAX_FALLOS = 5
ESPERA_FALLOS_S = 30

_lock = threading.Lock()
_tokens: dict[str, float] = {}
_fallos = {"cantidad": 0, "hasta": 0.0}


class AccesoDenegado(Exception):
    def __init__(self, mensaje: str, estado: int = 401) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.estado = estado


def _ruta() -> Path:
    return Path(config.DATA_DIR) / "acceso_clientes.json"


def _clave_env() -> str:
    return os.getenv("MODULONUEVO_CLIENTES_CLAVE", "")


def _hash(clave: str, sal: bytes) -> str:
    return hashlib.scrypt(
        clave.encode("utf-8"), salt=sal, n=2**14, r=8, p=1, dklen=32
    ).hex()


def _guardado() -> dict | None:
    try:
        datos = json.loads(_ruta().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return datos if isinstance(datos, dict) and datos.get("hash") else None


def configurada() -> bool:
    return bool(_clave_env()) or _guardado() is not None


def _verificar(clave: str) -> bool:
    env = _clave_env()
    if env:
        return hmac.compare_digest(clave.encode("utf-8"), env.encode("utf-8"))
    datos = _guardado()
    if datos is None:
        return False
    return hmac.compare_digest(_hash(clave, bytes.fromhex(datos["sal"])), datos["hash"])


def _crear(clave: str) -> None:
    sal = secrets.token_bytes(16)
    ruta = _ruta()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(".tmp")
    temporal.write_text(json.dumps({"hash": _hash(clave, sal), "sal": sal.hex()}), encoding="utf-8")
    try:
        os.chmod(temporal, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    temporal.replace(ruta)


def _nuevo_token() -> tuple[str, float]:
    token = secrets.token_urlsafe(32)
    vence = time.time() + DURACION_S
    _tokens[token] = vence
    # Limpieza de vencidos.
    for viejo in [t for t, v in _tokens.items() if v < time.time()]:
        _tokens.pop(viejo, None)
    return token, vence


def entrar(clave: str) -> tuple[str, float, bool]:
    """(token, vence, recien_creada). Crea la contraseña si todavía no hay."""
    clave = clave or ""
    with _lock:
        ahora = time.time()
        if _fallos["hasta"] > ahora:
            espera = int(_fallos["hasta"] - ahora) + 1
            raise AccesoDenegado(
                f"Demasiados intentos. Esperá {espera} s y probá de nuevo.", 429
            )
        if not configurada():
            if len(clave) < MIN_LARGO:
                raise AccesoDenegado(
                    f"La contraseña tiene que tener al menos {MIN_LARGO} caracteres.", 422
                )
            _crear(clave)
            token, vence = _nuevo_token()
            return token, vence, True
        if not _verificar(clave):
            _fallos["cantidad"] += 1
            if _fallos["cantidad"] >= MAX_FALLOS:
                _fallos["cantidad"] = 0
                _fallos["hasta"] = ahora + ESPERA_FALLOS_S
            raise AccesoDenegado("Contraseña incorrecta.")
        _fallos["cantidad"] = 0
        token, vence = _nuevo_token()
        return token, vence, False


def token_valido(token: str | None) -> bool:
    if not token:
        return False
    with _lock:
        vence = _tokens.get(token)
        if vence is None:
            return False
        if vence < time.time():
            _tokens.pop(token, None)
            return False
        return True


def salir(token: str | None) -> None:
    with _lock:
        _tokens.pop(token or "", None)


def autorizado(token: str | None) -> bool:
    """Sin contraseña configurada la sección está abierta."""
    return not configurada() or token_valido(token)
