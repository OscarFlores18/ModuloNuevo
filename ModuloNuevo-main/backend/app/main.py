import logging
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.api.rutas import router
from app.core.config import settings
from app.core.usuarios import Usuario, canjear_pase, como_yo, conectado, usuario_actual
from app.services.clientes import ErrorClientes
from app.services.ejecuciones import ErrorEjecucion

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Control de Facturas · ARCA vs. Xubio")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Usuario-Id", "X-Usuario-Nombre", "X-Usuario-Nivel", "X-Usuario-Modulos"],
    expose_headers=["Content-Disposition", "Content-Length"],
)

app.include_router(router, prefix="/api", dependencies=[Depends(usuario_actual)])


@app.middleware("http")
async def encabezados_de_seguridad(request: Request, siguiente):
    respuesta = await siguiente(request)
    respuesta.headers.setdefault("X-Content-Type-Options", "nosniff")
    respuesta.headers.setdefault("Referrer-Policy", "no-referrer")
    return respuesta


def _error(mensaje: str, estado: int, codigo: str) -> JSONResponse:
    return JSONResponse({"detail": {"codigo": codigo, "mensaje": mensaje, "errores": []}}, status_code=estado)


@app.exception_handler(ErrorClientes)
def error_clientes(_: Request, e: ErrorClientes) -> JSONResponse:
    return _error(e.mensaje, e.estado, "CLIENTES")


@app.exception_handler(ErrorEjecucion)
def error_ejecucion(_: Request, e: ErrorEjecucion) -> JSONResponse:
    return _error(e.mensaje, e.estado, "EJECUCION")


@app.get("/api/salud")
def salud() -> dict:
    return {"status": "ok"}


@app.get("/api/yo")
def yo(usuario: Usuario = Depends(usuario_actual)) -> dict:
    return {**como_yo(usuario), "conectado": conectado()}


class Pase(BaseModel):
    pase: str


@app.post("/api/sesion/entrar")
def entrar(datos: Pase) -> dict:
    token, vence, usuario = canjear_pase(datos.pase)
    return {"token": token, "expira": vence, "usuario": {**usuario, "conectado": True}}


def _servir_front(carpeta: Path) -> None:
    """El build del front en el mismo dominio que la API (sin CORS)."""
    raiz = carpeta.resolve()
    indice = raiz / "index.html"
    if not indice.is_file():
        logger.warning("FRONT_DIST=%s no tiene index.html: se sirve solo la API.", raiz)
        return

    @app.get("/{ruta:path}", include_in_schema=False)
    def front(ruta: str) -> FileResponse:
        if ruta == "api" or ruta.startswith("api/"):
            raise HTTPException(404)
        archivo = (raiz / ruta).resolve()
        if ruta and archivo.is_file() and archivo.is_relative_to(raiz):
            return FileResponse(archivo, headers={"Cache-Control": "public, max-age=31536000, immutable"})
        return FileResponse(indice, headers={"Cache-Control": "no-cache"})


if settings.FRONT_DIST and Path(settings.FRONT_DIST).is_dir():
    _servir_front(Path(settings.FRONT_DIST))
