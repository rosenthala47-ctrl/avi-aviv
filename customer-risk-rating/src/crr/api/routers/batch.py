"""POST /api/v1/batch-score — asynchronous scoring of many customers.

Submitting returns a job id immediately; the work runs in the background and the
client polls ``GET /api/v1/batch-score/{job_id}``. The background function is
synchronous, so Starlette runs it in a threadpool and the CPU-bound scoring never
blocks the event loop.

For the alpha this is an in-process background task. The interface — submit, poll,
per-job status — is the same one a real task queue (Celery, RQ, Arq) exposes, so
the production swap is the executor, not the API.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from crr.api.dependencies import get_jobs, get_scores, get_service
from crr.api.projections import assessment_to_stored
from crr.api.repository import StoredJob
from crr.api.schemas import BatchJobResponse, BatchJobStatus, BatchScoreRequest

router = APIRouter(prefix="/api/v1", tags=["batch"])


def _run_batch(job_id: str, customers: list[dict], explain: bool, service, jobs, scores) -> None:
    job = jobs.get(job_id)
    if job is None:
        return
    job.status = "running"
    jobs.update(job)
    try:
        for i, customer in enumerate(customers, start=1):
            assessment = service.score(customer, audience="internal")
            scores.save(assessment_to_stored(assessment))
            if i % 100 == 0 or i == len(customers):
                job.processed = i
                jobs.update(job)
        job.status = "completed"
        job.processed = len(customers)
        job.completed_at = dt.datetime.now(dt.UTC)
    except Exception as exc:  # noqa: BLE001 — record any failure on the job, don't crash the worker
        job.status = "failed"
        job.error = str(exc)[:500]
        job.completed_at = dt.datetime.now(dt.UTC)
    jobs.update(job)


def _status(job: StoredJob) -> BatchJobStatus:
    return BatchJobStatus(
        job_id=job.job_id, status=job.status, total=job.total, processed=job.processed,
        submitted_at=job.submitted_at, completed_at=job.completed_at, error=job.error,
    )


@router.post("/batch-score", response_model=BatchJobResponse, status_code=202)
def submit_batch(
    request: BatchScoreRequest,
    background: BackgroundTasks,
    service=Depends(get_service),
    jobs=Depends(get_jobs),
    scores=Depends(get_scores),
) -> BatchJobResponse:
    job_id = str(uuid.uuid4())
    job = StoredJob(
        job_id=job_id, status="queued", total=len(request.customers), processed=0,
        submitted_at=dt.datetime.now(dt.UTC),
    )
    jobs.create(job)
    customers = [c.model_dump() for c in request.customers]
    background.add_task(_run_batch, job_id, customers, request.explain, service, jobs, scores)
    return BatchJobResponse(job=_status(job))


@router.get("/batch-score/{job_id}", response_model=BatchJobResponse)
def batch_status(job_id: str, jobs=Depends(get_jobs)) -> BatchJobResponse:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no such job {job_id!r}")
    return BatchJobResponse(job=_status(job))
