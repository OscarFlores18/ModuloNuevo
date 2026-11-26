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


def _hojas_xls(contenido: bytes) -> dict[str, list[tuple]]:
    """El .xls como lo ve openpyxl: filas con None en lo vacío, fechas y enteros."""
    import xlrd

    libro = xlrd.open_workbook(file_contents=contenido)
    hojas = {}
    for h in libro.sheets():
        filas = []
        for i in range(h.nrows):
            fila = []
            for j in range(h.ncols):
                c = h.cell(i, j)
                if c.ctype == xlrd.XL_CELL_EMPTY:
                    fila.append(None)
                elif c.ctype == xlrd.XL_CELL_DATE:
                    fila.append(xlrd.xldate_as_datetime(c.value, libro.datemode))
                elif c.ctype == xlrd.XL_CELL_NUMBER and float(c.value).is_integer():
                    fila.append(int(c.value))
                else:
                    fila.append(c.value)
            filas.append(tuple(fila))
        hojas[h.name] = filas
    return hojas


def test_excel_con_el_modelo_de_importacion_de_xubio():
    import io

    from openpyxl import load_workbook

    from backend.app.extraccion.a_excel import Extras, Lectura, excel_importacion, nombre_excel

    e, items, t = comprobante_de_texto(XUBIO)
    e["tipo"] = "1 - Factura A"
    lectura = Lectura(archivo="factura.pdf", tipo="comprobante", encabezado=e, items=items, totales=t)
    assert nombre_excel(lectura, "Ventas", "Vendedora Prueba S.A.") == \
        "Conversor de facturas Ventas con AFIP - 11-26 - Vendedora Prueba S.A.xls"
    filas = [list(f) for f in _hojas_xls(excel_importacion(lectura, "Ventas", Extras(productos=["Honorarios"])))["Sheet0"]]
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
    # Compras: como filas del modelo de Xubio. Ventas: en la hoja aparte.
    filas = _hojas_xls(excel_importacion(lectura, "Compras"))["Hoja1"]
    assert [(f[10], f[15]) for f in filas[3:]] == [("Impuestos Internos", 50), ("Percepción IIBB Buenos Aires", 30)]
    filas = _hojas_xls(excel_importacion(lectura, "Ventas"))["Impuestos y percepciones"]
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


def test_importe_corregido_solo_avisa():
    from backend.app.extraccion.a_excel import Item, Lectura, verificar

    items = [Item(cantidad=1, precio=3600, alicuota=21, importe=2937.79),
             Item(cantidad=1, precio=3600, alicuota=21, importe=2937.79, importe_leido=2937.73)]
    v = [x for x in verificar(Lectura(tipo="comprobante", items=items, totales={"neto_gravado": 5875.58}))
         if x.titulo.startswith("Autocorregí el importe")]
    assert len(v) == 1 and v[0].nivel == "aviso" and not v[0].opciones
    assert v[0].datos == ["Leído 2.937,73", "Ahora 2.937,79"]


def _factura_referencia():
    """Tique Factura A 00103-00000524 de FOC Fuels Marketing SA (25/10/2026)."""
    from backend.app.extraccion.a_excel import Item, Lectura

    filas = [("AGUA MINERAL SMART WATER 591ML", 1, 2937.79), ("AGUA SMART WATER CON GAS 591ML", 1, 2937.79),
             ("ENTRAÑA", 1, 21900.83), ("SALMON ROSADO", 1, 23140.50), ("DOBLE OKLAHOMA FRIED ONION", 2, 22314.05),
             ("PORCION DE PAPAS FRITAS", 1, 4545.45), ("PURE DE BONIATO", 1, 3719.01), ("COLESLAW DE REMOLACHA", 1, 2479.33)]
    items = [Item(descripcion=d, cantidad=c, precio=round(i / c, 2), alicuota=21, importe=i) for d, c, i in filas]
    return Lectura(
        archivo="tique.jpg", tipo="comprobante",
        encabezado={"emisor": "FGC FUELS MARKETING SA", "tipo": "081 - Tique Factura A",
                    "numero": "00103-00000524", "fecha": "25/10/2026"},
        items=items,
        totales={"neto_gravado": 83974.75, "iva": 17634.71, "impuestos_internos": 90.54,
                 "percepcion_iibb": 6717.98, "otros_tributos": 0.0, "total": 108417.98},
    )


