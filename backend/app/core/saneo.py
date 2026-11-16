"""Sanitización de logs: tapa contraseñas, CBU/CVU y tokens con ``***``.

Se aplica en el formatter del logger principal (archivo y consola, incluidos
los tracebacks) y en el registro de las ejecuciones que ve el frontend. Es una
red de seguridad: el código ya no manda secretos al log, pero si alguno se
cuela (un mensaje de error de una librería, un ``repr`` descuidado) se tapa
antes de escribirse.
"""

from __future__ import annotations

import logging
import re

TAPA = "***"

_CLAVES = (
    r"pass(?:word|wd)?|contrase(?:ñ|n)a|clave(?:[_ ]?fiscal)?|secret[o]?|"
    r"token|api[_-]?key|authorization|x-vault-token|dek|kek"
)

_PATRONES: list[tuple[re.Pattern[str], str]] = [
    # clave=valor, "password": "valor", clave fiscal: valor
    (
        re.compile(
            rf"(?i)(\b(?:{_CLAVES})\b[\"']?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}}\]]+)"
        ),
        rf"\1{TAPA}",
    ),
    # Encabezados de autenticación.
    (re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}"), rf"\1 {TAPA}"),
    # JWT.
    (re.compile(r"\beyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}"), TAPA),
    # Tokens de Vault.
    (re.compile(r"\bhv[sbr]\.[\w-]{10,}"), TAPA),
    # Valores cifrados del backend (no son el secreto, pero no hacen falta).
    (re.compile(r"\benc:v\d:[\w.=-]+"), f"enc:{TAPA}"),
    (re.compile(r"\bvault:v\d+:[\w+/=]+"), f"vault:{TAPA}"),
    # CBU / CVU: 22 dígitos (con o sin espacios/guiones entre bloques).
    (re.compile(r"(?<!\d)\d{8}[\s-]?\d{14}(?!\d)"), TAPA),
]


def sanear(texto: object) -> str:
    """Devuelve el texto con los datos sensibles reemplazados por ``***``."""
    salida = str(texto)
    for patron, reemplazo in _PATRONES:
        salida = patron.sub(reemplazo, salida)
    return salida


class FormatterSaneado(logging.Formatter):
    """Formatter que sanea el mensaje ya armado (incluye el traceback)."""

    def format(self, record: logging.LogRecord) -> str:
        return sanear(super().format(record))


class FiltroSaneado(logging.Filter):
    """Filtro para handlers ajenos: sanea ``msg``/``args`` antes de formatear."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = sanear(record.getMessage())
            record.args = None
        except Exception:
            pass
        return True
