"""Casos borde del cruce que llegaban mal al archivo para Xubio o pasaban sin aviso.

- Facturas en dólares: el CSV para Xubio va en la moneda original (como el de ARCA),
  una sola vez y sin filas vacías.
- Lo que encuentra la 2ª pasada (letra, tipo o CUIT distinto) es un error de carga,
  pero el AUX sale como lo dio el cruce: sigue en «Faltantes» hasta que se corrija.
- Ventas: factura y nota de crédito pueden tener el mismo número; una factura cargada
  como nota de crédito, o a otro cliente, es un error.
- Libro IVA con las columnas en otro orden se lee por nombre; sin las columnas
  clave, el control avisa en vez de cruzar mal.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from backend.app.auditor import procesar_cruce
from backend.app.auditor.csv_arca import convertir_auxiliares
from backend.app.core.errors import ArchivoIlegible

from .test_errores_reales import _arca, _xubio


def _a_ventas(ruta: Path) -> None:
    """El Excel de ARCA de Emitidos trae al receptor (el cliente), no al emisor."""
    df = pd.read_excel(ruta, skiprows=1)
    df = df.rename(columns={"Denominación Emisor": "Denominación Receptor", "Nro. Doc. Emisor": "Nro. Doc. Receptor"})
    df = df.drop(columns=["CUIT Emisor"])
    df.to_excel(ruta, index=False, startrow=1)


def _cruzar(tmp_path: Path, arca, xubio, operacion="Compras", preparar_arca=None, preparar_xubio=None):
    ra, rx = tmp_path / "arca.xlsx", tmp_path / "xubio.xlsx"
    _arca(ra, arca)
    if operacion == "Ventas":
        _a_ventas(ra)
    if preparar_arca:
        preparar_arca(ra)
    _xubio(rx, xubio)
    if preparar_xubio:
        preparar_xubio(rx)
    errores: list[dict] = []
    salida = procesar_cruce(
        "ACME", operacion, "01/04/2026", "30/04/2026", str(tmp_path),
        ruta_arca_manual=str(ra), ruta_xubio_manual=str(rx),
        log=lambda _: None, avisar_errores=errores.extend,
    )
    return salida, {e["tipo"]: e for e in errores}


def _csv(salida: str, operacion: str) -> list[list[str]]:
    """Las filas (sin encabezado) del CSV que se sube a Xubio."""
    nombre = f"AUX - Control de facturas {operacion} 04-26 - ACME.xlsx"
    filas: list[list[str]] = []
    for r in convertir_auxiliares([(nombre, Path(salida).read_bytes())]):
        assert r["error"] is None
        for z in r["zips"].values():
            with zipfile.ZipFile(io.BytesIO(z["contenido"])) as zf:
                for n in zf.namelist():
                    filas += [l.split(";") for l in zf.read(n).decode("utf-8").splitlines()[1:]]
    return filas


# Posiciones en el CSV de Mis Comprobantes.
NUMERO, NRO_DOC, TIPO_CAMBIO, IMP_TOTAL = 3, 7, 9, 27


def test_factura_en_dolares_va_en_moneda_original_una_sola_vez(tmp_path: Path):
    salida, _ = _cruzar(
        tmp_path,
        [{"cuit": "20111111112", "total": 100, "tc": 1000}],
        [{"cuit": "20999999992", "comprobante": "C-0009-00000001"}],
    )
    filas = _csv(salida, "Compras")
    assert len(filas) == 1
    assert filas[0][IMP_TOTAL] == "100,00"
    assert filas[0][TIPO_CAMBIO] == "1000,00"
    # El AUX sigue mostrando cuánto es en pesos, en una hoja aparte.
    wb = openpyxl.load_workbook(salida)
    assert "USD en pesos" in wb.sheetnames


@pytest.mark.parametrize(
    ("caso", "arca", "xubio", "error"),
    [
        ("letra", {"cuit": "20111111112", "tipo": "11 - Factura C"},
         {"cuit": "20111111112", "comprobante": "A-0002-00000006"}, "letra_distinta"),
        ("tipo", {"cuit": "20111111112"}, {"cuit": "20111111112", "tipo": "Nota Débito"}, "tipo_distinto"),
        ("cuit", {"cuit": "20111111112"}, {"cuit": "20999999992"}, "cuit_distinto"),
    ],
)
def test_lo_de_la_segunda_pasada_queda_en_faltantes_sin_corregir(tmp_path: Path, caso, arca, xubio, error):
    salida, errores = _cruzar(tmp_path, [arca], [xubio])
    assert error in errores
    assert "faltante_en_xubio" not in errores
    # Nadie lo corrigió: la hoja «Faltantes en Libro IVA» lo sigue trayendo.
    assert len(_csv(salida, "Compras")) == 1


def test_ventas_factura_y_nota_de_credito_con_el_mismo_numero(tmp_path: Path):
    salida, errores = _cruzar(
        tmp_path,
        [
            {"cuit": "20111111112", "tipo": "11 - Factura C"},
            {"cuit": "20111111112", "tipo": "13 - Nota de Crédito C", "total": 200},
        ],
        [{"cuit": "20111111112"}],
        operacion="Ventas",
    )
    assert errores["faltante_en_xubio"]["a_corregir"] == 1
    assert "duplicado" not in errores
    filas = _csv(salida, "Ventas")
    assert len(filas) == 1 and filas[0][1] == "13"


def test_ventas_factura_cargada_como_nota_de_credito(tmp_path: Path):
    salida, errores = _cruzar(
        tmp_path, [{"cuit": "20111111112"}], [{"cuit": "20111111112", "tipo": "Nota Crédito"}], operacion="Ventas",
    )
    assert errores["tipo_distinto"]["a_corregir"] == 2
    assert "faltante_en_xubio" not in errores
    # Sin corregir, sigue en la hoja «Faltantes» del AUX.
    assert len(_csv(salida, "Ventas")) == 1


def test_ventas_cargada_a_otro_cliente(tmp_path: Path):
    _, errores = _cruzar(tmp_path, [{"cuit": "20111111112"}], [{"cuit": "20999999992"}], operacion="Ventas")
    assert errores["cuit_distinto"]["a_corregir"] == 1
    assert "faltante_en_xubio" not in errores and "faltante_en_arca" not in errores


def test_ventas_consumidor_final_sin_cuit_no_es_error(tmp_path: Path):
    _, errores = _cruzar(tmp_path, [{"cuit": "0"}], [{"cuit": None}], operacion="Ventas")
    assert errores == {}


def _invertir_cuit_y_proveedor(ruta: Path) -> None:
    df = pd.read_excel(ruta)
    columnas = list(df.columns)
    i, j = columnas.index("CUIT"), columnas.index("Proveedor")
    columnas[i], columnas[j] = columnas[j], columnas[i]
    df[columnas].to_excel(ruta, index=False)


def test_libro_iva_con_columnas_en_otro_orden(tmp_path: Path):
    _, errores = _cruzar(
        tmp_path, [{"cuit": "20111111112"}], [{"cuit": "20111111112"}], preparar_xubio=_invertir_cuit_y_proveedor,
    )
    assert errores == {}


def test_libro_iva_sin_columnas_clave_avisa(tmp_path: Path):
    def sin_comprobante(ruta: Path) -> None:
        pd.read_excel(ruta).drop(columns=["Comprobante", "CUIT"]).to_excel(ruta, index=False)

    with pytest.raises(ArchivoIlegible, match="Comprobante"):
        _cruzar(tmp_path, [{"cuit": "20111111112"}], [{"cuit": "20111111112"}], preparar_xubio=sin_comprobante)


def test_exentos_y_no_gravado_pasan_al_libro_iva(tmp_path: Path):
    def con_exentos(ruta: Path) -> None:
        df = pd.read_excel(ruta, skiprows=1)
        df["Neto No Gravado"] = 300
        df["Op. Exentas"] = 700
        df.to_excel(ruta, index=False, startrow=1)

    salida, _ = _cruzar(
        tmp_path, [{"cuit": "20111111112"}], [{"cuit": "20999999992", "comprobante": "C-0009-00000001"}],
        preparar_arca=con_exentos,
    )
    ws = openpyxl.load_workbook(salida)["Libro Iva"]
    titulos = {str(c.value): c.column for c in ws[1]}
    fila = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, titulos["Comprobante"]).value == "C-00002-00000006")
    assert ws.cell(fila, titulos["No gravado exento"]).value == 1000
    assert ws.cell(fila, titulos["Total"]).value == 1000


def test_importe_distinto_en_dolares_trae_el_total_en_pesos(tmp_path: Path):
    """«Aplicar» escribe en el Libro IVA (en pesos): necesita el total convertido."""
    _, errores = _cruzar(
        tmp_path, [{"cuit": "20111111112", "total": 100, "tc": 1000}], [{"cuit": "20111111112", "total": 90000}],
    )
    arca = next(c for c in errores["importe_distinto"]["comprobantes"] if c["origen"] == "arca")
    fila = {c["columna"]: c["valor"] for c in arca["fila"]}
    assert float(fila["Imp. Total"]) == 100 and fila["Total en pesos"] == 100000
