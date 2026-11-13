"""API que consume el frontend Angular.

Mantiene el contrato del módulo web (clientes sin clave, ajustes, ejecuciones con
pasos por cliente y pausa, descarga por archivo) sobre nuestro backend: los bots
y las sesiones son los mismos de `backend/app`, y los datos siguen en
`config.json` / `xubio_credenciales.json`.

Las rutas `/api/jobs` siguen existiendo para uso programático.
"""

from __future__ import annotations

import re
import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ...core import config
from ...core.paths import SUBCARPETA_CONTROL
from ...core.storage import ClientesRepository, CredencialesXubioRepository
from ...core.usuarios import Usuario, usuario_actual, usuario_por_defecto
from ...services import ejecuciones as ejecuciones_srv
from ..deps import get_clientes, get_credenciales_xubio

router = APIRouter(tags=["web"])


# --- Clientes -----------------------------------------------------------------


class DatosArca(BaseModel):
    model_config = ConfigDict(extra="allow")

    cuit_login: str = ""
    clave: str = ""
    cuit_representado: str = ""


class DatosXubio(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: str = ""


class ClienteEntrada(BaseModel):
    model_config = ConfigDict(extra="allow")

    nombre: str = ""
    arca: DatosArca = Field(default_factory=DatosArca)
    xubio: DatosXubio = Field(default_factory=DatosXubio)


class RenombrarCliente(BaseModel):
    nuevo: str = ""


def _solo_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def _faltantes(datos: dict) -> list[str]:
    arca = datos.get("arca") or {}
    xubio = datos.get("xubio") or {}
    falta = []
    if not str(arca.get("cuit_login") or "").strip():
        falta.append("CUIT de ingreso a ARCA")
    if not str(arca.get("clave") or "").strip():
        falta.append("Clave fiscal")
    if not str(arca.get("cuit_representado") or "").strip():
        falta.append("CUIT representado")
    if not str(xubio.get("url") or "").strip():
        falta.append("URL de Xubio")
    return falta


def _publico(nombre: str, datos: dict) -> dict:
    """Lo que ve el front: nunca la clave fiscal."""
    arca = datos.get("arca") or {}
    xubio = datos.get("xubio") or {}
    salida = {
        "nombre": nombre,
        "arca": {
            "cuit_login": str(arca.get("cuit_login") or "").strip(),
            "cuit_representado": str(arca.get("cuit_representado") or "").strip(),
            "tiene_clave": bool(str(arca.get("clave") or "").strip()),
        },
        "xubio": {"url": str(xubio.get("url") or "").strip()},
        "faltantes": _faltantes(datos),
    }
    # Se conservan claves extra (AGIP, ARBA, categoría...) para la app de escritorio.
    for clave, valor in datos.items():
        if clave not in ("arca", "xubio", "nombre"):
            salida[clave] = valor
    return salida


def _combinar(previo: dict, body: ClienteEntrada) -> dict:
    arca_previo = previo.get("arca") or {}
    xubio_previo = previo.get("xubio") or {}

    arca = {
        **arca_previo,
        "cuit_login": _solo_digitos(body.arca.cuit_login)
        or str(arca_previo.get("cuit_login") or ""),
        "cuit_representado": _solo_digitos(body.arca.cuit_representado)
        or str(arca_previo.get("cuit_representado") or ""),
        # Una clave vacía al editar deja la que había.
        "clave": (body.arca.clave or "").strip()
        or str(arca_previo.get("clave") or ""),
    }
    xubio = {**xubio_previo, "url": (body.xubio.url or "").strip()}

    datos = dict(previo)
    datos["arca"] = arca
    datos["xubio"] = xubio
    for clave, valor in (body.model_extra or {}).items():
        datos[clave] = valor
    return datos


def _validar_nombre(nombre: str) -> str:
    nombre = (nombre or "").strip()
    if not nombre:
        raise HTTPException(
            status_code=422, detail={"mensaje": "Poné el nombre del cliente."}
        )
    if re.search(r'[\\/*?:"<>|]', nombre):
        raise HTTPException(
            status_code=422,
            detail={
                "mensaje": 'El nombre no puede tener \\ / * ? : " < > | '
                "(se usa como nombre de carpeta)."
            },
        )
    return nombre


@router.get("/clientes")
def listar_clientes(
    repo: ClientesRepository = Depends(get_clientes),
) -> list[dict]:
    return [
        _publico(nombre, datos if isinstance(datos, dict) else {})
        for nombre, datos in repo.listar().items()
    ]


@router.post("/clientes", status_code=status.HTTP_201_CREATED)
def crear_cliente(
    body: ClienteEntrada,
    repo: ClientesRepository = Depends(get_clientes),
) -> dict:
    nombre = _validar_nombre(body.nombre)
    if repo.obtener(nombre) is not None:
        raise HTTPException(
            status_code=409, detail={"mensaje": f"Ya hay un cliente llamado «{nombre}»."}
        )
    datos = _combinar({}, body)
    repo.guardar(nombre, datos)
    return _publico(nombre, datos)


@router.get("/clientes/{nombre}")
def obtener_cliente(
    nombre: str, repo: ClientesRepository = Depends(get_clientes)
) -> dict:
    datos = repo.obtener(nombre)
    if datos is None:
        raise HTTPException(
            status_code=404, detail={"mensaje": f"No existe el cliente «{nombre}»."}
        )
    return _publico(nombre, datos)


@router.put("/clientes/{nombre}")
def editar_cliente(
    nombre: str,
    body: ClienteEntrada,
    repo: ClientesRepository = Depends(get_clientes),
) -> dict:
    previo = repo.obtener(nombre)
    if previo is None:
        raise HTTPException(
            status_code=404, detail={"mensaje": f"No existe el cliente «{nombre}»."}
        )
    nuevo = _validar_nombre(body.nombre or nombre)
    datos = _combinar(previo, body)
    if nuevo != nombre:
        if repo.obtener(nuevo) is not None:
            raise HTTPException(
                status_code=409,
                detail={"mensaje": f"Ya hay un cliente llamado «{nuevo}»."},
            )
        repo.renombrar(nombre, nuevo)
    repo.guardar(nuevo, datos)
    return _publico(nuevo, datos)


@router.post("/clientes/{nombre}/renombrar")
def renombrar_cliente(
    nombre: str,
    body: RenombrarCliente,
    repo: ClientesRepository = Depends(get_clientes),
) -> dict:
    nuevo = _validar_nombre(body.nuevo)
    try:
        datos = repo.renombrar(nombre, nuevo)
    except Exception as exc:
        raise HTTPException(status_code=404, detail={"mensaje": str(exc)}) from exc
    return _publico(nuevo, datos)


@router.delete("/clientes/{nombre}", status_code=status.HTTP_204_NO_CONTENT)
def borrar_cliente(
    nombre: str, repo: ClientesRepository = Depends(get_clientes)
) -> None:
    if not repo.eliminar(nombre):
        raise HTTPException(
            status_code=404, detail={"mensaje": f"No existe el cliente «{nombre}»."}
        )


# --- Ajustes -------------------------------------------------------------------


@router.get("/ajustes")
def ajustes(
    creds: CredencialesXubioRepository = Depends(get_credenciales_xubio),
) -> dict:
    datos = creds.obtener()
    return {
        "carpeta_salida": str(config.OUTPUT_DIR),
        "subcarpeta": SUBCARPETA_CONTROL,
        "pausa_segundos": config.PAUSA_ENTRE_CLIENTES_S,
        "xubio": {
            "usuario": datos["user"],
            "tiene_clave": bool(datos["pass"]),
            "recordar": datos["recordar"],
            "del_servidor": False,
        },
    }


@router.delete("/ajustes/xubio", status_code=status.HTTP_204_NO_CONTENT)
def olvidar_xubio(
    creds: CredencialesXubioRepository = Depends(get_credenciales_xubio),
) -> None:
    creds.guardar("", "", False)


# --- Ejecuciones ---------------------------------------------------------------


class Pasos(BaseModel):
    arca: bool = True
    xubio: bool = True
    auditoria: bool = True


class CredencialXubio(BaseModel):
    usuario: str = ""
    clave: str = ""
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


def _http_error(exc: ejecuciones_srv.ErrorEjecucion) -> HTTPException:
    return HTTPException(status_code=exc.estado, detail={"mensaje": exc.mensaje})


@router.get("/ejecuciones")
def listar_ejecuciones() -> list[dict]:
    return ejecuciones_srv.listar()


@router.get("/ejecuciones/activa")
def ejecucion_activa() -> dict | None:
    ej = ejecuciones_srv.activa()
    return ej.resumen() if ej else None


@router.post("/ejecuciones", status_code=status.HTTP_201_CREATED)
def iniciar_ejecucion(
    pedido: PedidoEjecucion,
    usuario: Usuario = Depends(usuario_actual),
    clientes: ClientesRepository = Depends(get_clientes),
    creds: CredencialesXubioRepository = Depends(get_credenciales_xubio),
) -> dict:
    try:
        ej = ejecuciones_srv.iniciar(
            usuario.nombre, pedido.model_dump(), clientes, creds
        )
    except ejecuciones_srv.ErrorEjecucion as exc:
        raise _http_error(exc) from exc
    return ej.detalle()


@router.get("/ejecuciones/{ejecucion_id}")
def ver_ejecucion(ejecucion_id: str, log_desde: int = 0) -> dict:
    try:
        return ejecuciones_srv.obtener(ejecucion_id).detalle(log_desde)
    except ejecuciones_srv.ErrorEjecucion as exc:
        raise _http_error(exc) from exc


@router.post("/ejecuciones/{ejecucion_id}/respuesta")
def responder_ejecucion(ejecucion_id: str, respuesta: Respuesta) -> dict:
    try:
        ej = ejecuciones_srv.obtener(ejecucion_id)
        ejecuciones_srv.responder_pausa(ej, respuesta.accion)
    except ejecuciones_srv.ErrorEjecucion as exc:
        raise _http_error(exc) from exc
    return ej.resumen()


@router.post("/ejecuciones/{ejecucion_id}/cancelar")
def cancelar_ejecucion(ejecucion_id: str) -> dict:
    try:
        ej = ejecuciones_srv.obtener(ejecucion_id)
        ejecuciones_srv.cancelar(ej)
    except ejecuciones_srv.ErrorEjecucion as exc:
        raise _http_error(exc) from exc
    return ej.resumen()


@router.get("/ejecuciones/{ejecucion_id}/clientes/{indice}/archivos/{nombre}")
def descargar_archivo_ejecucion(
    ejecucion_id: str, indice: int, nombre: str
) -> FileResponse:
    try:
        ej = ejecuciones_srv.obtener(ejecucion_id)
        archivo = ejecuciones_srv.ruta_archivo(ej, indice, nombre)
    except ejecuciones_srv.ErrorEjecucion as exc:
        raise _http_error(exc) from exc
    return FileResponse(archivo, filename=archivo.name)


# --- Identidad -----------------------------------------------------------------


class EntrarRequest(BaseModel):
    pase: str = ""


@router.get("/yo")
def yo(usuario: Usuario = Depends(usuario_actual)) -> Usuario:
    return usuario


@router.post("/sesion/entrar")
def sesion_entrar(body: EntrarRequest) -> dict:
    """Modo local: acepta el pase y devuelve una sesión de este backend.

    Cuando se conecte ContadorWeb, acá se valida la firma del pase.
    """
    if not (body.pase or "").strip():
        raise HTTPException(
            status_code=422, detail={"mensaje": "Falta el pase de ContadorWeb."}
        )
    usuario = usuario_por_defecto()
    return {
        "token": body.pase,
        "expira": int(time.time()) + 12 * 3600,
        "usuario": usuario.model_dump(),
    }
