"""El bot de «Subir a Xubio» contra una copia falsa de la pantalla de Xubio.

NUNCA entra a Xubio: Chrome corre oculto y cada pedido lo contesta esta prueba
(`page.route`), con una página que imita el camino «Compras > Comprobantes de
Compra > Acciones > Importar > Desde Mis Comprobantes (ARCA)» y el iframe de la
importación. Sirve para ver que el bot aprieta «Importar», acepta la
confirmación y lee el resultado. Si esta PC no tiene Chrome, se saltea.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from backend.app.bots import xubio

PRINCIPAL = """<!doctype html><html><body>
<nav><a href="#" id="menu">Compras</a></nav>
<div id="sub" hidden><a href="#" id="listado">Comprobantes de Compra</a></div>
<div id="pantalla" hidden>
  <button id="acciones">Acciones</button>
  <div id="acc" hidden><a href="#" id="importar">Importar</a>
    <ul id="opciones" hidden><li id="desde">Desde Mis Comprobantes (ARCA)</li></ul></div>
  <div id="lugar"></div>
</div>
<script>
  menu.onclick = () => { sub.hidden = false; };
  listado.onclick = () => { pantalla.hidden = false; };
  acciones.onclick = () => { acc.hidden = false; };
  importar.onclick = () => { opciones.hidden = false; };
  desde.onclick = () => {
    lugar.innerHTML = '<iframe src="https://app.xubio.test/importacion-csv-afip/1" style="width:600px;height:400px"></iframe>';
  };
</script></body></html>"""

# RESULTADO lo reemplaza cada prueba: lo que «Xubio» contesta al importar.
IFRAME = """<!doctype html><html><body>
<input type="file" id="archivo">
<p id="nombre"></p>
<button id="importar" disabled>Importar</button>
<div id="modal" role="dialog" hidden>¿Desea importar los comprobantes? <button id="si">Aceptar</button></div>
<div id="res" class="alert" hidden></div>
<script>
  archivo.onchange = () => { nombre.textContent = archivo.files[0].name; importar.disabled = false; };
  importar.onclick = () => { modal.hidden = false; };
  si.onclick = () => {
    modal.hidden = true;
    setTimeout(() => { res.textContent = 'RESULTADO'; res.hidden = false; }, 400);
  };
</script></body></html>"""


def _servir(page, resultado: str) -> None:
    def contestar(route):
        url = route.request.url
        if "importacion-csv-afip" in url:
            route.fulfill(status=200, content_type="text/html", body=IFRAME.replace("RESULTADO", resultado))
        elif url.startswith("https://xubio.test/"):
            route.fulfill(status=200, content_type="text/html", body=PRINCIPAL)
        else:
            route.abort()  # nada sale a internet

    page.route("**/*", contestar)


@pytest.fixture
def pagina():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:
            pytest.skip(f"Sin Chrome para la prueba: {exc}")
        page = browser.new_page()
        yield page
        browser.close()


def _zip(tmp_path: Path) -> str:
    ruta = tmp_path / "Mis Comprobantes Compras - Acme - 10-26.zip"
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("Mis Comprobantes Compras - Acme - 10-26.csv", "x")
    return str(ruta)


def test_aprieta_importar_confirma_y_lee_el_resultado(pagina, tmp_path):
    _servir(pagina, "Se importaron 2 comprobantes correctamente.")
    pagina.goto("https://xubio.test/NXV/")
    registro: list[str] = []
    r = xubio.importar_arca(pagina, "Compras", _zip(tmp_path), log=registro.append, espera_ms=15_000)
    assert r["archivo_cargado"] and r["boton_importar_habilitado"]
    assert r["importado"] is True
    assert r["mensaje"] == "Se importaron 2 comprobantes correctamente."
    assert any("pidió confirmar" in linea for linea in registro)


def test_si_xubio_da_error_lo_dice(pagina, tmp_path):
    _servir(pagina, "Error: el archivo tiene comprobantes que ya existen.")
    pagina.goto("https://xubio.test/NXV/")
    r = xubio.importar_arca(pagina, "Compras", _zip(tmp_path), log=lambda _: None, espera_ms=15_000)
    assert r["importado"] is False and "ya existen" in r["mensaje"]


def test_respuesta_desconocida_no_se_da_por_buena(pagina, tmp_path):
    _servir(pagina, "Procesando lote 4471")
    pagina.goto("https://xubio.test/NXV/")
    r = xubio.importar_arca(pagina, "Compras", _zip(tmp_path), log=lambda _: None, espera_ms=4_000)
    assert r["importado"] is None and r["mensaje"] == "Procesando lote 4471"
