"""Conversor De Facturas: lectura local con Tesseract + OpenCV y PaddleOCR, y la
cascada QR → Gemini → PaddleOCR / Tesseract. No hace falta Tesseract instalado: el texto sale de
`datos/tique_ocr.txt` (OCR real de un tique factura A) y los motores se simulan."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from backend.app.extraccion import a_excel, gemini, ocr, paddle, tesseract

TIQUE = (Path(__file__).parent / "datos" / "tique_ocr.txt").read_text(encoding="utf-8")


def _png(imagen: Image.Image) -> bytes:
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    return buffer.getvalue()


def _captura() -> Image.Image:
    """Como una captura de pantalla: fondo blanco puro y texto negro."""
    img = Image.new("RGB", (900, 600), "white")
    d = ImageDraw.Draw(img)
    for i in range(12):
        d.text((30, 30 + 40 * i), f"Renglon {i}   1.234,56", fill="black")
    return img


def _foto() -> Image.Image:
    """Como una foto de un tique: papel gris con sombras."""
    img = Image.linear_gradient("L").resize((900, 600)).point(lambda v: 90 + v // 3).convert("RGB")
    return img


@pytest.fixture
def sin_entorno(monkeypatch):
    for nombre in ("OCR_MODE", "FORCE_TESSERACT", "GEMINI_PLAZO_S"):
        monkeypatch.setenv(nombre, "")
    return monkeypatch


# --- Preprocesado ------------------------------------------------------------------


def test_preprocesado_da_blanco_y_negro_y_agranda_las_chicas():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    binaria = tesseract.preprocess_for_ocr(_captura())
    assert binaria.shape[1] >= tesseract.ANCHO_MINIMO  # 900 px → agrandada
    assert set(np.unique(binaria)) <= {0, 255}
    # Casi todo blanco (el fondo) y algo de negro (el texto).
    assert 0.80 < (binaria == 255).mean() < 1


def test_preprocesado_limpia_la_sombra_del_papel():
    pytest.importorskip("cv2")
    binaria = tesseract.preprocess_for_ocr(_foto())
    # Un degradé sin texto: el umbral adaptativo lo deja blanco.
    assert (binaria == 255).mean() > 0.95


# --- Texto → JSON ------------------------------------------------------------------


def test_iva_y_tributos_del_tique_real():
    lineas = TIQUE.splitlines()
    assert tesseract.desglose_iva_de_texto(lineas, 63974.75) == [
        {"alicuota": 21.0, "base_imponible": 63974.75, "importe_iva": 17634.71}
    ]
    # La leyenda de la Ley 27.743 («Imp.Int: 90.54», «Perc. IIBB») no se duplica.
    assert tesseract.tributos_de_texto(lineas) == [
        {"codigo": "10", "descripcion": "IMPUESTOS INTERNOS ITEMS", "base_imponible": 5675.59, "importe": 90.54},
        {"codigo": "07", "descripcion": "PERCEPCION ITBB BS AS", "base_imponible": 63974.76, "importe": 6717.98},
    ]


def test_iva_en_varios_formatos():
    lineas = ["IVA 21%: $ 210,00", "IVA 10,5 % 100,00 10,50", "IVA Responsable Inscripto", "Percepción IVA 3% 30,00", "Total IVA 220,50"]
    assert tesseract.desglose_iva_de_texto(lineas) == [
        {"alicuota": 21.0, "base_imponible": None, "importe_iva": 210.0},
        {"alicuota": 10.5, "base_imponible": 100.0, "importe_iva": 10.5},
    ]
    assert tesseract.tributos_de_texto(lineas)[0]["importe"] == 30.0


def test_json_del_tique_con_el_esquema_de_la_ia():
    d = tesseract.json_de_texto([TIQUE])
    e, v = d["encabezado"], d["validaciones"]
    assert d["tipo"] == "comprobante"
    assert e["tipo_comprobante"] == "81 - Tique Factura A"
    assert (e["punto_venta"], e["numero_comprobante"], e["fecha"]) == ("00103", "00000524", "25/10/2026")
    assert "cae" not in e and "cae" not in str(d["importes"]).lower()
    # 33714077769 no pasa el módulo 11: cambiando el 0 por 8 sí.
    assert e["emisor_cuit"] == "33714877769" and v["cuit_emisor_valido"]
    assert any("cambiando 0 y 8" in a for a in v["advertencias"])
    assert v["motor_utilizado"] == "tesseract_local"
    # El OCR leyó 63.974,75 (es 83.974,75): no cierra, y lo dice.
    assert not v["total_coincide"] and any("total" in a for a in v["advertencias"])
    assert len(d["items"]) == 8 and d["importes"]["desglose_iva"][0]["alicuota"] == 21.0


def test_lectura_con_tesseract_pasa_por_las_mismas_verificaciones(monkeypatch):
    monkeypatch.setattr(tesseract, "extract_with_tesseract", lambda imagen, pasadas_extra=True: tesseract.json_de_texto([TIQUE]))
    l = a_excel._imagen_a_lectura("tique.jpg", _foto(), a_excel.Lectura(archivo="tique.jpg"))
    a_excel.completar_desglose(l)
    l.verificaciones = a_excel.verificar(l)
    assert l.metodo == "ocr" and l.motor == "tesseract_local"
    assert [(x.codigo, x.importe) for x in l.tributos] == [("10", 90.54), ("07", 6717.98)]
    # «PERCEPCION ITBB» (el OCR leyó T por I) también es la percepción de IIBB Bs. As.
    assert (l.totales["impuestos_internos"], l.totales["percepcion_iibb"], l.totales["otros_tributos"]) == (90.54, 6717.98, None)
    sugerencias = {v.campo: v.sugerencia for v in l.verificaciones if v.sugerencia is not None}
    assert sugerencias["neto_gravado"] == 83974.75 and sugerencias["total"] == 108417.98
    assert any("cambiando 0 y 8" in a for a in l.avisos)


# --- Cascada -----------------------------------------------------------------------


def test_modo_ocr(sin_entorno):
    assert a_excel.modo_ocr() == "auto"
    sin_entorno.setenv("OCR_MODE", "gemini_only")
    assert a_excel.modo_ocr() == "gemini"
    sin_entorno.setenv("OCR_MODE", "paddle")
    assert a_excel.modo_ocr() == "paddle"
    sin_entorno.setenv("FORCE_TESSERACT", "true")
    assert a_excel.modo_ocr() == "tesseract"
    assert a_excel.modo_ocr("gemini") == "gemini"  # lo pedido manda


def test_captura_limpia_o_foto():
    assert a_excel.es_captura_limpia(_captura())
    assert not a_excel.es_captura_limpia(_foto())


def _simular(monkeypatch, *, tesseract_cierra: bool, gemini_responde: bool):
    llamadas = []
    bien = {"tipo": "comprobante", "encabezado": {"emisor_cuit": "30712345671"}, "importes": {"neto_gravado": 100, "total": 100}}
    mal = {"tipo": "comprobante", "encabezado": {"emisor_cuit": "30712345671"}, "importes": {"neto_gravado": 100, "total": 999}}

    def leer_tesseract(imagen, pasadas_extra=True):
        llamadas.append("tesseract" if pasadas_extra else "tesseract_rapido")
        return {**(bien if tesseract_cierra else mal), "validaciones": {"motor_utilizado": "tesseract_local"}}

    def leer_gemini(imagen, plazo_s=None):
        llamadas.append(f"gemini({plazo_s})")
        if not gemini_responde:
            raise gemini.GeminiNoDisponible("Gemini no contestó en 6 s.")
        return bien

    monkeypatch.setattr(tesseract, "extract_with_tesseract", leer_tesseract)
    monkeypatch.setattr(gemini, "leer_imagen", leer_gemini)
    monkeypatch.setattr(gemini, "disponible", lambda: True)
    monkeypatch.setattr(ocr, "disponible", lambda: True)
    monkeypatch.setattr(paddle, "disponible", lambda: False)
    return llamadas


def test_captura_que_cierra_no_usa_gemini(sin_entorno):
    llamadas = _simular(sin_entorno, tesseract_cierra=True, gemini_responde=True)
    l = a_excel.leer("captura.png", _png(_captura()))
    assert llamadas == ["tesseract_rapido"]
    assert l.motor == "tesseract_local" and l.duracion_ms is not None


def test_foto_va_a_gemini_con_plazo(sin_entorno):
    llamadas = _simular(sin_entorno, tesseract_cierra=True, gemini_responde=True)
    l = a_excel.leer("foto.jpg", _png(_foto()))
    assert llamadas == ["gemini(15.0)"] and l.metodo == "ia"


def test_si_gemini_tarda_lee_tesseract(sin_entorno):
    llamadas = _simular(sin_entorno, tesseract_cierra=True, gemini_responde=False)
    l = a_excel.leer("foto.jpg", _png(_foto()))
    assert llamadas == ["gemini(15.0)", "tesseract"]
    assert l.metodo == "ocr" and any("6 s" in a and "OCR local" in a for a in l.avisos)


def test_sin_ocr_local_gemini_no_tiene_plazo(sin_entorno):
    llamadas = _simular(sin_entorno, tesseract_cierra=True, gemini_responde=True)
    sin_entorno.setattr(ocr, "disponible", lambda: False)
    l = a_excel.leer("foto.jpg", _png(_foto()))
    assert llamadas == ["gemini(None)"] and l.metodo == "ia"


def test_sin_ocr_local_y_gemini_caido_no_promete_ocr(sin_entorno):
    llamadas = _simular(sin_entorno, tesseract_cierra=True, gemini_responde=False)
    sin_entorno.setattr(ocr, "disponible", lambda: False)
    l = a_excel.leer("foto.jpg", _png(_foto()))
    assert llamadas == ["gemini(None)"]
    assert not any("Se leyó con el OCR local" in a for a in l.avisos)


def test_tesseract_only_no_usa_gemini(sin_entorno):
    sin_entorno.setenv("FORCE_TESSERACT", "true")
    llamadas = _simular(sin_entorno, tesseract_cierra=False, gemini_responde=True)
    a_excel.leer("foto.jpg", _png(_foto()))
    assert llamadas == ["tesseract"]


def test_motor_pedido_en_la_peticion(sin_entorno):
    llamadas = _simular(sin_entorno, tesseract_cierra=True, gemini_responde=True)
    a_excel.leer("captura.png", _png(_captura()), motor="gemini")
    assert llamadas == ["gemini(None)"]


def test_el_qr_de_arca_manda(sin_entorno):
    _simular(sin_entorno, tesseract_cierra=False, gemini_responde=True)
    sin_entorno.setenv("FORCE_TESSERACT", "true")
    qr = {"fecha": "2026-10-25", "cuit": 30712345671, "ptoVta": 103, "tipoCmp": 81, "nroCmp": 524, "importe": 100,
          "tipoDocRec": 80, "nroDocRec": 30709864013, "codAut": 76123456789012}
    sin_entorno.setattr("backend.app.extraccion.qr_afip.leer_qrs", lambda imagen: [qr])
    l = a_excel.leer("foto.jpg", _png(_foto()))
    e = l.encabezado
    assert (e["numero"], e["fecha"], e["tipo"], e["receptor_cuit"]) == ("00103-00000524", "25/10/2026", "81 - Tique Factura A", "30709864013")
    assert l.totales["total"] == 100.0 and l.motor == "qr+tesseract_local"
    assert e["cae"] == "76123456789012" and any("QR de ARCA" in a and "999" in a for a in l.avisos)


def test_endpoint_acepta_el_motor(cliente_demo, sin_entorno):
    import base64

    llamadas = _simular(sin_entorno, tesseract_cierra=True, gemini_responde=True)
    r = cliente_demo.post("/api/a-excel/leer", json={"motor": "tesseract", "archivos": [
        {"nombre": "foto.png", "contenido": base64.b64encode(_png(_foto())).decode()}]})
    assert r.status_code == 200, r.text
    assert llamadas == ["tesseract"] and r.json()["lecturas"][0]["motor"] == "tesseract_local"


def test_sin_clave_de_gemini_lee_el_ocr_local(sin_entorno):
    llamadas = _simular(sin_entorno, tesseract_cierra=False, gemini_responde=True)
    sin_entorno.setattr(gemini, "disponible", lambda: False)
    l = a_excel.leer("foto.jpg", _png(_foto()))
    assert llamadas == ["tesseract"] and l.metodo == "ocr"


# --- PaddleOCR -----------------------------------------------------------------------


def test_paddle_preparado_en_gris_con_contraste():
    pytest.importorskip("cv2")
    preparada = paddle.preparar(_foto())
    assert preparada.shape == (600, 900, 3)
    assert (preparada[..., 0] == preparada[..., 1]).all()  # gris, en 3 canales para PaddleOCR


def test_paddle_cajas_en_renglones_por_y_y_x():
    # Columnas desparejas: el precio está un poco más arriba que la descripción.
    cajas = [
        (400, 102, 480, 122, "1.000,00"), (10, 100, 120, 120, "ENTRANA"),
        (10, 140, 80, 160, "PAN"), (400, 138, 470, 158, "100,00"),
        (10, 10, 200, 30, "FGC SA"),
    ]
    assert paddle.renglones(cajas) == ["FGC SA", "ENTRANA  1.000,00", "PAN  100,00"]


def test_paddle_salida_de_las_dos_versiones():
    v3 = [{"rec_texts": ["TOTAL", "$ 1.210,50"], "rec_polys": [[[10, 10], [60, 10], [60, 30], [10, 30]], [[200, 12], [300, 12], [300, 32], [200, 32]]]}]
    v2 = [[[[[10, 10], [60, 10], [60, 30], [10, 30]], ("TOTAL", 0.99)], [[[200, 12], [300, 12], [300, 32], [200, 32]], ("$ 1.210,50", 0.98)]]]
    assert paddle.renglones(paddle.cajas_de(v3)) == paddle.renglones(paddle.cajas_de(v2)) == ["TOTAL  $ 1.210,50"]


def test_paddle_arma_el_mismo_json(monkeypatch):
    monkeypatch.setattr(paddle, "texto_paddle", lambda imagen: TIQUE)
    d = paddle.extract_with_paddleocr(_foto())
    assert d["validaciones"]["motor_utilizado"] == "paddleocr_local"
    assert d["encabezado"]["emisor_cuit"] == "33714877769" and "ms" in d["validaciones"]


def test_con_paddle_instalado_el_ocr_local_es_paddle(sin_entorno):
    llamadas = _simular(sin_entorno, tesseract_cierra=True, gemini_responde=False)
    sin_entorno.setattr(paddle, "disponible", lambda: True)

    def leer_paddle(imagen):
        llamadas.append("paddle")
        return tesseract.json_de_texto([TIQUE], motor="paddleocr_local")

    sin_entorno.setattr(paddle, "extract_with_paddleocr", leer_paddle)
    l = a_excel.leer("foto.jpg", _png(_foto()))
    assert llamadas == ["gemini(15.0)", "paddle"] and l.motor == "paddleocr_local"


def test_paddle_pedido_sin_instalar_usa_tesseract(sin_entorno):
    llamadas = _simular(sin_entorno, tesseract_cierra=True, gemini_responde=True)
    l = a_excel.leer("foto.jpg", _png(_foto()), motor="paddle")
    assert llamadas == ["tesseract"] and any("PaddleOCR no está instalado" in a for a in l.avisos)


def test_extract_invoice_data_desde_el_disco(sin_entorno, tmp_path):
    _simular(sin_entorno, tesseract_cierra=True, gemini_responde=True)
    ruta = tmp_path / "foto.png"
    ruta.write_bytes(_png(_foto()))
    l = a_excel.extract_invoice_data(ruta)
    assert l.archivo == "foto.png" and l.metodo == "ia" and l.verificaciones
