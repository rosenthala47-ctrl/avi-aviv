"""Explainability layer: TreeSHAP contributions mapped to policy-owned reason codes."""

from crr.explain.explainer import Explainer, Explanation, ReasonFactor
from crr.explain.reason_codes import (
    REASON_CODES,
    ReasonCode,
    code_for_feature,
    unmapped_features,
    validate_against_policy,
)
from crr.explain.shap_explainer import ShapExplainer, ShapResult

__all__ = [
    "REASON_CODES",
    "Explainer",
    "Explanation",
    "ReasonCode",
    "ReasonFactor",
    "ShapExplainer",
    "ShapResult",
    "code_for_feature",
    "unmapped_features",
    "validate_against_policy",
]
