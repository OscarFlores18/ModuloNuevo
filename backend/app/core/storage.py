"""Persistencia en JSON.

Se mantiene el mismo formato que usaba la app de escritorio para que ambas
puedan convivir durante la migración. La interfaz (`ClientesRepository`,
`CredencialesXubioRepository`) permite cambiar a base de datos más adelante
sin tocar los servicios ni la API.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

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


class ClientesRepository:
    """Clientes + credenciales fiscales (config.json)."""

    def __init__(self, config_path: Path, xubio_cred_path: Path):
        self.store = JsonStore(config_path)
        self.xubio_cred_path = Path(xubio_cred_path)
        self._lock = threading.RLock()

    # -- lectura -----------------------------------------------------------
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
            JsonStore(self.xubio_cred_path).write(data["xubio_global"])
        del data["xubio_global"]
        self.store.write(data)

    def listar(self) -> dict[str, dict[str, Any]]:
        return dict(self._cargar().get("clientes", {}))

    def obtener(self, nombre: str) -> dict[str, Any] | None:
        return self._cargar().get("clientes", {}).get(nombre)

    # -- escritura ---------------------------------------------------------
    def guardar(self, nombre: str, datos: dict[str, Any]) -> dict[str, Any]:
        nombre = nombre.strip()
        if not nombre:
            raise ConfigInvalida("El nombre del cliente no puede estar vacío")
        with self._lock:
            data = self._cargar()
            data["clientes"][nombre] = datos
            data["clientes"] = dict(
                sorted(data["clientes"].items(), key=lambda kv: kv[0].casefold())
            )
            self.store.write(data)
        return datos

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


class CredencialesXubioRepository:
    """Credenciales del operador de Xubio (xubio_credenciales.json)."""

    def __init__(self, path: Path):
        self.store = JsonStore(path)

    def obtener(self) -> dict[str, Any]:
        data = self.store.read()
        return {
            "user": str(data.get("user", "")),
            "pass": str(data.get("pass", "")),
            "recordar": bool(data.get("recordar", False)),
        }

    def guardar(self, user: str, password: str, recordar: bool) -> dict[str, Any]:
        """Guarda las credenciales.

        Si el frontend manda `recordar=True` con la contraseña vacía (porque
        nunca se le devolvió la guardada), se conserva la que ya estaba.
        """
        actual = self.obtener()
        clave = password.strip()
        if recordar and not clave:
            clave = actual["pass"]
        datos = {
            "user": user.strip() if recordar else "",
            "pass": clave if recordar else "",
            "recordar": bool(recordar),
        }
        self.store.write(datos)
        return datos

    def resolver_para_job(
        self, user: str | None = None, password: str | None = None
    ) -> tuple[str, str]:
        """Prioriza lo que manda el request; si no, usa lo guardado."""
        guardadas = self.obtener()
        usuario = (user or "").strip() or guardadas["user"]
        clave = (password or "").strip() or guardadas["pass"]
        if not usuario or not clave:
            raise ConfigInvalida(
                "Faltan las credenciales de Xubio: enviá 'xubio_user' y 'xubio_pass' "
                "en el job o guardalas en el backend."
            )
        return usuario, clave
