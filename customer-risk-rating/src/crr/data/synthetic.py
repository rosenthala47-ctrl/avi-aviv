"""Synthetic Customer Risk Rating dataset generator (alpha training data).

Why this file exists
--------------------
Before we can train anything we need data whose *ground truth we control*. A naive
generator that draws random features and a random label produces a dataset on which
no model can beat AUC 0.5 — training on it teaches us nothing. So this generator is
built as an explicit **structural causal model**:

    1. latent factors      z_credit, z_liquidity, z_volatility, z_concealment
    2. observable features  drawn *conditionally* on those latents (+ noise, so the
                            mapping features -> latents is not invertible)
    3. latent text signals  distress / concealment, only partly reflected in (2)
    4. outcomes             drawn from a documented log-odds equation over (2) + (3)

Consequences that matter for the alpha:

  * There is a real, learnable signal, with a known ceiling. Irreducible noise is
    injected deliberately so a well-fit model lands around AUC 0.82-0.88 on the
    credit target rather than an implausible 0.99.
  * Non-linearities and interactions are included, so gradient-boosted trees
    should beat plain logistic regression — which is what we will claim.
  * Part of the signal lives ONLY in the free text. A tabular-only model is
    therefore provably leaving lift on the table; closing that gap is the
    measurable justification for the hybrid LLM branch.
  * Ground-truth latents are written to a SEPARATE file. They must never be joined
    into a training feature frame — see ``GROUND_TRUTH_COLUMNS``.

Everything here is fictional. No real person, account or entity is represented.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import narratives as nrt
from . import taxonomy as tx

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0.0"

#: Columns that encode the generator's hidden state. Useful for evaluation and
#: calibration studies, illegal as model inputs.
GROUND_TRUTH_COLUMNS: tuple[str, ...] = (
    "z_credit_quality",
    "z_liquidity_stress",
    "z_volatility",
    "z_concealment",
    "latent_distress",
    "latent_concealment_text",
    "narrative_distress_level",
    "narrative_concealment_level",
    "p_default_true",
    "p_financial_crime_true",
    "true_risk_score",
    "true_risk_band",
)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass
class GeneratorConfig:
    """All knobs for one generation run. Serialised into the run manifest."""

    n_customers: int = 10_000
    seed: int = 42
    as_of: dt.date = dt.date(2026, 1, 1)
    cohort_months: int = 12
    """Snapshot dates are spread over this many months, ending at ``as_of``."""

    outcome_horizon_months: int = 12
    """Performance window after the snapshot in which the label is observed."""

    target_default_rate: float = 0.055
    target_financial_crime_rate: float = 0.015

    # --- realism switches -------------------------------------------------
    inject_missingness: bool = True
    inject_categorical_noise: bool = True
    categorical_noise_rate: float = 0.02
    duplicate_rate: float = 0.004
    """Share of near-duplicate records, for entity-resolution testing."""

    include_pii: bool = True
    """Emit synthetic PII columns so the anonymisation layer has something to strip."""

    # --- text -------------------------------------------------------------
    language: str = "en"  # "en" | "he" | "mixed"
    hebrew_share: float = 0.35  # used when language == "mixed"

    # --- event stream -----------------------------------------------------
    generate_events: bool = True
    events_per_customer: float = 12.0
    event_window_days: int = 180
    trigger_event_rate: float = 0.08
    """Share of customers who receive a fresh shock event in the last 30 days."""

    # --- signal-to-noise --------------------------------------------------
    # The composite driver is standardised to unit variance and then scaled by
    # ``*_signal_strength``, against N(0, ``*_noise_sd``) irreducible noise. The
    # RATIO is what sets the achievable AUC. These defaults were tuned to land a
    # well-fit model around AUC 0.82 (credit) / 0.80 (financial crime) — the
    # realistic range for a production model. Raise them and the alpha will look
    # brilliant on synthetic data and disappoint on real data.
    credit_signal_strength: float = 1.15
    credit_noise_sd: float = 1.00
    crime_signal_strength: float = 1.20
    crime_noise_sd: float = 1.00

    def validate(self) -> None:
        if self.n_customers < 1:
            raise ValueError("n_customers must be >= 1")
        if not 0.0 < self.target_default_rate < 0.5:
            raise ValueError("target_default_rate must be in (0, 0.5)")
        if not 0.0 < self.target_financial_crime_rate < 0.5:
            raise ValueError("target_financial_crime_rate must be in (0, 0.5)")
        if self.language not in ("en", "he", "mixed"):
            raise ValueError("language must be one of: en, he, mixed")
        if self.cohort_months < 1:
            raise ValueError("cohort_months must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["as_of"] = self.as_of.isoformat()
        return payload


@dataclass
class SyntheticDataset:
    """The frames produced by one generation run."""

    customers: pd.DataFrame
    narratives: pd.DataFrame
    events: pd.DataFrame
    outcomes: pd.DataFrame
    ground_truth: pd.DataFrame
    config: GeneratorConfig
    manifest: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Small numeric helpers
# --------------------------------------------------------------------------


def _sigmoid(x: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -35.0, 35.0)))


def _rescale(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Min-max rescale, robust to a constant vector."""
    span = float(np.nanmax(x) - np.nanmin(x))
    if span < 1e-12:
        return np.full_like(x, (lo + hi) / 2.0, dtype=float)
    return lo + (x - float(np.nanmin(x))) * (hi - lo) / span


def _calibrate_intercept(linear: np.ndarray, target_rate: float, tol: float = 1e-6) -> float:
    """Solve for b0 such that ``mean(sigmoid(b0 + linear)) == target_rate``.

    Bisection: the mean probability is strictly increasing in b0, so a plain
    bracket-and-halve converges quickly and deterministically.
    """
    lo, hi = -40.0, 40.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        rate = float(np.mean(_sigmoid(mid + linear)))
        if abs(rate - target_rate) < tol:
            return mid
        if rate < target_rate:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _weighted_choice(rng: np.random.Generator, labels: tuple[str, ...], weights: np.ndarray, n: int) -> np.ndarray:
    """Vectorised categorical draw from a per-row weight matrix (n x k)."""
    probs = weights / weights.sum(axis=1, keepdims=True)
    cum = np.cumsum(probs, axis=1)
    draws = rng.random((n, 1))
    idx = (draws > cum).sum(axis=1)
    idx = np.clip(idx, 0, len(labels) - 1)
    return np.asarray(labels, dtype=object)[idx]


def _bin_to_level(x: np.ndarray, cuts: tuple[float, float, float]) -> np.ndarray:
    """Map a continuous 0..1 score onto integer levels 0..3."""
    return np.digitize(x, np.asarray(cuts)).astype(int)


# --------------------------------------------------------------------------
# Block 1 — latent factors
# --------------------------------------------------------------------------


def _draw_latents(rng: np.random.Generator, n: int) -> dict[str, np.ndarray]:
    """Four correlated latent drivers of customer risk.

    Correlation structure (chosen, not fitted):
      * credit quality and liquidity stress are strongly negatively correlated
      * volatility is mildly linked to liquidity stress
      * concealment is close to independent of credit health — a wealthy,
        perfectly-performing customer can still be a financial-crime risk. Keeping
        these axes separate is what lets us score credit and compliance risk
        independently instead of collapsing them into one number.
    """
    cov = np.array(
        [
            [1.00, -0.55, -0.20, -0.05],
            [-0.55, 1.00, 0.35, 0.10],
            [-0.20, 0.35, 1.00, 0.12],
            [-0.05, 0.10, 0.12, 1.00],
        ]
    )
    draws = rng.multivariate_normal(mean=np.zeros(4), cov=cov, size=n, method="cholesky")
    return {
        "z_credit_quality": draws[:, 0],
        "z_liquidity_stress": draws[:, 1],
        "z_volatility": draws[:, 2],
        "z_concealment": draws[:, 3],
    }


# --------------------------------------------------------------------------
# Block 2 — profile / demographics
# --------------------------------------------------------------------------


