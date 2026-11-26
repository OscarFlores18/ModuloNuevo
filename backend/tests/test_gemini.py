"""Conversor De Facturas: imágenes leídas con Gemini (sin llamar a internet)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from backend.app.extraccion import a_excel, gemini


def _png(ancho=400, alto=300) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (ancho, alto), "white").save(buffer, format="PNG")
    return buffer.getvalue()


RESPUESTA = {
    "tipo": "comprobante",
    "encabezado": {
        "emisor": "ESTACION DE SERVICIO NORTE SRL",
        "emisor_cuit": "30-71234567-1",
        "receptor": "PICCININI Y ASOCIADOS S.A.",
        "receptor_cuit": "30718889881",
        "tipo_codigo": 1,
        "punto_de_venta": "18",
        "numero": "76897",
        "fecha": "12/08/2026",
        "cae": "76123456789012",
    },
    "items": [{"codigo": "", "descripcion": "Nafta", "cantidad": 10, "precio": "7.683,214", "alicuota": 21, "importe": 76832.14}],
    "totales": {"neto_gravado": 76832.14, "no_gravado": None, "exento": None, "iva": 16134.75, "otros_tributos": 19033.17, "total": "112.000,06"},
    "filas": [],
    "texto": "FACTURA A ...",
}


@pytest.fixture
def con_gemini(monkeypatch):
    monkeypatch.setattr(gemini, "disponible", lambda: True)
    return monkeypatch


def test_imagen_leida_con_gemini(con_gemini):
    con_gemini.setattr(gemini, "leer_imagen", lambda imagen: RESPUESTA)
    l = a_excel.leer("factura.png", _png())
    assert l.metodo == "ia" and l.tipo == "comprobante"
    e = l.encabezado
    assert e["emisor_cuit"] == "30712345671" and e["receptor_cuit"] == "30718889881"
    assert e["tipo"] == "1 - Factura A" and e["numero"] == "00018-00076897"
    assert e["fecha"] == "12/08/2026" and e["cae"] == "76123456789012"
    assert l.totales["total"] == 112000.06 and l.totales["no_gravado"] is None
    assert l.items[0].precio == 7683.21  # como el resto de la app: 2 decimales
    # Pasa por las mismas verificaciones que el OCR.
    assert any(v.campo == "total" or "total" in v.mensaje.lower() for v in l.verificaciones)


def test_tabla_leida_con_gemini(con_gemini):
    con_gemini.setattr(gemini, "leer_imagen", lambda imagen: {"tipo": "tabla", "filas": [["Fecha", "Total"], ["01/08/2026", "1.210,50"]], "texto": ""})
    l = a_excel.leer("captura.png", _png())
    assert l.metodo == "ia" and l.tipo == "tabla"
    assert l.hojas[0].filas == [["Fecha", "Total"], ["01/08/2026", 1210.5]]


def test_si_gemini_falla_sigue_con_ocr(con_gemini):
    def falla(imagen):
        raise gemini.GeminiNoDisponible("Gemini: se llegó al límite gratuito por minuto.")

    con_gemini.setattr(gemini, "leer_imagen", falla)
    con_gemini.setattr(a_excel, "_imagen_a_lectura", lambda nombre, imagen, lectura: lectura.model_copy(update={"metodo": "ocr"}))
    l = a_excel.leer("factura.jpg", _png())
    assert l.metodo == "ocr"
    assert any("límite gratuito" in a and "OCR" in a for a in l.avisos)


def test_sin_clave_no_se_usa(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(gemini.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(gemini.config, "BACKEND_DIR", tmp_path / "otro")
    assert gemini.clave() == "" and not gemini.disponible()


def test_clave_desde_env_de_la_carpeta_de_datos(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(gemini.config, "DATA_DIR", tmp_path)
    (tmp_path / ".env").write_text('# clave\nGEMINI_API_KEY="abc123"\n', encoding="utf-8")
    assert gemini.clave() == "abc123"


def test_achica_la_imagen_a_1600():
    grande = Image.new("RGB", (4000, 3000))
    chica = gemini._achicar(grande)
    assert max(chica.size) == 1600 and chica.size == (1600, 1200)
    assert gemini._achicar(Image.new("RGB", (800, 600))).size == (800, 600)


def test_json_entre_comillas_de_codigo():
    assert gemini._json_de('```json\n{"tipo": "tabla"}\n```') == {"tipo": "tabla"}
