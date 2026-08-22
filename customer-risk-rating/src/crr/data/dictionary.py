"""Column documentation, and a renderer that builds the data dictionary from real data.

Generating the dictionary from a live frame rather than maintaining it by hand
means it cannot drift out of date: a column added to the generator with no entry
here shows up as UNDOCUMENTED in the rendered table.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ColumnDoc:
    block: str
    description: str
    pii: bool = False
    note: str = ""


#: block -> ordering weight, used only to group the rendered tables.
BLOCK_ORDER: tuple[str, ...] = (
    "identity", "pii", "profile", "financial", "behavioural", "compliance",
    "narrative", "event", "outcome", "ground_truth",
)

COLUMNS: dict[str, ColumnDoc] = {
    # --- identity ---------------------------------------------------------
    "customer_id": ColumnDoc("identity", "Stable synthetic customer key. Join key for every frame."),
    "snapshot_date": ColumnDoc("identity", "Date the feature values were observed. Features must not use data after this date."),
    "split": ColumnDoc("identity", "Out-of-time split: train / validation / test, assigned by snapshot cohort."),
    "cohort_month": ColumnDoc("identity", "Index of the monthly cohort, 0 = oldest."),
    # --- pii --------------------------------------------------------------
    "full_name": ColumnDoc("pii", "Synthetic name.", pii=True, note="Strip before any model or log."),
    "national_id": ColumnDoc("pii", "Fake national identifier, 'SYN-' prefix, no valid checksum.", pii=True),
    "email": ColumnDoc("pii", "Synthetic address on the reserved .invalid TLD.", pii=True),
    "phone": ColumnDoc("pii", "Number in the +1-555-01xx range reserved for fiction.", pii=True),
    "address_line": ColumnDoc("pii", "Synthetic street address.", pii=True),
    # --- profile ----------------------------------------------------------
    "segment": ColumnDoc("profile", "Book segment: retail / sme / private_banking / corporate."),
    "occupation": ColumnDoc("profile", "Declared occupation; carries AML cash-intensity and PEP priors."),
    "employment_status": ColumnDoc("profile", "salaried / self_employed / business_owner / unemployed / retired / student."),
    "age": ColumnDoc("profile", "Age in years at snapshot date."),
    "years_at_employer": ColumnDoc("profile", "Tenure with the current employer or business."),
    "account_age_months": ColumnDoc("profile", "Age of the banking relationship in months (the brief's 'גיל החשבון')."),
    "education_level": ColumnDoc("profile", "Highest completed education level."),
    "marital_status": ColumnDoc("profile", "Marital status."),
    "dependents_count": ColumnDoc("profile", "Number of financial dependents."),
    "preferred_channel": ColumnDoc("profile", "Channel the customer mostly uses."),
    "residency_status": ColumnDoc("profile", "citizen / permanent_resident / temporary_visa / non_resident."),
    "country_of_residence": ColumnDoc("profile", "ISO-3166 alpha-2 code of the primary jurisdiction."),
    "num_products_held": ColumnDoc("profile", "Count of distinct products held."),
    # --- financial --------------------------------------------------------
    "declared_annual_income": ColumnDoc("financial", "Income as declared by the customer."),
    "verified_income_ratio": ColumnDoc("financial", "Verified income divided by declared income. Below 1 means overstatement."),
    "income_volatility_cv": ColumnDoc("financial", "Coefficient of variation of monthly income over 12 months."),
    "income_months_missing_12m": ColumnDoc("financial", "Months in the last 12 with no observed income credit."),
    "total_credit_limit": ColumnDoc("financial", "Sum of all approved credit limits."),
    "credit_utilization_ratio": ColumnDoc("financial", "Drawn balance over limit. Can exceed 1 when over-limit."),
    "revolving_balance": ColumnDoc("financial", "Outstanding revolving balance."),
    "num_open_loans": ColumnDoc("financial", "Count of open loan facilities."),
    "dti_ratio": ColumnDoc("financial", "Total debt service divided by income."),
    "num_credit_inquiries_12m": ColumnDoc("financial", "Credit searches in the last 12 months."),
    "delinquencies_30d_12m": ColumnDoc("financial", "Payments 30+ days late in the last 12 months."),
    "delinquencies_60d_24m": ColumnDoc("financial", "Payments 60+ days late in the last 24 months."),
    "delinquencies_90d_24m": ColumnDoc("financial", "Payments 90+ days late in the last 24 months."),
    "max_days_past_due_24m": ColumnDoc("financial", "Worst delinquency depth in days over 24 months."),
    "months_since_last_delinquency": ColumnDoc("financial", "Months since the most recent late payment."),
    "prior_default_flag": ColumnDoc("financial", "A default occurred before the snapshot date.", note="Historical, not the label."),
    "num_bounced_payments_12m": ColumnDoc("financial", "Direct debits or cheques returned unpaid."),
    "bureau_score": ColumnDoc("financial", "External bureau score, 300-850.", note="Missing for thin files (MAR)."),
    "payment_history_score": ColumnDoc("financial", "Internal repayment behaviour score, 0-100."),
    "avg_monthly_balance": ColumnDoc("financial", "Mean end-of-day balance over 12 months."),
    "min_monthly_balance": ColumnDoc("financial", "Lowest monthly balance; negative means overdrawn."),
    "balance_volatility": ColumnDoc("financial", "Standard deviation of balance over its mean."),
    "overdraft_events_12m": ColumnDoc("financial", "Times the customer breached their facility (the brief's 'חריגות ממסגרת')."),
    "overdraft_days_12m": ColumnDoc("financial", "Total days spent over the limit."),
    "savings_to_income_ratio": ColumnDoc("financial", "Liquid savings divided by annual income."),
    "loan_to_value": ColumnDoc("financial", "LTV on secured lending.", note="Structurally absent without a secured loan."),
    "collateral_coverage_ratio": ColumnDoc("financial", "Collateral value over exposure.", note="Structurally absent without a secured loan."),
    # --- behavioural ------------------------------------------------------
    "txn_count_90d": ColumnDoc("behavioural", "Transaction count over 90 days."),
    "txn_volume_90d": ColumnDoc("behavioural", "Total transacted value over 90 days."),
    "avg_txn_amount": ColumnDoc("behavioural", "Mean absolute transaction size."),
    "cash_intensity_ratio": ColumnDoc("behavioural", "Share of activity settled in cash."),
    "large_cash_deposits_90d": ColumnDoc("behavioural", "Count of cash deposits above the reporting threshold."),
    "cross_border_txn_count_90d": ColumnDoc("behavioural", "Cross-border transactions in 90 days."),
    "cross_border_txn_ratio": ColumnDoc("behavioural", "Share of transactions crossing a border."),
    "night_txn_ratio": ColumnDoc("behavioural", "Share of transactions outside normal hours."),
    "new_counterparty_ratio_90d": ColumnDoc("behavioural", "Share of value sent to counterparties first seen in the window."),
    "merchant_category_entropy": ColumnDoc("behavioural", "Shannon entropy over merchant categories."),
    "txn_velocity_change_pct": ColumnDoc("behavioural", "30-day volume against the trailing 90-day baseline.", note="Primary real-time re-scoring trigger."),
    "gambling_spend_ratio_90d": ColumnDoc("behavioural", "Share of spend at gambling merchants."),
    "crypto_exposure_ratio_90d": ColumnDoc("behavioural", "Share of value moving to or from crypto venues."),
    "structuring_score": ColumnDoc("behavioural", "Propensity of deposits to sit just under the reporting threshold."),
    # --- compliance -------------------------------------------------------
    "pep_flag": ColumnDoc("compliance", "Customer is politically exposed."),
    "pep_relationship": ColumnDoc("compliance", "none / self / family_member / close_associate."),
    "sanctions_screen_hits": ColumnDoc("compliance", "Unresolved sanctions-list matches."),
    "adverse_media_hits_12m": ColumnDoc("compliance", "Negative-news hits in the last 12 months."),
    "high_risk_jurisdiction_exposure": ColumnDoc("compliance", "Primary jurisdiction is on the high-risk list."),
    "medium_risk_jurisdiction_exposure": ColumnDoc("compliance", "Primary jurisdiction is medium risk."),
    "jurisdiction_risk_tier": ColumnDoc("compliance", "low / medium / high, derived from country_of_residence."),
    "offshore_entity_links": ColumnDoc("compliance", "Linked entities in offshore financial centres."),
    "source_of_funds_declared": ColumnDoc("compliance", "Declared origin of wealth (the brief's 'מקור כספים מוצהר')."),
    "source_of_funds_verified": ColumnDoc("compliance", "Declared source was evidenced.", note="MNAR: missing mostly when undeclared."),
    "kyc_document_completeness": ColumnDoc("compliance", "Share of required KYC documents held and in date."),
    "kyc_refresh_overdue_days": ColumnDoc("compliance", "Days past the due date for periodic KYC review."),
    "beneficial_ownership_transparency": ColumnDoc("compliance", "clear / partial / opaque."),
    "sar_filed_prior": ColumnDoc("compliance", "A suspicious activity report was filed before the snapshot."),
    "edd_required": ColumnDoc("compliance", "Enhanced due diligence triggered by policy."),
    # --- narratives -------------------------------------------------------
    "language": ColumnDoc("narrative", "Language of the narrative fields: en or he."),
    "support_call_summary": ColumnDoc("narrative", "Agent's summary of a customer-service interaction."),
    "underwriter_note": ColumnDoc("narrative", "Free-text credit opinion written by a human underwriter."),
    "kyc_document_extract": ColumnDoc("narrative", "Extract from the KYC file rendered as free text."),
    # --- events -----------------------------------------------------------
    "event_id": ColumnDoc("event", "Unique event key."),
    "event_ts": ColumnDoc("event", "Event timestamp; always at or before the customer's snapshot_date."),
    "event_type": ColumnDoc("event", "Transaction or lifecycle event type."),
    "amount": ColumnDoc("event", "Signed amount; negative is money out."),
    "counterparty_country": ColumnDoc("event", "Counterparty jurisdiction."),
    "channel": ColumnDoc("event", "card / atm / branch / online / swift."),
    "is_trigger_event": ColumnDoc("event", "Event should force an immediate re-score under the current policy."),
    # --- outcomes ---------------------------------------------------------
    "outcome_observation_date": ColumnDoc("outcome", "End of the performance window: snapshot_date + horizon."),
    "default_12m": ColumnDoc("outcome", "TARGET. 90+ days past due within the performance window."),
    "financial_crime_12m": ColumnDoc("outcome", "TARGET. Financial-crime concern confirmed within the window."),
    "days_past_due_at_outcome": ColumnDoc("outcome", "Delinquency depth at outcome; 0 when no default."),
    "underwriter_decision": ColumnDoc("outcome", "Human decision at snapshot time.", note="Feature only for the feedback loop, never for the risk model."),
    "underwriter_confidence": ColumnDoc("outcome", "Self-reported confidence, 0-1."),
    "underwriter_override_flag": ColumnDoc("outcome", "Human materially disagreed with the model view."),
    "underwriter_perceived_score": ColumnDoc("outcome", "Risk the human perceived. Deliberately biased against the truth."),
    # --- ground truth -----------------------------------------------------
    "z_credit_quality": ColumnDoc("ground_truth", "Latent credit-quality factor. Higher is better."),
    "z_liquidity_stress": ColumnDoc("ground_truth", "Latent liquidity-stress factor."),
    "z_volatility": ColumnDoc("ground_truth", "Latent income/balance volatility factor."),
    "z_concealment": ColumnDoc("ground_truth", "Latent opacity factor driving the AML dimension."),
    "latent_distress": ColumnDoc("ground_truth", "Continuous repayment-distress signal behind the narratives."),
    "latent_concealment_text": ColumnDoc("ground_truth", "Continuous concealment signal behind the narratives."),
    "narrative_distress_level": ColumnDoc("ground_truth", "0-3 distress level the notes actually express.", note="Oracle for measuring LLM headroom."),
    "narrative_concealment_level": ColumnDoc("ground_truth", "0-3 concealment level the notes actually express."),
    "p_default_true": ColumnDoc("ground_truth", "True default probability used to sample the label."),
    "p_financial_crime_true": ColumnDoc("ground_truth", "True financial-crime probability used to sample the label."),
    "true_risk_score": ColumnDoc("ground_truth", "Composite 0-100 ground-truth risk score."),
    "true_risk_band": ColumnDoc("ground_truth", "Low / Medium / High / Extreme from true_risk_score."),
    "duplicate_of_customer_id": ColumnDoc("ground_truth", "Set when this record is a near-duplicate of another."),
}


def render_markdown(frames: dict[str, pd.DataFrame]) -> str:
    """Render the data dictionary from live frames."""
    lines: list[str] = [
        "# Data Dictionary",
        "",
        "Generated by `scripts/render_data_dictionary.py` from a real generated dataset —",
        "do not edit by hand. A column with no entry in `crr.data.dictionary.COLUMNS`",
        "renders as **UNDOCUMENTED**, which is the signal to add one.",
        "",
        "All data is synthetic. No real person, account or entity is represented.",
        "",
    ]

    seen: set[str] = set()
    for frame_name, frame in frames.items():
        lines += [f"## `{frame_name}`", "", f"{len(frame):,} rows × {len(frame.columns)} columns", "",
                  "| column | type | block | null % | description |",
                  "|---|---|---|---|---|"]
        for column in frame.columns:
            doc = COLUMNS.get(column)
            seen.add(column)
            null_pct = float(frame[column].isna().mean() * 100)
            dtype = str(frame[column].dtype)
            if doc is None:
                lines.append(f"| `{column}` | {dtype} | ? | {null_pct:.1f} | **UNDOCUMENTED** |")
                continue
            text = doc.description
            if doc.pii:
                text = "**PII.** " + text
            if doc.note:
                text += f" _{doc.note}_"
            lines.append(f"| `{column}` | {dtype} | {doc.block} | {null_pct:.1f} | {text} |")
        lines.append("")

    orphans = sorted(set(COLUMNS) - seen)
    if orphans:
        lines += ["## Documented but not present", "", ", ".join(f"`{c}`" for c in orphans), ""]
    return "\n".join(lines)


def pii_columns() -> tuple[str, ...]:
    """Columns the anonymisation layer must strip or pseudonymise."""
    return tuple(name for name, doc in COLUMNS.items() if doc.pii)


def undocumented(frame: pd.DataFrame) -> list[str]:
    """Columns present in a frame with no dictionary entry."""
    return [c for c in frame.columns if c not in COLUMNS]
