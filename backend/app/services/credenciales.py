"""Servicio interno de descifrado de credenciales.

Este es el **único** punto del backend que descifra ``arca.clave`` y
``xubio.pass``. Los datos viven cifrados en disco (ver ``core.storage``) y solo
se abren en memoria cuando un bot autorizado los necesita.

Reglas de diseño:

- ``backend/app/api`` no importa este módulo: la API solo ve las vistas
  públicas de ``core.storage`` (``tiene_clave`` / ``tiene_pass``).
- Los dataclasses usan ``repr=False`` en los secretos para que un log o un
  traceback accidental no los imprima.
- La contraseña se entrega al bot en el momento de ejecutar el paso; no se
  guarda en el request, ni en el estado de la ejecución, ni en el log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..core.crypto import descifrar
from ..core.errors import ConfigInvalida
from ..core.storage import ClientesRepository, CredencialesXubioRepository


@dataclass(frozen=True)
class CredencialesArca:
    """Credenciales de ARCA listas para usarse (solo en memoria)."""

    cuit_login: str = ""
    clave: str = field(default="", repr=False)
    cuit_representado: str = ""


@dataclass(frozen=True)
class CredencialesXubio:
    """Credenciales del operador de Xubio listas para usarse (solo en memoria)."""

    usuario: str = ""
    clave: str = field(default="", repr=False)


class ProveedorCredenciales(Protocol):
    """Lo que necesita el pipeline de bots para pedir credenciales."""

    def arca(self, cliente: str) -> CredencialesArca: ...

    def xubio(
        self,
        usuario: str | None = None,
        clave: str | None = None,
        *,
        recordar: bool = False,
    ) -> CredencialesXubio: ...


class ServicioCredenciales:
    """Descifra credenciales y las entrega al componente que las pide."""

    def __init__(
        self,
        clientes: ClientesRepository,
        xubio: CredencialesXubioRepository,
    ):
        self._clientes = clientes
        self._xubio = xubio

    def arca(self, cliente: str) -> CredencialesArca:
        datos = self._clientes.obtener_cifrado(cliente)
        if datos is None:
            raise ConfigInvalida(f"El cliente '{cliente}' no existe en la configuración.")
        arca = datos.get("arca") or {}
        return CredencialesArca(
            cuit_login=str(arca.get("cuit_login") or "").strip(),
            clave=descifrar(
                arca.get("clave"),
                credencial=f"clave fiscal de ARCA de '{cliente}'",
            ),
            cuit_representado=str(arca.get("cuit_representado") or "").strip(),
        )

    def xubio(
        self,
        usuario: str | None = None,
        clave: str | None = None,
        *,
        recordar: bool = False,
    ) -> CredencialesXubio:
        """Prioriza lo que manda el pedido; si no, usa lo guardado (cifrado)."""
        guardadas = self._xubio.obtener_cifrado()
        usuario_final = (usuario or "").strip() or guardadas["user"]
        clave_final = (clave or "").strip() or descifrar(
            guardadas["pass"],
            credencial="contraseña de Xubio",
        )
        if not usuario_final or not clave_final:
            raise ConfigInvalida(
                "Faltan las credenciales de Xubio: enviá 'xubio_user' y 'xubio_pass' "
                "en el job o guardalas en el backend."
            )
        if recordar:
            self._xubio.guardar(usuario_final, clave_final, True)
        return CredencialesXubio(usuario=usuario_final, clave=clave_final)

    def migrar(self) -> int:
        """Cifra lo que haya quedado en texto plano. Devuelve cuántos valores."""
        return self._clientes.cifrar_pendientes() + self._xubio.cifrar_pendientes()
