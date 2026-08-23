"""Tests for phase 8's model risk management layer (crr.governance):
population/calibration drift, group fairness, human-model disagreement and
bias reproduction, and the champion/challenger promotion gate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crr.governance.drift import (
    calibration_drift,
    population_stability_index,
    psi_report,
)
from crr.governance.fairness import (
    EXEMPT_DISPARITIES,
    MIN_FALSE_POSITIVE_EVENTS,
    MIN_GROUP_N,
    GroupFairnessResult,
    age_bucket,
    evaluate_group_fairness,
    jurisdiction_tier,
)
from crr.governance.feedback import (
    bias_reproduction_report,
    decision_label,
    human_model_disagreement,
)
from crr.governance.promotion import evaluate_promotion
from crr.models.baseline import train_booster

# --------------------------------------------------------------------------
# drift.py
# --------------------------------------------------------------------------


def test_psi_is_near_zero_for_identical_distributions():
    rng = np.random.default_rng(0)
    reference = pd.Series(rng.normal(0, 1, 5000))
    current = pd.Series(rng.normal(0, 1, 5000))
    assert population_stability_index(reference, current) < 0.02


def test_psi_is_large_for_a_shifted_distribution():
    rng = np.random.default_rng(0)
    reference = pd.Series(rng.normal(0, 1, 5000))
    shifted = pd.Series(rng.normal(2.0, 1.5, 5000))
    assert population_stability_index(reference, shifted) >= 0.25


def test_psi_categorical_detects_a_proportion_shift():
    rng = np.random.default_rng(0)
    reference = pd.Series(rng.choice(["a", "b", "c"], 5000, p=[0.7, 0.2, 0.1]))
    shifted = pd.Series(rng.choice(["a", "b", "c"], 5000, p=[0.1, 0.1, 0.8]))
    assert population_stability_index(reference, shifted) >= 0.25


def test_psi_handles_a_constant_reference_column_without_raising():
    reference = pd.Series([5.0] * 200)
    current = pd.Series([5.0] * 150 + [6.0] * 50)
    psi = population_stability_index(reference, current)
    assert np.isfinite(psi)


def test_psi_report_sorts_worst_first_and_labels_severity():
    rng = np.random.default_rng(1)
    reference = pd.DataFrame({
        "stable": rng.normal(0, 1, 3000),
        "shifted": rng.normal(0, 1, 3000),
    })
    current = pd.DataFrame({
        "stable": rng.normal(0, 1, 3000),
        "shifted": rng.normal(3, 1, 3000),
    })
    report = psi_report(reference, current, ["stable", "shifted"])
    assert report.iloc[0]["feature"] == "shifted"
    assert report.iloc[0]["severity"] == "major"


def test_calibration_drift_flags_growth_past_tolerance():
    result = calibration_drift(current_ece=0.03, baseline_ece=0.01)
    assert result["drifted"] is True
    assert result["delta_ece"] == pytest.approx(0.02)


def test_calibration_drift_does_not_flag_an_improvement():
    result = calibration_drift(current_ece=0.01, baseline_ece=0.02)
    assert result["drifted"] is False


# --------------------------------------------------------------------------
# fairness.py
# --------------------------------------------------------------------------


def test_age_bucket_boundaries():
    ages = pd.Series([18, 24, 25, 59, 60, 92])
    buckets = age_bucket(ages)
    assert list(buckets) == ["18-24", "18-24", "25-59", "25-59", "60+", "60+"]


def test_jurisdiction_tier_maps_known_codes_and_falls_back_for_unknown():
    codes = pd.Series(["IL", "SY", "CY", "ZZ"])
    tiers = jurisdiction_tier(codes)
    assert list(tiers) == ["low", "high", "medium", "unknown"]


def test_evaluate_group_fairness_flags_an_engineered_disparity():
    rng = np.random.default_rng(3)
    n = 2000
    group = pd.Series(np.where(rng.random(n) < 0.5, "A", "B"))
    y_true = rng.binomial(1, 0.2, n)
    # Group B is flagged High regardless of true label about half the time;
    # group A never is — an unambiguous, large disparity.
    band = pd.Series(np.where((group == "B") & (rng.random(n) < 0.5), "High", "Low"))
    result = evaluate_group_fairness("grp", "target", group, y_true, band)
    assert result.passes is False
    assert result.disparate_impact_pass is False


def test_evaluate_group_fairness_passes_when_groups_are_treated_alike():
    rng = np.random.default_rng(4)
    n = 2000
    group = pd.Series(np.where(rng.random(n) < 0.5, "A", "B"))
    y_true = rng.binomial(1, 0.2, n)
    band = pd.Series(np.where(y_true == 1, "High", "Low"))  # band depends only on the true label
    result = evaluate_group_fairness("grp", "target", group, y_true, band)
    assert result.passes is True


def test_small_groups_are_excluded_from_the_disparate_impact_gate():
    tiny_n = MIN_GROUP_N - 1
    group = pd.Series(["big"] * 200 + ["tiny"] * tiny_n)
    y_true = np.zeros(200 + tiny_n, dtype=int)
    band = pd.Series(["Low"] * 200 + ["High"] * tiny_n)  # the tiny group looks terrible in isolation
    result = evaluate_group_fairness("grp", "target", group, y_true, band)
    assert "tiny" not in result.evaluable["group"].to_numpy()
    assert result.disparate_impact_pass is True  # only the reliable ("big") group is judged


def test_a_zero_false_positive_group_with_too_few_events_does_not_sink_others():
    """The bug this guards against: a group that lands on 0% FPR purely
    because it drew zero false positives from a handful of negatives makes
    every other group's ratio collapse toward 0 if it is trusted as the
    reference. Below MIN_FALSE_POSITIVE_EVENTS raw false positives, a group
    must not be usable as the reference or judged itself."""
    group = pd.Series(["reliable"] * 1000 + ["tiny_luck"] * 40)
    y_true = np.array([0] * 900 + [1] * 100 + [0] * 40)  # tiny_luck group is all negatives
    # "reliable" group: 30 false positives out of 900 negatives (3.3% FPR, well-supported).
    reliable_favourable = np.array([True] * 870 + [False] * 30)
    tiny_favourable = np.array([True] * 40)  # 0 false positives out of 40 negatives — could easily be luck
    band = pd.Series(
        np.where(np.concatenate([reliable_favourable, [True] * 100, tiny_favourable]), "Low", "High")
    )
    result = evaluate_group_fairness("grp", "target", group, y_true, band)
    tiny_row = result.groups[result.groups["group"] == "tiny_luck"].iloc[0]
    assert tiny_row["fp_count"] < MIN_FALSE_POSITIVE_EVENTS
    assert "tiny_luck" not in result.evaluable_for_equal_opportunity["group"].to_numpy()
    # The reliable group's own ratio must not be computed against the lucky zero.
    reliable_row = result.groups[result.groups["group"] == "reliable"].iloc[0]
    assert reliable_row["fpr_parity_ratio"] == pytest.approx(1.0)


def test_exempt_disparities_documents_the_financial_crime_jurisdiction_pair():
    assert ("financial_crime_12m", "jurisdiction_tier") in EXEMPT_DISPARITIES
    assert ("default_12m", "jurisdiction_tier") not in EXEMPT_DISPARITIES


# --------------------------------------------------------------------------
# feedback.py
# --------------------------------------------------------------------------


def test_decision_label_maps_refer_and_decline_to_one():
    decisions = pd.Series(["approve", "approve_with_conditions", "refer", "decline"])
    assert list(decision_label(decisions)) == [0, 0, 1, 1]


def test_human_model_disagreement_perfect_agreement():
    decision = pd.Series(["approve", "refer", "decline"])
    band = pd.Series(["Low", "High", "Extreme"])
    result = human_model_disagreement(decision, band)
    assert result["agreement_rate"] == 1.0


def test_human_model_disagreement_measures_a_known_gap():
    decision = pd.Series(["approve"] * 3 + ["decline"] * 1)
    band = pd.Series(["Low"] * 2 + ["High"] * 1 + ["Extreme"] * 1)  # one approve disagrees
    result = human_model_disagreement(decision, band)
    assert result["agreement_rate"] == pytest.approx(0.75)


def test_bias_reproduction_report_detects_an_engineered_segment_effect():
    """A tiny synthetic dataset where the *decision* label carries an extra
    segment-based push that the *outcome* label does not — training on the
    decision should show a larger segment gap than training on the outcome,
    and the report should say so via a positive reproduced_bias."""
    rng = np.random.default_rng(7)
    n = 1200
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    segment = pd.Series(np.where(rng.random(n) < 0.3, "favoured", "other"))
    outcome_logit = 0.9 * x1 - 0.6 * x2
    y_outcome = (rng.random(n) < 1 / (1 + np.exp(-outcome_logit))).astype(int)
    # Decision is the outcome plus a large, segment-only leniency push with no
    # basis in x1/x2 — exactly the shape of the roadmap's underwriter bias.
    decision_logit = outcome_logit - 3.0 * (segment == "favoured").to_numpy()
    y_decision = (rng.random(n) < 1 / (1 + np.exp(-decision_logit))).astype(int)

    # segment has to be an input feature for either booster to be *able* to
    # learn a segment-specific effect — exactly like the real pipeline, where
    # the customer's segment is itself a categorical model feature.
    X = pd.DataFrame({"x1": x1, "x2": x2, "is_favoured": (segment == "favoured").astype(int)})
    split = rng.choice(["train", "validation", "test"], n, p=[0.6, 0.2, 0.2])
    masks = {name: split == name for name in ("train", "validation", "test")}

    outcome_booster = train_booster(
        X[masks["train"]], y_outcome[masks["train"]], X[masks["validation"]], y_outcome[masks["validation"]], [],
    )
    outcome_scores_test = np.asarray(outcome_booster.predict(X[masks["test"]]), dtype=float)

    report = bias_reproduction_report(
        X, masks, [], y_decision, outcome_scores_test, segment[masks["test"]].reset_index(drop=True), seed=1,
    )
    favoured_row = report[report["segment"] == "favoured"].iloc[0]
    # The decision-trained model should show a much more negative gap for the
    # favoured segment than the outcome-trained model — the reproduced bias.
    assert favoured_row["reproduced_bias"] < -0.05


# --------------------------------------------------------------------------
# promotion.py
# --------------------------------------------------------------------------

_POLICY = {"promotion_min_auc_gain": 0.005, "require_human_approval": True}


def test_promotion_requires_a_measured_gain_above_the_policy_threshold():
    below = evaluate_promotion("t", {"auc": 0.752}, {"auc": 0.750}, [], _POLICY)
    above = evaluate_promotion("t", {"auc": 0.760}, {"auc": 0.750}, [], _POLICY)
    assert below.eligible is False
    assert above.eligible is True


def test_promotion_with_no_champion_on_record_skips_the_gain_check():
    decision = evaluate_promotion("t", {"auc": 0.70}, None, [], _POLICY, approved_by_human=True)
    assert decision.champion_auc is None
    assert decision.auc_gain is None
    assert decision.eligible is True
    assert decision.promoted is True


def test_human_approval_actually_gates_promotion_even_when_eligible():
    unapproved = evaluate_promotion("t", {"auc": 0.760}, {"auc": 0.750}, [], _POLICY, approved_by_human=False)
    approved = evaluate_promotion("t", {"auc": 0.760}, {"auc": 0.750}, [], _POLICY, approved_by_human=True)
    assert unapproved.eligible is True
    assert unapproved.promoted is False
    assert approved.promoted is True


def test_policy_without_human_approval_requirement_promotes_on_eligibility_alone():
    lenient_policy = {"promotion_min_auc_gain": 0.005, "require_human_approval": False}
    decision = evaluate_promotion("t", {"auc": 0.760}, {"auc": 0.750}, [], lenient_policy, approved_by_human=False)
    assert decision.promoted is True


def _fake_result(ratio: float, exempt: bool) -> GroupFairnessResult:
    groups = pd.DataFrame([
        {"group": "a", "n": 500, "favourable_rate": 0.95, "prevalence": 0.1, "fp_count": 20, "fpr": 0.04,
         "disparate_impact_ratio": 1.0, "fpr_parity_ratio": 1.0},
        {"group": "b", "n": 500, "favourable_rate": 0.95 * ratio, "prevalence": 0.1, "fp_count": 20, "fpr": 0.04 / ratio,
         "disparate_impact_ratio": round(ratio, 4), "fpr_parity_ratio": round(ratio, 4)},
    ])
    return GroupFairnessResult(
        attribute="axis", target="t", groups=groups, reference_group="a",
        exempt_reason="documented" if exempt else None,
    )


def test_a_non_exempt_fairness_failure_blocks_an_otherwise_eligible_gain():
    decision = evaluate_promotion("t", {"auc": 0.80}, {"auc": 0.75}, [_fake_result(0.5, exempt=False)], _POLICY)
    assert decision.gain_ok is True
    assert decision.eligible is False
    assert "axis" in decision.fairness_failures


def test_an_exempt_fairness_failure_does_not_block_eligibility_but_forces_signoff():
    lenient_policy = {"promotion_min_auc_gain": 0.005, "require_human_approval": False}
    decision = evaluate_promotion("t", {"auc": 0.80}, {"auc": 0.75}, [_fake_result(0.5, exempt=True)], lenient_policy)
    assert decision.eligible is True
    assert decision.requires_human_approval is True  # forced on despite the lenient policy
    assert decision.promoted is False  # no sign-off was given
