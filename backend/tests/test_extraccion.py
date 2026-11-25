"""Comprobantes desde planillas, CSV, PDF e imágenes, hasta el .zip de Xubio."""

from __future__ import annotations

import base64
import io
import json
import zipfile

import pandas as pd
import pytest

from backend.app.auditor.csv_arca import COLUMNAS_CSV
from backend.app.extraccion import documentos, ocr, servicio
from backend.app.extraccion.modelo import Comprobante, Parte, completar, fecha, validar
from backend.app.extraccion.planillas import campo, extraer_planilla
from backend.app.extraccion.qr_afip import comprobante_del_qr, datos_del_qr, leer_qrs
from backend.app.extraccion.texto_factura import comprobante_del_texto
from backend.app.services.importacion_xubio import csv_de_operacion

ENCABEZADO = ";".join(f'"{c}"' for c in COLUMNAS_CSV)
# Una fila tal cual la baja ARCA (Emitidos): así tiene que volver a salir.
LINEA_ARCA = (
    "2026-11-01;1;4;738;738;86440441843745;80;20331920585;ROMEO FACUNDO MATIAS;1,00;$;;;;;;;;"
    "17181,82;81818,18;;;81818,18;0,00;0,00;0,00;17181,82;99000,00"
)

QR = {
    "ver": 1, "fecha": "2026-11-03", "cuit": 30661792015, "ptoVta": 2, "tipoCmp": 1, "nroCmp": 1234,
    "importe": 1210, "moneda": "PES", "ctz": 1, "tipoDocRec": 80, "nroDocRec": 20222222223,
    "tipoCodAut": "E", "codAut": 74123456789012,
}
URL_QR = "https://www.afip.gob.ar/fe/qr/?p=" + base64.b64encode(json.dumps(QR).encode()).decode()

TEXTO_FACTURA = """ORIGINAL
A
COD. 01
FACTURA
Razón Social: MEGATOM S. A.
Punto de Venta: 00002 Comp. Nro: 00001234
Fecha de Emisión: 03/11/2026
CUIT: 30661792015
CUIT: 20222222223   Apellido y Nombre / Razón Social: ACME SRL
Importe Neto Gravado: $ 1.000,00
IVA 27%: $ 0,00
IVA 21%: $ 210,00
Importe Otros Tributos: $ 0,00
Importe Total: $ 1.210,00
CAE N°: 74123456789012
Fecha de Vto. de CAE: 13/11/2026
"""


def _pdf(lineas: list[str]) -> bytes:
    """Un PDF digital mínimo con esas líneas de texto (Helvetica)."""
    def esc(t: str) -> str:
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    flujo = "BT /F1 10 Tf 40 800 Td 14 TL " + " ".join(f"({esc(l)}) Tj T*" for l in lineas) + " ET"
    flujo_b = flujo.encode("latin-1")
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(flujo_b)).encode() + b" >>\nstream\n" + flujo_b + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    salida = bytearray(b"%PDF-1.4\n")
    posiciones = []
    for i, obj in enumerate(objetos, start=1):
        posiciones.append(len(salida))
        salida += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(salida)
    salida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
    salida += b"".join(f"{p:010d} 00000 n \n".encode() for p in posiciones)
    salida += f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(salida)


def _png_qr(texto: str) -> bytes:
    import zxingcpp
    from PIL import Image

    img = Image.fromarray(zxingcpp.create_barcode(texto, zxingcpp.BarcodeFormat.QRCode).to_image(scale=6, add_quiet_zones=True))
    lienzo = Image.new("RGB", (img.width + 400, img.height + 300), "white")
    lienzo.paste(img.convert("RGB"), (200, 150))
    buffer = io.BytesIO()
    lienzo.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def sin_tesseract(monkeypatch):
    def falla(_imagen):
        raise ocr.OCRNoDisponible("Tesseract no está instalado: de las imágenes solo se lee el QR de ARCA.")

    monkeypatch.setattr(documentos, "texto_de_imagen", falla)


# --- Normalización ---------------------------------------------------------------


def test_encabezados_por_sinonimos():
    assert campo("Pto. Vta.") == "punto_venta"
    assert campo("Fecha de Emisión") == "fecha"
    assert campo("Fecha Vto. CAE") is None
    assert campo("Neto Grav. IVA 10.5%") == "Imp. Neto Gravado IVA 10,5%"
    assert campo("IVA 2,5%") == "IVA 2,5%"
    assert campo("Imp. Neto No Gravado") == "Imp. Neto No Gravado"
    assert campo("Denominación Emisor") == "emisor_nombre"
    assert campo("Importe Total") == "Imp. Total"


