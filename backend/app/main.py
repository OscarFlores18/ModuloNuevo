"""Punto de entrada del backend.

    uvicorn backend.app.main:app --reload --port 8000

La documentación interactiva queda en http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.router import api_router
from .core import config
from .core.errors import BotError
from .core.logging import configure_logging, get_logger
from .core.storage import ClientesRepository, CredencialesXubioRepository
from .services.jobs import JobManager


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config.ensure_dirs()
        configure_logging(config.LOG_DIR)
        logger = get_logger()
        logger.info("Iniciando backend. data=%s output=%s", config.DATA_DIR, config.OUTPUT_DIR)

        clientes = ClientesRepository(config.CONFIG_PATH, config.XUBIO_CRED_PATH)
        credenciales_xubio = CredencialesXubioRepository(config.XUBIO_CRED_PATH)
        manager = JobManager(clientes, credenciales_xubio, base_salida=config.OUTPUT_DIR)

        app.state.clientes = clientes
        app.state.credenciales_xubio = credenciales_xubio
        app.state.jobs = manager
        manager.start()

        try:
            yield
        finally:
            logger.info("Deteniendo backend...")
            manager.stop()

    app = FastAPI(
        title="Control de Facturas - Backend",
        description="Migración a web del motor de bots y auditoría.",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(BotError)
    async def _bot_error(_: Request, exc: BotError) -> JSONResponse:
        return JSONResponse(status_code=400, content=exc.to_dict())

    app.include_router(api_router)
    return app


app = create_app()
