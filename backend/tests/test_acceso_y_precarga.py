"""Contraseña de la sección Clientes y precarga de los clientes del estudio."""

from __future__ import annotations

import json

import pytest

from backend.app.core import acceso_clientes
from backend.app.services.precarga import CLIENTES_PRECARGADOS, CUIT_INGRESO

CLAVE_FISCAL = "Clave-Fiscal-Compartida-9"


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    monkeypatch.delenv("MODULONUEVO_CLIENTES_CLAVE", raising=False)
    acceso_clientes._tokens.clear()
    acceso_clientes._fallos.update(cantidad=0, hasta=0.0, bloqueos=0)


def _alta(client, headers=None):
    return client.post(
        "/api/clientes",
        json={"nombre": "Acme", "arca": {"cuit_login": "20", "clave": "x", "cuit_representado": "21"}},
        headers=headers or {},
    )


def test_sin_contrasena_la_seccion_esta_abierta(entorno):
    client, _ = entorno
    assert client.get("/api/clientes/acceso").json() == {
        "configurada": False,
        "desbloqueado": True,
    }
    assert _alta(client).status_code == 201


def test_la_primera_contrasena_queda_guardada_como_hash(entorno, tmp_path):
    client, _ = entorno
    r = client.post("/api/clientes/acceso", json={"clave": "estudio2026"})
    assert r.status_code == 200
    assert r.json()["creada"] is True

    texto = (tmp_path / "data" / "acceso_clientes.json").read_text(encoding="utf-8")
    assert "estudio2026" not in texto
    assert set(json.loads(texto)) == {"hash", "sal"}


def test_con_contrasena_editar_pide_el_token(entorno):
    client, _ = entorno
    token = client.post("/api/clientes/acceso", json={"clave": "estudio2026"}).json()["token"]

    # Sin token: bloqueado. La lista sigue abierta para «Nuevo control».
    assert _alta(client).status_code == 401
    assert client.get("/api/clientes").status_code == 200
    assert client.get("/api/clientes/acceso").json()["desbloqueado"] is False

    assert _alta(client, {"X-Clientes-Token": token}).status_code == 201
    assert client.get(
        "/api/clientes/acceso", headers={"X-Clientes-Token": token}
    ).json() == {"configurada": True, "desbloqueado": True}

    client.delete("/api/clientes/acceso", headers={"X-Clientes-Token": token})
    assert client.delete("/api/clientes/Acme", headers={"X-Clientes-Token": token}).status_code == 401


def test_contrasena_incorrecta_y_bloqueo_por_intentos(entorno):
    client, _ = entorno
    client.post("/api/clientes/acceso", json={"clave": "estudio2026"})
    for _ in range(acceso_clientes.MAX_FALLOS):
        assert client.post("/api/clientes/acceso", json={"clave": "mal"}).status_code == 401
    # Bloqueado un rato aunque ahora la clave sea la correcta.
    assert client.post("/api/clientes/acceso", json={"clave": "estudio2026"}).status_code == 429


def test_cada_bloqueo_seguido_duplica_la_espera(entorno, monkeypatch):
    client, _ = entorno
    client.post("/api/clientes/acceso", json={"clave": "estudio2026"})
    ahora = [1000.0]
    monkeypatch.setattr(acceso_clientes.time, "time", lambda: ahora[0])
    esperas = []
    for _ in range(3):
        for _ in range(acceso_clientes.MAX_FALLOS):
            client.post("/api/clientes/acceso", json={"clave": "mal"})
        esperas.append(acceso_clientes._fallos["hasta"] - ahora[0])
        ahora[0] = acceso_clientes._fallos["hasta"] + 1
    assert esperas == [30, 60, 120]
    # Entrar bien reinicia la espera.
    assert client.post("/api/clientes/acceso", json={"clave": "estudio2026"}).status_code == 200
    assert acceso_clientes._fallos["bloqueos"] == 0


def test_contrasena_corta_no_se_acepta(entorno):
    client, _ = entorno
    assert client.post("/api/clientes/acceso", json={"clave": "ab"}).status_code == 422


