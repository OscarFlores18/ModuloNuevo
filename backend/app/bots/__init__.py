"""Bots de scraping (ARCA y Xubio)."""

from .arca import ejecutar_extraccion as extraer_arca
from .xubio import ejecutar_extraccion as extraer_xubio

__all__ = ["extraer_arca", "extraer_xubio"]
