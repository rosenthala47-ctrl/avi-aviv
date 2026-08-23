"""The customer-facing explanation: SHAP values, aggregated into ranked reason codes.

This is what ``GET /api/v1/explain/{customer_id}`` returns and what an
adverse-action notice is built from. It takes the exact per-feature SHAP
contributions from :mod:`crr.explain.shap_explainer`, groups them into the
policy-owned reason codes from :mod:`crr.explain.reason_codes`, ranks them, and
applies audience-based suppression.

Two audiences, one engine:

* ``internal`` — the underwriter and the auditor. Every reason code, plus the
  member features and their individual SHAP values behind each one.
* ``customer`` — the applicant. Non-customer-visible codes (PEP, prior SAR,
  sanctions, structuring) are removed, and raw feature values are never exposed,
  only the plain-language statement and direction.

The ``min_absolute_shap`` floor and ``top_factors`` count come from
``config/risk_policy.yaml`` so the risk owner controls how many reasons appear
and how small a contribution is worth mentioning, without a code change.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

from crr.explain.reason_codes import BY_CODE, ReasonCode, code_for_feature
from crr.explain.shap_explainer import ShapExplainer, ShapResult

Audience = Literal["internal", "customer"]


def _sigmoid(x: np.ndarray) -> np.ndarray:
    """Logistic function; converts a raw margin to the booster's probability scale."""
    return 1.0 / (1.0 + np.exp(-np.clip(x, -35.0, 35.0)))

#: Defaults, overridden by the ``explainability`` block of the risk policy.
DEFAULT_TOP_FACTORS = 5
DEFAULT_MIN_ABSOLUTE_SHAP = 0.01


@dataclass
class FeatureContribution:
    """One feature's contribution to one reason code (internal audience only)."""

    feature: str
    shap_value: float
    value: Any

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # numpy scalars are not JSON-serialisable; coerce to Python types.
        payload["shap_value"] = float(self.shap_value)
        payload["value"] = _py(self.value)
        return payload


@dataclass
class ReasonFactor:
    """One reason code's aggregated contribution to a single customer's score."""

    code: str
    category: str
    statement: str
    contribution: float  # summed SHAP, log-odds scale; sign = direction
    share: float  # fraction of total absolute contribution across all codes
    direction: Literal["increases", "decreases"]
    features: list[FeatureContribution] = field(default_factory=list)

    def to_dict(self, include_features: bool) -> dict[str, Any]:
        payload = {
            "code": self.code,
            "category": self.category,
            "statement": self.statement,
            "contribution": round(float(self.contribution), 6),
            "share": round(float(self.share), 4),
            "direction": self.direction,
        }
        if include_features:
            payload["features"] = [fc.to_dict() for fc in self.features]
        return payload


@dataclass
class Explanation:
    """The full explanation for one customer and one model."""

    customer_id: str
    target: str
    audience: Audience
    raw_margin: float
    calibrated_probability: float
    base_probability: float
    top_factors: list[ReasonFactor]
    protective_factors: list[ReasonFactor]
    additivity_error: float
    suppressed_factor_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        include_features = self.audience == "internal"
        return {
            "customer_id": self.customer_id,
            "target": self.target,
            "audience": self.audience,
            "raw_margin": round(float(self.raw_margin), 6),
            "calibrated_probability": round(float(self.calibrated_probability), 6),
            "base_probability": round(float(self.base_probability), 6),
            "top_factors": [rf.to_dict(include_features) for rf in self.top_factors],
            "protective_factors": [rf.to_dict(include_features) for rf in self.protective_factors],
            "additivity_error": float(self.additivity_error),
            "suppressed_factor_count": self.suppressed_factor_count,
        }


def _py(value: Any) -> Any:
    """Coerce numpy / pandas scalars to plain Python for JSON."""
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, np.bool_):
        return bool(value)
    return value


