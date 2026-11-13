"""Credenciales del operador de Xubio.

El ABM de clientes vive en `web.py` (contrato del frontend). Acá queda solo lo
que no cambió: la credencial propia del operador de Xubio.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...core.models import CredencialesXubioIn, CredencialesXubioOut
from ...core.storage import CredencialesXubioRepository
from ..deps import get_credenciales_xubio

router = APIRouter(tags=["xubio"])


@router.get("/xubio/credenciales", response_model=CredencialesXubioOut)
def obtener_credenciales(
    repo: CredencialesXubioRepository = Depends(get_credenciales_xubio),
) -> CredencialesXubioOut:
    datos = repo.obtener()
    return CredencialesXubioOut(
        user=datos["user"],
        recordar=datos["recordar"],
        tiene_pass=datos["tiene_pass"],
    )


@router.put("/xubio/credenciales", response_model=CredencialesXubioOut)
def guardar_credenciales(
    body: CredencialesXubioIn,
    repo: CredencialesXubioRepository = Depends(get_credenciales_xubio),
) -> CredencialesXubioOut:
    repo.guardar(body.user, body.pass_, body.recordar)
    datos = repo.obtener()
    return CredencialesXubioOut(
        user=datos["user"],
        recordar=datos["recordar"],
        tiene_pass=datos["tiene_pass"],
    )
