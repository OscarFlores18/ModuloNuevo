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
    # Lo que hay que hacer: subirlas desde la pestaña «Faltantes a Xubio».
    assert errores["faltante_en_xubio"]["acciones"] == ["Subir desde Faltantes a Xubio"]
    assert "desde «Faltantes a Xubio»" in errores["faltante_en_xubio"]["detalle"]


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


def test_el_excel_marca_los_errores_en_rojo_claro_y_negrita(tmp_path: Path):
    import openpyxl

    arca, xubio = tmp_path / "arca.xlsx", tmp_path / "xubio.xlsx"
    _arca(arca, [("11 - Factura C", "23473905639", "SOTO STOCKMAYER FRANCO")])
    _xubio(xubio, [("A-0002-00000006", "23-47390563-9", "FRANCO SOTO STOCKMAYER")])
    ruta = procesar_cruce(
        "PICCININI", "Compras", "01/04/2026", "30/04/2026", str(tmp_path),
        ruta_arca_manual=str(arca), ruta_xubio_manual=str(xubio), log=lambda _: None,
    )
    wb = openpyxl.load_workbook(ruta)
    libro = wb["Libro Iva"]
    assert libro.cell(2, 1).font.bold
    assert str(libro.cell(2, 1).fill.start_color.rgb).endswith("FFCCCC")  # rojo claro
    # Las columnas amarillas de totales quedan amarillas.
    total_s_libro = {str(c.value).lower(): c.column for c in libro[1] if c.value}["total monto s/libro"]
    assert str(libro.cell(2, total_s_libro).fill.start_color.rgb).endswith("FFFF00")
    estado = {str(c.value).lower(): c.column for c in libro[1] if c.value}["estado conciliación"]
    assert libro.cell(2, estado).value == "Letra distinta: en ARCA es C-00002-00000006"
    assert "Referencias" not in wb.sheetnames


def test_mismo_numero_de_dos_proveedores_concilia_cada_uno_con_su_fila(tmp_path: Path):
    """Gazzo y Fernández tienen C-00002-00000006: cada fila del Libro IVA tiene
    que apuntar a SU fila de ARCA, no al primero que aparezca."""
    import re
    import openpyxl

    arca, xubio = tmp_path / "arca.xlsx", tmp_path / "xubio.xlsx"
    _arca(arca, [("11 - Factura C", "27479591992", "GAZZO"), ("11 - Factura C", "27942062635", "FERNANDEZ")])
    _xubio(xubio, [("C-0002-00000006", "27-94206263-5", "FERNANDEZ"), ("C-0002-00000006", "27-47959199-2", "GAZZO")])
    ruta = procesar_cruce(
        "PICCININI", "Compras", "01/04/2026", "30/04/2026", str(tmp_path),
        ruta_arca_manual=str(arca), ruta_xubio_manual=str(xubio), log=lambda _: None,
    )
    wb = openpyxl.load_workbook(ruta)
    wa, wx = wb["Mis Comprobantes"], wb["Libro Iva"]
    ca = {str(c.value).lower(): c.column for c in wa[1] if c.value}
    cx = {str(c.value).lower(): c.column for c in wx[1] if c.value}
    for r in (2, 3):
        ref = str(wx.cell(r, cx["total monto s/arca"]).value)
        fila_arca = int(re.search(r"(\d+)$", ref).group(1))
        cuit_x = re.sub(r"\D", "", str(wx.cell(r, cx["cuit"]).value))
        cuit_a = re.sub(r"\D", "", str(wa.cell(fila_arca, ca["nro. doc. emisor"]).value))
        assert cuit_a == cuit_x
        assert wx.cell(r, cx["estado conciliación"]).value.startswith("=IF(")  # no «Duplicado»


