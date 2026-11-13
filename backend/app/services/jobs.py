"""Gestor de jobs: cola + un único worker + cancelación cooperativa.

Los scrapers abren Chrome visible y no son seguros en paralelo, por eso hay
**un solo** hilo de trabajo. La API encola y consulta; el worker ejecuta.

La cancelación es cooperativa: se marca el evento y el pipeline la consulta
entre clientes y entre pasos. No se puede interrumpir un `click` de Playwright
a mitad de camino, pero el job se detiene en el siguiente punto seguro.
"""

from __future__ import annotations

import queue
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..core import config
from ..core.errors import BotError, Cancelado
from ..core.logging import JobLog, get_logger
from ..core.models import (
    Job,
    JobError,
    JobFile,
    JobLogEntry,
    JobProgress,
    JobRequest,
    JobResumen,
    JobResult,
    JobStatus,
)
from ..core.storage import ClientesRepository, CredencialesXubioRepository
from ..bots.sesiones import GestorSesiones
from .control_facturas import ejecutar_control_facturas
from .credenciales import ServicioCredenciales


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class JobManager:
    def __init__(
        self,
        clientes: ClientesRepository,
        credenciales_xubio: CredencialesXubioRepository,
        base_salida: Path | None = None,
        servicio: ServicioCredenciales | None = None,
    ):
        self._clientes = clientes
        self._credenciales_xubio = credenciales_xubio
        self._servicio = servicio or ServicioCredenciales(
            clientes, credenciales_xubio
        )
        self._base_salida = Path(base_salida) if base_salida else config.OUTPUT_DIR
        self._jobs: dict[str, Job] = {}
        self._cancelaciones: dict[str, threading.Event] = {}
        self._queue: "queue.Queue[str | None]" = queue.Queue()
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._logger = get_logger("modulonuevo.jobs")

    # -- ciclo de vida -----------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._loop, name="control-facturas-worker", daemon=True
            )
            self._worker.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            worker = self._worker
        if not worker:
            return
        self._queue.put(None)
        worker.join(timeout=timeout)
        with self._lock:
            self._worker = None

    @property
    def en_ejecucion(self) -> str | None:
        with self._lock:
            for job in self._jobs.values():
                if job.status is JobStatus.RUNNING:
                    return job.id
        return None

    # -- API interna -------------------------------------------------------
    def crear(self, request: JobRequest) -> Job:
        job = Job(id=uuid.uuid4().hex, request=request)
        with self._lock:
            self._jobs[job.id] = job
            self._cancelaciones[job.id] = threading.Event()
            self._logger.info("Job %s encolado (%s clientes)", job.id, len(request.clientes))
        self._queue.put(job.id)
        return job.model_copy(deep=True)

    def obtener(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def listar(self) -> list[JobResumen]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(), key=lambda j: j.created_at, reverse=True
            )
            return [self.resumen(job) for job in jobs]

    def logs(self, job_id: str, desde: int = 0) -> list[JobLogEntry] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return [entrada.model_copy(deep=True) for entrada in job.logs[desde:]]

    def archivos(self, job_id: str) -> list[JobFile] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return [archivo.model_copy(deep=True) for archivo in job.files]

    def ruta_archivo(self, job_id: str, nombre: str) -> Path | None:
        """Devuelve la ruta real de un archivo generado por ese job.

        Solo se aceptan archivos que el propio job registró: no se arma una
        ruta a partir de lo que manda el cliente HTTP.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            for archivo in job.files:
                if archivo.nombre == nombre:
                    ruta = Path(archivo.ruta)
                    return ruta if ruta.is_file() else None
        return None

    def cancelar(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            evento = self._cancelaciones[job_id]
            evento.set()
            if job.status is JobStatus.QUEUED:
                job.status = JobStatus.CANCELLED
                job.finished_at = _ahora()
                self._logger.info("Job %s cancelado antes de arrancar", job_id)
            elif job.status is JobStatus.RUNNING:
                JobLog(job, lock=self._lock)(
                    "      [AVISO]: Cancelación solicitada. Se detendrá en el próximo punto seguro."
                )
            return job.model_copy(deep=True)

    def eliminar(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.status is JobStatus.RUNNING:
                return False
            del self._jobs[job_id]
            self._cancelaciones.pop(job_id, None)
            return True

    # -- worker ------------------------------------------------------------
    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None or job.status is not JobStatus.QUEUED:
                    continue
                job.status = JobStatus.RUNNING
                job.started_at = _ahora()
                cancelacion = self._cancelaciones[job_id]
            self._ejecutar(job, cancelacion)

    def _ejecutar(self, job: Job, cancelacion: threading.Event) -> None:
        log = JobLog(job, lock=self._lock)

        def on_progreso(progreso: JobProgress) -> None:
            with self._lock:
                job.progress = progreso

        sesiones = GestorSesiones(log=log)
        try:
            if cancelacion.is_set():
                raise Cancelado("Cancelado antes de iniciar.")

            if "xubio" in job.request.pasos:
                credenciales_xubio = self._servicio.xubio(
                    job.request.xubio_user, job.request.xubio_pass
                )
                usuario, clave = (
                    credenciales_xubio.usuario,
                    credenciales_xubio.clave,
                )
            else:
                usuario = clave = ""

            base = (
                Path(job.request.carpeta_salida).expanduser()
                if job.request.carpeta_salida
                else self._base_salida
            )
            base.mkdir(parents=True, exist_ok=True)

            resultado = ejecutar_control_facturas(
                request=job.request,
                clientes=self._clientes.listar_publico(),
                xubio_user=usuario,
                xubio_pass=clave,
                base_salida=base,
                log=log,
                cancelado=cancelacion.is_set,
                on_progreso=on_progreso,
                sesiones=sesiones,
                credenciales=self._servicio,
            )

            with self._lock:
                job.resultado = resultado
                job.errores = list(resultado.errores)
                job.files = self._archivos_de(resultado)
                job.status = JobStatus.PARTIAL if resultado.errores else JobStatus.SUCCEEDED

        except Cancelado as exc:
            log(f"[CANCELADO] {exc}")
            with self._lock:
                job.status = JobStatus.CANCELLED
        except BotError as exc:
            log(f"[ERROR] {exc}", level="error")
            with self._lock:
                job.status = JobStatus.FAILED
                job.errores.append(
                    JobError(
                        code=exc.code,
                        mensaje=exc.mensaje,
                        paso=exc.detalle.get("paso") if isinstance(exc.detalle, dict) else None,
                        detalle=exc.detalle,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - el worker nunca debe morir
            self._logger.exception("Job %s falló de forma inesperada", job.id)
            log(f"[ERROR] Fallo inesperado: {exc}", level="error")
            with self._lock:
                job.status = JobStatus.FAILED
                job.errores.append(
                    JobError(code="error_interno", mensaje=str(exc))
                )
        finally:
            sesiones.cerrar()
            with self._lock:
                job.finished_at = _ahora()

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _archivos_de(resultado: JobResult) -> list[JobFile]:
        archivos: list[JobFile] = []
        vistos: set[str] = set()
        for ruta_cruda in resultado.archivos:
            ruta = Path(ruta_cruda)
            if ruta.name in vistos:
                continue
            vistos.add(ruta.name)
            try:
                size = ruta.stat().st_size
            except OSError:
                size = 0
            archivos.append(JobFile(nombre=ruta.name, ruta=str(ruta), size=size))
        return archivos

    @staticmethod
    def resumen(job: Job) -> JobResumen:
        return JobResumen(
            id=job.id,
            status=job.status,
            request=job.request,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            progress=job.progress,
            total_archivos=len(job.files),
            total_errores=len(job.errores),
        )
