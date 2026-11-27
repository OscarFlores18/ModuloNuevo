"""Catálogo de Xubio, memoria por CUIT y clasificación con Gemini (sin internet)."""

from __future__ import annotations

import pytest

from backend.app.core import config
from backend.app.extraccion import gemini
from backend.app.imputacion import almacen
from backend.app.imputacion import clasificador_xubio as clasificador
from backend.app.imputacion.clasificador_xubio import LineaCompra, RespuestaGemini
from backend.app.imputacion.xubio_catalog_parser import CatalogoInvalido, inferir_alicuota, parsear_catalogo

XML = (
    '<root><fields><field fieldID="PRODUCTOID">PRODUCTOID</field><field fieldID="NOMBRE">NOMBRE</field>'
    '<field fieldID="USRCODE">USRCODE</field><field fieldID="BARCODE">BARCODE</field></fields>'
    "<data><![CDATA["
    "-97,Producto al 21%25,PROD21,;"
    "-96,Producto al 10.5%25,PROD105,;"
    "-95,Producto Exento,PRODEX,;"
    "1001,HONORARIOS  PROFESIONALES ,HON,;"
    "1002,Servicios p%C3%BAblicos 27%25,SP27,;"
    "1003,Combustible%2C lubricantes,COMB,779123;"
    "1004,GASTOS VARIOS,GV,;"
    "1005,Intereses exentos,INT,;"
    "abc,roto,,;"
    "]]></data></root>"
)