def test_resumen_por_operacion(tmp_path: Path):
    """Ventas sin hallazgos igual informa cuántos coinciden (para la ventana)."""
    arca, xubio = tmp_path / "arca.xlsx", tmp_path / "xubio.xlsx"
    _arca(arca, [("11 - Factura C", "23473905639", "SOTO")])
    _xubio(xubio, [("C-0002-00000006", "23-47390563-9", "SOTO")])
    resumenes: list[dict] = []
    procesar_cruce(
        "PICCININI", "Compras", "01/04/2026", "30/04/2026", str(tmp_path),
        ruta_arca_manual=str(arca), ruta_xubio_manual=str(xubio),
        log=lambda _: None, avisar_resumen=resumenes.append,
    )
    assert resumenes == [{"operacion": "Compras", "arca": 1, "xubio": 1, "coinciden": 1, "a_corregir": 0}]


def test_columnas_de_arca_que_no_suman_el_total():
    """Caso real (estación de servicio, octubre): neto + IVA no llegan al Imp.
    Total porque ARCA no discrimina el impuesto al combustible."""
    from backend.app.auditor.cruce_fo import diagnosticar

    arca = pd.DataFrame({
        "Fecha": ["08/10/2026", "09/10/2026", "10/10/2026"],
        "Tipo": ["81 - Tique Factura A", "81 - Tique Factura A", "11 - Factura C"],
        "Clave_Py": ["A-00019-00099717", "A-00021-00076517", "C-00002-00000006"],
        "Nro. Doc. Emisor": ["30537781323", "30537781323", "23473905639"],
        "Denominación Emisor": ["ESTACION", "ESTACION", "SOTO"],
        "Neto Gravado Total": [74406.81, 100.0, 0.0],
        "Neto No Gravado": [0.0, 0.0, 0.0],
        "Op. Exentas": [0.0, 0.0, 0.0],
        "Otros Tributos": [0.0, 0.0, 0.0],
        "Total IVA": [15625.43, 21.0, 0.0],
        "Imp. Total": [108011.99, 121.0, 612027.0],  # el 2.º cierra; el 3.º (C) no trae desglose
    })
    xubio = pd.DataFrame({
        "Fecha": ["08/10/2026"], "Tipo": ["Factura"], "Comprobante": ["A-00019-00099717"],
        "CUIT": ["30-53778132-3"], "Proveedor": ["ESTACION"], "Total": [90032.24],
    })
    vacio = pd.DataFrame()
    errores = {e["tipo"]: e for e in diagnosticar(
        "Compras", arca, xubio, vacio, vacio, [], "Nro. Doc. Emisor", "Denominación Emisor", "Fecha", "Imp. Total",
    )}
    e = errores["total_no_cierra"]
    assert e["total"] == 1 and e["gravedad"] == "corregir" and e["a_corregir"] == 1
    # La fila como está en el Excel de ARCA: sin columnas calculadas.
    columnas = [c["columna"] for c in e["comprobantes"][0]["fila"]]
    assert columnas == [
        "Fecha", "Tipo", "Comprobante", "Nro. Doc.", "Denominación",
        "Neto Gravado Total", "Neto No Gravado", "Op. Exentas", "Otros Tributos", "Total IVA", "Imp. Total",
    ]
    fila = {c["columna"]: c["valor"] for c in e["comprobantes"][0]["fila"]}
    assert fila["Neto Gravado Total"] == 74406.81 and fila["Total IVA"] == 15625.43 and fila["Imp. Total"] == 108011.99

    # Aunque en Xubio esté con el total completo, sigue «A revisar»: la
    # diferencia sin discriminar hay que consultarla.
    xubio.loc[0, "Total"] = 108011.99
    e = {x["tipo"]: x for x in diagnosticar(
        "Compras", arca, xubio, vacio, vacio, [], "Nro. Doc. Emisor", "Denominación Emisor", "Fecha", "Imp. Total",
    )}["total_no_cierra"]
    assert e["gravedad"] == "corregir" and e["a_corregir"] == 1
    assert e["acciones"] == ["Revisar la factura", "Consultar al cliente"]
    assert "1 ya está en Xubio con el total completo" in e["detalle"]
