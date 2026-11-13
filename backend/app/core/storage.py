"""Persistencia en JSON.

Las credenciales (``arca.clave`` en ``config.json`` y ``pass`` en
``xubio_credenciales.json``) se guardan **cifradas** con :mod:`core.crypto`.

Separación de responsabilidades:

- El repositorio almacena y cifra al escribir, pero **nunca descifra**.
- La API solo usa las vistas ``listar_publico`` / ``obtener_publico`` y
  ``CredencialesXubioRepository.obtener`` (indicadores, sin secretos).
- El único que descifra es ``services.credenciales.ServicioCredenciales``.

Se mantiene el mismo formato de JSON que usaba la app de escritorio para que
ambas puedan convivir durante la migración.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .crypto import cifrar, esta_cifrado
from .errors import ConfigInvalida


class JsonStore:
    """Lectura/escritura atómica de un JSON."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def read(self) -> dict[str, Any]:
        with self._lock:
            try:
                contenido = self.path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return {}
            try:
                data = json.loads(contenido)
            except json.JSONDecodeError:
                return {}
            return data if isinstance(data, dict) else {}

    def write(self, data: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporal = self.path.with_suffix(self.path.suffix + ".tmp")
            temporal.write_text(
                json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8"
            )
            temporal.replace(self.path)


def _cifrar_cliente(
    datos: dict[str, Any], existente: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Cifra ``arca.clave`` antes de escribir y conserva la anterior si viene vacía."""
    guardados = dict(datos)
    arca = dict(guardados.get("arca") or {})
    clave = str(arca.get("clave") or "")
    if clave:
        arca["clave"] = cifrar(clave)
    elif existente:
        previa = (existente.get("arca") or {}).get("clave")
        if previa:
            arca["clave"] = str(previa)
    guardados["arca"] = arca
    return guardados


def _vista_publica(datos: dict[str, Any]) -> dict[str, Any]:
    """Copia sin secretos: reemplaza ``clave`` por el indicador ``tiene_clave``."""
    salida = dict(datos)
    arca = dict(salida.get("arca") or {})
    tenia_clave = bool(str(arca.pop("clave", "") or "").strip())
    arca["tiene_clave"] = tenia_clave
    salida["arca"] = arca
    return salida


class ClientesRepository:
    """Clientes + credenciales fiscales (config.json)."""

    def __init__(self, config_path: Path, xubio_cred_path: Path):
        self.store = JsonStore(config_path)
        self.xubio_cred_path = Path(xubio_cred_path)
        self._lock = threading.RLock()

    # -- lectura interna (cifrada, tal cual disco) -------------------------
    def _cargar(self) -> dict[str, Any]:
        with self._lock:
            data = self.store.read()
            data.setdefault("clientes", {})
            self._migrar_xubio_global(data)
            return data

    def _migrar_xubio_global(self, data: dict[str, Any]) -> None:
        """Mueve `xubio_global` a su archivo propio (una sola vez)."""
        if "xubio_global" not in data:
            return
        if not self.xubio_cred_path.exists():
            global_xubio = dict(data["xubio_global"])
            clave = str(global_xubio.get("pass") or "")
            global_xubio["pass"] = cifrar(clave) if clave else ""
            JsonStore(self.xubio_cred_path).write(global_xubio)
        del data["xubio_global"]
        self.store.write(data)

    def listar_cifrado(self) -> dict[str, dict[str, Any]]:
        """Datos completos con los secretos cifrados. Solo uso interno."""
        return dict(self._cargar().get("clientes", {}))

    def obtener_cifrado(self, nombre: str) -> dict[str, Any] | None:
        return self._cargar().get("clientes", {}).get(nombre)

    # -- lectura pública (sin secretos) ------------------------------------
    def listar_publico(self) -> dict[str, dict[str, Any]]:
        return {
            nombre: _vista_publica(datos if isinstance(datos, dict) else {})
            for nombre, datos in self.listar_cifrado().items()
        }

    def obtener_publico(self, nombre: str) -> dict[str, Any] | None:
        datos = self.obtener_cifrado(nombre)
        return _vista_publica(datos) if isinstance(datos, dict) else None

    # -- escritura ---------------------------------------------------------
    def guardar(self, nombre: str, datos: dict[str, Any]) -> dict[str, Any]:
        nombre = nombre.strip()
        if not nombre:
            raise ConfigInvalida("El nombre del cliente no puede estar vacío")
        with self._lock:
            data = self._cargar()
            existente = data["clientes"].get(nombre)
            guardados = _cifrar_cliente(datos, existente)
            data["clientes"][nombre] = guardados
            data["clientes"] = dict(
                sorted(data["clientes"].items(), key=lambda kv: kv[0].casefold())
            )
            self.store.write(data)
        return guardados

    def renombrar(self, viejo: str, nuevo: str) -> dict[str, Any]:
        nuevo = nuevo.strip()
        if not nuevo:
            raise ConfigInvalida("El nombre del cliente no puede estar vacío")
        with self._lock:
            data = self._cargar()
            clientes = data["clientes"]
            if viejo not in clientes:
                raise ConfigInvalida(f"No existe el cliente '{viejo}'")
            datos = clientes.pop(viejo)
            clientes[nuevo] = datos
            data["clientes"] = dict(
                sorted(clientes.items(), key=lambda kv: kv[0].casefold())
            )
            self.store.write(data)
        return datos

    def eliminar(self, nombre: str) -> bool:
        with self._lock:
            data = self._cargar()
            if nombre not in data["clientes"]:
                return False
            del data["clientes"][nombre]
            self.store.write(data)
            return True

    def cifrar_pendientes(self) -> int:
        """Cifra las claves que hayan quedado en texto plano. Devuelve cuántas."""
        with self._lock:
            data = self._cargar()
            cambios = 0
            for datos in data["clientes"].values():
                if not isinstance(datos, dict):
                    continue
                arca = datos.get("arca")
                if not isinstance(arca, dict):
                    continue
                clave = str(arca.get("clave") or "")
                if clave and not esta_cifrado(clave):
                    arca["clave"] = cifrar(clave)
                    cambios += 1
            if cambios:
                self.store.write(data)
            return cambios


class CredencialesXubioRepository:
    """Credenciales del operador de Xubio (xubio_credenciales.json).

    ``obtener`` devuelve la vista pública (sin contraseña). El único que puede
    leer la contraseña cifrada es ``ServicioCredenciales``, vía
    ``obtener_cifrado``.
    """

    def __init__(self, path: Path):
        self.store = JsonStore(path)

    def obtener_cifrado(self) -> dict[str, Any]:
        """Datos tal cual disco (el ``pass`` puede estar cifrado o ser legado)."""
        data = self.store.read()
        return {
            "user": str(data.get("user", "")),
            "pass": str(data.get("pass", "")),
            "recordar": bool(data.get("recordar", False)),
        }

    def obtener(self) -> dict[str, Any]:
        """Vista pública: nunca incluye la contraseña."""
        datos = self.obtener_cifrado()
        return {
            "user": datos["user"],
            "recordar": datos["recordar"],
            "tiene_pass": bool(datos["pass"].strip()),
        }

    def guardar(self, user: str, password: str, recordar: bool) -> dict[str, Any]:
        """Guarda las credenciales cifradas.

        Si el frontend manda `recordar=True` con la contraseña vacía (porque
        nunca se le devolvió la guardada), se conserva la que ya estaba.
        """
        actual = self.obtener_cifrado()
        clave = (password or "").strip()
        if recordar and not clave:
            clave = actual["pass"]
        datos = {
            "user": user.strip() if recordar else "",
            "pass": cifrar(clave) if clave else "",
            "recordar": bool(recordar),
        }
        self.store.write(datos)
        return datos

    def cifrar_pendientes(self) -> int:
        """Cifra la contraseña si quedó en texto plano. Devuelve 1 o 0."""
        datos = self.obtener_cifrado()
        clave = datos["pass"]
        if clave and not esta_cifrado(clave):
            datos["pass"] = cifrar(clave)
            self.store.write(datos)
            return 1
        return 0
