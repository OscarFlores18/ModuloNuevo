"""Logging del backend.

Regla: ningún bot escribe en `stdout` ni en la GUI. Cada bot recibe una
función `log(mensaje)` y el job se encarga de guardar la línea con timestamp.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal

from .models import Job, JobLogEntry

LOGGER_NAME = "modulonuevo"

NivelLog = Literal["info", "warning", "error"]


def configure_logging(log_dir: Path, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    formato = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    consola = logging.StreamHandler()
    consola.setFormatter(formato)
    logger.addHandler(consola)

    log_dir.mkdir(parents=True, exist_ok=True)
    archivo = RotatingFileHandler(
        log_dir / "backend.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    archivo.setFormatter(formato)
    logger.addHandler(archivo)

    logger.propagate = False
    return logger


def get_logger(nombre: str = LOGGER_NAME) -> logging.Logger:
    return logging.getLogger(nombre)


class JobLog:
    """Callable que usan los bots para reportar avance.

    Guarda la línea en el job (para `GET /jobs/{id}`) y en el log del backend.
    """

    def __init__(
        self,
        job: Job,
        max_entradas: int = 2000,
        lock: threading.RLock | None = None,
    ):
        self.job = job
        self.max_entradas = max_entradas
        self._lock = lock or threading.RLock()
        self._logger = get_logger(f"{LOGGER_NAME}.job.{job.id}")

    def __call__(self, mensaje: str, level: NivelLog = "info") -> None:
        texto = str(mensaje).strip()
        if not texto:
            return
        entrada = JobLogEntry(
            ts=datetime.now(timezone.utc), level=level, message=texto
        )
        with self._lock:
            self.job.logs.append(entrada)
            if len(self.job.logs) > self.max_entradas:
                del self.job.logs[: len(self.job.logs) - self.max_entradas]
        getattr(self._logger, level, self._logger.info)(texto)
