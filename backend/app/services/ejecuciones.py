"""
Ejecuciones del control de facturas: el mismo recorrido que `_run_scrappy` de la
app de escritorio, pero en un hilo del servidor y con estado consultable.

Por cliente, en orden: ARCA (Ventas/Compras) -> Xubio -> Auditoría (Ventas/Compras).
Cada paso queda con su estado (pendiente, en_curso, ok, aviso, error, omitido) y
lo que imprimen los bots se guarda en el registro de la ejecución.

Entre un cliente y el siguiente hay una pausa: el front muestra la cuenta regresiva
con «Continuar», «Cancelar todo» y «No preguntar más»; sin respuesta, sigue sola.

Hay una sola ejecución a la vez: los bots abren Chrome visible en la computadora
del servidor (doble factor de ARCA, captcha de Xubio).
"""

from __future__ import annotations

import io
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app import motor
from app.core.config import settings
from app.services import clientes as clientes_srv

MAX_HISTORIAL = 20
MAX_LINEAS_LOG = 5000


class ErrorEjecucion(Exception):
    def __init__(self, mensaje: str, estado: int = 400) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.estado = estado


# --- Captura de los print() de los bots -----------------------------------------


class _Despachador(io.TextIOBase):
    """
    Reemplaza a sys.stdout una sola vez. Lo que escribe un hilo registrado va al
    registro de su ejecución; lo demás (uvicorn, otros hilos) sigue a la consola.
    """

    def __init__(self, original) -> None:
        self.original = original
        self.destinos: dict[int, callable] = {}
        self._pendiente: dict[int, str] = {}

    def write(self, texto: str) -> int:
        if self.original:
            try:
                self.original.write(texto)
            except Exception:
                pass
        hilo = threading.get_ident()
        destino = self.destinos.get(hilo)
        if destino:
            buffer = self._pendiente.get(hilo, "") + texto
            *lineas, resto = buffer.split("\n")
            self._pendiente[hilo] = resto
            for linea in lineas:
                if linea.strip():
                    destino(linea)
        return len(texto)

    def flush(self) -> None:
        if self.original:
            try:
                self.original.flush()
            except Exception:
                pass

    def soltar(self, hilo: int) -> None:
        resto = self._pendiente.pop(hilo, "")
        destino = self.destinos.pop(hilo, None)
        if destino and resto.strip():
            destino(resto)


_despachador: _Despachador | None = None


def _instalar_despachador() -> _Despachador:
    global _despachador
    if _despachador is None:
        _despachador = _Despachador(sys.stdout)
        sys.stdout = _despachador
    return _despachador


# --- Modelo ----------------------------------------------------------------------

ESTADOS_FINALES = {"finalizada", "cancelada", "error"}


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _tipo_de_linea(texto: str) -> str:
    t = texto.upper()
    if "[ERROR" in t:
        return "error"
    if "[AVISO" in t or "[CANCELADO" in t:
        return "aviso"
    if "[OK" in t or "CONSOLIDADO" in t or "GUARDADO:" in t:
        return "ok"
    return "info"


# Sin emojis en la interfaz (regla del proyecto): los bots imprimen alguno.
_EMOJIS = re.compile("[☀-➿\U0001f300-\U0001faff️]")


@dataclass
class Paso:
    id: str
    etiqueta: str
    estado: str = "pendiente"
    mensaje: str = ""
    inicio: str | None = None
    fin: str | None = None


@dataclass
class ClienteEnCurso:
    nombre: str
    pasos: list[Paso]
    estado: str = "pendiente"
    carpeta: str = ""
    archivos: list[dict] = field(default_factory=list)


