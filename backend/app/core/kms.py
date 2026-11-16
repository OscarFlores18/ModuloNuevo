"""Gestión de la KEK (Key Encryption Key) para el cifrado de sobre.

La KEK nunca se guarda junto a los datos: solo envuelve (cifra) las DEK, una
por credencial. Hay dos proveedores, elegidos con ``MODULONUEVO_KMS``:

``vault`` (recomendado en producción)
    HashiCorp Vault, motor *Transit*. La KEK vive y opera **dentro** de Vault y
    nunca sale de él: el backend manda la DEK, Vault devuelve la DEK envuelta.
    Variables: ``VAULT_ADDR``, ``VAULT_TOKEN``, ``MODULONUEVO_VAULT_KEY``
    (nombre de la clave, por defecto ``modulonuevo-kek``),
    ``MODULONUEVO_VAULT_MOUNT`` (por defecto ``transit``) y, para mTLS,
    ``VAULT_CACERT``, ``VAULT_CLIENT_CERT``, ``VAULT_CLIENT_KEY``.

``local`` (por defecto, para desarrollo o una sola PC)
    Un llavero JSON con versiones de la KEK en ``MODULONUEVO_KEK_FILE`` (por
    defecto ``DATA_DIR/kms/keyring.json``, fuera de ``config.json``). La KEK
    envuelve cada DEK con AES-256-GCM. Conviene que ese archivo viva en otro
    volumen que los datos.

Ambos versionan la KEK y saben rotarla; ``core.crypto.reenvolver`` re-cifra
las DEK con la versión nueva sin tocar el texto cifrado de la credencial.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import stat
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import config
from .errors import ConfigInvalida, CredencialInvalida


def _b64e(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).decode("ascii").rstrip("=")


def _b64d(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class ProveedorKms(Protocol):
    nombre: str

    def envolver(self, dek: bytes) -> str:
        """DEK en claro -> DEK envuelta (texto ASCII que incluye la versión)."""

    def desenvolver(self, envuelta: str) -> bytearray:
        """DEK envuelta -> DEK en claro (``bytearray`` para poder borrarla)."""

    def version_de(self, envuelta: str) -> str: ...

    def version_actual(self) -> str: ...

    def rotar(self) -> str:
        """Crea una versión nueva de la KEK y la deja como vigente."""

    def antiguedad_dias(self) -> float | None:
        """Días desde que se creó la versión vigente (None si no se sabe)."""


# --- Proveedor local ------------------------------------------------------------


class KmsLocal:
    """Llavero local con versiones de la KEK (AES-256-GCM)."""

    nombre = "local"
    _AAD = b"modulonuevo-kek-wrap|"

    def __init__(self, ruta: Path):
        self.ruta = Path(ruta)
        self._lock = threading.RLock()

    # -- llavero --------------------------------------------------------------
    def _leer(self) -> dict:
        try:
            datos = json.loads(self.ruta.read_text(encoding="utf-8"))
        except FileNotFoundError:
            datos = {}
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigInvalida(
                "El llavero de la KEK está dañado o no se puede leer. Revisá "
                "MODULONUEVO_KEK_FILE."
            ) from exc
        if not datos.get("versiones"):
            datos = self._crear_version({"versiones": {}})
        return datos

    def _escribir(self, datos: dict) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal = self.ruta.with_suffix(self.ruta.suffix + ".tmp")
        temporal.write_text(json.dumps(datos, indent=2), encoding="utf-8")
        try:
            os.chmod(temporal, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        temporal.replace(self.ruta)

    def _crear_version(self, datos: dict) -> dict:
        versiones = datos.setdefault("versiones", {})
        numero = max((int(v[1:]) for v in versiones), default=0) + 1
        version = f"k{numero}"
        versiones[version] = {
            "clave": _b64e(AESGCM.generate_key(bit_length=256)),
            "creada": _ahora().isoformat(),
        }
        datos["actual"] = version
        self._escribir(datos)
        return datos

    def _material(self, datos: dict, version: str) -> bytes:
        info = datos["versiones"].get(version)
        if not info:
            raise CredencialInvalida(
                "La credencial fue cifrada con una versión de la KEK que ya no "
                "existe en el llavero: volvé a cargarla."
            )
        return _b64d(info["clave"])

    # -- API ------------------------------------------------------------------
    def version_actual(self) -> str:
        with self._lock:
            return self._leer()["actual"]

    def version_de(self, envuelta: str) -> str:
        return envuelta.split(".", 1)[0]

    def envolver(self, dek: bytes) -> str:
        with self._lock:
            datos = self._leer()
            version = datos["actual"]
            kek = self._material(datos, version)
        nonce = os.urandom(12)
        ct = AESGCM(kek).encrypt(nonce, bytes(dek), self._AAD + version.encode())
        return f"{version}.{_b64e(nonce + ct)}"

    def desenvolver(self, envuelta: str) -> bytearray:
        try:
            version, cuerpo = envuelta.split(".", 1)
            crudo = _b64d(cuerpo)
        except ValueError as exc:
            raise CredencialInvalida("La DEK envuelta está dañada.") from exc
        with self._lock:
            kek = self._material(self._leer(), version)
        try:
            return bytearray(
                AESGCM(kek).decrypt(crudo[:12], crudo[12:], self._AAD + version.encode())
            )
        except Exception as exc:
            raise CredencialInvalida("La DEK envuelta no es auténtica.") from exc

    def rotar(self) -> str:
        with self._lock:
            return self._crear_version(self._leer())["actual"]

    def antiguedad_dias(self) -> float | None:
        with self._lock:
            datos = self._leer()
            creada = datos["versiones"][datos["actual"]].get("creada")
        if not creada:
            return None
        return (_ahora() - datetime.fromisoformat(creada)).total_seconds() / 86400


# --- HashiCorp Vault (Transit) -------------------------------------------------------


class KmsVault:
    """KEK dentro de Vault Transit. La clave nunca sale de Vault."""

    nombre = "vault"

    def __init__(
        self,
        direccion: str,
        token: str,
        clave: str = "modulonuevo-kek",
        montaje: str = "transit",
        *,
        ca: str | None = None,
        cert_cliente: str | None = None,
        clave_cliente: str | None = None,
        timeout: float = 10.0,
    ):
        if not direccion or not token:
            raise ConfigInvalida(
                "MODULONUEVO_KMS=vault necesita VAULT_ADDR y VAULT_TOKEN."
            )
        self._base = direccion.rstrip("/") + f"/v1/{montaje.strip('/')}"
        self._token = token
        self._clave = clave
        self._timeout = timeout
        self._ssl: ssl.SSLContext | None = None
        if direccion.startswith("https://"):
            self._ssl = ssl.create_default_context(cafile=ca or None)
            self._ssl.minimum_version = ssl.TLSVersion.TLSv1_2
            if cert_cliente:
                # mTLS: Vault exige el certificado de este servidor.
                self._ssl.load_cert_chain(cert_cliente, clave_cliente or None)

    def __repr__(self) -> str:  # el token nunca va a un log
        return f"KmsVault(clave={self._clave!r})"

    def _pedir(self, metodo: str, ruta: str, cuerpo: dict | None = None) -> dict:
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        pedido = urllib.request.Request(
            f"{self._base}/{ruta}",
            data=datos,
            method=metodo,
            headers={"X-Vault-Token": self._token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(
                pedido, timeout=self._timeout, context=self._ssl
            ) as resp:
                texto = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # El cuerpo de Vault no lleva secretos, pero se recorta igual.
            raise ConfigInvalida(
                f"Vault rechazó la operación '{ruta.split('/')[0]}' (HTTP {exc.code})."
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ConfigInvalida(f"No se pudo contactar a Vault: {type(exc).__name__}.") from None
        return json.loads(texto) if texto else {}

    def version_de(self, envuelta: str) -> str:
        # Formato de Vault: "vault:v3:<base64>".
        return ":".join(envuelta.split(":", 2)[:2])

    def version_actual(self) -> str:
        info = self._pedir("GET", f"keys/{self._clave}")["data"]
        return f"vault:v{info['latest_version']}"

    def envolver(self, dek: bytes) -> str:
        r = self._pedir(
            "POST",
            f"encrypt/{self._clave}",
            {"plaintext": base64.b64encode(bytes(dek)).decode("ascii")},
        )
        return r["data"]["ciphertext"]

    def desenvolver(self, envuelta: str) -> bytearray:
        r = self._pedir("POST", f"decrypt/{self._clave}", {"ciphertext": envuelta})
        return bytearray(base64.b64decode(r["data"]["plaintext"]))

    def reenvolver_remoto(self, envuelta: str) -> str:
        """Vault re-cifra la DEK con la versión vigente sin exponerla."""
        r = self._pedir("POST", f"rewrap/{self._clave}", {"ciphertext": envuelta})
        return r["data"]["ciphertext"]

    def rotar(self) -> str:
        self._pedir("POST", f"keys/{self._clave}/rotate", {})
        return self.version_actual()

    def antiguedad_dias(self) -> float | None:
        info = self._pedir("GET", f"keys/{self._clave}")["data"]
        creada = (info.get("keys") or {}).get(str(info["latest_version"]))
        if isinstance(creada, (int, float)):
            momento = datetime.fromtimestamp(creada, timezone.utc)
        elif isinstance(creada, str):
            momento = datetime.fromisoformat(creada.replace("Z", "+00:00"))
        else:
            return None
        return (_ahora() - momento).total_seconds() / 86400


# --- Selección ----------------------------------------------------------------------

_cache: dict[tuple, ProveedorKms] = {}
_cache_lock = threading.Lock()


def proveedor() -> ProveedorKms:
    """El KMS configurado (se resuelve en cada llamada para los tests)."""
    tipo = (os.getenv("MODULONUEVO_KMS") or "local").strip().lower()
    if tipo == "vault":
        clave = (
            "vault",
            os.getenv("VAULT_ADDR", ""),
            os.getenv("MODULONUEVO_VAULT_KEY", "modulonuevo-kek"),
        )
        with _cache_lock:
            if clave not in _cache:
                _cache[clave] = KmsVault(
                    os.getenv("VAULT_ADDR", ""),
                    os.getenv("VAULT_TOKEN", ""),
                    os.getenv("MODULONUEVO_VAULT_KEY", "modulonuevo-kek"),
                    os.getenv("MODULONUEVO_VAULT_MOUNT", "transit"),
                    ca=os.getenv("VAULT_CACERT"),
                    cert_cliente=os.getenv("VAULT_CLIENT_CERT"),
                    clave_cliente=os.getenv("VAULT_CLIENT_KEY"),
                )
            return _cache[clave]
    if tipo != "local":
        raise ConfigInvalida(f"MODULONUEVO_KMS='{tipo}' no es válido (local | vault).")

    ruta_env = os.getenv("MODULONUEVO_KEK_FILE", "").strip()
    ruta = Path(ruta_env).expanduser() if ruta_env else Path(config.DATA_DIR) / "kms" / "keyring.json"
    clave = ("local", str(ruta.resolve()))
    with _cache_lock:
        if clave not in _cache:
            _cache[clave] = KmsLocal(ruta)
        return _cache[clave]
