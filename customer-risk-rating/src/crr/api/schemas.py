"""Request and response schemas for the scoring API.

Design decision on missing fields
---------------------------------
Every feature field is **optional and defaults to null**, and a null flows through
to the model as a genuine missing value (NaN) — never as a fabricated zero. This is
deliberate and it is the opposite of the failure the brief warns about. A real CRM
integration rarely has all ~65 fields; zero-filling ``credit_utilization_ratio``
for a customer whose value we simply do not have would invent a perfect borrower
and score them confidently wrong. Instead the pipeline's missing-value machinery
(native NaN handling plus the ``*_is_missing`` indicators) treats the gap as the
information it is. Malformed data — wrong type, out-of-range ratio — is still
rejected at the boundary by the type and range constraints below.

Only ``customer_id`` and ``snapshot_date`` are required: without an identity and an
as-of date there is nothing to score or to audit.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# A permissive upper bound on ratio-like fields: utilisation and velocity can
# legitimately exceed 1, so the guard only catches clearly-broken values.
_RATIO_MAX = 10.0


class EventPayload(BaseModel):
    """One recent transaction/event, for the event-derived features."""

    model_config = ConfigDict(extra="forbid")

    event_ts: dt.datetime
    event_type: str
    amount: float
    counterparty_country: str = "IL"
    channel: str = "online"
    is_trigger_event: int = Field(default=0, ge=0, le=1)


class CustomerPayload(BaseModel):
    """A customer record as a CRM or core-banking system would send it.

    ``extra="forbid"`` so a misspelled field is a loud 422, not a silently ignored
    value that leaves the real field null.
    """

    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1, max_length=64)
    snapshot_date: dt.date

    # --- profile ----------------------------------------------------------
    segment: str | None = None
    occupation: str | None = None
    employment_status: str | None = None
    age: int | None = Field(default=None, ge=18, le=120)
    years_at_employer: float | None = Field(default=None, ge=0, le=80)
    account_age_months: int | None = Field(default=None, ge=0, le=1200)
    education_level: str | None = None
    marital_status: str | None = None
    dependents_count: int | None = Field(default=None, ge=0, le=30)
    preferred_channel: str | None = None
    residency_status: str | None = None
    country_of_residence: str | None = Field(default=None, max_length=3)
    num_products_held: int | None = Field(default=None, ge=0, le=100)

    # --- financial --------------------------------------------------------
    declared_annual_income: float | None = Field(default=None, ge=0)
    verified_income_ratio: float | None = Field(default=None, ge=0, le=2)
    income_volatility_cv: float | None = Field(default=None, ge=0, le=_RATIO_MAX)
    income_months_missing_12m: int | None = Field(default=None, ge=0, le=12)
    total_credit_limit: float | None = Field(default=None, ge=0)
    credit_utilization_ratio: float | None = Field(default=None, ge=0, le=_RATIO_MAX)
    revolving_balance: float | None = Field(default=None, ge=0)
    num_open_loans: int | None = Field(default=None, ge=0, le=100)
    dti_ratio: float | None = Field(default=None, ge=0, le=_RATIO_MAX)
    num_credit_inquiries_12m: int | None = Field(default=None, ge=0, le=100)
    delinquencies_30d_12m: int | None = Field(default=None, ge=0, le=100)
    delinquencies_60d_24m: int | None = Field(default=None, ge=0, le=100)
    delinquencies_90d_24m: int | None = Field(default=None, ge=0, le=100)
    max_days_past_due_24m: int | None = Field(default=None, ge=0, le=1000)
    months_since_last_delinquency: int | None = Field(default=None, ge=0, le=1200)
    prior_default_flag: int | None = Field(default=None, ge=0, le=1)
    num_bounced_payments_12m: int | None = Field(default=None, ge=0, le=100)
    bureau_score: int | None = Field(default=None, ge=300, le=850)
    payment_history_score: float | None = Field(default=None, ge=0, le=100)
    avg_monthly_balance: float | None = None
    min_monthly_balance: float | None = None
    balance_volatility: float | None = Field(default=None, ge=0, le=_RATIO_MAX)
    overdraft_events_12m: int | None = Field(default=None, ge=0, le=1000)
    overdraft_days_12m: int | None = Field(default=None, ge=0, le=366)
    savings_to_income_ratio: float | None = Field(default=None, ge=0)
    loan_to_value: float | None = Field(default=None, ge=0, le=_RATIO_MAX)
    collateral_coverage_ratio: float | None = Field(default=None, ge=0)

    # --- behavioural ------------------------------------------------------
    txn_count_90d: int | None = Field(default=None, ge=0)
    txn_volume_90d: float | None = None
    avg_txn_amount: float | None = None
    cash_intensity_ratio: float | None = Field(default=None, ge=0, le=1)
    large_cash_deposits_90d: int | None = Field(default=None, ge=0)
    cross_border_txn_count_90d: int | None = Field(default=None, ge=0)
    cross_border_txn_ratio: float | None = Field(default=None, ge=0, le=1)
    night_txn_ratio: float | None = Field(default=None, ge=0, le=1)
    new_counterparty_ratio_90d: float | None = Field(default=None, ge=0, le=1)
    merchant_category_entropy: float | None = Field(default=None, ge=0, le=10)
    txn_velocity_change_pct: float | None = None
    gambling_spend_ratio_90d: float | None = Field(default=None, ge=0, le=1)
    crypto_exposure_ratio_90d: float | None = Field(default=None, ge=0, le=1)
    structuring_score: float | None = Field(default=None, ge=0, le=1)

    # --- AML / KYC --------------------------------------------------------
    pep_flag: int | None = Field(default=None, ge=0, le=1)
    pep_relationship: str | None = None
    sanctions_screen_hits: int | None = Field(default=None, ge=0)
    adverse_media_hits_12m: int | None = Field(default=None, ge=0)
    high_risk_jurisdiction_exposure: int | None = Field(default=None, ge=0, le=1)
    medium_risk_jurisdiction_exposure: int | None = Field(default=None, ge=0, le=1)
    jurisdiction_risk_tier: str | None = None
    offshore_entity_links: int | None = Field(default=None, ge=0)
    source_of_funds_declared: str | None = None
    source_of_funds_verified: int | None = Field(default=None, ge=0, le=1)
    kyc_document_completeness: float | None = Field(default=None, ge=0, le=1)
    kyc_refresh_overdue_days: int | None = Field(default=None, ge=0)
    beneficial_ownership_transparency: str | None = None
    sar_filed_prior: int | None = Field(default=None, ge=0, le=1)
    edd_required: int | None = Field(default=None, ge=0, le=1)


class ScoreRequest(BaseModel):
    """A single-customer scoring request."""

    model_config = ConfigDict(extra="forbid")

    customer: CustomerPayload
    events: list[EventPayload] = Field(default_factory=list, max_length=10000)
    explain: bool = True
    audience: Literal["internal", "customer"] = "internal"


class ReasonFactorOut(BaseModel):
    code: str
    category: str
    statement: str
    dimension: Literal["credit", "financial_crime"]
    contribution: float
    direction: Literal["increases", "decreases"]


class DimensionResult(BaseModel):
    probability: float = Field(ge=0, le=1)
    band_probability_note: str | None = None


class ScoreResult(BaseModel):
    """The rating for one customer."""

    customer_id: str
    risk_score: float = Field(ge=0, le=100)
    risk_band: Literal["Low", "Medium", "High", "Extreme"]
    credit: DimensionResult
    financial_crime: DimensionResult
    top_factors: list[ReasonFactorOut]
    requires_review: bool = False
    model_version: str
    policy_version: int
    scored_at: dt.datetime
    latency_ms: float


class ScoreResponse(BaseModel):
    result: ScoreResult


class BatchScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customers: list[CustomerPayload] = Field(min_length=1, max_length=50000)
    explain: bool = False


class BatchJobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    total: int
    processed: int
    submitted_at: dt.datetime
    completed_at: dt.datetime | None = None
    error: str | None = None


class BatchJobResponse(BaseModel):
    job: BatchJobStatus


class ExplanationResponse(BaseModel):
    """The stored, detailed explanation for a previously scored customer."""

    customer_id: str
    risk_score: float
    risk_band: str
    credit_probability: float
    financial_crime_probability: float
    audience: Literal["internal", "customer"]
    top_factors: list[ReasonFactorOut]
    protective_factors: list[ReasonFactorOut]
    model_version: str
    policy_version: int
    scored_at: dt.datetime


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    models_loaded: list[str]
    policy_version: int
    version: str
