"""GET /api/v1/explain/{customer_id} — the stored explanation for the last score.

Reads the persisted explanation rather than recomputing it. The score served to
the customer and the explanation shown to a reviewer must be the same event; a
freshly recomputed explanation could differ if the model or policy changed in
between, which is exactly the inconsistency a regulator would flag.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from crr.api.dependencies import get_scores
from crr.api.projections import rule_dict_to_out
from crr.api.schemas import ExplanationResponse, ReasonFactorOut
from crr.api.scoring import filter_for_audience

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
    top_factors, fired_rules = filter_for_audience(
        payload.get("top_factors", []), payload.get("fired_rules", []), audience
    )
    # protective_factors are always risk-decreasing (never AML-suppressed by
    # construction, since every non-customer-visible reason code in this
    # vocabulary is a risk-raising compliance concern), but run the same filter
    # for consistency rather than assuming that stays true forever.
    protective_factors, _ = filter_for_audience(payload.get("protective_factors", []), [], audience)

    return ExplanationResponse(
        customer_id=stored.customer_id,
        risk_score=stored.risk_score,
        model_band=stored.model_band,
        risk_band=stored.risk_band,
        band_floor_applied=stored.band_floor_applied,
        credit_probability=stored.credit_probability,
        financial_crime_probability=stored.financial_crime_probability,
        audience=audience,  # type: ignore[arg-type]
        top_factors=[ReasonFactorOut(**f) for f in top_factors],
        protective_factors=[ReasonFactorOut(**f) for f in protective_factors],
        fired_rules=[rule_dict_to_out(r) for r in fired_rules],
        model_version=stored.model_version,
        policy_version=stored.policy_version,
        scored_at=stored.scored_at,
    )
