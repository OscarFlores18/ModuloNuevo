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
    """Con MODULONUEVO_XUBIO_IMPORTAR=1: entra a la empresa, carga el .zip, importa y devuelve lo que dijo Xubio."""
    monkeypatch.setenv("MODULONUEVO_XUBIO_IMPORTAR", "1")
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


def test_por_defecto_frena_antes_de_importar(cliente_demo, monkeypatch):
    """Sin la variable: carga el archivo y no aprieta «Importar» (la persona lo revisa)."""
    monkeypatch.delenv("MODULONUEVO_XUBIO_IMPORTAR", raising=False)
    monkeypatch.setattr(srv, "VENTANA_ABIERTA_S", 0)

    class Pagina:
        def wait_for_event(self, *a, **k):
            pass

    class SesionFalsa:
        def __init__(self, *a, **k):
            self._page = Pagina()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def cambiar_empresa(self, nombre):
            pass

        def importar_arca(self, *a):
            pytest.fail("no tenía que apretar «Importar»")

        def preparar_importacion_arca(self, operacion, ruta_zip):
            return {"archivo_cargado": True, "boton_importar_habilitado": True}

    monkeypatch.setattr(srv, "SesionXubio", SesionFalsa)

    class Cred:
        usuario, clave = "u", "c"

    monkeypatch.setattr(srv.ServicioCredenciales, "xubio", lambda self: Cred())
    cliente = next(iter(cliente_demo.get("/api/clientes").json()))["nombre"]
    r = cliente_demo.post("/api/convertir-aux/xubio", json={
        "cliente": cliente, "operacion": "Compras",
        "zip": base64.b64encode(_zip(f"{PREFIJO_CSV} Compras - X.csv")).decode(),
    })
    assert r.status_code == 200, r.text
    assert r.json()["frenado"] is True and r.json()["importado"] is None
    assert "apretá «Importar»" in r.json()["mensaje"]


# --- El Excel del Conversor, por «Importar > Desde Excel» -----------------------


class _Clientes:
    def listar_publico(self):
        return {"Acme": {}}


def _bot_falso(monkeypatch, llamadas):
    monkeypatch.setattr(srv, "VENTANA_ABIERTA_S", 0)

    class Pagina:
        def wait_for_event(self, *a, **k):
            pass

    class SesionFalsa:
        def __init__(self, usuario, clave, log=print, visible=None):
            self._page = Pagina()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def cambiar_empresa(self, nombre):
            llamadas.append(("empresa", nombre))

        def _anotar(self, que, operacion, ruta):
            with open(ruta, "rb") as f:
                llamadas.append((que, operacion, ruta.rsplit("\\", 1)[-1].rsplit("/", 1)[-1], f.read()[:4]))

        def importar_excel(self, operacion, ruta):
            self._anotar("importar", operacion, ruta)
            return {"archivo_cargado": True, "boton_importar_habilitado": True, "importado": True, "mensaje": "Importación correcta"}

        def preparar_importacion_excel(self, operacion, ruta):
            self._anotar("cargar", operacion, ruta)
            return {"archivo_cargado": True, "boton_importar_habilitado": True}

    class Cred:
        usuario, clave = "u", "c"

    monkeypatch.setattr(srv, "SesionXubio", SesionFalsa)
    monkeypatch.setattr(srv.ServicioCredenciales, "xubio", lambda self: Cred())
    monkeypatch.setattr(srv.ejecuciones_srv, "activa", lambda: None)


XLS = bytes.fromhex("D0CF11E0") + b"resto"


def test_subir_excel_por_defecto_frena_antes_de_importar(monkeypatch):
    monkeypatch.delenv("MODULONUEVO_XUBIO_IMPORTAR", raising=False)
    llamadas = []
    _bot_falso(monkeypatch, llamadas)
    r = srv.subir_excel("Acme", "Ventas", "Factura A.xls", XLS, _Clientes(), None)
    assert r["frenado"] is True and r["importado"] is None and "apretá «Importar»" in r["mensaje"]
    assert llamadas == [("empresa", "Acme"), ("cargar", "Ventas", "Factura A.xls", XLS[:4])]


def test_subir_excel_importa_con_la_variable(monkeypatch):
    monkeypatch.setenv("MODULONUEVO_XUBIO_IMPORTAR", "1")
    llamadas = []
    _bot_falso(monkeypatch, llamadas)
    r = srv.subir_excel("Acme", "Compras", "Factura A.xls", XLS, _Clientes(), None)
    assert r["importado"] is True and r["mensaje"] == "Importación correcta"
    assert llamadas == [("empresa", "Acme"), ("importar", "Compras", "Factura A.xls", XLS[:4])]


def test_subir_excel_pide_el_formato_2003(monkeypatch):
    _bot_falso(monkeypatch, [])
    with pytest.raises(srv.ErrorImportacion, match="formato 2003"):
        srv.subir_excel("Acme", "Compras", "x.xlsx", b"PK\x03\x04", _Clientes(), None)
    with pytest.raises(srv.ErrorImportacion, match="no está en Clientes"):
        srv.subir_excel("Otra", "Compras", "x.xls", XLS, _Clientes(), None)


def test_lee_la_lista_de_productos_de_xubio():
    from backend.app.bots.xubio import leer_productos

    xml = ('<root><data><![CDATA[312769,COMBUSTIBLE,COMBUSTIBLE,;1871914,Análisis fiscal,0009,;'
           '311679,Luz%2C Gas%2C Telefono e internet 21%25,,;1,combustible,,]]></data></root>')
    assert leer_productos(xml) == [
        {"nombre": "COMBUSTIBLE", "codigo": ""},
        {"nombre": "Análisis fiscal", "codigo": "0009"},
        {"nombre": "Luz, Gas, Telefono e internet 21%", "codigo": ""},
    ]
    assert leer_productos("<root/>") == []
