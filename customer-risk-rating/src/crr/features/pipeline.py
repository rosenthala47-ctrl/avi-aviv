"""The feature pipeline: raw frames in, model-ready matrix out.

One class, one code path, used for both batch training and single-customer
serving. That is not tidiness for its own sake — training/serving skew is the
most common way a credit model degrades silently in production, and the only
reliable defence is that there is no second implementation to drift.

Everything the pipeline learns at fit time (category vocabularies) is saved with
the model. Nothing else is fitted: there are no imputation means, no target
encodings, no scalers. Gradient-boosted trees handle nulls natively, and
imputing a missing value here would destroy the MNAR signal the generator
deliberately puts in (see :func:`crr.features.transforms.missing_indicators`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from crr.features.contract import FeatureContract, FeatureSpec, assert_no_leakage
from crr.features.events import EVENT_WINDOWS, build_event_features, event_feature_specs
from crr.features.transforms import CategoricalEncoder, missing_indicators, safe_ratio

PIPELINE_VERSION = "1.0.0"

#: Identifiers and PII. Dropped, never modelled.
DROP_COLUMNS: tuple[str, ...] = (
    "customer_id", "snapshot_date", "split",
    "full_name", "national_id", "email", "phone", "address_line",
)

#: Columns modelled as categories rather than numbers.
CATEGORICAL_COLUMNS: tuple[str, ...] = (
    "segment", "occupation", "employment_status", "education_level", "marital_status",
    "preferred_channel", "residency_status", "country_of_residence", "pep_relationship",
    "source_of_funds_declared", "beneficial_ownership_transparency", "jurisdiction_risk_tier",
)

#: Columns that get an explicit "was this missing?" indicator. Declared statically
#: rather than discovered from the data: if it were discovered, fitting on a clean
#: extract would produce no indicators, and the first dirty batch at serving time
#: would hit columns the contract never mentioned.
INDICATOR_COLUMNS: tuple[str, ...] = (
    "bureau_score", "verified_income_ratio", "income_volatility_cv", "education_level",
    "years_at_employer", "source_of_funds_verified", "loan_to_value", "collateral_coverage_ratio",
)

#: The raw columns :meth:`FeaturePipeline._derive` reads. Declared so the serving
#: path can convert only these to numeric instead of the whole (mostly string)
#: customer frame. Kept in sync by a test that asserts _derive never reads outside it.
_DERIVE_SOURCE_COLUMNS: tuple[str, ...] = (
    "declared_annual_income", "total_credit_limit", "revolving_balance", "avg_monthly_balance",
    "min_monthly_balance", "savings_to_income_ratio", "dti_ratio", "verified_income_ratio",
    "income_months_missing_12m", "overdraft_days_12m", "overdraft_events_12m",
    "months_since_last_delinquency", "delinquencies_30d_12m", "delinquencies_60d_24m",
    "delinquencies_90d_24m", "num_credit_inquiries_12m", "num_open_loans", "num_products_held",
    "account_age_months", "age", "sanctions_screen_hits", "adverse_media_hits_12m",
    "kyc_document_completeness", "kyc_refresh_overdue_days", "pep_flag",
    "high_risk_jurisdiction_exposure", "sar_filed_prior", "edd_required",
    "source_of_funds_verified", "cash_intensity_ratio", "crypto_exposure_ratio_90d",
)

#: Everything :meth:`FeaturePipeline._derive` produces. Declared here so the
#: ablation study can isolate the block, and asserted against the actual output
#: so the two cannot drift apart.
DERIVED_COLUMNS: tuple[str, ...] = (
    "credit_limit_to_income", "revolving_balance_to_income", "balance_months_of_income",
    "min_balance_months_of_income", "overdraft_days_per_event", "delinquency_severity",
    "inquiries_per_open_loan", "products_per_tenure_year", "tenure_to_age_ratio",
    "screening_hit_total", "kyc_deficiency_score", "aml_flag_count", "cash_and_crypto_exposure",
)

# Deliberately NOT derived, after the phase 2 ablation measured them at zero:
#
#   debt_service_headroom      = 1 - dti_ratio
#   income_verification_gap    = 1 - verified_income_ratio
#   income_continuity          = 1 - income_months_missing_12m / 12
#   savings_months_of_cover    = savings_to_income_ratio * 12
#   delinquency_recency_weight = exp(-months_since_last_delinquency / 12)
#   account_seasoning_bucket   = binned account_age_months
#
# Every one is a MONOTONE transform of a single column that is already a feature,
# and a decision tree is invariant to those: a split at x >= t is the same
# partition as a split at f(x) >= f(t). They cannot add information to a tree
# model, however sensible they look in a feature list. They would matter for a
# linear model, where the functional form is the modeller's job — which is
# exactly why they get written by reflex and then never questioned.

class FeaturePipeline:
    """Fit on the training split only, then transform anything."""

    def __init__(self, windows: tuple[int, ...] = EVENT_WINDOWS) -> None:
        self.windows = tuple(windows)
        self.encoder = CategoricalEncoder()
        self._contract: FeatureContract | None = None

    # ---- properties ------------------------------------------------------

    @property
    def contract(self) -> FeatureContract:
        if self._contract is None:
            raise RuntimeError("pipeline has not been fitted")
        return self._contract

    @property
    def is_fitted(self) -> bool:
        return self._contract is not None

    # ---- fit / transform -------------------------------------------------

    def fit(self, customers: pd.DataFrame, events: pd.DataFrame | None = None) -> FeaturePipeline:
        """Learn category vocabularies and freeze the output contract.

        Must be given the TRAINING split only. Fitting on all the data lets the
        vocabulary see categories that only exist in the test period, which is a
        mild but real form of leakage and exactly the kind that survives review.
        """
        derived = self._derive(customers)
        categorical_source = self._categorical_source(customers, derived)
        self.encoder.fit(categorical_source, list(categorical_source.columns))

        # Build once to discover the concrete numeric column set, then freeze it.
        frame = self._assemble(customers, events)
        self._contract = FeatureContract(
            pipeline_version=PIPELINE_VERSION,
            specs=tuple(self._specs(frame)),
            categories={name: list(vocabulary) for name, vocabulary in self.encoder.vocabularies.items()},
        )
        return self

    def transform(self, customers: pd.DataFrame, events: pd.DataFrame | None = None) -> pd.DataFrame:
        """Build the feature matrix, validated against the frozen contract."""
        frame = self._assemble(customers, events)
        frame = frame[self.contract.names]
        self.contract.validate(frame, check_ranges=False)
        return frame

    def fit_transform(self, customers: pd.DataFrame, events: pd.DataFrame | None = None) -> pd.DataFrame:
        return self.fit(customers, events).transform(customers, events)

    # ---- internals -------------------------------------------------------

    def _assemble(self, customers: pd.DataFrame, events: pd.DataFrame | None) -> pd.DataFrame:
        if "customer_id" not in customers.columns:
            raise ValueError("customers frame must carry customer_id")

        derived = self._derive(customers)
        categorical_source = self._categorical_source(customers, derived)
        categorical = self.encoder.transform(categorical_source)

        numeric_columns = [
            column
            for column in customers.columns
            if column not in DROP_COLUMNS and column not in CATEGORICAL_COLUMNS
        ]
        numeric = customers[numeric_columns].apply(pd.to_numeric, errors="coerce").astype("float64")

        derived_numeric = derived.astype("float64")
        indicators = missing_indicators(customers, list(INDICATOR_COLUMNS))
        events_frame = self._event_block(customers, events)

        for block in (numeric, derived_numeric, indicators, categorical, events_frame):
            block.index = customers.index

        frame = pd.concat([numeric, derived_numeric, indicators, categorical, events_frame], axis=1)
        frame = frame.loc[:, ~frame.columns.duplicated()]
        assert_no_leakage(frame)
        return frame

    def _event_block(self, customers: pd.DataFrame, events: pd.DataFrame | None) -> pd.DataFrame:
        index = pd.DataFrame(
            {
                "customer_id": customers["customer_id"].to_numpy(),
                "as_of": pd.to_datetime(customers["snapshot_date"]).to_numpy(),
                "home_country": customers["country_of_residence"].to_numpy(),
            }
        )
        empty = pd.DataFrame(
            columns=[
                "event_id", "customer_id", "event_ts", "event_type",
                "amount", "counterparty_country", "channel", "is_trigger_event",
            ]
        )
        block = build_event_features(events if events is not None else empty, index, self.windows)
        return block.reset_index(drop=True).astype("float64")

    def _categorical_source(self, customers: pd.DataFrame, derived: pd.DataFrame) -> pd.DataFrame:
        return customers[list(CATEGORICAL_COLUMNS)].copy()

    def _derive(self, customers: pd.DataFrame) -> pd.DataFrame:
        """Domain-driven features a credit analyst would build by hand.

        A note on honesty: some of these mirror the structure the synthetic
        generator uses, because both come from the same domain knowledge — a
        real analyst does build "limit relative to income" and does bucket
        account seasoning. The lift they produce here is therefore optimistic
        relative to what the same features would give on real data. They are not
        copies of the generator's functional forms, and none of them uses a
        quantity that would be unavailable at serving time.
        """
        # Convert only the numeric columns _derive actually reads, not the whole
        # frame: applying to_numeric to the string categoricals produced NaN
        # columns that were never used and dominated the serving-path allocation.
        needed = [c for c in _DERIVE_SOURCE_COLUMNS if c in customers.columns]
        numeric = customers[needed].apply(pd.to_numeric, errors="coerce")
        income = numeric["declared_annual_income"].to_numpy(dtype=float)
        monthly_income = income / 12.0
        account_age = numeric["account_age_months"].to_numpy(dtype=float)

        derived = pd.DataFrame(index=customers.index)

        # --- affordability and leverage --------------------------------------
        derived["credit_limit_to_income"] = safe_ratio(numeric["total_credit_limit"], income, cap=20)
        derived["revolving_balance_to_income"] = safe_ratio(numeric["revolving_balance"], income, cap=20)
        derived["balance_months_of_income"] = safe_ratio(numeric["avg_monthly_balance"], monthly_income, cap=200)
        derived["min_balance_months_of_income"] = safe_ratio(numeric["min_monthly_balance"], monthly_income, cap=200)

        # --- repayment behaviour ---------------------------------------------
        derived["overdraft_days_per_event"] = safe_ratio(
            numeric["overdraft_days_12m"], numeric["overdraft_events_12m"].clip(lower=1), cap=365
        )
        derived["delinquency_severity"] = (
            numeric["delinquencies_30d_12m"].to_numpy(dtype=float)
            + 2.0 * numeric["delinquencies_60d_24m"].to_numpy(dtype=float)
            + 4.0 * numeric["delinquencies_90d_24m"].to_numpy(dtype=float)
        )
        derived["inquiries_per_open_loan"] = safe_ratio(
            numeric["num_credit_inquiries_12m"], numeric["num_open_loans"].clip(lower=1), cap=25
        )

        # --- relationship ------------------------------------------------------
        derived["products_per_tenure_year"] = safe_ratio(numeric["num_products_held"], account_age / 12.0, cap=50)
        derived["tenure_to_age_ratio"] = safe_ratio(account_age / 12.0, numeric["age"].clip(lower=18), cap=1.5)

        # --- AML composites ----------------------------------------------------
        derived["screening_hit_total"] = (
            numeric["sanctions_screen_hits"].to_numpy(dtype=float) + numeric["adverse_media_hits_12m"].to_numpy(dtype=float)
        )
        derived["kyc_deficiency_score"] = (1.0 - numeric["kyc_document_completeness"].to_numpy(dtype=float)) + (
            numeric["kyc_refresh_overdue_days"].to_numpy(dtype=float) > 0
        ).astype(float)
        derived["aml_flag_count"] = (
            numeric["pep_flag"].fillna(0).to_numpy(dtype=float)
            + numeric["high_risk_jurisdiction_exposure"].fillna(0).to_numpy(dtype=float)
            + numeric["sar_filed_prior"].fillna(0).to_numpy(dtype=float)
            + numeric["edd_required"].fillna(0).to_numpy(dtype=float)
            + (1.0 - numeric["source_of_funds_verified"].fillna(0).to_numpy(dtype=float))
        )
        derived["cash_and_crypto_exposure"] = (
            numeric["cash_intensity_ratio"].to_numpy(dtype=float) + numeric["crypto_exposure_ratio_90d"].to_numpy(dtype=float)
        )

        if tuple(derived.columns) != DERIVED_COLUMNS:
            raise RuntimeError(
                "DERIVED_COLUMNS is out of sync with _derive: "
                f"{sorted(set(derived.columns) ^ set(DERIVED_COLUMNS))}"
            )
        return derived

    def _specs(self, frame: pd.DataFrame) -> list[FeatureSpec]:
        """Contract entries for the assembled frame."""
        event_specs = {spec.name: spec for spec in event_feature_specs(self.windows)}
        specs: list[FeatureSpec] = []
        for column in frame.columns:
            if column in event_specs:
                specs.append(event_specs[column])
            elif column in self.encoder.vocabularies:
                specs.append(FeatureSpec(column, "categorical", "categorical", f"Normalised category: {column}."))
            elif column in DERIVED_COLUMNS:
                specs.append(FeatureSpec(column, "numeric", "derived", f"Engineered ratio or composite: {column}."))
            elif column.endswith("_is_missing"):
                source = column.removesuffix("_is_missing")
                specs.append(
                    FeatureSpec(column, "indicator", "indicator", f"Source value for {source} was absent.", minimum=0, maximum=1)
                )
            else:
                specs.append(FeatureSpec(column, "numeric", "customer", f"Customer attribute or derived ratio: {column}."))
        return specs

    # ---- persistence -----------------------------------------------------

    def save(self, directory: str | Path) -> None:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        state: dict[str, Any] = {
            "pipeline_version": PIPELINE_VERSION,
            "windows": list(self.windows),
            "vocabularies": self.encoder.vocabularies,
        }
        (path / "pipeline_state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        self.contract.save(path / "feature_contract.json")

    @classmethod
    def load(cls, directory: str | Path) -> FeaturePipeline:
        path = Path(directory)
        state = json.loads((path / "pipeline_state.json").read_text(encoding="utf-8"))
        pipeline = cls(windows=tuple(state["windows"]))
        pipeline.encoder.vocabularies = {k: list(v) for k, v in state["vocabularies"].items()}
        pipeline._contract = FeatureContract.load(path / "feature_contract.json")
        return pipeline
