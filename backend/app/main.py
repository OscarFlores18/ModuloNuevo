"""Punto de entrada del backend.

    uvicorn backend.app.main:app --reload --port 8002
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .api.router import api_router
from .core import config
from .core.errors import BotError
from .core.logging import configure_logging, get_logger
from .core.storage import ClientesRepository, CredencialesXubioRepository
from .services.jobs import JobManager


def _servir_front(app: FastAPI, carpeta: Path) -> None:
    """Sirve el build de Angular en el mismo dominio que la API (sin CORS).

    El front usa `apiUrl: '/api'` en producción, así que alcanza con montar el
    `dist` del frontend. Si no hay build, la API sigue funcionando sola.
    """
    raiz = carpeta.resolve()
    indice = raiz / "index.html"
    if not indice.is_file():
        get_logger().warning(
            "No hay build del frontend en %s: se sirve solo la API.", raiz
        )
        return

    @app.get("/{ruta:path}", include_in_schema=False)
    def front(ruta: str) -> FileResponse:
        if ruta == "api" or ruta.startswith("api/"):
            raise HTTPException(status_code=404)
        archivo = (raiz / ruta).resolve()
        if ruta and archivo.is_file() and archivo.is_relative_to(raiz):
            return FileResponse(archivo)
        return FileResponse(indice, headers={"Cache-Control": "no-cache"})


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
        docs_url=None,     # Desactiva /docs (Swagger)
        redoc_url=None,    # Desactiva /redoc
        openapi_url=None,  # Desactiva /openapi.json
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins(),
        # Cualquier puerto de localhost en desarrollo (ng serve elige uno libre).
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(BotError)
    async def _bot_error(_: Request, exc: BotError) -> JSONResponse:
        return JSONResponse(status_code=400, content=exc.to_dict())

    app.include_router(api_router)
    _servir_front(app, config.FRONT_DIST)
    return app


app = create_app()