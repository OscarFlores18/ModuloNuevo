"""Botón «Subir a Xubio» de la pestaña Subir a Xubio.

Abre Xubio en la PC del backend, entra a la empresa del cliente y, en
Compras/Ventas > Comprobantes > Acciones > Importar > Desde Mis Comprobantes
(ARCA), carga el .zip con el CSV de esa operación, **aprieta «Importar»** y lee
lo que contesta Xubio. La confirmación la da la persona antes, en la app.

- Si Xubio dice que importó, Chrome se cierra solo.
- Si da error o no se reconoce la respuesta, la ventana queda abierta para que
  la persona mire (se cierra cuando la cierra ella o a los 15 minutos).
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

# Login, empresa, carga del archivo y la importación (hasta 2 minutos).
ESPERA_RESULTADO_S = 360
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
    """Corre el bot hasta importar en Xubio. Devuelve el resultado o lanza
    ErrorImportacion. Si no quedó claro que importó, la ventana de Chrome queda
    abierta en otro hilo para que la persona revise."""
    _validar(cliente, operacion, clientes)
    nombre_csv, contenido = csv_de_operacion(zip_bytes, operacion)
    return _correr(cliente, operacion, f"{Path(nombre_csv).stem}.zip", zip_para_xubio(nombre_csv, contenido),
                   "importar_arca", clientes, credenciales, log)


def subir_excel(
    cliente: str,
    operacion: str,
    nombre: str,
    xls: bytes,
    clientes: ClientesRepository,
    credenciales: CredencialesXubioRepository,
    log=logger.info,
) -> dict:
    """El .xls del Conversor (modelo de Xubio) en Compras o Ventas > Importar >
    Desde Excel, en la empresa del cliente. Aprieta «Importar» (la persona ya
    confirmó en la app)."""
    _validar(cliente, operacion, clientes)
    if not xls.startswith(bytes.fromhex("D0CF11E0")):
        raise ErrorImportacion("Xubio pide el Excel en formato 2003 (.xls): generalo de nuevo desde el Conversor.")
    nombre = Path(nombre).name or f"{operacion}.xls"
    if not nombre.lower().endswith(".xls"):
        nombre = f"{Path(nombre).stem}.xls"
    return _correr(cliente, operacion, nombre, xls, "importar_excel", clientes, credenciales, log)


def _validar(cliente: str, operacion: str, clientes: ClientesRepository) -> None:
    if operacion not in ("Ventas", "Compras"):
        raise ErrorImportacion("Operación inválida: Ventas o Compras.")
    if cliente not in clientes.listar_publico():
        raise ErrorImportacion(f"«{cliente}» no está en Clientes.", 404)
    if ejecuciones_srv.activa() is not None:
        raise ErrorImportacion("Hay un control en curso: esperá a que termine para usar Xubio.", 409)


def _correr(
    cliente: str,
    operacion: str,
    nombre_archivo: str,
    contenido: bytes,
    accion: str,
    clientes: ClientesRepository,
    credenciales: CredencialesXubioRepository,
    log,
) -> dict:
    cred = ServicioCredenciales(clientes, credenciales).xubio()

    if not _en_curso.acquire(blocking=False):
        raise ErrorImportacion("Ya hay una carga en Xubio abierta: cerrá esa ventana de Chrome primero.", 409)

    carpeta = Path(tempfile.mkdtemp(prefix="xubio-importar-"))
    ruta_zip = carpeta / nombre_archivo
    ruta_zip.write_bytes(contenido)

    listo = threading.Event()
    salida: dict = {}
    registro: list[str] = []

    def anotar(m: str) -> None:
        registro.append(m)
        log(m)

    def trabajar() -> None:
        try:
            # A la vista: la persona ve lo que hace el bot.
            with SesionXubio(cred.usuario, cred.clave, log=anotar, visible=True) as sesion:
                sesion.cambiar_empresa(cliente)
                salida.update(getattr(sesion, accion)(operacion, str(ruta_zip)))
                listo.set()
                if salida.get("importado") is not True:
                    # Error o respuesta dudosa: la ventana queda para que la persona mire.
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
        raise ErrorImportacion("Xubio tardó demasiado en importar: revisá en Xubio si se importó antes de volver a subirlo.", 504)
    if "error" in salida:
        raise ErrorImportacion(f"No se pudo cargar en Xubio: {salida['error']}", 502)
    return {
        "cliente": cliente,
        "operacion": operacion,
        "archivo": ruta_zip.name,
        "archivo_cargado": bool(salida.get("archivo_cargado")),
        "boton_importar_habilitado": bool(salida.get("boton_importar_habilitado")),
        # True: Xubio dijo que importó · False: no importó · None: no se sabe (mirar en Xubio).
        "importado": salida.get("importado"),
        "mensaje": salida.get("mensaje") or "",
        "log": registro[-8:],
    }
