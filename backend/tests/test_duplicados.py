"""Duplicados en la auditoría: se anotan, pero el control nunca se frena."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from backend.app.auditor import procesar_cruce

from .test_auditor import COLUMNAS_XUBIO, _crear_arca


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


def test_con_duplicados_genera_el_control_igual(tmp_path: Path):
    arca, xubio = tmp_path / "arca.xlsx", tmp_path / "xubio.xlsx"
    _crear_arca(arca)
    _crear_xubio_duplicado(xubio)
    log: list[str] = []

    salida = procesar_cruce(
        "Acme", "Ventas", "01/03/2025", "31/03/2025", str(tmp_path),
        ruta_arca_manual=str(arca), ruta_xubio_manual=str(xubio), log=log.append,
    )

    assert Path(salida).is_file()
    assert any("duplicado" in linea for linea in log)
