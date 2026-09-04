"""Champion/challenger promotion: automatic gates decide *eligibility*, a
human decides *promotion*. The two are kept separate on purpose — an
automatic gate that also pulls the trigger is exactly the failure mode a
model-risk committee exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from crr.governance.fairness import GroupFairnessResult


@dataclass
class PromotionDecision:
    target: str
    challenger_auc: float
    champion_auc: float | None
    auc_gain: float | None
    required_gain: float
    gain_ok: bool
    fairness_failures: list[str] = field(default_factory=list)
    fairness_exceptions: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    requires_human_approval: bool = True
    approved_by_human: bool = False

    @property
    def eligible(self) -> bool:
        """Would be promoted automatically if human approval were not required."""
        return self.gain_ok and not self.fairness_failures

    @property
    def promoted(self) -> bool:
        if not self.eligible:
            return False
        return self.approved_by_human or not self.requires_human_approval

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "challenger_auc": self.challenger_auc,
            "champion_auc": self.champion_auc,
            "auc_gain": self.auc_gain,
            "required_gain": self.required_gain,
            "gain_ok": self.gain_ok,
            "eligible": self.eligible,
            "fairness_failures": self.fairness_failures,
            "fairness_exceptions": self.fairness_exceptions,
            "requires_human_approval": self.requires_human_approval,
            "approved_by_human": self.approved_by_human,
            "promoted": self.promoted,
            "reasons": self.reasons,
        }


def evaluate_promotion(
    target: str,
    challenger_metrics: dict[str, Any],
    champion_metrics: dict[str, Any] | None,
    fairness_results: list[GroupFairnessResult],
    policy_feedback: dict[str, Any],
    *,
    approved_by_human: bool = False,
) -> PromotionDecision:
    """Decide whether a freshly trained challenger is *eligible* for
    promotion, and whether it is actually *promoted* given the human
    approval this call was told about.

    ``challenger_metrics``/``champion_metrics`` are ``crr.models.metrics.
    summarise()`` dicts for the out-of-time test split. ``fairness_results``
    is ``crr.governance.fairness.fairness_report()``'s output for the
    challenger. ``policy_feedback`` is ``RiskPolicy.feedback`` straight from
    ``config/risk_policy.yaml`` — ``promotion_min_auc_gain`` and
    ``require_human_approval`` are read from there, not hardcoded, so a
    policy change takes effect without a code change.
    """
    required_gain = float(policy_feedback.get("promotion_min_auc_gain", 0.0))
    require_human_approval = bool(policy_feedback.get("require_human_approval", True))
    challenger_auc = float(challenger_metrics["auc"])

    reasons = []
    if champion_metrics is None:
        champion_auc = None
        auc_gain = None
        gain_ok = True
        reasons.append("no champion on record for this target — first model, gain check does not apply")
    else:
        champion_auc = float(champion_metrics["auc"])
        auc_gain = challenger_auc - champion_auc
        gain_ok = auc_gain >= required_gain
        reasons.append(
            f"out-of-time AUC gain {auc_gain:+.4f} {'meets' if gain_ok else 'below'} "
            f"the required {required_gain:.4f} (policy.feedback.promotion_min_auc_gain)"
        )

    fairness_failures, fairness_exceptions = [], []
    for result in fairness_results:
        if result.passes:
            continue
        label = f"{result.attribute}"
        if result.exempt_reason:
            fairness_exceptions.append(label)
        else:
            fairness_failures.append(label)
    if fairness_failures:
        reasons.append(f"fairness gate failed (no exemption on record): {', '.join(fairness_failures)}")
    if fairness_exceptions:
        reasons.append(f"documented fairness exception present, needs named sign-off: {', '.join(fairness_exceptions)}")

    return PromotionDecision(
        target=target,
        challenger_auc=round(challenger_auc, 4),
        champion_auc=round(champion_auc, 4) if champion_auc is not None else None,
        auc_gain=round(auc_gain, 4) if auc_gain is not None else None,
        required_gain=required_gain,
        gain_ok=gain_ok,
        fairness_failures=fairness_failures,
        fairness_exceptions=fairness_exceptions,
        reasons=reasons,
        requires_human_approval=require_human_approval or bool(fairness_exceptions),
        approved_by_human=approved_by_human,
    )
