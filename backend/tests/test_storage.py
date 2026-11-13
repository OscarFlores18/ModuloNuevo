from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.core import config
from backend.app.core.crypto import PREFIJO, descifrar
from backend.app.core.errors import ConfigInvalida
from backend.app.core.storage import ClientesRepository, CredencialesXubioRepository


@pytest.fixture(autouse=True)
def clave_local(tmp_path: Path, monkeypatch):
    """Aísla la clave Fernet de los tests en una carpeta temporal."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.delenv("MODULONUEVO_MASTER_KEY", raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> ClientesRepository:
    return ClientesRepository(
        tmp_path / "config.json", tmp_path / "xubio_credenciales.json"
    )


def test_guardar_listar_y_ordenar(repo: ClientesRepository):
    repo.guardar("Zeta", {"arca": {"cuit_login": "1"}})
    repo.guardar("alfa", {"arca": {"cuit_login": "2"}})

    nombres = list(repo.listar_cifrado())
    assert nombres == ["alfa", "Zeta"]
    assert repo.obtener_cifrado("alfa")["arca"]["cuit_login"] == "2"


def test_renombrar_y_eliminar(repo: ClientesRepository):
    repo.guardar("Viejo", {"arca": {}})
    repo.renombrar("Viejo", "Nuevo")
    assert repo.obtener_cifrado("Viejo") is None
    assert repo.obtener_cifrado("Nuevo") == {"arca": {}}
    assert repo.eliminar("Nuevo") is True
    assert repo.eliminar("Nuevo") is False


def test_nombre_vacio_es_error(repo: ClientesRepository):
    with pytest.raises(ConfigInvalida):
        repo.guardar("   ", {})


def test_migra_xubio_global(tmp_path: Path):
    config_path = tmp_path / "config.json"
    cred_path = tmp_path / "xubio_credenciales.json"
    config_path.write_text(
        json.dumps({"clientes": {}, "xubio_global": {"user": "u", "pass": "p"}}),
        encoding="utf-8",
    )

    repo = ClientesRepository(config_path, cred_path)
    repo.listar_cifrado()

    credenciales = json.loads(cred_path.read_text(encoding="utf-8"))
    assert credenciales["user"] == "u"
    assert credenciales["pass"].startswith(PREFIJO)
    assert descifrar(credenciales["pass"]) == "p"
    assert "xubio_global" not in json.loads(config_path.read_text(encoding="utf-8"))


def test_credenciales_conservan_pass_si_viene_vacia(tmp_path: Path):
    repo = CredencialesXubioRepository(tmp_path / "xubio_credenciales.json")
    repo.guardar("usuario", "clave", recordar=True)
    guardada = repo.obtener_cifrado()["pass"]
    repo.guardar("usuario", "", recordar=True)
    assert repo.obtener_cifrado()["pass"] == guardada
    assert descifrar(repo.obtener_cifrado()["pass"]) == "clave"
    assert repo.obtener()["user"] == "usuario"


def test_obtener_publico_no_trae_la_clave(repo: ClientesRepository):
    repo.guardar(
        "Acme",
        {"arca": {"cuit_login": "20", "clave": "secreta", "cuit_representado": "21"}},
    )
    publico = repo.obtener_publico("Acme")
    assert "clave" not in publico["arca"]
    assert publico["arca"]["tiene_clave"] is True
    # Internamente sigue disponible (cifrada) para el servicio de descifrado.
    assert descifrar(repo.obtener_cifrado("Acme")["arca"]["clave"]) == "secreta"
