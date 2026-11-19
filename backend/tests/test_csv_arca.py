"""CSV de partidas de ARCA no encontradas, en el formato de «Mis Comprobantes»."""

from __future__ import annotations

import math
import zipfile
from pathlib import Path

import pandas as pd

from backend.app.auditor.csv_arca import COLUMNAS_CSV, convertir_auxiliares, fila_csv, escribir_csv, nombre_csv

NAN = math.nan

# Una fila del Excel de ARCA (Emitidos) y la misma tal como sale en el CSV de ARCA.
EMITIDO = {
    "Fecha": "01/11/2026", "Tipo": "1 - Factura A", "Punto de Venta": 4, "Número Desde": 738,
    "Número Hasta": 738, "Cód. Autorización": 86440441843745, "Tipo Doc. Receptor": "CUIT",
    "Nro. Doc. Receptor": 20331920585, "Denominación Receptor": "ROMEO FACUNDO MATIAS",
    "Tipo Cambio": 1, "Moneda": "$", "Neto Grav. IVA 0%": NAN, "IVA 2,5%": NAN,
    "Neto Grav. IVA 2,5%": NAN, "IVA 5%": NAN, "Neto Grav. IVA 5%": NAN, "IVA 10,5%": NAN,
    "Neto Grav. IVA 10,5%": NAN, "IVA 21%": 17181.82, "Neto Grav. IVA 21%": 81818.18,
    "IVA 27%": NAN, "Neto Grav. IVA 27%": NAN, "Neto Gravado Total": 81818.18,
    "Neto No Gravado": 0, "Op. Exentas": 0, "Otros Tributos": 0, "Total IVA": 17181.82,
    "Imp. Total": 99000,
}
LINEA_ARCA = (
    "2026-11-01;1;4;738;738;86440441843745;80;20331920585;ROMEO FACUNDO MATIAS;1,00;$;;;;;;;;"
    "17181,82;81818,18;;;81818,18;0,00;0,00;0,00;17181,82;99000,00"
)


def test_ventas_igual_al_csv_de_arca(tmp_path: Path):
    ruta = escribir_csv(pd.DataFrame([EMITIDO]), "Ventas", tmp_path / "v.csv")
    datos = ruta.read_bytes()
    # Sin BOM, como el de ARCA: es el formato que reconoce Xubio.
    assert not datos.startswith(b"\xef\xbb\xbf") and b"\r\n" not in datos
    lineas = datos.decode("utf-8").splitlines()
    assert lineas[0] == ";".join(f'"{c}"' for c in COLUMNAS_CSV)
    assert lineas[1] == LINEA_ARCA


def test_compras_usa_el_emisor_en_las_columnas_receptor(tmp_path: Path):
    recibido = {k: v for k, v in EMITIDO.items() if "Receptor" not in k}
    recibido.update({
        "Tipo Doc. Emisor": "CUIT", "Nro. Doc. Emisor": 30661792015, "Denominación Emisor": "MEGATOM S. A.",
        "Tipo Doc. Receptor": "CUIT", "Nro. Doc. Receptor": 30716425432,
    })
    ruta = escribir_csv(pd.DataFrame([recibido]), "Compras", tmp_path / "c.csv")
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    assert lineas[0].split(";")[6:9] == ['"Tipo Doc. Receptor"', '"Nro. Doc. Receptor"', '"Denominación Receptor"']
    assert lineas[1].split(";")[6:9] == ["80", "30661792015", "MEGATOM S. A."]


def test_sin_partidas_queda_solo_el_encabezado(tmp_path: Path):
    ruta = escribir_csv(pd.DataFrame(), "Ventas", tmp_path / "v.csv")
    assert len(ruta.read_text(encoding="utf-8").splitlines()) == 1


