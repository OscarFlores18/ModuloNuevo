"""Misma factura con otra letra en Xubio: aviso que no frena el control."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from backend.app.auditor import procesar_cruce
from backend.app.auditor.cruce_fo import texto_letras_distintas

from .test_auditor import COLUMNAS_ARCA, COLUMNAS_XUBIO


def _arca(path: Path, filas: list[tuple[str, str, str]]) -> None:
    """(tipo, cuit, denominación) con punto de venta 2 y número 6."""
    datos = []
    for tipo, cuit, nombre in filas:
        datos.append({
            "Fecha": "07/04/2026", "Tipo": tipo, "Punto de Venta": 2,
            "Número Desde": 6, "Número Hasta": 6, "Denominación Emisor": nombre,
            "Nro. Doc. Emisor": cuit, "CUIT Emisor": cuit,
            "Condición IVA": "Monotributista", "Neto Gravado 21%": 0, "IVA 21%": 0,
            "Neto gravado total": 0, "Total IVA": 0, "Otros Tributos": 0,
            "Imp. Total": 604887, "Tipo Cambio": 1,
        })
    pd.DataFrame(datos, columns=COLUMNAS_ARCA).to_excel(path, index=False, startrow=1)


def _xubio(path: Path, filas: list[tuple[str, str, str]]) -> None:
    """(comprobante, cuit, proveedor)."""
    datos = []
    for comprobante, cuit, nombre in filas:
        fila = {c: None for c in COLUMNAS_XUBIO}
        fila.update({
            "Fecha": "07/04/2026", "Tipo": "Factura", "Comprobante": comprobante,
            "Proveedor": nombre, "CUIT": cuit, "Condición IVA": "Monotributista",
            "No gravado exento": 604887, "Total": 604887,
        })
        datos.append(fila)
    pd.DataFrame(datos, columns=COLUMNAS_XUBIO).to_excel(path, index=False)


def _cruce(tmp_path: Path, arca_filas, xubio_filas) -> tuple[list[dict], list[str]]:
    arca, xubio = tmp_path / "arca.xlsx", tmp_path / "xubio.xlsx"
    _arca(arca, arca_filas)
    _xubio(xubio, xubio_filas)
    avisos: list[dict] = []
    log: list[str] = []
    salida = procesar_cruce(
        "PICCININI", "Compras", "01/04/2026", "30/04/2026", str(tmp_path),
        ruta_arca_manual=str(arca), ruta_xubio_manual=str(xubio),
        log=log.append, avisar_letras_distintas=avisos.extend,
    )
    assert Path(salida).is_file()  # el control se genera igual
    return avisos, log


def test_detecta_la_letra_distinta(tmp_path: Path):
    avisos, log = _cruce(
        tmp_path,
        [("11 - Factura C", "23473905639", "SOTO STOCKMAYER FRANCO")],
        [("A-0002-00000006", "23-47390563-9", "FRANCO SOTO STOCKMAYER")],
    )
    assert len(avisos) == 1
    aviso = avisos[0]
    assert aviso["proveedor"] == "Soto Stockmayer Franco"
    assert aviso["cuit"] == "23473905639"
    assert (aviso["comprobante_arca"], aviso["comprobante_xubio"]) == ("C-00002-00000006", "A-00002-00000006")
    assert (aviso["letra_arca"], aviso["letra_xubio"]) == ("C", "A")
    # Las dos filas, para dibujarlas como en Excel (y que se puedan pasar a JSON).
    arca = {c["columna"]: c["valor"] for c in aviso["fila_arca"]}
    xubio = {c["columna"]: c["valor"] for c in aviso["fila_xubio"]}
    assert arca["Comprobante"] == "C-00002-00000006" and float(arca["Imp. Total"]) == 604887
    assert xubio["Comprobante"] == "A-00002-00000006" and xubio["CUIT"] == "23-47390563-9"
    import json
    json.dumps(avisos)
    assert any("letra distinta" in linea for linea in log)


def test_mismo_numero_de_otro_proveedor_no_avisa(tmp_path: Path):
    avisos, _ = _cruce(
        tmp_path,
        [("11 - Factura C", "27479591992", "GAZZO MARIA PAULA")],
        [("A-0002-00000006", "23-47390563-9", "FRANCO SOTO STOCKMAYER")],
    )
    assert avisos == []


def test_si_coincide_la_letra_no_avisa(tmp_path: Path):
    avisos, _ = _cruce(
        tmp_path,
        [("11 - Factura C", "23473905639", "SOTO STOCKMAYER FRANCO")],
        [("C-0002-00000006", "23-47390563-9", "FRANCO SOTO STOCKMAYER")],
    )
    assert avisos == []


def test_texto_del_aviso():
    letra = {
        "proveedor": "Soto Stockmayer Franco", "comprobante_arca": "C-00002-00000006",
        "letra_xubio": "A",
    }
    assert texto_letras_distintas("PICCININI", "Compras", [letra]) == (
        "PICCININI – Compras: 1 comprobante con letra distinta entre ARCA y Xubio "
        "(Soto Stockmayer Franco, C-00002-00000006 cargado como A). Corregilo en Xubio."
    )
    assert "2 comprobantes" in texto_letras_distintas("X", "Ventas", [letra, letra])
    assert "Corregilos en Xubio." in texto_letras_distintas("X", "Ventas", [letra, letra])
