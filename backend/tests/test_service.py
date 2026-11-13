from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.core.errors import Cancelado, LoginError
from backend.app.core.models import BotResult, JobRequest, JobResult
from backend.app.services import control_facturas


def _request(**cambios) -> JobRequest:
    datos = {
        "clientes": ["Acme"],
        "fecha_desde": "01/03/2025",
        "fecha_hasta": "31/03/2025",
        "ventas": True,
        "compras": True,
        "pasos": ["arca", "xubio", "auditoria"],
    }
    datos.update(cambios)
    return JobRequest(**datos)


CLIENTES = {
    "Acme": {
        "arca": {"cuit_login": "20", "clave": "x", "cuit_representado": "21"},
        "xubio": {"url": "/NXV/acme"},
    }
}


def test_pipeline_completo(tmp_path: Path, monkeypatch):
    llamadas: list[str] = []

    def fake_arca(*args, **kwargs):
        llamadas.append(f"arca:{args[5]}")
        ruta = Path(args[7]) / f"arca-{args[5]}.xlsx"
        ruta.write_bytes(b"x")
        return BotResult(archivos=[str(ruta)])

    def fake_xubio(*args, **kwargs):
        llamadas.append("xubio")
        ruta = Path(args[8]) / "xubio.xlsx"
        ruta.write_bytes(b"x")
        return BotResult(archivos=[str(ruta)])

    def fake_cruce(*args, **kwargs):
        llamadas.append(f"cruce:{args[1]}")
        ruta = Path(args[4]) / f"cruce-{args[1]}.xlsx"
        ruta.write_bytes(b"x")
        return str(ruta)

    monkeypatch.setattr(control_facturas, "extraer_arca", fake_arca)
    monkeypatch.setattr(control_facturas, "extraer_xubio", fake_xubio)
    monkeypatch.setattr(control_facturas, "procesar_cruce", fake_cruce)

    progreso = []
    resultado = control_facturas.ejecutar_control_facturas(
        request=_request(),
        clientes=CLIENTES,
        xubio_user="u",
        xubio_pass="p",
        base_salida=tmp_path,
        log=lambda mensaje: None,
        on_progreso=progreso.append,
    )

    assert isinstance(resultado, JobResult)
    assert resultado.errores == []
    assert llamadas == [
        "arca:Emitidos",
        "arca:Recibidos",
        "xubio",
        "cruce:Ventas",
        "cruce:Compras",
    ]
    assert len(resultado.archivos) == 5
    assert progreso[-1].actual == 1
    assert progreso[-1].total == 1


def test_error_de_un_paso_no_corta_el_resto(tmp_path: Path, monkeypatch):
    def fake_arca(*args, **kwargs):
        if args[5] == "Recibidos":
            raise LoginError("credenciales ARCA inválidas")
        return BotResult(archivos=[])

    def fake_xubio(*args, **kwargs):
        return BotResult(archivos=[])

    def fake_cruce(*args, **kwargs):
        raise AssertionError("no debería auditar sin archivos")

    monkeypatch.setattr(control_facturas, "extraer_arca", fake_arca)
    monkeypatch.setattr(control_facturas, "extraer_xubio", fake_xubio)
    monkeypatch.setattr(control_facturas, "procesar_cruce", fake_cruce)

    resultado = control_facturas.ejecutar_control_facturas(
        request=_request(pasos=["arca", "xubio"]),
        clientes=CLIENTES,
        xubio_user="u",
        xubio_pass="p",
        base_salida=tmp_path,
        log=lambda mensaje: None,
    )

    assert len(resultado.errores) == 1
    assert resultado.errores[0].code == "login_error"
    assert resultado.errores[0].paso == "arca"


def test_cliente_inexistente_se_reporta(tmp_path: Path):
    resultado = control_facturas.ejecutar_control_facturas(
        request=_request(pasos=["arca"]),
        clientes={},
        xubio_user="u",
        xubio_pass="p",
        base_salida=tmp_path,
        log=lambda mensaje: None,
    )
    assert resultado.errores[0].code == "cliente_no_encontrado"


def test_cancelacion_es_cooperativa(tmp_path: Path):
    with pytest.raises(Cancelado):
        control_facturas.ejecutar_control_facturas(
            request=_request(),
            clientes=CLIENTES,
            xubio_user="u",
            xubio_pass="p",
            base_salida=tmp_path,
            log=lambda mensaje: None,
            cancelado=lambda: True,
        )