class Explainer:
    """Produces :class:`Explanation` objects from a fitted model and calibrator."""

    def __init__(
        self,
        booster,
        calibrator,
        feature_names: list[str],
        target: str,
        *,
        top_factors: int = DEFAULT_TOP_FACTORS,
        min_absolute_shap: float = DEFAULT_MIN_ABSOLUTE_SHAP,
    ) -> None:
        self.shap = ShapExplainer(booster, feature_names)
        self.calibrator = calibrator
        self.feature_names = list(feature_names)
        self.target = target
        self.top_factors = top_factors
        self.min_absolute_shap = min_absolute_shap

        # Precompute reason-code -> member feature indices for this model.
        self._code_to_indices: dict[str, list[int]] = {}
        for index, feature in enumerate(self.feature_names):
            reason_code = code_for_feature(feature)
            if reason_code is not None:
                self._code_to_indices.setdefault(reason_code.code, []).append(index)

    # ---- global --------------------------------------------------------------

    def global_importance(self, X: pd.DataFrame) -> pd.DataFrame:
        """Mean-absolute-SHAP importance, aggregated to reason codes.

        The model-documentation view: which reason codes drive the model overall,
        not just for one customer.
        """
        result = self.shap.explain(X)
        rows = []
        mean_abs = np.abs(result.values).mean(axis=0)
        for code, indices in self._code_to_indices.items():
            reason_code = BY_CODE[code]
            rows.append(
                {
                    "code": code,
                    "category": reason_code.category,
                    "statement": reason_code.statement,
                    "mean_abs_shap": float(mean_abs[indices].sum()),
                    "customer_visible": reason_code.customer_visible,
                }
            )
        frame = pd.DataFrame(rows).sort_values("mean_abs_shap", ascending=False, ignore_index=True)
        total = frame["mean_abs_shap"].sum()
        frame["importance_share"] = frame["mean_abs_shap"] / total if total > 0 else 0.0
        return frame

    # ---- per customer --------------------------------------------------------

    def explain_row(
        self, customer_id: str, X_row: pd.DataFrame, *, audience: Audience = "internal"
    ) -> Explanation:
        """Explain a single customer. ``X_row`` is a one-row feature frame."""
        if len(X_row) != 1:
            raise ValueError("explain_row expects exactly one row")
        result = self.shap.explain(X_row)
        return self._build(customer_id, X_row, result, 0, audience)

    def explain_batch(
        self, customer_ids: list[str], X: pd.DataFrame, *, audience: Audience = "internal"
    ) -> list[Explanation]:
        """Explain many customers in one SHAP pass (cheaper than looping)."""
        if len(customer_ids) != len(X):
            raise ValueError("customer_ids and X must be the same length")
        result = self.shap.explain(X)
        return [
            self._build(customer_ids[i], X.iloc[[i]], result, i, audience)
            for i in range(len(X))
        ]

    def _build(
        self, customer_id: str, X_row: pd.DataFrame, result: ShapResult, row: int, audience: Audience
    ) -> Explanation:
        shap_row = result.values[row]
        raw_margin = float(result.raw_margin[row])
        # SHAP additivity is on the margin; the calibrator was fit on the
        # booster's probability output. sigmoid(margin) converts between them.
        calibrated = float(self.calibrator.transform(_sigmoid(np.array([raw_margin])))[0])
        base_probability = float(self.calibrator.transform(_sigmoid(np.array([result.base_value])))[0])

        factors: list[ReasonFactor] = []
        suppressed = 0
        total_abs = float(np.abs(shap_row).sum()) or 1.0

        for code, indices in self._code_to_indices.items():
            reason_code = BY_CODE[code]
            if audience == "customer" and not reason_code.customer_visible:
                # Count it if it materially contributed, so the customer view can
                # honestly say "N compliance factors were also considered".
                if abs(float(shap_row[indices].sum())) >= self.min_absolute_shap:
                    suppressed += 1
                continue

            contribution = float(shap_row[indices].sum())
            if abs(contribution) < self.min_absolute_shap:
                continue

            members = self._member_features(reason_code, indices, shap_row, X_row) if audience == "internal" else []
            factors.append(
                ReasonFactor(
                    code=code,
                    category=reason_code.category,
                    statement=reason_code.statement,
                    contribution=contribution,
                    share=abs(contribution) / total_abs,
                    direction="increases" if contribution > 0 else "decreases",
                    features=members,
                )
            )

        increasing = sorted(
            (f for f in factors if f.direction == "increases"), key=lambda f: f.contribution, reverse=True
        )
        decreasing = sorted(
            (f for f in factors if f.direction == "decreases"), key=lambda f: f.contribution
        )
        return Explanation(
            customer_id=customer_id,
            target=self.target,
            audience=audience,
            raw_margin=raw_margin,
            calibrated_probability=calibrated,
            base_probability=base_probability,
            top_factors=increasing[: self.top_factors],
            protective_factors=decreasing[: self.top_factors],
            additivity_error=result.additivity_error(),
            suppressed_factor_count=suppressed,
        )

    def _member_features(
        self, reason_code: ReasonCode, indices: list[int], shap_row: np.ndarray, X_row: pd.DataFrame
    ) -> list[FeatureContribution]:
        """The individual features behind a reason code, most influential first."""
        members = [
            FeatureContribution(
                feature=self.feature_names[i],
                shap_value=float(shap_row[i]),
                value=X_row.iloc[0, X_row.columns.get_loc(self.feature_names[i])],
            )
            for i in indices
            if abs(float(shap_row[i])) > 1e-9
        ]
        return sorted(members, key=lambda fc: abs(fc.shap_value), reverse=True)

    @classmethod
    def from_artifact(cls, artifact, *, top_factors: int = DEFAULT_TOP_FACTORS,
                      min_absolute_shap: float = DEFAULT_MIN_ABSOLUTE_SHAP) -> Explainer:
        """Build an explainer from a saved :class:`~crr.models.ModelArtifact`."""
        return cls(
            artifact.booster,
            artifact.calibrator,
            artifact.contract.names,
            artifact.target,
            top_factors=top_factors,
            min_absolute_shap=min_absolute_shap,
        )
