"""«Pasar a Excel»: formatos de factura en PDF que el escáner tiene que leer enteros
(datos inventados, con la misma forma que los reales)."""

from backend.app.extraccion.a_excel import comprobante_de_texto, corregir_totales

# Factura de ARCA («Comprobantes en línea»): rótulos con «:» y el detalle partido en dos líneas.
ARCA = """ORIGINAL
Razón Social: Emisora Prueba S.A.S.
Condición frente al IVA: IVA Responsable Inscripto
FACTURA
Ingresos Brutos: 285345308
CUIT: 30716583097
Fecha emisión: 07/10/2026
Punto de venta: 00001 Comp. Nro: 00005243
CUIT: 30710809883
Apellido y Nombre/Razón Social: Receptora Prueba S.A.
Servicio Cantidad Precio Unit. % IVA Imp.IVA Total
Fee de acceso mensual - Estudio
Contable 1 69.285,12 21% 14.549,88 83.835,00
Importe Neto: $ 69.285,12
Importe IVA: $ 14.549,88
Importe Otros Tributos: $ 0,00
Importe Total: $ 83.835,00
Esta Agencia no se responsabiliza por los datos ingresados en el detalle de la operación.
CAE N°: 86406611601602
"""

# Factura de Xubio: rótulos en una columna y valores en otra, números con coma de miles.
XUBIO = """A
FACTURA DE VENTA
Número:
Fecha:
CUIT:
A-00004-00000751
20/11/2026
30-71080988-3
Sr. (es): CLIENTE PRUEBA S.A.
CUIT: 30-71753882-6
Razón Social: VENDEDORA PRUEBA S.A.
Cód. Artículo Observaciones Cantidad Precio % Dto. Importe Alíc. IVA Impt. c/IVA
0003 Servicios
profesionales de auditoria.
1.00 2,479,338.84 0.00 2,479,338.84 21.00% 3,000,000.00
Observaciones:
IVA 2.5: 0.00 IVA 5: 0.00 IVA 10.5: 0.00 IVA 21: 520,661.16 IVA 27: 0.00
Bruto: 2,479,338.84 Impuestos:520,661.16 Total: $ 3,000,000.00
CAE: 86473617087645
"""


def test_factura_de_arca():
    e, items, t = comprobante_de_texto(ARCA)
    assert (e["emisor"], e["receptor"]) == ("Emisora Prueba S.A.S.", "Receptora Prueba S.A.")
    assert (e["emisor_cuit"], e["receptor_cuit"], e["numero"]) == ("30716583097", "30710809883", "00001-00005243")
    assert (t["neto_gravado"], t["iva"], t["total"]) == (69285.12, 14549.88, 83835.0)
    assert len(items) == 1
    assert items[0].descripcion == "Fee de acceso mensual - Estudio Contable"
    assert (items[0].cantidad, items[0].alicuota, items[0].importe) == (1, 21, 69285.12)


def test_factura_de_xubio():
    e, items, t = comprobante_de_texto(XUBIO)
    assert (e["emisor"], e["emisor_cuit"]) == ("VENDEDORA PRUEBA S.A.", "30710809883")
    assert (e["receptor"], e["receptor_cuit"]) == ("CLIENTE PRUEBA S.A.", "30717538826")
    assert e["numero"] == "00004-00000751"
    assert (t["neto_gravado"], t["iva"], t["total"]) == (2479338.84, 520661.16, 3000000.0)
    assert len(items) == 1
    i = items[0]
    assert (i.codigo, i.descripcion) == ("0003", "Servicios profesionales de auditoria.")
    assert (i.cantidad, i.precio, i.alicuota, i.importe) == (1, 2479338.84, 21, 2479338.84)


def test_tique_factura_y_sa_mal_leida():
    e, _, _ = comprobante_de_texto("FGC FUELS MARKETING $A\nC.U.I.T. Nro.: 30716583097\nCód. 001 - TIQUE FACTURA \"A\"\n")
    assert e["tipo"].startswith("81") and e["emisor"] == "FGC FUELS MARKETING SA"


def test_no_corrige_un_cero():
    # IVA (9 por 3) y otros tributos (0 por 8) mal leídos; el 0,00 del no gravado no se toca.
    t = {"neto_gravado": 83974.75, "no_gravado": 0.0, "exento": None, "iva": 17694.71, "otros_tributos": 6800.52, "total": 108417.98}
    assert corregir_totales(t) == {"iva": 17634.71, "otros_tributos": 6808.52}


def test_excel_con_el_modelo_de_importacion_de_xubio():
    import io

    from openpyxl import load_workbook

    from backend.app.extraccion.a_excel import Extras, Lectura, excel_importacion, nombre_excel

    e, items, t = comprobante_de_texto(XUBIO)
    e["tipo"] = "1 - Factura A"
    lectura = Lectura(archivo="factura.pdf", tipo="comprobante", encabezado=e, items=items, totales=t)
    assert nombre_excel(lectura, "Ventas", "Vendedora Prueba S.A.") == \
        "Conversor de facturas Ventas con AFIP - 11-26 - Vendedora Prueba S.A.xlsx"
    ws = load_workbook(io.BytesIO(excel_importacion(lectura, "Ventas", Extras(productos=["Honorarios"])))).active
    filas = [list(f) for f in ws.iter_rows(values_only=True)]
    assert filas[0][:4] == ["NUMERODECONTROL", "CLIENTE", "TIPO", "NUMERO"]
    assert filas[1][1:4] == ["CLIENTE PRUEBA S.A.", 1, "A-00004-00000751"]
    assert filas[2][10] == "Honorarios" and filas[2][13:] == [1, 2479338.84, None, 2479338.84, 21]


