"""Conversor De Facturas: imágenes leídas con Gemini (sin llamar a internet)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from backend.app.extraccion import a_excel, gemini
from backend.app.extraccion.conversor import leer as paso_leer


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
    for nombre in ("OCR_MODE",):
        monkeypatch.delenv(nombre, raising=False)
    monkeypatch.delenv("FORCE_TESSERACT", raising=False)
    # La imagen de prueba es blanca: que no la tome como captura para el OCR local.
    monkeypatch.setattr(paso_leer, "es_captura_limpia", lambda *a: False)
    return monkeypatch


def test_imagen_leida_con_gemini(con_gemini):
    con_gemini.setattr(gemini, "leer_imagen", lambda imagen, **_: {**RESPUESTA, "validaciones": {"motor_utilizado": "gemini"}})
    l = a_excel.leer("factura.png", _png())
    assert l.metodo == "ia" and l.tipo == "comprobante" and l.motor == "gemini"
    e = l.encabezado
    assert e["emisor_cuit"] == "30712345671" and e["receptor_cuit"] == "30718889881"
    assert e["tipo"] == "1 - Factura A" and e["numero"] == "00018-00076897"
    assert e["fecha"] == "12/08/2026" and e["cae"] == "76123456789012"  # se guarda (no se muestra): lo pide Xubio
    assert l.totales["total"] == 112000.06 and l.totales["no_gravado"] is None
    assert l.items[0].precio == 7683.21  # como el resto de la app: 2 decimales
    # Pasa por las mismas verificaciones que el OCR.
    assert any(v.campo == "total" or "total" in v.mensaje.lower() for v in l.verificaciones)


def test_tabla_leida_con_gemini(con_gemini):
    con_gemini.setattr(gemini, "leer_imagen", lambda imagen, **_: {"tipo": "tabla", "filas": [["Fecha", "Total"], ["01/08/2026", "1.210,50"]], "texto": ""})
    l = a_excel.leer("captura.png", _png())
    assert l.metodo == "ia" and l.tipo == "tabla"
    assert l.hojas[0].filas == [["Fecha", "Total"], ["01/08/2026", 1210.5]]


def test_si_gemini_falla_sigue_con_ocr(con_gemini):
    def falla(imagen, **_):
        raise gemini.GeminiNoDisponible("Gemini: se llegó al límite gratuito por minuto.")

    con_gemini.setattr(gemini, "leer_imagen", falla)
    con_gemini.setattr(paso_leer, "hay_ocr_local", lambda: True)
    con_gemini.setattr(paso_leer, "_leer_local", lambda nombre, imagen, lectura, *a, **k: lectura.model_copy(update={"metodo": "ocr"}))
    l = a_excel.leer("factura.jpg", _png())
    assert l.metodo == "ocr"
    assert any("límite gratuito" in a and "OCR local" in a for a in l.avisos)


def test_sin_clave_no_se_usa(monkeypatch, tmp_path):
    from backend.app.extraccion import entorno

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(entorno.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(entorno.config, "BACKEND_DIR", tmp_path / "otro")
    assert gemini.clave() == "" and not gemini.disponible()


def test_clave_y_plazo_desde_env_de_la_carpeta_de_datos(monkeypatch, tmp_path):
    from backend.app.extraccion import entorno

    for nombre in ("GEMINI_API_KEY", "GEMINI_PLAZO_S"):
        monkeypatch.delenv(nombre, raising=False)
    monkeypatch.setattr(entorno.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(entorno.config, "BACKEND_DIR", tmp_path / "otro")
    assert gemini.plazo_auto() == 15.0
    (tmp_path / ".env").write_text('# clave\nGEMINI_API_KEY="abc123"\nGEMINI_PLAZO_S=10  # más\n', encoding="utf-8")
    assert gemini.clave() == "abc123" and gemini.plazo_auto() == 10.0


def test_achica_la_imagen_a_1600():
    grande = Image.new("RGB", (4000, 3000))
    chica = gemini._achicar(grande)
    assert max(chica.size) == 1600 and chica.size == (1600, 1200)
    assert gemini._achicar(Image.new("RGB", (800, 600))).size == (800, 600)


def test_json_entre_comillas_de_codigo():
    assert gemini._json_de('```json\n{"tipo": "tabla"}\n```') == {"tipo": "tabla"}


def test_si_un_modelo_esta_saturado_pasa_al_siguiente(monkeypatch):
    from google.genai import errors, types

    monkeypatch.setattr(gemini, "PAUSAS_S", ())
    monkeypatch.setattr(gemini, "modelo", lambda: "primero")
    pedidos = []

    class Modelos:
        def generate_content(self, model, contents, config):
            pedidos.append(model)
            if model != "gemini-3.1-flash-lite":
                raise errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
            return "respuesta"

    class Cliente:
        models = Modelos()

    assert gemini._pedir(Cliente(), types, errors, b"jpeg") == "respuesta"
    assert pedidos == ["primero", "gemini-3.5-flash", "gemini-3.1-flash-lite"]


def test_con_plazo_nunca_pide_menos_de_10_s_y_empieza_por_el_rapido(monkeypatch):
    # Google rechaza con 400 un plazo menor a 10 s; un 429 o 504 pasa al siguiente modelo.
    from google.genai import errors, types

    monkeypatch.setattr(gemini, "modelo", lambda: "primero")
    pedidos = []

    class Modelos:
        def generate_content(self, model, contents, config):
            pedidos.append((model, config.http_options.timeout))
            if model == "gemini-3.1-flash-lite":
                raise errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
            if model == "primero":
                raise errors.ServerError(504, {"error": {"code": 504, "message": "deadline", "status": "DEADLINE_EXCEEDED"}})
            return "respuesta"

    class Cliente:
        models = Modelos()

    assert gemini._pedir(Cliente(), types, errors, b"jpeg", plazo_s=3) == "respuesta"
    assert [m for m, _ in pedidos] == ["gemini-3.1-flash-lite", "primero", "gemini-3.5-flash"]
    assert all(t >= 10_000 for _, t in pedidos)


def test_gemini_con_plazo_vencido_no_espera():
    from google.genai import errors, types

    class Cliente:
        class models:  # noqa: N801
            @staticmethod
            def generate_content(**kw):
                raise AssertionError("no debería pedir nada")

    with pytest.raises(TimeoutError):
        gemini._pedir(Cliente(), types, errors, b"jpeg", plazo_s=0.1)


RESPUESTA_NUEVA = {
    "tipo": "comprobante",
    "encabezado": {
        "emisor_razon_social": "FGC SA",
        "emisor_cuit": "33-71487776-9",
        "receptor_razon_social": "LOGYS SOLUTIONS SRL",
        "receptor_cuit": "30709864013",
        "tipo_comprobante": "81 - Tique Factura A",
        "punto_venta": "00103",
        "numero_comprobante": "00000524",
        "fecha": "25/10/2026",
    },
    "importes": {
        "neto_gravado": 1100.0, "no_gravado": 0, "exento": 0,
        "desglose_iva": [
            {"alicuota": 21.0, "base_imponible": 1000.0, "importe_iva": 210.0},
            {"alicuota": 10.5, "base_imponible": 100.0, "importe_iva": 10.5},
        ],
        "desglose_tributos": [
            {"codigo": None, "descripcion": "10 - IMPUESTOS INTERNOS ITEMS", "base_imponible": 5875.59, "importe": 90.54},
            {"codigo": "07", "descripcion": "PERCEPCION IIBB BS AS", "base_imponible": 83974.76, "importe": 6717.98},
        ],
        "otros_tributos": 6808.52, "total": 8129.02,
    },
    "items": [
        {"codigo": None, "descripcion": "ENTRANA", "cantidad": 1, "precio_unitario": 1000, "alicuota_iva": 21, "importe": 1000},
        {"codigo": "77", "descripcion": "PAN", "cantidad": 1, "precio_unitario": 100, "alicuota_iva": 10.5, "importe": 100},
    ],
    "validaciones": {"cuit_emisor_valido": True, "advertencias": ["El ticket está algo borroso."]},
    "texto": "",
}


def test_esquema_nuevo_con_desglose_de_iva(con_gemini):
    con_gemini.setattr(gemini, "leer_imagen", lambda imagen, **_: RESPUESTA_NUEVA)
    l = a_excel.leer("tique.jpg", _png())
    e = l.encabezado
    assert e["emisor"] == "FGC SA" and e["tipo"] == "81 - Tique Factura A"
    assert e["numero"] == "00103-00000524"
    assert [(x.alicuota, x.base, x.importe) for x in l.iva_alicuotas] == [(21.0, 1000.0, 210.0), (10.5, 100.0, 10.5)]
    assert l.totales["iva"] == 220.5  # la suma del desglose
    assert l.items[1].precio == 100 and l.items[1].alicuota == 10.5
    assert [(x.codigo, x.descripcion, x.importe) for x in l.tributos] == [
        ("10", "IMPUESTOS INTERNOS ITEMS", 90.54), ("07", "PERCEPCION IIBB BS AS", 6717.98)]
    # Cada fila en su total, una sola vez: internos, IIBB Bs. As. y ningún «otro».
    assert (l.totales["impuestos_internos"], l.totales["percepcion_iibb"], l.totales["otros_tributos"]) == (90.54, 6717.98, None)
    assert not any("borroso" in a for a in l.avisos)  # las advertencias de Gemini no se muestran
    v = {(x.campo, x.nivel) for x in l.verificaciones}
    assert ("total", "ok") in v and ("iva", "ok") in v and ("items", "ok") in v


def test_cuit_con_ceros_leidos_como_ocho(con_gemini):
    # 30709864013 con dos 0 tachados leídos como 8: no pasa el módulo 11.
    datos = {**RESPUESTA_NUEVA, "encabezado": {**RESPUESTA_NUEVA["encabezado"], "receptor_cuit": "30789864813"}}
    con_gemini.setattr(gemini, "leer_imagen", lambda imagen, **_: datos)
    l = a_excel.leer("tique.jpg", _png())
    assert l.encabezado["receptor_cuit"] == "30709864013"
    assert any("cambiando 0 y 8" in a for a in l.avisos)


def test_el_prompt_no_pide_cae():
    assert "desglose_iva" in gemini.PROMPT and "no llevan CAE" in gemini.PROMPT
    assert '"cae"' not in gemini.PROMPT
