"""Imputación de compras a productos/servicios de Xubio.

- ``PUT  /api/imputacion/{empresa}/catalogo``: carga el XML del catálogo de Xubio.
- ``GET  /api/imputacion/{empresa}/catalogo``: el catálogo guardado.
- ``POST /api/imputacion/{empresa}/clasificar``: producto sugerido para cada línea.
- ``GET  /api/imputacion/{empresa}/reglas``: la memoria por CUIT.
- ``PUT  /api/imputacion/{empresa}/reglas``: corrección manual (guarda la regla).
- ``DELETE /api/imputacion/{empresa}/reglas``: olvida una regla.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ...core.storage import ClientesRepository
from ...imputacion import almacen
from ...imputacion.clasificador_xubio import Asignacion, LineaCompra, clasificar_lineas, corregir
from ...imputacion.xubio_catalog_parser import CatalogoInvalido, parsear_catalogo
from ..deps import get_clientes

router = APIRouter(prefix="/imputacion", tags=["imputacion"])

MAX_LINEAS = 500


class CatalogoEntrada(BaseModel):
    xml: str = Field(min_length=1, max_length=5_000_000)


class ClasificarEntrada(BaseModel):
    lineas: list[LineaCompra] = Field(max_length=MAX_LINEAS)


class CorreccionEntrada(BaseModel):
    proveedor_cuit: str
    proveedor_nombre: str = ""
    alicuota_iva: float | None = None
    producto_id: int


def _empresa(empresa: str, clientes: ClientesRepository) -> str:
    if clientes.obtener_cifrado(empresa) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe el cliente «{empresa}».")
    return empresa


@router.put("/{empresa}/catalogo")
def cargar_catalogo(empresa: str, body: CatalogoEntrada, clientes: ClientesRepository = Depends(get_clientes)) -> dict:
    empresa = _empresa(empresa, clientes)
    try:
        productos = parsear_catalogo(body.xml)
    except CatalogoInvalido as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    guardado = almacen.guardar_catalogo(empresa, productos)
    return {
        "actualizado": guardado["actualizado"],
        "cantidad": len(productos),
        "genericos": sum(p.es_generico for p in productos),
        "productos": [p.model_dump() for p in productos],
    }


@router.get("/{empresa}/catalogo")
def ver_catalogo(empresa: str, clientes: ClientesRepository = Depends(get_clientes)) -> dict:
    empresa = _empresa(empresa, clientes)
    productos = almacen.catalogo(empresa)
    return {
        "actualizado": almacen.catalogo_actualizado(empresa),
        "cantidad": len(productos),
        "productos": [p.model_dump() for p in productos],
    }


@router.post("/{empresa}/clasificar")
def clasificar(
    empresa: str, body: ClasificarEntrada, clientes: ClientesRepository = Depends(get_clientes)
) -> list[Asignacion]:
    empresa = _empresa(empresa, clientes)
    if not almacen.catalogo(empresa):
        raise HTTPException(status.HTTP_409_CONFLICT, f"«{empresa}» no tiene el catálogo de Xubio cargado.")
    return clasificar_lineas(empresa, body.lineas)


@router.get("/{empresa}/reglas")
def ver_reglas(empresa: str, clientes: ClientesRepository = Depends(get_clientes)) -> list[almacen.Regla]:
    return almacen.reglas(_empresa(empresa, clientes))


@router.put("/{empresa}/reglas")
def corregir_manual(
    empresa: str, body: CorreccionEntrada, clientes: ClientesRepository = Depends(get_clientes)
) -> almacen.Regla:
    empresa = _empresa(empresa, clientes)
    linea = LineaCompra(
        proveedor_cuit=body.proveedor_cuit, proveedor_nombre=body.proveedor_nombre, alicuota_iva=body.alicuota_iva
    )
    try:
        return corregir(empresa, linea, body.producto_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.delete("/{empresa}/reglas", status_code=status.HTTP_204_NO_CONTENT)
def borrar_regla(
    empresa: str,
    proveedor_cuit: str,
    alicuota_iva: float | None = None,
    clientes: ClientesRepository = Depends(get_clientes),
) -> None:
    if not almacen.borrar_regla(_empresa(empresa, clientes), proveedor_cuit, alicuota_iva):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No hay una regla para ese proveedor y alícuota.")
