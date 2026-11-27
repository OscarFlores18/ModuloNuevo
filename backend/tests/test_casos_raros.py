"""Comprobantes difíciles que ya se resolvieron: cada caso nuevo va acá.

Cómo agregar uno: armá la respuesta (lo que devolvería Gemini o el OCR) con los
datos del comprobante, pasala por `leer` con Gemini simulado y fijá lo que tiene
que dar. Se corren todos con:

    .venv\\Scripts\\python.exe -m pytest backend/tests/test_casos_raros.py
"""

from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook
from PIL import Image

from backend.app.extraccion import a_excel, gemini
from backend.app.extraccion.conversor import leer as paso_leer


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf_escaneado() -> bytes:
    """Un PDF que es solo una imagen (sin texto): como los escaneados."""
    buffer = io.BytesIO()
    Image.new("RGB", (600, 800), "white").save(buffer, format="PDF")
    return buffer.getvalue()


@pytest.fixture
def gemini_responde(monkeypatch):
    """Gemini simulado: devuelve lo que se le pase, sin internet."""
    monkeypatch.setattr(gemini, "disponible", lambda: True)
    monkeypatch.delenv("OCR_MODE", raising=False)
    monkeypatch.delenv("FORCE_TESSERACT", raising=False)
    monkeypatch.setattr(paso_leer, "es_captura_limpia", lambda *a: False)

    def responder(datos: dict):
        monkeypatch.setattr(gemini, "leer_imagen", lambda imagen, **_: datos)

    return responder


def _no_ok(lectura) -> list[str]:
    return [v.titulo or v.mensaje for v in lectura.verificaciones if v.nivel != "ok"]


# --- Factura A con IVA 21% + impuestos internos + percepción IIBB Bs. As. -----------
# El cuadro de la factura:
#   Alícuota 21,00%     base 83.974,75    IVA 17.634,71
#   10 - IMPUESTOS INTERNOS ITEMS    base 5.875,59    90,54
#   07 - PERCEPCION IIBB BS AS       base 83.974,76   6.717,98
#   Total 108.417,98
FACTURA_CON_TRIBUTOS = {
    "tipo": "comprobante",
    "encabezado": {
        "emisor": "DISTRIBUIDORA EJEMPLO SA", "emisor_cuit": "30500010912",
        "receptor": "CLIENTE EJEMPLO SRL", "receptor_cuit": "30718889800",
        "tipo_codigo": 1, "punto_de_venta": "3", "numero": "120455", "fecha": "02/09/2026",
    },
    "items": [],
    "totales": {
        "neto_gravado": 83974.75, "iva": 17634.71, "total": 108417.98,
        "desglose_iva": [{"alicuota": 21, "base_imponible": 83974.75, "importe_iva": 17634.71}],
        "desglose_tributos": [
            {"codigo": "10", "descripcion": "IMPUESTOS INTERNOS ITEMS", "base": 5875.59, "importe": 90.54},
            {"codigo": "07", "descripcion": "PERCEPCION IIBB BS AS", "base": 83974.76, "importe": 6717.98},
        ],
    },
    "validaciones": {"advertencias": ["El ticket está algo borroso."]},
    "texto": "FACTURA A ...",
}


def test_factura_con_iva_e_impuestos_internos_y_percepcion_cierra(gemini_responde):
    gemini_responde(FACTURA_CON_TRIBUTOS)
    l = a_excel.leer("factura.jpg", _png())
    assert l.metodo == "ia" and l.tipo == "comprobante"
    # Cada tributo va a su total, una sola vez.
    t = a_excel.totales_efectivos(l)
    assert t["impuestos_internos"] == 90.54
    assert t["percepcion_iibb"] == 6717.98
    assert not t.get("otros_tributos")
    # Neto + IVA + tributos = total: no hay nada que avisar.
    assert _no_ok(l) == []
    # Las advertencias de Gemini no se muestran: lo dicen las cuentas.
    assert not any("borroso" in a for a in l.avisos)


def test_factura_con_tributos_que_no_cierra_avisa_solo_la_cuenta(gemini_responde):
    datos = {**FACTURA_CON_TRIBUTOS, "totales": {**FACTURA_CON_TRIBUTOS["totales"], "total": 108517.98}}
    gemini_responde(datos)
    l = a_excel.leer("factura.jpg", _png())
    assert _no_ok(l) == ["Los importes no suman el total"]