def test_refrigerio_agrupa_la_factura_de_referencia():
    import io

    from openpyxl import load_workbook

    from backend.app.extraccion.a_excel import COLUMNAS_COMPRAS, Extras, excel_importacion, verificar

    l = _factura_referencia()
    assert round(sum(i.importe for i in l.items), 2) == 83974.75
    contenido = excel_importacion(l, "Compras", Extras(agrupar="Refrigerio"))
    assert contenido[:8] == bytes.fromhex("D0CF11E0A1B11AE1")  # .xls (Excel 97-2003), como el modelo
    hojas = _hojas_xls(contenido)
    assert list(hojas) == ["Hoja1"]
    filas = [dict(zip(COLUMNAS_COMPRAS, f)) for f in hojas["Hoja1"]]
    # Las columnas, tal cual el modelo de compras de Xubio.
    assert list(filas[0]) == list(filas[0].values()) == COLUMNAS_COMPRAS
    cab = filas[1]
    # Tique Factura A: tipo 5 en compras.
    assert (cab["PROVEEDOR"], cab["TIPO"], cab["NUMERO"], cab["MONEDA"]) == ("FGC FUELS MARKETING SA", 5, "A-00103-00000524", "pesos argentinos")
    assert cab["FECHA"].strftime("%d/%m/%Y") == cab["FECHAFISCAL"].strftime("%d/%m/%Y") == "25/10/2026"
    assert all(f["NUMERODECONTROL"] == 1 for f in filas[1:])
    # Una sola fila de detalle (todo al 21 %) y una por impuesto, sin tocar los importes.
    assert len(filas) == 5
    d = filas[2]
    assert (d["PRODUCTOSERVICIOPERCEPCION"], d["CANTIDAD"], d["PRECIO"], d["IMPORTE"], d["IVA"]) == ("Refrigerio", 1, 83974.75, 83974.75, 21)
    for x in ("1x AGUA MINERAL SMART WATER 591ML", "2x DOBLE OKLAHOMA FRIED ONION", "1x COLESLAW DE REMOLACHA", "1x ENTRAÑA"):
        assert x in d["PRODUCTOOBSERVACION"]
    assert d["PRODUCTOOBSERVACION"].count("x ") == 8
    assert [(f["PRODUCTOSERVICIOPERCEPCION"], f["CANTIDAD"], f["IMPORTE"]) for f in filas[3:]] == [
        ("Impuestos Internos", 1, 90.54), ("Percepción IIBB Buenos Aires", 1, 6717.98)]
    # Neto + IVA + impuestos = total; el IVA que calcula Xubio difiere en un centavo: se avisa.
    assert round(83974.75 + 17634.71 + 90.54 + 6717.98, 2) == l.totales["total"]
    v = verificar(l)
    assert not [x for x in v if x.nivel == "error"], [x.titulo for x in v if x.nivel == "error"]
    red = [x for x in v if x.titulo == "Redondeo de IVA"]
    assert len(red) == 1 and red[0].datos[-1] == "Dif. 0,01"


def test_sin_agrupar_sigue_una_fila_por_item():
    from backend.app.extraccion.a_excel import filas_compras

    filas = filas_compras(_factura_referencia())
    # Cabecera + 8 ítems + impuestos internos + percepción.
    assert len(filas) == 11
    # PRECIO x CANTIDAD = IMPORTE (el neto, no el precio con IVA de la foto).
    for f in filas[1:9]:
        assert round(f[13] * f[14], 2) == f[15], f
    # 22.314,05 / 2 no da justo: va 1 x 22.314,05 y la cantidad en la observación.
    assert filas[5][12:16] == ["2x DOBLE OKLAHOMA FRIED ONION", 1, 22314.05, 22314.05]


def test_precio_neto_que_no_da_justo_va_como_uno():
    from backend.app.extraccion.a_excel import _precio_neto

    assert _precio_neto(1, 2937.79, "AGUA") == (1, 2937.79, "AGUA")
    assert _precio_neto(2, 100.0, "X") == (2, 50.0, "X")
    assert _precio_neto(3, 100.0, "X") == (1, 100.0, "3x X")


def test_percepcion_iibb_leida_como_otros_tributos():
    from backend.app.extraccion.a_excel import completar_tributos, verificar

    l = _factura_referencia()
    l.texto = "Perc. IIBB BS AS 6.709,98\nOtros tributos 6.808,52"
    l.totales.update(percepcion_iibb=None, otros_tributos=6709.98)
    completar_tributos(l)
    assert (l.totales["percepcion_iibb"], l.totales["otros_tributos"]) == (6717.98, 0)
    v = [x for x in verificar(l) if x.campo == "percepcion_iibb"]
    assert len(v) == 1 and v[0].titulo == "Autocorregí la percepción iibb bs. as."
    assert v[0].datos == ["Leído Otros tributos 6.709,98", "Ahora 6.717,98"]
    # Sin mención de Ingresos Brutos: sigue como otros tributos, con lo que falta para el total.
    l = _factura_referencia()
    l.totales.update(percepcion_iibb=None, otros_tributos=6709.98)
    completar_tributos(l)
    assert (l.totales["percepcion_iibb"], l.totales["otros_tributos"]) == (None, 6717.98)


def test_emisor_con_simbolo_se_autocorrige():
    from backend.app.extraccion.a_excel import Lectura, limpiar_nombres, verificar

    l = Lectura(tipo="comprobante", encabezado={"emisor": "FOC FUELS MARKETING $"},
                dudas={"emisor": ["FOC FUELS MARKETING $", "FOC FUELS MARKETING SA"]})
    limpiar_nombres(l)
    assert l.encabezado["emisor"] == "FOC FUELS MARKETING SA" and "emisor" not in l.dudas
    assert l.corregidos == {"emisor": "FOC FUELS MARKETING $"}
    v = [x for x in verificar(l) if x.campo == "emisor"]
    assert len(v) == 1 and v[0].nivel == "aviso" and v[0].titulo == "Autocorregí el emisor"
    # Nombres de verdad distintos: se sigue preguntando.
    l = Lectura(tipo="comprobante", encabezado={"emisor": "ACME SA"}, dudas={"emisor": ["ACME SA", "PEREZ JUAN"]})
    limpiar_nombres(l)
    assert "emisor" in l.dudas


def test_duda_de_neto_se_elige_con_usar():
    from backend.app.extraccion.a_excel import Lectura, verificar

    l = Lectura(tipo="comprobante", totales={"neto_gravado": 3974.75, "iva": 17634.70, "total": 101609.45},
                dudas={"neto_gravado": ["3.974,75", "83.974,75", "89.974,75"]})
    v = [x for x in verificar(l) if x.campo == "neto_gravado" and x.opciones]
    assert len(v) == 1 and v[0].nivel == "error" and v[0].titulo == "Neto gravado mal leído"
    assert v[0].opciones[0] == "83.974,75"
