"""Tests for the model layer: calibration, metrics and the saved artefact."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crr.data.synthetic import GeneratorConfig, generate
from crr.features import ContractViolation, FeaturePipeline
from crr.models import ModelArtifact, build_artifact, summarise, train_booster
from crr.models.calibration import IsotonicCalibrator, PlattCalibrator, load_calibrator
from crr.models.metrics import (
    auc_standard_error,
    calibration_table,
    decile_table,
    expected_calibration_error,
    gini,
    ks_statistic,
)


@pytest.fixture(scope="module")
def trained():
    dataset = generate(GeneratorConfig(n_customers=9000, seed=17))
    customers, events = dataset.customers, dataset.events
    y = dataset.outcomes.set_index("customer_id")["default_12m"].loc[customers["customer_id"]].to_numpy()
    masks = {name: (customers["split"] == name).to_numpy() for name in ("train", "validation", "test")}

    pipeline = FeaturePipeline().fit(customers[masks["train"]], events)
    X = pipeline.transform(customers, events)
    booster = train_booster(
        X[masks["train"]], y[masks["train"]],
        X[masks["validation"]], y[masks["validation"]],
        pipeline.contract.categorical_names, seed=0,
    )
    artifact = build_artifact(booster, pipeline.contract, "default_12m", X[masks["validation"]], y[masks["validation"]])
    return artifact, X, y, masks


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------


def test_isotonic_calibration_fixes_the_level_and_keeps_the_ranking():
    rng = np.random.default_rng(0)
    n = 6000
    z = rng.normal(size=n)
    truth = 1 / (1 + np.exp(-(-2.8 + 1.3 * z)))
    y = (rng.random(n) < truth).astype(int)
    badly_scaled = 1 / (1 + np.exp(-(1.3 * z)))  # right order, wrong level

    calibrator = IsotonicCalibrator().fit(badly_scaled[:3000], y[:3000])
    calibrated = calibrator.transform(badly_scaled[3000:])

    before = expected_calibration_error(y[3000:], badly_scaled[3000:])
    after = expected_calibration_error(y[3000:], calibrated)
    assert after < before / 5
    # Isotonic is monotone NON-decreasing, so it merges scores into flat steps.
    # Exact rank order is therefore not preserved and must not be asserted; AUC
    # is, because it handles ties correctly, and AUC is what we actually care
    # about keeping.
    from sklearn.metrics import roc_auc_score

    assert roc_auc_score(y[3000:], calibrated) >= roc_auc_score(y[3000:], badly_scaled[3000:]) - 0.01


def test_platt_preserves_auc_exactly():
    """Strictly monotone, unlike isotonic — this is why it is the default."""
    from sklearn.metrics import roc_auc_score

    rng = np.random.default_rng(0)
    n = 6000
    z = rng.normal(size=n)
    y = (rng.random(n) < 1 / (1 + np.exp(-(-2.8 + 1.3 * z)))).astype(int)
    badly_scaled = 1 / (1 + np.exp(-(1.3 * z)))

    calibrated = PlattCalibrator().fit(badly_scaled[:3000], y[:3000]).transform(badly_scaled[3000:])
    assert roc_auc_score(y[3000:], calibrated) == pytest.approx(
        roc_auc_score(y[3000:], badly_scaled[3000:]), abs=1e-9
    )
    assert expected_calibration_error(y[3000:], calibrated) < 0.02


@pytest.mark.parametrize("factory", [PlattCalibrator, IsotonicCalibrator])
def test_load_calibrator_detects_the_saved_method(factory, tmp_path):
    rng = np.random.default_rng(9)
    scores = rng.uniform(0.01, 0.99, 500)
    y = (rng.random(500) < scores).astype(int)
    original = factory().fit(scores, y)
    original.save(tmp_path / "cal.json")
    restored = load_calibrator(tmp_path / "cal.json")
    assert isinstance(restored, factory)
    np.testing.assert_allclose(restored.transform(scores), original.transform(scores))


def test_calibrator_round_trips_through_json(tmp_path):
    rng = np.random.default_rng(1)
    scores = rng.random(500)
    y = (rng.random(500) < scores).astype(int)
    calibrator = IsotonicCalibrator().fit(scores, y)
    calibrator.save(tmp_path / "cal.json")
    restored = IsotonicCalibrator.load(tmp_path / "cal.json")
    np.testing.assert_allclose(calibrator.transform(scores), restored.transform(scores))


def test_calibrator_output_stays_a_probability():
    rng = np.random.default_rng(2)
    scores = rng.random(400)
    calibrator = IsotonicCalibrator().fit(scores, (rng.random(400) < 0.3).astype(int))
    out = calibrator.transform(np.array([-5.0, 0.5, 5.0]))
    assert np.all((out >= 0) & (out <= 1))


def test_unfitted_calibrator_refuses_to_transform_or_save(tmp_path):
    with pytest.raises(RuntimeError):
        IsotonicCalibrator().transform(np.array([0.5]))
    with pytest.raises(RuntimeError):
        IsotonicCalibrator().save(tmp_path / "x.json")


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def test_gini_and_auc_agree():
    rng = np.random.default_rng(3)
    y = (rng.random(1000) < 0.3).astype(int)
    scores = y + rng.normal(0, 0.6, 1000)
    assert gini(y, scores) == pytest.approx(2 * summarise(y, scores)["auc"] - 1)


def test_ks_is_bounded_and_positive_for_a_real_signal():
    rng = np.random.default_rng(4)
    y = (rng.random(2000) < 0.3).astype(int)
    scores = y + rng.normal(0, 0.5, 2000)
    assert 0.0 < ks_statistic(y, scores) <= 1.0


def test_perfect_calibration_scores_near_zero_error():
    rng = np.random.default_rng(5)
    p = rng.uniform(0.02, 0.6, 40000)
    y = (rng.random(40000) < p).astype(int)
    assert expected_calibration_error(y, p) < 0.01


def test_calibration_table_flags_bins_outside_two_standard_errors():
    rng = np.random.default_rng(6)
    p = rng.uniform(0.05, 0.5, 5000)
    y = (rng.random(5000) < p).astype(int)
    table = calibration_table(y, p)
    assert len(table) == 10
    assert table["n"].sum() == 5000
    assert table["within_2se"].sum() >= 8


def test_decile_table_lift_is_monotone_for_a_good_model():
    rng = np.random.default_rng(7)
    z = rng.normal(size=20000)
    p = 1 / (1 + np.exp(-(-3 + 1.6 * z)))
    y = (rng.random(20000) < p).astype(int)
    table = decile_table(y, p)
    assert len(table) == 10
    assert table["rate"].iloc[-1] > table["rate"].iloc[0] * 5
    assert table["lift"].iloc[-1] > 1.0


def test_auc_standard_error_shrinks_with_sample_size():
    rng = np.random.default_rng(8)
    errors = []
    for n in (1000, 20000):
        y = (rng.random(n) < 0.05).astype(int)
        scores = y + rng.normal(0, 1.0, n)
        errors.append(auc_standard_error(y, scores))
    assert errors[0] > errors[1]


def test_auc_standard_error_is_nan_without_both_classes():
    assert np.isnan(auc_standard_error(np.zeros(10), np.arange(10.0)))


# --------------------------------------------------------------------------
# Artefact
# --------------------------------------------------------------------------


def test_model_learns_something(trained):
    artifact, X, y, masks = trained
    metrics = summarise(y[masks["test"]], artifact.predict_proba(X[masks["test"]]))
    assert metrics["auc"] > 0.68, f"test AUC collapsed to {metrics['auc']:.4f}"


def test_predictions_are_calibrated_probabilities(trained):
    artifact, X, y, masks = trained
    probabilities = artifact.predict_proba(X[masks["test"]])
    assert np.all((probabilities >= 0) & (probabilities <= 1))
    # Mean prediction should land near the observed rate, not far from it.
    assert abs(probabilities.mean() - y[masks["test"]].mean()) < 0.02


def test_artifact_round_trips_and_reproduces_predictions(trained, tmp_path):
    artifact, X, _, masks = trained
    artifact.metrics = {"test": {"auc": 0.77}}
    artifact.save(tmp_path)
    restored = ModelArtifact.load(tmp_path)

    sample = X[masks["test"]].head(200)
    np.testing.assert_allclose(restored.predict_proba(sample), artifact.predict_proba(sample))
    assert restored.target == artifact.target
    assert restored.contract.names == artifact.contract.names
    assert restored.metadata["best_iteration"] == artifact.metadata["best_iteration"]


def test_artifact_records_its_provenance(trained):
    artifact, _, _, _ = trained
    for key in ("target", "trained_at_utc", "lightgbm_version", "pipeline_version", "best_iteration", "params"):
        assert key in artifact.metadata


def test_prediction_rejects_a_frame_that_breaks_the_contract(trained):
    artifact, X, _, masks = trained
    broken = X[masks["test"]].head(10).drop(columns=[artifact.contract.names[2]])
    with pytest.raises(ContractViolation):
        artifact.predict_proba(broken)


def test_prediction_is_order_invariant(trained):
    """Scoring a customer must not depend on who else is in the batch."""
    artifact, X, _, masks = trained
    sample = X[masks["test"]].head(100)
    shuffled = sample.sample(frac=1.0, random_state=3)
    both = pd.DataFrame(
        {"a": artifact.predict_proba(sample), "idx": sample.index}
    ).set_index("idx")
    reordered = pd.DataFrame(
        {"b": artifact.predict_proba(shuffled), "idx": shuffled.index}
    ).set_index("idx")
    joined = both.join(reordered)
    np.testing.assert_allclose(joined["a"].to_numpy(), joined["b"].to_numpy())


def test_feature_importance_covers_the_contract(trained):
    artifact, _, _, _ = trained
    importance = artifact.feature_importance()
    assert set(importance["feature"]) == set(artifact.contract.names)
    assert importance["gain_share"].sum() == pytest.approx(1.0, abs=1e-6)
