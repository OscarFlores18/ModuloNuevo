"""Servicio interno de descifrado de credenciales.

Este es el **único** punto del backend que descifra ``arca.clave`` y
``xubio.pass``. Los datos viven cifrados en disco (ver ``core.storage``) y solo
se abren en memoria cuando un bot autorizado los necesita.

Reglas de diseño:

- ``backend/app/api`` no importa este módulo: la API solo ve las vistas
  públicas de ``core.storage`` (``tiene_clave`` / ``tiene_pass``).
- Los secretos descifrados son :class:`core.secreto.Secreto` (bytes bloqueados
  en RAM y borrables), nunca ``str`` sueltos; su ``repr`` es ``***``.
- **Just-In-Time**: los bots no reciben la contraseña al arrancar, reciben una
  *fuente* (:class:`FuenteCredencial`). Cada vez que van a escribirla en el
  formulario la piden: en ese instante se verifica el consentimiento
  (:class:`Consentimiento`), se descifra, se audita y se entrega. El bot la
  borra de la memoria apenas la inyectó.
- Cada descifrado queda en el registro de auditoría (``core.auditoria``) sin
  el dato sensible.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..core import auditoria, config
from ..core.crypto import descifrar_secreto
from ..core.errors import ConfigInvalida, ConsentimientoRevocado
from ..core.secreto import Secreto
from ..core.storage import ClientesRepository, CredencialesXubioRepository

# --- Consentimiento (JIT) ---------------------------------------------------------

_revocacion_lock = threading.Lock()
_revocado_en = 0.0


def revocar_consentimientos() -> None:
    """Invalida todos los consentimientos otorgados hasta ahora (botón de pánico)."""
    global _revocado_en
    with _revocacion_lock:
        # time.time() puede repetirse en Windows: se fuerza a avanzar.
        _revocado_en = max(time.time(), _revocado_en + 1e-6)


@dataclass
class Consentimiento:
    """El permiso que da el usuario al iniciar una ejecución.

    Vence a los ``MODULONUEVO_CONSENTIMIENTO_MIN`` minutos (por defecto 240),
    se pierde si la ejecución se cancela y se revoca con el botón de pánico.
    """

    usuario: str
    ejecucion: str = ""
    cancelado: Callable[[], bool] = lambda: False
    otorgado: float = field(default_factory=time.time)
    vigencia_s: float = field(
        default_factory=lambda: config.CONSENTIMIENTO_MIN * 60
    )

    def verificar(self) -> None:
        if self.cancelado():
            raise ConsentimientoRevocado(
                "La ejecución se canceló: no se usa la credencial."
            )
        if time.time() > self.otorgado + self.vigencia_s:
            raise ConsentimientoRevocado(
                "El permiso para usar las credenciales venció. Iniciá el control de nuevo."
            )
        with _revocacion_lock:
            revocado = self.otorgado <= _revocado_en
        if revocado:
            raise ConsentimientoRevocado(
                "Las credenciales se borraron o el permiso fue revocado: se aborta."
            )


# --- Credenciales -----------------------------------------------------------------


@dataclass(frozen=True)
class CredencialesArca:
    """Credenciales de ARCA listas para usarse (solo en memoria)."""

    cuit_login: str = ""
    clave: Secreto | str = field(default="", repr=False)
    cuit_representado: str = ""


@dataclass(frozen=True)
class CredencialesXubio:
    """Credenciales del operador de Xubio listas para usarse (solo en memoria)."""

    usuario: str = ""
    clave: Secreto | str = field(default="", repr=False)


class FuenteCredencial:
    """Entrega la credencial *en el momento* de inyectarla (JIT).

    Cada llamada verifica el consentimiento, descifra (o copia la que vino en
    el pedido), audita y devuelve un :class:`Secreto` nuevo que el bot debe
    borrar después del ``fill``. ``cerrar()`` borra lo que quede en memoria.
    """

    def __init__(self, obtener: Callable[[], Secreto], al_cerrar: Callable[[], None] | None = None):
        self._obtener = obtener
        self._al_cerrar = al_cerrar

    def __call__(self) -> Secreto:
        return self._obtener()

    def cerrar(self) -> None:
        if self._al_cerrar is not None:
            self._al_cerrar()

    def __repr__(self) -> str:
        return "FuenteCredencial(***)"


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

    # -- datos sin secretos ------------------------------------------------------
    def datos_arca(self, cliente: str) -> CredencialesArca:
        """CUIT de ingreso y representado, **sin** descifrar la clave."""
        datos = self._clientes.obtener_cifrado(cliente)
        if datos is None:
            raise ConfigInvalida(f"El cliente '{cliente}' no existe en la configuración.")
        arca = datos.get("arca") or {}
        return CredencialesArca(
            cuit_login=str(arca.get("cuit_login") or "").strip(),
            cuit_representado=str(arca.get("cuit_representado") or "").strip(),
        )

    # -- descifrado ------------------------------------------------------------------
    @staticmethod
    def _auditar(
        objetivo: str,
        consentimiento: Consentimiento | None,
        cliente: str = "",
        resultado: str = "ok",
    ) -> None:
        auditoria.registrar(
            "consumo_credencial",
            usuario=consentimiento.usuario if consentimiento else "sistema",
            ejecucion=consentimiento.ejecucion if consentimiento else "",
            objetivo=objetivo,
            cliente=cliente,
            resultado=resultado,
        )

    def arca(
        self, cliente: str, *, consentimiento: Consentimiento | None = None
    ) -> CredencialesArca:
        if consentimiento is not None:
            try:
                consentimiento.verificar()
            except ConsentimientoRevocado:
                self._auditar("arca", consentimiento, cliente, "rechazado")
                raise
        datos = self._clientes.obtener_cifrado(cliente)
        if datos is None:
            raise ConfigInvalida(f"El cliente '{cliente}' no existe en la configuración.")
        arca = datos.get("arca") or {}
        clave = descifrar_secreto(
            arca.get("clave"),
            credencial=f"clave fiscal de ARCA de '{cliente}'",
        )
        self._auditar("arca", consentimiento, cliente)
        return CredencialesArca(
            cuit_login=str(arca.get("cuit_login") or "").strip(),
            clave=clave,
            cuit_representado=str(arca.get("cuit_representado") or "").strip(),
        )

    def xubio(
        self,
        usuario: str | None = None,
        clave: str | Secreto | None = None,
        *,
        recordar: bool = False,
        consentimiento: Consentimiento | None = None,
    ) -> CredencialesXubio:
        """Prioriza lo que manda el pedido; si no, usa lo guardado (cifrado)."""
        if consentimiento is not None:
            try:
                consentimiento.verificar()
            except ConsentimientoRevocado:
                self._auditar("xubio", consentimiento, resultado="rechazado")
                raise
        guardadas = self._xubio.obtener_cifrado()
        usuario_final = (usuario or "").strip() or guardadas["user"]
        del_pedido = Secreto.de(clave.strip() if isinstance(clave, str) else clave)
        if del_pedido:
            clave_final = del_pedido.copia()
        else:
            clave_final = descifrar_secreto(
                guardadas["pass"], credencial="contraseña de Xubio"
            )
        if not usuario_final or not clave_final:
            raise ConfigInvalida(
                "Faltan las credenciales de Xubio: enviá 'xubio_user' y 'xubio_pass' "
                "en el job o guardalas en el backend."
            )
        if recordar:
            with clave_final.revelar() as texto:
                self._xubio.guardar(usuario_final, texto, True)
        self._auditar("xubio", consentimiento)
        return CredencialesXubio(usuario=usuario_final, clave=clave_final)

    # -- fuentes JIT -------------------------------------------------------------
    def fuente_arca(self, cliente: str, consentimiento: Consentimiento) -> FuenteCredencial:
        return FuenteCredencial(
            lambda: Secreto.de(self.arca(cliente, consentimiento=consentimiento).clave)
        )

    def fuente_xubio(
        self, clave_pedido: Secreto | None, consentimiento: Consentimiento
    ) -> FuenteCredencial:
        """Si la contraseña vino en el pedido se guarda solo en un Secreto que
        se borra al cerrar la fuente; si no, se descifra la guardada cada vez."""
        pedido = Secreto.de(clave_pedido) if clave_pedido else None

        def obtener() -> Secreto:
            cred = self.xubio(clave=pedido, consentimiento=consentimiento)
            return Secreto.de(cred.clave)

        def cerrar() -> None:
            if pedido is not None:
                pedido.borrar()

        return FuenteCredencial(obtener, cerrar)

    def hay_clave_xubio_guardada(self) -> bool:
        return bool(self._xubio.obtener_cifrado()["pass"])

    def usuario_xubio_guardado(self) -> str:
        return self._xubio.obtener_cifrado()["user"]

    # -- botón de pánico ------------------------------------------------------------
    def borrar_todo(self, usuario: str) -> dict[str, int]:
        """Borrado físico e inmediato de todas las credenciales y sus DEK."""
        revocar_consentimientos()
        arca = self._clientes.borrar_claves()
        xubio = int(self._xubio.borrar())
        auditoria.registrar(
            "borrado_credenciales",
            usuario=usuario,
            objetivo="todas",
            resultado=f"arca={arca} xubio={xubio}",
        )
        return {"arca": arca, "xubio": xubio}

    def borrar_arca(self, cliente: str, usuario: str) -> int:
        revocar_consentimientos()
        borradas = self._clientes.borrar_claves(cliente)
        auditoria.registrar(
            "borrado_credenciales",
            usuario=usuario,
            objetivo="arca",
            cliente=cliente,
            resultado=f"arca={borradas}",
        )
        return borradas

    def borrar_xubio(self, usuario: str) -> int:
        revocar_consentimientos()
        borradas = int(self._xubio.borrar())
        auditoria.registrar(
            "borrado_credenciales",
            usuario=usuario,
            objetivo="xubio",
            resultado=f"xubio={borradas}",
        )
        return borradas

    # -- mantenimiento ----------------------------------------------------------------
    def migrar(self) -> int:
        """Pasa a ``enc:v2`` lo que esté en texto plano o en ``enc:v1``."""
        return self._clientes.cifrar_pendientes() + self._xubio.cifrar_pendientes()

    def reenvolver(self, funcion: Callable[[str], str]) -> int:
        """Aplica ``funcion`` (re-envolver DEK) a todas las credenciales."""
        return self._clientes.transformar_secretos(funcion) + self._xubio.transformar_secretos(
            funcion
        )
