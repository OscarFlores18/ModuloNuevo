"""Dependencias de FastAPI.

Los repositorios y el gestor de jobs viven en `app.state`, creados en el
lifespan. Así los tests pueden inyectar dobles sin tocar variables globales.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from ..core import acceso_clientes

from ..core.storage import ClientesRepository, CredencialesXubioRepository
from ..services.jobs import JobManager


def get_clientes(request: Request) -> ClientesRepository:
    return request.app.state.clientes


def get_credenciales_xubio(request: Request) -> CredencialesXubioRepository:
    return request.app.state.credenciales_xubio


def get_jobs(request: Request) -> JobManager:
    return request.app.state.jobs


def exigir_acceso_clientes(
    x_clientes_token: str | None = Header(default=None),
) -> None:
    """Lo que toca credenciales (clientes, cuenta de Xubio, API de jobs) pide la
    contraseña de la sección Clientes."""
    if not acceso_clientes.autorizado(x_clientes_token):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "clientes_bloqueado",
                "mensaje": "La sección Clientes está bloqueada: escribí la contraseña.",
            },
        )