def test_fechas():
    assert fecha("3/11/26") == "2026-11-03"
    assert fecha("2026-11-03 00:00:00") == "2026-11-03"
    assert fecha("20261103") == "2026-11-03"
    assert fecha("31/31/2026") == ""


def test_completar_deduce_neto_e_iva_y_validar_avisa():
    c = Comprobante(fecha="2026-11-03", tipo=1, punto_venta=2, numero=5, operacion="Compras", cliente="Acme",
                    emisor=Parte(doc_nro="30-66179201-5"), importes={"IVA 21%": 210, "Imp. Total": 1210})
    completar(c)
    assert c.importes["Imp. Neto Gravado IVA 21%"] == 1000
    assert c.importes["Imp. Neto Gravado Total"] == 1000 and c.importes["Total IVA"] == 210
    assert c.emisor.doc_nro == "30661792015" and c.emisor.doc_tipo == 80
    avisos = validar(c)
    assert any("Sin CAE" in a for a in avisos)
    assert not any("suman" in a for a in avisos)
    c.importes["Imp. Total"] = 1500
    assert any("no suman" in a for a in validar(c))
    c.emisor.doc_nro = "30661792016"
    assert any("no es válido" in a for a in validar(c))


# --- Planillas -------------------------------------------------------------------


def test_csv_de_mis_comprobantes_vuelve_igual_al_de_arca():
    contenido = (ENCABEZADO + "\n" + LINEA_ARCA + "\n").encode("utf-8")
    comps = extraer_planilla("Mis Comprobantes Emitidos - Acme.csv", contenido)
    assert len(comps) == 1
    c = comps[0]
    assert (c.operacion, c.tipo, c.punto_venta, c.numero, c.cae) == ("Ventas", 1, 4, 738, "86440441843745")
    assert c.receptor.doc_nro == "20331920585" and c.importes["Imp. Total"] == 99000
    c.cliente = "Acme"
    [r] = servicio.preparar([c])
    assert r["error"] is None and r["partidas"] == {"Ventas": 1} and r["periodo"] == "11-26"
    nombre, csv = csv_de_operacion(r["zips"]["Ventas"]["contenido"], "Ventas")
    assert csv.decode("utf-8").splitlines() == [ENCABEZADO, LINEA_ARCA]


def test_xlsx_propio_de_compras_con_comprobante_completo():
    df = pd.DataFrame([
        ["Planilla de compras de octubre", None, None, None, None, None, None],
        [None] * 7,
        ["Fecha", "Comprobante", "Proveedor", "CUIT", "Neto", "IVA 21%", "Total"],
        ["15/10/2026", "A-0003-00000077", "MEGATOM S. A.", "30-66179201-5", "1.000,00", "210,00", "1.210,00"],
        ["16/10/2026", "B 0001-00000010", "KIOSCO", "20331920585", None, None, 500],
        ["Total", None, None, None, None, None, 1710],
    ])
    buffer = io.BytesIO()
    df.to_excel(buffer, header=False, index=False)
    comps = extraer_planilla("Compras Acme octubre.xlsx", buffer.getvalue())
    assert len(comps) == 2
    a, b = comps
    assert a.operacion == "Compras" and (a.tipo, a.punto_venta, a.numero) == (1, 3, 77)
    assert a.emisor.doc_nro == "30661792015" and a.emisor.nombre == "MEGATOM S. A."
    assert a.importes["Imp. Neto Gravado IVA 21%"] == 1000 and a.importes["Imp. Total"] == 1210
    assert (b.tipo, b.numero, b.importes["Imp. Total"]) == (6, 10, 500)


def test_csv_con_coma_y_latin1():
    texto = "Fecha,Tipo,Punto de Venta,Número,Razón Social,CUIT,Importe Total\n03/11/2026,Factura C,1,9,ÑANDÚ SRL,30716425432,\"1.500,00\"\n"
    comps = extraer_planilla("ventas.csv", texto.encode("cp1252"))
    assert len(comps) == 1
    c = comps[0]
    assert c.operacion == "Ventas" and c.tipo == 11 and c.receptor.nombre == "ÑANDÚ SRL" and c.importes["Imp. Total"] == 1500


