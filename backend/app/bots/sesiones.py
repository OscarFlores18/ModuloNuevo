"""Orquestación de sesiones de los bots.

Un job abre **una** sesión de ARCA por CUIT de ingreso y **una** de Xubio por
operador, y las reutiliza para todos los clientes y pasos. Antes esto abría un
navegador y hacía login por cada llamada al bot (dos veces por cliente en ARCA,
una por cliente en Xubio).

`GestorSesiones` se crea y se usa dentro del hilo que corre el job: Playwright
sync no permite tocar un navegador desde otro hilo. Al terminar el job se llama
a `cerrar()` y los navegadores quedan cerrados.
"""

from __future__ import annotations

from typing import Callable

from .arca import SesionArca
from .xubio import SesionXubio

Log = Callable[[str], None]


class GestorSesiones:
    def __init__(self, log: Log = print) -> None:
        self.log = log
        self._arca: dict[tuple[str, str], SesionArca] = {}
        self._xubio: SesionXubio | None = None

    # -- ARCA --------------------------------------------------------------
    def arca(self, cuit_login: str, clave: str) -> SesionArca:
        """La sesión de ARCA para esas credenciales (se crea la primera vez)."""
        clave_sesion = ((cuit_login or "").strip(), clave or "")
        sesion = self._arca.get(clave_sesion)
        if sesion is None:
            sesion = SesionArca(cuit_login, clave, log=self.log)
            sesion.abrir()
            self._arca[clave_sesion] = sesion
        else:
            sesion.asegurar_sesion()
        return sesion

    # -- Xubio -------------------------------------------------------------
    def xubio(self, usuario: str, password: str) -> SesionXubio:
        """La única sesión de Xubio del job (se crea la primera vez)."""
        if self._xubio is None:
            sesion = SesionXubio(usuario, password, log=self.log)
            sesion.abrir()
            self._xubio = sesion
        return self._xubio

    def descartar_xubio(self) -> None:
        """Cierra la sesión de Xubio para que el próximo cliente vuelva a entrar."""
        if self._xubio is not None:
            try:
                self._xubio.cerrar()
            finally:
                self._xubio = None

    # -- cierre ------------------------------------------------------------
    def cerrar(self) -> None:
        for sesion in list(self._arca.values()):
            try:
                sesion.cerrar()
            except Exception:
                pass
        self._arca.clear()
        if self._xubio is not None:
            try:
                self._xubio.cerrar()
            except Exception:
                pass
            self._xubio = None

    def __enter__(self) -> "GestorSesiones":
        return self

    def __exit__(self, *exc) -> None:
        self.cerrar()
