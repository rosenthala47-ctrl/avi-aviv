"""Model risk management: drift, fairness and champion/challenger promotion."""

from crr.governance.drift import (
    PSI_MAJOR_THRESHOLD,
    PSI_MODERATE_THRESHOLD,
    calibration_drift,
    population_stability_index,
    psi_report,
)
from crr.governance.fairness import (
    EXEMPT_DISPARITIES,
    FAIRNESS_TOLERANCE,
    GroupFairnessResult,
    age_bucket,
    evaluate_group_fairness,
    fairness_report,
    jurisdiction_tier,
)
from crr.governance.feedback import (
    DECISION_TO_BAND,
    bias_reproduction_report,
    decision_label,
    human_model_disagreement,
)
from crr.governance.promotion import PromotionDecision, evaluate_promotion

__all__ = [
    "DECISION_TO_BAND",
    "EXEMPT_DISPARITIES",
    "FAIRNESS_TOLERANCE",
    "PSI_MAJOR_THRESHOLD",
    "PSI_MODERATE_THRESHOLD",
    "GroupFairnessResult",
    "PromotionDecision",
    "age_bucket",
    "bias_reproduction_report",
    "calibration_drift",
    "decision_label",
    "evaluate_group_fairness",
    "evaluate_promotion",
    "fairness_report",
    "human_model_disagreement",
    "jurisdiction_tier",
    "population_stability_index",
    "psi_report",
]
