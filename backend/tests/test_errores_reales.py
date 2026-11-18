"""Qué es un error y qué es un aviso en el cruce ARCA / Xubio.

Error (rojo): algo quedó mal cargado en Xubio — letra, tipo, CUIT, importe o
repetido. Aviso: un comprobante que está de un solo lado (falta cargarlo o hay
que preguntar). Acá se prueban los dos errores que antes pasaban como faltantes
o no se informaban: CUIT distinto e importe distinto.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from backend.app.auditor import procesar_cruce

from .test_auditor import COLUMNAS_ARCA, COLUMNAS_XUBIO


def _arca(path: Path, filas: list[dict]) -> None:
    datos = []
    for f in filas:
        datos.append({
            "Fecha": "07/04/2026", "Tipo": f.get("tipo", "11 - Factura C"), "Punto de Venta": 2,
            "Número Desde": f.get("numero", 6), "Número Hasta": f.get("numero", 6),
            "Denominación Emisor": f.get("nombre", "PROVEEDOR"),
            "Nro. Doc. Emisor": f["cuit"], "CUIT Emisor": f["cuit"],
            "Condición IVA": "Monotributista", "Neto Gravado 21%": 0, "IVA 21%": 0,
            "Neto gravado total": 0, "Total IVA": 0, "Otros Tributos": 0,
            "Imp. Total": f.get("total", 1000), "Tipo Cambio": f.get("tc", 1),
        })
    pd.DataFrame(datos, columns=COLUMNAS_ARCA).to_excel(path, index=False, startrow=1)


def _xubio(path: Path, filas: list[dict]) -> None:
    datos = []
    for f in filas:
        fila = {c: None for c in COLUMNAS_XUBIO}
        total = f.get("total", 1000)
        fila.update({
            "Fecha": "07/04/2026", "Tipo": f.get("tipo", "Factura"),
            "Comprobante": f.get("comprobante", "C-0002-00000006"),
            "Proveedor": f.get("nombre", "PROVEEDOR"), "CUIT": f["cuit"],
            "Condición IVA": "Monotributista", "No gravado exento": total, "Total": total,
        })
        datos.append(fila)
    pd.DataFrame(datos, columns=COLUMNAS_XUBIO).to_excel(path, index=False)


def _errores(tmp_path: Path, arca, xubio, operacion="Compras") -> dict[str, dict]:
    ra, rx = tmp_path / "arca.xlsx", tmp_path / "xubio.xlsx"
    _arca(ra, arca)
    _xubio(rx, xubio)
    errores: list[dict] = []
    procesar_cruce(
        "PICCININI", operacion, "01/04/2026", "30/04/2026", str(tmp_path),
        ruta_arca_manual=str(ra), ruta_xubio_manual=str(rx),
        log=lambda _: None, avisar_errores=errores.extend,
    )
    return {e["tipo"]: e for e in errores}


def test_mismo_importe_no_es_error(tmp_path: Path):
    errores = _errores(tmp_path, [{"cuit": "27479591992"}], [{"cuit": "27-47959199-2"}])
    assert errores == {}


def test_importe_distinto_es_error(tmp_path: Path):
    errores = _errores(tmp_path, [{"cuit": "27479591992", "total": 1000}], [{"cuit": "27-47959199-2", "total": 1100}])
    e = errores["importe_distinto"]
    assert e["gravedad"] == "corregir"
    assert e["a_corregir"] == 1
    assert [c["origen"] for c in e["comprobantes"]] == ["arca", "xubio"]
    # No es un faltante: el comprobante está en los dos lados.
    assert "faltante_en_xubio" not in errores and "faltante_en_arca" not in errores


def test_diferencia_de_centavos_no_es_error(tmp_path: Path):
    errores = _errores(tmp_path, [{"cuit": "27479591992", "total": 1000}], [{"cuit": "27-47959199-2", "total": 1000.4}])
    assert "importe_distinto" not in errores


def test_factura_en_dolares_se_compara_en_pesos(tmp_path: Path):
    errores = _errores(
        tmp_path,
        [{"cuit": "27479591992", "total": 100, "tc": 1000}],
        [{"cuit": "27-47959199-2", "total": 100000}],
    )
    assert "importe_distinto" not in errores


def test_nota_de_credito_compara_sin_signo(tmp_path: Path):
    errores = _errores(
        tmp_path,
        [{"cuit": "27479591992", "tipo": "13 - Nota de Crédito C", "total": 500}],
        [{"cuit": "27-47959199-2", "tipo": "Nota de Crédito", "total": -500}],
    )
    assert "importe_distinto" not in errores


def test_cuit_distinto_es_error_y_no_dos_faltantes(tmp_path: Path):
    errores = _errores(tmp_path, [{"cuit": "27479591992"}], [{"cuit": "27-94206263-5"}])
    e = errores["cuit_distinto"]
    assert e["gravedad"] == "corregir"
    assert e["a_corregir"] == 1
    assert "faltante_en_xubio" not in errores and "faltante_en_arca" not in errores


def test_otro_proveedor_con_otro_importe_siguen_siendo_faltantes(tmp_path: Path):
    errores = _errores(
        tmp_path,
        [{"cuit": "27479591992", "total": 1000}],
        [{"cuit": "27-94206263-5", "total": 7777}],
    )
    assert "cuit_distinto" not in errores
    assert errores["faltante_en_xubio"]["a_corregir"] == 1
    assert errores["faltante_en_arca"]["a_corregir"] == 1


def test_faltantes_cuentan_solo_los_que_hay_que_tocar(tmp_path: Path):
    # Un tique en Xubio es normal (ARCA no lo informa): no cuenta para «a revisar».
    errores = _errores(
        tmp_path,
        [{"cuit": "27479591992"}],
        [
            {"cuit": "27-47959199-2"},
            {"cuit": "30-67791583-4", "tipo": "Tique", "comprobante": "A-0006-00034327"},
            {"cuit": "30-71839989-7", "comprobante": "A-0006-00000001", "total": 5445},
        ],
    )
    e = errores["faltante_en_arca"]
    assert e["total"] == 2
    assert e["a_corregir"] == 1
