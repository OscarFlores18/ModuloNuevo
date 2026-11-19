"""Ejecuciones del control de facturas para el frontend web.

Es el mismo recorrido de la app de escritorio, pero con estado consultable y
una pausa entre clientes. El contrato de datos es el que consume el Angular
(`frontend/src/app/core/models.ts`).

Por cliente, en orden: ARCA (Ventas/Compras) -> Xubio -> Auditoría
(Ventas/Compras). Cada paso queda con su estado y lo que reportan los bots se
guarda en el registro de la ejecución.

Las sesiones de ARCA y Xubio se comparten entre clientes y pasos: un solo login
por credencial. ARCA se mantiene viva con pings al portal y se renueva sola si
vence por inactividad (ver `bots/sesiones.py` y `bots/arca.py`).

Hay una sola ejecución a la vez: los bots abren Chrome visible en la
computadora del servidor (doble factor de ARCA, captcha de Xubio).
"""

from __future__ import annotations

import calendar
import io
import json
import re
import shutil
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..auditor import procesar_cruce
from ..auditor.corregir_libro import LibroIlegible, corregir_libro
from ..bots.arca import FiltroNoDisponible
from ..bots.sesiones import GestorSesiones
from ..core import config
from ..core.errors import ArchivoEnUso, BotError, SesionVencida
from ..bots.xubio_common import es_url_de_xubio
from ..core.controles import descartar_si_vacia, nueva_carpeta_control, numero_de_carpeta
from ..core.paths import SUBCARPETA_CONTROL, carpeta_cliente, nombre_carpeta
from ..core.mensajes_bot import mensaje_legible, portal_de_paso
from ..core.saneo import sanear
from ..core.storage import ClientesRepository, CredencialesXubioRepository
from .credenciales import ServicioCredenciales

# Controles que se recuerdan (en memoria y en DATA_DIR/ejecuciones/<id>.json).
MAX_HISTORIAL = 100
# Del registro técnico se guarda en disco solo el final (alcanza para ver qué falló).
MAX_LINEAS_LOG_GUARDADAS = 500
MAX_LINEAS_LOG = 5000

ESTADOS_FINALES = {"finalizada", "cancelada", "error"}


class ErrorEjecucion(Exception):
    def __init__(self, mensaje: str, estado: int = 400) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.estado = estado


# --- Modelo ------------------------------------------------------------------


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


# Sin emojis en la interfaz (regla del proyecto).
_EMOJIS = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F000-\U0001F2FF"
    "\U0000FE0F"
    "]+"
)


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
    # Link de Xubio ya parado en la empresa del cliente (Relogin?tenant=...).
    xubio_url: str | None = None
    # «Aplicar cambios»: por operación, el Libro IVA corregido, el AUX re-chequeado
    # y los cambios aplicados hasta ahora.
    corregidos: dict = field(default_factory=dict)


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
    log_base: int = 0  # líneas viejas descartadas (el front pide por índice absoluto)
    # Avisos que no frenan el control y se muestran al terminar (letra distinta...).
    advertencias: list[dict] = field(default_factory=list)
    # Por cliente y operación: cuántos comprobantes coinciden (para la ventana).
    resumenes: list[dict] = field(default_factory=list)

    # Interno: la respuesta a la pausa.
    _respuesta: str | None = None
    _evento: threading.Event = field(default_factory=threading.Event)
    # Errores registrados por hilo: ARCA y Xubio corren en paralelo y cada paso
    # tiene que ver solo los suyos.
    _errores_hilo: dict = field(default_factory=dict)

    def registrar(self, texto: str, tipo: str | None = None) -> None:
        texto = sanear(_EMOJIS.sub("", str(texto))).rstrip()
        if not texto.strip():
            return
        self.log.append(
            {"cuando": _ahora(), "texto": texto, "tipo": tipo or _tipo_de_linea(texto)}
        )
        if len(self.log) > MAX_LINEAS_LOG:
            sobra = len(self.log) - MAX_LINEAS_LOG
            del self.log[:sobra]
            self.log_base += sobra
        if (tipo or _tipo_de_linea(texto)) == "error":
            hilo = threading.get_ident()
            cantidad, _ = self._errores_hilo.get(hilo, (0, ""))
            self._errores_hilo[hilo] = (cantidad + 1, texto.strip())
        self._a_disco(texto)

    def _a_disco(self, texto: str) -> None:
        # Como la app de escritorio: debug_log.txt en la carpeta elegida.
        try:
            carpeta = Path(self.parametros["carpeta"])
        except (KeyError, TypeError):
            return
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
            "nombres": [c.nombre for c in self.clientes],
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
            "advertencias": self.advertencias,
            "resumenes": self.resumenes,
            "detalle_clientes": [
                {
                    "nombre": c.nombre,
                    "estado": c.estado,
                    "carpeta": c.carpeta,
                    "archivos": c.archivos,
                    "xubio_url": c.xubio_url,
                    "corregidos": c.corregidos,
                    "pasos": [p.__dict__ for p in c.pasos],
                }
                for c in self.clientes
            ],
            "log": self.log[inicio:],
            "log_total": self.log_base + len(self.log),
        }

    def a_guardar(self) -> dict:
        """Lo que queda en disco al terminar: todo menos el estado interno del hilo."""
        return {
            "version": 1,
            "id": self.id,
            "usuario": self.usuario,
            "parametros": self.parametros,
            "estado": self.estado,
            "creada": self.creada,
            "terminada": self.terminada,
            "mensaje": self.mensaje,
            "advertencias": self.advertencias,
            "resumenes": self.resumenes,
            "clientes": [
                {
                    "nombre": c.nombre,
                    "estado": c.estado,
                    "carpeta": c.carpeta,
                    "archivos": c.archivos,
                    "xubio_url": c.xubio_url,
                    "corregidos": c.corregidos,
                    "pasos": [p.__dict__ for p in c.pasos],
                }
                for c in self.clientes
            ],
            "log": self.log[-MAX_LINEAS_LOG_GUARDADAS:],
        }

    @classmethod
    def desde_guardado(cls, datos: dict) -> "Ejecucion":
        ej = cls(
            id=str(datos["id"]),
            usuario=str(datos.get("usuario") or ""),
            parametros=dict(datos["parametros"]),
            clientes=[
                ClienteEnCurso(
                    nombre=c["nombre"],
                    # Los controles guardados antes de traducir los errores de los bots
                    # se ven igual de claros que los nuevos.
                    pasos=[
                        Paso(**{**p, "mensaje": mensaje_legible(p.get("mensaje") or "", portal_de_paso(p.get("id") or ""))})
                        for p in c.get("pasos") or []
                    ],
                    estado=c.get("estado") or "pendiente",
                    carpeta=c.get("carpeta") or "",
                    archivos=list(c.get("archivos") or []),
                    xubio_url=c.get("xubio_url"),
                    corregidos=dict(c.get("corregidos") or {}),
                )
                for c in datos.get("clientes") or []
            ],
            estado=datos.get("estado") or "error",
            creada=datos.get("creada") or _ahora(),
            terminada=datos.get("terminada"),
            preguntar=False,
            mensaje=datos.get("mensaje") or "",
            log=list(datos.get("log") or []),
            advertencias=[_advertencia_legible(a) for a in datos.get("advertencias") or []],
            resumenes=list(datos.get("resumenes") or []),
        )
        # Los pasos que fallaron se vuelven a diagnosticar con las reglas de hoy: un
        # control viejo no sigue mandando a «Corregí el CUIT» si ARCA solo estaba lenta.
        pasos = {(c.nombre, p.etiqueta): p for c in ej.clientes for p in c.pasos}
        for i, a in enumerate(ej.advertencias):
            paso = pasos.get((a.get("cliente"), str(a.get("titulo") or "").rsplit(":", 1)[0]))
            if a.get("tipo") == "paso_error" and paso is not None and paso.mensaje:
                detalle, acciones = _acciones_de_paso(paso)
                ej.advertencias[i] = {**a, "detalle": detalle, "acciones": acciones}
        if ej.estado not in ESTADOS_FINALES:
            # Quedó a medias (se apagó el servidor): no puede seguir corriendo.
            ej.estado = "error"
            ej.mensaje = ej.mensaje or "Se cortó: el servidor se apagó durante el control."
        return ej


