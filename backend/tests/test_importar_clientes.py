"""Clientes > «Cargar varios»: varios clientes de una vez desde un JSON."""

from __future__ import annotations

import json

import pytest

from backend.app.services.importar_clientes import JsonInvalido, leer

ARCA = {"cuit_login": "20111111112", "clave": "secreto", "cuit_representado": "30710809883"}


def test_formatos_aceptados():
    esperado = [{"nombre": "ACME S.A.", "arca": ARCA, "extras": {}}]
    assert leer(json.dumps({"CLIENTES": {"ACME S.A.": {"ARCA": ARCA}}})) == esperado
    assert leer(json.dumps({"ACME S.A.": {"arca": ARCA}})) == esperado
    assert leer(json.dumps({"ACME S.A.": ARCA})) == esperado
    assert leer(json.dumps([{"nombre": "ACME S.A.", "arca": ARCA}])) == esperado


def test_repara_el_json_escrito_a_mano():
    # Como se escribe a mano: sin «"CLIENTES":», llaves sin cerrar y una coma de más.
    texto = '{CLIENTES{"ACME S.A.": {"ARCA": {"cuit_login": "20111111112","clave": "secreto","cuit_representado": "30710809883",}}'
    [c] = leer(texto)
    assert c["nombre"] == "ACME S.A." and c["arca"] == ARCA


@pytest.mark.parametrize("texto", ["", "hola", "[1, 2]", "{}", '{"A": "x"}'])
def test_json_invalido(texto):
    with pytest.raises(JsonInvalido):
        leer(texto)


def test_endpoint_agrega_y_actualiza(entorno):
    client, _ = entorno
    client.post("/api/clientes", json={"nombre": "Acme", "arca": {"cuit_login": "20111111112", "clave": "vieja", "cuit_representado": ""}})
    texto = json.dumps({"CLIENTES": {
        "ACME": {"ARCA": {"cuit_login": "", "clave": "", "cuit_representado": "30710809883"}},
        "Nuevo S.A.": {"ARCA": ARCA},
        "Mal/Nombre": {"ARCA": ARCA},
    }})
    r = client.post("/api/clientes/importar", json={"texto": texto})
    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["agregados"] == ["Nuevo S.A."]
    assert datos["actualizados"] == ["Acme"]
    assert len(datos["errores"]) == 1 and "Mal/Nombre" in datos["errores"][0]
    clientes = {c["nombre"]: c for c in datos["clientes"]}
    # El existente conserva su nombre, su CUIT de ingreso y su clave; suma el representado.
    assert clientes["Acme"]["arca"] == {"cuit_login": "20111111112", "cuit_representado": "30710809883", "tiene_clave": True}
    assert clientes["Nuevo S.A."]["arca"]["tiene_clave"] is True
    # La clave nunca vuelve en la respuesta.
    assert "secreto" not in r.text


def test_endpoint_json_invalido_y_contrasena(entorno):
    client, _ = entorno
    r = client.post("/api/clientes/importar", json={"texto": "no es json"})
    assert r.status_code == 422 and "JSON" in r.json()["detail"]["mensaje"]
    client.post("/api/clientes/acceso", json={"clave": "estudio2026"})
    assert client.post("/api/clientes/importar", json={"texto": "{}"}).status_code == 401
