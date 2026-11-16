"""Pruebas del contrato web (lo que consume el frontend Angular).

No abren navegadores: se reemplaza `GestorSesiones` por dobles que escriben
archivos, así se prueba el recorrido, la pausa y la descarga sin ARCA/Xubio.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from backend.app.core import config
from backend.app.core.models import BotResult
from backend.app.services import ejecuciones as ejecuciones_srv


class _ServicioFalso:
    def is_closed(self) -> bool:
        return False

    def close(self) -> None:
        pass


class _SesionArcaFalsa:
    def abrir_mis_comprobantes(self, cuit_representado: str = "") -> _ServicioFalso:
        return _ServicioFalso()

    def descargar(
        self,
        servicio,
        tipo_comprobante,
        fecha_desde,
        fecha_hasta,
        nombre_cliente,
        directorio_guardado,
        **kwargs,
    ) -> BotResult:
        archivo = Path(directorio_guardado) / f"Mis comprobantes - {tipo_comprobante}.xlsx"
        archivo.write_bytes(b"arca")
        return BotResult(archivos=[str(archivo)])

    def renovar(self) -> None:
        pass

    def cerrar(self) -> None:
        pass


class _SesionXubioFalsa:
    def cambiar_empresa(self, nombre_cliente: str) -> None:
        pass

    def descargar(
        self, nombre_cliente, fecha_desde, fecha_hasta, ventas, compras, carpeta
    ) -> BotResult:
        archivo = Path(carpeta) / "IVA VENTAS.xlsx"
        archivo.write_bytes(b"xubio")
        return BotResult(archivos=[str(archivo)])

    def cerrar(self) -> None:
        pass


class _GestorFalso:
    def __init__(self, log=print) -> None:
        self.log = log
        self.cerrado = False

    def arca(self, cuit_login: str, clave: str) -> _SesionArcaFalsa:
        return _SesionArcaFalsa()

    def xubio(self, usuario: str, password: str) -> _SesionXubioFalsa:
        return _SesionXubioFalsa()

    def descartar_xubio(self) -> None:
        pass

    def cerrar(self) -> None:
        self.cerrado = True


@pytest.fixture
def sin_pausa(monkeypatch):
    monkeypatch.setattr(config, "PAUSA_ENTRE_CLIENTES_S", 0)
    monkeypatch.setattr(ejecuciones_srv, "GestorSesiones", _GestorFalso)
    monkeypatch.setattr(
        ejecuciones_srv, "procesar_cruce", lambda *a, **k: {"ok": True}
    )


def test_ajustes(entorno):
    client, _ = entorno
    datos = client.get("/api/ajustes").json()
    assert isinstance(datos["pausa_segundos"], int)
    assert set(datos["xubio"]) == {"usuario", "tiene_clave", "recordar", "del_servidor"}


def test_ejecucion_completa_y_descarga(cliente_demo, sin_pausa, tmp_path):
    carpeta = tmp_path / "salidas"
    respuesta = cliente_demo.post(
        "/api/ejecuciones",
        json={
            "clientes": ["Acme"],
            "desde": "01/03/2025",
            "hasta": "31/03/2025",
            "ventas": True,
            "compras": True,
            "pasos": {"arca": True, "xubio": True, "auditoria": True},
            "carpeta": str(carpeta),
            "xubio": {"usuario": "operador", "clave": "clave-xubio"},
        },
    )
    assert respuesta.status_code == 201, respuesta.text
    ejecucion_id = respuesta.json()["id"]

    detalle = None
    for _ in range(200):
        detalle = cliente_demo.get(f"/api/ejecuciones/{ejecucion_id}").json()
        if detalle["estado"] in {"finalizada", "cancelada", "error"}:
            break
        time.sleep(0.02)

    assert detalle is not None
    assert detalle["estado"] == "finalizada", detalle["mensaje"]
    assert detalle["clientes"] == 1
    assert detalle["totales"]["ok"] == 1

    pasos = {p["id"]: p["estado"] for p in detalle["detalle_clientes"][0]["pasos"]}
    assert pasos == {
        "arca_ventas": "ok",
        "arca_compras": "ok",
        "xubio": "ok",
        "auditoria_ventas": "ok",
        "auditoria_compras": "ok",
    }

    archivos = [a["nombre"] for a in detalle["detalle_clientes"][0]["archivos"]]
    assert "Mis comprobantes - Emitidos.xlsx" in archivos
    assert "IVA VENTAS.xlsx" in archivos

    descarga = cliente_demo.get(
        f"/api/ejecuciones/{ejecucion_id}/clientes/0/archivos/"
        "Mis comprobantes - Emitidos.xlsx"
    )
    assert descarga.status_code == 200
    assert descarga.content == b"arca"

    # Sin archivo fuera de la carpeta del cliente.
    assert (
        cliente_demo.get(
            f"/api/ejecuciones/{ejecucion_id}/clientes/0/archivos/../../config.json"
        ).status_code
        == 404
    )

    assert cliente_demo.get("/api/ejecuciones/activa").json() is None
    assert len(cliente_demo.get("/api/ejecuciones").json()) == 1


def test_no_permite_dos_ejecuciones(cliente_demo, sin_pausa, monkeypatch):
    # Una ejecución que queda en curso: el segundo pedido tiene que dar 409.
    monkeypatch.setattr(config, "PAUSA_ENTRE_CLIENTES_S", 30)
    pedido = {
        "clientes": ["Acme"],
        "desde": "01/03/2025",
        "hasta": "31/03/2025",
        "ventas": True,
        "compras": False,
        "pasos": {"arca": True, "xubio": False, "auditoria": False},
        "xubio": {"usuario": "", "clave": ""},
    }
    primera = cliente_demo.post("/api/ejecuciones", json=pedido)
    assert primera.status_code == 201, primera.text

    segunda = cliente_demo.post("/api/ejecuciones", json=pedido)
    assert segunda.status_code == 409

    cliente_demo.post(f"/api/ejecuciones/{primera.json()['id']}/cancelar")


def test_yo_usa_encabezados(entorno):
    client, _ = entorno
    respuesta = client.get(
        "/api/yo",
        headers={
            "X-Usuario-Id": "7",
            "X-Usuario-Nombre": "Ana",
            "X-Usuario-Nivel": "10",
        },
    )
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["id"] == "7"
    assert datos["nombre"] == "Ana"
    assert datos["roles"] == ["empleado"]


def test_arca_y_xubio_corren_en_paralelo(cliente_demo, sin_pausa, tmp_path, monkeypatch):
    """ARCA y Xubio no se esperan entre sí: cada uno en su hilo y navegador."""
    import threading

    hilos: dict[str, str] = {}
    adentro = {"arca": threading.Event(), "xubio": threading.Event()}

    class _ArcaLenta(_SesionArcaFalsa):
        def descargar(self, *args, **kwargs):
            hilos["arca"] = threading.current_thread().name
            adentro["arca"].set()
            # Si Xubio no arranca mientras ARCA está adentro, no es paralelo.
            assert adentro["xubio"].wait(2), "Xubio esperó a que termine ARCA"
            return super().descargar(*args, **kwargs)

    class _XubioLenta(_SesionXubioFalsa):
        def descargar(self, *args, **kwargs):
            hilos["xubio"] = threading.current_thread().name
            adentro["xubio"].set()
            assert adentro["arca"].wait(2), "ARCA esperó a que termine Xubio"
            return super().descargar(*args, **kwargs)

    class _Gestor(_GestorFalso):
        def arca(self, cuit_login, clave):
            return _ArcaLenta()

        def xubio(self, usuario, password):
            return _XubioLenta()

    monkeypatch.setattr(ejecuciones_srv, "GestorSesiones", _Gestor)
    respuesta = cliente_demo.post(
        "/api/ejecuciones",
        json={
            "clientes": ["Acme"],
            "desde": "01/03/2025",
            "hasta": "31/03/2025",
            "ventas": True,
            "compras": False,
            "pasos": {"arca": True, "xubio": True, "auditoria": True},
            "carpeta": str(tmp_path / "salidas"),
            "xubio": {"usuario": "operador", "clave": "clave-xubio"},
        },
    )
    assert respuesta.status_code == 201, respuesta.text
    ejecucion_id = respuesta.json()["id"]
    for _ in range(300):
        detalle = cliente_demo.get(f"/api/ejecuciones/{ejecucion_id}").json()
        if detalle["estado"] in {"finalizada", "cancelada", "error"}:
            break
        time.sleep(0.02)

    pasos = {p["id"]: p["estado"] for p in detalle["detalle_clientes"][0]["pasos"]}
    assert pasos["arca_ventas"] == "ok", detalle
    assert pasos["xubio"] == "ok", detalle
    assert hilos["arca"] != hilos["xubio"]
