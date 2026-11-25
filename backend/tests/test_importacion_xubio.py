"""Botón «Subir a Xubio»: sin abrir Chrome ni entrar a Xubio."""

import base64
import io
import zipfile

import pytest

from backend.app.auditor.csv_arca import PREFIJO_CSV
from backend.app.services import importacion_xubio as srv


def _zip(*nombres: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for n in nombres:
            z.writestr(n, f"contenido {n}")
    return buffer.getvalue()


def test_toma_solo_el_csv_de_la_operacion():
    datos = _zip(f"{PREFIJO_CSV} Ventas - Acme - 03-25.csv", f"{PREFIJO_CSV} Compras - Acme - 03-25.csv")
    nombre, contenido = srv.csv_de_operacion(datos, "Compras")
    assert nombre == f"{PREFIJO_CSV} Compras - Acme - 03-25.csv"
    with zipfile.ZipFile(io.BytesIO(srv.zip_para_xubio(nombre, contenido))) as z:
        assert z.namelist() == [nombre]


def test_sin_csv_de_la_operacion_avisa():
    with pytest.raises(srv.ErrorImportacion, match="no tiene el CSV de Ventas"):
        srv.csv_de_operacion(_zip(f"{PREFIJO_CSV} Compras - Acme.csv"), "Ventas")


def test_endpoint_rechaza_cliente_desconocido(cliente_demo):
    r = cliente_demo.post("/api/convertir-aux/xubio", json={
        "cliente": "No existe", "operacion": "Compras",
        "zip": base64.b64encode(_zip(f"{PREFIJO_CSV} Compras - X.csv")).decode(),
    })
    assert r.status_code == 404 and "no está en Clientes" in r.json()["detail"]["mensaje"]


def test_endpoint_corre_el_bot_e_importa(cliente_demo, monkeypatch):
    """Con el bot simulado: entra a la empresa, carga el .zip, importa y devuelve lo que dijo Xubio."""
    llamadas = []

    class SesionFalsa:
        def __init__(self, usuario, clave, log=print, visible=None):
            assert visible is True  # «Cargar en Xubio» siempre muestra la ventana
            self._page = None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def cambiar_empresa(self, nombre):
            llamadas.append(("empresa", nombre))

        def importar_arca(self, operacion, ruta_zip):
            with zipfile.ZipFile(ruta_zip) as z:
                llamadas.append(("zip", operacion, z.namelist()))
            return {"archivo_cargado": True, "boton_importar_habilitado": True, "importado": True,
                    "mensaje": "Se importaron 3 comprobantes"}

    monkeypatch.setattr(srv, "SesionXubio", SesionFalsa)

    class Cred:
        usuario, clave = "u", "c"

    monkeypatch.setattr(srv.ServicioCredenciales, "xubio", lambda self: Cred())
    cliente = next(iter(cliente_demo.get("/api/clientes").json()))["nombre"]
    datos = _zip(f"{PREFIJO_CSV} Ventas - X.csv", f"{PREFIJO_CSV} Compras - X.csv")
    r = cliente_demo.post("/api/convertir-aux/xubio", json={
        "cliente": cliente, "operacion": "Compras", "zip": base64.b64encode(datos).decode(),
    })
    assert r.status_code == 200, r.text
    assert r.json()["boton_importar_habilitado"] is True
    assert r.json()["importado"] is True and r.json()["mensaje"] == "Se importaron 3 comprobantes"
    assert llamadas == [("empresa", cliente), ("zip", "Compras", [f"{PREFIJO_CSV} Compras - X.csv"])]