def test_impuestos_internos_y_percepcion_iibb():
    import io

    from openpyxl import load_workbook

    from backend.app.extraccion.a_excel import Item, Lectura, _totales, excel_importacion, verificar

    t = _totales(["Neto gravado: 1.000,00", "IVA 21%: 210,00", "Impuestos internos: 50,00",
                  "Percepción IIBB Bs. As.: 30,00", "Importe Otros Tributos: 80,00", "Total: 1.290,00"])
    assert (t["impuestos_internos"], t["percepcion_iibb"], t["otros_tributos"]) == (50, 30, 0)
    lectura = Lectura(tipo="comprobante", encabezado={"fecha": "01/10/2026"}, totales=t, items=[
        Item(descripcion="Gaseosa", cantidad=1, precio=1000, alicuota=21, importe=1000, impuestos_internos=50)])
    assert any(v.campo == "total" and v.nivel == "ok" for v in verificar(lectura))
    wb = load_workbook(io.BytesIO(excel_importacion(lectura, "Compras")))
    filas = list(wb["Impuestos y percepciones"].iter_rows(values_only=True))
    assert filas[1:] == [("Impuestos internos", 1, "Gaseosa", 50), ("Percepción IIBB Bs. As.", None, None, 30)]


def test_producto_sugerido_por_item():
    from backend.app.extraccion.a_excel import Item, sugerir_productos

    restaurante = [Item(descripcion=d) for d in ("AGUA NWINERA", "ENTRANA", "SALHON ROSAI", "DOBLE OKLAHOMA")]
    sugerir_productos(restaurante)
    assert {i.producto for i in restaurante} == {"Refrigerio"}
    varios = [Item(descripcion=d) for d in ("Nafta Infinia", "Lavandina 1L", "Fee de acceso mensual", "Tornillos x100")]
    sugerir_productos(varios)
    assert [i.producto for i in varios] == ["Combustible", "Gastos de limpieza", "Servicios", "Mercaderías"]


def test_autocorrige_descripciones_mal_leidas():
    from backend.app.extraccion.a_excel import Item, corregir_descripcion, corregir_descripciones, verificar, Lectura

    assert corregir_descripcion("AGUA NWINERAL SMART VATER 591NL") == "AGUA MINERAL SMART WATER 591ML"
    assert corregir_descripcion("SALHON ROSAI") == "SALMON ROSAI"
    # Lo que no se parece a nada conocido no se toca.
    assert corregir_descripcion("Fee de acceso mensual a ACONT") == "Fee de acceso mensual a ACONT"
    items = [Item(descripcion="AGUA NWINERAL SMART VATER 591NL", importe=1), Item(descripcion="ENTRANA", importe=1)]
    assert corregir_descripciones(items) == 1 and items[0].leido == "AGUA NWINERAL SMART VATER 591NL"
    v = verificar(Lectura(tipo="comprobante", items=items, totales={"total": 2}))
    assert any(x.campo == "descripcion" and x.titulo == "Autocorregí 1 descripción" for x in v)


def test_corrige_el_importe_de_un_item_mal_leido():
    from backend.app.extraccion.a_excel import Item, corregir_importes_items

    importes = [2937.79, 2937.73, 25619.83, 21487.60, 15702.48, 6611.57, 4545.45, 4132.23]
    precios = [3600, 3600, 31000, 26000, 19000, 8000, 5500, 5000]
    items = [Item(cantidad=1, precio=p, alicuota=21, importe=v) for p, v in zip(precios, importes)]
    # El ítem 2 es igual al 1 (misma cantidad y precio): con su importe, el detalle suma el neto.
    assert corregir_importes_items(items, {"neto_gravado": 83974.75, "total": 108417.98}) == 1
    assert (items[1].importe, items[1].importe_leido) == (2937.79, 2937.73)
    # Si ya suma, no se toca nada.
    assert corregir_importes_items(items, {"neto_gravado": 83974.75}) == 0


def test_importe_corregido_es_una_pregunta_obligatoria():
    from backend.app.extraccion.a_excel import Item, Lectura, verificar

    items = [Item(cantidad=1, precio=3600, alicuota=21, importe=2937.79),
             Item(cantidad=1, precio=3600, alicuota=21, importe=2937.79, importe_leido=2937.73)]
    v = [x for x in verificar(Lectura(tipo="comprobante", items=items, totales={"neto_gravado": 5875.58})) if x.obligatoria]
    assert len(v) == 1 and v[0].campo == "item:1:importe" and v[0].nivel == "error"
    assert v[0].opciones == ["2.937,73", "2.937,79"] and v[0].titulo == "Importe del ítem 2: ¿cuál es?"
