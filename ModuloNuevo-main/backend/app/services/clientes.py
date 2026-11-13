"""
Clientes: el mismo config.json que usa la app de escritorio (y DRLiqui).

    {"clientes": {"<nombre>": {"arca": {"cuit_login", "clave", "cuit_representado"},
                               "xubio": {"url"}, ...otras claves de DRLiqui...}}}

Las claves que no se editan acá (AGIP, ARBA, categoría, jurisdicciones) se
conservan al guardar. La clave fiscal nunca sale hacia el front: se informa solo
si está cargada; al editar, una clave vacía deja la que había.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path

from app.core.config import settings

_lock = threading.RLock()


class ErrorClientes(Exception):
    def __init__(self, mensaje: str, estado: int = 400) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.estado = estado


def _ruta() -> Path:
    return Path(settings.CONFIG_CLIENTES)


def leer_config() -> dict:
    with _lock:
        try:
            data = json.loads(_ruta().read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError) as e:
            raise ErrorClientes(f"No se pudo leer {_ruta().name}: {e}", 500) from e
        if not isinstance(data, dict):
            data = {}
        if not isinstance(data.get("clientes"), dict):
            data["clientes"] = {}
        return data


def _guardar_config(data: dict) -> None:
    # Ordenados alfabéticamente sin distinguir mayúsculas, como la app de escritorio.
    data["clientes"] = dict(sorted(data["clientes"].items(), key=lambda kv: kv[0].casefold()))
    ruta = _ruta()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    # Escritura atómica: si algo falla a mitad, el config.json anterior queda intacto.
    fd, tmp = tempfile.mkstemp(dir=ruta.parent, prefix=".config-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp, ruta)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _solo_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def faltantes(datos: dict) -> list[str]:
    """Qué le falta al cliente para poder correr cada paso."""
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


def publico(nombre: str, datos: dict) -> dict:
    """Lo que ve el front de un cliente (sin la clave)."""
    arca = datos.get("arca") or {}
    xubio = datos.get("xubio") or {}
    return {
        "nombre": nombre,
        "arca": {
            "cuit_login": str(arca.get("cuit_login") or "").strip(),
            "cuit_representado": str(arca.get("cuit_representado") or "").strip(),
            "tiene_clave": bool(str(arca.get("clave") or "").strip()),
        },
        "xubio": {"url": str(xubio.get("url") or "").strip()},
        "faltantes": faltantes(datos),
    }


def listar() -> list[dict]:
    clientes = leer_config()["clientes"]
    return [publico(n, d if isinstance(d, dict) else {}) for n, d in sorted(clientes.items(), key=lambda kv: kv[0].casefold())]


def obtener(nombre: str) -> dict:
    clientes = leer_config()["clientes"]
    if nombre not in clientes:
        raise ErrorClientes(f"No existe el cliente «{nombre}».", 404)
    return clientes[nombre]


def _validar(nombre: str, arca: dict, xubio: dict) -> list[str]:
    errores = []
    if not nombre:
        errores.append("Poné el nombre del cliente.")
    elif re.search(r'[\\/*?:"<>|]', nombre):
        errores.append('El nombre no puede tener \\ / * ? : " < > | (se usa como nombre de carpeta).')
    for campo, etiqueta in (("cuit_login", "CUIT de ingreso"), ("cuit_representado", "CUIT representado")):
        valor = _solo_digitos(arca.get(campo, ""))
        if valor and len(valor) != 11:
            errores.append(f"{etiqueta}: tiene que tener 11 dígitos.")
    url = (xubio.get("url") or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        errores.append("La URL de Xubio tiene que empezar con https://")
    return errores


def guardar(nombre_original: str | None, nombre: str, arca: dict, xubio: dict) -> dict:
    """Alta (nombre_original=None) o edición, con cambio de nombre incluido."""
    nombre = (nombre or "").strip()
    errores = _validar(nombre, arca, xubio)
    if errores:
        raise ErrorClientes(" ".join(errores), 422)

    with _lock:
        data = leer_config()
        clientes = data["clientes"]
        existentes = {n.casefold(): n for n in clientes}
        if nombre_original is not None and nombre_original not in clientes:
            raise ErrorClientes(f"No existe el cliente «{nombre_original}».", 404)
        choque = existentes.get(nombre.casefold())
        if choque is not None and choque != nombre_original:
            raise ErrorClientes(f"Ya hay un cliente llamado «{choque}».", 409)

        previo = dict(clientes.get(nombre_original, {})) if nombre_original else {}
        arca_previo = previo.get("arca") or {}
        clave = (arca.get("clave") or "").strip() or str(arca_previo.get("clave") or "")

        nuevo = dict(previo)  # conserva AGIP, ARBA, categoría, jurisdicciones...
        nuevo["arca"] = {
            **arca_previo,
            "cuit_login": _solo_digitos(arca.get("cuit_login", "")),
            "clave": clave,
            "cuit_representado": _solo_digitos(arca.get("cuit_representado", "")),
        }
        nuevo["xubio"] = {**(previo.get("xubio") or {}), "url": (xubio.get("url") or "").strip()}

        if nombre_original and nombre_original != nombre:
            del clientes[nombre_original]
        clientes[nombre] = nuevo
        _guardar_config(data)
        return publico(nombre, nuevo)


def eliminar(nombre: str) -> None:
    with _lock:
        data = leer_config()
        if nombre not in data["clientes"]:
            raise ErrorClientes(f"No existe el cliente «{nombre}».", 404)
        del data["clientes"][nombre]
        _guardar_config(data)


# --- Credencial propia de Xubio ------------------------------------------------


def leer_xubio() -> dict:
    """La credencial recordada (xubio_credenciales.json) o la del .env."""
    try:
        data = json.loads(Path(settings.CREDENCIALES_XUBIO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    usuario = str(data.get("user") or "").strip()
    clave = str(data.get("pass") or "")
    recordar = bool(data.get("recordar"))
    if not (usuario and clave) and settings.XUBIO_USUARIO and settings.XUBIO_CLAVE:
        return {"user": settings.XUBIO_USUARIO, "pass": settings.XUBIO_CLAVE, "recordar": False, "env": True}
    return {"user": usuario, "pass": clave, "recordar": recordar, "env": False}


def xubio_publico() -> dict:
    x = leer_xubio()
    return {"usuario": x["user"], "tiene_clave": bool(x["pass"]), "recordar": x["recordar"], "del_servidor": x["env"]}


def guardar_xubio(usuario: str, clave: str, recordar: bool) -> None:
    """Mismo formato que la app de escritorio. Sin «recordar» se borra lo guardado."""
    datos = {"user": usuario, "pass": clave, "recordar": True} if recordar else {"user": "", "pass": "", "recordar": False}
    ruta = Path(settings.CREDENCIALES_XUBIO)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, indent=4, ensure_ascii=False), encoding="utf-8")
