"""Endpoints de jobs (control de facturas)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from ...core.models import (
    Job,
    JobFile,
    JobLogEntry,
    JobRequest,
    JobResumen,
)
from ...core.storage import ClientesRepository
from ...services.jobs import JobManager
from ..deps import get_clientes, get_jobs

router = APIRouter(tags=["jobs"])


def _obtener_o_404(manager: JobManager, job_id: str) -> Job:
    job = manager.obtener(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No existe el job '{job_id}'")
    return job


@router.post("/jobs", response_model=JobResumen, status_code=status.HTTP_201_CREATED)
def crear_job(
    body: JobRequest,
    manager: JobManager = Depends(get_jobs),
    clientes: ClientesRepository = Depends(get_clientes),
) -> JobResumen:
    desconocidos = [c for c in body.clientes if clientes.obtener(c) is None]
    if desconocidos:
        raise HTTPException(
            status_code=400,
            detail=f"Clientes inexistentes en config.json: {', '.join(desconocidos)}",
        )
    job = manager.crear(body)
    return manager.resumen(job)


@router.get("/jobs", response_model=list[JobResumen])
def listar_jobs(manager: JobManager = Depends(get_jobs)) -> list[JobResumen]:
    return manager.listar()


@router.get("/jobs/{job_id}", response_model=Job)
def obtener_job(job_id: str, manager: JobManager = Depends(get_jobs)) -> Job:
    return _obtener_o_404(manager, job_id)


@router.get("/jobs/{job_id}/logs", response_model=list[JobLogEntry])
def obtener_logs(
    job_id: str,
    desde: int = Query(0, ge=0, description="Índice desde el que devolver logs"),
    manager: JobManager = Depends(get_jobs),
) -> list[JobLogEntry]:
    logs = manager.logs(job_id, desde=desde)
    if logs is None:
        raise HTTPException(status_code=404, detail=f"No existe el job '{job_id}'")
    return logs


@router.get("/jobs/{job_id}/files", response_model=list[JobFile])
def listar_archivos(
    job_id: str, manager: JobManager = Depends(get_jobs)
) -> list[JobFile]:
    archivos = manager.archivos(job_id)
    if archivos is None:
        raise HTTPException(status_code=404, detail=f"No existe el job '{job_id}'")
    return archivos


@router.get("/jobs/{job_id}/files/{nombre}")
def descargar_archivo(
    job_id: str, nombre: str, manager: JobManager = Depends(get_jobs)
) -> FileResponse:
    if Path(nombre).name != nombre:
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido")
    ruta = manager.ruta_archivo(job_id, nombre)
    if ruta is None:
        raise HTTPException(status_code=404, detail=f"No existe el archivo '{nombre}'")
    return FileResponse(
        path=ruta,
        filename=ruta.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/jobs/{job_id}/cancel", response_model=JobResumen)
def cancelar_job(
    job_id: str, manager: JobManager = Depends(get_jobs)
) -> JobResumen:
    job = manager.cancelar(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No existe el job '{job_id}'")
    return manager.resumen(job)


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_job(job_id: str, manager: JobManager = Depends(get_jobs)) -> None:
    if not manager.eliminar(job_id):
        raise HTTPException(
            status_code=409,
            detail="El job no existe o todavía se está ejecutando",
        )
