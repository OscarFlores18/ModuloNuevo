"""La API con bots simulados: no abre Chrome ni toca el config.json real."""

import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import motor
from app.core.config import settings
from app.main import app
from app.services import ejecuciones


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"clientes": {
        "Beta SA": {"arca": {"cuit_login": "20111111112", "clave": "secreta", "cuit_representado": "30222222223"},
                    "xubio": {"url": "https://xubio.com/x"}, "agip": {"clave": "otra"}},
        "alfa srl": {"arca": {}, "xubio": {}},
    }}), encoding="utf-8")
    salida = tmp_path / "salida"
    salida.mkdir()
    monkeypatch.setattr(settings, "CONFIG_CLIENTES", config)
    monkeypatch.setattr(settings, "CREDENCIALES_XUBIO", tmp_path / "xubio.json")
    monkeypatch.setattr(settings, "CARPETA_SALIDA", salida)
    monkeypatch.setattr(settings, "PAUSA_ENTRE_CLIENTES_S", 1)
    monkeypatch.setattr(settings, "XUBIO_USUARIO", "")
    monkeypatch.setattr(settings, "XUBIO_CLAVE", "")
    ejecuciones._ejecuciones.clear()
    ejecuciones._orden.clear()

    def arca(cuit, clave, repres, desde, hasta, tipo, cliente, carpeta, **_):
        print(f"   -> ARCA {tipo} {cliente}")
        (carpeta and open(f"{carpeta}/Mis comprobantes - {tipo} - {cliente}.xlsx", "w").close())

    def xubio(usuario, clave, cliente, url, *a):
        print("      [ERROR]: Falló extracción Xubio Ventas: captcha")

    def cruce(cliente, operacion, *a):
        raise Exception("No se encontró el archivo de Xubio esperado")

    falsos = motor.Bots(arca=SimpleNamespace(ejecutar_extraccion=arca), xubio=SimpleNamespace(ejecutar_extraccion=xubio),
                        auditor=SimpleNamespace(procesar_cruce=cruce))
    monkeypatch.setattr(motor, "cargar", lambda: falsos)
    return SimpleNamespace(config=config, salida=salida, cliente=TestClient(app))


def esperar(c, ej_id, hasta=("finalizada", "cancelada", "error"), segundos=10):
    fin = time.time() + segundos
    while time.time() < fin:
        d = c.get(f"/api/ejecuciones/{ej_id}").json()
        if d["estado"] in hasta:
            return d
        time.sleep(0.05)
    raise AssertionError(f"quedó en {d['estado']}")


def test_clientes_no_exponen_la_clave_y_conservan_lo_demas(entorno):
    c = entorno.cliente
    lista = c.get("/api/clientes").json()
    assert [x["nombre"] for x in lista] == ["alfa srl", "Beta SA"]
    assert lista[1]["arca"]["tiene_clave"] is True
    assert "secreta" not in json.dumps(lista)
    assert "Clave fiscal" in lista[0]["faltantes"]

    r = c.put("/api/clientes/Beta SA", json={"nombre": "Beta S.A.", "arca": {"cuit_login": "20-11111111-2", "clave": "", "cuit_representado": "30222222223"}, "xubio": {"url": "https://xubio.com/y"}})
    assert r.status_code == 200, r.text
    guardado = json.loads(entorno.config.read_text(encoding="utf-8"))["clientes"]
    assert "Beta SA" not in guardado
    assert guardado["Beta S.A."]["arca"]["clave"] == "secreta"  # vacía = se deja la que había
    assert guardado["Beta S.A."]["agip"] == {"clave": "otra"}

    assert c.post("/api/clientes", json={"nombre": "ALFA SRL"}).status_code == 409
    assert c.post("/api/clientes", json={"nombre": "Gama", "arca": {"cuit_login": "123"}}).status_code == 422
    assert c.delete("/api/clientes/alfa srl").status_code == 204


