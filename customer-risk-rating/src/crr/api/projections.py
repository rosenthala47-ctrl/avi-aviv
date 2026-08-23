"""Convert the internal :class:`~crr.api.scoring.Assessment` to API and storage shapes.

Kept in one place so the response schema, the stored explanation, and the audit
record cannot drift into three subtly different representations of the same score.
"""

from __future__ import annotations

from crr.api.repository import StoredScore
from crr.api.schemas import DimensionResult, ReasonFactorOut, ScoreResult
from crr.api.scoring import Assessment, MergedFactor


def _factor_out(factor: MergedFactor) -> ReasonFactorOut:
    return ReasonFactorOut(
        code=factor.code,
        category=factor.category,
        statement=factor.statement,
        dimension=factor.dimension,  # type: ignore[arg-type]
        contribution=round(factor.contribution, 6),
        direction=factor.direction,  # type: ignore[arg-type]
    )


def assessment_to_result(assessment: Assessment) -> ScoreResult:
    return ScoreResult(
        customer_id=assessment.customer_id,
        risk_score=round(assessment.risk_score, 2),
        risk_band=assessment.risk_band,  # type: ignore[arg-type]
        credit=DimensionResult(probability=assessment.credit_probability),
        financial_crime=DimensionResult(probability=assessment.financial_crime_probability),
        top_factors=[_factor_out(f) for f in assessment.top_factors],
        requires_review=assessment.requires_review,
        model_version=assessment.model_version,
        policy_version=assessment.policy_version,
        scored_at=assessment.scored_at,
        latency_ms=round(assessment.latency_ms, 3),
    )


def _factor_dict(factor: MergedFactor) -> dict:
    return {
        "code": factor.code,
        "category": factor.category,
        "statement": factor.statement,
        "dimension": factor.dimension,
        "contribution": round(factor.contribution, 6),
        "direction": factor.direction,
    }


def assessment_to_stored(assessment: Assessment) -> StoredScore:
    return StoredScore(
        customer_id=assessment.customer_id,
        scored_at=assessment.scored_at,
        risk_score=assessment.risk_score,
        risk_band=assessment.risk_band,
        credit_probability=assessment.credit_probability,
        financial_crime_probability=assessment.financial_crime_probability,
        requires_review=assessment.requires_review,
        model_version=assessment.model_version,
        policy_version=assessment.policy_version,
        input_hash=assessment.input_hash,
        audience=assessment.audience,
        explanation={
            "top_factors": [_factor_dict(f) for f in assessment.top_factors],
            "protective_factors": [_factor_dict(f) for f in assessment.protective_factors],
        },
    )
