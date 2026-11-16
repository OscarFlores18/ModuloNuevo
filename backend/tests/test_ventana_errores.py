"""Ventana de errores: qué encontró la auditoría y cómo solucionarlo."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from backend.app.auditor import procesar_cruce
from backend.app.services import ejecuciones as srv

from .test_letra_distinta import _arca, _xubio
from .test_auditor import COLUMNAS_ARCA


def _errores(tmp_path: Path, arca_filas, xubio_filas, arca_vacio=False) -> dict[str, dict]:
    arca, xubio = tmp_path / "arca.xlsx", tmp_path / "xubio.xlsx"
    if arca_vacio:
        pd.DataFrame([], columns=COLUMNAS_ARCA).to_excel(arca, index=False, startrow=1)
    else:
        _arca(arca, arca_filas)
    _xubio(xubio, xubio_filas)
    errores: list[dict] = []
    procesar_cruce(
        "PICCININI", "Compras", "01/04/2026", "30/04/2026", str(tmp_path),
        ruta_arca_manual=str(arca), ruta_xubio_manual=str(xubio),
        log=lambda _: None, avisar_errores=errores.extend,
    )
    json.dumps(errores)  # viaja a la web
    for e in errores:
        assert e["titulo"] and e["acciones"], e
    return {e["tipo"]: e for e in errores}


def test_arca_vacio_es_grave_y_apunta_al_cuit_representado(tmp_path: Path):
    errores = _errores(tmp_path, [], [("C-0002-00000006", "23-47390563-9", "FRANCO")], arca_vacio=True)
    assert list(errores) == ["arca_vacio"]
    assert errores["arca_vacio"]["gravedad"] == "grave"
    assert errores["arca_vacio"]["acciones"] == ["Corregir CUIT del cliente"]
    assert "CUIT representado" in errores["arca_vacio"]["detalle"]


def test_letra_distinta_no_se_repite_como_faltante(tmp_path: Path):
    errores = _errores(
        tmp_path,
        [("11 - Factura C", "23473905639", "SOTO STOCKMAYER FRANCO")],
        [("A-0002-00000006", "23-47390563-9", "FRANCO SOTO STOCKMAYER")],
    )
    assert list(errores) == ["letra_distinta"]
    assert errores["letra_distinta"]["total"] == 1


def test_faltantes_de_los_dos_lados(tmp_path: Path):
    errores = _errores(
        tmp_path,
        [("11 - Factura C", "27479591992", "GAZZO MARIA PAULA")],
        [("C-0002-00000099", "23-47390563-9", "FRANCO SOTO STOCKMAYER")],
    )
    assert errores["faltante_en_xubio"]["comprobantes"][0]["origen"] == "arca"
    assert errores["faltante_en_arca"]["comprobantes"][0]["origen"] == "xubio"
    assert errores["faltante_en_xubio"]["hoja"] == "Faltantes en Libro IVA Compras"


def test_mismo_numero_de_otro_proveedor_no_es_duplicado(tmp_path: Path):
    errores = _errores(
        tmp_path,
        [("11 - Factura C", "27479591992", "GAZZO"), ("11 - Factura C", "27942062635", "FERNANDEZ")],
        [("C-0002-00000006", "27-47959199-2", "GAZZO"), ("C-0002-00000006", "27-94206263-5", "FERNANDEZ")],
    )
    assert "duplicado" not in errores


def test_duplicado_real_mismo_cuit(tmp_path: Path):
    errores = _errores(
        tmp_path,
        [("11 - Factura C", "27479591992", "GAZZO")],
        [("C-0002-00000006", "27-47959199-2", "GAZZO"), ("C-0002-00000006", "27-47959199-2", "GAZZO")],
    )
    assert errores["duplicado"]["total"] == 2


def test_paso_que_falla_trae_solucion():
    paso = srv.Paso("arca_ventas", "ARCA · Ventas", estado="error", mensaje="Clave fiscal incorrecta al iniciar sesión")
    e = srv._error_de_paso("PICCININI", paso)
    assert e["gravedad"] == "grave"
    assert e["acciones"] == ["Corregir clave de ARCA"]
