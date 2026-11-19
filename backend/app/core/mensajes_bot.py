"""Errores técnicos de los bots, en palabras de quien usa la app.

Playwright tira mensajes como «Page.click: Target page, context or browser has
been closed», que llegaban tal cual a la pantalla del empleado. Acá se traducen a
qué pasó y qué hacer. El mensaje original sigue en el registro del control.

Un mensaje que ya está en castellano (los que arman los bots) vuelve igual.
"""

from __future__ import annotations

import re

_CERRADO = (
    "target page, context or browser has been closed",
    "browser has been closed",
    "target closed",
    "browser closed",
    "connection closed",
)
_SIN_RED = (
    "net::err_internet_disconnected",
    "net::err_name_not_resolved",
    "net::err_connection",
    "net::err_network",
    "net::err_timed_out",
    "net::err_address_unreachable",
)
# Playwright con channel="chrome" y la PC sin Google Chrome:
# «Chromium distribution 'chrome' is not found at C:\...\chrome.exe».
_SIN_CHROME = ("distribution 'chrome' is not found",)
_SIN_NAVEGADOR = ("executable doesn't exist", "playwright install")
_TIMEOUT = re.compile(r"timeout (\d+)\s*ms exceeded", re.IGNORECASE)
# «Page.click: …», «Locator.fill: …»: una llamada de Playwright que falló.
_API_PLAYWRIGHT = re.compile(
    r"^\s*(page|locator|frame|elementhandle|browsercontext|browser|keyboard|mouse|response|request)\.\w+:",
    re.IGNORECASE,
)


def mensaje_legible(texto: str, portal: str | None = None) -> str:
    """El mensaje para la pantalla. `portal`: «ARCA» o «Xubio», si se sabe."""
    if not texto:
        return texto
    m = texto.lower()
    sitio = portal or "la página"
    if any(c in m for c in _CERRADO):
        return (
            "Se cerró la ventana de Chrome del bot antes de terminar "
            "(la cerraron a mano o Chrome se cortó). Volvé a correr el control."
        )
    if any(c in m for c in _SIN_RED):
        return (
            f"No hubo conexión con {sitio}: revisá internet en la PC del servidor "
            "y volvé a correr el control."
        )
    if any(c in m for c in _SIN_CHROME):
        return (
            "Esta PC no tiene Google Chrome instalado y los bots lo necesitan. "
            "Instalalo desde google.com/chrome y volvé a correr el control."
        )
    if any(c in m for c in _SIN_NAVEGADOR):
        return (
            "No se encontró el navegador del bot en la PC del servidor: hay que "
            "instalarlo (playwright install chrome)."
        )
    if t := _TIMEOUT.search(texto):
        segundos = max(1, round(int(t.group(1)) / 1000))
        return (
            f"{sitio[:1].upper() + sitio[1:]} no respondió a tiempo (se esperó {segundos} s). "
            "Puede estar lenta: volvé a correr el control en unos minutos."
        )
    if _API_PLAYWRIGHT.match(texto):
        return (
            f"El bot no encontró en {sitio} lo que esperaba en la pantalla (la página "
            "cambió o tardó en cargar). Volvé a correr el control; si se repite, avisá."
        )
    return texto


def portal_de_paso(paso_id_o_titulo: str) -> str | None:
    """«arca_ventas» / «ARCA · Ventas: falló» -> «ARCA»; lo mismo con Xubio."""
    t = paso_id_o_titulo.lower()
    if t.startswith("arca"):
        return "ARCA"
    if t.startswith("xubio"):
        return "Xubio"
    return None
