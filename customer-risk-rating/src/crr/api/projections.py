"""Convert the internal :class:`~crr.api.scoring.Assessment` to API and storage shapes.

Kept in one place so the response schema, the stored explanation, and the audit
record cannot drift into three subtly different representations of the same score.

The explanation is always PERSISTED in full (every SHAP factor, every fired rule,
visible or not) and filtered down to an audience only at the point something is
returned to a caller — the immediate ``POST /score`` response here, or a later
``GET /explain`` read (see ``crr.api.routers.explain``, which applies the same
:func:`~crr.api.scoring.filter_for_audience`). Storing a pre-filtered view would
mean a score first requested for customer display could never afterwards be
reviewed internally in full — exactly the kind of inconsistency a regulator would
flag.
"""

from __future__ import annotations

from typing import Any

from crr.api.repository import StoredScore
from crr.api.schemas import DimensionResult, FiredRuleOut, ReasonFactorOut, ScoreResult
from crr.api.scoring import Assessment, FiredRule, MergedFactor, filter_for_audience


def _factor_dict(factor: MergedFactor) -> dict[str, Any]:
    return {
        "code": factor.code,
        "category": factor.category,
        "statement": factor.statement,
        "dimension": factor.dimension,
        "contribution": round(factor.contribution, 6),
        "direction": factor.direction,
    }


def _rule_dict(rule: FiredRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "description": rule.description,
        "reason_code": rule.reason_code,
        "floor_band": rule.floor_band,
        "require_review": rule.require_review,
        "customer_visible": rule.customer_visible,
    }


def rule_dict_to_out(rule: dict[str, Any]) -> FiredRuleOut:
    return FiredRuleOut(
        id=rule["id"],
        description=rule["description"],
        reason_code=rule["reason_code"],
        floor_band=rule["floor_band"],
        require_review=rule["require_review"],
    )


def assessment_to_result(assessment: Assessment) -> ScoreResult:
    factors, rules = filter_for_audience(
        [_factor_dict(f) for f in assessment.top_factors],
        [_rule_dict(r) for r in assessment.fired_rules],
        assessment.audience,
    )
    return ScoreResult(
        customer_id=assessment.customer_id,
        risk_score=round(assessment.risk_score, 2),
        model_band=assessment.model_band,  # type: ignore[arg-type]
        risk_band=assessment.risk_band,  # type: ignore[arg-type]
        band_floor_applied=assessment.band_floor_applied,
        credit=DimensionResult(probability=assessment.credit_probability),
        financial_crime=DimensionResult(probability=assessment.financial_crime_probability),
        top_factors=[ReasonFactorOut(**f) for f in factors],
        fired_rules=[rule_dict_to_out(r) for r in rules],
        requires_review=assessment.requires_review,
        model_version=assessment.model_version,
        policy_version=assessment.policy_version,
        scored_at=assessment.scored_at,
        latency_ms=round(assessment.latency_ms, 3),
    )


def assessment_to_stored(assessment: Assessment) -> StoredScore:
    return StoredScore(
        customer_id=assessment.customer_id,
        scored_at=assessment.scored_at,
        risk_score=assessment.risk_score,
        model_band=assessment.model_band,
        risk_band=assessment.risk_band,
        band_floor_applied=assessment.band_floor_applied,
        credit_probability=assessment.credit_probability,
        financial_crime_probability=assessment.financial_crime_probability,
        requires_review=assessment.requires_review,
        model_version=assessment.model_version,
        policy_version=assessment.policy_version,
        input_hash=assessment.input_hash,
        audience=assessment.audience,
        customer_snapshot=dict(assessment.customer_snapshot),
        explanation={
            "top_factors": [_factor_dict(f) for f in assessment.top_factors],
            "protective_factors": [_factor_dict(f) for f in assessment.protective_factors],
            "fired_rules": [_rule_dict(r) for r in assessment.fired_rules],
        },
    )