# --- Registro de ejecuciones -------------------------------------------------

_ejecuciones: dict[str, Ejecucion] = {}
_orden: list[str] = []
_lock = threading.Lock()
# La carpeta de la que ya se leyó el historial (cambia en los tests).
_historial_de: Path | None = None


def _carpeta_historial() -> Path:
    return Path(config.DATA_DIR) / "ejecuciones"


def _cargar_historial() -> None:
    """Suma los controles guardados en disco. Se llama con `_lock` tomado."""
    global _historial_de
    carpeta = _carpeta_historial()
    if _historial_de == carpeta:
        return
    _historial_de = carpeta
    if not carpeta.is_dir():
        return
    for archivo in carpeta.glob("*.json"):
        try:
            ej = Ejecucion.desde_guardado(json.loads(archivo.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            continue  # un archivo roto no impide ver el resto
        _ejecuciones.setdefault(ej.id, ej)
    _orden[:] = sorted(_ejecuciones, key=lambda i: _ejecuciones[i].creada)
    _recortar()


def _recortar() -> None:
    """Olvida los controles terminados más viejos (y su archivo). Con `_lock` tomado."""
    while len(_orden) > MAX_HISTORIAL:
        viejo = _orden[0]
        if _ejecuciones[viejo].estado not in ESTADOS_FINALES:
            break
        _orden.pop(0)
        del _ejecuciones[viejo]
        try:
            (_carpeta_historial() / f"{viejo}.json").unlink(missing_ok=True)
        except OSError:
            pass


def _guardar(ej: Ejecucion) -> None:
    """Escribe el control terminado en disco. Si falla, el historial sigue en memoria."""
    carpeta = _carpeta_historial()
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        temporal = carpeta / f"{ej.id}.json.tmp"
        temporal.write_text(
            json.dumps(ej.a_guardar(), ensure_ascii=False, default=str), encoding="utf-8"
        )
        temporal.replace(carpeta / f"{ej.id}.json")
    except OSError:
        pass


def listar() -> list[dict]:
    with _lock:
        _cargar_historial()
        return [_ejecuciones[i].resumen() for i in reversed(_orden)]


def obtener(ejecucion_id: str) -> Ejecucion:
    with _lock:
        _cargar_historial()
        e = _ejecuciones.get(ejecucion_id)
    if e is None:
        raise ErrorEjecucion("No existe esa ejecución.", 404)
    return e


def eliminar(ejecucion_id: str) -> None:
    """Saca un control del historial (memoria y su .json). No toca los Excel ni
    las carpetas del cliente. Uno que está corriendo no se puede borrar."""
    with _lock:
        _cargar_historial()
        ej = _ejecuciones.get(ejecucion_id)
        if ej is None:
            raise ErrorEjecucion("No existe esa ejecución.", 404)
        if ej.estado not in ESTADOS_FINALES:
            raise ErrorEjecucion("Ese control está corriendo: cancelalo o esperá a que termine para borrarlo.", 409)
        del _ejecuciones[ejecucion_id]
        if ejecucion_id in _orden:
            _orden.remove(ejecucion_id)
        try:
            (_carpeta_historial() / f"{ejecucion_id}.json").unlink(missing_ok=True)
        except OSError:
            pass


def activa() -> Ejecucion | None:
    with _lock:
        _cargar_historial()
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


def _validar_fecha(texto: str, etiqueta: str, ajustes: list[str] | None = None) -> datetime:
    """DD/MM/AAAA. Un día que el mes no tiene (31/02, 31/11) se lleva al último
    día del mes en vez de cortar el control; el ajuste queda en `ajustes`."""
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", str(texto or ""))
    if not m:
        raise ErrorEjecucion(
            f"La fecha «{etiqueta}» tiene que tener el formato DD/MM/AAAA.", 422
        )
    dia, mes, anio = (int(x) for x in m.groups())
    if not 1 <= mes <= 12 or dia < 1:
        raise ErrorEjecucion(f"La fecha «{etiqueta}» ({texto}) no existe.", 422)
    ultimo = calendar.monthrange(anio, mes)[1]
    if dia > ultimo:
        if ajustes is not None:
            ajustes.append(
                f"«{etiqueta}»: el {dia:02d}/{mes:02d} no existe, se usa el "
                f"{ultimo:02d}/{mes:02d}/{anio} (último día del mes)."
            )
        dia = ultimo
    return datetime(anio, mes, dia)


def iniciar(
    usuario: str,
    pedido: dict,
    clientes_repo: ClientesRepository,
    credenciales_repo: CredencialesXubioRepository,
) -> Ejecucion:
    """Valida el pedido y arranca el hilo. Devuelve la ejecución nueva."""
    if activa() is not None:
        raise ErrorEjecucion(
            "Ya hay un control en curso. Esperá a que termine o cancelalo.", 409
        )

    nombres = [n for n in dict.fromkeys(pedido.get("clientes") or []) if n]
    if not nombres:
        raise ErrorEjecucion("Elegí al menos un cliente.", 422)
    config_clientes = clientes_repo.listar_publico()
    desconocidos = [n for n in nombres if n not in config_clientes]
    if desconocidos:
        raise ErrorEjecucion(
            "No existen estos clientes: " + ", ".join(desconocidos), 422
        )

    ajustes_fecha: list[str] = []
    desde = _validar_fecha(pedido.get("desde", ""), "Desde", ajustes_fecha)
    hasta = _validar_fecha(pedido.get("hasta", ""), "Hasta", ajustes_fecha)
    if hasta < desde:
        raise ErrorEjecucion("La fecha «Hasta» es anterior a «Desde».", 422)
    # ARCA no acepta fechas futuras: si el mes no terminó (ej. hoy es 26 y se
    # pidió hasta el 30), se toma la fecha más alta posible, que es hoy.
    hoy = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if desde > hoy:
        raise ErrorEjecucion(
            f"El período empieza el {desde:%d/%m/%Y}, que todavía no llegó.", 422
        )
    hasta_pedido = hasta
    hasta = min(hasta, hoy)

    ventas, compras = bool(pedido.get("ventas")), bool(pedido.get("compras"))
    if not (ventas or compras):
        raise ErrorEjecucion("Elegí Ventas, Compras o las dos.", 422)

    pasos = pedido.get("pasos") or {}
    pasos = {k: bool(pasos.get(k)) for k in ("arca", "xubio", "auditoria")}
    if not any(pasos.values()):
        raise ErrorEjecucion("Elegí al menos un paso (ARCA, Xubio o Auditoría).", 422)

    carpeta_cruda = (pedido.get("carpeta") or "").strip()
    carpeta = Path(carpeta_cruda).expanduser() if carpeta_cruda else config.OUTPUT_DIR
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ErrorEjecucion(
            f"No se pudo usar la carpeta «{carpeta}»: {exc}", 422
        ) from exc
    if not carpeta.is_dir():
        raise ErrorEjecucion(
            f"La carpeta «{carpeta}» no existe en la computadora del servidor.", 422
        )

    servicio = ServicioCredenciales(clientes_repo, credenciales_repo)
    xubio_usuario = xubio_clave = ""
    if pasos["xubio"]:
        x = pedido.get("xubio") or {}
        try:
            credenciales_xubio = servicio.xubio(
                (x.get("usuario") or "").strip(),
                x.get("clave") or "",
                recordar=bool(x.get("recordar")),
            )
        except BotError as exc:
            raise ErrorEjecucion(exc.mensaje, 422) from exc
        xubio_usuario, xubio_clave = (
            credenciales_xubio.usuario,
            credenciales_xubio.clave,
        )

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
    for ajuste in ajustes_fecha:
        ej.registrar(f"[AVISO] {ajuste}", "aviso")
    if hasta < hasta_pedido:
        ej.registrar(
            f"[AVISO] El período pedía hasta el {hasta_pedido:%d/%m/%Y}, que todavía no "
            f"llegó: se controla hasta hoy, {hasta:%d/%m/%Y}.",
            "aviso",
        )
    with _lock:
        _cargar_historial()
        _ejecuciones[ej.id] = ej
        _orden.append(ej.id)
        _recortar()

    hilo = threading.Thread(
        target=_correr,
        args=(ej, xubio_clave, servicio),
        name=f"control-{ej.id}",
        daemon=True,
    )
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
    ej.registrar(
        "[AVISO] Cancelación pedida: se corta al terminar el paso en curso.", "aviso"
    )
    if ej.pausa is not None:
        ej._respuesta = "cancelar"
        ej._evento.set()


# --- El recorrido ---------------------------------------------------------------


def listar_archivos(carpeta: Path, desde_ts: float | None = None) -> list[dict]:
    """Los Excel/CSV de la carpeta. Con `desde_ts`, solo los escritos desde ese
    momento: la carpeta <Cliente>/<mmaaaa> la comparten todos los controles de ese
    mes, y sin el filtro se mezclaban archivos de otros controles (otras fechas,
    o Compras en un control de solo Ventas)."""
    if not carpeta.is_dir():
        return []
    archivos = []
    for a in sorted(carpeta.iterdir(), key=lambda x: x.name.casefold()):
        if a.is_file() and a.suffix.lower() in (".xlsx", ".xls", ".csv") and not a.name.startswith("~$"):
            st = a.stat()
            if desde_ts is not None and st.st_mtime < desde_ts - 1:
                continue
            tipo = (
                "auditoria"
                if a.name.startswith("AUX - ")
                else "arca"
                if a.name.startswith("Mis comprobantes")
                else "xubio"
                if a.name.startswith("IVA ")
                else "otro"
            )
            archivos.append(
                {
                    "nombre": a.name,
                    "tamano": st.st_size,
                    "modificado": datetime.fromtimestamp(st.st_mtime).isoformat(
                        timespec="seconds"
                    ),
                    "tipo": tipo,
                }
            )
    return archivos


def _correr_paso(ej: Ejecucion, paso: Paso, funcion) -> BaseException | None:
    paso.estado = "en_curso"
    paso.inicio = _ahora()
    hilo = threading.get_ident()
    errores_antes = ej._errores_hilo.get(hilo, (0, ""))[0]
    error: BaseException | None = None
    try:
        funcion()
    except Exception as exc:  # los bots tiran Exception genérica
        error = exc
        paso.estado = "error"
        crudo = " ".join(str(exc).split())[:400] or type(exc).__name__
        # En pantalla, en criollo; el texto técnico queda en el registro.
        paso.mensaje = mensaje_legible(crudo, portal_de_paso(paso.id))
        ej.registrar(f" [ERROR] {paso.etiqueta}: {exc}", "error")
    else:
        # Algunos bots atrapan su propio error y solo lo registran.
        errores_despues, ultimo = ej._errores_hilo.get(hilo, (0, ""))
        if errores_despues > errores_antes:
            paso.estado = "aviso"
            paso.mensaje = mensaje_legible(ultimo[:300], portal_de_paso(paso.id))
        else:
            paso.estado = "ok"
    paso.fin = _ahora()
    return error


def _acciones_de_paso(paso: Paso) -> tuple[str, list[str]]:
    """Qué pasó (en criollo) y qué hacer, según el paso que falló."""
    m = (paso.mensaje or "").lower()
    portal = "ARCA" if paso.id.startswith("arca_") else "Xubio"
    # «No apareció el campo de la clave en 45 s (… o ARCA muy lenta)»: no se sabe
    # si fue el CUIT o ARCA caída. Mandar a «Corregí el CUIT» confundía cuando ARCA
    # estaba lenta: primero se reintenta y, si se repite, se revisan los datos.
    if portal == "ARCA" and "no apareció" in m and "muy lenta" in m:
        return (
            "ARCA no mostró a tiempo la pantalla de la clave: puede estar lenta, "
            "o el CUIT de ingreso o la clave fiscal no son correctos.",
            ["Volver a correr", "Revisar clave de ARCA"],
        )
    if "clave" in m or "login" in m or "contraseña" in m or "credencial" in m or "iniciar sesión" in m:
        return f"No se pudo entrar a {portal}.", [f"Corregir clave de {portal}"]
    if "empresa" in m or "compañ" in m:
        return "No se encontró la empresa en Xubio.", ["Revisar empresa en Xubio"]
    if "no se encontró el archivo" in m or ("archivo" in m and "esperado" in m):
        return "Faltó el Excel de ARCA o de Xubio.", ["Revisar paso anterior"]
    if "tiempo" in m or "timeout" in m or "vencid" in m:
        return f"{portal} no respondió a tiempo.", ["Volver a correr"]
    return paso.mensaje, ["Volver a correr"]


def _advertencia_legible(a: dict) -> dict:
    """Un hallazgo guardado de un paso que falló, con el detalle traducido."""
    if a.get("tipo") != "paso_error" or not a.get("detalle"):
        return a
    return {**a, "detalle": mensaje_legible(a["detalle"], portal_de_paso(a.get("titulo") or ""))}


def _error_de_paso(cliente: str, paso: Paso) -> dict:
    return {
        "tipo": "paso_error",
        "gravedad": "grave" if paso.estado == "error" else "corregir",
        "cliente": cliente,
        "operacion": None,
        "titulo": f"{paso.etiqueta}: {'falló' if paso.estado == 'error' else 'terminó con avisos'}",
        "detalle": _acciones_de_paso(paso)[0],
        "acciones": _acciones_de_paso(paso)[1],
        "total": 0,
        "a_corregir": 0,
        "comprobantes": [],
        "hoja": None,
    }


def _pausa(ej: Ejecucion, cliente: str) -> str:
    if not ej.preguntar:
        return "continuar"
    segundos = config.PAUSA_ENTRE_CLIENTES_S
    ej._respuesta = None
    ej._evento.clear()
    ej.pausa = {
        "cliente": cliente,
        "vence": time.time() + segundos,
        "segundos": segundos,
    }
    ej.estado = "esperando"
    ej._evento.wait(segundos)
    ej.pausa = None
    ej.estado = "en_curso"
    return ej._respuesta or "continuar"


class _HiloBot:
    """Un hilo dedicado con su propio ``GestorSesiones``.

    Playwright sync solo deja usar un navegador desde el hilo que lo abrió, y
    las sesiones se reutilizan entre clientes: por eso cada portal tiene su
    hilo fijo durante toda la ejecución.
    """

    def __init__(self, nombre: str, log) -> None:
        self._local = threading.local()
        self._log = log
        self._ejecutor: ThreadPoolExecutor | None = None
        self._nombre = nombre

    def _gestor(self) -> GestorSesiones:
        gestor = getattr(self._local, "gestor", None)
        if gestor is None:
            gestor = self._local.gestor = GestorSesiones(log=self._log)
        return gestor

    def enviar(self, funcion) -> Future:
        if self._ejecutor is None:
            self._ejecutor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix=f"bot-{self._nombre}"
            )
        return self._ejecutor.submit(lambda: funcion(self._gestor()))

    def cerrar(self) -> None:
        if self._ejecutor is None:
            return
        try:
            self._ejecutor.submit(
                lambda: getattr(self._local, "gestor", None) and self._local.gestor.cerrar()
            ).result(timeout=120)
        except Exception:
            pass
        self._ejecutor.shutdown(wait=False)
        self._ejecutor = None


def _correr(ej: Ejecucion, xubio_clave: str, servicio: ServicioCredenciales) -> None:
    p = ej.parametros
    _, mes, anio = p["desde"].split("/")
    hilo_arca = _HiloBot("arca", ej.registrar)
    hilo_xubio = _HiloBot("xubio", ej.registrar)
    try:
        ej.registrar("[INICIO] CONTROL DE FACTURAS")
        ej.registrar(
            f" Período {p['desde']} al {p['hasta']} · "
            f"{len(ej.clientes)} cliente(s) · usuario {ej.usuario}"
        )

        for indice, cli in enumerate(ej.clientes):
            if ej.cancelar_pedido:
                break
            ej.cliente_actual = indice
            cli.estado = "en_curso"
            ej.registrar(f"[{indice + 1}/{len(ej.clientes)}] PROCESANDO: {cli.nombre}")

            if any(paso.id.startswith("arca_") for paso in cli.pasos):
                cred = servicio.arca(cli.nombre)
                cuit_login = cred.cuit_login
                clave_arca = cred.clave
                cuit_repres = cred.cuit_representado
            else:
                cuit_login = clave_arca = cuit_repres = ""

            # Cada control tiene su carpeta: Control de facturas/1er control, 2do...
            carpeta, _ = nueva_carpeta_control(
                carpeta_cliente(Path(p["carpeta"]), cli.nombre, mes, anio)
            )
            cli.carpeta = str(carpeta)
            inicio_cliente = time.time()
            ej.registrar(f" -> Carpeta: {carpeta} ({carpeta.name})")

            servicio_arca = None

            def hacer_arca(sesion, tipo, _cuit_repres=cuit_repres, _cli=cli.nombre):
                nonlocal servicio_arca
                # Los botones Emitidos/Recibidos están solo en la entrada del
                # servicio: cada tipo arranca con Mis Comprobantes recién abierto.
                if servicio_arca is not None:
                    try:
                        servicio_arca.close()
                    except Exception:
                        pass
                    servicio_arca = None
                for intento in (1, 2):
                    if servicio_arca is None or servicio_arca.is_closed():
                        servicio_arca = sesion.abrir_mis_comprobantes(_cuit_repres)
                    try:
                        return sesion.descargar(
                            servicio_arca,
                            tipo,
                            p["desde"],
                            p["hasta"],
                            _cli,
                            str(carpeta),
                            bajar_excel=True,
                            bajar_csv=False,
                        )
                    except (SesionVencida, FiltroNoDisponible) as exc:
                        if intento == 2:
                            raise
                        try:
                            servicio_arca.close()
                        except Exception:
                            pass
                        servicio_arca = None
                        if isinstance(exc, SesionVencida):
                            ej.registrar(
                                "      [AVISO]: Sesión de ARCA vencida. "
                                "Se renueva y se reintenta..."
                            )
                            sesion.renovar()
                        else:
                            ej.registrar(
                                "      [AVISO]: ARCA quedó en una pantalla sin filtro de fechas. "
                                "Se vuelve a abrir Mis Comprobantes y se reintenta..."
                            )
                return None

            def correr_pasos_arca(gestor, _pasos=[x for x in cli.pasos if x.id.startswith("arca_")]):
                """Hilo de ARCA: Ventas y Compras en la misma sesión."""
                nonlocal servicio_arca
                try:
                    for paso in _pasos:
                        if ej.cancelar_pedido:
                            paso.estado = "omitido"
                            continue
                        ej.registrar(f" -> {paso.etiqueta}")
                        tipo = "Emitidos" if paso.id == "arca_ventas" else "Recibidos"

                        def hacer_paso_arca(_tipo=tipo):
                            sesion = gestor.arca(cuit_login, clave_arca)
                            return hacer_arca(sesion, _tipo)

                        _correr_paso(ej, paso, hacer_paso_arca)
                        cli.archivos = listar_archivos(carpeta, inicio_cliente)
                finally:
                    if servicio_arca is not None:
                        try:
                            servicio_arca.close()
                        except Exception:
                            pass
                        servicio_arca = None

            def correr_paso_xubio(gestor, _pasos=[x for x in cli.pasos if x.id == "xubio"]):
                """Hilo de Xubio: corre a la par de ARCA."""
                for paso in _pasos:
                    if ej.cancelar_pedido:
                        paso.estado = "omitido"
                        continue
                    ej.registrar(f" -> {paso.etiqueta}")

                    def hacer_xubio():
                        sesion = gestor.xubio(p["xubio_usuario"], xubio_clave)
                        url = sesion.cambiar_empresa(cli.nombre)
                        if isinstance(url, str) and es_url_de_xubio(url):
                            cli.xubio_url = url
                        return sesion.descargar(
                            cli.nombre,
                            p["desde"],
                            p["hasta"],
                            p["ventas"],
                            p["compras"],
                            str(carpeta),
                        )

                    if _correr_paso(ej, paso, hacer_xubio) is not None:
                        gestor.descartar_xubio()
                    cli.archivos = listar_archivos(carpeta, inicio_cliente)

            # ARCA y Xubio no dependen uno del otro: se bajan en paralelo, cada
            # uno en su hilo con su navegador. La auditoría espera a los dos.
            futuros = []
            if any(x.id.startswith("arca_") for x in cli.pasos):
                futuros.append(hilo_arca.enviar(correr_pasos_arca))
            if any(x.id == "xubio" for x in cli.pasos):
                futuros.append(hilo_xubio.enviar(correr_paso_xubio))
            for futuro in futuros:
                futuro.result()

            for paso in cli.pasos:
                if not paso.id.startswith("auditoria_"):
                    continue
                if ej.cancelar_pedido:
                    paso.estado = "omitido"
                    continue
                operacion = "Ventas" if paso.id == "auditoria_ventas" else "Compras"
                # Sin la descarga de ARCA o de Xubio no hay nada que cruzar: el
                # paso queda omitido en vez de sumar un segundo error.
                fallidos = [
                    x.etiqueta
                    for x in cli.pasos
                    if x.id in (f"arca_{operacion.lower()}", "xubio") and x.estado == "error"
                ]
                if fallidos:
                    paso.estado = "omitido"
                    paso.mensaje = f"No se cruzó: falló {' y '.join(fallidos)}."
                    ej.registrar(f" -> {paso.etiqueta}: omitida ({paso.mensaje})", "aviso")
                    continue
                ej.registrar(f" -> {paso.etiqueta}")
                errores_auditoria: list[dict] = []
                _correr_paso(
                    ej,
                    paso,
                    lambda op=operacion: procesar_cruce(
                        cli.nombre,
                        op,
                        p["desde"],
                        p["hasta"],
                        str(carpeta),
                        log=ej.registrar,
                        avisar_errores=errores_auditoria.extend,
                        avisar_resumen=lambda r, _c=cli.nombre: ej.resumenes.append({**r, "cliente": _c}),
                    ),
                )
                # Lo que encontró la auditoría no frena el control: el paso queda
                # OK y los errores se muestran al terminar, con cómo solucionarlos
                # (ventana de errores en la web).
                for e in errores_auditoria:
                    ej.advertencias.append({**e, "cliente": cli.nombre})

            # Pasos que fallaron (login, empresa de Xubio, descargas...): también
            # van a la ventana de errores, con qué revisar.
            for x in cli.pasos:
                if x.estado in ("error", "aviso") and x.mensaje:
                    ej.advertencias.append(_error_de_paso(cli.nombre, x))

            cli.archivos = listar_archivos(carpeta, inicio_cliente)
            if not cli.archivos and descartar_si_vacia(carpeta):
                # No bajó nada: el control no cuenta y el próximo usa este número.
                ej.registrar(
                    f" -> No se generó ningún archivo: se borró la carpeta «{carpeta.name}» "
                    "y el próximo control del período usa ese número.",
                    "aviso",
                )
                cli.carpeta = ""
            cli.estado = (
                "con_errores"
                if any(x.estado in ("error", "aviso") for x in cli.pasos)
                else "ok"
            )
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
            ej.mensaje = (
                "Terminó sin errores."
                if not con_errores
                else f"Terminó: {con_errores} cliente(s) con errores o avisos."
            )
            ej.registrar("[OK] PROCESO DE CONTROL DE FACTURAS FINALIZADO.", "ok")
    except Exception as exc:
        ej.estado = "error"
        ej.mensaje = f"El control se cortó: {mensaje_legible(str(exc))}"
        ej.registrar(f"[ERROR] El control se cortó: {exc}", "error")
    finally:
        hilo_arca.cerrar()
        hilo_xubio.cerrar()
        ej.pausa = None
        ej.cliente_actual = -1
        ej.terminada = _ahora()
        _guardar(ej)


def ruta_archivo(ej: Ejecucion, indice: int, nombre: str) -> Path:
    """Un archivo generado para un cliente, sin salir de su carpeta."""
    if not 0 <= indice < len(ej.clientes) or not ej.clientes[indice].carpeta:
        raise ErrorEjecucion("Ese cliente no tiene carpeta en esta ejecución.", 404)
    if nombre not in {a["nombre"] for a in ej.clientes[indice].archivos}:
        raise ErrorEjecucion("Ese archivo no es de esta ejecución.", 404)
    carpeta = Path(ej.clientes[indice].carpeta).resolve()
    archivo = (carpeta / nombre).resolve()
    if not archivo.is_relative_to(carpeta) or not archivo.is_file():
        raise ErrorEjecucion("No se encontró el archivo.", 404)
    return archivo


def guardar_archivo(ej: Ejecucion, indice: int, nombre: str, destino: Path) -> dict:
    """Copia un archivo del cliente a la ruta elegida en «Guardar como»."""
    origen = ruta_archivo(ej, indice, nombre)
    try:
        shutil.copy2(origen, destino)
    except PermissionError as exc:
        raise ErrorEjecucion(
            f"No se pudo guardar «{Path(destino).name}»: ya existe y está abierto (¿en Excel?). Cerralo y volvé a intentar.", 409
        ) from exc
    except OSError as exc:
        raise ErrorEjecucion(f"No se pudo guardar «{destino}»: {exc}", 422) from exc
    return {"ruta": str(destino)}


def archivos_del_cliente(ej: Ejecucion, indice: int) -> tuple[Path, list[dict]]:
    """La carpeta de salida del cliente y sus archivos; error si no hay nada que bajar."""
    if not 0 <= indice < len(ej.clientes) or not ej.clientes[indice].carpeta:
        raise ErrorEjecucion("Ese cliente no tiene carpeta en esta ejecución.", 404)
    origen = Path(ej.clientes[indice].carpeta)
    # Solo los de este control (no todo lo que haya en la carpeta del mes).
    archivos = [a for a in ej.clientes[indice].archivos if (origen / a["nombre"]).is_file()]
    if not archivos:
        raise ErrorEjecucion("Ese cliente no tiene archivos generados.", 404)
    return origen, archivos


def operacion_de(nombre: str) -> str | None:
    """«Ventas» o «Compras» según el archivo; None si no se sabe."""
    n = nombre.casefold()
    if " emitidos " in n or n.startswith("iva ventas") or "facturas ventas" in n:
        return "Ventas"
    if " recibidos " in n or n.startswith("iva compras") or "facturas compras" in n:
        return "Compras"
    return None


def plan_de_archivos(ej: Ejecucion, indice: int) -> tuple[Path, list[tuple[str, Path]]]:
    """La carpeta de origen y, por archivo, su ruta relativa dentro de <mmaaaa>:
    `Control de facturas/<N° control>/…`, todos juntos (Ventas y Compras)."""
    origen, archivos = archivos_del_cliente(ej, indice)
    destino = Path(SUBCARPETA_CONTROL)
    if numero_de_carpeta(origen):
        destino = destino / origen.name
    return origen, [(a["nombre"], destino / a["nombre"]) for a in archivos]


def zip_de_archivos(ej: Ejecucion, indice: int) -> tuple[str, bytes]:
    """«Descargar todos» sin ventanas (servidor): un .zip con
    <Cliente>/<mmaaaa>/Control de facturas/<N° control>/…"""
    origen, plan = plan_de_archivos(ej, indice)
    _, mes, anio = ej.parametros["desde"].split("/")
    raiz = Path(nombre_carpeta(ej.clientes[indice].nombre), f"{mes}{anio}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre, relativa in plan:
            z.write(origen / nombre, (raiz / relativa).as_posix())
    return f"{raiz.parts[0]} - {mes}{anio}.zip", buffer.getvalue()


def _misma_carpeta(a: Path, b: Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def exportar_archivos(
    ej: Ejecucion, indice: int, destino: Path | None, base_por_defecto: Path | None = None
) -> dict:
    """Copia los archivos del cliente con el formato de la app de escritorio y DRLiqui.

    - Con carpeta elegida (por ejemplo, la del cliente que ya existe):
      `<elegida>/<mmaaaa>/Control de facturas/`.
    - Sin carpeta elegida: se crea la del cliente en Descargas:
      `<Descargas>/<Cliente>/<mmaaaa>/Control de facturas/`. Si vuelve Descargas
      misma (la ventana abre ahí y se tocó «Seleccionar carpeta» sin entrar a
      ninguna) cuenta como sin elegir.
    - Ventas y Compras van juntos, sin subcarpetas por operación.

    Si uno no se puede copiar (por ejemplo, ya existe y está abierto en Excel),
    los demás se copian igual y ese queda en `fallas`.
    """
    origen, plan = plan_de_archivos(ej, indice)
    _, mes, anio = ej.parametros["desde"].split("/")
    base = Path(base_por_defecto or Path.home() / "Downloads")
    if destino is not None and _misma_carpeta(destino, base):
        destino = None
    try:
        if destino is not None:
            if not Path(destino).is_dir():
                raise ErrorEjecucion(f"La carpeta «{destino}» no existe.", 422)
            carpeta = Path(destino) / f"{mes}{anio}"
            carpeta.mkdir(parents=True, exist_ok=True)
        else:
            # carpeta_cliente ya incluye «Control de facturas»; el plan la agrega.
            carpeta = carpeta_cliente(base, ej.clientes[indice].nombre, mes, anio).parent
    except OSError as exc:
        raise ErrorEjecucion(f"No se pudo crear la carpeta para los archivos: {exc}", 422) from exc

    copiados: list[str] = []
    fallas: list[dict] = []
    for nombre, relativa in plan:
        destino_archivo = carpeta / relativa
        try:
            destino_archivo.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origen / nombre, destino_archivo)
            copiados.append(nombre)
        except PermissionError:
            fallas.append({"nombre": nombre, "motivo": "Ya existe y está abierto (¿en Excel?). Cerralo y volvé a descargar."})
        except OSError as exc:
            fallas.append({"nombre": nombre, "motivo": str(exc)})
    carpeta_final = carpeta / plan[0][1].parent if plan else carpeta / SUBCARPETA_CONTROL
    return {"carpeta": str(carpeta_final), "elegida": destino is not None, "copiados": copiados, "fallas": fallas}



# --- Aplicar cambios: corregir el Libro IVA y volver a cruzar -------------------

SUFIJO_CORREGIDO = " - corregido"
_lock_aplicar = threading.Lock()


def _con_sufijo(nombre: str) -> str:
    base = Path(nombre)
    return f"{base.stem}{SUFIJO_CORREGIDO}{base.suffix}"


def es_corregido(nombre: str) -> bool:
    return Path(nombre).stem.endswith(SUFIJO_CORREGIDO)


def _archivo_de(cli: ClienteEnCurso, patron: str) -> str | None:
    """El archivo original (no el corregido) del cliente que empieza con ``patron``."""
    for a in cli.archivos:
        if a["nombre"].lower().startswith(patron.lower()) and not es_corregido(a["nombre"]):
            return a["nombre"]
    return None


def aplicar_cambios(ej: Ejecucion, cambios: list[dict]) -> dict:
    """Corrige en una copia del Libro IVA las celdas elegidas (letra, tipo, importe o
    CUIT con el dato de ARCA) y vuelve a correr el cruce con esa copia.

    Por cliente y operación queda «IVA … - corregido.xlsx» (cada vez se corrige sobre el
    último corregido) y «AUX … - corregido.xlsx», que pasa a ser el activo para bajar y
    para «Faltantes a Xubio». Los hallazgos y el resumen de esa operación se reemplazan
    por los del cruce nuevo.
    """
    if ej.estado not in ESTADOS_FINALES:
        raise ErrorEjecucion("El control todavía está corriendo.", 409)
    if not cambios:
        raise ErrorEjecucion("No hay cambios para aplicar.", 422)
    por_operacion: dict[tuple[str, str], list[dict]] = {}
    for c in cambios:
        por_operacion.setdefault((c["cliente"], c["operacion"]), []).append(c)

    if not _lock_aplicar.acquire(blocking=False):
        raise ErrorEjecucion("Ya se están aplicando cambios. Esperá a que termine.", 409)
    resultados: list[dict] = []
    fallas: list[ErrorEjecucion] = []
    try:
        # Cada cliente y operación por su cuenta: si uno falla (por ejemplo, su
        # Excel está abierto), los demás se aplican igual y se informa cuál no.
        for (nombre, operacion), lista in por_operacion.items():
            indice = next((i for i, c in enumerate(ej.clientes) if c.nombre == nombre), None)
            try:
                if indice is None:
                    raise ErrorEjecucion(f"«{nombre}» no es un cliente de este control.", 404)
                resultados.append(_aplicar_en(ej, indice, operacion, lista))
            except ErrorEjecucion as exc:
                fallas.append(exc)
                resultados.append({
                    "cliente": nombre, "indice": indice if indice is not None else -1, "operacion": operacion,
                    "aplicados": 0, "no_aplicados": [], "libro": None, "aux": None, "error": exc.mensaje,
                })
        if fallas and len(fallas) == len(resultados):
            raise fallas[0] if len(fallas) == 1 else ErrorEjecucion(
                " ".join(r["error"] for r in resultados), fallas[0].estado
            )
        return {"resultados": resultados, "ejecucion": ej.detalle()}
    finally:
        try:
            # Lo que sí se aplicó queda guardado aunque otra operación haya fallado.
            if len(fallas) < len(resultados):
                _guardar(ej)
        finally:
            _lock_aplicar.release()


def _aplicar_en(ej: Ejecucion, indice: int, operacion: str, cambios: list[dict]) -> dict:
    cli = ej.clientes[indice]
    if not cli.carpeta or not Path(cli.carpeta).is_dir():
        raise ErrorEjecucion(f"No está la carpeta del control de {cli.nombre}.", 404)
    carpeta = Path(cli.carpeta)
    libro = _archivo_de(cli, f"IVA {operacion.upper()} - ")
    tipo_arca = "Emitidos" if operacion == "Ventas" else "Recibidos"
    arca = _archivo_de(cli, f"Mis comprobantes - {tipo_arca} - ")
    aux = _archivo_de(cli, f"AUX - Control de facturas {operacion} ")
    if not libro or not arca or not aux:
        faltan = [n for n, v in (("Libro IVA", libro), ("ARCA", arca), ("AUX", aux)) if not v]
        raise ErrorEjecucion(
            f"{cli.nombre} · {operacion}: falta {', '.join(faltan)} en la carpeta del control.", 422
        )
    libro_corr = carpeta / _con_sufijo(libro)
    aux_corr = carpeta / _con_sufijo(aux)
    # Se corrige sobre lo ya corregido: los cambios anteriores no se pierden.
    origen = libro_corr if libro_corr.is_file() else carpeta / libro
    # Si el re-cruce falla, el Libro IVA corregido vuelve a como estaba: así el
    # archivo y los hallazgos guardados nunca quedan desparejos.
    previo_libro = libro_corr.read_bytes() if libro_corr.is_file() else None
    try:
        hecho = corregir_libro(origen, libro_corr, [{k: v for k, v in c.items() if k not in ("cliente", "operacion")} for c in cambios])
    except ArchivoEnUso as exc:
        raise ErrorEjecucion(f"{cli.nombre} · {operacion}: {exc}", 409) from exc
    except (LibroIlegible, OSError) as exc:
        raise ErrorEjecucion(f"No se pudo corregir el Libro IVA de {cli.nombre}: {exc}", 422) from exc

    def deshacer_libro() -> None:
        try:
            if previo_libro is None:
                libro_corr.unlink(missing_ok=True)
            else:
                libro_corr.write_bytes(previo_libro)
        except OSError:
            pass

    errores: list[dict] = []
    resumenes: list[dict] = []
    log: list[str] = []
    try:
        procesar_cruce(
            cli.nombre,
            operacion,
            ej.parametros["desde"],
            ej.parametros["hasta"],
            str(carpeta),
            ruta_arca_manual=str(carpeta / arca),
            ruta_xubio_manual=str(libro_corr),
            ruta_salida_manual=str(aux_corr),
            log=log.append,
            avisar_errores=errores.extend,
            avisar_resumen=resumenes.append,
        )
    except ArchivoEnUso as exc:
        deshacer_libro()
        raise ErrorEjecucion(f"{cli.nombre} · {operacion}: {exc} No se aplicó ningún cambio.", 409) from exc
    except BotError as exc:
        deshacer_libro()
        raise ErrorEjecucion(f"No se pudo volver a cruzar {cli.nombre} · {operacion}: {exc}", 422) from exc
    except Exception as exc:
        deshacer_libro()
        raise ErrorEjecucion(
            f"No se pudo volver a cruzar {cli.nombre} · {operacion}: {mensaje_legible(str(exc))} "
            "No se aplicó ningún cambio.",
            500,
        ) from exc

    # Los hallazgos de esa operación pasan a ser los del cruce nuevo (los pasos que
    # fallaron, sin operación, quedan).
    ej.advertencias = [
        a for a in ej.advertencias
        if not (a.get("cliente") == cli.nombre and a.get("operacion") == operacion and a.get("tipo") != "paso_error")
    ] + [{**e, "cliente": cli.nombre} for e in errores]
    ej.resumenes = [
        r for r in ej.resumenes if not (r.get("cliente") == cli.nombre and r.get("operacion") == operacion)
    ] + [{**r, "cliente": cli.nombre} for r in resumenes]

    nombres = {a["nombre"] for a in cli.archivos} | {libro_corr.name, aux_corr.name}
    cli.archivos = [a for a in listar_archivos(carpeta) if a["nombre"] in nombres]
    previo = cli.corregidos.get(operacion) or {}
    cli.corregidos[operacion] = {
        "libro": libro_corr.name,
        "aux": aux_corr.name,
        "fecha": _ahora(),
        "cambios": list(previo.get("cambios") or []) + [
            {k: v for k, v in c.items() if k in ("campo", "cuit", "pv", "numero", "comprobante", "valor_arca", "antes", "despues")}
            for c in hecho["aplicados"]
        ],
    }
    ej.registrar(
        f"[CAMBIOS] {cli.nombre} · {operacion}: {len(hecho['aplicados'])} aplicado(s) en "
        f"«{libro_corr.name}»; nuevo control «{aux_corr.name}».",
        "ok",
    )
    return {
        "cliente": cli.nombre,
        "indice": indice,
        "operacion": operacion,
        "aplicados": len(hecho["aplicados"]),
        "no_aplicados": hecho["no_aplicados"],
        "libro": libro_corr.name,
        "aux": aux_corr.name,
    }