# --- QR y texto ------------------------------------------------------------------


def test_qr_de_arca():
    datos = datos_del_qr(URL_QR)
    assert datos["nroCmp"] == 1234
    assert datos_del_qr("https://otro.com/?p=abc") is None
    c = comprobante_del_qr(datos, "f.jpg")
    assert (c.fecha, c.tipo, c.punto_venta, c.numero, c.cae) == ("2026-11-03", 1, 2, 1234, "74123456789012")
    assert c.emisor.doc_nro == "30661792015" and c.receptor.doc_nro == "20222222223"
    assert c.importes == {"Imp. Total": 1210.0}


def test_texto_de_factura_de_arca():
    c = comprobante_del_texto(TEXTO_FACTURA, "f.pdf", 1, "pdf")
    assert (c.tipo, c.punto_venta, c.numero, c.fecha, c.cae) == (1, 2, 1234, "2026-11-03", "74123456789012")
    assert c.emisor.doc_nro == "30661792015" and c.emisor.nombre == "MEGATOM S. A."
    assert c.receptor.doc_nro == "20222222223" and c.receptor.nombre == "ACME SRL"
    assert c.importes["IVA 21%"] == 210 and c.importes["Imp. Total"] == 1210
    assert c.importes["Imp. Neto Gravado Total"] == 1000
    assert comprobante_del_texto("hola", "x", None, "ocr") is None


# --- PDF e imágenes ----------------------------------------------------------------


def test_pdf_digital():
    comps, avisos = documentos.extraer_pdf("factura.pdf", _pdf(TEXTO_FACTURA.splitlines()))
    assert avisos == []
    [c] = comps
    assert c.metodo == "pdf" and (c.punto_venta, c.numero) == (2, 1234)
    assert c.importes["Imp. Total"] == 1210


def test_pdf_escaneado_va_por_ocr(monkeypatch):
    monkeypatch.setattr(documentos, "texto_de_imagen", lambda _img: TEXTO_FACTURA)
    comps, _ = documentos.extraer_pdf("escaneo.pdf", _pdf([]))
    [c] = comps
    assert c.metodo == "ocr" and c.numero == 1234 and c.receptor.nombre == "ACME SRL"


def test_foto_con_qr_sin_tesseract(sin_tesseract):
    png = _png_qr(URL_QR)
    from PIL import Image

    assert leer_qrs(Image.open(io.BytesIO(png)))
    comps, avisos = documentos.extraer_imagen("foto.png", png)
    [c] = comps
    assert c.metodo == "qr" and c.numero == 1234 and c.importes["Imp. Total"] == 1210
    assert any("Tesseract" in a for a in avisos)


def test_foto_con_qr_y_ocr_combina(monkeypatch):
    # El OCR leyó mal el número: manda el QR, y del texto se toman razón social e IVA.
    monkeypatch.setattr(documentos, "texto_de_imagen", lambda _img: TEXTO_FACTURA.replace("00001234", "00001284"))
    comps, _ = documentos.extraer_imagen("foto.png", _png_qr(URL_QR))
    [c] = comps
    assert c.numero == 1234 and c.emisor.nombre == "MEGATOM S. A." and c.importes["IVA 21%"] == 210


def test_imagen_invalida():
    with pytest.raises(ValueError):
        documentos.extraer_imagen("x.png", b"no es imagen")


# --- Endpoints -----------------------------------------------------------------------


def _b64(datos: bytes) -> str:
    return base64.b64encode(datos).decode()


