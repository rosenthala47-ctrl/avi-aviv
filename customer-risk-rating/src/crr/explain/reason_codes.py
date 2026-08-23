"""Reason codes: the policy-owned vocabulary that SHAP values are translated into.

Why this layer exists
---------------------
A regulator, an adverse-action notice, or a customer appeal needs a *reason*, not
a coefficient. "Your application scored high because of ``f_credit_utilization_ratio:
+0.318``" is not an explanation anyone is allowed to send. This module maps the
model's 134 features onto a small, fixed set of human-readable reason codes that
the risk team owns and signs off — the same shape as the adverse-action reason
codes lenders already issue under ECOA/Reg B and equivalents.

Design rules
------------
* **Many features, one reason.** All the delinquency features collapse into a
  single "adverse repayment history" code. A customer does not care that it was
  ``delinquencies_60d_24m`` specifically; the risk owner defines the bucket.
* **Aggregation is additive.** A reason code's contribution is the sum of its
  member features' SHAP values. That is exact — SHAP is additive by construction —
  so no information is invented by grouping.
* **Suppression is explicit.** Some codes (PEP status, prior SAR, sanctions) must
  never appear in a customer-facing explanation: showing them can tip off a
  subject and is often legally prohibited. ``customer_visible=False`` gates that,
  and :func:`validate_against_policy` checks this layer agrees with the feature
  list in ``config/risk_policy.yaml``.
* **Every feature must map.** A feature with no reason code would silently vanish
  from every explanation. :func:`unmapped_features` reports those, the same way
  the data dictionary flags undocumented columns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReasonCode:
    """One policy-owned explanation bucket.

    ``features`` are matched exactly; ``patterns`` are regexes matched against the
    feature name, which is how the windowed event features (``*_90d``, ``*_180d``)
    are captured without listing every variant.
    """

    code: str
    category: str
    statement: str
    features: frozenset[str] = field(default_factory=frozenset)
    patterns: tuple[str, ...] = ()
    customer_visible: bool = True

    def matches(self, feature: str) -> bool:
        if feature in self.features:
            return True
        return any(re.fullmatch(pattern, feature) for pattern in self.patterns)


# --------------------------------------------------------------------------
# The vocabulary. Order is priority order: the first code that matches a feature
# owns it, so more-specific codes are listed before broad ones.
# --------------------------------------------------------------------------

REASON_CODES: tuple[ReasonCode, ...] = (
    # --- credit / repayment ----------------------------------------------
    ReasonCode(
        "CR01", "credit", "High credit utilisation relative to available limit",
        frozenset({"credit_utilization_ratio", "revolving_balance", "revolving_balance_to_income",
                   "credit_limit_to_income", "total_credit_limit"}),
    ),
    ReasonCode(
        "CR02", "credit", "Debt service is high relative to income",
        frozenset({"dti_ratio"}),
    ),
    ReasonCode(
        "CR03", "credit", "Adverse repayment history",
        frozenset({"delinquencies_30d_12m", "delinquencies_60d_24m", "delinquencies_90d_24m",
                   "max_days_past_due_24m", "months_since_last_delinquency", "num_bounced_payments_12m",
                   "delinquency_severity", "prior_default_flag"}),
    ),
    ReasonCode(
        "CR04", "credit", "Weak external or internal credit score",
        frozenset({"bureau_score", "payment_history_score"}),
    ),
    ReasonCode(
        "CR05", "credit", "Frequent recent credit-seeking",
        frozenset({"num_credit_inquiries_12m", "inquiries_per_open_loan", "num_open_loans"}),
    ),
    ReasonCode(
        "CR06", "credit", "Overdraft or facility breaches",
        frozenset({"overdraft_events_12m", "overdraft_days_12m", "overdraft_days_per_event"}),
        patterns=(r"overdraft_breach_.*",),
    ),
    ReasonCode(
        "CR07", "credit", "Weak collateral coverage on secured lending",
        frozenset({"loan_to_value", "collateral_coverage_ratio"}),
    ),
    # --- affordability / liquidity ---------------------------------------
    ReasonCode(
        "AF01", "affordability", "Thin liquidity buffer relative to income",
        frozenset({"savings_to_income_ratio", "avg_monthly_balance", "min_monthly_balance",
                   "balance_months_of_income", "min_balance_months_of_income"}),
    ),
    ReasonCode(
        "AF02", "affordability", "Income level, stability or verification",
        frozenset({"declared_annual_income", "income_volatility_cv", "verified_income_ratio",
                   "income_months_missing_12m"}),
    ),
    ReasonCode(
        "AF03", "affordability", "High balance volatility",
        frozenset({"balance_volatility"}),
    ),
    # --- transactional behaviour -----------------------------------------
    ReasonCode(
        "BH01", "behaviour", "Unusual recent transaction velocity",
        frozenset({"txn_velocity_change_pct", "outflow_velocity_ratio", "event_count_velocity_ratio"}),
    ),
    ReasonCode(
        "BH02", "behaviour", "High cash intensity",
        frozenset({"cash_intensity_ratio", "large_cash_deposits_90d"}),
        patterns=(r"cash_deposit_.*",),
    ),
    ReasonCode(
        "BH03", "behaviour", "Elevated cross-border activity",
        frozenset({"cross_border_txn_count_90d", "cross_border_txn_ratio"}),
        patterns=(r"foreign_event_ratio_.*", r"distinct_counterparty_countries_.*", r"wire_transfer_out_.*"),
    ),
    ReasonCode(
        "BH04", "behaviour", "Cryptocurrency exposure",
        frozenset({"crypto_exposure_ratio_90d"}),
        patterns=(r"crypto_transfer_.*",),
    ),
    ReasonCode(
        "BH05", "behaviour", "Gambling exposure",
        frozenset({"gambling_spend_ratio_90d"}),
    ),
    ReasonCode(
        "BH06", "behaviour", "Off-hours transaction pattern",
        frozenset({"night_txn_ratio"}),
        patterns=(r"night_event_ratio_.*",),
    ),
    ReasonCode(
        "BH07", "behaviour", "High share of new counterparties",
        frozenset({"new_counterparty_ratio_90d", "merchant_category_entropy"}),
    ),
    ReasonCode(
        "BH08", "behaviour", "Recent adverse account events",
        frozenset({"chargeback_count_90d", "chargeback_amount_90d"}),
        patterns=(r"missed_payment_.*", r"trigger_event_.*", r"days_since_last_.*"),
    ),
    ReasonCode(
        "BH09", "behaviour", "Overall transaction volume and flow",
        frozenset({"txn_count_90d", "txn_volume_90d", "avg_txn_amount"}),
        patterns=(r"event_count_\d+d", r"inflow_\d+d", r"outflow_\d+d", r"net_flow_\d+d",
                  r"max_abs_amount_\d+d", r"has_event_history"),
    ),
    # --- AML / compliance (several suppressed from customer view) ---------
    ReasonCode(
        "AM01", "aml", "Politically exposed person status",
        frozenset({"pep_flag", "pep_relationship"}),
        customer_visible=False,
    ),
    ReasonCode(
        "AM02", "aml", "Sanctions or adverse-media screening result",
        frozenset({"sanctions_screen_hits", "adverse_media_hits_12m", "screening_hit_total"}),
        customer_visible=False,
    ),
    ReasonCode(
        "AM03", "aml", "Geographic risk exposure",
        frozenset({"high_risk_jurisdiction_exposure", "medium_risk_jurisdiction_exposure",
                   "jurisdiction_risk_tier", "country_of_residence"}),
    ),
    ReasonCode(
        "AM04", "aml", "Opaque source of funds or ownership",
        frozenset({"source_of_funds_declared", "source_of_funds_verified",
                   "beneficial_ownership_transparency", "offshore_entity_links"}),
    ),
    ReasonCode(
        "AM05", "aml", "Prior suspicious-activity report",
        frozenset({"sar_filed_prior"}),
        customer_visible=False,
    ),
    ReasonCode(
        "AM06", "aml", "Transaction-structuring indicators",
        frozenset({"structuring_score"}),
        customer_visible=False,
    ),
    ReasonCode(
        "AM07", "aml", "Aggregate compliance risk indicators",
        frozenset({"aml_flag_count", "cash_and_crypto_exposure", "edd_required"}),
        customer_visible=False,
    ),
    ReasonCode(
        "KY01", "kyc", "KYC documentation deficiency",
        frozenset({"kyc_document_completeness", "kyc_refresh_overdue_days", "kyc_deficiency_score"}),
    ),
    # --- profile ----------------------------------------------------------
    ReasonCode(
        "PR01", "profile", "Limited relationship history",
        frozenset({"account_age_months", "tenure_to_age_ratio", "num_products_held",
                   "products_per_tenure_year"}),
    ),
    ReasonCode(
        "PR02", "profile", "Employment and occupation profile",
        frozenset({"occupation", "employment_status", "years_at_employer"}),
    ),
    ReasonCode(
        "PR03", "profile", "Demographic and account profile",
        frozenset({"age", "marital_status", "dependents_count", "education_level",
                   "residency_status", "segment", "preferred_channel"}),
    ),
    # --- data quality -----------------------------------------------------
    ReasonCode(
        "DQ01", "data_quality", "Missing information in the customer file",
        patterns=(r".*_is_missing",),
    ),
)

#: Fast lookup by code.
BY_CODE: dict[str, ReasonCode] = {rc.code: rc for rc in REASON_CODES}


def code_for_feature(feature: str) -> ReasonCode | None:
    """The first reason code that claims ``feature``, or None if unmapped."""
    for reason_code in REASON_CODES:
        if reason_code.matches(feature):
            return reason_code
    return None


def build_feature_map(features: list[str]) -> dict[str, str]:
    """Map each feature name to its reason-code id. Raises on collisions/gaps deferred to callers."""
    mapping: dict[str, str] = {}
    for feature in features:
        reason_code = code_for_feature(feature)
        if reason_code is not None:
            mapping[feature] = reason_code.code
    return mapping


def unmapped_features(features: list[str]) -> list[str]:
    """Features with no reason code — they would vanish from every explanation."""
    return [feature for feature in features if code_for_feature(feature) is None]


def validate_against_policy(suppressed_features: list[str]) -> list[str]:
    """Check every policy-suppressed feature sits in a non-customer-visible code.

    ``config/risk_policy.yaml`` names individual features to hide from customers.
    A feature in that list whose reason code is nonetheless customer-visible is a
    policy/vocabulary disagreement that would leak the feature into a customer
    explanation. Returns the offending features (empty means consistent).
    """
    offenders: list[str] = []
    for feature in suppressed_features:
        reason_code = code_for_feature(feature)
        if reason_code is None or reason_code.customer_visible:
            offenders.append(feature)
    return offenders
