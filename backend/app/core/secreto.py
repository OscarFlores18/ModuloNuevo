"""Secretos en memoria: bytes bloqueados y borrables.

Python no tiene un ``SecureString``: los ``str`` son inmutables y el recolector
puede dejar copias en la RAM. Lo más cercano que se puede hacer, y es lo que
hace esta clase, es:

- guardar el secreto en un ``bytearray`` (mutable), nunca en un ``str``;
- bloquear esas páginas en RAM (``mlock`` / ``VirtualLock``) para que el
  sistema no las mande al swap, si el sistema lo permite;
- sobrescribirlo con ceros (``borrar``) apenas se inyectó en el formulario;
- exponerlo como ``str`` solo dentro de ``with secreto.revelar() as texto:``,
  el tiempo justo de la llamada a ``page.fill``. Ese ``str`` temporal es
  inevitable (Playwright lo pide así) y queda a merced del recolector: por eso
  se crea tarde y se suelta enseguida.

``repr``/``str``/``format`` nunca muestran el valor.
"""

from __future__ import annotations

import ctypes
import hmac
import os
import secrets
import sys
from contextlib import contextmanager
from typing import Iterator

# Clave efímera del proceso para la huella: permite comparar secretos (p. ej.
# reutilizar una sesión del bot) sin guardar un hash crackeable offline.
_CLAVE_HUELLA = secrets.token_bytes(32)


def _bloqueador():
    """(lock, unlock) del sistema, o (None, None) si no hay."""
    try:
        if os.name == "nt":
            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            return k32.VirtualLock, k32.VirtualUnlock
        libc = ctypes.CDLL(None if sys.platform != "darwin" else "libc.dylib")
        return libc.mlock, libc.munlock
    except Exception:  # pragma: no cover - depende del sistema
        return None, None


_LOCK, _UNLOCK = _bloqueador()


def _direccion(buffer: bytearray) -> tuple[int, int]:
    if not buffer:
        return 0, 0
    c = (ctypes.c_char * len(buffer)).from_buffer(buffer)
    return ctypes.addressof(c), len(buffer)


class Secreto:
    """Un secreto en un ``bytearray`` bloqueado en RAM y borrable."""

    __slots__ = ("_datos", "_bloqueado", "__weakref__")

    def __init__(self, valor: str | bytes | bytearray | "Secreto" | None = None):
        if isinstance(valor, Secreto):
            datos = bytearray(valor._datos)
        elif isinstance(valor, (bytes, bytearray)):
            datos = bytearray(valor)
        else:
            datos = bytearray(str(valor or "").encode("utf-8"))
        self._datos = datos
        self._bloqueado = False
        self._bloquear()

    @classmethod
    def de(cls, valor: "str | bytes | bytearray | Secreto | None") -> "Secreto":
        return valor if isinstance(valor, Secreto) else cls(valor)

    # -- memoria ------------------------------------------------------------
    def _bloquear(self) -> None:
        if _LOCK is None or not self._datos:
            return
        try:
            direccion, largo = _direccion(self._datos)
            resultado = _LOCK(ctypes.c_void_p(direccion), ctypes.c_size_t(largo))
            # VirtualLock devuelve != 0 si anduvo; mlock devuelve 0.
            self._bloqueado = bool(resultado) if os.name == "nt" else resultado == 0
        except Exception:  # pragma: no cover - sin permisos para mlock
            self._bloqueado = False

    def borrar(self) -> None:
        """Sobrescribe el secreto con ceros y lo deja vacío."""
        datos = self._datos
        if not datos:
            return
        for i in range(len(datos)):
            datos[i] = 0
        if self._bloqueado and _UNLOCK is not None:
            try:
                direccion, largo = _direccion(datos)
                _UNLOCK(ctypes.c_void_p(direccion), ctypes.c_size_t(largo))
            except Exception:  # pragma: no cover
                pass
        self._bloqueado = False
        datos.clear()

    def __del__(self) -> None:
        try:
            self.borrar()
        except Exception:
            pass

    # -- uso ------------------------------------------------------------------
    @contextmanager
    def revelar(self) -> Iterator[str]:
        """El secreto como ``str`` solo dentro del bloque ``with``."""
        texto = bytes(self._datos).decode("utf-8")
        try:
            yield texto
        finally:
            del texto

    def copia(self) -> "Secreto":
        return Secreto(self)

    def huella(self) -> str:
        """Identificador estable dentro del proceso; no revela el secreto."""
        return hmac.new(_CLAVE_HUELLA, bytes(self._datos), "sha256").hexdigest()

    @property
    def borrado(self) -> bool:
        return not self._datos

    def __len__(self) -> int:
        return len(self._datos)

    def __bool__(self) -> bool:
        return bool(self._datos)

    def __eq__(self, otro: object) -> bool:
        if isinstance(otro, Secreto):
            return hmac.compare_digest(bytes(self._datos), bytes(otro._datos))
        if isinstance(otro, str):
            return hmac.compare_digest(bytes(self._datos), otro.encode("utf-8"))
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return "Secreto(***)"

    __str__ = __repr__

    def __format__(self, _spec: str) -> str:
        return "***"

    def __reduce__(self):  # no se serializa con pickle
        raise TypeError("Un Secreto no se puede serializar.")