def test_desde_el_excel_del_control_corta_antes_del_bloque_usd(tmp_path: Path):
    """La hoja «Faltantes» trae las alícuotas en 0 y, debajo, «Facturas Originales en USD»."""
    from openpyxl import Workbook

    from backend.app.auditor.csv_arca import csv_desde_control

    encabezado = [
        "Fecha", "Tipo", "Punto de Venta", "Número Desde", "Número Hasta", "Cód. Autorización",
        "Tipo Doc. Emisor", "Nro. Doc. Emisor", "Denominación Emisor", "Tipo Doc. Receptor",
        "Nro. Doc. Receptor", "Tipo Cambio", "Moneda", "Neto Grav. IVA 0%", "IVA 2,5%",
        "Neto Grav. IVA 2,5%", "IVA 5%", "Neto Grav. IVA 5%", "IVA 10,5%", "Neto Grav. IVA 10,5%",
        "IVA 21%", "Neto Grav. IVA 21%", "IVA 27%", "Neto Grav. IVA 27%", "Neto Gravado Total",
        "Neto No Gravado", "Op. Exentas", "Otros Tributos", "Total IVA", "Imp. Total",
    ]
    fila = [
        "03/11/2026", "1 - Factura A", 14, 1481153, 1481153, 86440892018310, "CUIT", 30519739115,
        "EQUIFAX ARGENTINA S. A.", "CUIT", 30710809883, 1, "$", 0, 0, 0, 0, 0, 0, 0, 3106.1,
        14790.97, 0, 0, 14790.97, 0, 0, 1774.92, 3106.1, 19671.99,
    ]
    wb = Workbook()
    ws = wb.active
    ws.title = "Faltantes en Libro IVA Compras"
    ws.append(encabezado)
    ws.append(fila)
    for _ in range(4):
        ws.append([])
    ws.append(["Facturas Originales en USD"])
    ws.append(encabezado)
    ws.append(fila)
    control = tmp_path / "control.xlsx"
    wb.save(control)

    ruta, n = csv_desde_control(control, "Compras", tmp_path / "c.csv")
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    assert n == 1 and len(lineas) == 2
    assert lineas[1] == (
        "2026-11-03;1;14;1481153;1481153;86440892018310;80;30519739115;EQUIFAX ARGENTINA S. A.;"
        "1,00;$;;;;;;;;3106,10;14790,97;;;14790,97;0,00;0,00;1774,92;3106,10;19671,99"
    )


def _aux(operacion: str, filas: int = 1) -> bytes:
    """Un AUX mínimo con la hoja «Faltantes en Libro IVA <operación>»."""
    import io

    from openpyxl import Workbook

    parte = "Emisor" if operacion == "Compras" else "Receptor"
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen"
    hoja = wb.create_sheet(f"Faltantes en Libro IVA {operacion}")
    hoja.append(["Fecha", "Tipo", "Punto de Venta", "Número Desde", "Número Hasta", "Cód. Autorización",
                 f"Tipo Doc. {parte}", f"Nro. Doc. {parte}", f"Denominación {parte}", "Tipo Cambio", "Moneda",
                 "Neto Gravado Total", "Total IVA", "Imp. Total"])
    for n in range(filas):
        hoja.append(["03/03/2025", "1 - Factura A", 1, 10 + n, 10 + n, 123, "CUIT", 30500000000, "PROVEEDOR", 1, "$", 100, 21, 121])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_convertir_aux_un_zip_por_cliente_y_periodo():
    """La pestaña «Convertir AUX»: agrupa por cliente y período, sin tocar carpetas."""
    import io

    nombre = "AUX - Control de facturas {} con AFIP - 03-25 - {} - 1er control.xlsx"
    resultados = convertir_auxiliares([
        (nombre.format("Ventas", "Acme"), _aux("Ventas", 2)),
        (nombre.format("Compras", "Acme"), _aux("Compras", 1)),
        (nombre.format("Compras", "Beta SA"), _aux("Compras", 0)),
    ])
    por_cliente = {r["cliente"]: r for r in resultados}
    acme = por_cliente["Acme"]
    assert acme["error"] is None and acme["periodo"] == "03-25"
    assert acme["partidas"] == {"Ventas": 2, "Compras": 1}
    # Un .zip por operación, cada uno con un solo CSV en la raíz.
    assert {op: z["nombre"] for op, z in acme["zips"].items()} == {
        "Ventas": "Partidas de ARCA no encontradas en Libro IVA Ventas - Acme - 03-25.zip",
        "Compras": "Partidas de ARCA no encontradas en Libro IVA Compras - Acme - 03-25.zip",
    }
    for op in ("Ventas", "Compras"):
        with zipfile.ZipFile(io.BytesIO(acme["zips"][op]["contenido"])) as z:
            assert z.namelist() == [nombre_csv(op, "Acme", "03-25")]
    with zipfile.ZipFile(io.BytesIO(acme["zips"]["Compras"]["contenido"])) as z:
        compras = z.read(nombre_csv("Compras", "Acme", "03-25")).decode("utf-8").splitlines()
    assert compras[1].startswith("2025-03-03;1;1;10;10;123;80;30500000000;PROVEEDOR;1,00;$;")
    assert por_cliente["Beta SA"]["partidas"] == {"Compras": 0}
    # Sin partidas: no hay archivo, hay aviso.
    assert por_cliente["Beta SA"]["zips"] == {} and por_cliente["Beta SA"]["excel"] is None
    assert por_cliente["Beta SA"]["avisos"] and "Compras: no hay partidas" in por_cliente["Beta SA"]["avisos"][0]


