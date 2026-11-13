"""Clientes desde la web: Xubio no pide datos por cliente (la empresa se elige por nombre)."""

from __future__ import annotations

import json

from backend.app.core import config

ARCA = {"cuit_login": "20-23510216-2", "clave": "secreta", "cuit_representado": "30-71080988-3"}


def test_cliente_sin_direccion_de_xubio_queda_completo(entorno):
    client, _ = entorno
    r = client.post("/api/clientes", json={"nombre": "Nuevo SA", "arca": ARCA})

    assert r.status_code == 201, r.text
    datos = r.json()
    assert datos["faltantes"] == []
    assert "xubio" not in datos


def test_editar_conserva_la_direccion_vieja_de_xubio(entorno):
    client, _ = entorno
    config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CONFIG_PATH.write_text(
        json.dumps({"clientes": {"Viejo SA": {"arca": {"cuit_login": "20235102162", "clave": "x", "cuit_representado": "30710809883"},
                                              "xubio": {"url": "https://xubio.com/NXV/viejo"}}}}),
        encoding="utf-8",
    )

    r = client.put("/api/clientes/Viejo SA", json={"nombre": "Viejo SA", "arca": {"cuit_representado": "30-71080988-3"}})

    assert r.status_code == 200, r.text
    guardado = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))["clientes"]["Viejo SA"]
    assert guardado["xubio"] == {"url": "https://xubio.com/NXV/viejo"}
    assert guardado["arca"]["clave"] == "x"
