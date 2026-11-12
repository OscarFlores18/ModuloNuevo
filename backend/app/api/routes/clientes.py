"""ABM de clientes y credenciales del operador de Xubio."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from ...core.errors import ConfigInvalida
from ...core.models import (
    ClienteIn,
    ClienteOut,
    CredencialesXubioIn,
    CredencialesXubioOut,
    RenombrarClienteRequest,
)
from ...core.storage import ClientesRepository, CredencialesXubioRepository
from ..deps import get_clientes, get_credenciales_xubio

router = APIRouter(tags=["clientes"])


def _salida(nombre: str, datos: dict) -> ClienteOut:
    return ClienteOut(nombre=nombre, **datos)


@router.get("/clientes", response_model=list[ClienteOut])
def listar_clientes(
    repo: ClientesRepository = Depends(get_clientes),
) -> list[ClienteOut]:
    return [_salida(nombre, datos) for nombre, datos in repo.listar().items()]


@router.get("/clientes/{nombre}", response_model=ClienteOut)
def obtener_cliente(
    nombre: str, repo: ClientesRepository = Depends(get_clientes)
) -> ClienteOut:
    datos = repo.obtener(nombre)
    if datos is None:
        raise HTTPException(status_code=404, detail=f"No existe el cliente '{nombre}'")
    return _salida(nombre, datos)


@router.put("/clientes/{nombre}", response_model=ClienteOut)
def guardar_cliente(
    nombre: str,
    body: ClienteIn,
    repo: ClientesRepository = Depends(get_clientes),
) -> ClienteOut:
    datos = body.model_dump()
    try:
        repo.guardar(nombre, datos)
    except ConfigInvalida as exc:
        raise HTTPException(status_code=400, detail=exc.mensaje) from exc
    return _salida(nombre.strip(), datos)


@router.post("/clientes/{nombre}/renombrar", response_model=ClienteOut)
def renombrar_cliente(
    nombre: str,
    body: RenombrarClienteRequest,
    repo: ClientesRepository = Depends(get_clientes),
) -> ClienteOut:
    try:
        datos = repo.renombrar(nombre, body.nuevo)
    except ConfigInvalida as exc:
        raise HTTPException(status_code=404, detail=exc.mensaje) from exc
    return _salida(body.nuevo.strip(), datos)


@router.delete("/clientes/{nombre}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_cliente(
    nombre: str, repo: ClientesRepository = Depends(get_clientes)
) -> Response:
    if not repo.eliminar(nombre):
        raise HTTPException(status_code=404, detail=f"No existe el cliente '{nombre}'")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------
# Credenciales del operador de Xubio
# --------------------------------------------------------------------------
@router.get("/xubio/credenciales", response_model=CredencialesXubioOut)
def obtener_credenciales(
    repo: CredencialesXubioRepository = Depends(get_credenciales_xubio),
) -> CredencialesXubioOut:
    datos = repo.obtener()
    return CredencialesXubioOut(
        user=datos["user"],
        recordar=datos["recordar"],
        tiene_pass=bool(datos["pass"]),
    )


@router.put("/xubio/credenciales", response_model=CredencialesXubioOut)
def guardar_credenciales(
    body: CredencialesXubioIn,
    repo: CredencialesXubioRepository = Depends(get_credenciales_xubio),
) -> CredencialesXubioOut:
    datos = repo.guardar(body.user, body.pass_, body.recordar)
    return CredencialesXubioOut(
        user=datos["user"],
        recordar=datos["recordar"],
        tiene_pass=bool(datos["pass"]),
    )
