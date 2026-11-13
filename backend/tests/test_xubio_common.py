from __future__ import annotations

from backend.app.bots.xubio_common import (
    _coincide,
    _link_compania_en_dom,
    _normalizar,
    cambiar_empresa,
)


def test_normalizar_ignora_acentos_puntuacion_y_sufijos():
    assert _normalizar("Estudio: PICCININI y ASOCIADOS S.A.") == (
        "estudio piccinini y asociados"
    )
    assert _normalizar("Acme S.R.L.") == "acme"
    assert _normalizar("ACME  SRL") == "acme"


def test_coincide_nombre_completo_y_abreviado():
    # El nombre de config.json puede ser parte del nombre real de Xubio.
    assert _coincide("Cliente Prueba", "CLIENTE PRUEBA S.R.L.")
    assert _coincide("COSENZA PEDRO HECTOR", "Cosenza Pedro Hector")
    assert _coincide("Acme S.A.", "ACME")


def test_coincide_tolera_un_typo_chico():
    assert _coincide("Piccinini y Asociados", "PICCININI y ASOCIDOS")


def test_coincide_no_confunde_empresas_distintas():
    assert not _coincide("ClientePruebaSRL", "COSENZA PEDRO HECTOR")
    assert not _coincide("Acme", "Beta")
    assert not _coincide("", "Acme")


# --- Selector de empresa: la lista del DOM alcanza aunque el menú no abra ----
# Regresión del diagnóstico 2026-11-13: el desplegable dejó de abrirse con
# hover/click, pero #compania-lista seguía en el DOM con los href de Relogin.

EMPRESAS = [
    ("COSENZA PEDRO HECTOR", "/NXV/Relogin?tenant=tnt_1"),
    ("PICCININI y ASOCIADOS S.A.", "/NXV/Relogin?tenant=tnt_14765"),
    ("ESBA FLORIDA S.A.", "/NXV/Relogin?tenant=tnt_216267"),
]


class _OpcionFalsa:
    """Link de una empresa: oculto (menú cerrado) pero legible por text_content."""

    def __init__(self, texto: str, href: str):
        self._texto = texto
        self._href = href

    def text_content(self) -> str:
        return self._texto

    def inner_text(self, timeout: int | None = None) -> str:
        raise AssertionError("inner_text no sirve con el menú cerrado")

    def is_visible(self) -> bool:
        return False

    def get_attribute(self, nombre: str):
        return self._href if nombre == "href" else None

    def click(self, timeout: int = 0):
        raise AssertionError("el camino directo no debe clickear la opción")


class _ListaFalsa:
    def __init__(self, opciones):
        self._opciones = opciones

    def count(self) -> int:
        return len(self._opciones)

    def nth(self, i: int):
        return self._opciones[i]


class _NombreFalso:
    """#resaltarCompania: devuelve la empresa activa actual de la página."""

    def __init__(self, pagina):
        self._pagina = pagina

    @property
    def first(self):
        return self

    def inner_text(self, timeout: int | None = None) -> str:
        return self._pagina.empresa


class _MouseFalso:
    def move(self, x, y):
        pass


class _PaginaFalsa:
    url = "https://xubio.com/NXV/"

    def __init__(self, empresa: str = "COSENZA PEDRO HECTOR"):
        self.empresa = empresa
        self.destino: str | None = None
        self.selectores: list[str] = []
        self.mouse = _MouseFalso()

    def wait_for_selector(self, selector, state=None, timeout=None):
        return self.locator(selector)

    def locator(self, selector):
        self.selectores.append(selector)
        if selector == "#resaltarCompania":
            return _NombreFalso(self)
        if selector == "#compania-lista li.licomp a":
            return _ListaFalsa([_OpcionFalsa(t, h) for t, h in EMPRESAS])
        raise AssertionError(f"selector inesperado: {selector}")

    # Navegar a /NXV/Relogin?tenant=... deja esa empresa como activa.
    def goto(self, url: str):
        self.destino = url
        self.empresa = "PICCININI y ASOCIADOS S.A."

    def wait_for_load_state(self, estado, timeout=None):
        pass

    def wait_for_timeout(self, ms):
        pass


def test_encuentra_el_link_aunque_el_menu_este_cerrado():
    pagina = _PaginaFalsa()

    enlace = _link_compania_en_dom(pagina, "PICCININI y ASOCIADOS S.A.")

    assert enlace is not None
    assert enlace.get_attribute("href") == "/NXV/Relogin?tenant=tnt_14765"


def test_sin_empresa_en_la_lista_devuelve_none():
    pagina = _PaginaFalsa()

    assert _link_compania_en_dom(pagina, "ClientePruebaSRL") is None


def test_cambiar_empresa_usa_el_link_sin_abrir_el_menu():
    pagina = _PaginaFalsa()
    pasos: list[str] = []

    cambiar_empresa(pagina, "PICCININI y ASOCIADOS S.A.", lambda: None, pasos.append)

    assert pagina.destino == "https://xubio.com/NXV/Relogin?tenant=tnt_14765"
    assert pagina.empresa == "PICCININI y ASOCIADOS S.A."
    # No se intentó abrir el desplegable ni tocar el buscador.
    assert "#buscador-empresa" not in pagina.selectores
    assert any("Empresa activa" in paso for paso in pasos)

