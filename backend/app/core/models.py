"""Modelos de la API (Pydantic v2).

El contrato es el mismo que usa la app de escritorio para leer/escribir
`config.json`, así ambas pueden convivir durante la migración.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FECHA_FMT = "%d/%m/%Y"


def _validar_fecha(valor: str) -> str:
    try:
        datetime.strptime(valor, FECHA_FMT)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Fecha inválida '{valor}': se espera DD/MM/AAAA") from exc
    return valor


# --------------------------------------------------------------------------
# Clientes
# --------------------------------------------------------------------------
class ArcaCredenciales(BaseModel):
    model_config = ConfigDict(extra="allow")

    cuit_login: str = ""
    clave: str = ""
    cuit_representado: str = ""


class XubioConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: str = ""


class ClienteIn(BaseModel):
    """Datos editables de un cliente. Se conservan claves extra (agip, arba...)."""

    model_config = ConfigDict(extra="allow")

    arca: ArcaCredenciales = Field(default_factory=ArcaCredenciales)
    xubio: XubioConfig = Field(default_factory=XubioConfig)


class ClienteOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    nombre: str
    arca: ArcaCredenciales = Field(default_factory=ArcaCredenciales)
    xubio: XubioConfig = Field(default_factory=XubioConfig)


class CredencialesXubio(BaseModel):
    user: str = ""
    pass_: str = Field(default="", alias="pass")
    recordar: bool = False

    model_config = ConfigDict(populate_by_name=True)


class CredencialesXubioIn(BaseModel):
    user: str = ""
    pass_: str = Field(default="", alias="pass")
    recordar: bool = True

    model_config = ConfigDict(populate_by_name=True)


class CredencialesXubioOut(BaseModel):
    """Vista segura: nunca devuelve la contraseña guardada."""

    user: str = ""
    recordar: bool = False
    tiene_pass: bool = False


class RenombrarClienteRequest(BaseModel):
    nuevo: str = Field(min_length=1)


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------
class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobRequest(BaseModel):
    clientes: list[str] = Field(min_length=1)
    fecha_desde: str
    fecha_hasta: str
    ventas: bool = True
    compras: bool = False
    pasos: list[Literal["arca", "xubio", "auditoria"]] = Field(
        default_factory=lambda: ["arca", "xubio", "auditoria"]
    )
    carpeta_salida: str | None = None
    xubio_user: str | None = None
    # Nunca se devuelve en las respuestas de la API.
    xubio_pass: str | None = Field(default=None, exclude=True)

    @field_validator("fecha_desde", "fecha_hasta")
    @classmethod
    def _fecha_valida(cls, valor: str) -> str:
        return _validar_fecha(valor)

    @model_validator(mode="after")
    def _coherente(self) -> "JobRequest":
        desde = datetime.strptime(self.fecha_desde, FECHA_FMT)
        hasta = datetime.strptime(self.fecha_hasta, FECHA_FMT)
        if desde > hasta:
            raise ValueError("'fecha_desde' no puede ser posterior a 'fecha_hasta'")
        if not (self.ventas or self.compras):
            raise ValueError("Hay que elegir al menos una operación (ventas o compras)")
        if not self.pasos:
            raise ValueError("Hay que elegir al menos un paso")
        return self


class JobLogEntry(BaseModel):
    ts: datetime
    level: Literal["info", "warning", "error"] = "info"
    message: str


class JobProgress(BaseModel):
    actual: int = 0
    total: int = 0
    cliente: str | None = None
    paso: str | None = None


class JobFile(BaseModel):
    nombre: str
    ruta: str = Field(exclude=True)
    size: int = 0


class JobError(BaseModel):
    code: str
    mensaje: str
    cliente: str | None = None
    paso: str | None = None
    detalle: Any = None


class JobResult(BaseModel):
    archivos: list[str] = Field(default_factory=list)
    errores: list[JobError] = Field(default_factory=list)
    sin_datos: list[str] = Field(default_factory=list)


class Job(BaseModel):
    id: str
    status: JobStatus = JobStatus.QUEUED
    request: JobRequest
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress: JobProgress = Field(default_factory=JobProgress)
    logs: list[JobLogEntry] = Field(default_factory=list)
    files: list[JobFile] = Field(default_factory=list)
    errores: list[JobError] = Field(default_factory=list)
    resultado: JobResult | None = None


class JobResumen(BaseModel):
    """Vista liviana para listados (sin logs)."""

    id: str
    status: JobStatus
    request: JobRequest
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    progress: JobProgress
    total_archivos: int = 0
    total_errores: int = 0


# --------------------------------------------------------------------------
# Contrato de bots
# --------------------------------------------------------------------------
class BotResult(BaseModel):
    """Lo único que un bot puede devolver. Nada de `None` ni de prints."""

    archivos: list[str] = Field(default_factory=list)
    sin_datos: bool = False
    errores: list[dict[str, Any]] = Field(default_factory=list)
    detalle: dict[str, Any] = Field(default_factory=dict)
