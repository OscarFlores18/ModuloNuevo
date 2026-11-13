"""Cifrado en reposo de credenciales (Fernet).

Formato de un valor cifrado: ``enc:v1:<token>``.

Reglas:

- ``cifrar`` es idempotente: un valor que ya tiene el prefijo queda igual.
- ``descifrar`` acepta texto plano heredado y lo devuelve tal cual, así los
  JSON viejos siguen funcionando hasta que la migración los cifre.
- La clave maestra sale de ``MODULONUEVO_MASTER_KEY``. Si no está definida se
  genera una clave local en ``DATA_DIR/fernet.key`` con permisos de solo dueño,
  para que el modo local funcione sin configuración. En un servidor conviene
  definir la variable: si se pierde el archivo, lo cifrado no se recupera.

Este módulo no decide *quién* puede descifrar: eso lo hace
``services.credenciales.ServicioCredenciales``, el único punto del backend que
entrega credenciales en claro a los bots.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import config
from .errors import ConfigInvalida, CredencialInvalida

PREFIJO = "enc:v1:"
NOMBRE_ARCHIVO_CLAVE = "fernet.key"

# Sal e info fijas: la clave se deriva siempre igual a partir del secreto de
# entorno, así un reinicio no invalida lo que ya está cifrado.
_INFO = b"modulonuevo-credenciales-v1"

_cache: dict[str, Fernet] = {}
_lock = threading.RLock()


def _derivar(secreto: str) -> bytes:
    """Convierte un secreto arbitrario en una clave Fernet válida."""
    material = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO
    ).derive(secreto.encode("utf-8"))
    return base64.urlsafe_b64encode(material)


def _leer_o_crear_archivo(ruta: Path) -> bytes:
    try:
        existente = ruta.read_bytes().strip()
    except FileNotFoundError:
        existente = b""
    if existente:
        return existente

    ruta.parent.mkdir(parents=True, exist_ok=True)
    nueva = Fernet.generate_key()
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    temporal.write_bytes(nueva)
    try:
        os.chmod(temporal, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # En Windows el chmod es limitado; no se rompe por eso.
        pass
    temporal.replace(ruta)
    return nueva


def _clave() -> tuple[str, bytes]:
    """Identificador de la clave vigente + material, resueltos en cada llamada.

    Se resuelve en cada llamada (y no al importar) para que los tests puedan
    apuntar ``config.DATA_DIR`` a una carpeta temporal.
    """
    secreto = (os.getenv("MODULONUEVO_MASTER_KEY") or "").strip()
    if secreto:
        # El identificador no es el secreto: es su hash, para distinguir claves
        # distintas en la caché sin guardar el secreto como texto.
        huella = hashlib.sha256(secreto.encode("utf-8")).hexdigest()
        return f"env:{huella}", _derivar(secreto)

    ruta = Path(config.DATA_DIR) / NOMBRE_ARCHIVO_CLAVE
    return str(ruta.resolve()), _leer_o_crear_archivo(ruta)


def _fernet() -> Fernet:
    with _lock:
        # Se resuelve dentro del lock: si dos hilos arrancan juntos y no existe
        # fernet.key, uno solo genera el archivo y el otro reutiliza esa clave.
        identificador, clave = _clave()
        cacheada = _cache.get(identificador)
        if cacheada is not None:
            return cacheada
        try:
            instancia = Fernet(clave)
        except (TypeError, ValueError) as exc:
            raise ConfigInvalida(
                "La clave de cifrado de credenciales no es válida. Revisá "
                "MODULONUEVO_MASTER_KEY o el archivo fernet.key."
            ) from exc
        _cache[identificador] = instancia
        return instancia


def esta_cifrado(valor: object) -> bool:
    """True si el valor ya está en el formato ``enc:v1:...``."""
    return isinstance(valor, str) and valor.startswith(PREFIJO)


def cifrar(valor: str) -> str:
    """Cifra un valor. Vacío o ya cifrado se devuelve sin cambios."""
    texto = str(valor or "")
    if not texto or esta_cifrado(texto):
        return texto
    token = _fernet().encrypt(texto.encode("utf-8")).decode("ascii")
    return PREFIJO + token


def descifrar(valor: str, *, credencial: str = "credencial") -> str:
    """Descifra un valor. El texto plano heredado se devuelve tal cual.

    Si el token no se puede abrir, el error **no** incluye el valor cifrado ni
    la clave maestra: solo el nombre de la credencial y qué revisar.
    """
    texto = str(valor or "")
    if not texto or not esta_cifrado(texto):
        return texto

    token = texto[len(PREFIJO) :]
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise CredencialInvalida(
            f"No se pudo descifrar la {credencial}. La clave maestra cambió o "
            "el dato guardado está dañado: volvé a cargar la credencial."
        ) from exc