@pytest.fixture
def datos(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    almacen.guardar_catalogo("Acme", parsear_catalogo(XML))
    return tmp_path


def _linea(**kw) -> LineaCompra:
    base = dict(descripcion="Abono mensual", alicuota_iva=21.0, importe_neto=1234.5,
                proveedor_nombre="TELECOM ARGENTINA SA", proveedor_cuit="30-63945373-8", tipo_comprobante="1 - Factura A")
    return LineaCompra(**{**base, **kw})


# --- Parser -------------------------------------------------------------------------


def test_parser_decodifica_y_limpia():
    productos = {p.producto_id: p for p in parsear_catalogo(XML)}
    assert set(productos) == {-97, -96, -95, 1001, 1002, 1003, 1004, 1005}
    hon = productos[1001]
    assert hon.nombre == "HONORARIOS  PROFESIONALES " and hon.nombre_limpio == "HONORARIOS PROFESIONALES"
    assert hon.alicuota_iva is None and not hon.es_generico
    assert productos[1003].nombre == "Combustible, lubricantes" and productos[1003].barcode == "779123"
    assert productos[1002].nombre == "Servicios públicos 27%" and productos[1002].alicuota_iva == 27.0
    assert productos[-97].alicuota_iva == 21.0 and productos[-97].es_generico and productos[-97].es_sistema
    assert productos[-96].alicuota_iva == 10.5 and productos[-96].es_generico
    assert productos[-95].alicuota_iva == 0.0 and productos[-95].es_generico
    assert productos[1004].es_generico and productos[1004].alicuota_iva is None
    assert productos[1005].alicuota_iva == 0.0 and not productos[1005].es_generico


@pytest.mark.parametrize("nombre,tasa", [
    ("Producto al 10,5%", 10.5), ("Insumos IVA 21", 21.0), ("Cosa no gravada", 0.0), ("PROD21", None), ("Algo al 99%", None),
])
def test_inferir_alicuota(nombre, tasa):
    assert inferir_alicuota(nombre) == tasa


def test_parser_rechaza_xml_malo():
    with pytest.raises(CatalogoInvalido):
        parsear_catalogo("<root><data></data></root>")
    with pytest.raises(CatalogoInvalido):
        parsear_catalogo("no es xml")


# --- Filtro -------------------------------------------------------------------------


def test_filtro_por_alicuota():
    productos = parsear_catalogo(XML)
    ids = {p.producto_id for p in clasificador.filtrar_por_alicuota(productos, 21.0)}
    assert ids == {-97, 1001, 1003, 1004}  # la tasa, sin tasa y el comodín de 21
    exento = {p.producto_id for p in clasificador.filtrar_por_alicuota(productos, 0.0)}
    assert -95 in exento and 1005 in exento and -97 not in exento
    assert clasificador.comodin(productos, 10.5).producto_id == -96
    assert clasificador.comodin(productos, 27.0).producto_id == 1004  # sin comodín de 27: GASTOS VARIOS


# --- Clasificación ------------------------------------------------------------------


def test_gemini_elige_y_recibe_solo_candidatos(datos):
    vistos = []

    def consultor(prompt):
        vistos.append(prompt)
        return RespuestaGemini(producto_id=1001, confianza=0.92, motivo="Estudio contable")

    a = clasificador.clasificar_linea("Acme", _linea(proveedor_nombre="ESTUDIO PEREZ"), consultor=consultor)
    assert (a.producto_id, a.origen, a.confianza, a.requiere_revision) == (1001, "gemini", 0.92, False)
    assert a.nombre == "HONORARIOS  PROFESIONALES "
    prompt = vistos[0]
    assert "ID -97: Producto al 21%" in prompt and "ID 1002" not in prompt and "ID -96" not in prompt
    assert "Alícuota de IVA detectada en la línea: 21%" in prompt and "Importe neto: $1.234,50" in prompt
    assert "CUIT: 30639453738" in prompt


def test_id_inventado_va_al_comodin(datos):
    a = clasificador.clasificar_linea("Acme", _linea(), consultor=lambda p: RespuestaGemini(producto_id=1002, confianza=0.99, motivo="x"))
    assert (a.producto_id, a.origen, a.confianza, a.requiere_revision) == (-97, "comodin", 0.0, True)


def test_baja_confianza_pide_revision(datos):
    a = clasificador.clasificar_linea("Acme", _linea(), consultor=lambda p: RespuestaGemini(producto_id=-97, confianza=0.4, motivo="ambiguo"))
    assert a.origen == "gemini" and a.requiere_revision


def test_gemini_caido_va_al_comodin(datos):
    def falla(prompt):
        raise ConnectionError("sin internet")

    a = clasificador.clasificar_linea("Acme", _linea(alicuota_iva=10.5), consultor=falla)
    assert (a.producto_id, a.origen) == (-96, "comodin")


def test_sin_clave_va_al_comodin(datos, monkeypatch):
    monkeypatch.setattr(gemini, "disponible", lambda: False)
    a = clasificador.clasificar_linea("Acme", _linea())
    assert a.origen == "comodin" and a.producto_id == -97 and "GEMINI_API_KEY" in a.motivo


def test_correccion_manual_crea_regla_y_gana(datos):
    def no_llamar(prompt):
        raise AssertionError("con regla no se consulta a Gemini")

    regla = clasificador.corregir("Acme", _linea(), 1004)
    assert regla.cuit_proveedor == "30639453738" and regla.alicuota_iva == 21.0
    a = clasificador.clasificar_linea("Acme", _linea(), consultor=no_llamar)
    assert (a.producto_id, a.origen, a.confianza, a.requiere_revision) == (1004, "regla_cuit", 1.0, False)
    assert almacen.reglas("Acme")[0].usos == 1
    # La regla es por alícuota: la línea al 10,5 % del mismo proveedor va a Gemini.
    b = clasificador.clasificar_linea("Acme", _linea(alicuota_iva=10.5), consultor=lambda p: RespuestaGemini(producto_id=-96, confianza=0.5, motivo=""))
    assert b.origen == "gemini"


def test_correccion_con_producto_ajeno_falla(datos):
    with pytest.raises(ValueError):
        clasificador.corregir("Acme", _linea(), 999999)


def test_regla_huerfana_se_ignora(datos):
    almacen.guardar_regla("Acme", "30639453738", 21.0, 555)
    a = clasificador.clasificar_linea("Acme", _linea(), consultor=lambda p: RespuestaGemini(producto_id=1001, confianza=0.8, motivo=""))
    assert a.origen == "gemini"


def test_lineas_repetidas_una_sola_consulta(datos):
    llamadas = []

    def consultor(prompt):
        llamadas.append(prompt)
        return RespuestaGemini(producto_id=1003, confianza=0.9, motivo="YPF")

    lineas = [_linea(descripcion="Nafta super"), _linea(descripcion="NAFTA SUPER "), _linea(descripcion="Lubricante")]
    salida = clasificador.clasificar_lineas("Acme", lineas, consultor=consultor)
    assert [a.producto_id for a in salida] == [1003, 1003, 1003] and len(llamadas) == 2


def test_sin_catalogo(datos):
    a = clasificador.clasificar_linea("Otra", _linea(), consultor=lambda p: None)
    assert a.origen == "sin_catalogo" and a.producto_id is None


# --- Llamada a Gemini (cliente falso) -----------------------------------------------


def test_consultar_gemini_usa_esquema_y_cae_al_modelo_del_conversor(monkeypatch):
    from google import genai
    from google.genai import errors

    pedidos = []

    class Modelos:
        def generate_content(self, model, contents, config):
            pedidos.append((model, config))
            if model == "gemini-2.5-flash":
                raise errors.APIError(404, {"error": {"message": "not found"}})

            class R:
                parsed = RespuestaGemini(producto_id=7, confianza=0.8, motivo="ok")
                text = ""
            return R()

    class Cliente:
        def __init__(self, **kw):
            self.models = Modelos()

    monkeypatch.setattr(genai, "Client", Cliente)
    monkeypatch.delenv("GEMINI_MODELO_CLASIFICADOR", raising=False)
    monkeypatch.setattr(gemini, "modelo", lambda: "gemini-3.8-flash")
    r = clasificador.consultar_gemini("hola")
    assert r.producto_id == 7
    assert [m for m, _ in pedidos] == ["gemini-2.5-flash", "gemini-3.8-flash"]
    cfg = pedidos[0][1]
    assert cfg.response_mime_type == "application/json" and cfg.temperature == 0.1 and cfg.response_schema is RespuestaGemini


# --- API ----------------------------------------------------------------------------


def test_api_catalogo_clasificar_y_corregir(cliente_demo, monkeypatch):
    client = cliente_demo
    monkeypatch.setattr(gemini, "disponible", lambda: True)
    monkeypatch.setattr(clasificador, "consultar_gemini", lambda p: RespuestaGemini(producto_id=1001, confianza=0.6, motivo="dudoso"))

    r = client.put("/api/imputacion/Acme/catalogo", json={"xml": XML})
    assert r.status_code == 200, r.text
    assert r.json()["cantidad"] == 8 and r.json()["genericos"] == 4
    assert client.get("/api/imputacion/Acme/catalogo").json()["cantidad"] == 8
    assert client.put("/api/imputacion/Acme/catalogo", json={"xml": "<root/>"}).status_code == 422
    assert client.get("/api/imputacion/Nadie/catalogo").status_code == 404

    linea = _linea().model_dump()
    r = client.post("/api/imputacion/Acme/clasificar", json={"lineas": [linea]})
    assert r.status_code == 200 and r.json()[0]["origen"] == "gemini" and r.json()[0]["requiere_revision"]

    r = client.put("/api/imputacion/Acme/reglas", json={"proveedor_cuit": linea["proveedor_cuit"], "alicuota_iva": 21, "producto_id": 1004})
    assert r.status_code == 200, r.text
    r = client.post("/api/imputacion/Acme/clasificar", json={"lineas": [linea]})
    assert r.json()[0]["producto_id"] == 1004 and r.json()[0]["origen"] == "regla_cuit"
    assert client.put("/api/imputacion/Acme/reglas", json={"proveedor_cuit": "30639453738", "producto_id": 5}).status_code == 422

    assert len(client.get("/api/imputacion/Acme/reglas").json()) == 1
    assert client.delete("/api/imputacion/Acme/reglas", params={"proveedor_cuit": "30639453738", "alicuota_iva": 21}).status_code == 204
    assert client.get("/api/imputacion/Acme/reglas").json() == []
