"""Navegador endurecido para los bots.

- **Silencio**: Chromium se lanza sin logging propio, sin extensiones y sin
  reporte de fallos, y con un entorno limpio: no hereda ``VAULT_TOKEN``,
  ``MODULONUEVO_*`` ni ``DEBUG``.
- **Sin artefactos**: los contextos se crean sin video, sin HAR y sin
  tracing; pedir esas opciones es un error. Durante el login
  (:func:`fase_login`) cualquier captura queda bloqueada.
- **Filtro de salida**: cada pedido del navegador pasa por una lista blanca de
  dominios (``config.EGRESS_DOMINIOS``). Lo demás se aborta y se avisa una vez
  por dominio.
- **Credenciales JIT**: :func:`como_fuente` convierte lo que recibe el bot en
  una función que entrega un :class:`Secreto` en el momento de inyectarlo.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Callable, Iterator
from urllib.parse import urlsplit

from ..core import config
from ..core.logging import get_logger
from ..core.secreto import Secreto

FuenteSecreto = Callable[[], Secreto]

ARGS_SILENCIO = (
    "--disable-logging",
    "--log-level=3",
    "--disable-extensions",
    "--disable-component-extensions-with-background-pages",
    "--disable-crash-reporter",
    "--disable-breakpad",
    "--disable-background-networking",
    "--disable-sync",
    "--metrics-recording-only",
    "--no-default-browser-check",
    "--no-first-run",
    "--password-store=basic",
    "--disable-features=AutofillServerCommunication,PasswordManagerOnboarding",
)

_PROHIBIDAS_CONTEXTO = ("record_video_dir", "record_video_size", "record_har_path", "record_har_content")
_PREFIJOS_ENTORNO_PRIVADO = ("MODULONUEVO_", "VAULT_", "AWS_", "AZURE_", "DEBUG")


# --- Lanzamiento --------------------------------------------------------------------


def entorno_navegador() -> dict[str, str]:
    """El entorno del proceso sin secretos ni variables de depuración."""
    return {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(_PREFIJOS_ENTORNO_PRIVADO)
    }


def opciones_lanzamiento(*, maximizado: bool = False) -> dict[str, object]:
    opciones: dict[str, object] = {
        "headless": config.PLAYWRIGHT_HEADLESS,
        "args": list(ARGS_SILENCIO) + (["--start-maximized"] if maximizado else []),
        "env": entorno_navegador(),
    }
    if config.PLAYWRIGHT_CHANNEL:
        opciones["channel"] = config.PLAYWRIGHT_CHANNEL
    return opciones


def chrome_disponible() -> bool:
    """False si los bots usan Google Chrome (channel="chrome") y esta PC no lo
    tiene: así la app avisa antes de correr un control que va a fallar."""
    if config.PLAYWRIGHT_CHANNEL != "chrome":
        return True
    if os.name != "nt":
        import shutil

        return any(shutil.which(n) for n in ("google-chrome", "google-chrome-stable"))
    bases = (
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    )
    return any(
        os.path.isfile(os.path.join(b, "Google", "Chrome", "Application", "chrome.exe"))
        for b in bases
        if b
    )


def lanzar(pw, *, maximizado: bool = False):
    return pw.chromium.launch(**opciones_lanzamiento(maximizado=maximizado))


# --- Contexto -----------------------------------------------------------------------


def _permitido(url: str) -> bool:
    partes = urlsplit(url)
    if partes.scheme in ("data", "blob", "about", "chrome-error"):
        return True
    host = (partes.hostname or "").lower()
    camino = partes.path or "/"
    for regla in config.EGRESS_DOMINIOS + config.EGRESS_EXTRA:
        dominio, _, prefijo = regla.partition("/")
        if host == dominio or host.endswith("." + dominio):
            if not prefijo or camino.startswith("/" + prefijo):
                return True
    return False


# Recursos que los bots no necesitan para clickear ni descargar: no bajarlos
# acelera cada carga de página de ARCA y Xubio.
RECURSOS_PESADOS = frozenset({"image", "media"})

# Donde las imágenes sí hacen falta: el login de ARCA y de Xubio y los captchas
# («No soy un robot» muestra fotos). Sin ellas el captcha no se puede resolver
# y el login queda trabado.
HOSTS_CON_IMAGENES = (
    "auth.afip.gob.ar",
    "connect.visma.com",
    "google.com",
    "gstatic.com",
    "recaptcha.net",
    "challenges.cloudflare.com",
)


def _url_del_marco(request) -> str:
    """La página que pidió el recurso (el captcha pide fotos desde su iframe)."""
    try:
        return request.frame.url or ""
    except Exception:
        return ""


def _necesita_imagenes(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in HOSTS_CON_IMAGENES)


def _instalar_filtro_salida(context, log: Callable[[str], None]) -> None:
    modo = config.EGRESS_MODO
    filtrar = modo not in ("off", "0", "no")
    aligerar = config.BLOQUEAR_RECURSOS
    if not filtrar and not aligerar:
        return
    avisados: set[str] = set()
    lock = threading.Lock()

    def manejar(route) -> None:
        url = route.request.url
        if (
            aligerar
            and route.request.resource_type in RECURSOS_PESADOS
            and not _necesita_imagenes(url)
            and not _necesita_imagenes(_url_del_marco(route.request))
        ):
            route.abort("blockedbyclient")
            return
        if not filtrar or _permitido(url):
            route.continue_()
            return
        host = urlsplit(url).hostname or "?"
        with lock:
            nuevo = host not in avisados
            avisados.add(host)
        if nuevo:
            # Solo al log técnico: son trackers y widgets de las páginas
            # (googletagmanager, facebook...) y en el registro del control eran ruido.
            accion = "bloqueó" if modo == "bloquear" else "detectó"
            get_logger("modulonuevo.filtro").info(
                "El filtro de salida %s una conexión a «%s» (no está en la lista blanca).",
                accion,
                host,
            )
        if modo == "bloquear":
            route.abort("blockedbyclient")
        else:
            route.continue_()

    context.route("**/*", manejar)


def nuevo_contexto(browser, log: Callable[[str], None] = print, **opciones):
    """Contexto sin video/HAR/tracing, con service workers bloqueados (así el
    filtro de salida ve todos los pedidos) y con la lista blanca instalada."""
    pedidas = [k for k in _PROHIBIDAS_CONTEXTO if opciones.get(k)]
    if pedidas:
        raise ValueError(
            "Los bots no pueden grabar video ni HAR: " + ", ".join(pedidas)
        )
    for k in _PROHIBIDAS_CONTEXTO:
        opciones.pop(k, None)
    opciones.setdefault("service_workers", "block")
    context = browser.new_context(**opciones)
    _instalar_filtro_salida(context, log)
    return context


# --- Fase de login --------------------------------------------------------------------

_estado = threading.local()


@contextmanager
def fase_login() -> Iterator[None]:
    """Mientras dura, ninguna captura de pantalla está permitida."""
    previo = getattr(_estado, "login", 0)
    _estado.login = previo + 1
    try:
        yield
    finally:
        _estado.login = previo


def capturas_permitidas() -> bool:
    return config.CAPTURAS_DIAGNOSTICO and not getattr(_estado, "login", 0)


# --- Credenciales -----------------------------------------------------------------------


def como_fuente(valor: "str | Secreto | FuenteSecreto | None") -> tuple[FuenteSecreto, bool]:
    """(fuente, es_jit).

    - Si ``valor`` es invocable (una ``FuenteCredencial``), cada llamada trae un
      Secreto nuevo que el bot borra después de inyectarlo (``es_jit=True``).
    - Si es ``str``/``Secreto``, se guarda una copia en un Secreto que el bot
      borra al cerrar la sesión (``es_jit=False``).
    """
    if callable(valor) and not isinstance(valor, Secreto):
        return valor, True
    guardado = Secreto(valor) if not isinstance(valor, Secreto) else valor.copia()
    return (lambda: guardado), False


def inyectar(page, selector: str, fuente: FuenteSecreto, es_jit: bool) -> None:
    """Escribe el secreto en el campo y lo pone en cero enseguida."""
    secreto = fuente()
    try:
        with fase_login(), secreto.revelar() as texto:
            page.fill(selector, texto)
    finally:
        if es_jit:
            secreto.borrar()
