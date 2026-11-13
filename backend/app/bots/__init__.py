"""Bots de scraping (ARCA y Xubio)."""

from .arca import SesionArca
from .arca import ejecutar_extraccion as extraer_arca
from .sesiones import GestorSesiones
from .xubio import SesionXubio
from .xubio import ejecutar_extraccion as extraer_xubio

__all__ = [
    "SesionArca",
    "SesionXubio",
    "GestorSesiones",
    "extraer_arca",
    "extraer_xubio",
]