def _draw_profile(rng: np.random.Generator, cfg: GeneratorConfig, n: int, z: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    seg_labels = tuple(tx.SEGMENTS.keys())
    seg_weights = np.tile(np.array(list(tx.SEGMENTS.values())), (n, 1))
    segment = _weighted_choice(rng, seg_labels, seg_weights, n)

    # Occupation: private banking skews to high-income roles, SME to owner-operators.
    occ_base = np.ones((n, len(tx.OCCUPATIONS)))
    income_mu = np.array([o.income_mu for o in tx.OCCUPATIONS])
    self_emp_p = np.array([o.self_employed_p for o in tx.OCCUPATIONS])
    occ_base *= np.exp(0.9 * (income_mu - income_mu.mean()) * (segment == "private_banking")[:, None])
    occ_base *= np.exp(1.4 * (self_emp_p - self_emp_p.mean()) * np.isin(segment, ["sme", "corporate"])[:, None])
    occupation = _weighted_choice(rng, tx.OCCUPATION_NAMES, occ_base, n)

    occ_index = {o.name: i for i, o in enumerate(tx.OCCUPATIONS)}
    occ_idx = np.array([occ_index[o] for o in occupation])
    occ_cash = np.array([o.cash_intensity for o in tx.OCCUPATIONS])[occ_idx]
    occ_pep = np.array([o.pep_propensity for o in tx.OCCUPATIONS])[occ_idx]
    occ_mu = income_mu[occ_idx]
    occ_sigma = np.array([o.income_sigma for o in tx.OCCUPATIONS])[occ_idx]
    occ_self_emp = self_emp_p[occ_idx]

    # Age: right-skewed, clipped to a bankable range; students young, retirees old.
    age = np.clip(rng.gamma(shape=5.2, scale=5.1, size=n) + 19, 18, 92)
    age = np.where(occupation == "student", rng.uniform(18, 29, n), age)
    age = np.where(occupation == "retired", rng.uniform(62, 90, n), age)
    age = np.round(age).astype(int)

    employment = np.where(
        occupation == "student",
        "student",
        np.where(
            occupation == "retired",
            "retired",
            np.where(
                occupation == "unemployed",
                "unemployed",
                np.where(
                    rng.random(n) < occ_self_emp,
                    np.where(np.isin(segment, ["sme", "corporate"]), "business_owner", "self_employed"),
                    "salaried",
                ),
            ),
        ),
    )

    years_at_employer = np.clip(
        np.where(
            employment == "salaried",
            rng.gamma(2.0, 2.6, n),
            rng.gamma(2.6, 3.1, n),
        ),
        0.0,
        np.maximum(age - 18, 0.5),
    ).round(1)

    # Account age is bounded by adult life, and private-banking relationships are older.
    account_age_months = np.clip(
        rng.gamma(2.2, 22.0, n) + 12.0 * np.isin(segment, ["private_banking", "corporate"]),
        1,
        (age - 17) * 12,
    ).round().astype(int)

    country = _draw_country(rng, n, z["z_concealment"], segment)

    edu_weights = np.tile(np.array([22.0, 16.0, 38.0, 20.0, 4.0]), (n, 1))
    edu_weights *= np.exp(0.35 * z["z_credit_quality"])[:, None] ** np.array([-1.0, -0.4, 0.2, 0.8, 1.2])
    education = _weighted_choice(rng, tx.EDUCATION_LEVELS, edu_weights, n)

    marital = _weighted_choice(
        rng, tx.MARITAL_STATUSES, np.tile(np.array([34.0, 48.0, 14.0, 4.0]), (n, 1)), n
    )
    dependents = rng.poisson(np.clip(0.15 * (age - 20) * (marital == "married") / 6.0 + 0.3, 0, 5)).clip(0, 8)

    channel = _weighted_choice(
        rng,
        tx.CHANNELS,
        np.column_stack(
            [
                12.0 + 18.0 * (age > 60),
                34.0 * np.ones(n),
                40.0 - 20.0 * (age > 60),
                6.0 + 30.0 * np.isin(segment, ["private_banking", "corporate"]),
            ]
        ),
        n,
    )

    residency = _weighted_choice(
        rng,
        tx.RESIDENCY_STATUSES,
        np.column_stack(
            [
                np.where(country == "IL", 86.0, 30.0),
                10.0 * np.ones(n),
                np.where(country == "IL", 3.0, 12.0),
                np.where(country == "IL", 1.0, 48.0),
            ]
        ),
        n,
    )

    num_products = np.clip(
        rng.poisson(1.6 + 0.02 * account_age_months + 1.2 * np.isin(segment, ["private_banking", "corporate"])), 1, 8
    )

    return {
        "segment": segment,
        "occupation": occupation,
        "employment_status": employment,
        "age": age,
        "years_at_employer": years_at_employer,
        "account_age_months": account_age_months,
        "education_level": education,
        "marital_status": marital,
        "dependents_count": dependents,
        "preferred_channel": channel,
        "residency_status": residency,
        "country_of_residence": country,
        "num_products_held": num_products,
        "_occ_cash_intensity": occ_cash,
        "_occ_pep_propensity": occ_pep,
        "_occ_income_mu": occ_mu,
        "_occ_income_sigma": occ_sigma,
    }


def _draw_country(rng: np.random.Generator, n: int, z_concealment: np.ndarray, segment: np.ndarray) -> np.ndarray:
    """Country of residence, tilted toward riskier jurisdictions as concealment rises."""
    codes = np.array([j.code for j in tx.JURISDICTIONS], dtype=object)
    base = np.array([j.base_weight for j in tx.JURISDICTIONS])
    tier_lift = np.array([tx.TIER_RISK_WEIGHT[j.tier] for j in tx.JURISDICTIONS])
    offshore = np.array([1.0 if j.code in tx.OFFSHORE_CODES else 0.0 for j in tx.JURISDICTIONS])

    weights = np.tile(base, (n, 1))
    weights *= np.exp(np.outer(np.clip(z_concealment, -3, 3), 1.15 * tier_lift))
    weights *= np.exp(np.outer(np.isin(segment, ["private_banking", "corporate"]).astype(float), 0.9 * offshore))
    return _weighted_choice(rng, tuple(codes), weights, n)


# --------------------------------------------------------------------------
# Block 3 — financial position and repayment history
# --------------------------------------------------------------------------


def _draw_financial(
    rng: np.random.Generator,
    n: int,
    z: dict[str, np.ndarray],
    prof: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    zc, zl, zv = z["z_credit_quality"], z["z_liquidity_stress"], z["z_volatility"]
    segment = prof["segment"]
    self_emp = np.isin(prof["employment_status"], ["self_employed", "business_owner"]).astype(float)

    # --- income -----------------------------------------------------------
    declared_income = np.exp(
        prof["_occ_income_mu"]
        + prof["_occ_income_sigma"] * rng.standard_normal(n)
        + 0.16 * zc
        + 0.55 * (segment == "private_banking")
        + 0.30 * (segment == "corporate")
    ).round(-2)

    # Verified/declared: self-employed and opaque customers over-state more often.
    verified_income_ratio = np.clip(
        1.0
        - np.abs(rng.normal(0.04 + 0.09 * self_emp + 0.07 * np.clip(z["z_concealment"], 0, None), 0.09, n)),
        0.35,
        1.15,
    ).round(3)

    # Coefficient of variation of monthly income over the last 12 months.
    income_volatility_cv = np.clip(
        np.exp(-2.05 + 0.52 * zv + 0.62 * self_emp + 0.35 * rng.standard_normal(n)), 0.01, 3.0
    ).round(3)
    income_months_missing = rng.binomial(12, np.clip(0.02 + 0.06 * _sigmoid(zl) + 0.05 * self_emp, 0, 0.6))

    # --- credit facilities -------------------------------------------------
    total_credit_limit = np.clip(
        declared_income * np.clip(rng.normal(0.42 + 0.10 * zc, 0.16, n), 0.03, 1.5), 2_000, None
    ).round(-2)
    credit_utilization_ratio = np.clip(
        _sigmoid(-0.75 - 0.60 * zc + 0.85 * zl + 0.45 * rng.standard_normal(n)) * 1.25, 0.0, 1.35
    ).round(4)
    revolving_balance = (total_credit_limit * credit_utilization_ratio).round(-1)

    num_open_loans = rng.poisson(np.clip(0.9 + 0.45 * zl - 0.15 * zc + 0.6 * (segment == "sme"), 0.05, 8)).clip(0, 12)
    dti_ratio = np.clip(rng.normal(0.29 + 0.135 * zl - 0.055 * zc, 0.095, n), 0.01, 1.7).round(4)
    num_credit_inquiries_12m = rng.poisson(np.clip(np.exp(-0.35 + 0.55 * zl - 0.25 * zc), 0.02, 14)).clip(0, 20)

    # --- repayment history -------------------------------------------------
    delinq_lambda = np.exp(-1.75 - 0.90 * zc + 0.62 * zl)
    delinquencies_30d_12m = rng.poisson(np.clip(delinq_lambda, 0, 12)).clip(0, 12)
    delinquencies_60d_24m = rng.binomial(np.maximum(delinquencies_30d_12m, 0) + 1, 0.28) * (delinquencies_30d_12m > 0)
    delinquencies_90d_24m = rng.binomial(np.maximum(delinquencies_60d_24m, 0) + 1, 0.22) * (delinquencies_60d_24m > 0)

    max_days_past_due_24m = np.where(
        delinquencies_30d_12m + delinquencies_60d_24m == 0,
        0,
        np.clip(rng.gamma(2.0, 16.0, n) + 25 * delinquencies_90d_24m, 1, 360),
    ).round().astype(int)

    months_since_last_delinquency = np.where(
        delinquencies_30d_12m == 0,
        np.clip(rng.gamma(3.0, 12.0, n), 0, 240).round().astype(int),
        rng.integers(0, 13, n),
    )
    # No delinquency can predate the relationship.
    months_since_last_delinquency = np.minimum(months_since_last_delinquency, prof["account_age_months"])

    prior_default_flag = (
        rng.random(n) < np.clip(_sigmoid(-3.1 - 0.85 * zc + 0.55 * zl), 0, 0.6)
    ).astype(int)
    num_bounced_payments_12m = rng.poisson(np.clip(np.exp(-2.4 + 0.75 * zl - 0.35 * zc), 0, 10)).clip(0, 15)

    bureau_score = np.clip(
        690 + 58 * zc - 20 * zl - 34 * prior_default_flag - 6 * delinquencies_30d_12m + rng.normal(0, 32, n),
        300,
        850,
    ).round().astype(int)
    payment_history_score = np.clip(
        78 + 9.5 * zc - 5.5 * zl - 4.0 * delinquencies_30d_12m - 9.0 * prior_default_flag + rng.normal(0, 6, n),
        0,
        100,
    ).round(1)

    # --- balances / liquidity ---------------------------------------------
    avg_monthly_balance = np.clip(
        declared_income / 12.0 * np.clip(rng.normal(1.35 - 0.55 * zl + 0.35 * zc, 0.75, n), 0.01, None), 0, None
    ).round(-1)
    balance_volatility = np.clip(rng.normal(0.30 + 0.18 * zv + 0.10 * zl, 0.12, n), 0.01, 2.5).round(4)
    min_monthly_balance = (avg_monthly_balance * np.clip(1.0 - 1.9 * balance_volatility, -1.2, 0.95)).round(-1)

    overdraft_events_12m = rng.poisson(np.clip(np.exp(-1.45 + 0.82 * zl + 0.30 * zv), 0, 30)).clip(0, 40)
    overdraft_days_12m = np.where(
        overdraft_events_12m == 0, 0, np.clip(rng.gamma(1.8, 6.0, n) * overdraft_events_12m / 2.0, 0, 365)
    ).round().astype(int)

    savings_to_income_ratio = np.clip(
        rng.gamma(1.6, 0.13, n) * np.exp(0.45 * zc - 0.55 * zl), 0.0, 6.0
    ).round(4)

    has_secured = (num_open_loans > 0) & (rng.random(n) < 0.42)
    loan_to_value = np.where(has_secured, np.clip(rng.normal(0.68 + 0.09 * zl, 0.15, n), 0.05, 1.35), np.nan).round(4)
    collateral_coverage_ratio = np.where(has_secured, np.clip(1.0 / np.maximum(loan_to_value, 0.05), 0.4, 12.0), np.nan).round(3)

    return {
        "declared_annual_income": declared_income,
        "verified_income_ratio": verified_income_ratio,
        "income_volatility_cv": income_volatility_cv,
        "income_months_missing_12m": income_months_missing,
        "total_credit_limit": total_credit_limit,
        "credit_utilization_ratio": credit_utilization_ratio,
        "revolving_balance": revolving_balance,
        "num_open_loans": num_open_loans,
        "dti_ratio": dti_ratio,
        "num_credit_inquiries_12m": num_credit_inquiries_12m,
        "delinquencies_30d_12m": delinquencies_30d_12m,
        "delinquencies_60d_24m": delinquencies_60d_24m,
        "delinquencies_90d_24m": delinquencies_90d_24m,
        "max_days_past_due_24m": max_days_past_due_24m,
        "months_since_last_delinquency": months_since_last_delinquency,
        "prior_default_flag": prior_default_flag,
        "num_bounced_payments_12m": num_bounced_payments_12m,
        "bureau_score": bureau_score,
        "payment_history_score": payment_history_score,
        "avg_monthly_balance": avg_monthly_balance,
        "min_monthly_balance": min_monthly_balance,
        "balance_volatility": balance_volatility,
        "overdraft_events_12m": overdraft_events_12m,
        "overdraft_days_12m": overdraft_days_12m,
        "savings_to_income_ratio": savings_to_income_ratio,
        "loan_to_value": loan_to_value,
        "collateral_coverage_ratio": collateral_coverage_ratio,
    }


# --------------------------------------------------------------------------
# Block 4 — transactional behaviour
# --------------------------------------------------------------------------


def cash_intensity_shift(occ_cash: np.ndarray, z_concealment: np.ndarray) -> np.ndarray:
    """Blend the occupation's baseline cash usage with the latent concealment factor."""
    return np.clip(0.75 * occ_cash + 0.45 * _sigmoid(z_concealment), 0.0, 1.4)


def _draw_behavioural(
    rng: np.random.Generator,
    n: int,
    z: dict[str, np.ndarray],
    prof: dict[str, np.ndarray],
    fin: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    zk, zv, zl = z["z_concealment"], z["z_volatility"], z["z_liquidity_stress"]
    segment = prof["segment"]
    cash_pref = prof["_occ_cash_intensity"]

    txn_count_90d = rng.poisson(
        np.clip(
            np.exp(3.9 + 0.30 * (segment == "sme") + 0.55 * (segment == "corporate") + 0.25 * rng.standard_normal(n)),
            1,
            4000,
        )
    ).clip(1, 5000)
    avg_txn_amount = np.clip(
        fin["declared_annual_income"] / 12.0 / np.maximum(txn_count_90d / 3.0, 1.0) * np.exp(rng.normal(0, 0.45, n)), 1, None
    ).round(2)
    txn_volume_90d = (txn_count_90d * avg_txn_amount).round(2)

    cash_intensity_ratio = np.clip(
        rng.beta(np.maximum(0.40 + 2.6 * cash_intensity_shift(cash_pref, zk), 0.2), 8.0, n), 0.0, 1.0
    ).round(4)
    large_cash_deposits_90d = rng.poisson(np.clip(np.exp(-2.2 + 3.0 * cash_intensity_ratio + 0.45 * zk), 0, 40)).clip(0, 60)

    cross_border_ratio = np.clip(
        rng.beta(
            np.maximum(0.8 + 2.6 * _sigmoid(zk) + 1.4 * np.isin(segment, ["corporate", "private_banking"]), 0.2), 9.0, n
        ),
        0.0,
        1.0,
    ).round(4)
    cross_border_txn_count_90d = rng.binomial(txn_count_90d, cross_border_ratio)

    night_txn_ratio = np.clip(rng.beta(1.6 + 1.6 * _sigmoid(zk), 12.0, n), 0.0, 1.0).round(4)
    new_counterparty_ratio_90d = np.clip(rng.beta(1.5 + 2.2 * _sigmoid(zk) + 1.0 * _sigmoid(zv), 7.0, n), 0.0, 1.0).round(4)
    merchant_category_entropy = np.clip(rng.normal(2.35 + 0.22 * _sigmoid(zv), 0.45, n), 0.0, 4.2).round(3)

    # 30d volume vs. the trailing 90d baseline. This is the primary trigger for
    # the real-time re-scoring engine, so it gets a deliberately fat tail.
    txn_velocity_change_pct = np.clip(
        rng.normal(0.02 + 0.22 * zl + 0.28 * zk, 0.30, n) + rng.standard_t(df=3, size=n) * 0.10, -0.95, 8.0
    ).round(4)

    gambling_spend_ratio_90d = np.clip(rng.beta(1.05 + 1.1 * _sigmoid(zl), 40.0, n), 0.0, 1.0).round(5)
    crypto_exposure_ratio_90d = np.clip(rng.beta(1.05 + 2.0 * _sigmoid(zk), 30.0, n), 0.0, 1.0).round(5)

    # "Structuring" = deposits deliberately kept just below the reporting threshold.
    structuring_score = np.clip(
        _sigmoid(-3.25 + 1.35 * zk + 1.8 * cash_intensity_ratio + 0.35 * rng.standard_normal(n)), 0.0, 1.0
    ).round(4)

    # --- tier-1 AML/KYC indicators (added post-launch) -----------------------
    # Actual turnover vs. what the declared profile would predict. Centred on 1
    # (as expected); the *spread* around 1 widens with concealment, not the mean,
    # because a mismatch cuts both ways — understated activity hides income,
    # overstated activity means money is moving that the declared profile does
    # not explain. A V-shaped risk term, same idea as _credit_terms' income
    # mismatch, is built from this at the term level, not here.
    expected_vs_actual_turnover_ratio = np.clip(
        1.0 + rng.normal(0.0, 0.15 + 0.55 * _sigmoid(zk), n), 0.0, 10.0
    ).round(3)

    # Hours an inflow rests before leaving again. Unlike every other field in
    # this block, LOW is the red flag — funds passing straight through is the
    # classic layering/mule-account pattern — so the latent pulls the median
    # DOWN, the opposite sign from cash_intensity_ratio/structuring_score above.
    pass_through_velocity_hours = np.clip(
        rng.lognormal(mean=4.9 - 1.3 * _sigmoid(zk), sigma=0.65, size=n), 0.5, 2000.0
    ).round(2)

    # Recent activity volume over the trailing 6-month baseline; 1.0 = no
    # change. Log-normal so the occasional genuine spike has a realistic tail.
    volume_spike_ratio_6m = np.clip(
        rng.lognormal(mean=-0.35 + 0.55 * _sigmoid(zk) + 0.15 * _sigmoid(zv), sigma=0.35, size=n), 0.05, 10.0
    ).round(3)

    # Access via VPN/Tor or a high-risk-reputation IP: sigmoid-propensity ->
    # bernoulli draw, the same template as pep_flag/sar_filed_prior below.
    vpn_p = np.clip(
        _sigmoid(-2.4 + 1.1 * zk + 0.4 * np.isin(segment, ["corporate", "private_banking"])), 0.02, 0.6
    )
    vpn_or_high_risk_ip_flag = (rng.random(n) < vpn_p).astype(int)

    # Distinct devices used in the last 30 days; a handful is routine, a churn
    # of devices tracks the same concealment latent as everything else here.
    device_change_frequency_30d = rng.poisson(
        np.clip(np.exp(-0.3 + 1.8 * _sigmoid(zk)), 0, 20)
    ).clip(0, 60)

    # Share of TOTAL volume (not just activity count) settled in cash — a
    # related but distinct measurement from cash_intensity_ratio above (a real
    # AML program computes both and they rarely agree exactly), so it is drawn
    # as a noisy variant rather than a duplicate of the same number.
    cash_to_total_volume_ratio = np.clip(cash_intensity_ratio + rng.normal(0.0, 0.06, n), 0.0, 1.0).round(4)

    # Counterparty is a Virtual Asset Service Provider (exchange, custodian) —
    # a sharper, more specific signal than the general crypto volume share, so
    # it is drawn as correlated with but not identical to crypto_exposure_ratio_90d.
    crypto_vasp_p = np.clip(crypto_exposure_ratio_90d * 1.6 + 0.02, 0.0, 0.85)
    crypto_vasp_exposure_flag = (rng.random(n) < crypto_vasp_p).astype(int)

    return {
        "txn_count_90d": txn_count_90d,
        "txn_volume_90d": txn_volume_90d,
        "avg_txn_amount": avg_txn_amount,
        "cash_intensity_ratio": cash_intensity_ratio,
        "large_cash_deposits_90d": large_cash_deposits_90d,
        "cross_border_txn_count_90d": cross_border_txn_count_90d,
        "cross_border_txn_ratio": cross_border_ratio,
        "night_txn_ratio": night_txn_ratio,
        "new_counterparty_ratio_90d": new_counterparty_ratio_90d,
        "merchant_category_entropy": merchant_category_entropy,
        "txn_velocity_change_pct": txn_velocity_change_pct,
        "gambling_spend_ratio_90d": gambling_spend_ratio_90d,
        "crypto_exposure_ratio_90d": crypto_exposure_ratio_90d,
        "structuring_score": structuring_score,
        "expected_vs_actual_turnover_ratio": expected_vs_actual_turnover_ratio,
        "pass_through_velocity_hours": pass_through_velocity_hours,
        "volume_spike_ratio_6m": volume_spike_ratio_6m,
        "vpn_or_high_risk_ip_flag": vpn_or_high_risk_ip_flag,
        "device_change_frequency_30d": device_change_frequency_30d,
        "cash_to_total_volume_ratio": cash_to_total_volume_ratio,
        "crypto_vasp_exposure_flag": crypto_vasp_exposure_flag,
    }


# --------------------------------------------------------------------------
# Block 5 — AML / KYC compliance
# --------------------------------------------------------------------------


def _draw_compliance(
    rng: np.random.Generator,
    n: int,
    z: dict[str, np.ndarray],
    prof: dict[str, np.ndarray],
    beh: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    zk = z["z_concealment"]
    country = prof["country_of_residence"]
    tier = np.array([tx.JURISDICTION_TIER[c] for c in country], dtype=object)
    tier_weight = np.array([tx.TIER_RISK_WEIGHT[t] for t in tier])
    segment = prof["segment"]

    pep_p = np.clip(
        _sigmoid(-4.9 + 3.2 * prof["_occ_pep_propensity"] + 0.55 * zk + 0.85 * (segment == "private_banking")), 0, 0.8
    )
    pep_flag = (rng.random(n) < pep_p).astype(int)
    pep_relationship = np.where(
        pep_flag == 0,
        "none",
        _weighted_choice(rng, ("self", "family_member", "close_associate"), np.tile(np.array([40.0, 38.0, 22.0]), (n, 1)), n),
    )

    sanctions_screen_hits = rng.poisson(np.clip(np.exp(-5.0 + 1.15 * zk + 1.35 * tier_weight), 0, 5)).clip(0, 6)
    adverse_media_hits_12m = rng.poisson(
        np.clip(np.exp(-4.0 + 1.05 * zk + 0.75 * tier_weight + 0.8 * pep_flag), 0, 8)
    ).clip(0, 10)

    high_risk_jurisdiction_exposure = np.isin(country, list(tx.HIGH_RISK_CODES)).astype(int)
    medium_risk_jurisdiction_exposure = np.isin(country, list(tx.MEDIUM_RISK_CODES)).astype(int)

    offshore_entity_links = rng.poisson(
        np.clip(np.exp(-3.2 + 1.25 * zk + 1.1 * np.isin(segment, ["corporate", "private_banking"])), 0, 8)
    ).clip(0, 12)

    # Source of funds: blend the "clean" and "opaque" mixtures by latent concealment.
    mix = _sigmoid(1.15 * zk)[:, None]
    clean_w = np.array([tx.SOF_WEIGHTS_CLEAN[s] for s in tx.SOURCE_OF_FUNDS])
    opaque_w = np.array([tx.SOF_WEIGHTS_OPAQUE[s] for s in tx.SOURCE_OF_FUNDS])
    sof_weights = (1 - mix) * clean_w + mix * opaque_w
    source_of_funds = _weighted_choice(rng, tx.SOURCE_OF_FUNDS, sof_weights, n)

    sof_verify_p = np.clip(
        0.93 - 0.30 * _sigmoid(zk) - 0.25 * np.isin(source_of_funds, list(tx.SOF_REQUIRING_EDD)), 0.05, 0.99
    )
    source_of_funds_verified = (rng.random(n) < sof_verify_p).astype(int)

    kyc_document_completeness = np.clip(rng.beta(9.0 - 4.0 * _sigmoid(zk), 1.4, n), 0.0, 1.0).round(4)
    kyc_refresh_overdue_days = np.clip(
        (rng.gamma(1.3, 55.0, n) * _sigmoid(0.9 * zk - 0.4) * 2.2) - 30, 0, 1500
    ).round().astype(int)

    ownership_transparency = _weighted_choice(
        rng,
        tx.OWNERSHIP_TRANSPARENCY,
        np.column_stack(
            [
                np.clip(80.0 - 45.0 * _sigmoid(zk), 5, None),
                25.0 + 10.0 * _sigmoid(zk),
                np.clip(4.0 + 34.0 * _sigmoid(zk) + 10.0 * (offshore_entity_links > 0), 1, None),
            ]
        ),
        n,
    )

    sar_filed_prior = (
        rng.random(n)
        < np.clip(
            _sigmoid(-4.6 + 1.30 * zk + 0.9 * high_risk_jurisdiction_exposure + 1.4 * beh["structuring_score"]), 0, 0.7
        )
    ).astype(int)

    # Layered/opaque ownership and a recent change of the ultimate beneficial
    # owner are both structurally not-applicable to a retail individual
    # customer, so both are held at a deterministic 0 there rather than drawn
    # — the same "structurally absent, not missing" discipline collateral_
    # coverage_ratio uses for an unsecured loan. For a business entity, this
    # reuses the offshore_entity_links > 0 boost that already tilts
    # ownership_transparency toward "opaque" above, so the two stay coherent.
    non_retail = np.isin(segment, ["sme", "corporate", "private_banking"])
    ownership_complexity_p = np.clip(_sigmoid(-2.0 + 1.4 * zk + 0.9 * (offshore_entity_links > 0)), 0.03, 0.85)
    complex_ownership_structure_flag = np.where(
        non_retail, (rng.random(n) < ownership_complexity_p).astype(int), 0
    )
    ubo_change_p = np.clip(_sigmoid(-2.6 + 1.1 * zk + 0.7 * complex_ownership_structure_flag), 0.02, 0.7)
    recent_ubo_change_flag = np.where(non_retail, (rng.random(n) < ubo_change_p).astype(int), 0)

    edd_required = (
        (pep_flag == 1)
        | (high_risk_jurisdiction_exposure == 1)
        | (sanctions_screen_hits > 0)
        | (np.isin(source_of_funds, list(tx.SOF_REQUIRING_EDD)) & (source_of_funds_verified == 0))
        | (offshore_entity_links >= 2)
    ).astype(int)

    return {
        "pep_flag": pep_flag,
        "pep_relationship": pep_relationship,
        "sanctions_screen_hits": sanctions_screen_hits,
        "adverse_media_hits_12m": adverse_media_hits_12m,
        "high_risk_jurisdiction_exposure": high_risk_jurisdiction_exposure,
        "medium_risk_jurisdiction_exposure": medium_risk_jurisdiction_exposure,
        "jurisdiction_risk_tier": tier,
        "offshore_entity_links": offshore_entity_links,
        "source_of_funds_declared": source_of_funds,
        "source_of_funds_verified": source_of_funds_verified,
        "kyc_document_completeness": kyc_document_completeness,
        "kyc_refresh_overdue_days": kyc_refresh_overdue_days,
        "beneficial_ownership_transparency": ownership_transparency,
        "sar_filed_prior": sar_filed_prior,
        "edd_required": edd_required,
        "complex_ownership_structure_flag": complex_ownership_structure_flag,
        "recent_ubo_change_flag": recent_ubo_change_flag,
    }


# --------------------------------------------------------------------------
# Block 6 — latent text signals
# --------------------------------------------------------------------------

#: Share of the text signal that is INDEPENDENT of the tabular block. This is the
#: headroom the LLM branch has to earn. Raise it to make the hybrid look better,
#: lower it to make the tabular baseline harder to beat. 0.60 is deliberately
#: conservative: most repayment stress does show up in the numbers eventually.
TEXT_INDEPENDENT_SHARE = 0.70

#: Quantile cut-offs mapping the continuous latent onto narrative levels 0..3.
#: Most interactions are routine; genuine distress is rare and that shape matters
#: when we later measure precision on the tail.
NARRATIVE_LEVEL_QUANTILES: tuple[float, float, float] = (0.55, 0.83, 0.95)


def _narrative_levels(values: np.ndarray) -> np.ndarray:
    """Quantile-bin a latent onto 0..3 so level populations stay stable run to run."""
    cuts = tuple(float(np.quantile(values, q)) for q in NARRATIVE_LEVEL_QUANTILES)
    return _bin_to_level(values, cuts)  # type: ignore[arg-type]


def _draw_text_latents(rng: np.random.Generator, n: int, z: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Distress / concealment as expressed in free text.

    Each is a blend of a factor the tabular block already reveals and an
    independent component that appears ONLY in the narratives.

    The *level* (0..3), not the continuous latent, is what drives both the
    narrative wording and the outcome model. That is deliberate: a reader of the
    note can recover roughly "how worried should I be, on a four-point scale",
    and nothing finer. Letting the outcome depend on a precision the text does not
    carry would build in a gap no LLM could ever close, and we would then blame
    the model for it.
    """
    share = TEXT_INDEPENDENT_SHARE
    latent_distress = (1 - share) * _sigmoid(z["z_liquidity_stress"]) + share * rng.beta(2.0, 5.0, n)
    latent_concealment = (1 - share) * _sigmoid(z["z_concealment"]) + share * rng.beta(2.0, 6.0, n)
    return {
        "latent_distress": np.round(latent_distress, 5),
        "latent_concealment_text": np.round(latent_concealment, 5),
        "distress_level": _narrative_levels(latent_distress),
        "concealment_level": _narrative_levels(latent_concealment),
    }


# --------------------------------------------------------------------------
# Block 7 — outcome model (the ground truth we will try to recover)
# --------------------------------------------------------------------------

# Every term below is z-scored before the coefficients are applied (see
# ``_standardise_terms``). That is what makes the coefficients a readable
# *variance budget*: a term with twice the coefficient carries roughly four times
# the weight, regardless of whether it was measured in days, shekels or a ratio.
# The first version of this file skipped that step and bureau_score silently ate
# 44% of the signal, which flattered logistic regression and buried the text.
#
# The budget is split roughly:  ~45% near-linear, ~22% non-linear, ~33% text.

#: Log-odds coefficients for the 12-month credit-default outcome.
BETA_CREDIT: dict[str, float] = {
    # --- near-linear drivers a scorecard would already capture ---------------
    "bureau_deficit": 0.38,
    "utilization_excess": 0.28,
    "dti_excess": 0.26,
    "delinquency_count": 0.30,
    "max_dpd": 0.20,
    "overdraft_frequency": 0.22,
    "income_volatility": 0.18,
    "credit_hunger": 0.16,
    "thin_file": 0.16,
    "no_savings_buffer": 0.24,
    "unverified_income": 0.14,
    "bounced_payments": 0.18,
    "prior_default": 0.26,
    "unemployed": 0.20,
    "gambling_exposure": 0.16,
    # --- shapes a linear model in the raw feature space cannot express -------
    # products, absolute differences and multi-way thresholds. These are the
    # reason the design calls for gradient-boosted trees rather than a scorecard.
    "x_utilization_volatility": 0.28,
    "x_dti_thin_file": 0.22,
    "x_maxed_and_delinquent": 0.26,
    "x_dti_threshold_breach": 0.24,
    "x_income_inconsistency": 0.48,
    "x_volatility_if_salaried": 0.40,
    "x_age_distance": 0.34,
    "x_seasoning_peak": 0.44,
    "x_leverage_to_income": 0.36,
    "x_leverage_squeeze": 0.28,
    # --- signal that lives only in the free text ----------------------------
    "text_distress": 1.85,
}

#: Log-odds coefficients for the 12-month confirmed-financial-crime outcome.
BETA_CRIME: dict[str, float] = {
    "pep": 0.26,
    "sanctions_hits": 0.34,
    "high_risk_jurisdiction": 0.28,
    "medium_risk_jurisdiction": 0.18,
    "cash_intensity": 0.24,
    "structuring": 0.32,
    "sof_requires_edd": 0.24,
    "sof_unverified": 0.20,
    "offshore_links": 0.22,
    "adverse_media": 0.24,
    "prior_sar": 0.26,
    "cross_border": 0.22,
    "crypto_exposure": 0.24,
    "opaque_ownership": 0.20,
    "kyc_incomplete": 0.18,
    # non-linear
    "x_cash_unexpected": 0.32,
    "x_structuring_cash": 0.28,
    "x_pep_offshore": 0.26,
    # --- tier-1 AML/KYC indicators (added post-launch) -----------------------
    "turnover_mismatch": 0.16,
    "fast_pass_through": 0.26,
    "volume_spike": 0.18,
    "vpn_or_proxy": 0.14,
    "device_churn": 0.12,
    "complex_ownership": 0.20,
    "ubo_change": 0.16,
    "cash_share": 0.10,
    "crypto_vasp": 0.16,
    # text-only
    "text_concealment": 1.70,
}

#: Weight of the credit vs. financial-crime dimension in the composite score, and
#: the power transform that spreads a low-base-rate probability across 0-100.
COMPOSITE_CREDIT_WEIGHT = 0.60
RISK_SCORE_EXPONENT = 0.45


def _standardise_terms(terms: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Z-score each term so the coefficients express relative importance."""
    out: dict[str, np.ndarray] = {}
    for name, values in terms.items():
        array = np.asarray(values, dtype=float)
        sd = float(np.std(array))
        out[name] = (array - float(np.mean(array))) / sd if sd > 1e-9 else np.zeros_like(array)
    return out


def _linear_combination(terms: dict[str, np.ndarray], betas: dict[str, float]) -> np.ndarray:
    """Dot product of standardised terms and coefficients, with an explicit key check."""
    missing = set(betas) - set(terms)
    if missing:
        raise KeyError(f"coefficients without a matching term: {sorted(missing)}")
    standardised = _standardise_terms({k: terms[k] for k in betas})
    total = np.zeros_like(next(iter(standardised.values())), dtype=float)
    for name, beta in betas.items():
        total = total + beta * standardised[name]
    return total


def _compose_logit(
    rng: np.random.Generator,
    terms: dict[str, np.ndarray],
    betas: dict[str, float],
    signal_strength: float,
    noise_sd: float,
    target_rate: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Build a calibrated log-odds vector with an explicit signal-to-noise ratio.

    The composite is standardised to unit variance, then scaled by
    ``signal_strength`` and mixed with N(0, ``noise_sd``) irreducible noise. That
    ratio — not the individual coefficients — is what sets the achievable AUC, so
    it is a single, tunable dial rather than an emergent accident.
    """
    raw = _linear_combination(terms, betas)
    sd = float(np.std(raw))
    signal = signal_strength * (raw - float(np.mean(raw))) / (sd if sd > 1e-9 else 1.0)
    linear = signal + rng.normal(0.0, noise_sd, len(raw))
    intercept = _calibrate_intercept(linear, target_rate)
    return signal, linear, intercept


def fin_account_age(prof: dict[str, np.ndarray]) -> np.ndarray:
    """Account age in months as a float array (kept as a helper for readability)."""
    return np.asarray(prof["account_age_months"], dtype=float)


def _credit_terms(
    fin: dict[str, np.ndarray],
    prof: dict[str, np.ndarray],
    beh: dict[str, np.ndarray],
    txt: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Drivers of credit default, before standardisation."""
    util = np.asarray(fin["credit_utilization_ratio"], dtype=float)
    dti = np.asarray(fin["dti_ratio"], dtype=float)
    vol = np.asarray(fin["income_volatility_cv"], dtype=float)
    savings = np.asarray(fin["savings_to_income_ratio"], dtype=float)
    delinq = np.asarray(fin["delinquencies_30d_12m"], dtype=float)

    utilization_excess = (util - 0.35) / 0.25
    dti_excess = (dti - 0.30) / 0.18
    income_volatility = (vol - 0.18) / 0.25
    thin_file = np.clip((18.0 - fin_account_age(prof)) / 18.0, 0.0, 1.0)

    # Declared income that the account balance does not corroborate — in EITHER
    # direction. Overstated income hides affordability problems; income far above
    # what the account shows means the money is going somewhere unobserved. An
    # absolute difference is a V-shape, which no linear model can represent.
    monthly_income = np.asarray(fin["declared_annual_income"], dtype=float) / 12.0
    observed = np.asarray(fin["avg_monthly_balance"], dtype=float)
    income_inconsistency = np.abs(np.log1p(monthly_income) - np.log1p(observed) - 0.55)

    return {
        "bureau_deficit": (700.0 - np.asarray(fin["bureau_score"], dtype=float)) / 60.0,
        "utilization_excess": utilization_excess,
        "dti_excess": dti_excess,
        "delinquency_count": np.minimum(delinq, 6.0),
        "max_dpd": np.asarray(fin["max_days_past_due_24m"], dtype=float),
        "overdraft_frequency": np.minimum(fin["overdraft_events_12m"], 12).astype(float),
        "income_volatility": income_volatility,
        "credit_hunger": np.minimum(fin["num_credit_inquiries_12m"], 10).astype(float),
        "thin_file": thin_file,
        "no_savings_buffer": -np.minimum(savings, 1.0),
        "unverified_income": 1.0 - np.asarray(fin["verified_income_ratio"], dtype=float),
        "bounced_payments": np.minimum(fin["num_bounced_payments_12m"], 6).astype(float),
        "prior_default": np.asarray(fin["prior_default_flag"], dtype=float),
        "unemployed": (prof["employment_status"] == "unemployed").astype(float),
        "gambling_exposure": np.minimum(np.asarray(beh["gambling_spend_ratio_90d"], dtype=float) * 12.0, 3.0),
        # --- non-linear -----------------------------------------------------
        "x_utilization_volatility": np.clip(utilization_excess, 0, None) * np.clip(income_volatility, 0, None),
        "x_dti_thin_file": np.clip(dti_excess, 0, None) * thin_file,
        "x_maxed_and_delinquent": ((util > 0.90) & (delinq >= 2)).astype(float),
        "x_dti_threshold_breach": (dti > 0.43).astype(float),
        "x_income_inconsistency": income_inconsistency,
        "x_leverage_squeeze": ((util > 0.75) & (savings < 0.06) & (vol > 0.25)).astype(float),
        # Income volatility means something different depending on how you are paid.
        # For a salaried employee it signals trouble; for the self-employed it is
        # simply how the job works. A linear model can shift the intercept per
        # employment type but cannot change the SLOPE, so it cannot learn this.
        "x_volatility_if_salaried": income_volatility * (prof["employment_status"] == "salaried").astype(float),
        # Risk is elevated at both ends of the age range, lowest in middle age.
        "x_age_distance": np.abs(np.asarray(prof["age"], dtype=float) - 45.0),
        # The seasoning curve: default hazard peaks roughly 12-18 months into a
        # relationship, then falls as the good payers prove themselves. It is a
        # bump, not a slope, so a linear term on account age gets it backwards at
        # one end or the other whichever sign it picks.
        "x_seasoning_peak": np.exp(-(((fin_account_age(prof) - 14.0) / 12.0) ** 2)),
        # Leverage taken relative to income, amplified by how much of it is drawn.
        "x_leverage_to_income": np.clip(utilization_excess, 0, None)
        * np.clip(
            np.asarray(fin["total_credit_limit"], dtype=float)
            / np.maximum(np.asarray(fin["declared_annual_income"], dtype=float), 1.0),
            0.0,
            3.0,
        ),
        # --- text-only ------------------------------------------------------
        "text_distress": np.asarray(txt["distress_level"], dtype=float),
    }


def _crime_terms(
    comp: dict[str, np.ndarray],
    beh: dict[str, np.ndarray],
    prof: dict[str, np.ndarray],
    txt: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Drivers of a confirmed financial-crime outcome, before standardisation."""
    cash = np.asarray(beh["cash_intensity_ratio"], dtype=float)
    structuring = np.asarray(beh["structuring_score"], dtype=float)
    pep = np.asarray(comp["pep_flag"], dtype=float)
    offshore = np.asarray(comp["offshore_entity_links"], dtype=float)

    # Cash use only means something relative to the occupation. A restaurant taking
    # 60% cash is ordinary; a software engineer doing the same is not. The baseline
    # is not a model input, so this comparison is invisible to a linear model.
    cash_unexpected = np.clip(cash - np.asarray(prof["_occ_cash_intensity"], dtype=float), 0.0, None)

    return {
        "pep": pep,
        "sanctions_hits": np.minimum(comp["sanctions_screen_hits"], 3).astype(float),
        "high_risk_jurisdiction": np.asarray(comp["high_risk_jurisdiction_exposure"], dtype=float),
        "medium_risk_jurisdiction": np.asarray(comp["medium_risk_jurisdiction_exposure"], dtype=float),
        "cash_intensity": cash,
        "structuring": structuring,
        "sof_requires_edd": np.isin(comp["source_of_funds_declared"], list(tx.SOF_REQUIRING_EDD)).astype(float),
        "sof_unverified": 1.0 - np.asarray(comp["source_of_funds_verified"], dtype=float),
        "offshore_links": np.minimum(offshore, 4.0),
        "adverse_media": np.minimum(comp["adverse_media_hits_12m"], 3).astype(float),
        "prior_sar": np.asarray(comp["sar_filed_prior"], dtype=float),
        "cross_border": np.asarray(beh["cross_border_txn_ratio"], dtype=float),
        "crypto_exposure": np.asarray(beh["crypto_exposure_ratio_90d"], dtype=float),
        "opaque_ownership": (comp["beneficial_ownership_transparency"] == "opaque").astype(float),
        "kyc_incomplete": 1.0 - np.asarray(comp["kyc_document_completeness"], dtype=float),
        # --- non-linear -----------------------------------------------------
        "x_cash_unexpected": cash_unexpected,
        "x_structuring_cash": structuring * cash,
        "x_pep_offshore": pep * (offshore >= 1).astype(float),
        # --- tier-1 AML/KYC indicators (added post-launch) -------------------
        # V-shaped: deviation from the expected turnover in EITHER direction,
        # same "abs() of a mismatch" shape as _credit_terms' income_inconsistency.
        "turnover_mismatch": np.abs(np.asarray(beh["expected_vs_actual_turnover_ratio"], dtype=float) - 1.0),
        # Sign flipped relative to the raw field on purpose: LOW hours (fast
        # pass-through) is the risk, so this term is large when hours are low.
        "fast_pass_through": np.clip(
            240.0 - np.asarray(beh["pass_through_velocity_hours"], dtype=float), -240.0, 240.0
        ),
        # Only the spike side counts as risk — a quiet 6 months is not itself
        # a red flag, so this is clipped at 0 rather than signed both ways.
        "volume_spike": np.clip(np.asarray(beh["volume_spike_ratio_6m"], dtype=float) - 1.0, 0.0, None),
        "vpn_or_proxy": np.asarray(beh["vpn_or_high_risk_ip_flag"], dtype=float),
        "device_churn": np.minimum(np.asarray(beh["device_change_frequency_30d"], dtype=float), 10.0),
        "complex_ownership": np.asarray(comp["complex_ownership_structure_flag"], dtype=float),
        "ubo_change": np.asarray(comp["recent_ubo_change_flag"], dtype=float),
        "cash_share": np.asarray(beh["cash_to_total_volume_ratio"], dtype=float),
        "crypto_vasp": np.asarray(beh["crypto_vasp_exposure_flag"], dtype=float),
        # --- text-only ------------------------------------------------------
        "text_concealment": np.asarray(txt["concealment_level"], dtype=float),
    }


def _draw_outcomes(
    rng: np.random.Generator,
    cfg: GeneratorConfig,
    n: int,
    prof: dict[str, np.ndarray],
    fin: dict[str, np.ndarray],
    beh: dict[str, np.ndarray],
    comp: dict[str, np.ndarray],
    txt: dict[str, np.ndarray],
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Sample the two outcome labels and the composite ground-truth risk score."""
    _, credit_linear, credit_b0 = _compose_logit(
        rng,
        _credit_terms(fin, prof, beh, txt),
        BETA_CREDIT,
        cfg.credit_signal_strength,
        cfg.credit_noise_sd,
        cfg.target_default_rate,
    )
    p_default = _sigmoid(credit_b0 + credit_linear)

    _, crime_linear, crime_b0 = _compose_logit(
        rng,
        _crime_terms(comp, beh, prof, txt),
        BETA_CRIME,
        cfg.crime_signal_strength,
        cfg.crime_noise_sd,
        cfg.target_financial_crime_rate,
    )
    p_crime = _sigmoid(crime_b0 + crime_linear)

    default_12m = (rng.random(n) < p_default).astype(int)
    financial_crime_12m = (rng.random(n) < p_crime).astype(int)

    # Composite: probability of ANY adverse outcome, weighted toward credit, then
    # spread over 0-100 by a power transform. Monotone, so the band ordering is safe.
    p_blend = 1.0 - (1.0 - p_default) ** (2.0 * COMPOSITE_CREDIT_WEIGHT) * (1.0 - p_crime) ** (
        2.0 * (1.0 - COMPOSITE_CREDIT_WEIGHT)
    )
    true_risk_score = np.clip(100.0 * np.power(np.clip(p_blend, 1e-9, 1.0), RISK_SCORE_EXPONENT), 0.0, 100.0).round(2)
    true_risk_band = score_to_band(true_risk_score)

    days_past_due = np.where(
        default_12m == 1, np.clip(rng.gamma(3.0, 34.0, n) + 90, 90, 720).round().astype(int), 0
    )

    outcomes = {
        "default_12m": default_12m,
        "financial_crime_12m": financial_crime_12m,
        "days_past_due_at_outcome": days_past_due,
        "p_default_true": p_default.round(6),
        "p_financial_crime_true": p_crime.round(6),
        "true_risk_score": true_risk_score,
        "true_risk_band": true_risk_band,
    }
    meta = {
        "credit_intercept": round(float(credit_b0), 6),
        "crime_intercept": round(float(crime_b0), 6),
        "credit_signal_to_noise": round(cfg.credit_signal_strength / cfg.credit_noise_sd, 4),
        "crime_signal_to_noise": round(cfg.crime_signal_strength / cfg.crime_noise_sd, 4),
        "realised_default_rate": round(float(default_12m.mean()), 6),
        "realised_financial_crime_rate": round(float(financial_crime_12m.mean()), 6),
        "mean_p_default": round(float(p_default.mean()), 6),
        "mean_p_financial_crime": round(float(p_crime.mean()), 6),
    }
    return outcomes, meta


def score_to_band(score: np.ndarray, thresholds: dict[str, float] | None = None) -> np.ndarray:
    """Map a 0-100 risk score onto Low / Medium / High / Extreme."""
    th = thresholds or tx.DEFAULT_BAND_THRESHOLDS
    cuts = np.array([th["Low"], th["Medium"], th["High"]], dtype=float)
    return np.asarray(tx.RISK_BANDS, dtype=object)[np.digitize(np.asarray(score, dtype=float), cuts)]


def _draw_underwriter_decisions(
    rng: np.random.Generator,
    n: int,
    prof: dict[str, np.ndarray],
    comp: dict[str, np.ndarray],
    out: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Simulate the human decision made at snapshot time.

    Humans are modelled as *biased* estimators of the true risk: lenient toward
    private-banking relationships, harsh on high-risk jurisdictions, and noisy.
    That bias is the thing the self-correction feedback loop is supposed to find,
    so it has to be present in the training data on purpose.
    """
    perceived = (
        np.asarray(out["true_risk_score"], dtype=float)
        - 9.0 * (prof["segment"] == "private_banking")
        - 4.0 * (prof["segment"] == "corporate")
        + 11.0 * np.asarray(comp["high_risk_jurisdiction_exposure"], dtype=float)
        + 6.0 * np.asarray(comp["pep_flag"], dtype=float)
        + rng.normal(0.0, 9.0, n)
    )
    decision = np.where(
        perceived < 28,
        "approve",
        np.where(perceived < 48, "approve_with_conditions", np.where(perceived < 70, "refer", "decline")),
    )
    confidence = np.clip(rng.beta(6.0, 2.4, n) * 0.6 + 0.4, 0.0, 1.0).round(3)
    # An override is recorded when the human materially disagreed with the model view.
    override_flag = (np.abs(perceived - np.asarray(out["true_risk_score"], dtype=float)) > 14).astype(int)
    return {
        "underwriter_decision": decision,
        "underwriter_confidence": confidence,
        "underwriter_override_flag": override_flag,
        "underwriter_perceived_score": perceived.round(2),
    }


# --------------------------------------------------------------------------
# Block 8 — synthetic PII (exists so the anonymisation layer has a target)
# --------------------------------------------------------------------------

_GIVEN_NAMES = (
    "Noa", "Yael", "Tamar", "Maya", "Shira", "Adi", "Roni", "Lior", "Dana", "Efrat",
    "Itai", "Omer", "Yonatan", "Eitan", "Amit", "Guy", "Nadav", "Tomer", "Erez", "Doron",
    "Anna", "Marco", "Sofia", "Lucas", "Elena", "Viktor", "Amara", "Ravi", "Chen", "Leila",
)
_FAMILY_NAMES = (
    "Avrahami", "Bar-On", "Cohen", "Dagan", "Eliav", "Friedman", "Gilad", "Harari", "Ivri",
    "Katz", "Levi", "Mizrahi", "Nahmias", "Ophir", "Peretz", "Regev", "Shapira", "Tal",
    "Vardi", "Weiss", "Zohar", "Almeida", "Bianchi", "Novak", "Okafor", "Petrov", "Sharma",
)
_STREETS = ("Hazait", "Rotem", "Alon", "Dekel", "Tamar", "Brosh", "Erez", "Kalanit", "Narkis", "Savion")


def _draw_pii(rng: np.random.Generator, n: int, country: np.ndarray) -> dict[str, np.ndarray]:
    """Obviously-fake identity fields.

    Deliberate choices so nothing here can collide with a real identifier:
      * national IDs carry a ``SYN-`` prefix and no valid checksum
      * e-mail uses the reserved ``.invalid`` TLD, which can never resolve
      * phone numbers use the ``+1-555-01xx`` range reserved for fiction
    """
    given = np.asarray(_GIVEN_NAMES, dtype=object)[rng.integers(0, len(_GIVEN_NAMES), n)]
    family = np.asarray(_FAMILY_NAMES, dtype=object)[rng.integers(0, len(_FAMILY_NAMES), n)]
    full_name = np.array([f"{g} {f}" for g, f in zip(given, family, strict=True)], dtype=object)
    serial = rng.integers(100_000_000, 999_999_999, n)
    national_id = np.array([f"SYN-{v}" for v in serial], dtype=object)
    email = np.array(
        [f"{g.lower()}.{f.lower().replace('-', '')}{i % 997}@example.invalid" for i, (g, f) in enumerate(zip(given, family, strict=True))],
        dtype=object,
    )
    phone = np.array([f"+1-555-01{v:02d}" for v in rng.integers(0, 100, n)], dtype=object)
    street_no = rng.integers(1, 180, n)
    street = np.asarray(_STREETS, dtype=object)[rng.integers(0, len(_STREETS), n)]
    address = np.array(
        [f"{no} {st} St, {tx.JURISDICTION_NAME.get(c, c)}" for no, st, c in zip(street_no, street, country, strict=True)], dtype=object
    )
    return {
        "full_name": full_name,
        "national_id": national_id,
        "email": email,
        "phone": phone,
        "address_line": address,
    }


# --------------------------------------------------------------------------
# Block 9 — event stream (feeds the real-time re-scoring engine)
# --------------------------------------------------------------------------


def _draw_events(
    rng: np.random.Generator,
    cfg: GeneratorConfig,
    customer_id: np.ndarray,
    snapshot_date: np.ndarray,
    prof: dict[str, np.ndarray],
    fin: dict[str, np.ndarray],
    beh: dict[str, np.ndarray],
) -> pd.DataFrame:
    """A per-customer transaction/event log over the trailing window.

    Two jobs: (a) let phase 6 replay a stream against the re-scoring engine,
    (b) give phase 2 a source for aggregate features computed from raw events
    rather than handed over pre-aggregated.
    """
    n = len(customer_id)
    intensity = cfg.events_per_customer * np.clip(np.asarray(beh["txn_count_90d"], dtype=float) / 50.0, 0.35, 3.0)
    counts = rng.poisson(np.clip(intensity, 0.5, 200)).astype(int)
    total = int(counts.sum())
    if total == 0:
        return pd.DataFrame(
            columns=[
                "event_id", "customer_id", "event_ts", "event_type",
                "amount", "counterparty_country", "channel", "is_trigger_event",
            ]
        )

    idx = np.repeat(np.arange(n), counts)

    # Event-type weights, per event, driven by the owning customer's profile.
    cash = np.asarray(beh["cash_intensity_ratio"], dtype=float)[idx]
    crypto = np.asarray(beh["crypto_exposure_ratio_90d"], dtype=float)[idx]
    xborder = np.asarray(beh["cross_border_txn_ratio"], dtype=float)[idx]
    delinq = np.asarray(fin["delinquencies_30d_12m"], dtype=float)[idx]
    od = np.asarray(fin["overdraft_events_12m"], dtype=float)[idx]
    has_loan = (np.asarray(fin["num_open_loans"], dtype=float)[idx] > 0).astype(float)
    salaried = (prof["employment_status"][idx] == "salaried").astype(float)

    weights = np.column_stack(
        [
            34.0 * np.ones(total),            # card_purchase
            6.0 + 18.0 * cash,                # atm_withdrawal
            1.0 + 22.0 * cash,                # cash_deposit
            2.0 + 9.0 * salaried,             # salary_credit
            1.5 + 14.0 * xborder,             # wire_transfer_in
            1.5 + 16.0 * xborder,             # wire_transfer_out
            7.0 * np.ones(total),             # direct_debit
            1.0 + 8.0 * has_loan,             # loan_repayment
            0.15 + 1.6 * delinq,              # missed_payment
            0.15 + 1.1 * od,                  # overdraft_breach
            0.2 + 40.0 * crypto,              # crypto_transfer
            0.25 * np.ones(total),            # chargeback
        ]
    )
    event_type = _weighted_choice(rng, tx.EVENT_TYPES, weights, total)

    base_amount = np.asarray(beh["avg_txn_amount"], dtype=float)[idx]
    scale = np.where(
        np.isin(event_type, ["wire_transfer_in", "wire_transfer_out", "cash_deposit"]),
        rng.lognormal(1.1, 0.9, total),
        np.where(event_type == "salary_credit", rng.lognormal(1.6, 0.35, total), rng.lognormal(0.0, 0.7, total)),
    )
    amount = np.round(np.clip(base_amount * scale, 1.0, None), 2)
    signed = np.isin(
        event_type,
        ["card_purchase", "atm_withdrawal", "wire_transfer_out", "direct_debit", "loan_repayment", "crypto_transfer"],
    )
    amount = np.where(signed, -amount, amount)

    # Timestamps: uniform over the trailing window, relative to each customer's snapshot.
    snap_days = np.asarray(snapshot_date, dtype="datetime64[D]").astype("int64")[idx]
    offset = rng.integers(0, cfg.event_window_days, total)
    event_days = snap_days - offset
    seconds = rng.integers(0, 86_400, total)
    event_ts = (event_days.astype("datetime64[D]").astype("datetime64[s]").astype("int64") + seconds).astype("datetime64[s]")

    country_pool = np.asarray([j.code for j in tx.JURISDICTIONS], dtype=object)
    domestic = prof["country_of_residence"][idx]
    is_foreign = rng.random(total) < np.clip(xborder, 0, 1)
    counterparty_country = np.where(is_foreign, country_pool[rng.integers(0, len(country_pool), total)], domestic)

    channel = _weighted_choice(
        rng, ("card", "atm", "branch", "online", "swift"), np.tile(np.array([34.0, 12.0, 8.0, 38.0, 8.0]), (total, 1)), total
    )

    is_trigger = (np.isin(event_type, list(tx.TRIGGER_EVENT_TYPES)) & (offset <= 30)).astype(int)

    frame = pd.DataFrame(
        {
            "customer_id": np.asarray(customer_id, dtype=object)[idx],
            "event_ts": event_ts,
            "event_type": event_type,
            "amount": amount,
            "counterparty_country": counterparty_country,
            "channel": channel,
            "is_trigger_event": is_trigger,
        }
    ).sort_values(["customer_id", "event_ts"], kind="stable", ignore_index=True)
    frame.insert(0, "event_id", [f"EVT-{i:09d}" for i in range(len(frame))])
    return frame


# --------------------------------------------------------------------------
# Block 10 — data-quality degradation
# --------------------------------------------------------------------------

#: Missingness plan. Each entry is (column, mechanism, predicate -> probability).
#: MCAR = missing completely at random, MAR = depends on observed data,
#: MNAR = depends on the unobserved value itself. Phase 2's imputation strategy
#: has to survive all three, so all three are represented.
MISSINGNESS_PLAN: tuple[tuple[str, str, str], ...] = (
    ("education_level", "MCAR", "flat 7%"),
    ("years_at_employer", "MCAR", "flat 5%"),
    ("income_volatility_cv", "MCAR", "flat 6%"),
    ("verified_income_ratio", "MAR", "30% if self-employed / business owner, else 5%"),
    ("bureau_score", "MAR", "45% if account_age_months < 12 (thin file), else 2%"),
    ("source_of_funds_verified", "MNAR", "60% when declared source is 'undeclared'"),
    ("collateral_coverage_ratio", "STRUCTURAL", "absent when the customer holds no secured loan"),
    ("loan_to_value", "STRUCTURAL", "absent when the customer holds no secured loan"),
)

#: Free-text variants a real CRM produces for the same categorical value.
_CATEGORICAL_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "employment_status": {
        "salaried": ("Salaried", "SALARIED", "salaried "),
        "self_employed": ("Self-Employed", "self employed", "SELF_EMPLOYED"),
        "business_owner": ("Business Owner", "business-owner"),
        "unemployed": ("Unemployed", "UNEMPLOYED"),
        "retired": ("Retired", "pensioner"),
        "student": ("Student", "STUDENT"),
    },
    "education_level": {
        "bachelor": ("Bachelor", "BA", "bachelors"),
        "master": ("Master", "MA", "masters"),
        "doctorate": ("PhD", "Doctorate"),
        "high_school": ("High School", "highschool"),
        "vocational": ("Vocational", "VOCATIONAL"),
    },
    "marital_status": {
        "single": ("Single", "SINGLE"),
        "married": ("Married", "MARRIED"),
        "divorced": ("Divorced",),
        "widowed": ("Widowed",),
    },
}


def _inject_missingness(rng: np.random.Generator, df: pd.DataFrame) -> pd.DataFrame:
    """Blank out values according to :data:`MISSINGNESS_PLAN`."""
    n = len(df)
    self_emp = df["employment_status"].isin(["self_employed", "business_owner"]).to_numpy()
    thin_file = (df["account_age_months"] < 12).to_numpy()
    undeclared = (df["source_of_funds_declared"] == "undeclared").to_numpy()

    plan: dict[str, np.ndarray] = {
        "education_level": np.full(n, 0.07),
        "years_at_employer": np.full(n, 0.05),
        "income_volatility_cv": np.full(n, 0.06),
        "verified_income_ratio": np.where(self_emp, 0.30, 0.05),
        "bureau_score": np.where(thin_file, 0.45, 0.02),
        "source_of_funds_verified": np.where(undeclared, 0.60, 0.0),
    }
    for column, probability in plan.items():
        if column not in df.columns:
            continue
        mask = rng.random(n) < probability
        if not mask.any():
            continue
        if pd.api.types.is_integer_dtype(df[column]):
            df[column] = df[column].astype("Int64")
        elif pd.api.types.is_float_dtype(df[column]):
            df[column] = df[column].astype("Float64")
        df.loc[mask, column] = pd.NA
    return df


def _inject_categorical_noise(rng: np.random.Generator, df: pd.DataFrame, rate: float) -> pd.DataFrame:
    """Replace a small share of clean categorical values with real-world spelling variants."""
    for column, aliases in _CATEGORICAL_ALIASES.items():
        if column not in df.columns:
            continue
        values = df[column].astype(object).to_numpy(copy=True)
        hit = (rng.random(len(values)) < rate) & pd.notna(values)
        for i in np.flatnonzero(hit):
            options = aliases.get(str(values[i]))
            if options:
                values[i] = options[int(rng.integers(len(options)))]
        df[column] = values
    return df


def _append_near_duplicates(
    rng: np.random.Generator, customers: pd.DataFrame, rate: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Clone a few customers with perturbed attributes, for entity-resolution testing.

    The link back to the original is recorded in the ground-truth frame only — the
    customer frame must look exactly like a messy production extract.
    """
    n_dupes = int(round(len(customers) * rate))
    if n_dupes <= 0:
        return customers, pd.DataFrame(columns=["customer_id", "duplicate_of_customer_id"])

    source_pos = rng.choice(len(customers), size=n_dupes, replace=False)
    dupes = customers.iloc[source_pos].copy().reset_index(drop=True)
    original_ids = dupes["customer_id"].to_numpy(copy=True)
    dupes["customer_id"] = [f"CUS-D{i:07d}" for i in range(n_dupes)]

    if "full_name" in dupes.columns:
        dupes["full_name"] = [
            name.replace(" ", "  ") if rng.random() < 0.5 else name.upper() for name in dupes["full_name"].astype(str)
        ]
    if "age" in dupes.columns:
        dupes["age"] = (dupes["age"].astype(int) + rng.integers(-1, 2, n_dupes)).clip(18, 92)
    if "declared_annual_income" in dupes.columns:
        dupes["declared_annual_income"] = (
            dupes["declared_annual_income"].astype(float) * rng.normal(1.0, 0.02, n_dupes)
        ).round(-2)

    link = pd.DataFrame({"customer_id": dupes["customer_id"].to_numpy(), "duplicate_of_customer_id": original_ids})
    combined = pd.concat([customers, dupes], ignore_index=True)
    return combined, link


# --------------------------------------------------------------------------
# Block 11 — snapshot dates and out-of-time splits
# --------------------------------------------------------------------------

#: Out-of-time split: the most recent cohorts are held out. Random row-wise splits
#: would leak the macro cycle across folds and flatter the model; credit risk is
#: validated out-of-time or not at all.
SPLIT_FRACTIONS: dict[str, float] = {"train": 0.67, "validation": 0.165, "test": 0.165}


def _add_months(anchor: dt.date, months: int) -> dt.date:
    total = anchor.year * 12 + (anchor.month - 1) + months
    year, month = divmod(total, 12)
    day = min(anchor.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                           31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month])
    return dt.date(year, month + 1, day)


def _assign_cohorts(rng: np.random.Generator, cfg: GeneratorConfig, n: int) -> dict[str, np.ndarray]:
    """Spread customers over monthly cohorts and label the out-of-time split."""
    cohort = rng.integers(0, cfg.cohort_months, n)
    snapshot = np.array(
        [_add_months(cfg.as_of, -(cfg.cohort_months - 1 - int(c))) for c in cohort], dtype="datetime64[D]"
    )
    outcome_date = np.array(
        [_add_months(d.astype(dt.date), cfg.outcome_horizon_months) for d in snapshot], dtype="datetime64[D]"
    )

    n_train = max(1, int(round(cfg.cohort_months * SPLIT_FRACTIONS["train"])))
    n_val = max(1, int(round(cfg.cohort_months * SPLIT_FRACTIONS["validation"])))
    if n_train + n_val >= cfg.cohort_months:  # tiny cohort counts
        n_train, n_val = max(1, cfg.cohort_months - 2), 1
    split = np.where(cohort < n_train, "train", np.where(cohort < n_train + n_val, "validation", "test"))
    return {
        "cohort_month": cohort,
        "snapshot_date": snapshot,
        "outcome_observation_date": outcome_date,
        "split": split,
    }


# --------------------------------------------------------------------------
# Block 12 — orchestration
# --------------------------------------------------------------------------


def generate(cfg: GeneratorConfig | None = None) -> SyntheticDataset:
    """Generate one complete synthetic dataset.

    Returns five frames that share ``customer_id``:

    ``customers``    model inputs, exactly what an API caller could plausibly send
    ``narratives``   the three free-text fields, one row per customer
    ``events``       trailing transaction/event log for real-time re-scoring
    ``outcomes``     labels observed after the performance window + human decision
    ``ground_truth`` generator internals — evaluation only, never a model input
    """
    cfg = cfg or GeneratorConfig()
    cfg.validate()
    rng = np.random.default_rng(cfg.seed)
    n = cfg.n_customers
    logger.info("generating %d synthetic customers (seed=%d)", n, cfg.seed)

    z = _draw_latents(rng, n)
    prof = _draw_profile(rng, cfg, n, z)
    fin = _draw_financial(rng, n, z, prof)
    beh = _draw_behavioural(rng, n, z, prof, fin)
    comp = _draw_compliance(rng, n, z, prof, beh)
    txt = _draw_text_latents(rng, n, z)
    outcomes, outcome_meta = _draw_outcomes(rng, cfg, n, prof, fin, beh, comp, txt)
    decisions = _draw_underwriter_decisions(rng, n, prof, comp, outcomes)
    cohorts = _assign_cohorts(rng, cfg, n)

    customer_id = np.array([f"CUS-{i:07d}" for i in range(n)], dtype=object)

    # ---- customers frame -------------------------------------------------
    public_profile = {k: v for k, v in prof.items() if not k.startswith("_")}
    customers = pd.DataFrame(
        {
            "customer_id": customer_id,
            "snapshot_date": cohorts["snapshot_date"],
            "split": cohorts["split"],
            **public_profile,
            **fin,
            **beh,
            **comp,
        }
    )
    if cfg.include_pii:
        pii = _draw_pii(rng, n, prof["country_of_residence"])
        for position, (name, values) in enumerate(pii.items(), start=3):
            customers.insert(position, name, values)

    # ---- narratives frame ------------------------------------------------
    narratives = _build_narratives(rng, cfg, customer_id, cohorts["snapshot_date"], prof, comp, txt)

    # ---- events frame ----------------------------------------------------
    events = (
        _draw_events(rng, cfg, customer_id, cohorts["snapshot_date"], prof, fin, beh)
        if cfg.generate_events
        else pd.DataFrame()
    )
    events = _inject_trigger_events(rng, cfg, events, customer_id, cohorts["snapshot_date"], beh)

    # ---- outcomes frame --------------------------------------------------
    outcomes_frame = pd.DataFrame(
        {
            "customer_id": customer_id,
            "snapshot_date": cohorts["snapshot_date"],
            "outcome_observation_date": cohorts["outcome_observation_date"],
            "split": cohorts["split"],
            "default_12m": outcomes["default_12m"],
            "financial_crime_12m": outcomes["financial_crime_12m"],
            "days_past_due_at_outcome": outcomes["days_past_due_at_outcome"],
            **decisions,
        }
    )

    # ---- ground-truth frame (evaluation only) ----------------------------
    ground_truth = pd.DataFrame(
        {
            "customer_id": customer_id,
            **{k: np.round(v, 5) for k, v in z.items()},
            "latent_distress": txt["latent_distress"],
            "latent_concealment_text": txt["latent_concealment_text"],
            "narrative_distress_level": txt["distress_level"],
            "narrative_concealment_level": txt["concealment_level"],
            "p_default_true": outcomes["p_default_true"],
            "p_financial_crime_true": outcomes["p_financial_crime_true"],
            "true_risk_score": outcomes["true_risk_score"],
            "true_risk_band": outcomes["true_risk_band"],
        }
    )

    # ---- realism degradation --------------------------------------------
    if cfg.inject_missingness:
        customers = _inject_missingness(rng, customers)
    if cfg.inject_categorical_noise:
        customers = _inject_categorical_noise(rng, customers, cfg.categorical_noise_rate)
    if cfg.duplicate_rate > 0:
        customers, duplicate_link = _append_near_duplicates(rng, customers, cfg.duplicate_rate)
        # A duplicated CRM record carries its own notes, outcome row and hidden state,
        # so the clone is propagated to every frame. Otherwise a join on customer_id
        # would quietly drop the duplicates and the entity-resolution task disappears.
        narratives = _clone_rows(narratives, duplicate_link)
        outcomes_frame = _clone_rows(outcomes_frame, duplicate_link)
        ground_truth = _clone_rows(ground_truth, duplicate_link)
        ground_truth = ground_truth.merge(duplicate_link, on="customer_id", how="left")
    else:
        ground_truth["duplicate_of_customer_id"] = pd.NA

    dataset = SyntheticDataset(
        customers=customers,
        narratives=narratives,
        events=events,
        outcomes=outcomes_frame,
        ground_truth=ground_truth,
        config=cfg,
    )
    dataset.manifest = build_manifest(dataset, outcome_meta)
    return dataset


def _clone_rows(frame: pd.DataFrame, link: pd.DataFrame) -> pd.DataFrame:
    """Append copies of the source rows under the duplicate customer ids."""
    if frame.empty or link.empty or "customer_id" not in frame.columns:
        return frame
    lookup = frame.set_index("customer_id")
    present = link[link["duplicate_of_customer_id"].isin(lookup.index)]
    if present.empty:
        return frame
    clones = lookup.loc[present["duplicate_of_customer_id"]].reset_index(drop=True)
    clones.insert(0, "customer_id", present["customer_id"].to_numpy())
    return pd.concat([frame, clones[frame.columns]], ignore_index=True)


def _build_narratives(
    rng: np.random.Generator,
    cfg: GeneratorConfig,
    customer_id: np.ndarray,
    snapshot_date: np.ndarray,
    prof: dict[str, np.ndarray],
    comp: dict[str, np.ndarray],
    txt: dict[str, np.ndarray],
) -> pd.DataFrame:
    """Render three free-text fields per customer from the latent text signals."""
    n = len(customer_id)
    # Levels come from _draw_text_latents, which is also what the outcome model
    # used. Recomputing them here would risk the text and the label disagreeing.
    distress_level = txt["distress_level"]
    concealment_level = txt["concealment_level"]

    if cfg.language == "mixed":
        languages = np.where(rng.random(n) < cfg.hebrew_share, "he", "en")
    else:
        languages = np.full(n, cfg.language, dtype=object)

    rows: list[dict[str, Any]] = []
    for i in range(n):
        ctx = nrt.NarrativeContext(
            customer_ref=str(customer_id[i]),
            language=str(languages[i]),
            segment=str(prof["segment"][i]),
            occupation=str(prof["occupation"][i]),
            employment_status=str(prof["employment_status"][i]),
            country_code=str(prof["country_of_residence"][i]),
            distress=int(distress_level[i]),
            concealment=int(concealment_level[i]),
            pep_flag=bool(comp["pep_flag"][i]),
            account_age_months=int(prof["account_age_months"][i]),
            as_of=snapshot_date[i].astype(dt.date),
            extras={
                "source_of_funds": str(comp["source_of_funds_declared"][i]),
                "sanctions_hits": int(comp["sanctions_screen_hits"][i]),
                "adverse_media_hits": int(comp["adverse_media_hits_12m"][i]),
            },
        )
        rows.append({"customer_id": customer_id[i], "language": ctx.language, **nrt.build_all(rng, ctx)})

    return pd.DataFrame(rows)


def _inject_trigger_events(
    rng: np.random.Generator,
    cfg: GeneratorConfig,
    events: pd.DataFrame,
    customer_id: np.ndarray,
    snapshot_date: np.ndarray,
    beh: dict[str, np.ndarray],
) -> pd.DataFrame:
    """Guarantee a population of fresh shock events for the re-scoring demo.

    Without this, trigger events appear only by chance and a re-scoring test would
    have too few positives to be meaningful.
    """
    if events.empty or cfg.trigger_event_rate <= 0:
        return events

    n_trigger = int(round(len(customer_id) * cfg.trigger_event_rate))
    if n_trigger <= 0:
        return events
    chosen = rng.choice(len(customer_id), size=n_trigger, replace=False)
    shock_types = np.asarray(
        ["missed_payment", "overdraft_breach", "cash_deposit", "wire_transfer_out", "chargeback"], dtype=object
    )
    kinds = shock_types[rng.integers(0, len(shock_types), n_trigger)]
    base = np.asarray(beh["avg_txn_amount"], dtype=float)[chosen]
    amount = np.round(np.clip(base * rng.lognormal(2.1, 0.8, n_trigger), 50.0, None), 2)
    amount = np.where(np.isin(kinds, ["wire_transfer_out", "chargeback"]), -amount, amount)

    snap = np.asarray(snapshot_date, dtype="datetime64[D]").astype("int64")[chosen]
    ts = (
        (snap - rng.integers(0, 30, n_trigger)).astype("datetime64[D]").astype("datetime64[s]").astype("int64")
        + rng.integers(0, 86_400, n_trigger)
    ).astype("datetime64[s]")

    extra = pd.DataFrame(
        {
            "event_id": [f"EVT-T{i:08d}" for i in range(n_trigger)],
            "customer_id": np.asarray(customer_id, dtype=object)[chosen],
            "event_ts": ts,
            "event_type": kinds,
            "amount": amount,
            "counterparty_country": np.asarray(["IL"] * n_trigger, dtype=object),
            "channel": np.asarray(["online"] * n_trigger, dtype=object),
            "is_trigger_event": np.ones(n_trigger, dtype=int),
        }
    )
    return pd.concat([events, extra], ignore_index=True).sort_values(
        ["customer_id", "event_ts"], kind="stable", ignore_index=True
    )


# --------------------------------------------------------------------------
# Block 13 — manifest, validation summary and writers
# --------------------------------------------------------------------------


def build_manifest(dataset: SyntheticDataset, outcome_meta: dict[str, Any]) -> dict[str, Any]:
    """Everything needed to reproduce and audit a run.

    Model risk management will ask "which data trained this model?". A dataset with
    no manifest cannot answer that, so the manifest is part of the artefact, not a
    nice-to-have.
    """
    cfg = dataset.config
    config_payload = cfg.to_dict()
    config_hash = hashlib.sha256(json.dumps(config_payload, sort_keys=True).encode()).hexdigest()[:16]

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "config": config_payload,
        "config_hash": config_hash,
        "library_versions": {"numpy": np.__version__, "pandas": pd.__version__},
        "row_counts": {
            "customers": int(len(dataset.customers)),
            "narratives": int(len(dataset.narratives)),
            "events": int(len(dataset.events)),
            "outcomes": int(len(dataset.outcomes)),
            "ground_truth": int(len(dataset.ground_truth)),
        },
        "outcome_model": {
            "beta_credit": BETA_CREDIT,
            "beta_crime": BETA_CRIME,
            "text_independent_share": TEXT_INDEPENDENT_SHARE,
            "composite_credit_weight": COMPOSITE_CREDIT_WEIGHT,
            "risk_score_exponent": RISK_SCORE_EXPONENT,
            **outcome_meta,
        },
        "summary": summarise(dataset),
    }


def summarise(dataset: SyntheticDataset) -> dict[str, Any]:
    """Distribution checks a reviewer would want before trusting the data."""
    customers, outcomes, truth = dataset.customers, dataset.outcomes, dataset.ground_truth
    merged = outcomes.merge(
        truth[["customer_id", "true_risk_score", "true_risk_band"]], on="customer_id", how="left"
    )

    by_split = (
        merged.groupby("split")
        .agg(
            n=("customer_id", "size"),
            default_rate=("default_12m", "mean"),
            financial_crime_rate=("financial_crime_12m", "mean"),
        )
        .round(5)
        .to_dict(orient="index")
    )

    # Decile lift: the single most useful "is there signal here?" check.
    ranked = merged.dropna(subset=["true_risk_score"]).sort_values("true_risk_score", kind="stable")
    deciles: list[dict[str, Any]] = []
    if len(ranked) >= 10:
        chunks = np.array_split(np.arange(len(ranked)), 10)
        for i, chunk in enumerate(chunks, start=1):
            block = ranked.iloc[chunk]
            deciles.append(
                {
                    "decile": i,
                    "n": int(len(block)),
                    "mean_true_risk_score": round(float(block["true_risk_score"].mean()), 2),
                    "default_rate": round(float(block["default_12m"].mean()), 5),
                    "financial_crime_rate": round(float(block["financial_crime_12m"].mean()), 5),
                }
            )

    missing_share = (customers.isna().mean() * 100).round(2)
    return {
        "by_split": by_split,
        "risk_band_distribution": truth["true_risk_band"].value_counts(normalize=True).round(4).to_dict(),
        "underwriter_decisions": outcomes["underwriter_decision"].value_counts(normalize=True).round(4).to_dict(),
        "risk_score_deciles": deciles,
        "missing_value_pct": {k: float(v) for k, v in missing_share[missing_share > 0].items()},
        "trigger_events": int(dataset.events["is_trigger_event"].sum()) if len(dataset.events) else 0,
        "narrative_language_mix": dataset.narratives["language"].value_counts(normalize=True).round(4).to_dict(),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_dataset(dataset: SyntheticDataset, out_dir: Path, formats: tuple[str, ...] = ("csv",)) -> dict[str, Any]:
    """Write every frame plus the manifest. Returns the manifest that was written."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    frames: dict[str, pd.DataFrame] = {
        "customers": dataset.customers,
        "narratives": dataset.narratives,
        "events": dataset.events,
        "outcomes": dataset.outcomes,
        "ground_truth": dataset.ground_truth,
    }

    written: dict[str, dict[str, Any]] = {}
    for name, frame in frames.items():
        if frame is None or frame.empty:
            continue
        for fmt in formats:
            if fmt == "csv":
                path = out_dir / f"{name}.csv"
                frame.to_csv(path, index=False)
            elif fmt == "parquet":
                path = out_dir / f"{name}.parquet"
                try:
                    frame.to_parquet(path, index=False)
                except (ImportError, ValueError) as exc:  # pragma: no cover - env dependent
                    logger.warning("skipping parquet for %s: %s", name, exc)
                    continue
            elif fmt == "jsonl":
                path = out_dir / f"{name}.jsonl"
                frame.to_json(path, orient="records", lines=True, force_ascii=False)
            else:
                raise ValueError(f"unsupported output format: {fmt!r}")
            written.setdefault(name, {})[fmt] = {
                "path": path.name,
                "rows": int(len(frame)),
                "bytes": int(path.stat().st_size),
                "sha256": _sha256(path),
            }

    manifest = dict(dataset.manifest)
    manifest["files"] = written
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    logger.info("wrote %d frames to %s", len(written), out_dir)
    return manifest


def format_report(manifest: dict[str, Any]) -> str:
    """Human-readable run report, printed by the CLI."""
    summary = manifest["summary"]
    lines: list[str] = []
    add = lines.append

    add("=" * 78)
    add("SYNTHETIC CUSTOMER RISK RATING DATASET")
    add("=" * 78)
    cfg = manifest["config"]
    add(f"  schema {manifest['schema_version']}   config-hash {manifest['config_hash']}   seed {cfg['seed']}")
    add(f"  snapshot anchor {cfg['as_of']}   cohorts {cfg['cohort_months']}m   outcome window {cfg['outcome_horizon_months']}m")
    add("")

    add("ROW COUNTS")
    for name, count in manifest["row_counts"].items():
        add(f"  {name:<14} {count:>10,}")
    add("")

    add("OUTCOME PREVALENCE BY OUT-OF-TIME SPLIT")
    add(f"  {'split':<12}{'n':>9}{'default':>12}{'fin-crime':>12}")
    for split in ("train", "validation", "test"):
        row = summary["by_split"].get(split)
        if row:
            add(f"  {split:<12}{int(row['n']):>9,}{row['default_rate']:>11.2%}{row['financial_crime_rate']:>12.2%}")
    add("")

    add("RISK BAND MIX (ground truth)")
    for band in tx.RISK_BANDS:
        share = summary["risk_band_distribution"].get(band, 0.0)
        add(f"  {band:<10}{share:>7.2%}  {'#' * int(share * 50)}")
    add("")

    add("SIGNAL CHECK — outcome rate by ground-truth risk decile")
    add(f"  {'decile':<8}{'score':>8}{'default':>11}{'fin-crime':>12}")
    for row in summary["risk_score_deciles"]:
        add(
            f"  {row['decile']:<8}{row['mean_true_risk_score']:>8.1f}"
            f"{row['default_rate']:>10.2%}{row['financial_crime_rate']:>12.2%}"
        )
    if summary["risk_score_deciles"]:
        lo = summary["risk_score_deciles"][0]["default_rate"]
        hi = summary["risk_score_deciles"][-1]["default_rate"]
        if lo > 0:
            add(f"  top-vs-bottom decile default lift: {hi / lo:,.1f}x")
        else:
            add(f"  bottom decile has zero defaults; top decile default rate {hi:.2%}")
    add("")

    if summary["missing_value_pct"]:
        add("MISSINGNESS (columns with gaps)")
        for column, pct in sorted(summary["missing_value_pct"].items(), key=lambda kv: -kv[1]):
            add(f"  {column:<34}{pct:>6.2f}%")
        add("")

    add(f"TRIGGER EVENTS (real-time re-score candidates): {summary['trigger_events']:,}")
    add(f"NARRATIVE LANGUAGE MIX: {summary['narrative_language_mix']}")
    add("")

    files = manifest.get("files", {})
    if files:
        add("FILES")
        for formats in files.values():
            for info in formats.values():
                add(
                    f"  {info['path']:<24}{info['rows']:>9,} rows"
                    f"{info['bytes'] / 1e6:>9.2f} MB  sha256:{info['sha256'][:12]}"
                )
    add("=" * 78)
    return "\n".join(lines)
