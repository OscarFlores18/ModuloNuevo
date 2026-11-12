"""Fixtures comunes de los tests del backend."""

from __future__ import annotations

import logging

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


@pytest.fixture
def cliente_demo(entorno):
    client, _ = entorno
    respuesta = client.put(
        "/api/clientes/Acme",
        json={
            "arca": {
                "cuit_login": "20111111112",
                "clave": "secreto",
                "cuit_representado": "20222222223",
            },
            "xubio": {"url": "/NXV/empresa/acme"},
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    return client
