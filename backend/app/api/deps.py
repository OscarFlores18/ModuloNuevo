"""Dependencias de FastAPI.

Los repositorios y el gestor de jobs viven en `app.state`, creados en el
lifespan. Así los tests pueden inyectar dobles sin tocar variables globales.
"""

from __future__ import annotations

from fastapi import Request

from ..core.storage import ClientesRepository, CredencialesXubioRepository
from ..services.jobs import JobManager


def get_clientes(request: Request) -> ClientesRepository:
    return request.app.state.clientes


def get_credenciales_xubio(request: Request) -> CredencialesXubioRepository:
    return request.app.state.credenciales_xubio


def get_jobs(request: Request) -> JobManager:
    return request.app.state.jobs
