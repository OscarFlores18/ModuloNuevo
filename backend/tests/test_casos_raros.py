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


# --- Gemini en el límite por minuto y sin OCR local -----------------------------------


def _cliente_con_cupo(respuestas):
    """Un cliente de Gemini falso: cada pedido saca la siguiente respuesta (o error)."""
    pedidos = []

    class Modelos:
        def generate_content(self, model, contents, config):
            pedidos.append(model)
            r = respuestas.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

    class Cliente:
        models = Modelos()

    return Cliente(), pedidos


def _sin_cupo(detalle: str = "PerMinute", demora: str = "12s"):
    from google.genai import errors

    return errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                              "message": f"Quota exceeded for metric ...{detalle}...",
                                              "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": demora}]}})


def test_limite_por_minuto_sin_ocr_local_espera_y_reintenta(monkeypatch):
    """Caso real: «se llegó al límite gratuito por minuto» y no hay Tesseract: antes
    no se leía nada; ahora espera lo que pide Google y vuelve a probar."""
    from google.genai import errors, types

    esperas = []
    monkeypatch.setattr(gemini.time, "sleep", esperas.append)
    monkeypatch.setattr(gemini, "modelo", lambda: "primero")
    cliente, pedidos = _cliente_con_cupo([_sin_cupo(), _sin_cupo(), _sin_cupo(), "respuesta"])
    assert gemini._pedir(cliente, types, errors, b"jpeg") == "respuesta"
    assert esperas == [13.0]  # el retryDelay de Google + 1 s
    assert len(pedidos) == 4


def test_cupo_del_dia_no_espera(monkeypatch):
    from google.genai import errors, types

    monkeypatch.setattr(gemini.time, "sleep", lambda s: pytest.fail("no tenía que esperar"))
    monkeypatch.setattr(gemini, "modelo", lambda: "primero")
    cliente, _ = _cliente_con_cupo([_sin_cupo("PerDay")] * 3)
    with pytest.raises(errors.ClientError):
        gemini._pedir(cliente, types, errors, b"jpeg")


def test_con_ocr_local_no_espera_el_cupo(monkeypatch):
    """Con plazo (hay OCR local para seguir) no se espera: se lee con el OCR."""
    from google.genai import errors, types

    monkeypatch.setattr(gemini.time, "sleep", lambda s: pytest.fail("no tenía que esperar"))
    monkeypatch.setattr(gemini, "modelo", lambda: "primero")
    cliente, _ = _cliente_con_cupo([_sin_cupo()] * 3)
    with pytest.raises(errors.ClientError):
        gemini._pedir(cliente, types, errors, b"jpeg", plazo_s=15)


def test_modelo_sin_cupo_del_dia_se_saltea_hasta_que_se_renueva(monkeypatch):
    """Caso real: gemini-3.8-flash con sus 20 pedidos del día usados. El siguiente
    pedido ya no lo prueba: va directo al de respaldo."""
    from google.genai import errors, types

    monkeypatch.setattr(gemini, "modelo", lambda: "primero")
    cliente, pedidos = _cliente_con_cupo([_sin_cupo("PerDayPerProjectPerModel", "17699s"), "uno", "dos"])
    assert gemini._pedir(cliente, types, errors, b"jpeg") == "uno"
    assert gemini._pedir(cliente, types, errors, b"jpeg") == "dos"
    assert pedidos == ["primero", "gemini-3.5-flash", "gemini-3.5-flash"]


def test_modelo_que_ya_no_existe_pasa_al_siguiente(monkeypatch):
    from google.genai import errors, types

    monkeypatch.setattr(gemini, "modelo", lambda: "viejo")
    no_existe = errors.ClientError(404, {"error": {"code": 404, "message": "not found", "status": "NOT_FOUND"}})
    cliente, pedidos = _cliente_con_cupo([no_existe, "respuesta"])
    assert gemini._pedir(cliente, types, errors, b"jpeg") == "respuesta"


def test_todos_sin_cupo_del_dia_lo_dice_claro(monkeypatch):
    from google.genai import errors, types

    monkeypatch.setattr(gemini, "modelo", lambda: "primero")
    cliente, _ = _cliente_con_cupo([_sin_cupo("PerDay", "3600s")] * 3)
    with pytest.raises(errors.ClientError):
        gemini._pedir(cliente, types, errors, b"jpeg")
    with pytest.raises(gemini.GeminiNoDisponible, match="cupo gratuito del día de todos"):
        gemini._pedir(cliente, types, errors, b"jpeg")


