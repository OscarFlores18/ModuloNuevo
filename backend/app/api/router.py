"""Router raíz de la API."""

from __future__ import annotations

from fastapi import APIRouter

from .routes import clientes, health, jobs, web

api_router = APIRouter()
api_router.include_router(health.router)
# Contrato del frontend Angular (clientes, ajustes, ejecuciones, identidad).
api_router.include_router(web.router, prefix="/api")
api_router.include_router(clientes.router, prefix="/api")
api_router.include_router(jobs.router, prefix="/api")
