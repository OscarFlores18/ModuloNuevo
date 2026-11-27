"""Fixtures comunes de los tests del backend."""

from __future__ import annotations

import logging
import threading

import pytest
from fastapi.testclient import TestClient

from backend.app.core import config
from backend.app.main import create_app


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Backend con datos, salidas y logs dentro de `tmp_path`."""
    data = tmp_path / "data"
    salidas = tmp_path / "salidas"
    logs = tmp_path / "logs"

    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "OUTPUT_DIR", salidas)
    monkeypatch.setattr(config, "LOG_DIR", logs)
    monkeypatch.setattr(config, "CONFIG_PATH", data / "config.json")
    monkeypatch.setattr(config, "XUBIO_CRED_PATH", data / "xubio_credenciales.json")

    # configure_logging() solo agrega handlers una vez: se limpian para que
    # cada test escriba en su propio directorio temporal.
    logger = logging.getLogger("modulonuevo")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    app = create_app()
    with TestClient(app) as client:
        yield client, app

    # Los controles corren en su propio hilo y se guardan al terminar. Si el test
    # termina antes, monkeypatch devuelve DATA_DIR a la carpeta real y el control
    # de prueba («Acme») aparecía en el historial de verdad: se espera a que terminen.
    for hilo in threading.enumerate():
        if hilo.name.startswith("control-"):
            hilo.join(timeout=30)


@pytest.fixture
def cliente_demo(entorno):
    client, _ = entorno
    respuesta = client.post(
        "/api/clientes",
        json={
            "nombre": "Acme",
            "arca": {
                "cuit_login": "20111111112",
                "clave": "secreto",
                "cuit_representado": "20222222223",
            },
            "xubio": {"url": "/NXV/empresa/acme"},
        },
    )
    assert respuesta.status_code == 201, respuesta.text
    return client


@pytest.fixture(autouse=True)
def _gemini_con_tres_modelos(monkeypatch):
    """Los tests de Gemini cuentan con tres modelos (el principal y dos de respaldo) y
    sin memoria de cupos agotados entre un test y otro."""
    from backend.app.extraccion import gemini

    monkeypatch.setattr(gemini, "RESPALDOS", ("gemini-3.5-flash", "gemini-3.1-flash-lite"))
    monkeypatch.setattr(gemini, "RAPIDO", "gemini-3.1-flash-lite")
    monkeypatch.setattr(gemini, "_sin_cupo_hasta", {})


@pytest.fixture(autouse=True)
def _gemini_sin_memoria_de_opciones(monkeypatch):
    from backend.app.extraccion import gemini

    monkeypatch.setattr(gemini, "_sin_presupuesto_cero", set())
