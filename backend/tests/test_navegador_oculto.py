"""Los bots corren ocultos y con resolución fija; «visible» lo puede forzar."""

from backend.app.bots import navegador
from backend.app.core import config


def test_oculto_por_defecto_con_tamano_fijo(monkeypatch):
    monkeypatch.setattr(config, "PLAYWRIGHT_HEADLESS", True)
    monkeypatch.setattr(config, "PLAYWRIGHT_VIEWPORT", (1366, 768))
    op = navegador.opciones_lanzamiento(maximizado=True)
    assert op["headless"] is True
    assert "--window-size=1366,768" in op["args"]
    assert "--start-maximized" not in op["args"]


def test_visible_fuerza_la_ventana(monkeypatch):
    monkeypatch.setattr(config, "PLAYWRIGHT_HEADLESS", True)
    op = navegador.opciones_lanzamiento(visible=True)
    assert op["headless"] is False
    assert not any(a.startswith("--window-size") for a in op["args"])


def test_contexto_con_viewport_fijo(monkeypatch):
    monkeypatch.setattr(config, "PLAYWRIGHT_VIEWPORT", (1280, 800))
    pedidos = {}

    class Navegador:
        def new_context(self, **opciones):
            pedidos.update(opciones)
            raise RuntimeError("alcanza")

    try:
        navegador.nuevo_contexto(Navegador())
    except RuntimeError:
        pass
    assert pedidos["viewport"] == {"width": 1280, "height": 800}


def test_tamano_invalido_usa_el_de_siempre():
    assert config._tamano("abc", (1366, 768)) == (1366, 768)
    assert config._tamano("1920x1080", (1366, 768)) == (1920, 1080)