def test_endpoints_extraer_y_preparar(cliente_demo, sin_tesseract):
    # Acme es el cliente con CUIT 20222222223: la factura del QR es una compra suya.
    r = cliente_demo.post("/api/comprobantes/extraer", json={"archivos": [
        {"nombre": "foto.png", "contenido": _b64(_png_qr(URL_QR))},
        {"nombre": "nada.pdf", "contenido": _b64(_pdf(["hola"]))},
        {"nombre": "roto.pdf", "contenido": _b64(b"%PDF roto")},
    ]})
    assert r.status_code == 200, r.text
    datos = r.json()
    tipos = {a["nombre"]: a["tipo"] for a in datos["archivos"]}
    assert tipos == {"foto.png": "comprobantes", "nada.pdf": "comprobantes", "roto.pdf": "error"}
    foto = next(c for c in datos["comprobantes"] if c["archivo"] == "foto.png")
    assert foto["operacion"] == "Compras" and foto["cliente"] == "Acme"
    vacio = next(c for c in datos["comprobantes"] if c["archivo"] == "nada.pdf")
    assert vacio["metodo"] == "manual" and vacio["avisos"]

    # La persona completa el proveedor y saca el vacío.
    foto["emisor"]["nombre"] = "MEGATOM S. A."
    vacio["incluir"] = False
    r = cliente_demo.post("/api/comprobantes/preparar", json={"comprobantes": [foto, vacio]})
    assert r.status_code == 200, r.text
    [res] = r.json()["resultados"]
    assert res["cliente"] == "Acme" and res["error"] is None and res["partidas"] == {"Compras": 1}
    assert res["filas"]["Compras"][0]["comprobante"] == "A-00002-00001234"
    nombre, csv = csv_de_operacion(base64.b64decode(res["zips"]["Compras"]["contenido"]), "Compras")
    linea = csv.decode().splitlines()[1].split(";")
    assert linea[:9] == ["2026-11-03", "1", "2", "1234", "1234", "74123456789012", "80", "30661792015", "MEGATOM S. A."]
    assert linea[-1] == "1210,00"


def test_endpoint_extraer_marca_los_aux_y_rechaza_formatos(cliente_demo, tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Faltantes en Libro IVA Compras"
    ws.append(["Fecha", "Tipo", "Punto de Venta", "Número Desde", "Imp. Total"])
    ws.append(["01/11/2026", "1 - Factura A", 1, 2, 100])
    buffer = io.BytesIO()
    wb.save(buffer)
    r = cliente_demo.post("/api/comprobantes/extraer", json={"archivos": [{"nombre": "aux.xlsx", "contenido": _b64(buffer.getvalue())}]})
    assert r.json()["archivos"][0]["tipo"] == "aux" and r.json()["comprobantes"] == []

    r = cliente_demo.post("/api/comprobantes/extraer", json={"archivos": [{"nombre": "a.docx", "contenido": _b64(b"x")}]})
    assert r.status_code == 422


def test_cliente_por_el_nombre_del_archivo():
    clientes = {"PICCININI y ASOCIADOS S.A.": {}, "TRUCKNOR S.A.": {}, "FISCALES SAN MARTIN S.A.": {}, "FISCALES NORTE SRL": {}}
    assert servicio._cliente_por_nombre("compras PICCININI noviembre.csv", clientes) == "PICCININI y ASOCIADOS S.A."
    assert servicio._cliente_por_nombre("Trucknor S.A. factura.pdf", clientes) == "TRUCKNOR S.A."
    # «fiscales» es de dos clientes: mejor no elegir.
    assert servicio._cliente_por_nombre("fiscales.pdf", clientes) == ""


def test_preparar_sin_cliente_avisa(cliente_demo):
    c = Comprobante(fecha="2026-11-03", tipo=1, punto_venta=1, numero=1, importes={"Imp. Total": 10}).model_dump()
    r = cliente_demo.post("/api/comprobantes/preparar", json={"comprobantes": [c]})
    [res] = r.json()["resultados"]
    assert res["error"] and not res["zips"]


def test_texto_con_errores_tipicos_de_ocr():
    # Tesseract sin español: «Razón» -> «Raz6én», «Emisión» -> «Emision».
    texto = TEXTO_FACTURA.replace("Razón Social: ACME", "Raz6én Social: ACME").replace("Emisión", "Emision")
    c = comprobante_del_texto(texto, "f.png", None, "ocr")
    assert c.receptor.nombre == "ACME SRL" and c.fecha == "2026-11-03"


@pytest.mark.skipif(not ocr.disponible(), reason="Tesseract no está instalado en esta PC")
def test_ocr_real_con_tesseract():
    from PIL import Image, ImageDraw, ImageFont

    try:
        fuente = ImageFont.truetype("arial.ttf", 30)
    except OSError:
        pytest.skip("Sin la fuente Arial")
    img = Image.new("RGB", (1300, 760), "white")
    ImageDraw.Draw(img).multiline_text((40, 30), TEXTO_FACTURA, fill="black", font=fuente, spacing=12)
    c = comprobante_del_texto(ocr.texto_de_imagen(img), "f.png", None, "ocr")
    assert (c.tipo, c.punto_venta, c.numero, c.cae) == (1, 2, 1234, "74123456789012")
    assert c.emisor.doc_nro == "30661792015" and c.importes["Imp. Total"] == 1210
