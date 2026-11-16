"""Cifrado en reposo de credenciales: cifrado de sobre con AES-256-GCM.

Formato vigente (``enc:v2``)::

    enc:v2:<DEK envuelta por la KEK, base64url>.<nonce + ciphertext + tag, base64url>

- Cada credencial tiene su **propia DEK** (32 bytes aleatorios, ``os.urandom``).
- La credencial se cifra con **AES-256-GCM** usando la DEK: confidencialidad y
  autenticidad (si alguien toca un byte, el descifrado falla).
- La DEK se guarda **envuelta** por la KEK del KMS (ver :mod:`core.kms`). La
  KEK nunca se escribe en ``config.json`` ni en ningún archivo de datos.
- Rotar la KEK no re-cifra las credenciales: ``reenvolver`` solo vuelve a
  envolver la DEK con la versión nueva.

Formato heredado (``enc:v1``, Fernet con ``MODULONUEVO_MASTER_KEY`` o
``DATA_DIR/fernet.key``): se sigue pudiendo **leer** para no romper datos
viejos, y ``migrar_valor`` lo pasa a ``enc:v2`` al arrancar.

Reglas:

- ``cifrar`` es idempotente: un valor que ya está cifrado queda igual.
- ``descifrar`` acepta texto plano heredado y lo devuelve tal cual.
- Ningún error incluye el valor cifrado, la DEK ni la KEK.

Este módulo no decide *quién* puede descifrar: eso lo hace
``services.credenciales.ServicioCredenciales``.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import threading
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import config, kms
from .errors import ConfigInvalida, CredencialInvalida
from .secreto import Secreto

PREFIJO = "enc:v2:"
PREFIJO_V1 = "enc:v1:"
_AAD = b"modulonuevo-credencial-v2"

NOMBRE_ARCHIVO_CLAVE = "fernet.key"
_INFO_V1 = b"modulonuevo-credenciales-v1"
_cache_v1: dict[str, Fernet] = {}
_lock_v1 = threading.RLock()


def _b64e(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).decode("ascii").rstrip("=")


def _b64d(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _borrar(buffer: bytearray) -> None:
    for i in range(len(buffer)):
        buffer[i] = 0


# --- v2: cifrado de sobre ---------------------------------------------------------


def esta_cifrado(valor: object) -> bool:
    """True si el valor está cifrado (cualquier versión)."""
    return isinstance(valor, str) and valor.startswith(("enc:v2:", "enc:v1:"))


def es_formato_vigente(valor: object) -> bool:
    return isinstance(valor, str) and valor.startswith(PREFIJO)


def cifrar(valor: str | Secreto) -> str:
    """Cifra un valor con una DEK nueva. Vacío o ya cifrado se devuelve igual."""
    if isinstance(valor, Secreto):
        if not valor:
            return ""
        with valor.revelar() as texto:
            return _cifrar_v2(texto.encode("utf-8"))
    texto = str(valor or "")
    if not texto or esta_cifrado(texto):
        return texto
    return _cifrar_v2(texto.encode("utf-8"))


def _cifrar_v2(plano: bytes) -> str:
    dek = bytearray(AESGCM.generate_key(bit_length=256))
    try:
        nonce = os.urandom(12)
        ct = AESGCM(bytes(dek)).encrypt(nonce, plano, _AAD)
        envuelta = kms.proveedor().envolver(bytes(dek))
    finally:
        _borrar(dek)
    return f"{PREFIJO}{_b64e(envuelta.encode('ascii'))}.{_b64e(nonce + ct)}"


def _partes_v2(texto: str) -> tuple[str, bytes]:
    try:
        envuelta_b64, cuerpo_b64 = texto[len(PREFIJO) :].split(".", 1)
        return _b64d(envuelta_b64).decode("ascii"), _b64d(cuerpo_b64)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("formato v2 inválido") from exc


def _descifrar_v2(texto: str) -> bytearray:
    envuelta, cuerpo = _partes_v2(texto)
    dek = kms.proveedor().desenvolver(envuelta)
    try:
        return bytearray(AESGCM(bytes(dek)).decrypt(cuerpo[:12], cuerpo[12:], _AAD))
    finally:
        _borrar(dek)


def descifrar_secreto(valor: str, *, credencial: str = "credencial") -> Secreto:
    """Descifra a un :class:`Secreto` (bytes bloqueados y borrables)."""
    texto = str(valor or "")
    if not texto or not esta_cifrado(texto):
        return Secreto(texto)
    try:
        if texto.startswith(PREFIJO):
            plano = _descifrar_v2(texto)
        else:
            plano = bytearray(_descifrar_v1(texto))
    except CredencialInvalida as exc:
        raise CredencialInvalida(
            f"No se pudo descifrar la {credencial}. {exc.mensaje}"
        ) from None
    except (InvalidTag, InvalidToken, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise CredencialInvalida(
            f"No se pudo descifrar la {credencial}. La clave maestra cambió o "
            "el dato guardado está dañado: volvé a cargar la credencial."
        ) from exc
    try:
        return Secreto(plano)
    finally:
        _borrar(plano)


def descifrar(valor: str, *, credencial: str = "credencial") -> str:
    """Como :func:`descifrar_secreto` pero devuelve ``str``.

    Se conserva por compatibilidad; el código nuevo usa ``descifrar_secreto``.
    """
    secreto = descifrar_secreto(valor, credencial=credencial)
    try:
        with secreto.revelar() as texto:
            return texto
    finally:
        secreto.borrar()


def version_kek(valor: str) -> str | None:
    """La versión de la KEK que envuelve la DEK de este valor (solo v2)."""
    if not es_formato_vigente(valor):
        return None
    try:
        envuelta, _ = _partes_v2(valor)
    except ValueError:
        return None
    return kms.proveedor().version_de(envuelta)


def reenvolver(valor: str) -> str:
    """Re-envuelve la DEK con la KEK vigente. El ciphertext no cambia."""
    if not es_formato_vigente(valor):
        return valor
    proveedor = kms.proveedor()
    envuelta, cuerpo = _partes_v2(valor)
    if proveedor.version_de(envuelta) == proveedor.version_actual():
        return valor
    reenvolver_remoto = getattr(proveedor, "reenvolver_remoto", None)
    if reenvolver_remoto is not None:
        nueva = reenvolver_remoto(envuelta)
    else:
        dek = proveedor.desenvolver(envuelta)
        try:
            nueva = proveedor.envolver(bytes(dek))
        finally:
            _borrar(dek)
    return f"{PREFIJO}{_b64e(nueva.encode('ascii'))}.{_b64e(cuerpo)}"


def migrar_valor(valor: str) -> str:
    """Texto plano o ``enc:v1`` -> ``enc:v2``. Lo vigente queda igual."""
    texto = str(valor or "")
    if not texto or es_formato_vigente(texto):
        return texto
    secreto = descifrar_secreto(texto)
    try:
        return cifrar(secreto)
    finally:
        secreto.borrar()


# --- v1 (heredado, solo lectura) -------------------------------------------------


def _derivar_v1(secreto: str) -> bytes:
    material = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO_V1
    ).derive(secreto.encode("utf-8"))
    return base64.urlsafe_b64encode(material)


def _clave_v1() -> tuple[str, bytes]:
    secreto = (os.getenv("MODULONUEVO_MASTER_KEY") or "").strip()
    if secreto:
        huella = hashlib.sha256(secreto.encode("utf-8")).hexdigest()
        return f"env:{huella}", _derivar_v1(secreto)
    ruta = Path(config.DATA_DIR) / NOMBRE_ARCHIVO_CLAVE
    try:
        existente = ruta.read_bytes().strip()
    except FileNotFoundError:
        existente = b""
    if not existente:
        # Sin clave v1 no hay forma de abrir datos v1: se informa como dañado.
        raise ValueError("no hay clave v1")
    return str(ruta.resolve()), existente


def _descifrar_v1(texto: str) -> bytes:
    with _lock_v1:
        identificador, clave = _clave_v1()
        fernet = _cache_v1.get(identificador)
        if fernet is None:
            try:
                fernet = Fernet(clave)
            except (TypeError, ValueError) as exc:
                raise ConfigInvalida(
                    "La clave heredada (MODULONUEVO_MASTER_KEY o fernet.key) no es válida."
                ) from exc
            _cache_v1[identificador] = fernet
    return fernet.decrypt(texto[len(PREFIJO_V1) :].encode("ascii"))


def cifrar_v1_para_tests(valor: str) -> str:
    """Genera un valor ``enc:v1`` (solo para probar la migración)."""
    ruta = Path(config.DATA_DIR) / NOMBRE_ARCHIVO_CLAVE
    if not (os.getenv("MODULONUEVO_MASTER_KEY") or "").strip() and not ruta.exists():
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(Fernet.generate_key())
        try:
            os.chmod(ruta, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
    with _lock_v1:
        _, clave = _clave_v1()
    return PREFIJO_V1 + Fernet(clave).encrypt(valor.encode("utf-8")).decode("ascii")
