"""Duplicados en la auditoría: primero se detectan, después se pregunta."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pandas as pd
import pytest

from backend.app.auditor import procesar_cruce
from backend.app.core import config
from backend.app.core.errors import DuplicadosDetectados
from backend.app.services import ejecuciones as srv

from .test_auditor import COLUMNAS_XUBIO, _crear_arca, _crear_xubio


def _crear_xubio_duplicado(path: Path) -> None:
    fila = {columna: None for columna in COLUMNAS_XUBIO}
    fila.update(
        {
            "Fecha": "15/03/2025",
            "Tipo": "Factura A",
            "Comprobante": "A-1-123",
            "Proveedor": "Proveedor SA",
            "CUIT": "30712345678",
            "Condición IVA": "Responsable Inscripto",
            "Neto gravado 21%": 1000,
            "IVA 21%": 210,
            "Total": 1210,
        }
    )
    pd.DataFrame([fila, fila], columns=COLUMNAS_XUBIO).to_excel(path, index=False)


def _cruce(tmp_path: Path, xubio_duplicado: bool, **kwargs):
    arca, xubio = tmp_path / "arca.xlsx", tmp_path / "xubio.xlsx"
    _crear_arca(arca)
    (_crear_xubio_duplicado if xubio_duplicado else _crear_xubio)(xubio)
    return procesar_cruce(
        "Acme", "Ventas", "01/03/2025", "31/03/2025", str(tmp_path),
        ruta_arca_manual=str(arca), ruta_xubio_manual=str(xubio), **kwargs,
    )


def _aux(tmp_path: Path) -> list[Path]:
    return list(tmp_path.glob("AUX - *.xlsx"))


def test_sin_duplicados_no_pregunta(tmp_path: Path):
    preguntas: list[dict] = []
    _cruce(tmp_path, False, confirmar_duplicados=lambda i: preguntas.append(i) or True)
    assert preguntas == []
    assert len(_aux(tmp_path)) == 1


def test_duplicados_y_el_usuario_frena(tmp_path: Path):
    preguntas: list[dict] = []
    with pytest.raises(DuplicadosDetectados):
        _cruce(tmp_path, True, confirmar_duplicados=lambda i: preguntas.append(i) or False)
    assert preguntas[0]["xubio"] == ["A-00001-00000123"]
    assert preguntas[0]["arca"] == []
    assert _aux(tmp_path) == []  # no se genera un control mal hecho


def test_duplicados_y_el_usuario_sigue(tmp_path: Path):
    _cruce(tmp_path, True, confirmar_duplicados=lambda i: True)
    assert len(_aux(tmp_path)) == 1


def _ejecucion() -> srv.Ejecucion:
    return srv.Ejecucion(id="x", usuario="u", parametros={}, clientes=[])


INFO = {"cliente": "Acme", "operacion": "Ventas", "arca": [], "xubio": ["A-00001-00000123"]}


@pytest.mark.parametrize("accion", ["continuar", "omitir", "cancelar"])
def test_pregunta_en_la_web_y_espera_la_respuesta(accion):
    ej = _ejecucion()
    resultado: list[str] = []
    hilo = threading.Thread(target=lambda: resultado.append(srv._preguntar_duplicados(ej, INFO)))
    hilo.start()
    for _ in range(100):
        if ej.pausa:
            break
        time.sleep(0.01)
    assert ej.estado == "esperando"
    assert ej.pausa["tipo"] == "duplicados"
    assert ej.pausa["xubio"] == ["A-00001-00000123"]
    with pytest.raises(srv.ErrorEjecucion):
        srv.responder_pausa(ej, "no_preguntar")  # no aplica a esta pausa
    srv.responder_pausa(ej, accion)
    hilo.join(2)
    assert resultado == [accion]
    assert ej.pausa is None and ej.estado == "en_curso"


def test_si_nadie_responde_se_saltea(monkeypatch):
    monkeypatch.setattr(config, "ESPERA_DUPLICADOS_S", 0)
    assert srv._preguntar_duplicados(_ejecucion(), INFO) == "omitir"
