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


def test_factura_y_nota_de_credito_con_el_mismo_numero_no_son_duplicado(tmp_path: Path):
    from backend.app.auditor.cruce_fo import _clase_comprobante
    assert _clase_comprobante("Nota Crédito") == "NC"
    assert _clase_comprobante("Factura") == "FC"
    assert _clase_comprobante("13 - Nota de Crédito C") == "NC"


def test_anuladas_con_nota_de_credito():
    from backend.app.auditor.cruce_fo import _anuladas_con_nc

    def fila(tipo, cuit, total):
        return {"origen": "arca", "fila": [
            {"columna": "Tipo", "valor": tipo},
            {"columna": "Nro. Doc.", "valor": cuit},
            {"columna": "Imp. Total", "valor": total},
        ]}

    fc, nc, otra = fila("11 - Factura C", "27942062635", 623532.18), fila("13 - Nota de Crédito C", "27942062635", 623532.18), fila("11 - Factura C", "20374382803", 120000.0)
    anuladas = _anuladas_con_nc([fc, otra, nc])
    assert any(a is fc for a in anuladas) and any(a is nc for a in anuladas)
    assert not any(a is otra for a in anuladas)


def test_cloudflare_permitido_para_el_login_de_arca():
    from backend.app.core import config
    assert "challenges.cloudflare.com" in config.EGRESS_DOMINIOS


def test_compras_compara_por_cuit_y_tipo():
    """Caso real de septiembre: la NC C-00001-00000001 de Crisci y la factura
    C-00001-00000001 de Díaz comparten número; ninguna debe esconder a la otra."""
    from backend.app.auditor.cruce_fo import _faltantes_por_cuit

    arca = pd.DataFrame({
        "Nro. Doc.": ["20140568121", "20140568121", "23324208909", "23324208909"],
        "Clave_Py": ["C-00001-00000001", "C-00001-00000001", "C-00001-00000093", "C-00001-00000001"],
        "Tipo": ["11 - Factura C", "13 - Nota de Crédito C", "11 - Factura C", "13 - Nota de Crédito C"],
    })
    xubio = pd.DataFrame({
        "CUIT": ["20-14056812-1", "20-14056812-1"],
        "Comprobante": ["C-00001-00000001", "C-00001-00000001"],
        "Tipo": ["Factura", "Nota Crédito"],
    })
    falta_en_xubio, falta_en_arca = _faltantes_por_cuit(arca, xubio, "Nro. Doc.")
    assert list(falta_en_xubio["Nro. Doc."]) == ["23324208909", "23324208909"]  # solo Crisci
    assert falta_en_arca.empty  # Díaz: factura y NC cargadas, no son duplicado


def test_el_excel_pinta_cada_error_con_su_color(tmp_path: Path):
    import openpyxl
    from backend.app.auditor.cruce_fo import COLORES_ERROR

    arca, xubio = tmp_path / "arca.xlsx", tmp_path / "xubio.xlsx"
    _arca(arca, [("11 - Factura C", "23473905639", "SOTO STOCKMAYER FRANCO")])
    _xubio(xubio, [("A-0002-00000006", "23-47390563-9", "FRANCO SOTO STOCKMAYER")])
    ruta = procesar_cruce(
        "PICCININI", "Compras", "01/04/2026", "30/04/2026", str(tmp_path),
        ruta_arca_manual=str(arca), ruta_xubio_manual=str(xubio), log=lambda _: None,
    )
    wb = openpyxl.load_workbook(ruta)
    violeta = COLORES_ERROR["letra_distinta"][0]
    assert wb["Libro Iva"].cell(2, 1).fill.fgColor.rgb.endswith(violeta)
    assert "Referencias" in wb.sheetnames
    assert len(set(c for c, _ in COLORES_ERROR.values())) == len(COLORES_ERROR)  # sin colores repetidos