def _numeros(contenido: bytes) -> dict[str, set[float]]:
    libro = load_workbook(io.BytesIO(contenido))
    return {h.title: {c for f in h.iter_rows(values_only=True) for c in f if isinstance(c, (int, float))} for h in libro.worksheets}


def test_factura_con_tributos_llega_al_excel(gemini_responde):
    gemini_responde(FACTURA_CON_TRIBUTOS)
    l = a_excel.leer("factura.jpg", _png())
    comprobante = _numeros(a_excel.generar(l))["Comprobante"]
    assert {83974.75, 17634.71, 90.54, 6717.98, 108417.98} <= comprobante


def test_excel_para_xubio_lleva_los_tributos_renglon_por_renglon(gemini_responde):
    """Bug: con los tributos solo en el desglose (sin totales sueltos), la hoja de
    impuestos del Excel para Xubio salía vacía."""
    gemini_responde(FACTURA_CON_TRIBUTOS)
    l = a_excel.leer("factura.jpg", _png())
    hojas = _numeros(a_excel.generar(l, "Compras"))
    assert {90.54, 6717.98} <= hojas["Impuestos y percepciones"]


# --- PDF escaneado: la misma cascada que las fotos -----------------------------------


def test_pdf_escaneado_se_lee_con_gemini(gemini_responde):
    gemini_responde(FACTURA_CON_TRIBUTOS)
    l = a_excel.leer("escaneada.pdf", _pdf_escaneado())
    assert l.metodo == "ia"
    assert l.totales["total"] == 108417.98


def test_pdf_escaneado_sin_gemini_usa_el_ocr_local(monkeypatch):
    monkeypatch.setattr(gemini, "disponible", lambda: False)
    monkeypatch.delenv("OCR_MODE", raising=False)
    monkeypatch.setattr(paso_leer, "hay_ocr_local", lambda: True)
    monkeypatch.setattr(paso_leer, "_leer_local", lambda nombre, imagen, lectura, *a, **k: lectura.model_copy(update={"metodo": "ocr"}))
    l = a_excel.leer("escaneada.pdf", _pdf_escaneado())
    assert l.metodo == "ocr"


# --- Tributos: los renglones mandan ---------------------------------------------------


def test_borrar_un_renglon_de_tributo_deja_de_sumar():
    """Bug: al borrar el renglón de impuestos internos, su importe seguía en el total."""
    l = a_excel.Lectura(
        tipo="comprobante",
        totales={"neto_gravado": 1000.0, "iva": 210.0, "impuestos_internos": 50.0, "percepcion_iibb": 30.0, "total": 1290.0},
        tributos=[a_excel.Tributo(descripcion="PERCEPCION IIBB BS AS", importe=30.0)],
    )
    t = a_excel.totales_efectivos(l)
    assert t["impuestos_internos"] is None and t["percepcion_iibb"] == 30.0


def test_total_suelto_sin_renglon_pasa_a_renglon(gemini_responde):
    """Impuestos internos leídos aparte y la percepción en renglón: no se pierde ninguno."""
    datos = {**FACTURA_CON_TRIBUTOS, "totales": {
        **FACTURA_CON_TRIBUTOS["totales"], "impuestos_internos": 90.54,
        "desglose_tributos": [FACTURA_CON_TRIBUTOS["totales"]["desglose_tributos"][1]],
    }}
    gemini_responde(datos)
    l = a_excel.leer("factura.jpg", _png())
    assert [(x.descripcion, x.importe) for x in l.tributos] == [("PERCEPCION IIBB BS AS", 6717.98), ("Impuestos internos", 90.54)]
    assert _no_ok(l) == []


def test_otros_tributos_que_es_la_suma_no_se_duplica(gemini_responde):
    datos = {**FACTURA_CON_TRIBUTOS, "totales": {**FACTURA_CON_TRIBUTOS["totales"], "otros_tributos": 6808.52}}
    gemini_responde(datos)
    l = a_excel.leer("factura.jpg", _png())
    assert len(l.tributos) == 2 and l.totales["otros_tributos"] is None
    assert _no_ok(l) == []
