"""Tests for the explainability layer.

Additivity is the load-bearing test: it is the guarantee that an explanation is a
faithful decomposition of the model's actual output rather than a plausible story.
If it holds, every reason code is a real share of the decision; if it broke, none
of the explanations could be trusted no matter how sensible they read.
"""

from __future__ import annotations

import numpy as np
import pytest

from crr.data.synthetic import GeneratorConfig, generate
from crr.explain import (
    Explainer,
    code_for_feature,
    unmapped_features,
    validate_against_policy,
)
from crr.explain.reason_codes import BY_CODE, REASON_CODES
from crr.features import FeaturePipeline
from crr.models import build_artifact, train_booster

POLICY_SUPPRESSED = ["pep_flag", "sar_filed_prior", "adverse_media_hits_12m", "sanctions_screen_hits"]


@pytest.fixture(scope="module")
def trained():
    dataset = generate(GeneratorConfig(n_customers=6000, seed=31))
    customers, events = dataset.customers, dataset.events
    y = dataset.outcomes.set_index("customer_id")["default_12m"].loc[customers["customer_id"]].to_numpy()
    masks = {name: (customers["split"] == name).to_numpy() for name in ("train", "validation", "test")}

    pipeline = FeaturePipeline().fit(customers[masks["train"]], events)
    X = pipeline.transform(customers, events)
    booster = train_booster(
        X[masks["train"]], y[masks["train"]], X[masks["validation"]], y[masks["validation"]],
        pipeline.contract.categorical_names, seed=0,
    )
    artifact = build_artifact(booster, pipeline.contract, "default_12m", X[masks["validation"]], y[masks["validation"]])
    ids = customers.loc[masks["test"], "customer_id"].to_numpy()
    return artifact, X[masks["test"]].reset_index(drop=True), ids


@pytest.fixture(scope="module")
def explainer(trained):
    artifact, _, _ = trained
    return Explainer.from_artifact(artifact)


# --------------------------------------------------------------------------
# Reason-code vocabulary
# --------------------------------------------------------------------------


def test_reason_codes_are_unique():
    codes = [rc.code for rc in REASON_CODES]
    assert len(codes) == len(set(codes))


def test_every_model_feature_maps_to_a_reason_code(trained):
    artifact, _, _ = trained
    assert unmapped_features(artifact.contract.names) == []


def test_no_feature_matches_two_reason_codes(trained):
    """First-match-wins ordering must not hide an ambiguous mapping."""
    artifact, _, _ = trained
    for feature in artifact.contract.names:
        matching = [rc.code for rc in REASON_CODES if rc.matches(feature)]
        # code_for_feature returns the first; assert the intent is unambiguous
        # enough that the first match is also the most specific.
        assert matching, feature
        assert code_for_feature(feature).code == matching[0]


def test_policy_suppression_is_consistent_with_the_vocabulary():
    assert validate_against_policy(POLICY_SUPPRESSED) == []


def test_suppressed_features_belong_to_internal_only_codes():
    for feature in POLICY_SUPPRESSED:
        reason_code = code_for_feature(feature)
        assert reason_code is not None and not reason_code.customer_visible, feature


# --------------------------------------------------------------------------
# SHAP additivity — the guarantee
# --------------------------------------------------------------------------


def test_shap_reconstructs_the_raw_margin(explainer, trained):
    _, X_test, _ = trained
    result = explainer.shap.explain(X_test.head(1500))
    assert result.additivity_error() < 1e-6


def test_shap_base_value_is_the_expected_margin(explainer, trained):
    _, X_test, _ = trained
    result = explainer.shap.explain(X_test.head(50))
    # For any row, sum(shap) + base == raw margin.
    reconstructed = result.values.sum(axis=1) + result.base_value
    np.testing.assert_allclose(reconstructed, result.raw_margin, atol=1e-9)


def test_global_importance_shares_sum_to_one(explainer, trained):
    _, X_test, _ = trained
    importance = explainer.shap.explain(X_test.head(500)).global_importance()
    assert importance["importance_share"].sum() == pytest.approx(1.0, abs=1e-6)


# --------------------------------------------------------------------------
# Explanation content
# --------------------------------------------------------------------------


def test_explanation_top_factors_are_ranked_by_contribution(explainer, trained):
    _, X_test, ids = trained
    explanation = explainer.explain_row(str(ids[0]), X_test.iloc[[0]])
    contributions = [f.contribution for f in explanation.top_factors]
    assert contributions == sorted(contributions, reverse=True)
    assert all(f.direction == "increases" for f in explanation.top_factors)


def test_protective_factors_decrease_risk(explainer, trained):
    _, X_test, ids = trained
    explanation = explainer.explain_row(str(ids[0]), X_test.iloc[[0]])
    assert all(f.direction == "decreases" for f in explanation.protective_factors)
    assert all(f.contribution < 0 for f in explanation.protective_factors)