@dataclass
class Ejecucion:
    id: str
    usuario: str
    parametros: dict
    clientes: list[ClienteEnCurso]
    estado: str = "en_curso"
    creada: str = field(default_factory=_ahora)
    terminada: str | None = None
    cliente_actual: int = -1
    preguntar: bool = True
    pausa: dict | None = None
    cancelar_pedido: bool = False
    mensaje: str = ""
    log: list[dict] = field(default_factory=list)
    log_base: int = 0  # cuántas líneas viejas se descartaron (el front pide por índice absoluto)

    # Interno: la respuesta a la pausa.
    _respuesta: str | None = None
    _evento: threading.Event = field(default_factory=threading.Event)

    def registrar(self, texto: str, tipo: str | None = None) -> None:
        texto = _EMOJIS.sub("", texto).rstrip()
        if not texto.strip():
            return
        self.log.append({"cuando": _ahora(), "texto": texto, "tipo": tipo or _tipo_de_linea(texto)})
        if len(self.log) > MAX_LINEAS_LOG:
            sobra = len(self.log) - MAX_LINEAS_LOG
            del self.log[:sobra]
            self.log_base += sobra
        self._a_disco(texto)

    def _a_disco(self, texto: str) -> None:
        # Como la app de escritorio: debug_log.txt en la carpeta elegida.
        carpeta = Path(self.parametros["carpeta"])
        if carpeta.is_dir():
            try:
                with open(carpeta / "debug_log.txt", "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.now():%H:%M:%S}] {texto}\n")
            except OSError:
                pass

    def resumen(self) -> dict:
        totales = {"ok": 0, "con_errores": 0, "pendientes": 0}
        for c in self.clientes:
            if c.estado == "ok":
                totales["ok"] += 1
            elif c.estado == "con_errores":
                totales["con_errores"] += 1
            elif c.estado in ("pendiente", "en_curso"):
                totales["pendientes"] += 1
        return {
            "id": self.id,
            "usuario": self.usuario,
            "estado": self.estado,
            "creada": self.creada,
            "terminada": self.terminada,
            "mensaje": self.mensaje,
            "periodo": f"{self.parametros['desde']} al {self.parametros['hasta']}",
            "clientes": len(self.clientes),
            "totales": totales,
        }

    def detalle(self, log_desde: int = 0) -> dict:
        inicio = max(0, log_desde - self.log_base)
        return {
            **self.resumen(),
            "parametros": {k: v for k, v in self.parametros.items()},
            "cliente_actual": self.cliente_actual,
            "preguntar": self.preguntar,
            "cancelar_pedido": self.cancelar_pedido,
            "pausa": self.pausa,
            "detalle_clientes": [
                {
                    "nombre": c.nombre,
                    "estado": c.estado,
                    "carpeta": c.carpeta,
                    "archivos": c.archivos,
                    "pasos": [p.__dict__ for p in c.pasos],
                }
                for c in self.clientes
            ],
            "log": self.log[inicio:],
            "log_total": self.log_base + len(self.log),
        }


# --- Registro de ejecuciones -----------------------------------------------------

_ejecuciones: dict[str, Ejecucion] = {}
_orden: list[str] = []
_lock = threading.Lock()


def listar() -> list[dict]:
    with _lock:
        return [_ejecuciones[i].resumen() for i in reversed(_orden)]


def obtener(ejecucion_id: str) -> Ejecucion:
    e = _ejecuciones.get(ejecucion_id)
    if e is None:
        raise ErrorEjecucion("No existe esa ejecución (se borran al reiniciar el servidor).", 404)
    return e


def activa() -> Ejecucion | None:
    with _lock:
        for i in reversed(_orden):
            if _ejecuciones[i].estado not in ESTADOS_FINALES:
                return _ejecuciones[i]
    return None


def _pasos_para(p: dict) -> list[Paso]:
    ops = [("ventas", "Ventas"), ("compras", "Compras")]
    pasos: list[Paso] = []
    if p["pasos"]["arca"]:
        pasos += [Paso(f"arca_{o}", f"ARCA · {e}") for o, e in ops if p[o]]
    if p["pasos"]["xubio"]:
        pasos.append(Paso("xubio", "Xubio · " + " y ".join(e for o, e in ops if p[o])))
    if p["pasos"]["auditoria"]:
        pasos += [Paso(f"auditoria_{o}", f"Auditoría · {e}") for o, e in ops if p[o]]
    return pasos


def _validar_fecha(texto: str, etiqueta: str) -> datetime:
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y")
    except (ValueError, AttributeError):
        raise ErrorEjecucion(f"La fecha «{etiqueta}» tiene que tener el formato DD/MM/AAAA.", 422) from None


