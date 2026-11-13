"""
Los bots de siempre (bot_arca.py, bot_xubio.py, auditor_fo.py) viven en la raíz
del proyecto y los comparte la app de escritorio (main_control.py). El módulo web
los importa tal cual, sin copiarlos: un arreglo en un bot vale para las dos.

Se cargan recién al ejecutar (importan Playwright, pandas y numpy): así la API
arranca rápido y las pantallas andan aunque falte alguna librería de los bots.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from types import ModuleType

from app.core.config import RAIZ_PROYECTO


@dataclass(frozen=True)
class Bots:
    arca: ModuleType
    xubio: ModuleType
    auditor: ModuleType


def cargar() -> Bots:
    raiz = str(RAIZ_PROYECTO)
    if raiz not in sys.path:
        sys.path.insert(0, raiz)
    import auditor_fo
    import bot_arca
    import bot_xubio

    return Bots(arca=bot_arca, xubio=bot_xubio, auditor=auditor_fo)