def test_contrasena_por_variable_de_entorno(entorno, monkeypatch):
    client, _ = entorno
    monkeypatch.setenv("MODULONUEVO_CLIENTES_CLAVE", "del-servidor")
    assert client.get("/api/clientes/acceso").json()["configurada"] is True
    assert client.post("/api/clientes/acceso", json={"clave": "otra"}).status_code == 401
    assert client.post("/api/clientes/acceso", json={"clave": "del-servidor"}).status_code == 200


def test_precarga_agrega_los_clientes_con_la_clave_compartida(entorno, tmp_path):
    client, _ = entorno
    # Un cliente existente con el mismo CUIT de ingreso y la clave cargada.
    client.post(
        "/api/clientes",
        json={
            "nombre": "PICCININI y ASOCIADOS S.A.",
            "arca": {"cuit_login": CUIT_INGRESO, "clave": CLAVE_FISCAL, "cuit_representado": "30710809883"},
        },
    )

    r = client.post("/api/clientes/precargar")
    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["ya_estaban"] == ["PICCININI y ASOCIADOS S.A."]
    assert len(datos["agregados"]) == len(CLIENTES_PRECARGADOS) - 1
    assert datos["con_clave"] is True

    por_nombre = {c["nombre"]: c for c in datos["clientes"]}
    fiscales = por_nombre["FISCALES SAN MARTIN S.A."]
    assert fiscales["arca"] == {
        "cuit_login": CUIT_INGRESO,
        "cuit_representado": "30717538826",
        "tiene_clave": True,
    }
    assert fiscales["faltantes"] == []

    # En disco: la clave cifrada, y cada cliente con su propia DEK (no una copia).
    guardado = json.loads((tmp_path / "data" / "config.json").read_text(encoding="utf-8"))
    claves = [c["arca"]["clave"] for c in guardado["clientes"].values()]
    assert CLAVE_FISCAL not in json.dumps(guardado)
    assert len(set(claves)) == len(claves)

    # Precargar de nuevo no duplica ni pisa nada.
    otra = client.post("/api/clientes/precargar").json()
    assert otra["agregados"] == []


def test_precarga_sin_clave_compartida_deja_la_clave_pendiente(entorno):
    client, _ = entorno
    datos = client.post("/api/clientes/precargar").json()
    assert datos["con_clave"] is False
    assert all(not c["arca"]["tiene_clave"] for c in datos["clientes"])


def test_precarga_tambien_pide_la_contrasena(entorno):
    client, _ = entorno
    client.post("/api/clientes/acceso", json={"clave": "estudio2026"})
    assert client.post("/api/clientes/precargar").status_code == 401


def test_precargar_de_nuevo_completa_la_clave_que_faltaba(entorno):
    client, _ = entorno
    client.post("/api/clientes/precargar")  # sin clave compartida todavía
    client.put(
        "/api/clientes/TRUCKNOR S.A.",
        json={"nombre": "TRUCKNOR S.A.", "arca": {"clave": CLAVE_FISCAL}},
    )
    datos = client.post("/api/clientes/precargar").json()
    assert len(datos["completados"]) == len(CLIENTES_PRECARGADOS) - 1
    assert all(c["arca"]["tiene_clave"] for c in datos["clientes"])


def test_cuenta_de_xubio_y_api_de_jobs_piden_la_contrasena(entorno):
    """Con contraseña configurada, nadie sin token cambia la cuenta de Xubio ni
    corre bots por la API de jobs."""
    client, _ = entorno
    token = client.post("/api/clientes/acceso", json={"clave": "estudio2026"}).json()["token"]
    sin = {}
    con = {"X-Clientes-Token": token}

    assert client.put("/api/xubio/credenciales", json={"user": "x", "pass": "y"}, headers=sin).status_code == 401
    assert client.delete("/api/ajustes/xubio", headers=sin).status_code == 401
    assert client.get("/api/jobs", headers=sin).status_code == 401

    assert client.put("/api/xubio/credenciales", json={"user": "x", "pass": "y"}, headers=con).status_code == 200
    assert client.delete("/api/ajustes/xubio", headers=con).status_code == 204
    assert client.get("/api/jobs", headers=con).status_code == 200
    # Leer sigue abierto y nunca devuelve la contraseña.
    assert "pass" not in client.get("/api/xubio/credenciales").json()