# --- Salida a Xubio: «Importar desde Mis Comprobantes (ARCA)» -------------------------


def _csv_del_zip(contenido: bytes) -> list[list[str]]:
    import zipfile

    with zipfile.ZipFile(io.BytesIO(contenido)) as z:
        nombre = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        texto = z.read(nombre).decode("utf-8")
    return [linea.split(";") for linea in texto.strip().split("\n")]


def test_factura_a_mis_comprobantes_para_xubio(gemini_responde):
    """La factura con IVA, impuestos internos y percepción: una fila del CSV de ARCA
    con el proveedor por CUIT, el IVA 21% y los tributos juntos en «Otros Tributos»."""
    from backend.app.extraccion.conversor import xubio

    datos = {**FACTURA_CON_TRIBUTOS, "encabezado": {**FACTURA_CON_TRIBUTOS["encabezado"], "cae": "76123456789012"}}
    gemini_responde(datos)
    l = a_excel.leer("factura.jpg", _png())
    assert l.encabezado["cae"] == "76123456789012"  # se guarda aunque no se muestre
    r = xubio.preparar(l, "Compras", "CLIENTE EJEMPLO SRL")
    assert r["listo"] and r["avisos"] == []
    encabezado, fila = _csv_del_zip(r["zip"]["contenido"])
    d = dict(zip((c.strip('"') for c in encabezado), fila))
    assert d["Fecha de Emisión"] == "2026-09-02" and d["Tipo de Comprobante"] == "1"
    assert d["Punto de Venta"] == "3" and d["Número Desde"] == "120455"
    assert d["Cód. Autorización"] == "76123456789012"
    # En Compras, el proveedor (emisor) va en las columnas «Receptor» del esqueleto.
    assert d["Nro. Doc. Receptor"] == "30500010912" and d["Denominación Receptor"] == "DISTRIBUIDORA EJEMPLO SA"
    assert d["Imp. Neto Gravado IVA 21%"] == "83974,75" and d["IVA 21%"] == "17634,71"
    assert d["Otros Tributos"] == "6808,52" and d["Imp. Total"] == "108417,98"


def test_para_xubio_sin_cae_avisa():
    from backend.app.extraccion.conversor import xubio

    l = a_excel.Lectura(tipo="comprobante", encabezado={
        "emisor": "X SA", "emisor_cuit": "30500010912", "tipo": "1 - Factura A", "numero": "00001-00000010", "fecha": "01/09/2026"},
        totales={"neto_gravado": 100.0, "iva": 21.0, "total": 121.0},
        iva_alicuotas=[a_excel.AlicuotaIva(alicuota=21, base=100, importe=21)])
    r = xubio.preparar(l, "Compras", "EMPRESA")
    assert any("Sin CAE" in a for a in r["avisos"])


def test_endpoint_prepara_el_zip_sin_abrir_xubio(entorno, monkeypatch):
    """/a-excel/xubio/preparar arma el .zip y no toca el bot."""
    import base64

    from backend.app.bots import xubio as bot

    monkeypatch.setattr(bot, "importar_arca", lambda *a, **k: pytest.fail("no tenía que abrir Xubio"))
    client, _ = entorno
    lectura = a_excel.Lectura(tipo="comprobante", encabezado={
        "emisor": "X SA", "emisor_cuit": "30500010912", "tipo": "1 - Factura A", "numero": "00001-00000010",
        "fecha": "01/09/2026", "cae": "76123456789012"},
        totales={"neto_gravado": 100.0, "iva": 21.0, "total": 121.0},
        iva_alicuotas=[a_excel.AlicuotaIva(alicuota=21, base=100, importe=21)])
    r = client.post("/api/a-excel/xubio/preparar", json={"lectura": lectura.model_dump(), "operacion": "Compras", "empresa": "EMPRESA"})
    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["listo"] and datos["avisos"] == []
    assert datos["zip"]["nombre"].endswith(".zip") and base64.b64decode(datos["zip"]["contenido"])[:2] == b"PK"


