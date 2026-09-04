"""POST /api/v1/events/{customer_id} — push one event, maybe get a new score.

The point of this endpoint, per ``crr.pipelines.rescoring``, is that a caller
does not resend the customer's ~65-field profile to report one transaction —
the engine rebuilds the input from the customer's last stored snapshot plus
the event log. A customer with no score on record yet cannot be re-scored
this way (``reason="not_yet_scored"``, HTTP 200 — the event is still stored
for when they eventually are): the first score for a new customer is always
a normal ``POST /api/v1/score`` with their full profile.

Always computed and returned as the internal view, never audience-filtered:
unlike ``/score``/``/explain``, there is no customer-facing caller of this
endpoint to filter for — it is a machine-to-machine event feed, consumed by
whatever pushed the event, not by the customer it describes.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from crr.api.dependencies import get_rescoring_engine
from crr.api.projections import assessment_to_result
from crr.api.schemas import EventIngestResponse, EventPayload
from crr.pipelines.rescoring import EventInput, RescoringEngine

router = APIRouter(prefix="/api/v1", tags=["events"])


@router.post("/events/{customer_id}", response_model=EventIngestResponse)
def ingest_event(
    customer_id: str,
    payload: EventPayload,
    engine: RescoringEngine = Depends(get_rescoring_engine),
) -> EventIngestResponse:
    started = time.perf_counter()
    event = EventInput(
        event_ts=payload.event_ts,
        event_type=payload.event_type,
        amount=payload.amount,
        counterparty_country=payload.counterparty_country,
        channel=payload.channel,
    )
    outcome = engine.ingest_event(customer_id, event)
    if outcome.assessment is not None:
        outcome.assessment.latency_ms = (time.perf_counter() - started) * 1000.0
    return EventIngestResponse(
        customer_id=outcome.customer_id,
        reason=outcome.reason,
        rescored=outcome.rescored,
        notified=outcome.notified,
        band_changed=outcome.band_changed,
        triggered_by=outcome.triggered_by,
        result=assessment_to_result(outcome.assessment) if outcome.assessment is not None else None,
    )