def test_convertir_aux_detecta_todo_del_archivo_y_avisa_errores():
    """Sin nombres de la app: junta Ventas y Compras del mismo cliente, el período
    sale de las fechas y avisa los duplicados o los que no tienen partidas."""
    resultados = convertir_auxiliares([
        ("testeo ventas 1.xlsx", _aux("Ventas", 2)),
        ("testeo compras 1.xlsx", _aux("Compras", 1)),
        ("AUX - Control de facturas Compras con AFIP - 03-25 - Acme - 1er control.xlsx", _aux("Compras")),
        ("AUX - Control de facturas Compras con AFIP - 03-25 - Acme - 2do control.xlsx", _aux("Compras")),
        ("cualquiera.xlsx", b"no es un excel"),
    ])
    por_cliente = {r["cliente"]: r for r in resultados}
    testeo = por_cliente["testeo 1"]
    assert testeo["partidas"] == {"Ventas": 2, "Compras": 1} and testeo["periodo"] == "03-25"
    assert testeo["zips"]["Compras"]["nombre"] == "Partidas de ARCA no encontradas en Libro IVA Compras - testeo 1 - 03-25.zip"
    assert "dos AUX de Compras" in por_cliente["Acme"]["error"] and por_cliente["Acme"]["zips"] == {}
    assert "no tiene una hoja de partidas faltantes" in por_cliente["cualquiera"]["error"]


def test_detecta_hoja_encabezado_y_operacion_sin_nombres_fijos():
    """Hoja con otro nombre, un título arriba del encabezado y columnas con otras
    mayúsculas y sin tildes: la operación sale de las columnas (Emisor = Compras)."""
    import io

    from openpyxl import Workbook

    from backend.app.auditor.csv_arca import detectar_faltantes

    wb = Workbook()
    ws = wb.active
    ws.title = "FALTANTES"
    ws.append(["Partidas de ARCA que no están en el libro"])
    ws.append([])
    ws.append(["FECHA", "tipo", "Punto de venta", "Numero Desde", "Numero Hasta", "Cod. Autorizacion",
               "Tipo Doc. Emisor", "Nro. Doc. Emisor", "Denominacion Emisor", "Tipo Cambio", "Moneda",
               "Neto Gravado Total", "Total IVA", "Imp. Total"])
    ws.append(["03/03/2025", "1 - Factura A", 1, 10, 10, 123, "CUIT", 30500000000, "PROVEEDOR", 1, "$", 100, 21, 121])
    buffer = io.BytesIO()
    wb.save(buffer)
    [(op, tabla)] = detectar_faltantes(io.BytesIO(buffer.getvalue()))
    assert op == "Compras" and len(tabla) == 1
    [linea] = [";".join(fila_csv(f, op)) for _, f in tabla.iterrows()]
    assert linea.startswith("2025-03-03;1;1;10;10;123;80;30500000000;PROVEEDOR;1,00;$;")


def test_endpoint_convertir_aux(cliente_demo):
    import base64
    import io

    nombre = "AUX - Control de facturas Compras con AFIP - 03-25 - Acme - 1er control.xlsx"
    r = cliente_demo.post("/api/convertir-aux", json={"archivos": [{"nombre": nombre, "contenido": base64.b64encode(_aux("Compras", 3)).decode()}]})
    assert r.status_code == 200, r.text
    [item] = r.json()["resultados"]
    assert item["partidas"] == {"Compras": 3} and item["error"] is None
    assert list(item["zips"]) == ["Compras"]
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(item["zips"]["Compras"]["contenido"]))) as z:
        [csv] = z.namelist()
        assert len(z.read(csv).decode("utf-8").splitlines()) == 4

    r = cliente_demo.post("/api/convertir-aux", json={"archivos": [{"nombre": "x.xlsx", "contenido": "%%%"}]})
    assert r.status_code == 422


def test_excel_para_mirar_trae_cae_y_cuit_como_texto():
    """La copia .xlsx: CAE y CUIT como texto (sin 8,64E+13), fecha e importes como número."""
    import io
    from datetime import datetime

    from openpyxl import load_workbook

    nombre = "AUX - Control de facturas Compras con AFIP - 03-25 - Acme - 1er control.xlsx"
    [r] = convertir_auxiliares([(nombre, _aux("Compras", 1))])
    assert r["excel"]["nombre"].endswith("Acme - 03-25.xlsx")
    ws = load_workbook(io.BytesIO(r["excel"]["contenido"]))["Compras"]
    encabezado = [c.value for c in ws[1]]
    fila = {k: c for k, c in zip(encabezado, ws[2])}
    assert fila["Cód. Autorización"].value == "123" and fila["Cód. Autorización"].number_format == "@"
    assert fila["Nro. Doc. Receptor"].value == "30500000000"
    assert fila["Fecha de Emisión"].value == datetime(2025, 3, 3)
    assert fila["Imp. Total"].value == 121.0