# --- Ítems: cantidad × precio = importe ----------------------------------------------


def test_item_cuya_cantidad_por_precio_no_da_el_importe_se_avisa():
    """Caso visto probando: 150 × 279,99 = 41.998,50 pero el importe decía 41.999,95, y
    como la suma de los ítems daba el neto, la pantalla decía «Todo cuadra»."""
    items = [a_excel.Item(descripcion="Gaseosa", cantidad=120, precio=349.79, alicuota=21, importe=41974.80),
             a_excel.Item(descripcion="Agua", cantidad=150, precio=279.99, alicuota=21, importe=41999.95)]
    v = a_excel._verificar_items(items, {"neto_gravado": 83974.75})
    malos = [x for x in v if x.nivel != "ok"]
    assert [x.campo for x in malos] == ["item_1"]
    assert "41.998,50" in malos[0].mensaje


def test_item_con_precio_con_iva_no_se_avisa():
    """Tiques: el precio ya trae el IVA y el importe es sin IVA (o al revés)."""
    items = [a_excel.Item(cantidad=2, precio=121.0, alicuota=21, importe=200.0),
             a_excel.Item(cantidad=1, precio=100.0, alicuota=21, importe=121.0)]
    assert not [x for x in a_excel._verificar_items(items, {}) if x.nivel != "ok"]


def test_modelo_que_no_acepta_sin_pensar_se_pide_sin_esa_opcion(monkeypatch):
    """Caso real: gemini-3.5-flash-lite responde 400 a thinking_budget=0 y cortaba la
    cadena antes de probar los demás modelos."""
    from google.genai import errors, types

    monkeypatch.setattr(gemini, "modelo", lambda: "lite")
    pedidos = []

    class Modelos:
        def generate_content(self, model, contents, config):
            pedidos.append(config.thinking_config is not None)
            if config.thinking_config is not None:
                raise errors.ClientError(400, {"error": {"code": 400, "message": "Request contains an invalid argument.", "status": "INVALID_ARGUMENT"}})
            return "respuesta"

    class Cliente:
        models = Modelos()

    assert gemini._pedir(Cliente(), types, errors, b"jpeg") == "respuesta"
    assert gemini._pedir(Cliente(), types, errors, b"jpeg") == "respuesta"
    assert pedidos == [True, False, False]  # la segunda vez ya no prueba «sin pensar»


def test_clave_invalida_corta_sin_probar_otros_modelos(monkeypatch):
    from google.genai import errors, types

    monkeypatch.setattr(gemini, "modelo", lambda: "primero")
    mala = errors.ClientError(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}})
    cliente, pedidos = _cliente_con_cupo([mala, "no debería llegar"])
    with pytest.raises(errors.ClientError):
        gemini._pedir(cliente, types, errors, b"jpeg")
    assert pedidos == ["primero"]


# --- Fecha de vencimiento del pago (no la del CAE) ------------------------------------


def test_vencimiento_de_gemini_se_guarda_y_va_al_excel_para_xubio(gemini_responde):
    datos = {**FACTURA_CON_TRIBUTOS, "encabezado": {**FACTURA_CON_TRIBUTOS["encabezado"], "fecha_vencimiento": "2026-10-02"}}
    gemini_responde(datos)
    l = a_excel.leer("factura.jpg", _png())
    assert l.encabezado["vencimiento"] == "02/10/2026"
    libro = load_workbook(io.BytesIO(a_excel.generar(l, "Compras")))
    fila = [c for f in libro.worksheets[0].iter_rows(min_row=2, max_row=2, values_only=True) for c in f]
    assert fila[4].strftime("%d/%m/%Y") == "02/09/2026" and fila[5].strftime("%d/%m/%Y") == "02/10/2026"


def test_vencimiento_del_texto_salta_el_vto_del_cae():
    from backend.app.extraccion.conversor.normalizar import _vencimiento_de_texto

    texto = "Fecha de Emisión: 02/09/2026\nFecha de Vto. de CAE: 12/09/2026\nFecha de Vto. para el pago: 02/10/2026"
    assert _vencimiento_de_texto(texto) == "02/10/2026"
    assert _vencimiento_de_texto("CAE N°: 76123456789012  Fecha de Vto. de CAE: 12/09/2026") is None