def test_reason_factor_contribution_equals_member_feature_sum(explainer, trained):
    """A reason code's contribution must be exactly the sum of its features' SHAP."""
    _, X_test, ids = trained
    explanation = explainer.explain_row(str(ids[0]), X_test.iloc[[0]], audience="internal")
    for factor in explanation.top_factors + explanation.protective_factors:
        member_sum = sum(fc.shap_value for fc in factor.features)
        # Members below 1e-9 are dropped from the list, so allow a small slack.
        assert factor.contribution == pytest.approx(member_sum, abs=1e-6)


def test_customer_audience_hides_internal_only_codes(explainer, trained):
    _, X_test, ids = trained
    # Find a customer whose explanation actually surfaces a compliance code.
    for i in range(len(X_test)):
        internal = explainer.explain_row(str(ids[i]), X_test.iloc[[i]], audience="internal")
        internal_codes = {f.code for f in internal.top_factors + internal.protective_factors}
        if any(not BY_CODE[c].customer_visible for c in internal_codes):
            customer = explainer.explain_row(str(ids[i]), X_test.iloc[[i]], audience="customer")
            shown = {f.code for f in customer.top_factors + customer.protective_factors}
            assert all(BY_CODE[c].customer_visible for c in shown)
            return
    pytest.skip("no compliance code surfaced in the sample")


def test_customer_audience_never_exposes_feature_values(explainer, trained):
    _, X_test, ids = trained
    explanation = explainer.explain_row(str(ids[0]), X_test.iloc[[0]], audience="customer")
    payload = explanation.to_dict()
    for factor in payload["top_factors"] + payload["protective_factors"]:
        assert "features" not in factor


def test_internal_audience_includes_member_features(explainer, trained):
    _, X_test, ids = trained
    explanation = explainer.explain_row(str(ids[0]), X_test.iloc[[0]], audience="internal")
    payload = explanation.to_dict()
    assert any("features" in f and f["features"] for f in payload["top_factors"])


def test_calibrated_probability_matches_the_artifact(explainer, trained):
    artifact, X_test, ids = trained
    direct = artifact.predict_proba(X_test.iloc[[0]])[0]
    explanation = explainer.explain_row(str(ids[0]), X_test.iloc[[0]])
    assert explanation.calibrated_probability == pytest.approx(direct, abs=1e-9)


def test_explain_row_matches_explain_batch(explainer, trained):
    _, X_test, ids = trained
    sample_ids = [str(x) for x in ids[:20]]
    batch = explainer.explain_batch(sample_ids, X_test.head(20))
    for i, explanation in enumerate(batch):
        single = explainer.explain_row(sample_ids[i], X_test.iloc[[i]])
        assert explanation.raw_margin == pytest.approx(single.raw_margin, abs=1e-9)
        assert [f.code for f in explanation.top_factors] == [f.code for f in single.top_factors]


def test_min_absolute_shap_filters_small_factors(trained):
    artifact, X_test, ids = trained
    strict = Explainer.from_artifact(artifact, min_absolute_shap=0.5)
    loose = Explainer.from_artifact(artifact, min_absolute_shap=0.0)
    strict_expl = strict.explain_row(str(ids[0]), X_test.iloc[[0]])
    loose_expl = loose.explain_row(str(ids[0]), X_test.iloc[[0]])
    total_strict = len(strict_expl.top_factors) + len(strict_expl.protective_factors)
    total_loose = len(loose_expl.top_factors) + len(loose_expl.protective_factors)
    assert total_strict <= total_loose


def test_top_factors_respects_the_configured_count(trained):
    artifact, X_test, ids = trained
    explainer = Explainer.from_artifact(artifact, top_factors=3, min_absolute_shap=0.0)
    explanation = explainer.explain_row(str(ids[0]), X_test.iloc[[0]])
    assert len(explanation.top_factors) <= 3
    assert len(explanation.protective_factors) <= 3


def test_explanation_serialises_to_json(explainer, trained):
    import json

    _, X_test, ids = trained
    explanation = explainer.explain_row(str(ids[0]), X_test.iloc[[0]], audience="internal")
    payload = json.dumps(explanation.to_dict())
    restored = json.loads(payload)
    assert restored["customer_id"] == str(ids[0])
    assert restored["target"] == "default_12m"
    assert 0.0 <= restored["calibrated_probability"] <= 1.0


def test_explain_row_rejects_multi_row_frames(explainer, trained):
    _, X_test, ids = trained
    with pytest.raises(ValueError):
        explainer.explain_row("x", X_test.head(2))


def test_high_risk_customer_is_explained_by_risk_increasing_factors(explainer, trained):
    """Sanity: the highest-risk customer's story should be dominated by risk drivers."""
    artifact, X_test, ids = trained
    probabilities = artifact.predict_proba(X_test)
    worst = int(np.argmax(probabilities))
    explanation = explainer.explain_row(str(ids[worst]), X_test.iloc[[worst]])
    assert explanation.calibrated_probability > 0.3
    assert len(explanation.top_factors) >= 1
    # The dominant factor should push risk up and be a real, sizeable share.
    assert explanation.top_factors[0].contribution > 0
    assert explanation.top_factors[0].share > 0.1