def iniciar(usuario: str, pedido: dict) -> Ejecucion:
    """Valida el pedido y arranca el hilo. Devuelve la ejecución nueva."""
    if activa() is not None:
        raise ErrorEjecucion("Ya hay un control en curso. Esperá a que termine o cancelalo.", 409)

    nombres = [n for n in dict.fromkeys(pedido.get("clientes") or []) if n]
    if not nombres:
        raise ErrorEjecucion("Elegí al menos un cliente.", 422)
    config = clientes_srv.leer_config()["clientes"]
    desconocidos = [n for n in nombres if n not in config]
    if desconocidos:
        raise ErrorEjecucion("No existen estos clientes: " + ", ".join(desconocidos), 422)

    desde = _validar_fecha(pedido.get("desde", ""), "Desde")
    hasta = _validar_fecha(pedido.get("hasta", ""), "Hasta")
    if hasta < desde:
        raise ErrorEjecucion("La fecha «Hasta» es anterior a «Desde».", 422)

    ventas, compras = bool(pedido.get("ventas")), bool(pedido.get("compras"))
    if not (ventas or compras):
        raise ErrorEjecucion("Elegí Ventas, Compras o las dos.", 422)
    pasos = pedido.get("pasos") or {}
    pasos = {k: bool(pasos.get(k)) for k in ("arca", "xubio", "auditoria")}
    if not any(pasos.values()):
        raise ErrorEjecucion("Elegí al menos un paso (ARCA, Xubio o Auditoría).", 422)

    carpeta = Path((pedido.get("carpeta") or "").strip() or settings.CARPETA_SALIDA).expanduser()
    if not carpeta.is_dir():
        raise ErrorEjecucion(f"La carpeta «{carpeta}» no existe en la computadora del servidor.", 422)

    xubio_usuario = xubio_clave = ""
    if pasos["xubio"]:
        x = pedido.get("xubio") or {}
        guardada = clientes_srv.leer_xubio()
        xubio_usuario = (x.get("usuario") or "").strip() or guardada["user"]
        xubio_clave = x.get("clave") or (guardada["pass"] if xubio_usuario == guardada["user"] else "")
        if not xubio_usuario or not xubio_clave:
            raise ErrorEjecucion("El paso de Xubio necesita el usuario y la contraseña de Xubio.", 422)
        if not guardada["env"] or x.get("recordar"):
            clientes_srv.guardar_xubio(xubio_usuario, xubio_clave, bool(x.get("recordar")))

    parametros = {
        "desde": desde.strftime("%d/%m/%Y"),
        "hasta": hasta.strftime("%d/%m/%Y"),
        "ventas": ventas,
        "compras": compras,
        "pasos": pasos,
        "carpeta": str(carpeta),
        "xubio_usuario": xubio_usuario,
    }
    ej = Ejecucion(
        id=uuid.uuid4().hex[:12],
        usuario=usuario,
        parametros=parametros,
        clientes=[ClienteEnCurso(n, _pasos_para(parametros)) for n in nombres],
    )
    with _lock:
        _ejecuciones[ej.id] = ej
        _orden.append(ej.id)
        while len(_orden) > MAX_HISTORIAL:
            viejo = _orden.pop(0)
            if _ejecuciones[viejo].estado in ESTADOS_FINALES:
                del _ejecuciones[viejo]
            else:
                _orden.insert(0, viejo)
                break

    _instalar_despachador()
    hilo = threading.Thread(target=_correr, args=(ej, xubio_clave), name=f"control-{ej.id}", daemon=True)
    hilo.start()
    return ej


def responder_pausa(ej: Ejecucion, accion: str) -> None:
    if accion not in ("continuar", "cancelar", "no_preguntar"):
        raise ErrorEjecucion("Acción desconocida.", 422)
    if ej.pausa is None:
        raise ErrorEjecucion("La ejecución no está esperando una respuesta.", 409)
    ej._respuesta = accion
    ej._evento.set()


