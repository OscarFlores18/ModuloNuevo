"""Errores tipados del dominio.

Regla del proyecto: los bots nunca fallan con `Exception` genérica. Cada fallo
tiene un `code` estable que la API puede devolver al frontend y que el
frontend puede traducir a una acción concreta.
"""

from __future__ import annotations

from typing import Any


class BotError(Exception):
    """Base de todos los errores de negocio."""

    code = "bot_error"

    def __init__(self, mensaje: str, *, detalle: Any = None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.detalle = detalle

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "mensaje": self.mensaje, "detalle": self.detalle}


class ConfigInvalida(BotError):
    code = "config_invalida"


class LoginError(BotError):
    code = "login_error"


class SesionVencida(BotError):
    """La sesión del portal se venció y hay que volver a entrar."""

    code = "sesion_vencida"


class EmpresaNoEncontrada(BotError):
    code = "empresa_no_encontrada"


class TimeoutDescarga(BotError):
    code = "timeout_descarga"


class ArchivoNoEncontrado(BotError):
    code = "archivo_no_encontrado"


class SinDatos(BotError):
    """No es un fallo: el período no tiene comprobantes."""

    code = "sin_datos"


class Cancelado(BotError):
    code = "cancelado"