def test_empleado_no_edita_clientes(entorno):
    r = entorno.cliente.post("/api/clientes", json={"nombre": "X"},
                             headers={"X-Usuario-Id": "u", "X-Usuario-Nivel": "10", "X-Usuario-Modulos": "Impuestos/Control de facturas"})
    assert r.status_code == 403


def test_ejecucion_completa_con_pausa_y_estados(entorno):
    c = entorno.cliente
    r = c.post("/api/ejecuciones", json={"clientes": ["Beta SA", "alfa srl"], "desde": "01/10/2026", "hasta": "31/10/2026",
                                         "ventas": True, "compras": False, "xubio": {"usuario": "yo@x.com", "clave": "k"}})
    assert r.status_code == 201, r.text
    ej = r.json()
    assert [p["id"] for p in ej["detalle_clientes"][0]["pasos"]] == ["arca_ventas", "xubio", "auditoria_ventas"]

    # Otra mientras corre: no.
    assert c.post("/api/ejecuciones", json={"clientes": ["Beta SA"], "desde": "01/10/2026", "hasta": "31/10/2026"}).status_code == 409

    d = esperar(c, ej["id"], hasta=("esperando",))
    assert d["pausa"]["cliente"] == "Beta SA"
    c.post(f"/api/ejecuciones/{ej['id']}/respuesta", json={"accion": "no_preguntar"})

    d = esperar(c, ej["id"])
    assert d["estado"] == "finalizada"
    beta = d["detalle_clientes"][0]
    assert [p["estado"] for p in beta["pasos"]] == ["ok", "aviso", "error"]
    assert beta["estado"] == "con_errores"
    assert beta["carpeta"].endswith("102026\\Control de Facturas") or beta["carpeta"].endswith("102026/Control de Facturas")
    assert beta["archivos"][0]["tipo"] == "arca"

    archivo = c.get(f"/api/ejecuciones/{ej['id']}/clientes/0/archivos/{beta['archivos'][0]['nombre']}")
    assert archivo.status_code == 200
    assert c.get(f"/api/ejecuciones/{ej['id']}/clientes/0/archivos/..%2F..%2Fconfig.json").status_code == 404

    # El log se pide incremental.
    total = d["log_total"]
    assert c.get(f"/api/ejecuciones/{ej['id']}?log_desde={total}").json()["log"] == []
    # Sin emojis y con tipos.
    assert any(l["tipo"] == "error" for l in d["log"])


def test_cancelar_en_la_pausa(entorno):
    c = entorno.cliente
    ej = c.post("/api/ejecuciones", json={"clientes": ["Beta SA", "alfa srl"], "desde": "01/10/2026", "hasta": "31/10/2026",
                                          "pasos": {"arca": True, "xubio": False, "auditoria": False}}).json()
    esperar(c, ej["id"], hasta=("esperando",))
    c.post(f"/api/ejecuciones/{ej['id']}/cancelar")
    d = esperar(c, ej["id"])
    assert d["estado"] == "cancelada"
    assert d["detalle_clientes"][1]["estado"] == "cancelado"


def test_validaciones(entorno):
    c = entorno.cliente
    base = {"clientes": ["Beta SA"], "desde": "01/10/2026", "hasta": "31/10/2026"}
    assert c.post("/api/ejecuciones", json={**base, "clientes": []}).status_code == 422
    assert c.post("/api/ejecuciones", json={**base, "desde": "2026-10-01"}).status_code == 422
    assert c.post("/api/ejecuciones", json={**base, "hasta": "01/09/2026"}).status_code == 422
    assert c.post("/api/ejecuciones", json={**base, "ventas": False, "compras": False}).status_code == 422
    # Xubio sin credenciales.
    r = c.post("/api/ejecuciones", json=base)
    assert r.status_code == 422 and "Xubio" in r.json()["detail"]["mensaje"]
    assert c.post("/api/ejecuciones", json={**base, "carpeta": "Z:/no/existe/123"}).status_code == 422