def cancelar(ej: Ejecucion) -> None:
    if ej.estado in ESTADOS_FINALES:
        raise ErrorEjecucion("La ejecución ya terminó.", 409)
    ej.cancelar_pedido = True
    ej.registrar("[AVISO] Cancelación pedida: se corta al terminar el paso en curso.", "aviso")
    if ej.pausa is not None:
        ej._respuesta = "cancelar"
        ej._evento.set()


# --- El recorrido ---------------------------------------------------------------


def _carpeta_cliente(base: str, cliente: str, mes: str, anio: str) -> Path:
    """<Carpeta>/<Cliente>/<mmaaaa>/Control de Facturas (igual que la app de escritorio)."""
    limpio = re.sub(r'[\\/*?:"<>|]', "", cliente).strip()
    carpeta = Path(base) / limpio / f"{mes}{anio}" / settings.SUBCARPETA_CONTROL
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def listar_archivos(carpeta: Path) -> list[dict]:
    if not carpeta.is_dir():
        return []
    archivos = []
    for a in sorted(carpeta.iterdir(), key=lambda x: x.name.casefold()):
        if a.is_file() and a.suffix.lower() in (".xlsx", ".xls", ".csv"):
            st = a.stat()
            tipo = "auditoria" if a.name.startswith("AUX - ") else "arca" if a.name.startswith("Mis comprobantes") else "xubio" if a.name.startswith("IVA ") else "otro"
            archivos.append({"nombre": a.name, "tamano": st.st_size, "modificado": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"), "tipo": tipo})
    return archivos


def _correr_paso(ej: Ejecucion, paso: Paso, funcion) -> None:
    paso.estado = "en_curso"
    paso.inicio = _ahora()
    desde = ej.log_base + len(ej.log)
    try:
        funcion()
    except Exception as e:  # los bots tiran Exception genérica
        paso.estado = "error"
        # En una línea: los bots ponen la ruta del archivo que falta en la segunda.
        paso.mensaje = " ".join(str(e).split())[:400] or type(e).__name__
        ej.registrar(f" [ERROR] {paso.etiqueta}: {e}", "error")
    else:
        # Algunos bots atrapan su propio error y solo lo imprimen.
        errores = [l["texto"] for l in ej.log[max(0, desde - ej.log_base):] if l["tipo"] == "error"]
        if errores:
            paso.estado = "aviso"
            paso.mensaje = errores[-1].strip()[:300]
        else:
            paso.estado = "ok"
    paso.fin = _ahora()


def _pausa(ej: Ejecucion, cliente: str) -> str:
    if not ej.preguntar:
        return "continuar"
    ej._respuesta = None
    ej._evento.clear()
    ej.pausa = {"cliente": cliente, "vence": time.time() + settings.PAUSA_ENTRE_CLIENTES_S, "segundos": settings.PAUSA_ENTRE_CLIENTES_S}
    ej.estado = "esperando"
    ej._evento.wait(settings.PAUSA_ENTRE_CLIENTES_S)
    ej.pausa = None
    ej.estado = "en_curso"
    return ej._respuesta or "continuar"


def _correr(ej: Ejecucion, xubio_clave: str) -> None:
    hilo = threading.get_ident()
    if _despachador is not None:
        _despachador.destinos[hilo] = ej.registrar
    p = ej.parametros
    _, mes, anio = p["desde"].split("/")
    try:
        bots = motor.cargar()
        ej.registrar("[INICIO] CONTROL DE FACTURAS")
        ej.registrar(f" Período {p['desde']} al {p['hasta']} · {len(ej.clientes)} cliente(s) · usuario {ej.usuario}")
        config = clientes_srv.leer_config()["clientes"]

        for indice, cli in enumerate(ej.clientes):
            if ej.cancelar_pedido:
                break
            ej.cliente_actual = indice
            cli.estado = "en_curso"
            ej.registrar(f"[{indice + 1}/{len(ej.clientes)}] PROCESANDO: {cli.nombre}")
            datos = config.get(cli.nombre, {})
            arca = datos.get("arca") or {}
            cuit_login = str(arca.get("cuit_login") or "").strip()
            clave_arca = str(arca.get("clave") or "")
            cuit_repres = str(arca.get("cuit_representado") or "").strip()
            url_xubio = str((datos.get("xubio") or {}).get("url") or "").strip()

            carpeta = _carpeta_cliente(p["carpeta"], cli.nombre, mes, anio)
            cli.carpeta = str(carpeta)
            ej.registrar(f" -> Carpeta: {carpeta}")

            acciones = {
                "arca_ventas": lambda: bots.arca.ejecutar_extraccion(cuit_login, clave_arca, cuit_repres, p["desde"], p["hasta"], "Emitidos", cli.nombre, str(carpeta), bajar_excel=True, bajar_csv=False),
                "arca_compras": lambda: bots.arca.ejecutar_extraccion(cuit_login, clave_arca, cuit_repres, p["desde"], p["hasta"], "Recibidos", cli.nombre, str(carpeta), bajar_excel=True, bajar_csv=False),
                "xubio": lambda: bots.xubio.ejecutar_extraccion(p["xubio_usuario"], xubio_clave, cli.nombre, url_xubio, p["desde"], p["hasta"], p["ventas"], p["compras"], str(carpeta)),
                "auditoria_ventas": lambda: bots.auditor.procesar_cruce(cli.nombre, "Ventas", p["desde"], p["hasta"], str(carpeta)),
                "auditoria_compras": lambda: bots.auditor.procesar_cruce(cli.nombre, "Compras", p["desde"], p["hasta"], str(carpeta)),
            }
            for paso in cli.pasos:
                if ej.cancelar_pedido:
                    paso.estado = "omitido"
                    continue
                ej.registrar(f" -> {paso.etiqueta}")
                _correr_paso(ej, paso, acciones[paso.id])
                cli.archivos = listar_archivos(carpeta)

            cli.estado = "con_errores" if any(x.estado in ("error", "aviso") for x in cli.pasos) else "ok"
            if ej.cancelar_pedido and any(x.estado == "omitido" for x in cli.pasos):
                cli.estado = "cancelado"
            ej.registrar("-----------------------------------------")

            if indice < len(ej.clientes) - 1 and not ej.cancelar_pedido:
                accion = _pausa(ej, cli.nombre)
                if accion == "cancelar":
                    ej.cancelar_pedido = True
                elif accion == "no_preguntar":
                    ej.preguntar = False

        if ej.cancelar_pedido:
            for c in ej.clientes:
                if c.estado == "pendiente":
                    c.estado = "cancelado"
                    for x in c.pasos:
                        x.estado = "omitido"
            ej.estado = "cancelada"
            ej.mensaje = "Se canceló el control."
            ej.registrar("[CANCELADO] PROCESO CANCELADO.", "aviso")
        else:
            ej.estado = "finalizada"
            con_errores = sum(1 for c in ej.clientes if c.estado == "con_errores")
            ej.mensaje = "Terminó sin errores." if not con_errores else f"Terminó: {con_errores} cliente(s) con errores o avisos."
            ej.registrar("[OK] PROCESO DE CONTROL DE FACTURAS FINALIZADO.", "ok")
    except Exception as e:
        ej.estado = "error"
        ej.mensaje = f"El control se cortó: {e}"
        ej.registrar(f"[ERROR] El control se cortó: {e}", "error")
    finally:
        ej.pausa = None
        ej.cliente_actual = -1
        ej.terminada = _ahora()
        if _despachador is not None:
            _despachador.soltar(hilo)


def ruta_archivo(ej: Ejecucion, indice: int, nombre: str) -> Path:
    """Un archivo generado para un cliente de la ejecución, sin salir de su carpeta."""
    if not 0 <= indice < len(ej.clientes) or not ej.clientes[indice].carpeta:
        raise ErrorEjecucion("Ese cliente no tiene carpeta en esta ejecución.", 404)
    carpeta = Path(ej.clientes[indice].carpeta).resolve()
    archivo = (carpeta / nombre).resolve()
    if not archivo.is_relative_to(carpeta) or not archivo.is_file():
        raise ErrorEjecucion("No se encontró el archivo.", 404)
    return archivo
