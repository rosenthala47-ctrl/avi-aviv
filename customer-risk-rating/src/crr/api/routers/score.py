"""POST /api/v1/score — real-time single-customer rating."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from crr.api.audit import emit
from crr.api.dependencies import get_cache, get_scores, get_service, get_settings
from crr.api.projections import assessment_to_result, assessment_to_stored
from crr.api.schemas import ScoreRequest, ScoreResponse
from crr.api.settings import Settings

router = APIRouter(prefix="/api/v1", tags=["score"])


@router.post("/score", response_model=ScoreResponse)
def score(
    request: ScoreRequest,
    service=Depends(get_service),
    scores=Depends(get_scores),
    cache=Depends(get_cache),
    settings: Settings = Depends(get_settings),
) -> ScoreResponse:
    # CPU-bound work in a sync handler: FastAPI runs it in a threadpool, so the
    # event loop stays free and many requests score concurrently.
    started = time.perf_counter()
    customer = request.customer.model_dump()
    events = [e.model_dump() for e in request.events]

    assessment = service.score(customer, events, audience=request.audience, explain=request.explain)

    # Idempotency: a client retry with the same input returns the first score
    # rather than creating a second one at a new timestamp.
    idem_key = f"score:{assessment.customer_id}:{assessment.input_hash}:{request.audience}"
    cached = cache.get(idem_key)
    if cached is None:
        scores.save(assessment_to_stored(assessment))
        cache.set(idem_key, assessment.scored_at.isoformat(), ttl_seconds=settings.idempotency_ttl_seconds)

    assessment.latency_ms = (time.perf_counter() - started) * 1000.0
    emit(assessment.to_audit_record())
    return ScoreResponse(result=assessment_to_result(assessment))
