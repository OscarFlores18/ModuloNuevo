from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.usuarios import Usuario, usuario_actual
from app.services import clientes as clientes_srv
from app.services import ejecuciones as ejecuciones_srv

router = APIRouter()


def _solo_supervisor(usuario: Usuario = Depends(usuario_actual)) -> Usuario:
    if not usuario.tiene("supervisor"):
        raise HTTPException(403, {"codigo": "SOLO_SUPERVISOR", "errores": [],
                                  "mensaje": "Solo un supervisor puede cambiar los datos de los clientes."})
    return usuario


# --- Clientes ------------------------------------------------------------------


class DatosArca(BaseModel):
    cuit_login: str = ""
    clave: str = ""  # vacía al editar = se deja la que había
    cuit_representado: str = ""


class DatosXubio(BaseModel):
    url: str = ""


class ClienteEntrada(BaseModel):
    nombre: str
    arca: DatosArca = Field(default_factory=DatosArca)
    xubio: DatosXubio = Field(default_factory=DatosXubio)


@router.get("/clientes")
def listar_clientes() -> list[dict]:
    return clientes_srv.listar()


@router.post("/clientes", status_code=201, dependencies=[Depends(_solo_supervisor)])
def crear_cliente(datos: ClienteEntrada) -> dict:
    return clientes_srv.guardar(None, datos.nombre, datos.arca.model_dump(), datos.xubio.model_dump())


@router.put("/clientes/{nombre}", dependencies=[Depends(_solo_supervisor)])
def editar_cliente(nombre: str, datos: ClienteEntrada) -> dict:
    return clientes_srv.guardar(nombre, datos.nombre, datos.arca.model_dump(), datos.xubio.model_dump())


@router.delete("/clientes/{nombre}", status_code=204, dependencies=[Depends(_solo_supervisor)])
def borrar_cliente(nombre: str) -> None:
    clientes_srv.eliminar(nombre)


# --- Ajustes -------------------------------------------------------------------


@router.get("/ajustes")
def ajustes() -> dict:
    """Lo que el formulario necesita para arrancar: carpeta por defecto y la cuenta de Xubio recordada."""
    return {
        "carpeta_salida": str(settings.CARPETA_SALIDA),
        "subcarpeta": settings.SUBCARPETA_CONTROL,
        "pausa_segundos": settings.PAUSA_ENTRE_CLIENTES_S,
        "xubio": clientes_srv.xubio_publico(),
    }


@router.delete("/ajustes/xubio", status_code=204)
def olvidar_xubio() -> None:
    clientes_srv.guardar_xubio("", "", False)


# --- Ejecuciones ---------------------------------------------------------------


class Pasos(BaseModel):
    arca: bool = True
    xubio: bool = True
    auditoria: bool = True


class CredencialXubio(BaseModel):
    usuario: str = ""
    clave: str = ""  # vacía = la recordada
    recordar: bool = False


class PedidoEjecucion(BaseModel):
    clientes: list[str]
    desde: str
    hasta: str
    ventas: bool = True
    compras: bool = False
    pasos: Pasos = Field(default_factory=Pasos)
    carpeta: str = ""
    xubio: CredencialXubio = Field(default_factory=CredencialXubio)


class Respuesta(BaseModel):
    accion: str


@router.get("/ejecuciones")
def listar_ejecuciones() -> list[dict]:
    return ejecuciones_srv.listar()


@router.get("/ejecuciones/activa")
def ejecucion_activa() -> dict | None:
    ej = ejecuciones_srv.activa()
    return ej.resumen() if ej else None


@router.post("/ejecuciones", status_code=201)
def iniciar_ejecucion(pedido: PedidoEjecucion, usuario: Usuario = Depends(usuario_actual)) -> dict:
    return ejecuciones_srv.iniciar(usuario.nombre, pedido.model_dump()).detalle()


@router.get("/ejecuciones/{ejecucion_id}")
def ver_ejecucion(ejecucion_id: str, log_desde: int = 0) -> dict:
    return ejecuciones_srv.obtener(ejecucion_id).detalle(log_desde)


@router.post("/ejecuciones/{ejecucion_id}/respuesta")
def responder(ejecucion_id: str, respuesta: Respuesta) -> dict:
    ej = ejecuciones_srv.obtener(ejecucion_id)
    ejecuciones_srv.responder_pausa(ej, respuesta.accion)
    return ej.resumen()


@router.post("/ejecuciones/{ejecucion_id}/cancelar")
def cancelar(ejecucion_id: str) -> dict:
    ej = ejecuciones_srv.obtener(ejecucion_id)
    ejecuciones_srv.cancelar(ej)
    return ej.resumen()


@router.get("/ejecuciones/{ejecucion_id}/clientes/{indice}/archivos/{nombre}")
def descargar(ejecucion_id: str, indice: int, nombre: str) -> FileResponse:
    archivo = ejecuciones_srv.ruta_archivo(ejecuciones_srv.obtener(ejecucion_id), indice, nombre)
    return FileResponse(archivo, filename=archivo.name)
