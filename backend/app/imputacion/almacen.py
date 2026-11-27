"""Catálogos de Xubio y reglas por CUIT, en JSON dentro de ``DATA_DIR``.

- ``xubio_catalogos.json``: ``{empresa: {"actualizado": iso, "productos": [...]}}``
- ``xubio_reglas.json``: ``{empresa: {"<cuit>|<alícuota>": {producto_id, ...}}}``

La «empresa» es el nombre del cliente (la misma clave que ``config.json``). La ruta
se resuelve en cada llamada para que los tests (que cambian ``config.DATA_DIR``) y
la app usen cada uno su carpeta.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from ..core import config
from ..core.storage import JsonStore
from .xubio_catalog_parser import ProductoXubio

ARCHIVO_CATALOGOS = "xubio_catalogos.json"
ARCHIVO_REGLAS = "xubio_reglas.json"

_stores: dict[Path, JsonStore] = {}
_lock = threading.Lock()


def _store(nombre: str) -> JsonStore:
    ruta = Path(config.DATA_DIR) / nombre
    with _lock:
        return _stores.setdefault(ruta, JsonStore(ruta))


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def solo_digitos(cuit: str) -> str:
    return re.sub(r"\D", "", cuit or "")


def clave_alicuota(alicuota: float | None) -> str:
    """21.0 → «21», 10.5 → «10.5», None → «sin»."""
    return "sin" if alicuota is None else f"{float(alicuota):g}"


def clave_regla(cuit: str, alicuota: float | None) -> str:
    return f"{solo_digitos(cuit)}|{clave_alicuota(alicuota)}"


# --- Catálogos -----------------------------------------------------------------


def guardar_catalogo(empresa: str, productos: list[ProductoXubio]) -> dict:
    store = _store(ARCHIVO_CATALOGOS)
    with store._lock:
        datos = store.read()
        datos[empresa] = {"actualizado": _ahora(), "productos": [p.model_dump() for p in productos]}
        store.write(datos)
    return datos[empresa]


def catalogo(empresa: str) -> list[ProductoXubio]:
    guardado = _store(ARCHIVO_CATALOGOS).read().get(empresa) or {}
    return [ProductoXubio.model_validate(p) for p in guardado.get("productos", [])]


def catalogo_actualizado(empresa: str) -> str | None:
    return (_store(ARCHIVO_CATALOGOS).read().get(empresa) or {}).get("actualizado")


# --- Reglas por CUIT -------------------------------------------------------------


class Regla(BaseModel):
    cuit_proveedor: str
    alicuota_iva: float | None
    producto_id: int
    proveedor_nombre: str = ""
    actualizado: str = ""
    usos: int = 0


def regla(empresa: str, cuit: str, alicuota: float | None) -> Regla | None:
    guardada = (_store(ARCHIVO_REGLAS).read().get(empresa) or {}).get(clave_regla(cuit, alicuota))
    return Regla.model_validate(guardada) if guardada else None


def reglas(empresa: str) -> list[Regla]:
    guardadas = _store(ARCHIVO_REGLAS).read().get(empresa) or {}
    return [Regla.model_validate(r) for r in guardadas.values()]


def guardar_regla(
    empresa: str, cuit: str, alicuota: float | None, producto_id: int, proveedor_nombre: str = ""
) -> Regla:
    """La corrección manual (o la confirmación) de una persona: desde ahora, todo lo
    de ese proveedor con esa alícuota va a ese producto."""
    cuit = solo_digitos(cuit)
    if not cuit:
        raise ValueError("Falta el CUIT del proveedor.")
    store = _store(ARCHIVO_REGLAS)
    with store._lock:
        datos = store.read()
        de_empresa = datos.setdefault(empresa, {})
        previa = de_empresa.get(clave_regla(cuit, alicuota)) or {}
        nueva = Regla(
            cuit_proveedor=cuit,
            alicuota_iva=alicuota,
            producto_id=int(producto_id),
            proveedor_nombre=proveedor_nombre or previa.get("proveedor_nombre", ""),
            actualizado=_ahora(),
            usos=int(previa.get("usos", 0)) if previa.get("producto_id") == int(producto_id) else 0,
        )
        de_empresa[clave_regla(cuit, alicuota)] = nueva.model_dump()
        store.write(datos)
    return nueva


def contar_uso(empresa: str, cuit: str, alicuota: float | None) -> None:
    store = _store(ARCHIVO_REGLAS)
    with store._lock:
        datos = store.read()
        guardada = (datos.get(empresa) or {}).get(clave_regla(cuit, alicuota))
        if guardada:
            guardada["usos"] = int(guardada.get("usos", 0)) + 1
            store.write(datos)


def borrar_regla(empresa: str, cuit: str, alicuota: float | None) -> bool:
    store = _store(ARCHIVO_REGLAS)
    with store._lock:
        datos = store.read()
        if (datos.get(empresa) or {}).pop(clave_regla(cuit, alicuota), None) is None:
            return False
        store.write(datos)
        return True
