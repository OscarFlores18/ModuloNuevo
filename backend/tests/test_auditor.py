from __future__ import annotations

from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from backend.app.auditor import procesar_cruce
from backend.app.core.errors import ArchivoNoEncontrado

COLUMNAS_ARCA = [
    "Fecha",
    "Tipo",
    "Punto de Venta",
    "Número Desde",
    "Número Hasta",
    "Denominación Emisor",
    "Nro. Doc. Emisor",
    "CUIT Emisor",
    "Condición IVA",
    "Neto Gravado 21%",
    "IVA 21%",
    "Neto gravado total",
    "Total IVA",
    "Otros Tributos",
    "Imp. Total",
    "Tipo Cambio",
]

COLUMNAS_XUBIO = [
    "Fecha",
    "Tipo",
    "Comprobante",
    "Proveedor",
    "CUIT",
    "Condición IVA",
    "Neto gravado 21%",
    "IVA 21%",
    "Neto gravado 10.5%",
    "IVA 10.5%",
    "Neto gravado 27%",
    "IVA 27%",
    "Neto gravado 2.5%",
    "IVA 2.5%",
    "Neto gravado 5%",
    "IVA 5%",
    "Retención IVA",
    "Percepción IVA",
    "Percepción IIBB",
    "Impuestos internos",
    "No gravado exento",
    "Total",
]


def _crear_arca(path: Path) -> None:
    fila = {
        "Fecha": "15/03/2025",
        "Tipo": "Factura A",
        "Punto de Venta": 1,
        "Número Desde": 123,
        "Número Hasta": 123,
        "Denominación Emisor": "Proveedor SA",
        "Nro. Doc. Emisor": "30712345678",
        "CUIT Emisor": "30712345678",
        "Condición IVA": "Responsable Inscripto",
        "Neto Gravado 21%": 1000,
        "IVA 21%": 210,
        "Neto gravado total": 1000,
        "Total IVA": 210,
        "Otros Tributos": 0,
        "Imp. Total": 1210,
        "Tipo Cambio": 1,
    }
    pd.DataFrame([fila], columns=COLUMNAS_ARCA).to_excel(
        path, index=False, startrow=1
    )


def _crear_xubio(path: Path) -> None:
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
    pd.DataFrame([fila], columns=COLUMNAS_XUBIO).to_excel(path, index=False)


def test_genera_control_de_facturas(tmp_path: Path):
    arca = tmp_path / "arca.xlsx"
    xubio = tmp_path / "xubio.xlsx"
    _crear_arca(arca)
    _crear_xubio(xubio)

    salida = procesar_cruce(
        "Acme",
        "Ventas",
        "01/03/2025",
        "31/03/2025",
        str(tmp_path),
        ruta_arca_manual=str(arca),
        ruta_xubio_manual=str(xubio),
    )

    assert Path(salida).is_file()
    wb = openpyxl.load_workbook(salida)
    assert "Mis Comprobantes" in wb.sheetnames
    assert "Libro Iva" in wb.sheetnames
    assert wb["Mis Comprobantes"].max_row >= 2
    assert wb["Libro Iva"].max_row >= 2


def test_falta_archivo_arca(tmp_path: Path):
    xubio = tmp_path / "xubio.xlsx"
    _crear_xubio(xubio)

    with pytest.raises(ArchivoNoEncontrado):
        procesar_cruce(
            "Acme",
            "Ventas",
            "01/03/2025",
            "31/03/2025",
            str(tmp_path),
            ruta_arca_manual=str(tmp_path / "no-existe.xlsx"),
            ruta_xubio_manual=str(xubio),
        )
