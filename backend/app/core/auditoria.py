"""Registro de auditoría inmutable del uso de credenciales.

Cada vez que el backend descifra una credencial para un bot (o la borra con el
botón de pánico) se agrega una línea JSON a un archivo **solo de anexado**.
Las líneas forman una cadena de hashes: cada una incluye el SHA-256 de la
anterior, y si está definida ``MODULONUEVO_AUDIT_HMAC_KEY`` el sello es un
HMAC-SHA256 (no se puede recalcular sin la clave). Borrar, editar o reordenar
una línea rompe la cadena y ``verificar()`` lo detecta.

Se guarda: usuario, fecha y hora UTC (del reloj del sistema, que en el servidor
debe estar sincronizado por NTP), acción, objetivo del bot, cliente y
ejecución. **Nunca** la credencial ni datos derivados de ella.

Para inmutabilidad real (WORM) este archivo debe vivir en un almacenamiento de
solo escritura (S3 Object Lock en modo *compliance*, Azure Immutable Blob, o
``chattr +a`` en Linux). El backend nunca abre el archivo en otro modo que
``"a"``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

GENESIS = "0" * 64

_lock = threading.Lock()


def ruta() -> Path:
    valor = os.getenv("MODULONUEVO_AUDIT_FILE", "").strip()
    return (
        Path(valor).expanduser()
        if valor
        else Path(config.DATA_DIR) / "auditoria" / "credenciales.jsonl"
    )


def _sellar(previo: str, cuerpo: dict[str, Any]) -> str:
    canonico = json.dumps(cuerpo, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    material = (previo + "|" + canonico).encode("utf-8")
    clave = os.getenv("MODULONUEVO_AUDIT_HMAC_KEY", "").encode("utf-8")
    if clave:
        return hmac.new(clave, material, hashlib.sha256).hexdigest()
    return hashlib.sha256(material).hexdigest()


def _ultima(archivo: Path) -> tuple[int, str]:
    """(seq, sello) de la última línea, o (0, GENESIS)."""
    try:
        with archivo.open("rb") as f:
            f.seek(0, os.SEEK_END)
            fin = f.tell()
            bloque = min(fin, 8192)
            f.seek(fin - bloque)
            lineas = f.read().splitlines()
    except FileNotFoundError:
        return 0, GENESIS
    for linea in reversed(lineas):
        if linea.strip():
            datos = json.loads(linea)
            return int(datos["seq"]), str(datos["sello"])
    return 0, GENESIS


def registrar(
    accion: str,
    *,
    usuario: str,
    objetivo: str,
    cliente: str = "",
    ejecucion: str = "",
    resultado: str = "ok",
) -> None:
    """Agrega una entrada. Nunca recibe ni guarda el secreto."""
    archivo = ruta()
    with _lock:
        archivo.parent.mkdir(parents=True, exist_ok=True)
        seq, previo = _ultima(archivo)
        ahora_ns = time.time_ns()
        cuerpo = {
            "seq": seq + 1,
            "ts": datetime.fromtimestamp(ahora_ns / 1e9, timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "ts_ns": ahora_ns,
            "reloj": "sistema-utc-ntp",
            "accion": accion,
            "usuario": usuario or "desconocido",
            "objetivo": objetivo,
            "cliente": cliente,
            "ejecucion": ejecucion,
            "resultado": resultado,
            "previo": previo,
        }
        cuerpo["sello"] = _sellar(previo, cuerpo)
        nuevo = not archivo.exists()
        # Solo anexado: nunca se abre en modo escritura ni se reescribe.
        with archivo.open("a", encoding="utf-8") as f:
            f.write(json.dumps(cuerpo, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        if nuevo:
            try:
                os.chmod(archivo, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass


def verificar(archivo: Path | None = None) -> tuple[bool, int, str]:
    """Recorre la cadena. Devuelve (íntegra, entradas, mensaje)."""
    archivo = archivo or ruta()
    previo, esperado, n = GENESIS, 1, 0
    try:
        lineas = archivo.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return True, 0, "Sin entradas."
    for numero, linea in enumerate(lineas, 1):
        if not linea.strip():
            continue
        try:
            datos = json.loads(linea)
        except json.JSONDecodeError:
            return False, n, f"Línea {numero}: no es JSON válido."
        sello = datos.pop("sello", "")
        if datos.get("seq") != esperado:
            return False, n, f"Línea {numero}: secuencia {datos.get('seq')} (se esperaba {esperado})."
        if datos.get("previo") != previo:
            return False, n, f"Línea {numero}: no encadena con la anterior."
        if not hmac.compare_digest(sello, _sellar(previo, datos)):
            return False, n, f"Línea {numero}: el sello no coincide (entrada alterada)."
        previo, esperado, n = sello, esperado + 1, n + 1
    return True, n, f"{n} entradas íntegras."
