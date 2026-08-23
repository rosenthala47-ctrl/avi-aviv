"""Group fairness testing across age, jurisdiction and residency.

The roadmap names the real tension directly: ``country_of_residence`` is both
a legitimate AML risk factor and a proxy for national origin. This module
does not resolve that by picking a side — it measures the same way on every
protected axis and lets the *causal structure already built into the
generator* decide which measured disparities are expected:

``crr.data.synthetic.BETA_CREDIT`` (the ``default_12m`` outcome model) has no
jurisdiction, residency or PEP term anywhere in it — a defaulter's home
country plays no causal role in whether they repay. Any measured disparity by
``jurisdiction_tier`` or ``residency_status`` on *that* target is therefore a
proxy effect with no legitimate basis, and the fairness gate treats it as an
ordinary failure.

``crr.data.synthetic.BETA_CRIME`` (the ``financial_crime_12m`` outcome model)
by contrast *directly* includes ``high_risk_jurisdiction``, ``medium_risk_
jurisdiction`` and ``cross_border`` terms — jurisdiction risk is a real,
intentional, documented driver of that target, exactly mirroring how FATF-
style AML programmes work in practice. Disparity by ``jurisdiction_tier`` on
*that* target is expected, not a bug, and is listed in ``EXEMPT_DISPARITIES``
below with the reasoning inline rather than silently passed or silently
failed. An exemption changes how ``crr.governance.promotion`` gates it (named
human sign-off instead of an automatic block); it never hides the number.

``age`` sits in between: ``x_age_distance`` is a real, documented, *legitimate*
credit-risk shape in ``BETA_CREDIT`` (very young and very old borrowers behave
differently) — but age is also a protected characteristic under fair-lending
law in most jurisdictions, and "the coefficient is causal in our synthetic
generator" is not, by itself, a defence a real regulator would accept. It gets
no exemption here: measured age disparity is reported like any other finding,
on purpose, so it cannot be missed by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from crr.data import taxonomy as tx

#: The four-fifths (80%) rule — US EEOC adverse-impact guidance, also the most
#: widely cited fair-lending disparate-impact threshold. Chosen over an
#: invented number for the same reason PSI's 0.10/0.25 bands were cited rather
#: than picked: a reviewer can check it against a real external standard.
FAIRNESS_TOLERANCE = 0.8

#: Below this many customers, a group's disparate-impact ratio is sampling
#: noise, not a finding — report it, but do not fail the gate on it. 30 is
#: the conventional floor below which a proportion's standard error swamps a
#: 20-point gap from the tolerance line.
MIN_GROUP_N = 30

#: A false-positive *rate* is a different kind of unstable when the
#: underlying event is rare (financial_crime_12m's true prevalence is
#: ~1.5%, so most groups see well under 1% FPR): a group can land on
#: 0.00% FPR purely because it drew zero false positives by chance out of a
#: handful expected, not because it is genuinely well-served, and every
#: other group's ratio against that accidental zero collapses toward 0
#: regardless of how good they actually are. Below this many *raw false
#: positives*, a group is excluded from the equal-opportunity comparison
#: entirely (both as a candidate reference and as a group being judged) —
#: the standard "avoid ratios over near-zero counts" convention (the same
#: reasoning behind the rule-of-5 floor for a chi-square cell).
MIN_FALSE_POSITIVE_EVENTS = 5

#: Bands that represent an adverse outcome for the customer (enhanced review,
#: worse terms) versus ordinary treatment — mirrors risk_policy.yaml's own
#: `review.require_for_bands: [High, Extreme]` split.
_ADVERSE_BANDS = ("High", "Extreme")

#: (target, protected attribute) pairs where a measured disparity has a
#: documented, causally legitimate basis in the generator's own outcome model
#: (see the module docstring) and is therefore routed to human sign-off by
#: crr.governance.promotion instead of blocking automatically. Every other
#: combination is an ordinary pass/fail.
EXEMPT_DISPARITIES: dict[tuple[str, str], str] = {
    ("financial_crime_12m", "jurisdiction_tier"): (
        "high_risk_jurisdiction, medium_risk_jurisdiction and cross_border are direct terms "
        "in BETA_CRIME (crr.data.synthetic) — jurisdiction is a real, intended AML risk driver "
        "for this target, not a proxy. Disparity here is the risk methodology working as "
        "designed; treat as a documented exception requiring named sign-off, not a block."
    ),
}


def age_bucket(age: pd.Series) -> pd.Series:
    """Three life-stage bands wide enough to hold a usable sample per group."""
    return pd.cut(
        age.astype(float), bins=[0, 24, 59, 200], labels=["18-24", "25-59", "60+"], right=True
    ).astype("object")


def jurisdiction_tier(country_of_residence: pd.Series) -> pd.Series:
    """AML risk tier of the customer's country — the same grouping BETA_CRIME
    itself uses (`crr.data.taxonomy.JURISDICTION_TIER`), not raw country
    codes: 33 countries would fragment the sample below any usable group
    size, and the tier is the causally meaningful grouping either way."""
    return country_of_residence.map(tx.JURISDICTION_TIER).fillna("unknown").astype("object")


@dataclass
class GroupFairnessResult:
    """Fairness measurement for one (protected attribute, target) pair."""

    attribute: str
    target: str
    groups: pd.DataFrame
    reference_group: str
    tolerance: float = FAIRNESS_TOLERANCE
    exempt_reason: str | None = None

    @property
    def evaluable(self) -> pd.DataFrame:
        """Groups with enough customers that a disparate-impact ratio means something."""
        return self.groups[self.groups["n"] >= MIN_GROUP_N]

    @property
    def evaluable_for_equal_opportunity(self) -> pd.DataFrame:
        """Groups with enough *raw false positives* that an FPR ratio means
        something — a stricter floor than ``evaluable``, see
        MIN_FALSE_POSITIVE_EVENTS."""
        return self.groups[self.groups["fp_count"] >= MIN_FALSE_POSITIVE_EVENTS]

    @property
    def disparate_impact_pass(self) -> bool:
        evaluable = self.evaluable
        return bool((evaluable["disparate_impact_ratio"] >= self.tolerance).all()) if len(evaluable) else True

    @property
    def equal_opportunity_pass(self) -> bool:
        evaluable = self.evaluable_for_equal_opportunity
        return bool((evaluable["fpr_parity_ratio"] >= self.tolerance).all()) if len(evaluable) else True

    @property
    def passes(self) -> bool:
        return self.disparate_impact_pass and self.equal_opportunity_pass

    def to_dict(self) -> dict[str, Any]:
        return {
            "attribute": self.attribute,
            "target": self.target,
            "reference_group": self.reference_group,
            "tolerance": self.tolerance,
            "exempt_reason": self.exempt_reason,
            "disparate_impact_pass": self.disparate_impact_pass,
            "equal_opportunity_pass": self.equal_opportunity_pass,
            "passes": self.passes,
            "groups": self.groups.to_dict(orient="records"),
        }


def evaluate_group_fairness(
    attribute: str,
    target: str,
    protected: pd.Series,
    y_true: np.ndarray,
    band: pd.Series,
    *,
    tolerance: float = FAIRNESS_TOLERANCE,
) -> GroupFairnessResult:
    """Disparate impact (favourable-band rate) and equal-opportunity (false-
    positive rate among genuinely good customers) across the groups of
    ``protected``, for one target's predictions.

    ``band`` is the policy band actually assigned (Low/Medium/High/Extreme);
    favourable = not flagged High/Extreme, matching risk_policy.yaml's own
    adverse-action split. Reference group for both ratios is the *best*
    group observed (highest favourable rate / lowest false-positive rate) —
    the standard EEOC convention — so every ratio reads as "how this group
    compares to the best-treated group," not to an arbitrary baseline.
    """
    frame = pd.DataFrame({
        "group": protected.astype("object").fillna("unknown").to_numpy(),
        "y_true": np.asarray(y_true, dtype=int),
        "favourable": (~band.isin(_ADVERSE_BANDS)).to_numpy(),
    })

    rows = []
    for group, part in frame.groupby("group", observed=True):
        negatives = part[part["y_true"] == 0]
        fp_count = int((~negatives["favourable"]).sum()) if len(negatives) else 0
        fpr = float(fp_count / len(negatives)) if len(negatives) else float("nan")
        rows.append({
            "group": group,
            "n": int(len(part)),
            "favourable_rate": round(float(part["favourable"].mean()), 4),
            "prevalence": round(float(part["y_true"].mean()), 4),
            "fp_count": fp_count,
            "fpr": round(fpr, 4) if np.isfinite(fpr) else fpr,
        })
    groups = pd.DataFrame(rows).sort_values("n", ascending=False, ignore_index=True)

    best_favourable = groups["favourable_rate"].max()
    reliable_fpr = groups.loc[groups["fp_count"] >= MIN_FALSE_POSITIVE_EVENTS, "fpr"]
    best_fpr = reliable_fpr.min() if len(reliable_fpr) else float("nan")
    groups["disparate_impact_ratio"] = (groups["favourable_rate"] / best_favourable).round(4) if best_favourable > 0 else 1.0
    groups["fpr_parity_ratio"] = groups["fpr"].apply(
        lambda fpr: round(best_fpr / fpr, 4) if np.isfinite(best_fpr) and fpr and np.isfinite(fpr) and fpr > 0 else float("nan")
    )

    reference_group = str(groups.loc[groups["favourable_rate"].idxmax(), "group"])
    exempt_reason = EXEMPT_DISPARITIES.get((target, attribute))
    return GroupFairnessResult(
        attribute=attribute, target=target, groups=groups,
        reference_group=reference_group, tolerance=tolerance, exempt_reason=exempt_reason,
    )


def fairness_report(
    customers: pd.DataFrame, y_true: np.ndarray, band: pd.Series, target: str,
) -> list[GroupFairnessResult]:
    """Run every protected axis for one target's out-of-time predictions."""
    protected_axes = {
        "age_bucket": age_bucket(customers["age"]),
        "jurisdiction_tier": jurisdiction_tier(customers["country_of_residence"]),
        "residency_status": customers["residency_status"],
    }
    return [
        evaluate_group_fairness(attribute, target, values, y_true, band)
        for attribute, values in protected_axes.items()
    ]
