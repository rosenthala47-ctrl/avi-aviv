"""Tests for the synthetic data generator.

These guard the properties the rest of the project depends on: reproducibility,
frame alignment, no label leakage, and the presence of real signal. If any of
these break, every downstream metric becomes meaningless — so they are asserted
here rather than discovered later in a model that quietly underperforms.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from crr.data import taxonomy as tx
from crr.data.synthetic import (
    BETA_CREDIT,
    BETA_CRIME,
    GROUND_TRUTH_COLUMNS,
    GeneratorConfig,
    _calibrate_intercept,
    _sigmoid,
    generate,
    score_to_band,
    write_dataset,
)


@pytest.fixture(scope="module")
def dataset():
    return generate(GeneratorConfig(n_customers=3000, seed=20240101, language="mixed"))


# --------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------


def test_same_seed_produces_identical_data():
    a = generate(GeneratorConfig(n_customers=400, seed=7))
    b = generate(GeneratorConfig(n_customers=400, seed=7))
    pd.testing.assert_frame_equal(a.customers, b.customers)
    pd.testing.assert_frame_equal(a.outcomes, b.outcomes)
    pd.testing.assert_frame_equal(a.narratives, b.narratives)


def test_different_seeds_produce_different_data():
    a = generate(GeneratorConfig(n_customers=400, seed=7))
    b = generate(GeneratorConfig(n_customers=400, seed=8))
    assert not a.customers["bureau_score"].equals(b.customers["bureau_score"])


# --------------------------------------------------------------------------
# Structural integrity
# --------------------------------------------------------------------------


def test_all_frames_align_on_customer_id(dataset):
    ids = set(dataset.customers["customer_id"])
    assert len(ids) == len(dataset.customers), "customer_id must be unique"
    for name in ("narratives", "outcomes", "ground_truth"):
        assert set(getattr(dataset, name)["customer_id"]) == ids, f"{name} does not align"
    assert set(dataset.events["customer_id"]) <= ids


def test_ground_truth_columns_are_absent_from_model_inputs(dataset):
    """The single most expensive mistake available here is leaking a latent."""
    for column in GROUND_TRUTH_COLUMNS:
        assert column not in dataset.customers.columns
        assert column not in dataset.narratives.columns
        assert column not in dataset.events.columns


def test_outcome_labels_are_not_in_the_feature_frame(dataset):
    for column in ("default_12m", "financial_crime_12m", "days_past_due_at_outcome"):
        assert column not in dataset.customers.columns


def test_splits_are_out_of_time(dataset):
    """Test snapshots must be strictly later than training snapshots."""
    frame = dataset.outcomes
    latest_train = frame.loc[frame["split"] == "train", "snapshot_date"].max()
    earliest_test = frame.loc[frame["split"] == "test", "snapshot_date"].min()
    assert earliest_test > latest_train


def test_no_delinquency_predates_the_relationship(dataset):
    customers = dataset.customers
    assert (customers["months_since_last_delinquency"] <= customers["account_age_months"]).all()


def test_account_age_fits_inside_adult_life(dataset):
    customers = dataset.customers
    assert (customers["account_age_months"] <= (customers["age"] - 17) * 12).all()


def test_events_precede_their_snapshot(dataset):
    joined = dataset.events.merge(dataset.customers[["customer_id", "snapshot_date"]], on="customer_id")
    assert (joined["event_ts"].to_numpy().astype("datetime64[D]") <= joined["snapshot_date"].to_numpy()).all()


# --------------------------------------------------------------------------
# Distributions and calibration
# --------------------------------------------------------------------------


@pytest.mark.parametrize("target", [0.02, 0.055, 0.12])
def test_intercept_calibration_hits_the_target_rate(target):
    rng = np.random.default_rng(0)
    linear = rng.normal(0, 1.4, 20_000)
    intercept = _calibrate_intercept(linear, target)
    assert _sigmoid(intercept + linear).mean() == pytest.approx(target, abs=1e-4)


def test_realised_prevalence_is_close_to_the_configured_target():
    cfg = GeneratorConfig(n_customers=20_000, seed=3, target_default_rate=0.07, target_financial_crime_rate=0.02)
    dataset = generate(cfg)
    assert dataset.outcomes["default_12m"].mean() == pytest.approx(0.07, abs=0.01)
    assert dataset.outcomes["financial_crime_12m"].mean() == pytest.approx(0.02, abs=0.006)


def test_risk_score_ranks_the_actual_outcomes(dataset):
    """The whole dataset is worthless if the composite score does not rank risk."""
    merged = dataset.outcomes.merge(dataset.ground_truth, on="customer_id")
    ranked = merged.sort_values("true_risk_score")
    bottom = ranked.head(len(ranked) // 5)["default_12m"].mean()
    top = ranked.tail(len(ranked) // 5)["default_12m"].mean()
    assert top > bottom * 5, f"weak rank-ordering: bottom {bottom:.3%} vs top {top:.3%}"


def test_narrative_level_separates_the_outcome(dataset):
    """The text must carry signal, or the hybrid architecture has no purpose."""
    merged = dataset.outcomes.merge(dataset.ground_truth, on="customer_id")
    by_level = merged.groupby("narrative_distress_level")["default_12m"].mean()
    assert by_level.index.min() == 0 and by_level.index.max() == 3
    assert by_level.loc[3] > by_level.loc[0] * 2


def test_score_to_band_is_monotone():
    scores = np.array([0.0, 24.9, 25.0, 49.9, 50.0, 74.9, 75.0, 100.0])
    bands = score_to_band(scores)
    assert list(bands) == ["Low", "Low", "Medium", "Medium", "High", "High", "Extreme", "Extreme"]
    assert set(bands) <= set(tx.RISK_BANDS)


def test_every_coefficient_has_a_matching_term(dataset):
    """A typo in a beta key would silently drop that driver from the outcome."""
    from crr.data.synthetic import _credit_terms, _crime_terms

    rng = np.random.default_rng(0)
    cfg = GeneratorConfig(n_customers=200, seed=1)
    from crr.data.synthetic import (
        _draw_behavioural,
        _draw_compliance,
        _draw_financial,
        _draw_latents,
        _draw_profile,
        _draw_text_latents,
    )

    n = 200
    z = _draw_latents(rng, n)
    prof = _draw_profile(rng, cfg, n, z)
    fin = _draw_financial(rng, n, z, prof)
    beh = _draw_behavioural(rng, n, z, prof, fin)
    comp = _draw_compliance(rng, n, z, prof, beh)
    txt = _draw_text_latents(rng, n, z)

    assert set(BETA_CREDIT) <= set(_credit_terms(fin, prof, beh, txt))
    assert set(BETA_CRIME) <= set(_crime_terms(comp, beh, prof, txt))


# --------------------------------------------------------------------------
# Realism switches
# --------------------------------------------------------------------------


def test_clean_mode_produces_no_missing_values_or_duplicates():
    cfg = GeneratorConfig(
        n_customers=500,
        seed=11,
        inject_missingness=False,
        inject_categorical_noise=False,
        duplicate_rate=0.0,
    )
    dataset = generate(cfg)
    structural = {"loan_to_value", "collateral_coverage_ratio"}
    leaked = [c for c in dataset.customers.columns if c not in structural and dataset.customers[c].isna().any()]
    assert leaked == []
    assert dataset.ground_truth["duplicate_of_customer_id"].isna().all()


def test_missingness_is_injected_when_requested(dataset):
    assert dataset.customers["education_level"].isna().any()
    assert dataset.customers["verified_income_ratio"].isna().any()


def test_missingness_is_higher_for_the_self_employed(dataset):
    """The MAR mechanism has to actually depend on the observed data."""
    customers = dataset.customers
    self_employed = customers["employment_status"].astype(str).str.lower().str.contains("self|business")
    assert customers.loc[self_employed, "verified_income_ratio"].isna().mean() > (
        customers.loc[~self_employed, "verified_income_ratio"].isna().mean()
    )


def test_pii_can_be_switched_off():
    dataset = generate(GeneratorConfig(n_customers=200, seed=4, include_pii=False))
    for column in ("full_name", "national_id", "email", "phone", "address_line"):
        assert column not in dataset.customers.columns


def test_synthetic_pii_is_obviously_fake():
    dataset = generate(GeneratorConfig(n_customers=200, seed=4))
    assert dataset.customers["national_id"].str.startswith("SYN-").all()
    assert dataset.customers["email"].str.endswith("@example.invalid").all()
    assert dataset.customers["phone"].str.startswith("+1-555-01").all()


def test_mixed_language_produces_both_languages():
    dataset = generate(GeneratorConfig(n_customers=600, seed=5, language="mixed", hebrew_share=0.4))
    assert set(dataset.narratives["language"]) == {"en", "he"}


# --------------------------------------------------------------------------
# Configuration validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"n_customers": 0},
        {"target_default_rate": 0.0},
        {"target_default_rate": 0.9},
        {"target_financial_crime_rate": 1.5},
        {"language": "fr"},
        {"cohort_months": 0},
    ],
)
def test_invalid_config_is_rejected(kwargs):
    with pytest.raises(ValueError):
        GeneratorConfig(**kwargs).validate()


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def test_write_dataset_emits_files_and_a_manifest(tmp_path, dataset):
    manifest = write_dataset(dataset, tmp_path, formats=("csv",))
    for name in ("customers", "narratives", "events", "outcomes", "ground_truth"):
        assert (tmp_path / f"{name}.csv").exists()
        assert manifest["files"][name]["csv"]["sha256"]
    assert (tmp_path / "manifest.json").exists()
    assert manifest["config"]["seed"] == dataset.config.seed
    assert manifest["summary"]["risk_score_deciles"][-1]["default_rate"] > (
        manifest["summary"]["risk_score_deciles"][0]["default_rate"]
    )


def test_manifest_config_round_trips(dataset):
    config = dataset.manifest["config"]
    assert config["as_of"] == dt.date(2026, 1, 1).isoformat()
    assert config["n_customers"] == 3000
