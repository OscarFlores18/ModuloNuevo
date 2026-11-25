"""Botón «Subir a Xubio» de la pestaña Convertir AUX.

Abre Xubio en la PC del backend, entra a la empresa del cliente y, en
Compras/Ventas > Comprobantes > Acciones > Importar > Desde Mis Comprobantes
(ARCA), carga el .zip con el CSV de esa operación y aprieta «Importar». La
ventana queda abierta con la respuesta de Xubio; se cierra sola cuando la
cierra la persona o a los 15 minutos.
"""

from __future__ import annotations

import io
import logging
import tempfile
import threading
import zipfile
from pathlib import Path

from ..auditor.csv_arca import PREFIJO_CSV
from ..bots.xubio import SesionXubio
from ..core.storage import ClientesRepository, CredencialesXubioRepository
from . import ejecuciones as ejecuciones_srv
from .credenciales import ServicioCredenciales

logger = logging.getLogger(__name__)

ESPERA_RESULTADO_S = 480  # abrir Xubio + importar (hasta 3 min)
VENTANA_ABIERTA_S = 15 * 60
_en_curso = threading.Lock()


class ErrorImportacion(Exception):
    def __init__(self, mensaje: str, status: int = 422):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.status = status


def csv_de_operacion(zip_bytes: bytes, operacion: str) -> tuple[str, bytes]:
    """Del .zip del cliente (Ventas y/o Compras), el CSV de esa operación."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            for nombre in z.namelist():
                if Path(nombre).name.startswith(f"{PREFIJO_CSV} {operacion} "):
                    return Path(nombre).name, z.read(nombre)
    except zipfile.BadZipFile as exc:
        raise ErrorImportacion("El archivo no es un .zip válido.") from exc
    raise ErrorImportacion(f"El .zip no tiene el CSV de {operacion}.")


def zip_para_xubio(nombre_csv: str, contenido: bytes) -> bytes:
    """Como el .zip de ARCA: un solo CSV en la raíz (lo que pide Xubio)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(nombre_csv, contenido)
    return buffer.getvalue()


def preparar(
    cliente: str,
    operacion: str,
    zip_bytes: bytes,
    clientes: ClientesRepository,
    credenciales: CredencialesXubioRepository,
    log=logger.info,
) -> dict:
    """Corre el bot hasta dejar el archivo cargado. Devuelve el resultado o lanza
    ErrorImportacion. La ventana de Chrome queda abierta en otro hilo."""
    if operacion not in ("Ventas", "Compras"):
        raise ErrorImportacion("Operación inválida: Ventas o Compras.")
    if cliente not in clientes.listar_publico():
        raise ErrorImportacion(f"«{cliente}» no está en Clientes.", 404)
    if ejecuciones_srv.activa() is not None:
        raise ErrorImportacion("Hay un control en curso: esperá a que termine para usar Xubio.", 409)
    nombre_csv, contenido = csv_de_operacion(zip_bytes, operacion)
    cred = ServicioCredenciales(clientes, credenciales).xubio()

    if not _en_curso.acquire(blocking=False):
        raise ErrorImportacion("Ya hay una carga en Xubio abierta: cerrá esa ventana de Chrome primero.", 409)

    carpeta = Path(tempfile.mkdtemp(prefix="xubio-importar-"))
    ruta_zip = carpeta / f"{Path(nombre_csv).stem}.zip"
    ruta_zip.write_bytes(zip_para_xubio(nombre_csv, contenido))

    listo = threading.Event()
    salida: dict = {}
    registro: list[str] = []

    def anotar(m: str) -> None:
        registro.append(m)
        log(m)

    def trabajar() -> None:
        try:
            # Siempre a la vista: la persona ve lo que responde Xubio.
            with SesionXubio(cred.usuario, cred.clave, log=anotar, visible=True) as sesion:
                sesion.cambiar_empresa(cliente)
                salida.update(sesion.preparar_importacion_arca(operacion, str(ruta_zip)))
                listo.set()
                # La ventana queda para la persona: se espera a que la cierre.
                try:
                    sesion._page.wait_for_event("close", timeout=VENTANA_ABIERTA_S * 1000)
                except Exception:
                    pass
        except Exception as exc:
            salida["error"] = str(exc) or exc.__class__.__name__
        finally:
            listo.set()
            try:
                ruta_zip.unlink(missing_ok=True)
                carpeta.rmdir()
            except OSError:
                pass
            _en_curso.release()

    threading.Thread(target=trabajar, name="xubio-importar", daemon=True).start()
    if not listo.wait(ESPERA_RESULTADO_S):
        raise ErrorImportacion("Xubio tardó demasiado en abrir la importación.", 504)
    if "error" in salida:
        raise ErrorImportacion(f"No se pudo cargar en Xubio: {salida['error']}", 502)
    return {
        "cliente": cliente,
        "operacion": operacion,
        "archivo": ruta_zip.name,
        "archivo_cargado": bool(salida.get("archivo_cargado")),
        "boton_importar_habilitado": bool(salida.get("boton_importar_habilitado")),
        "importado": bool(salida.get("importado")),
        "mensaje": str(salida.get("mensaje") or ""),
        "log": registro[-8:],
    }
