"""Los errores técnicos de Playwright no llegan crudos a la pantalla."""

from __future__ import annotations

import pytest

from backend.app.core.mensajes_bot import mensaje_legible, portal_de_paso
from backend.app.services.ejecuciones import Ejecucion, _acciones_de_paso, Paso


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        ("Page.click: Target page, context or browser has been closed", "Se cerró la ventana de Chrome"),
        ("Locator.fill: Target closed", "Se cerró la ventana de Chrome"),
        ("Page.goto: net::ERR_INTERNET_DISCONNECTED at https://xubio.com", "No hubo conexión con Xubio"),
        ("Page.wait_for_selector: Timeout 30000ms exceeded.", "Xubio no respondió a tiempo (se esperó 30 s)"),
        ("BrowserType.launch: Executable doesn't exist at C:\\x\\chrome.exe", "No se encontró el navegador"),
        ("Locator.click: Element is not visible", "El bot no encontró en Xubio lo que esperaba"),
    ],
)
def test_traduce_los_errores_de_playwright(crudo, esperado):
    texto = mensaje_legible(crudo, "Xubio")
    assert esperado in texto
    assert "Page." not in texto and "Locator." not in texto


def test_los_mensajes_en_castellano_quedan_igual():
    propio = "ARCA no respondió: la página no cargó en dos intentos de 60 s."
    assert mensaje_legible(propio, "ARCA") == propio
    assert mensaje_legible("", "ARCA") == ""


def test_sin_portal_habla_de_la_pagina():
    assert mensaje_legible("Page.click: Timeout 5000ms exceeded.").startswith("La página no respondió a tiempo")


def test_portal_de_paso():
    assert portal_de_paso("arca_ventas") == "ARCA"
    assert portal_de_paso("ARCA · Compras: falló") == "ARCA"
    assert portal_de_paso("xubio") == "Xubio"
    assert portal_de_paso("auditoria_ventas") is None


def test_un_timeout_traducido_sigue_sugiriendo_volver_a_correr():
    paso = Paso("xubio", "Xubio · Ventas", "error", mensaje_legible("Page.click: Timeout 30000ms exceeded.", "Xubio"))
    assert _acciones_de_paso(paso) == ("Xubio no respondió a tiempo.", ["Volver a correr"])


def test_un_control_guardado_con_el_error_crudo_se_ve_traducido():
    crudo = "Page.click: Target page, context or browser has been closed"
    ej = Ejecucion.desde_guardado(
        {
            "id": "abc",
            "parametros": {},
            "estado": "finalizada",
            "clientes": [
                {"nombre": "Acme", "pasos": [{"id": "xubio", "etiqueta": "Xubio · Ventas", "estado": "error", "mensaje": crudo}]}
            ],
            "advertencias": [
                {"tipo": "paso_error", "titulo": "Xubio · Ventas: falló", "detalle": crudo},
                {"tipo": "duplicado", "titulo": "Duplicado", "detalle": crudo},
            ],
        }
    )
    assert ej.clientes[0].pasos[0].mensaje.startswith("Se cerró la ventana de Chrome")
    assert ej.advertencias[0]["detalle"].startswith("Se cerró la ventana de Chrome")
    # Solo se tocan los hallazgos de pasos que fallaron.
    assert ej.advertencias[1]["detalle"] == crudo


def test_el_filtro_de_logs_no_rompe_el_registro_de_accesos_de_uvicorn():
    """Con ``args = None`` el AccessFormatter de uvicorn tiraba «Logging error» en cada pedido."""
    import logging

    from uvicorn.logging import AccessFormatter

    from backend.app.core.saneo import FiltroSaneado

    registro = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/api/ejecuciones", "1.1", 200), None,
    )
    assert FiltroSaneado().filter(registro)
    assert "GET /api/ejecuciones" in AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s').format(registro)


def test_arca_lenta_en_el_login_no_manda_a_corregir_el_cuit_primero():
    paso = Paso(
        "arca_ventas", "ARCA · Ventas", "error",
        "No se pudo iniciar sesión en ARCA: no apareció el campo de la clave (después del CUIT) en 45 s "
        "(puede ser un CUIT o una clave fiscal incorrectos, o ARCA muy lenta).",
    )
    detalle, acciones = _acciones_de_paso(paso)
    assert acciones == ["Volver a correr", "Revisar clave de ARCA"]
    assert "puede estar lenta" in detalle


def test_un_control_guardado_se_vuelve_a_diagnosticar_al_cargarlo():
    lento = (
        "No se pudo iniciar sesión en ARCA: no apareció el campo de la clave (después del CUIT) en 45 s "
        "(puede ser un CUIT o una clave fiscal incorrectos, o ARCA muy lenta)."
    )
    ej = Ejecucion.desde_guardado({
        "id": "x1", "parametros": {}, "estado": "finalizada",
        "clientes": [{"nombre": "Acme", "pasos": [{"id": "arca_ventas", "etiqueta": "ARCA · Ventas", "estado": "error", "mensaje": lento}]}],
        "advertencias": [{
            "tipo": "paso_error", "gravedad": "grave", "cliente": "Acme", "titulo": "ARCA · Ventas: falló",
            "detalle": "No se pudo entrar a ARCA.", "acciones": ["Corregir clave de ARCA"],
        }],
    })
    assert ej.advertencias[0]["acciones"] == ["Volver a correr", "Revisar clave de ARCA"]