def test_operacion_sin_partidas_no_va_al_zip_y_avisa():
    import io

    nombre = "AUX - Control de facturas {} con AFIP - 03-25 - Acme - 1er control.xlsx"
    [r] = convertir_auxiliares([
        (nombre.format("Ventas"), _aux("Ventas", 0)),
        (nombre.format("Compras"), _aux("Compras", 2)),
    ])
    assert r["partidas"] == {"Ventas": 0, "Compras": 2}
    assert len(r["avisos"]) == 1 and r["avisos"][0].startswith("Ventas: no hay partidas")
    assert list(r["zips"]) == ["Compras"]
    with zipfile.ZipFile(io.BytesIO(r["zips"]["Compras"]["contenido"])) as z:
        assert z.namelist() == [nombre_csv("Compras", "Acme", "03-25")]


def test_zip_de_operacion_un_solo_csv_en_la_raiz():
    """Para «Importar desde Mis Comprobantes (ARCA)»: como el .zip de ARCA, un solo CSV."""
    import io

    from backend.app.auditor.csv_arca import zip_de_operacion

    datos = zip_de_operacion(pd.DataFrame([EMITIDO]), "Compras", "Acme", "03-25")
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        assert z.namelist() == [nombre_csv("Compras", "Acme", "03-25")]


def test_filas_para_mostrar_tal_como_estan_en_el_excel():
    """La pantalla muestra la hoja «Faltantes en Libro IVA» completa, con el comprobante armado."""
    import io

    from backend.app.auditor.csv_arca import filas_para_mostrar

    filas = filas_para_mostrar(io.BytesIO(_aux("Compras", filas=3)))
    assert list(filas) == ["Compras"] and len(filas["Compras"]) == 3
    assert filas["Compras"][0] == {
        "fecha": "03/03/2025", "tipo": "1 - Factura A", "comprobante": "A-00001-00000010",
        "cuit": "30500000000", "razon": "PROVEEDOR", "total": 121.0,
    }


def test_libro_sin_arca_lee_el_bloque_al_pie_y_suma_el_total():
    """«Partidas del Libro IVA … no encontradas en ARCA»: al pie de la primera hoja, con el total en =SUM(…)."""
    import io

    from openpyxl import Workbook

    from backend.app.auditor.csv_arca import libro_sin_arca

    wb = Workbook()
    ws = wb.active
    ws.append(["Clave_Conciliacion", "Fecha", "Tipo", "Nro. Doc. Emisor", "Denominación Emisor", "Neto", "IVA", "Imp. Total"])
    ws.append(["A-00001-00000001", "01/04/2026", "1 - Factura A", 30500000000, "CRUZADO", 100, 21, 121])
    ws.append([])
    ws.append(["Partidas del Libro IVA Compras no encontradas en ARCA"])
    ws.append(["=A1", "=B1", "=C1", "=D1", "=E1", "=F1", "=G1", "=H1"])
    ws.append(["A-00411-00306778", "01/04/2026", "Tique", "30-71580481-2", "CORREDORES VIALES", 1239.68, 260.32, "=SUM(F6:G6)"])
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    assert libro_sin_arca(buffer, "Compras") == [{
        "fecha": "01/04/2026", "tipo": "Tique", "comprobante": "A-00411-00306778",
        "cuit": "30715804812", "razon": "CORREDORES VIALES", "total": 1500.0,
    }]


def test_convertir_aux_sin_las_filas_destildadas():
    """Lo que se destildó al revisar el control («CUIT|comprobante») no va al .zip."""
    from backend.app.auditor.csv_arca import convertir_auxiliares

    nombre = "AUX - Control de facturas Compras con AFIP - 03-25 - Acme - 1er control.xlsx"
    todo = convertir_auxiliares([(nombre, _aux("Compras", filas=3))])
    assert todo[0]["partidas"] == {"Compras": 3}
    menos = convertir_auxiliares([(nombre, _aux("Compras", filas=3))], {nombre: {"30500000000|A-00001-00000011"}})
    assert menos[0]["partidas"] == {"Compras": 2}
    # La pantalla muestra los mismos comprobantes que van al .zip.
    filas = menos[0]["filas"]["Compras"]
    assert len(filas) == 2
    assert "A-00001-00000011" not in [f["comprobante"] for f in filas]
    nada = convertir_auxiliares(
        [(nombre, _aux("Compras", filas=1))], {nombre: {"30500000000|A-00001-00000010"}},
    )
    assert nada[0]["zips"] == {} and nada[0]["avisos"]
