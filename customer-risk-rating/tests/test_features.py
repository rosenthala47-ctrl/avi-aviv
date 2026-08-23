"""Tests for the feature layer.

The point-in-time and parity tests here are the ones that matter. Everything else
in the project can be re-run; a leaked feature produces a model that looks
excellent, passes review, and fails in production, and no metric catches it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crr.data.synthetic import GeneratorConfig, generate
from crr.features import ContractViolation, FeatureContract, FeaturePipeline, LeakageError, assert_no_leakage
from crr.features.contract import FeatureSpec
from crr.features.events import build_event_features
from crr.features.pipeline import CATEGORICAL_COLUMNS, DERIVED_COLUMNS, DROP_COLUMNS, INDICATOR_COLUMNS
from crr.features.transforms import (
    MISSING_CATEGORY,
    UNKNOWN_CATEGORY,
    CategoricalEncoder,
    normalise_category,
    safe_ratio,
)


@pytest.fixture(scope="module")
def dataset():
    return generate(GeneratorConfig(n_customers=2500, seed=99, language="mixed"))


@pytest.fixture(scope="module")
def fitted(dataset):
    train = dataset.customers[dataset.customers["split"] == "train"]
    pipeline = FeaturePipeline().fit(train, dataset.events)
    return pipeline, pipeline.transform(dataset.customers, dataset.events)


# --------------------------------------------------------------------------
# Leakage
# --------------------------------------------------------------------------


def test_forbidden_columns_are_rejected_by_name():
    with pytest.raises(LeakageError, match="p_default_true"):
        assert_no_leakage(pd.DataFrame({"age": [30], "p_default_true": [0.1]}))


def test_forbidden_columns_are_rejected_by_pattern():
    """A renamed leak must still be caught."""
    with pytest.raises(LeakageError):
        assert_no_leakage(pd.DataFrame({"age": [30], "customer_latent_score": [0.1]}))


def test_outcome_and_ground_truth_never_reach_the_feature_matrix(fitted, dataset):
    _, X = fitted
    forbidden = set(dataset.outcomes.columns) | set(dataset.ground_truth.columns)
    forbidden.discard("customer_id")
    assert not (set(X.columns) & forbidden)


def test_pii_is_dropped(fitted):
    _, X = fitted
    for column in ("full_name", "national_id", "email", "phone", "address_line"):
        assert column not in X.columns


def test_identifiers_are_dropped(fitted):
    _, X = fitted
    for column in DROP_COLUMNS:
        assert column not in X.columns


# --------------------------------------------------------------------------
# Point-in-time correctness
# --------------------------------------------------------------------------


def test_future_events_do_not_change_features(dataset):
    """The single most important test in the project."""
    index = dataset.customers[["customer_id", "snapshot_date", "country_of_residence"]].rename(
        columns={"snapshot_date": "as_of", "country_of_residence": "home_country"}
    )
    before = build_event_features(dataset.events, index)

    future = dataset.events.head(300).copy()
    future["event_ts"] = pd.to_datetime(future["event_ts"]) + pd.Timedelta(days=365)
    future["amount"] = -9_999_999.0
    future["event_type"] = "missed_payment"
    future["is_trigger_event"] = 1
    future["event_id"] = [f"EVT-FUTURE-{i:06d}" for i in range(len(future))]

    after = build_event_features(pd.concat([dataset.events, future], ignore_index=True), index)
    pd.testing.assert_frame_equal(before, after)


def test_events_on_the_snapshot_date_are_included(dataset):
    """A snapshot is taken at the end of its day, so same-day events count."""
    customer = dataset.customers.iloc[0]
    index = pd.DataFrame(
        {
            "customer_id": [customer["customer_id"]],
            "as_of": [pd.to_datetime(customer["snapshot_date"])],
            "home_country": [customer["country_of_residence"]],
        }
    )
    same_day = pd.DataFrame(
        {
            "event_id": ["EVT-SAMEDAY"],
            "customer_id": [customer["customer_id"]],
            "event_ts": [pd.to_datetime(customer["snapshot_date"]) + pd.Timedelta(hours=13)],
            "event_type": ["cash_deposit"],
            "amount": [5000.0],
            "counterparty_country": [customer["country_of_residence"]],
            "channel": ["branch"],
            "is_trigger_event": [1],
        }
    )
    features = build_event_features(same_day, index)
    assert features["event_count_30d"].iloc[0] == 1
    assert features["days_since_last_event"].iloc[0] == 0


def test_window_boundaries_are_exclusive_at_the_far_end(dataset):
    customer = dataset.customers.iloc[0]
    as_of = pd.to_datetime(customer["snapshot_date"])
    index = pd.DataFrame(
        {"customer_id": [customer["customer_id"]], "as_of": [as_of], "home_country": ["IL"]}
    )
    events = pd.DataFrame(
        {
            "event_id": ["a", "b"],
            "customer_id": [customer["customer_id"]] * 2,
            # 29 days old is inside a 30-day window; exactly 30 is outside.
            "event_ts": [as_of - pd.Timedelta(days=29), as_of - pd.Timedelta(days=30)],
            "event_type": ["card_purchase"] * 2,
            "amount": [-100.0, -100.0],
            "counterparty_country": ["IL", "IL"],
            "channel": ["card", "card"],
            "is_trigger_event": [0, 0],
        }
    )
    features = build_event_features(events, index)
    assert features["event_count_30d"].iloc[0] == 1
    assert features["event_count_90d"].iloc[0] == 2


def test_customers_without_events_still_get_a_row(fitted, dataset):
    _, X = fitted
    assert len(X) == len(dataset.customers)
    assert (X["has_event_history"] == 0).any(), "near-duplicate records should have no event history"


def test_counts_fill_with_zero_but_ratios_stay_null(fitted):
    """A count of zero is a fact; a ratio over zero events is undefined."""
    _, X = fitted
    no_history = X["has_event_history"] == 0
    assert (X.loc[no_history, "event_count_90d"] == 0).all()
    assert X.loc[no_history, "foreign_event_ratio_90d"].isna().all()
    assert X.loc[no_history, "days_since_last_event"].isna().all()


# --------------------------------------------------------------------------
# Training/serving parity
# --------------------------------------------------------------------------


def test_single_row_transform_matches_the_batch(fitted, dataset):
    """Serving one customer must give exactly what batch scoring gave."""
    pipeline, batch = fitted
    sample = dataset.customers.sample(15, random_state=0)

    rows = [
        pipeline.transform(row.to_frame().T, dataset.events[dataset.events["customer_id"] == row["customer_id"]])
        for _, row in sample.iterrows()
    ]
    single = pd.concat(rows, ignore_index=True)
    expected = batch.loc[sample.index].reset_index(drop=True)

    for column in expected.columns:
        if str(expected[column].dtype) == "category":
            assert list(expected[column].astype(str)) == list(single[column].astype(str)), column
        else:
            np.testing.assert_allclose(
                expected[column].to_numpy(dtype=float),
                single[column].to_numpy(dtype=float),
                equal_nan=True,
                err_msg=column,
            )


def test_transform_is_deterministic(fitted, dataset):
    pipeline, first = fitted
    second = pipeline.transform(dataset.customers, dataset.events)
    pd.testing.assert_frame_equal(first, second)


# --------------------------------------------------------------------------
# Categorical handling
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Self-Employed", "self_employed"),
        ("self employed", "self_employed"),
        ("SELF_EMPLOYED", "self_employed"),
        ("  salaried ", "salaried"),
        ("pensioner", "retired"),
        ("BA", "bachelor"),
        ("bachelors", "bachelor"),
        ("PhD", "doctorate"),
        ("High School", "high_school"),
        ("highschool", "high_school"),
    ],
)
def test_dirty_categoricals_normalise_to_one_value(raw, expected):
    assert normalise_category(pd.Series([raw])).iloc[0] == expected


def test_unseen_and_missing_categories_get_sentinels():
    encoder = CategoricalEncoder().fit(pd.DataFrame({"c": ["a", "b"]}), ["c"])
    out = encoder.transform(pd.DataFrame({"c": ["a", "zzz", None]}))
    assert list(out["c"].astype(str)) == ["a", UNKNOWN_CATEGORY, MISSING_CATEGORY]


def test_encoder_rejects_transform_before_fit():
    with pytest.raises(RuntimeError):
        CategoricalEncoder().transform(pd.DataFrame({"c": ["a"]}))


def test_categorical_cardinality_matches_the_clean_taxonomy(fitted):
    """Normalisation must actually collapse the injected spelling variants."""
    pipeline, _ = fitted
    employment = set(pipeline.encoder.vocabularies["employment_status"])
    employment -= {MISSING_CATEGORY, UNKNOWN_CATEGORY}
    assert employment <= {"salaried", "self_employed", "business_owner", "unemployed", "retired", "student"}


def test_vocabulary_is_learned_from_training_data_only(dataset):
    """Fitting on all splits would let the encoder see the future."""
    train = dataset.customers[dataset.customers["split"] == "train"]
    pipeline = FeaturePipeline().fit(train, dataset.events)
    train_countries = set(normalise_category(train["country_of_residence"]).dropna())
    learned = set(pipeline.encoder.vocabularies["country_of_residence"]) - {MISSING_CATEGORY, UNKNOWN_CATEGORY}
    assert learned <= train_countries


# --------------------------------------------------------------------------
# Missingness
# --------------------------------------------------------------------------


def test_missing_indicators_exist_for_every_declared_column(fitted):
    _, X = fitted
    for column in INDICATOR_COLUMNS:
        assert f"{column}_is_missing" in X.columns


def test_missing_indicator_matches_the_source(fitted, dataset):
    _, X = fitted
    expected = dataset.customers["bureau_score"].isna().to_numpy()
    np.testing.assert_array_equal(X["bureau_score_is_missing"].to_numpy() == 1.0, expected)


def test_nulls_are_preserved_rather_than_imputed(fitted):
    """Imputing here would destroy the MNAR signal the generator puts in."""
    _, X = fitted
    assert X["bureau_score"].isna().any()


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------


def test_contract_round_trips_through_disk(fitted, tmp_path):
    pipeline, _ = fitted
    pipeline.contract.save(tmp_path / "c.json")
    loaded = FeatureContract.load(tmp_path / "c.json")
    assert loaded.names == pipeline.contract.names
    assert loaded.categorical_names == pipeline.contract.categorical_names
    assert loaded.categories == pipeline.contract.categories


def test_pipeline_round_trips_and_reproduces_features(fitted, dataset, tmp_path):
    pipeline, X = fitted
    pipeline.save(tmp_path)
    restored = FeaturePipeline.load(tmp_path)
    pd.testing.assert_frame_equal(restored.transform(dataset.customers, dataset.events), X)


def test_contract_rejects_a_missing_column(fitted):
    pipeline, X = fitted
    with pytest.raises(ContractViolation, match="missing"):
        pipeline.contract.validate(X.drop(columns=[X.columns[3]]))


def test_contract_rejects_an_out_of_range_value():
    contract = FeatureContract(specs=(FeatureSpec("ratio", "numeric", "x", "d", minimum=0.0, maximum=1.0),))
    contract.validate(pd.DataFrame({"ratio": [0.5]}))
    with pytest.raises(ContractViolation, match="above maximum"):
        contract.validate(pd.DataFrame({"ratio": [1.5]}))


def test_unfitted_pipeline_refuses_to_transform(dataset):
    with pytest.raises(RuntimeError):
        FeaturePipeline().transform(dataset.customers, dataset.events)


# --------------------------------------------------------------------------
# Derived features
# --------------------------------------------------------------------------


def test_declared_derived_columns_match_what_is_produced(fitted):
    _, X = fitted
    assert set(DERIVED_COLUMNS) <= set(X.columns)


def test_removed_monotone_transforms_stay_removed(fitted):
    """These were measured at zero contribution; a tree is invariant to them."""
    _, X = fitted
    for column in ("debt_service_headroom", "income_verification_gap", "savings_months_of_cover",
                   "delinquency_recency_weight", "account_seasoning_bucket", "income_continuity"):
        assert column not in X.columns


def test_safe_ratio_returns_nan_not_infinity():
    result = safe_ratio(np.array([1.0, 2.0]), np.array([0.0, 2.0]))
    assert np.isnan(result[0]) and result[1] == 1.0


def test_every_source_categorical_is_encoded(fitted):
    pipeline, _ = fitted
    assert set(CATEGORICAL_COLUMNS) == set(pipeline.contract.categorical_names)
