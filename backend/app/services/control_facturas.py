"""Orquestación del control de facturas (sin GUI).

Este módulo es el corazón de la migración: reproduce exactamente el flujo de
`main_control._run_scrappy`, pero sin Tkinter, sin `sys.stdout` global y con
errores tipados. Se puede llamar desde la API, desde un script o desde tests.

Flujo por cliente:

    ARCA (Emitidos / Recibidos)  ->  Xubio (IVA Ventas / Compras)
                                 ->  cruce FO 04-01
                                 ->  AUX - Control de facturas ...xlsx

Cada paso es independiente: si uno falla, se registra el error y se continúa
con el siguiente, igual que hacía la app de escritorio.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from ..auditor import procesar_cruce
from ..bots import extraer_arca, extraer_xubio
from ..bots.sesiones import GestorSesiones
from ..core.errors import BotError, Cancelado, ConfigInvalida, SesionVencida
from ..core.models import JobError, JobProgress, JobRequest, JobResult
from ..core.paths import carpeta_cliente

Log = Callable[[str], None]
CanceladoFn = Callable[[], bool]
ProgresoFn = Callable[[JobProgress], None]


def _error(exc: BaseException, cliente: str, paso: str) -> JobError:
    if isinstance(exc, BotError):
        return JobError(
            code=exc.code,
            mensaje=exc.mensaje,
            cliente=cliente,
            paso=paso,
            detalle=exc.detalle,
        )
    return JobError(code="bot_error", mensaje=str(exc), cliente=cliente, paso=paso)


def _sin_datos(cliente: str, paso: str, resultado: JobResult) -> None:
    etiqueta = f"{cliente} ({paso})"
    if etiqueta not in resultado.sin_datos:
        resultado.sin_datos.append(etiqueta)


def _agregar_archivos(resultado: JobResult, archivos: list[str]) -> None:
    for ruta in archivos:
        if ruta and ruta not in resultado.archivos:
            resultado.archivos.append(ruta)


def _arca_con_sesion(
    sesiones: GestorSesiones,
    cuit_login: str,
    clave_arca: str,
    cuit_repres: str,
    request: JobRequest,
    cliente: str,
    carpeta_final: Path,
    resultado: JobResult,
    log: Log,
    cancelado: CanceladoFn,
) -> None:
    """Baja Emitidos y Recibidos reutilizando una sola sesión y ventana de ARCA."""
    servicio = None
    try:
        sesion = sesiones.arca(cuit_login, clave_arca)
        for tipo, hacer in (("Emitidos", request.ventas), ("Recibidos", request.compras)):
            if not hacer:
                continue
            if cancelado():
                raise Cancelado(f"Cancelado antes de descargar ARCA {tipo} de '{cliente}'.")
            try:
                if servicio is None or servicio.is_closed():
                    servicio = sesion.abrir_mis_comprobantes(cuit_repres)
                res = sesion.descargar(
                    servicio,
                    tipo,
                    request.fecha_desde,
                    request.fecha_hasta,
                    cliente,
                    str(carpeta_final),
                    bajar_excel=True,
                    bajar_csv=False,
                )
            except SesionVencida:
                # Se renueva la sesión y se reintenta una vez con una ventana nueva.
                log("      [AVISO]: Sesión de ARCA vencida. Se renueva y se reintenta...")
                sesion.renovar()
                try:
                    if servicio is not None and not servicio.is_closed():
                        servicio.close()
                except Exception:
                    pass
                servicio = sesion.abrir_mis_comprobantes(cuit_repres)
                try:
                    res = sesion.descargar(
                        servicio,
                        tipo,
                        request.fecha_desde,
                        request.fecha_hasta,
                        cliente,
                        str(carpeta_final),
                        bajar_excel=True,
                        bajar_csv=False,
                    )
                except Exception as exc:
                    log(f" [ERROR] ARCA ({tipo}): {exc}")
                    resultado.errores.append(_error(exc, cliente, "arca"))
                    continue
            except Exception as exc:
                log(f" [ERROR] ARCA ({tipo}): {exc}")
                resultado.errores.append(_error(exc, cliente, "arca"))
                continue

            _agregar_archivos(resultado, res.archivos)
            if res.sin_datos:
                _sin_datos(cliente, f"arca_{tipo.lower()}", resultado)
    finally:
        if servicio is not None:
            try:
                servicio.close()
            except Exception:
                pass


def ejecutar_control_facturas(
    *,
    request: JobRequest,
    clientes: Mapping[str, Mapping[str, Any]],
    xubio_user: str,
    xubio_pass: str,
    base_salida: Path,
    log: Log,
    cancelado: CanceladoFn = lambda: False,
    on_progreso: ProgresoFn | None = None,
    sesiones: GestorSesiones | None = None,
) -> JobResult:
    """Ejecuta el pipeline completo y devuelve qué se generó y qué falló.

    `clientes` es el mapa nombre -> datos de `config.json` (solo lectura).
    `base_salida` es la carpeta raíz elegida por el usuario.

    Si se pasa `sesiones`, se reutilizan las sesiones de ARCA/Xubio del job (un
    solo login por credencial). Si no, se mantiene el comportamiento histórico
    de abrir una sesión por llamada (app de escritorio y tests).
    """
    resultado = JobResult()
    total = len(request.clientes)
    pasos = set(request.pasos)

    _, mes, anio = request.fecha_desde.split("/")

    log("[INICIO] CONTROL DE FACTURAS")
    log("=========================================")

    for index, cliente in enumerate(request.clientes, start=1):
        if cancelado():
            raise Cancelado("El job fue cancelado antes de procesar el siguiente cliente.")

        log(f"[{index}/{total}] PROCESANDO: {cliente}")
        if on_progreso:
            on_progreso(
                JobProgress(actual=index - 1, total=total, cliente=cliente, paso="inicio")
            )

        datos = clientes.get(cliente)
        if datos is None:
            log(f" [ERROR] El cliente '{cliente}' no existe en config.json.")
            resultado.errores.append(
                JobError(
                    code="cliente_no_encontrado",
                    mensaje=f"El cliente '{cliente}' no existe en config.json.",
                    cliente=cliente,
                )
            )
            continue

        datos_arca = datos.get("arca", {}) or {}
        cuit_login = datos_arca.get("cuit_login", "")
        clave_arca = datos_arca.get("clave", "")
        cuit_repres = datos_arca.get("cuit_representado", "")
        url_xubio = (datos.get("xubio", {}) or {}).get("url", "")

        carpeta_final = carpeta_cliente(base_salida, cliente, mes, anio)
        log(f" -> Directorio Destino: {carpeta_final}")

        # -- 1. ARCA -------------------------------------------------------
        if "arca" in pasos:
            if on_progreso:
                on_progreso(
                    JobProgress(actual=index - 1, total=total, cliente=cliente, paso="arca")
                )
            if sesiones is None:
                for tipo, hacer in (("Emitidos", request.ventas), ("Recibidos", request.compras)):
                    if not hacer:
                        continue
                    if cancelado():
                        raise Cancelado(f"Cancelado antes de descargar ARCA {tipo} de '{cliente}'.")
                    try:
                        res = extraer_arca(
                            cuit_login,
                            clave_arca,
                            cuit_repres,
                            request.fecha_desde,
                            request.fecha_hasta,
                            tipo,
                            cliente,
                            str(carpeta_final),
                            bajar_excel=True,
                            bajar_csv=False,
                            log=log,
                        )
                        _agregar_archivos(resultado, res.archivos)
                        if res.sin_datos:
                            _sin_datos(cliente, f"arca_{tipo.lower()}", resultado)
                    except Exception as exc:
                        log(f" [ERROR] ARCA ({tipo}): {exc}")
                        resultado.errores.append(_error(exc, cliente, "arca"))
            else:
                _arca_con_sesion(
                    sesiones,
                    cuit_login,
                    clave_arca,
                    cuit_repres,
                    request,
                    cliente,
                    carpeta_final,
                    resultado,
                    log,
                    cancelado,
                )

        # -- 2. Xubio ------------------------------------------------------
        if "xubio" in pasos:
            if on_progreso:
                on_progreso(
                    JobProgress(actual=index - 1, total=total, cliente=cliente, paso="xubio")
                )
            if cancelado():
                raise Cancelado(f"Cancelado antes de descargar Xubio de '{cliente}'.")
            try:
                if sesiones is None:
                    res = extraer_xubio(
                        xubio_user,
                        xubio_pass,
                        cliente,
                        url_xubio,
                        request.fecha_desde,
                        request.fecha_hasta,
                        request.ventas,
                        request.compras,
                        str(carpeta_final),
                        log=log,
                    )
                else:
                    sesion_xubio = sesiones.xubio(xubio_user, xubio_pass)
                    sesion_xubio.cambiar_empresa(cliente)
                    res = sesion_xubio.descargar(
                        cliente,
                        request.fecha_desde,
                        request.fecha_hasta,
                        request.ventas,
                        request.compras,
                        str(carpeta_final),
                    )
                _agregar_archivos(resultado, res.archivos)
                if res.sin_datos:
                    _sin_datos(cliente, "xubio", resultado)
                for err in res.errores:
                    resultado.errores.append(
                        JobError(
                            code=err.get("code", "bot_error"),
                            mensaje=err.get("mensaje", ""),
                            cliente=cliente,
                            paso="xubio",
                            detalle=err.get("detalle"),
                        )
                    )
            except Exception as exc:
                log(f" [ERROR] XUBIO: {exc}")
                resultado.errores.append(_error(exc, cliente, "xubio"))
                if sesiones is not None:
                    # La sesión quedó rota: el próximo cliente vuelve a entrar.
                    sesiones.descartar_xubio()

        # -- 3. Auditoría --------------------------------------------------
        if "auditoria" in pasos:
            if on_progreso:
                on_progreso(
                    JobProgress(actual=index - 1, total=total, cliente=cliente, paso="auditoria")
                )
            for operacion, hacer in (("Ventas", request.ventas), ("Compras", request.compras)):
                if not hacer:
                    continue
                if cancelado():
                    raise Cancelado(f"Cancelado antes de auditar {operacion} de '{cliente}'.")
                try:
                    ruta = procesar_cruce(
                        cliente,
                        operacion,
                        request.fecha_desde,
                        request.fecha_hasta,
                        str(carpeta_final),
                        log=log,
                    )
                    _agregar_archivos(resultado, [ruta])
                except Exception as exc:
                    log(f" [ERROR] CRUCE ({operacion}): {exc}")
                    resultado.errores.append(_error(exc, cliente, "auditoria"))

        log("-----------------------------------------")
        if on_progreso:
            on_progreso(
                JobProgress(actual=index, total=total, cliente=cliente, paso="fin")
            )

    if not request.clientes:
        raise ConfigInvalida("No hay clientes para procesar.")

    log("[FIN] PROCESO DE CONTROL DE FACTURAS FINALIZADO.")
    return resultado
