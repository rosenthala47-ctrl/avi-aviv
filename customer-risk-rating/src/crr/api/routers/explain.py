"""GET /api/v1/explain/{customer_id} — the stored explanation for the last score.

Reads the persisted explanation rather than recomputing it. The score served to
the customer and the explanation shown to a reviewer must be the same event; a
freshly recomputed explanation could differ if the model or policy changed in
between, which is exactly the inconsistency a regulator would flag.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from crr.api.dependencies import get_scores
from crr.api.schemas import ExplanationResponse, ReasonFactorOut

router = APIRouter(prefix="/api/v1", tags=["explain"])


@router.get("/explain/{customer_id}", response_model=ExplanationResponse)
def explain(
    customer_id: str,
    scores=Depends(get_scores),
    audience: str = Query("internal", pattern="^(internal|customer)$"),
) -> ExplanationResponse:
    stored = scores.latest(customer_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"no score on record for customer {customer_id!r}")

    payload = stored.explanation
    factors = payload.get("top_factors", [])
    protective = payload.get("protective_factors", [])
    if audience == "customer":
        from crr.api.scoring import visible_factor

        factors = [f for f in factors if visible_factor(f["code"])]
        protective = [f for f in protective if visible_factor(f["code"])]

    return ExplanationResponse(
        customer_id=stored.customer_id,
        risk_score=stored.risk_score,
        risk_band=stored.risk_band,
        credit_probability=stored.credit_probability,
        financial_crime_probability=stored.financial_crime_probability,
        audience=audience,  # type: ignore[arg-type]
        top_factors=[ReasonFactorOut(**f) for f in factors],
        protective_factors=[ReasonFactorOut(**f) for f in protective],
        model_version=stored.model_version,
        policy_version=stored.policy_version,
        scored_at=stored.scored_at,
    )
