from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.core.errors import ConfigInvalida
from backend.app.core.storage import ClientesRepository, CredencialesXubioRepository


@pytest.fixture
def repo(tmp_path: Path) -> ClientesRepository:
    return ClientesRepository(
        tmp_path / "config.json", tmp_path / "xubio_credenciales.json"
    )


def test_guardar_listar_y_ordenar(repo: ClientesRepository):
    repo.guardar("Zeta", {"arca": {"cuit_login": "1"}})
    repo.guardar("alfa", {"arca": {"cuit_login": "2"}})

    nombres = list(repo.listar())
    assert nombres == ["alfa", "Zeta"]
    assert repo.obtener("alfa")["arca"]["cuit_login"] == "2"


def test_renombrar_y_eliminar(repo: ClientesRepository):
    repo.guardar("Viejo", {"arca": {}})
    repo.renombrar("Viejo", "Nuevo")
    assert repo.obtener("Viejo") is None
    assert repo.obtener("Nuevo") == {"arca": {}}
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
    repo.listar()

    assert json.loads(cred_path.read_text(encoding="utf-8")) == {"user": "u", "pass": "p"}
    assert "xubio_global" not in json.loads(config_path.read_text(encoding="utf-8"))


def test_credenciales_conservan_pass_si_viene_vacia(tmp_path: Path):
    repo = CredencialesXubioRepository(tmp_path / "xubio_credenciales.json")
    repo.guardar("usuario", "clave", recordar=True)
    repo.guardar("usuario", "", recordar=True)
    assert repo.obtener()["pass"] == "clave"
    assert repo.obtener()["user"] == "usuario"


def test_resolver_para_job_prioriza_request(tmp_path: Path):
    repo = CredencialesXubioRepository(tmp_path / "xubio_credenciales.json")
    repo.guardar("guardado", "guardada", recordar=True)
    assert repo.resolver_para_job("otro", "otra") == ("otro", "otra")
    assert repo.resolver_para_job() == ("guardado", "guardada")
