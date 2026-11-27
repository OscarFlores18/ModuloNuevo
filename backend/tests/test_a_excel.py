"""«Pasar a Excel»: leer, verificar y armar el .xlsx.

`datos/tique_ocr.txt` es el OCR real (Tesseract, 4000 px, psm 6) de la foto de un
tique factura A de FGC con los errores típicos: 8 leído como 6 en el neto y el
total, y un dígito mal en el CUIT del emisor. No hace falta Tesseract para correr
estas pruebas.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from openpyxl import load_workbook

from backend.app.extraccion import a_excel
from backend.app.extraccion.a_excel import Item, Lectura

TIQUE = (Path(__file__).parent / "datos" / "tique_ocr.txt").read_text(encoding="utf-8")


def _lectura_del_tique() -> Lectura:
    lectura = a_excel._desde_texto(Lectura(archivo="tique.jpg", metodo="ocr"), [TIQUE])
    lectura.verificaciones = a_excel.verificar(lectura)
    return lectura


def _sugerencia(lectura: Lectura, campo: str):
    return next((v.sugerencia for v in lectura.verificaciones if v.campo == campo and v.sugerencia is not None), None)


def test_el_tique_se_lee_como_comprobante():
    l = _lectura_del_tique()
    assert l.tipo == "comprobante"
    assert l.encabezado["tipo"] == "81 - Tique Factura A"
    assert l.encabezado["numero"] == "00103-00000524"
    assert l.encabezado["fecha"] == "25/10/2026"
    assert l.encabezado["receptor_cuit"] == "30709864013"
    assert l.encabezado["receptor"] == "LOGYS SOLUTIONS SRL"
    assert len(l.items) == 8
    assert l.items[2].descripcion == "ENTRANA" and l.items[2].importe == 25619.83
    assert l.totales["iva"] == 17634.71


def test_la_verificacion_detecta_y_corrige_lo_mal_leido():
    l = _lectura_del_tique()
    # El OCR leyó 63.974,75 y 106.417,98: las cuentas dan el 8 que falta.
    assert _sugerencia(l, "neto_gravado") == 83974.75
    assert _sugerencia(l, "total") == 108417.98
    # El CUIT del emisor no pasa el dígito verificador; el de Ingresos Brutos ayuda a corregirlo.
    assert _sugerencia(l, "emisor_cuit") == "33714877769"
    assert l.verificaciones[0].nivel == "error"


def test_con_lo_corregido_la_verificacion_da_bien():
    l = _lectura_del_tique()
    l.totales.update(neto_gravado=83974.75, total=108417.98)
    l.encabezado["emisor_cuit"] = "33714877769"
    v = {(x.campo, x.nivel) for x in a_excel.verificar(l)}
    assert ("total", "ok") in v and ("iva", "ok") in v and ("emisor_cuit", "ok") in v
    assert ("neto_gravado", "error") not in v


def test_items_iguales_con_otro_importe_se_avisan():
    items = [Item(descripcion="AGUA", cantidad=1, precio=3600, alicuota=21, importe=2937.79),
             Item(descripcion="AGUA C/GAS", cantidad=1, precio=3600, alicuota=21, importe=2937.73)]
    v = a_excel._verificar_items(items, {"neto_gravado": 5875.58})
    assert any("1 y 2" in x.mensaje for x in v)


def test_corregir_totales_solo_si_hay_una_salida():
    assert a_excel.corregir_totales({"neto_gravado": 1000.0, "iva": 210.0, "total": 1210.0}) is None  # ya cierra: nada que corregir
    assert a_excel.corregir_totales({"neto_gravado": 1000.0, "iva": 210.0, "total": 1216.0}) == {"total": 1210.0}


def test_cuit_parecido():
    assert "33714877769" in a_excel.cuits_parecidos("33714077769")
    assert a_excel._mejor_parecido("33714077769", "Ing. Brutos: 33714877760") == "33714877769"


def test_csv_a_excel_con_numeros_y_texto():
    csv = "Fecha;CUIT;Importe\n01/11/2026;20222222223;1.210,50\n02/11/2026;0001;99\n".encode("cp1252")
    l = a_excel.leer("ventas.csv", csv)
    assert l.tipo == "tabla"
    filas = l.hojas[0].filas
    assert filas[1] == ["01/11/2026", 20222222223, 1210.5]
    assert filas[2][1] == "0001"  # ceros a la izquierda quedan como texto
    ws = load_workbook(io.BytesIO(a_excel.generar(l)))["Hoja1"]
    assert ws["C2"].value == 1210.5
    assert "Verificación" in load_workbook(io.BytesIO(a_excel.generar(l))).sheetnames


def test_excel_del_comprobante_tiene_detalle_con_formulas():
    l = _lectura_del_tique()
    wb = load_workbook(io.BytesIO(a_excel.generar(l)))
    assert wb.sheetnames == ["Comprobante", "Detalle", "Verificación", "Texto leído"]
    detalle = wb["Detalle"]
    assert detalle["B4"].value == "ENTRANA"
    assert str(detalle.cell(10, 6).value).startswith("=SUM(F2:F9)")


def test_endpoints(cliente_demo):
    csv = base64.b64encode(b"a,b\n1,2\n").decode()
    r = cliente_demo.post("/api/a-excel/leer", json={"archivos": [
        {"nombre": "datos.csv", "contenido": csv},
        {"nombre": "carta.docx", "contenido": csv},
    ]})
    assert r.status_code == 200, r.text
    lecturas = r.json()["lecturas"]
    assert lecturas[0]["tipo"] == "tabla" and lecturas[0]["verificaciones"]
    assert "no soportado" in lecturas[1]["error"]

    r = cliente_demo.post("/api/a-excel/verificar", json={"lectura": lecturas[0]})
    assert r.status_code == 200 and r.json()["verificaciones"]

    r = cliente_demo.post("/api/a-excel/generar", json={"lectura": lecturas[0]})
    assert r.status_code == 200
    assert r.json()["nombre"] == "datos.xlsx"
    load_workbook(io.BytesIO(base64.b64decode(r.json()["contenido"])))


def test_cuit_con_varios_ceros_leidos_como_ocho():
    assert a_excel.cuits_cambiando_0_y_8("30789864813") == ["30709864013"]
    assert "30709864013" in a_excel.cuits_parecidos("30789864813")
    assert a_excel._mejor_parecido("30789864813", "") == "30709864013"


def test_desglose_de_iva_mal_y_sin_los_items():
    l = Lectura(
        tipo="comprobante",
        totales={"neto_gravado": 1100.0, "iva": None, "total": 1320.5},
        iva_alicuotas=[
            a_excel.AlicuotaIva(alicuota=21, base=1000, importe=201),  # 21% de 1000 es 210
            a_excel.AlicuotaIva(alicuota=10.5, base=100, importe=10.5),
        ],
        items=[Item(descripcion="A", alicuota=21, importe=1000), Item(descripcion="B", alicuota=27, importe=100)],
    )
    v = a_excel.verificar(l)
    fila = next(x for x in v if x.campo == "iva_0" and x.nivel == "error")
    assert fila.sugerencia == 210.0
    assert any(x.campo == "iva_1" and "ítems" in x.mensaje for x in v)  # no hay ítems al 10,5%
    assert any(x.campo == "iva" and "27%" in x.mensaje for x in v)  # ítems al 27% sin fila


def test_sin_desglose_se_arma_con_el_iva_leido():
    l = _lectura_del_tique()
    a_excel.completar_desglose(l)
    assert len(l.iva_alicuotas) == 1 and l.iva_alicuotas[0].importe == 17634.71
