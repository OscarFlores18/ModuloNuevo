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

import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..auditor import procesar_cruce
from ..bots.sesiones import GestorSesiones
from ..core import config
from ..core.errors import BotError, SesionVencida
from ..core.paths import carpeta_cliente
from ..core.storage import ClientesRepository, CredencialesXubioRepository
from .credenciales import ServicioCredenciales

MAX_HISTORIAL = 20
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

    # Interno: la respuesta a la pausa.
    _respuesta: str | None = None
    _evento: threading.Event = field(default_factory=threading.Event)

    def registrar(self, texto: str, tipo: str | None = None) -> None:
        texto = _EMOJIS.sub("", str(texto)).rstrip()
        if not texto.strip():
            return
        self.log.append(
            {"cuando": _ahora(), "texto": texto, "tipo": tipo or _tipo_de_linea(texto)}
        )
        if len(self.log) > MAX_LINEAS_LOG:
            sobra = len(self.log) - MAX_LINEAS_LOG
            del self.log[:sobra]
            self.log_base += sobra
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


# --- Registro de ejecuciones -------------------------------------------------

_ejecuciones: dict[str, Ejecucion] = {}
_orden: list[str] = []
_lock = threading.Lock()


def listar() -> list[dict]:
    with _lock:
        return [_ejecuciones[i].resumen() for i in reversed(_orden)]


def obtener(ejecucion_id: str) -> Ejecucion:
    with _lock:
        e = _ejecuciones.get(ejecucion_id)
    if e is None:
        raise ErrorEjecucion(
            "No existe esa ejecución (se borran al reiniciar el servidor).", 404
        )
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
        return datetime.strptime(str(texto).strip(), "%d/%m/%Y")
    except (ValueError, AttributeError):
        raise ErrorEjecucion(
            f"La fecha «{etiqueta}» tiene que tener el formato DD/MM/AAAA.", 422
        ) from None


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
    desde = ej.log_base + len(ej.log)
    error: BaseException | None = None
    try:
        funcion()
    except Exception as exc:  # los bots tiran Exception genérica
        error = exc
        paso.estado = "error"
        paso.mensaje = " ".join(str(exc).split())[:400] or type(exc).__name__
        ej.registrar(f" [ERROR] {paso.etiqueta}: {exc}", "error")
    else:
        # Algunos bots atrapan su propio error y solo lo registran.
        errores = [
            linea["texto"]
            for linea in ej.log[max(0, desde - ej.log_base) :]
            if linea["tipo"] == "error"
        ]
        if errores:
            paso.estado = "aviso"
            paso.mensaje = errores[-1].strip()[:300]
        else:
            paso.estado = "ok"
    paso.fin = _ahora()
    return error


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


def _correr(ej: Ejecucion, xubio_clave: str, servicio: ServicioCredenciales) -> None:
    p = ej.parametros
    _, mes, anio = p["desde"].split("/")
    gestor = GestorSesiones(log=ej.registrar)
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

            carpeta = carpeta_cliente(Path(p["carpeta"]), cli.nombre, mes, anio)
            cli.carpeta = str(carpeta)
            inicio_cliente = time.time()
            ej.registrar(f" -> Carpeta: {carpeta}")

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
                    except SesionVencida:
                        if intento == 2:
                            raise
                        ej.registrar(
                            "      [AVISO]: Sesión de ARCA vencida. "
                            "Se renueva y se reintenta..."
                        )
                        try:
                            servicio_arca.close()
                        except Exception:
                            pass
                        servicio_arca = None
                        sesion.renovar()
                return None

            try:
                for paso in cli.pasos:
                    if ej.cancelar_pedido:
                        paso.estado = "omitido"
                        continue
                    ej.registrar(f" -> {paso.etiqueta}")

                    if paso.id in ("arca_ventas", "arca_compras"):
                        tipo = "Emitidos" if paso.id == "arca_ventas" else "Recibidos"

                        def hacer_paso_arca(_tipo=tipo):
                            sesion = gestor.arca(cuit_login, clave_arca)
                            return hacer_arca(sesion, _tipo)

                        _correr_paso(ej, paso, hacer_paso_arca)

                    elif paso.id == "xubio":
                        def hacer_xubio():
                            sesion = gestor.xubio(p["xubio_usuario"], xubio_clave)
                            sesion.cambiar_empresa(cli.nombre)
                            return sesion.descargar(
                                cli.nombre,
                                p["desde"],
                                p["hasta"],
                                p["ventas"],
                                p["compras"],
                                str(carpeta),
                            )

                        error = _correr_paso(ej, paso, hacer_xubio)
                        if error is not None:
                            gestor.descartar_xubio()

                    elif paso.id.startswith("auditoria_"):
                        operacion = (
                            "Ventas" if paso.id == "auditoria_ventas" else "Compras"
                        )
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
                            ),
                        )

                    cli.archivos = listar_archivos(carpeta, inicio_cliente)
            finally:
                if servicio_arca is not None:
                    try:
                        servicio_arca.close()
                    except Exception:
                        pass

            cli.archivos = listar_archivos(carpeta, inicio_cliente)
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
        ej.mensaje = f"El control se cortó: {exc}"
        ej.registrar(f"[ERROR] El control se cortó: {exc}", "error")
    finally:
        gestor.cerrar()
        ej.pausa = None
        ej.cliente_actual = -1
        ej.terminada = _ahora()


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
      `<elegida>/<mmaaaa>/`.
    - Sin carpeta elegida: se crea la del cliente en Descargas:
      `<Descargas>/<Cliente>/<mmaaaa>/`. Si vuelve Descargas misma (la ventana abre
      ahí y se tocó «Seleccionar carpeta» sin entrar a ninguna) cuenta como sin elegir.
    - Si el control fue de Ventas y Compras, adentro de <mmaaaa> van `Ventas/` y `Compras/`.

    Si uno no se puede copiar (por ejemplo, ya existe y está abierto en Excel),
    los demás se copian igual y ese queda en `fallas`.
    """
    origen, archivos = archivos_del_cliente(ej, indice)
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
            carpeta = carpeta_cliente(base, ej.clientes[indice].nombre, mes, anio)
    except OSError as exc:
        raise ErrorEjecucion(f"No se pudo crear la carpeta para los archivos: {exc}", 422) from exc

    # Con Ventas y Compras en el mismo control, cada una en su subcarpeta.
    separar = bool(ej.parametros.get("ventas")) and bool(ej.parametros.get("compras"))

    copiados: list[str] = []
    fallas: list[dict] = []
    for a in archivos:
        destino_archivo = carpeta
        if separar and (op := operacion_de(a["nombre"])):
            destino_archivo = carpeta / op
        try:
            destino_archivo.mkdir(exist_ok=True)
            shutil.copy2(origen / a["nombre"], destino_archivo / a["nombre"])
            copiados.append(a["nombre"])
        except PermissionError:
            fallas.append({"nombre": a["nombre"], "motivo": "Ya existe y está abierto (¿en Excel?). Cerralo y volvé a descargar."})
        except OSError as exc:
            fallas.append({"nombre": a["nombre"], "motivo": str(exc)})
    return {"carpeta": str(carpeta), "elegida": destino is not None, "copiados": copiados, "fallas": fallas}

