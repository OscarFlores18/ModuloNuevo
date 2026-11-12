"""Estado del backend."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...core import config
from ...services.jobs import JobManager
from ..deps import get_jobs

router = APIRouter(tags=["health"])


@router.get("/health")
def health(jobs: JobManager = Depends(get_jobs)) -> dict[str, object]:
    return {
        "status": "ok",
        "worker": "ocupado" if jobs.en_ejecucion else "libre",
        "data_dir": str(config.DATA_DIR),
        "output_dir": str(config.OUTPUT_DIR),
    }
