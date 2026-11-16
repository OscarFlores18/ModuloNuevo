"""Endurecimiento del proceso del backend.

- Sin volcados de memoria (``ulimit -c 0`` + ``PR_SET_DUMPABLE=0`` en Linux):
  si el proceso o Chromium se caen, no se escribe a disco una imagen de la RAM
  con credenciales en claro. Los hijos (Chromium) heredan el límite.
- En Windows se apaga el diálogo de error que dispara el volcado de WER.
- Se saca ``pw:browser`` de ``DEBUG`` para que el driver de Playwright no
  reenvíe la salida del navegador.
"""

from __future__ import annotations

import ctypes
import os
import sys

from .logging import get_logger

_PR_SET_DUMPABLE = 4


def deshabilitar_core_dumps() -> bool:
    """Devuelve True si se pudo aplicar en este sistema."""
    aplicado = False
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            aplicado = True
        except (ImportError, ValueError, OSError):
            pass
        if sys.platform.startswith("linux"):
            try:
                libc = ctypes.CDLL(None)
                libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0)
            except Exception:
                pass
    elif os.name == "nt":
        try:
            # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX
            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)  # type: ignore[attr-defined]
            aplicado = True
        except Exception:
            pass
    return aplicado


def silenciar_depuracion_playwright() -> None:
    valor = os.environ.get("DEBUG", "")
    if "pw:" in valor:
        restantes = [p for p in valor.split(",") if not p.strip().startswith("pw:")]
        if restantes:
            os.environ["DEBUG"] = ",".join(restantes)
        else:
            os.environ.pop("DEBUG", None)


def endurecer() -> None:
    logger = get_logger()
    if deshabilitar_core_dumps():
        logger.info("Volcados de memoria deshabilitados para este proceso.")
    else:
        logger.warning("No se pudieron deshabilitar los volcados de memoria en este sistema.")
    silenciar_depuracion_playwright()
