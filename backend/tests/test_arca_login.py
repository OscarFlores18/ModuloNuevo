"""El login de ARCA explica por qué no entra, en vez de un timeout de Playwright."""

from __future__ import annotations

import pytest

from backend.app.bots import arca
from backend.app.bots.navegador import _necesita_imagenes
from backend.app.core.errors import LoginError


class _PaginaFalsa:
    """Una pantalla de ARCA: el selector aparece después de `aparece_en` consultas."""

    def __init__(self, pantalla: dict, aparece_en: int | None = None) -> None:
        self.pantalla = pantalla
        self.aparece_en = aparece_en
        self.consultas = 0

    def wait_for_selector(self, selector, state=None, timeout=None):
        self.consultas += 1
        if self.aparece_en is not None and self.consultas >= self.aparece_en:
            return
        raise TimeoutError("no está")

    def evaluate(self, _js):
        return self.pantalla


@pytest.fixture
def sesion(monkeypatch):
    # Sin esperas reales: el reloj avanza 10 s por consulta.
    reloj = {"t": 0.0}

    def ahora():
        reloj["t"] += 10
        return reloj["t"]

    monkeypatch.setattr(arca.time, "monotonic", ahora)
    s = object.__new__(arca.SesionArca)
    s.registro = []
    s.log = s.registro.append
    return s


def test_mensaje_de_error_de_arca_corta_enseguida(sesion):
    pagina = _PaginaFalsa({"mensajes": ["Clave o usuario incorrecto"], "texto": "Ingresar con Clave Fiscal"})
    with pytest.raises(LoginError) as e:
        sesion._esperar_login(pagina, "id=F1:password", "el campo de la clave")
    assert "Clave o usuario incorrecto" in e.value.mensaje
    assert pagina.consultas == 1


def test_sin_respuesta_dice_que_mostraba_la_pantalla(sesion):
    pagina = _PaginaFalsa({"mensajes": [], "texto": "Estamos procesando tu solicitud"})
    with pytest.raises(LoginError) as e:
        sesion._esperar_login(pagina, "#buscadorInput", "el portal")
    assert "no apareció el portal" in e.value.mensaje
    assert "Estamos procesando tu solicitud" in e.value.mensaje
    assert "Timeout" not in e.value.mensaje


def test_captcha_avisa_y_espera_a_la_persona(sesion, monkeypatch):
    # Con la ventana a la vista (MODULONUEVO_HEADLESS=0) se espera a la persona.
    monkeypatch.setattr(arca.config, "PLAYWRIGHT_HEADLESS", False)
    # Con captcha se espera más que los 45 s normales: aparece a la consulta 8 (80 s).
    pagina = _PaginaFalsa({"mensajes": [], "captcha": True, "texto": "No soy un robot"}, aparece_en=8)
    sesion._esperar_login(pagina, "id=F1:password", "el campo de la clave")
    assert any("captcha" in linea for linea in sesion.registro)
    assert any("[OK]" in linea for linea in sesion.registro)


def test_captcha_con_navegador_oculto_corta_y_explica(sesion, monkeypatch):
    # Oculto nadie puede completar el captcha: se corta enseguida, sin esperar 3 minutos.
    monkeypatch.setattr(arca.config, "PLAYWRIGHT_HEADLESS", True)
    pagina = _PaginaFalsa({"mensajes": [], "captcha": True, "texto": "No soy un robot"}, aparece_en=8)
    with pytest.raises(LoginError, match="oculto"):
        sesion._esperar_login(pagina, "id=F1:password", "el campo de la clave")


def test_un_cartel_sin_palabras_de_error_no_corta(sesion):
    pagina = _PaginaFalsa({"mensajes": ["Bienvenido"], "texto": ""}, aparece_en=2)
    sesion._esperar_login(pagina, "#buscadorInput", "el portal")


def test_el_error_lleva_a_corregir_la_clave():
    from backend.app.services.ejecuciones import Paso, _acciones_de_paso

    paso = Paso("arca_compras", "ARCA · Compras", "error", "ARCA no dejó entrar: «Clave incorrecta». Revisá el CUIT de ingreso y la clave fiscal del cliente.")
    _, acciones = _acciones_de_paso(paso)
    assert acciones == ["Corregir clave de ARCA"]


@pytest.mark.parametrize(
    "url, esperado",
    [
        ("https://auth.afip.gob.ar/contribuyente_/resources/img/logo.png", True),
        ("https://www.google.com/recaptcha/api2/payload?p=1", True),
        ("https://www.gstatic.com/recaptcha/img.png", True),
        ("https://portalcf.cloud.afip.gob.ar/portal/img/banner.png", False),
        ("https://xubio.com/NXV/img/logo.png", False),
    ],
)
def test_imagenes_solo_en_login_y_captcha(url, esperado):
    assert _necesita_imagenes(url) is esperado
