"""Los productos/servicios de Xubio de cada empresa, para el Conversor.

Xubio tiene una lista para Compras y otra para Ventas, distinta en cada empresa.
El bot las trae (sin abrir ningún comprobante: las pide al mismo selector que usa
el formulario) y quedan guardadas en ``data/xubio_productos.json`` para no abrir
Xubio cada vez.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from ..core import config
from ..core.storage import ClientesRepository, CredencialesXubioRepository
from . import ejecuciones as ejecuciones_srv
from .credenciales import ServicioCredenciales
from .importacion_xubio import ErrorImportacion

logger = logging.getLogger(__name__)
_bloqueo = threading.Lock()


def _ruta() -> Path:
    return Path(config.DATA_DIR) / "xubio_productos.json"


def _todo() -> dict:
    try:
        return json.loads(_ruta().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardados(cliente: str) -> dict:
    """Lo último traído de Xubio para el cliente: {Compras, Ventas, actualizado} o vacío."""
    return _todo().get(cliente) or {"Compras": [], "Ventas": [], "actualizado": None}


def traer(
    cliente: str,
    clientes: ClientesRepository,
    credenciales: CredencialesXubioRepository,
    log=logger.info,
) -> dict:
    """Abre Xubio (oculto), entra a la empresa del cliente y trae las dos listas."""
    from ..bots.xubio import SesionXubio

    if cliente not in clientes.listar_publico():
        raise ErrorImportacion(f"«{cliente}» no está en Clientes.", 404)
    if ejecuciones_srv.activa() is not None:
        raise ErrorImportacion("Hay un control en curso: esperá a que termine para usar Xubio.", 409)
    cred = ServicioCredenciales(clientes, credenciales).xubio()
    if not _bloqueo.acquire(blocking=False):
        raise ErrorImportacion("Ya se están trayendo los productos de Xubio: esperá un momento.", 409)
    try:
        with SesionXubio(cred.usuario, cred.clave, log=log, visible=False) as sesion:
            sesion.cambiar_empresa(cliente)
            datos = {op: sesion.productos(op) for op in ("Compras", "Ventas")}
    except ErrorImportacion:
        raise
    except Exception as exc:
        raise ErrorImportacion(f"No se pudieron traer los productos de Xubio: {str(exc)[:300] or type(exc).__name__}", 502) from exc
    finally:
        _bloqueo.release()
    datos["actualizado"] = datetime.now().isoformat(timespec="seconds")
    todo = _todo()
    todo[cliente] = datos
    _ruta().parent.mkdir(parents=True, exist_ok=True)
    _ruta().write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding="utf-8")
    return datos
