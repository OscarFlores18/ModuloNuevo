"""ARCA a veces ignora el período pedido y trae el mes en curso (caso real:
8vo control de octubre, Recibidos trajo 1 comprobante del 17/11)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import openpyxl

from backend.app.bots.arca import fechas_fuera_de_rango


def _excel_arca(ruta: Path, fechas: list) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Mis Comprobantes Recibidos - CUIT 30710809883"])
    ws.append(["Fecha", "Tipo", "Imp. Total"])
    for f in fechas:
        ws.append([f, "11 - Factura C", 100.0])
    wb.save(ruta)
    return ruta


def test_detecta_comprobantes_de_otro_periodo(tmp_path: Path):
    ruta = _excel_arca(tmp_path / "a.xlsx", ["17/11/2026"])
    assert fechas_fuera_de_rango(ruta, "01/10/2026", "31/10/2026") == ["17/11/2026"]


def test_el_periodo_pedido_pasa(tmp_path: Path):
    ruta = _excel_arca(tmp_path / "a.xlsx", ["01/10/2026", "15/10/2026", datetime(2026, 10, 31), "31/10/2026"])
    assert fechas_fuera_de_rango(ruta, "01/10/2026", "31/10/2026") == []


def test_sin_comprobantes_no_es_otro_periodo(tmp_path: Path):
    ruta = _excel_arca(tmp_path / "a.xlsx", [])
    assert fechas_fuera_de_rango(ruta, "01/10/2026", "31/10/2026") == []
