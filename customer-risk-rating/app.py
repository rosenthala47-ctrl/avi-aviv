"""Streamlit front end for the Customer Risk Rating API — an operations console.

A thin HTTP client, deliberately. Every score, band, SHAP factor and re-score
shown here comes from the live FastAPI service over its documented endpoints —
this module holds no model, no policy and no scoring logic that could quietly
disagree with the API. That discipline extends to the workflow layer added in
this file (the Operations Queue, case status, SLA clock and review notes): the
API has no case-management endpoints, so that state is held in
``st.session_state`` for this browser session only — it is not written to any
database, and a page refresh or a new tab starts a fresh queue. It exists to
demonstrate the reviewer workflow a real deployment would wire to a case
system, not to replace one.

    streamlit run app.py

Set ``CRR_API_URL`` to point at a running API (default http://127.0.0.1:8000).
"""

from __future__ import annotations

import datetime as dt
import html
import os
import random
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

API_URL = os.environ.get("CRR_API_URL", "http://127.0.0.1:8000").rstrip("/")
REQUEST_TIMEOUT = float(os.environ.get("CRR_UI_TIMEOUT", "30"))

# --------------------------------------------------------------------------
# Design tokens.
#
# The page commits to a single light look, so every colour below is validated
# against one known surface (#fcfcfb) rather than being left to a theme that
# might swap underneath the charts. The categorical pair used for the SHAP
# chart — blue #2a78d6 (lowers risk) against red #e34948 (raises risk) — is a
# warm/cool diverging pair: it passes the colour-vision-deficiency separation
# check on this surface (worst-pair ΔE 21.6 protan, 32.3 normal vision), and
# direction is never carried by colour alone (every bar is labelled and the
# table view below each chart repeats the value). Case-status colours below
# reuse this same palette rather than inventing new hues, and — like every
# badge in this file — never carry meaning by colour alone: the label text
# always sits right next to it.
# --------------------------------------------------------------------------
SURFACE = "#fcfcfb"
PAGE_PLANE = "#f9f9f7"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
AXIS_LINE = "#c3c2b7"
HAIRLINE = "rgba(11,11,11,0.10)"

RAISES_RISK = "#e34948"
LOWERS_RISK = "#2a78d6"

# Bands are a state, not an identity, so they wear the reserved status palette
# — and always beside their own name, never as colour alone.
BAND_COLOUR = {"Low": "#0ca30c", "Medium": "#fab219", "High": "#ec835a", "Extreme": "#d03b3b"}
BAND_ORDER = ("Low", "Medium", "High", "Extreme")
BAND_RANK = {"Extreme": 0, "High": 1, "Medium": 2, "Low": 3}
BAND_DOT = {"Extreme": "\U0001f534", "High": "\U0001f7e0", "Medium": "\U0001f7e1", "Low": "\U0001f7e2"}

FONT_STACK = 'system-ui, -apple-system, "Segoe UI", sans-serif'

# Mirrors config/risk_policy.yaml's `bands:` block for the score-position strip
# only. The API remains authoritative for the band a customer actually gets —
# this is drawn from the returned band, never recomputed from these numbers.
BAND_CUTOFFS = {"Low": 25.0, "Medium": 50.0, "High": 75.0, "Extreme": 100.0}

# --------------------------------------------------------------------------
# Workflow layer — case status and SLA. Neither exists in the API: there is no
# case-management endpoint to call, so these are a UI-side convention (a
# reasonable stand-in for what a real Unit21/Feedzai-style queue enforces
# server-side) documented here rather than left implicit.
# --------------------------------------------------------------------------
STATUS_LABEL = {
    "pending_review": "Pending Review",
    "approved": "Approved",
    "escalated_aml": "Escalated to AML",
    "kyc_requested": "KYC Requested",
    "blocked": "Blocked",
}
STATUS_COLOUR = {
    "pending_review": INK_MUTED,
    "approved": "#0ca30c",
    "escalated_aml": "#d03b3b",
    "kyc_requested": "#fab219",
    "blocked": "#3a1414",
}
# Hours from the scoring timestamp a case may sit at "Pending Review" before
# counting as an SLA breach, banded by risk the way a real triage queue would
# prioritise review capacity — a workflow convention, not a compliance policy.
SLA_HOURS = {"Extreme": 4, "High": 24, "Medium": 72, "Low": 168}

EVENT_TYPES = (
    "missed_payment", "overdraft_breach", "chargeback", "cash_deposit",
    "wire_transfer_out", "crypto_transfer", "card_purchase", "atm_withdrawal",
    "salary_credit", "wire_transfer_in", "direct_debit", "loan_repayment",
)
# A hint for the operator, not a rule: the policy decides, and the API's
# `reason` field reports what it actually decided.
LIKELY_TRIGGERS = frozenset(
    {"missed_payment", "overdraft_breach", "chargeback", "cash_deposit",
     "wire_transfer_out", "crypto_transfer"}
)

REASON_HELP = {
    "triggered": "The event matched a policy trigger and the customer was re-scored.",
    "debounced": "A matching trigger fired too recently — suppressed to stop alert storms.",
    "no_trigger": "Stored, but this event type/amount matches no trigger in the policy.",
    "not_yet_scored": "No score on record yet. Score the customer once first.",
    "stale": "The stored score was too old to re-use as a base for re-scoring.",
}

SEGMENTS = ("retail", "sme", "private_banking", "corporate")
OCCUPATIONS = (
    "software_engineer", "physician", "nurse", "teacher", "accountant", "lawyer",
    "construction_worker", "retail_worker", "driver", "chef", "business_owner",
    "consultant", "civil_servant", "police_officer", "military", "artist",
    "researcher", "sales_manager", "real_estate_agent", "jeweller",
    "import_exporter", "crypto_trader", "casino_employee", "money_changer",
    "student", "retired", "unemployed", "homemaker", "freelancer", "other",
)
EMPLOYMENT_STATUSES = ("salaried", "self_employed", "business_owner", "unemployed", "retired", "student")
RESIDENCY_STATUSES = ("citizen", "permanent_resident", "temporary_visa", "non_resident")
SOURCE_OF_FUNDS = (
    "salary", "business_income", "investment_returns", "inheritance",
    "property_sale", "gift", "loan_proceeds", "crypto_disposal", "undeclared",
)
COUNTRIES = (
    "IL", "US", "GB", "DE", "FR", "NL", "CA", "AU", "JP", "SG", "CH",
    "CY", "MT", "AE", "TR", "HK", "PA", "KY", "VG", "SC", "BZ", "RU", "UA", "NG", "ZA",
    "SY", "YE", "AF", "MM", "VU", "HT", "SS", "LY",
)

# Every field below is optional at the API boundary. What the UI sends as a
# real value versus omits entirely is the whole point of the "unknown fields"
# control on the sandbox tab: an omitted field reaches the model as a genuine
# missing value, not as a fabricated zero. These three profiles double as the
# archetypes the Operations Queue's demo book is jittered from (see
# `generate_book` below) — no fabricated customer names anywhere in this file,
# only the customer_id/segment/occupation fields the real schema carries.
PRESETS: dict[str, dict[str, Any]] = {
    "Low risk — salaried, clean file": {
        "segment": "retail", "occupation": "software_engineer", "employment_status": "salaried",
        "age": 41, "years_at_employer": 7.0, "account_age_months": 130, "residency_status": "citizen",
        "country_of_residence": "IL", "num_products_held": 4,
        "declared_annual_income": 320000.0, "verified_income_ratio": 1.0, "income_volatility_cv": 0.06,
        "total_credit_limit": 120000.0, "credit_utilization_ratio": 0.14, "dti_ratio": 0.19,
        "num_open_loans": 1, "num_credit_inquiries_12m": 0, "delinquencies_30d_12m": 0,
        "delinquencies_90d_24m": 0, "max_days_past_due_24m": 0, "prior_default_flag": 0,
        "num_bounced_payments_12m": 0, "bureau_score": 792, "savings_to_income_ratio": 0.55,
        "overdraft_events_12m": 0, "balance_volatility": 0.12,
        "txn_count_90d": 180, "cash_intensity_ratio": 0.04, "cross_border_txn_ratio": 0.02,
        "night_txn_ratio": 0.05, "structuring_score": 0.02, "crypto_exposure_ratio_90d": 0.0,
        "gambling_spend_ratio_90d": 0.0, "new_counterparty_ratio_90d": 0.10,
        "pep_flag": 0, "sanctions_screen_hits": 0, "adverse_media_hits_12m": 0,
        "high_risk_jurisdiction_exposure": 0, "medium_risk_jurisdiction_exposure": 0,
        "offshore_entity_links": 0, "source_of_funds_declared": "salary",
        "source_of_funds_verified": 1, "kyc_document_completeness": 1.0,
        "kyc_refresh_overdue_days": 0, "sar_filed_prior": 0, "edd_required": 0,
        "_narratives": {
            "support_call_summary": "Customer called to update their mailing address. No concerns raised.",
            "underwriter_note": "Long tenure, stable salary, low utilisation. Straightforward renewal.",
            "kyc_document_extract": "Passport and payslips on file, all current.",
        },
    },
    "Credit stress — thin buffer, recent arrears": {
        "segment": "retail", "occupation": "construction_worker", "employment_status": "self_employed",
        "age": 33, "years_at_employer": 1.0, "account_age_months": 22, "residency_status": "citizen",
        "country_of_residence": "IL", "num_products_held": 2,
        "declared_annual_income": 118000.0, "verified_income_ratio": 0.6, "income_volatility_cv": 0.42,
        "total_credit_limit": 40000.0, "credit_utilization_ratio": 0.71, "dti_ratio": 0.4,
        "num_open_loans": 3, "num_credit_inquiries_12m": 4, "delinquencies_30d_12m": 1,
        "delinquencies_90d_24m": 0, "max_days_past_due_24m": 38, "prior_default_flag": 0,
        "num_bounced_payments_12m": 2, "bureau_score": 601, "savings_to_income_ratio": 0.04,
        "overdraft_events_12m": 5, "balance_volatility": 0.62,
        "txn_count_90d": 96, "cash_intensity_ratio": 0.24, "cross_border_txn_ratio": 0.01,
        "night_txn_ratio": 0.15, "structuring_score": 0.08, "crypto_exposure_ratio_90d": 0.02,
        "gambling_spend_ratio_90d": 0.04, "new_counterparty_ratio_90d": 0.28,
        "pep_flag": 0, "sanctions_screen_hits": 0, "adverse_media_hits_12m": 0,
        "high_risk_jurisdiction_exposure": 0, "medium_risk_jurisdiction_exposure": 0,
        "offshore_entity_links": 0, "source_of_funds_declared": "business_income",
        "source_of_funds_verified": 0, "kyc_document_completeness": 0.7,
        "kyc_refresh_overdue_days": 40, "sar_filed_prior": 0, "edd_required": 0,
        "_narratives": {
            "support_call_summary": "Customer said their main contract ended and they are behind on "
                                    "payments. Asked whether payments can be paused for a few months.",
            "underwriter_note": "Income verification incomplete. Utilisation at limit and arrears rising.",
            "kyc_document_extract": "Payslips not provided; self-declared income only.",
        },
    },
    "AML concern — opaque funds, offshore links": {
        "segment": "private_banking", "occupation": "import_exporter", "employment_status": "business_owner",
        "age": 55, "years_at_employer": 14.0, "account_age_months": 60, "residency_status": "non_resident",
        "country_of_residence": "CY", "num_products_held": 6,
        "declared_annual_income": 900000.0, "verified_income_ratio": 0.3, "income_volatility_cv": 0.4,
        "total_credit_limit": 500000.0, "credit_utilization_ratio": 0.22, "dti_ratio": 0.2,
        "num_open_loans": 2, "num_credit_inquiries_12m": 1, "delinquencies_30d_12m": 0,
        "delinquencies_90d_24m": 0, "max_days_past_due_24m": 0, "prior_default_flag": 0,
        "num_bounced_payments_12m": 0, "bureau_score": 715, "savings_to_income_ratio": 0.9,
        "overdraft_events_12m": 0, "balance_volatility": 0.55,
        "txn_count_90d": 420, "cash_intensity_ratio": 0.72, "cross_border_txn_ratio": 0.68,
        "night_txn_ratio": 0.41, "structuring_score": 0.83, "crypto_exposure_ratio_90d": 0.35,
        "gambling_spend_ratio_90d": 0.0, "new_counterparty_ratio_90d": 0.77,
        "pep_flag": 1, "sanctions_screen_hits": 0, "adverse_media_hits_12m": 3,
        "high_risk_jurisdiction_exposure": 1, "medium_risk_jurisdiction_exposure": 1,
        "offshore_entity_links": 4, "source_of_funds_declared": "undeclared",
        "source_of_funds_verified": 0, "kyc_document_completeness": 0.35,
        "kyc_refresh_overdue_days": 260, "sar_filed_prior": 1, "edd_required": 1,
        "_narratives": {
            "support_call_summary": "Customer declined to explain the source of a large inbound "
                                    "transfer and ended the call when asked for documents.",
            "underwriter_note": "Ownership structure runs through two offshore holding entities; "
                                "beneficial owner not established. Repeated document requests unanswered.",
            "kyc_document_extract": "Corporate registry extract incomplete; nominee directors listed.",
        },
    },
}


# --------------------------------------------------------------------------
# Demo book — jittered variants of the three archetypes above, used to seed
# the Operations Queue with something that looks like a real morning's work
# rather than an empty table. Every variant is still scored by the live API;
# nothing about the score, band or factors is fabricated here, only the
# *input* profile is synthesised, exactly as scripts/generate_synthetic_data.py
# does for training — this is the same idea at UI scale. Jitter ranges are
# kept inside CustomerPayload's own field constraints (crr.api.schemas) so a
# generated variant can never fail validation at the API boundary.
# --------------------------------------------------------------------------

# field -> (relative pct jitter or None, absolute delta or None, lo, hi, integer?)
_LOW_RISK_JITTER: dict[str, tuple[float | None, float | None, float, float | None, bool]] = {
    "age": (None, 8, 18, 90, True),
    "bureau_score": (None, 60, 300, 850, True),
    "credit_utilization_ratio": (0.4, None, 0.0, 2.0, False),
    "dti_ratio": (0.4, None, 0.0, 2.0, False),
    "account_age_months": (0.4, None, 0, 480, True),
    "declared_annual_income": (0.3, None, 0.0, None, False),
    "txn_count_90d": (0.3, None, 0, 2000, True),
    "cash_intensity_ratio": (0.5, None, 0.0, 1.0, False),
    "num_products_held": (None, 2, 0, 15, True),
}
_CREDIT_STRESS_JITTER: dict[str, tuple[float | None, float | None, float, float | None, bool]] = {
    "bureau_score": (None, 60, 300, 850, True),
    "dti_ratio": (0.3, None, 0.0, 2.0, False),
    "credit_utilization_ratio": (0.25, None, 0.0, 2.0, False),
    "delinquencies_30d_12m": (None, 2, 0, 20, True),
    "overdraft_events_12m": (None, 4, 0, 60, True),
    "max_days_past_due_24m": (None, 20, 0, 365, True),
    "num_bounced_payments_12m": (None, 2, 0, 30, True),
    "balance_volatility": (0.4, None, 0.0, 2.0, False),
}
_AML_CONCERN_JITTER: dict[str, tuple[float | None, float | None, float, float | None, bool]] = {
    "structuring_score": (0.4, None, 0.0, 1.0, False),
    "cash_intensity_ratio": (0.3, None, 0.0, 1.0, False),
    "cross_border_txn_ratio": (0.3, None, 0.0, 1.0, False),
    "adverse_media_hits_12m": (None, 2, 0, 20, True),
    "offshore_entity_links": (None, 2, 0, 20, True),
    "kyc_document_completeness": (0.3, None, 0.0, 1.0, False),
    "new_counterparty_ratio_90d": (0.3, None, 0.0, 1.0, False),
    "txn_count_90d": (0.3, None, 0, 2000, True),
}
_ARCHETYPE_JITTER = {
    "Low risk — salaried, clean file": _LOW_RISK_JITTER,
    "Credit stress — thin buffer, recent arrears": _CREDIT_STRESS_JITTER,
    "AML concern — opaque funds, offshore links": _AML_CONCERN_JITTER,
}


def _jitter_value(
    value: float, rng: random.Random, *,
    pct: float | None, delta: float | None, lo: float, hi: float | None, integer: bool,
) -> float:
    result = value * (1 + rng.uniform(-pct, pct)) if pct is not None else value + rng.uniform(-delta, delta)
    result = max(lo, result)
    if hi is not None:
        result = min(hi, result)
    return float(round(result)) if integer else round(float(result), 4)


def make_variant(archetype: str, rng: random.Random) -> dict[str, Any]:
    """One jittered copy of an archetype's field values (``_narratives`` excluded)."""
    values = {k: v for k, v in PRESETS[archetype].items() if k != "_narratives"}
    for field, (pct, delta, lo, hi, integer) in _ARCHETYPE_JITTER[archetype].items():
        values[field] = _jitter_value(values[field], rng, pct=pct, delta=delta, lo=lo, hi=hi, integer=integer)
    return values


def generate_book(seed: int, per_archetype: int = 6) -> list[dict[str, Any]]:
    """A deterministic (given ``seed``) list of synthetic candidate customers,
    ``per_archetype`` variants of each of the three profiles above."""
    rng = random.Random(seed)
    book = []
    counter = 1
    for archetype in PRESETS:
        for _ in range(per_archetype):
            book.append({
                "customer_id": f"CUS-{100000 + counter}",
                "archetype": archetype,
                "values": make_variant(archetype, rng),
                "narratives": PRESETS[archetype]["_narratives"],
            })
            counter += 1
    return book


# --------------------------------------------------------------------------
# API client
# --------------------------------------------------------------------------


class ApiError(Exception):
    """A request reached the API and came back unusable, or never reached it."""


def _request(method: str, path: str, **kwargs: Any) -> Any:
    url = f"{API_URL}{path}"
    try:
        response = requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise ApiError(f"Could not reach the API at {url} — {exc}") from exc

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise ApiError(f"HTTP {response.status_code} from {path}: {detail}")
    return response.json()


def api_health() -> dict[str, Any]:
    return _request("GET", "/health")


def api_score(payload: dict[str, Any]) -> dict[str, Any]:
    return _request("POST", "/api/v1/score", json=payload)["result"]


def api_explain(customer_id: str, audience: str) -> dict[str, Any]:
    return _request("GET", f"/api/v1/explain/{customer_id}", params={"audience": audience})


def api_event(customer_id: str, event: dict[str, Any]) -> dict[str, Any]:
    return _request("POST", f"/api/v1/events/{customer_id}", json=event)


# --------------------------------------------------------------------------
# Presentation helpers
# --------------------------------------------------------------------------


def band_chip(band: str, label: str = "") -> str:
    colour = BAND_COLOUR.get(band, INK_MUTED)
    prefix = f"{label} " if label else ""
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
        f'background:{colour};color:#fff;font-weight:600;font-size:0.85rem;'
        f'font-family:{FONT_STACK};">{prefix}{band}</span>'
    )


def status_chip(status: str) -> str:
    """Unlike ``band_chip``, this is always rendered as the sole content of its
    own ``st.markdown`` call rather than concatenated onto a leading ``<div>``
    — and CommonMark's raw-HTML-block rule only recognises a fixed list of
    block-level tag names (``div`` among them) at the start of standalone
    content; a bare ``<span>`` there gets treated as literal text and escaped
    instead of rendered. ``display:inline-block`` keeps the compact pill look
    of a span while the tag name itself satisfies that rule.
    """
    colour = STATUS_COLOUR.get(status, INK_MUTED)
    label = STATUS_LABEL.get(status, status)
    return (
        f'<div style="display:inline-block;padding:2px 10px;border-radius:999px;'
        f'background:{colour};color:#fff;font-weight:600;font-size:0.85rem;'
        f'font-family:{FONT_STACK};">case: {label}</div>'
    )


def _parse_dt(value: str | dt.datetime) -> dt.datetime:
    return value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(value)


def flash_success(key: str, message: str) -> None:
    """Queue a success message for the render right after the ``st.rerun()``
    that normally follows an action like this. ``st.success(...)`` called
    immediately before ``st.rerun()`` never gets a chance to paint — the
    rerun tears down that render before the browser shows it — so the
    message is stashed in session state and picked up by ``show_flash`` on
    the next run instead."""
    st.session_state[f"_flash_{key}"] = message


def show_flash(key: str) -> None:
    message = st.session_state.pop(f"_flash_{key}", None)
    if message:
        st.success(message)


def _base_layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT_STACK, color=INK_SECONDARY, size=13),
        showlegend=False,
        hoverlabel=dict(font=dict(family=FONT_STACK, size=12), bgcolor="#ffffff", bordercolor=AXIS_LINE),
    )
    return fig


def score_position_strip(score: float, model_band: str, risk_band: str) -> go.Figure:
    """Where this score sits on the 0-100 policy band scale.

    A position indicator, not a one-bar bar chart: the four band segments are
    the scale, and the marker is the reading. When a policy rule has floored
    the band, the model's own band is still drawn — hiding the difference
    would hide the single most reviewable thing about the decision.
    """
    fig = go.Figure()
    lower = 0.0
    for band in BAND_ORDER:
        upper = BAND_CUTOFFS[band]
        fig.add_shape(
            type="rect", x0=lower, x1=upper, y0=0.0, y1=1.0,
            fillcolor=BAND_COLOUR[band], opacity=0.16, line=dict(width=0), layer="below",
        )
        fig.add_annotation(
            x=(lower + upper) / 2, y=-0.55, text=band, showarrow=False,
            font=dict(size=11, color=INK_MUTED, family=FONT_STACK),
        )
        lower = upper

    fig.add_shape(type="line", x0=score, x1=score, y0=-0.12, y1=1.12,
                  line=dict(color=INK, width=3))
    fig.add_annotation(
        x=score, y=1.65, text=f"<b>{score:.1f}</b>", showarrow=False,
        font=dict(size=14, color=INK, family=FONT_STACK),
    )
    # An invisible full-width trace carries the hover layer; shapes cannot.
    fig.add_trace(go.Scatter(
        x=list(range(0, 101, 5)), y=[0.5] * 21, mode="markers",
        marker=dict(size=1, color="rgba(0,0,0,0)"),
        hovertemplate="score %{x}<extra></extra>",
    ))
    fig.update_xaxes(
        range=[0, 100], tickvals=[0, 25, 50, 75, 100], showgrid=False,
        zeroline=False, linecolor=AXIS_LINE, tickfont=dict(size=11, color=INK_MUTED),
    )
    fig.update_yaxes(range=[-1.0, 2.1], visible=False)
    caption = f"model band {model_band}"
    if model_band != risk_band:
        caption += f" · policy floored to {risk_band}"
    fig.add_annotation(x=0, y=2.0, text=caption, showarrow=False, xanchor="left",
                       font=dict(size=11, color=INK_MUTED, family=FONT_STACK))
    return _base_layout(fig, 150)


def _axis_label(factor: dict[str, Any], limit: int = 44) -> str:
    """Code plus a trimmed statement. Trimmed rather than wrapped because two
    of these charts sit side by side; the untrimmed statement stays available
    in the hover and in full in the table view below."""
    statement = factor["statement"]
    if len(statement) > limit:
        statement = statement[: limit - 1].rstrip() + "…"
    return f"{factor['code']} · {statement}"


def factor_chart(factors: list[dict[str, Any]]) -> go.Figure:
    """Diverging horizontal bars: SHAP contribution per reason code.

    Contribution is summed SHAP on the log-odds scale and its sign is the
    direction, so zero is a real midpoint and the two arms genuinely oppose —
    which is what earns a diverging encoding here rather than a single hue.
    """
    ordered = sorted(factors, key=lambda f: f["contribution"])
    labels = [_axis_label(f) for f in ordered]
    values = [f["contribution"] for f in ordered]
    colours = [RAISES_RISK if v > 0 else LOWERS_RISK for v in values]
    dimensions = [f.get("dimension", "") for f in ordered]

    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker=dict(color=colours, cornerradius=4),
        text=[f"{v:+.3f}" for v in values],
        textposition="outside",
        textfont=dict(size=11, color=INK_SECONDARY, family=FONT_STACK),
        cliponaxis=False,
        customdata=[[d, f["statement"]] for d, f in zip(dimensions, ordered, strict=True)],
        hovertemplate="<b>%{customdata[1]}</b><br>contribution %{x:+.4f} (log-odds)"
                      "<br>dimension: %{customdata[0]}<extra></extra>",
    ))
    # Generous headroom on both arms: these charts sit two-to-a-row, so an
    # outside value label on a negative bar has little space before it runs
    # into the tick labels in the gutter. Widening the range buys that gap.
    span = max((abs(v) for v in values), default=1.0) * 1.95 or 1.0
    fig.update_xaxes(
        range=[-span, span], gridcolor=GRIDLINE, griddash="solid", zeroline=True,
        zerolinecolor=AXIS_LINE, zerolinewidth=2, linecolor=AXIS_LINE,
        title=dict(text="← lowers risk    ·    raises risk →",
                   font=dict(size=11, color=INK_MUTED, family=FONT_STACK)),
        tickfont=dict(size=11, color=INK_MUTED),
    )
    # automargin lets plotly reserve whatever the tick labels actually need, so a
    # long reason statement widens the gutter instead of overrunning the plot.
    fig.update_yaxes(showgrid=False, zeroline=False, linecolor=AXIS_LINE,
                     automargin=True, tickfont=dict(size=12, color=INK_SECONDARY))
    fig.update_layout(bargap=0.45)
    return _base_layout(fig, max(190, 42 * len(ordered) + 90))


def factor_frame(factors: list[dict[str, Any]]) -> pd.DataFrame:
    """The table-view twin every chart here carries — exact values, no colour."""
    if not factors:
        return pd.DataFrame(columns=["code", "category", "dimension", "direction", "contribution", "statement"])
    frame = pd.DataFrame(factors)
    columns = [c for c in ("code", "category", "dimension", "direction", "contribution", "statement") if c in frame]
    return frame[columns].sort_values("contribution", key=lambda s: s.abs(), ascending=False, ignore_index=True)


def render_factor_block(factors: list[dict[str, Any]], empty_note: str) -> None:
    if not factors:
        st.caption(empty_note)
        return
    st.plotly_chart(factor_chart(factors), use_container_width=True, config={"displayModeBar": False})
    st.markdown(
        f'<span style="color:{RAISES_RISK};font-weight:600;">■</span> '
        f'<span style="color:{INK_SECONDARY};font-size:0.85rem;">raises risk</span>'
        f'&nbsp;&nbsp;&nbsp;<span style="color:{LOWERS_RISK};font-weight:600;">■</span> '
        f'<span style="color:{INK_SECONDARY};font-size:0.85rem;">lowers risk</span>',
        unsafe_allow_html=True,
    )
    with st.expander("Table view (exact values)"):
        st.dataframe(factor_frame(factors), use_container_width=True, hide_index=True)


def render_fired_rules(rules: list[dict[str, Any]]) -> None:
    st.markdown("##### Policy rules that fired")
    if not rules:
        st.caption("No deterministic policy rule matched this customer.")
        return
    st.caption(
        "Kept separate from the model factors above on purpose: a rule is a "
        "pass/fail policy override, not a learned contribution."
    )
    st.dataframe(
        pd.DataFrame(rules)[["id", "reason_code", "description", "floor_band", "require_review"]],
        use_container_width=True, hide_index=True,
    )


def render_result_header(result: dict[str, Any]) -> None:
    score = result["risk_score"]
    left, right = st.columns([2, 3], gap="large")
    with left:
        st.markdown(
            f'<div style="font-size:3.4rem;line-height:1.05;font-weight:650;color:{INK};'
            f'font-family:{FONT_STACK};">{score:.1f}</div>'
            f'<div style="color:{INK_MUTED};font-size:0.85rem;margin-bottom:8px;'
            f'font-family:{FONT_STACK};">composite risk score (0-100)</div>'
            + band_chip(result["risk_band"], "band"),
            unsafe_allow_html=True,
        )
    with right:
        a, b, c = st.columns(3)
        a.metric("Credit default (12m)", f"{result['credit']['probability']:.2%}")
        b.metric("Financial crime (12m)", f"{result['financial_crime']['probability']:.2%}")
        c.metric("Latency", f"{result['latency_ms']:.0f} ms")

    st.plotly_chart(
        score_position_strip(score, result["model_band"], result["risk_band"]),
        use_container_width=True, config={"displayModeBar": False},
    )

    flags = []
    if result.get("band_floor_applied"):
        flags.append("A policy rule raised this band above the model's own reading.")
    if result.get("requires_review"):
        flags.append("Flagged for human review by policy.")
    if result.get("degraded"):
        flags.append(
            "**Degraded**: narrative text was supplied but no extraction ran, so this "
            "score is tabular-only. The request did not fail — the gap is recorded."
        )
    for flag in flags:
        st.warning(flag)

    st.caption(
        f"model `{result['model_version']}` · policy v{result['policy_version']} · "
        f"scored {result['scored_at']}"
    )


def render_profile_summary(entry: dict[str, Any]) -> None:
    p = entry["profile"]
    st.markdown(
        f"**Segment** {p.get('segment', '—')}&nbsp;&nbsp;·&nbsp;&nbsp;"
        f"**Occupation** {p.get('occupation', '—')}&nbsp;&nbsp;·&nbsp;&nbsp;"
        f"**Employment** {p.get('employment_status', '—')}&nbsp;&nbsp;·&nbsp;&nbsp;"
        f"**Residency** {p.get('residency_status', '—')} ({p.get('country_of_residence', '—')})"
        f"&nbsp;&nbsp;·&nbsp;&nbsp;**Age** {p.get('age', '—')}"
        f"&nbsp;&nbsp;·&nbsp;&nbsp;**Account age** {p.get('account_age_months', '—')} mo"
        f"&nbsp;&nbsp;·&nbsp;&nbsp;**Products held** {p.get('num_products_held', '—')}",
        unsafe_allow_html=True,
    )
    st.caption(f"Source profile: {entry.get('archetype', 'custom')}")


_DECISION_ICON = {"approved": "✅", "escalated_aml": "🚨", "kyc_requested": "📋", "blocked": "⛔"}


def render_timeline(entries: list[dict[str, Any]]) -> None:
    """Newest first. Combines score events, pushed transactions/AML events and
    case decisions into one chronological feed — the note text is the one
    piece of this that is operator-typed free text, so it is HTML-escaped
    before going into an ``unsafe_allow_html`` block; everything else here is
    an enum-like value this module controls."""
    if not entries:
        st.caption("No activity recorded yet.")
        return
    for item in sorted(entries, key=lambda e: _parse_dt(e["at"]), reverse=True):
        at = _parse_dt(item["at"]).strftime("%Y-%m-%d %H:%M UTC")
        kind = item["kind"]
        if kind == "scored":
            icon = "📊"
            text = f"<b>Scored</b> — {item['risk_band']} band, {item['risk_score']:.1f} — {html.escape(item.get('note', ''))}"
        elif kind == "event":
            status_txt = "re-scored" if item["rescored"] else item["reason"]
            change = "  ·  <b>band changed</b>" if item.get("band_changed") else ""
            icon = "🔔"
            text = f"<b>Event</b> <code>{item['event_type']}</code> (amount {item['amount']:,.0f}) — {status_txt}{change}"
        else:
            icon = _DECISION_ICON.get(item["action"], "📌")
            text = (f"<b>{STATUS_LABEL.get(item['action'], item['action'])}</b> by {html.escape(item['actor'])}"
                    f" — &ldquo;{html.escape(item['note'])}&rdquo;")
        st.markdown(
            f'<div style="padding:8px 0;border-bottom:1px solid {HAIRLINE};">'
            f'<span style="color:{INK_MUTED};font-size:0.8rem;">{at}</span><br>'
            f'<span style="font-family:{FONT_STACK};font-size:0.92rem;color:{INK};">{icon} {text}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )


def build_customer_payload(values: dict[str, Any], unknown: list[str]) -> dict[str, Any]:
    """Drop the fields the operator marked unknown.

    An omitted field is not a zero. The API treats it as genuinely missing and
    the pipeline's missing-value machinery keeps it that way, which is the
    whole reason this control exists rather than defaulting everything to 0.
    """
    return {k: v for k, v in values.items() if k not in unknown and v is not None}


# --------------------------------------------------------------------------
# Workflow state — the Operations Queue. Session-local (see module docstring).
# --------------------------------------------------------------------------


def add_to_queue(
    customer_id: str, profile: dict[str, Any], narratives: dict[str, Any],
    result: dict[str, Any], *, archetype: str = "custom", note: str = "Initial scoring",
) -> None:
    existing = st.session_state.queue.get(customer_id)
    timeline = existing["timeline"] if existing else []
    timeline.append({
        "kind": "scored", "at": dt.datetime.now(dt.UTC),
        "risk_score": result["risk_score"], "risk_band": result["risk_band"], "note": note,
    })
    st.session_state.queue[customer_id] = {
        "customer_id": customer_id,
        "archetype": archetype,
        "profile": profile,
        "narratives": narratives,
        "result": result,
        "status": existing["status"] if existing else "pending_review",
        "timeline": timeline,
    }


def seed_queue(per_archetype: int = 6) -> tuple[int, list[str]]:
    """Score the synthetic demo book against the live API and populate the
    queue. Returns (customers scored, error messages) — a per-customer
    failure is skipped rather than aborting the whole batch."""
    seed = st.session_state.get("book_seed", 42)
    book = generate_book(seed, per_archetype=per_archetype)
    snapshot = dt.date.today().isoformat()
    errors: list[str] = []
    succeeded = 0
    for candidate in book:
        payload = {
            "customer": {"customer_id": candidate["customer_id"], "snapshot_date": snapshot, **candidate["values"]},
            "narratives": candidate["narratives"],
            "explain": True,
            "audience": "internal",
        }
        try:
            result = api_score(payload)
        except ApiError as exc:
            errors.append(f"{candidate['customer_id']}: {exc}")
            continue
        add_to_queue(candidate["customer_id"], candidate["values"], candidate["narratives"], result,
                     archetype=candidate["archetype"])
        succeeded += 1
    return succeeded, errors


def record_event(customer_id: str, event: dict[str, Any], outcome: dict[str, Any]) -> None:
    entry = st.session_state.queue.get(customer_id)
    if entry is None:
        return
    entry["timeline"].append({
        "kind": "event", "at": dt.datetime.now(dt.UTC), "event_type": event["event_type"],
        "amount": event["amount"], "reason": outcome["reason"], "rescored": outcome["rescored"],
        "band_changed": outcome["band_changed"],
    })
    if outcome["rescored"] and outcome.get("result"):
        entry["result"] = outcome["result"]


def compute_kpis(queue: dict[str, dict[str, Any]]) -> dict[str, int]:
    now = dt.datetime.now(dt.UTC)
    high_risk_pending = sla_breaches = escalated = 0
    for entry in queue.values():
        band = entry["result"]["risk_band"]
        status = entry["status"]
        if status == "pending_review" and band in ("High", "Extreme"):
            high_risk_pending += 1
        if status == "pending_review":
            due = _parse_dt(entry["result"]["scored_at"]) + dt.timedelta(hours=SLA_HOURS.get(band, 72))
            if now > due:
                sla_breaches += 1
        if status == "escalated_aml":
            escalated += 1
    return {"total": len(queue), "high_risk_pending": high_risk_pending,
            "sla_breaches": sla_breaches, "escalated": escalated}


def queue_dataframe(
    queue: dict[str, dict[str, Any]], search: str, bands: list[str], statuses: list[str],
) -> pd.DataFrame:
    now = dt.datetime.now(dt.UTC)
    rows = []
    for entry in queue.values():
        result = entry["result"]
        band = result["risk_band"]
        status = entry["status"]
        if bands and band not in bands:
            continue
        if statuses and status not in statuses:
            continue
        haystack = " ".join([
            entry["customer_id"], str(entry["profile"].get("segment", "")),
            str(entry["profile"].get("occupation", "")), str(entry["profile"].get("country_of_residence", "")),
        ]).lower()
        if search and search.lower() not in haystack:
            continue
        scored_at = _parse_dt(result["scored_at"])
        due = scored_at + dt.timedelta(hours=SLA_HOURS.get(band, 72))
        breached = status == "pending_review" and now > due
        rows.append({
            "Customer ID": entry["customer_id"],
            "Band": f"{BAND_DOT.get(band, '⚪')} {band}",
            "Score": round(result["risk_score"], 1),
            "Segment": entry["profile"].get("segment", "—"),
            "Country": entry["profile"].get("country_of_residence", "—"),
            "Credit risk": f"{result['credit']['probability']:.1%}",
            "Fin. crime risk": f"{result['financial_crime']['probability']:.1%}",
            "Status": STATUS_LABEL.get(status, status),
            "SLA": "BREACHED" if breached else due.strftime("%Y-%m-%d %H:%M UTC"),
            "Scored": scored_at.strftime("%Y-%m-%d %H:%M UTC"),
            "_band_rank": BAND_RANK.get(band, 9),
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["_band_rank", "Score"], ascending=[True, False], ignore_index=True)


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------


def page_queue() -> None:
    st.title("Risk Operations Queue")
    st.caption(
        "Every customer scored in this session, highest risk first. Score, band and factors come "
        "from the API; case status, SLA and notes are this session's workflow state (see the sidebar)."
    )
    show_flash("queue")

    if not st.session_state.queue:
        st.info("The queue is empty.")
        if st.button("Load demo book (18 synthetic customers)", type="primary"):
            with st.spinner("Scoring demo book against the live API…"):
                ok, errors = seed_queue()
            if ok:
                st.rerun()
            else:
                st.error("Every candidate failed to score — is the API reachable? " + (errors[0] if errors else ""))
        return

    kpis = compute_kpis(st.session_state.queue)
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total scored", kpis["total"])
    k2.metric("High-risk pending review", kpis["high_risk_pending"])
    k3.metric("SLA breaches", kpis["sla_breaches"])
    k4.metric("Escalated to AML", kpis["escalated"])
    st.caption(
        "SLA windows while a case sits at Pending Review (workflow convention, not API policy): "
        f"Extreme {SLA_HOURS['Extreme']}h · High {SLA_HOURS['High']}h · "
        f"Medium {SLA_HOURS['Medium']}h · Low {SLA_HOURS['Low']}h from the scoring timestamp."
    )

    st.divider()
    f1, f2, f3, f4 = st.columns([2.2, 1.3, 1.3, 0.9])
    search = f1.text_input("Search — Customer ID, segment, occupation or country", "")
    bands = f2.multiselect("Risk band", BAND_ORDER, default=list(BAND_ORDER))
    statuses = f3.multiselect(
        "Case status", list(STATUS_LABEL), default=list(STATUS_LABEL), format_func=lambda s: STATUS_LABEL[s]
    )
    f4.markdown("<div style='height:1.85rem'></div>", unsafe_allow_html=True)
    if f4.button("↻ Reload book", use_container_width=True,
                 help="Score a fresh synthetic book and refresh the demo customers in the queue"):
        st.session_state.book_seed = st.session_state.get("book_seed", 42) + 1
        with st.spinner("Scoring…"):
            seed_queue()
        st.rerun()

    frame = queue_dataframe(st.session_state.queue, search, bands, statuses)
    if frame.empty:
        st.caption("No customers match these filters.")
    else:
        display_cols = ["Customer ID", "Band", "Score", "Segment", "Country",
                         "Credit risk", "Fin. crime risk", "Status", "SLA", "Scored"]
        event = st.dataframe(
            frame[display_cols], use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="single-row",
            column_config={"Score": st.column_config.NumberColumn(format="%.1f")},
        )
        st.caption(f"{len(frame)} of {len(st.session_state.queue)} customers shown. Click a row to open Customer 360.")
        if event.selection.rows:
            st.session_state.selected_customer = frame.iloc[event.selection.rows[0]]["Customer ID"]
            st.session_state.nav = "customer360"
            st.rerun()

    st.divider()
    with st.expander("+ Onboard a new customer from a preset"):
        st.caption(
            "Loads a full, realistic pre-configured profile automatically — no manual field entry. "
            "For hands-on control over every field, use the sandbox on the Event Simulator page."
        )
        next_id = f"CUS-{100000 + len(st.session_state.queue) + 1}"
        c1, c2, c3 = st.columns([2, 1, 1])
        onboard_preset = c1.selectbox("Profile", list(PRESETS), key="onboard_preset")
        onboard_id = c2.text_input("Customer ID", value=next_id, key="onboard_id")
        c3.markdown("<div style='height:1.85rem'></div>", unsafe_allow_html=True)
        if c3.button("Score & add", type="primary", use_container_width=True, key="onboard_submit"):
            base = PRESETS[onboard_preset]
            values = {k: v for k, v in base.items() if k != "_narratives"}
            payload = {
                "customer": {"customer_id": onboard_id, "snapshot_date": dt.date.today().isoformat(), **values},
                "narratives": base["_narratives"],
                "explain": True,
                "audience": "internal",
            }
            try:
                with st.spinner("Scoring…"):
                    result = api_score(payload)
            except ApiError as exc:
                st.error(str(exc))
            else:
                add_to_queue(onboard_id, values, base["_narratives"], result, archetype=onboard_preset)
                st.session_state.selected_customer = onboard_id
                flash_success("queue", f"{onboard_id} scored — {result['risk_band']} band — added to the queue.")
                st.rerun()


def render_action_panel(entry: dict[str, Any]) -> None:
    st.markdown("##### Case decision")
    show_flash(f"decision_{entry['customer_id']}")
    st.markdown(status_chip(entry["status"]), unsafe_allow_html=True)
    if entry["status"] != "pending_review":
        st.caption("This case has already been actioned. Recording another decision below updates the "
                   "status again and appends to the timeline.")

    cid = entry["customer_id"]
    with st.form(f"decision_form_{cid}"):
        note = st.text_area(
            "Review note (required)",
            placeholder="Document the reason for this decision — required for the audit trail.",
            height=90, key=f"decision_note_{cid}",
        )
        b1, b2 = st.columns(2)
        b3, b4 = st.columns(2)
        approve = b1.form_submit_button("✅ Approve Customer", use_container_width=True)
        escalate = b2.form_submit_button("🚨 Escalate to AML", use_container_width=True)
        kyc = b3.form_submit_button("📋 Request KYC Verification", use_container_width=True)
        block = b4.form_submit_button("⛔ Block Account", use_container_width=True)

    action = None
    if approve:
        action = "approved"
    elif escalate:
        action = "escalated_aml"
    elif kyc:
        action = "kyc_requested"
    elif block:
        action = "blocked"

    if action:
        if not note.strip():
            st.error("A review note is required before recording this decision.")
        else:
            entry["status"] = action
            entry["timeline"].append({
                "kind": "decision", "at": dt.datetime.now(dt.UTC), "action": action,
                "note": note.strip(), "actor": st.session_state.get("reviewer_name", "Risk Analyst"),
            })
            flash_success(f"decision_{cid}", f"Recorded: {STATUS_LABEL[action]}.")
            st.rerun()


def render_explainability(customer_id: str) -> None:
    st.caption(
        "The stored explanation for this customer's most recent score — read back from the API rather "
        "than recomputed, so this is provably the same event as the score above."
    )
    try:
        internal = api_explain(customer_id, "internal")
        customer_view = api_explain(customer_id, "customer")
    except ApiError as exc:
        st.error(str(exc))
        return

    visible_codes = {f["code"] for f in customer_view["top_factors"]} | {f["code"] for f in customer_view["protective_factors"]}
    all_factors = internal["top_factors"] + internal["protective_factors"]
    suppressed = [f for f in all_factors if f["code"] not in visible_codes]
    customer_all = customer_view["top_factors"] + customer_view["protective_factors"]

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown(f"##### Internal reviewer · {len(all_factors)} codes")
        render_factor_block(all_factors, "No reason codes on record.")
    with right:
        st.markdown(f"##### Customer-facing · {len(customer_all)} codes")
        render_factor_block(customer_all, "No reason code on this decision may be shown to the customer.")

    visible_rule_ids = {r["id"] for r in customer_view["fired_rules"]}
    suppressed_rules = [r for r in internal["fired_rules"] if r["id"] not in visible_rule_ids]

    if suppressed or suppressed_rules:
        parts = []
        if suppressed:
            parts.append(f"**{len(suppressed)} reason code(s) withheld:** " + ", ".join(f"`{f['code']}`" for f in suppressed))
        if suppressed_rules:
            parts.append(f"**{len(suppressed_rules)} policy rule(s) withheld:** " + ", ".join(f"`{r['id']}`" for r in suppressed_rules))
        st.warning("Not shown to the customer — " + "  \n".join(parts))
        if suppressed:
            st.dataframe(factor_frame(suppressed), use_container_width=True, hide_index=True)
        if suppressed_rules:
            st.dataframe(pd.DataFrame(suppressed_rules)[["id", "reason_code", "description", "floor_band"]],
                         use_container_width=True, hide_index=True)
    else:
        st.success("Every reason code and rule on this decision is disclosable to the customer.")

    with st.expander("Filter all reason codes"):
        frame = factor_frame(all_factors)
        if frame.empty:
            st.caption("No reason codes on this decision.")
        else:
            frame["customer_visible"] = frame["code"].isin(visible_codes)
            f1, f2, f3 = st.columns(3)
            dimensions = f1.multiselect("Dimension", sorted(frame["dimension"].unique()),
                                        default=list(sorted(frame["dimension"].unique())), key=f"dim_{customer_id}")
            directions = f2.multiselect("Direction", sorted(frame["direction"].unique()),
                                        default=list(sorted(frame["direction"].unique())), key=f"dir_{customer_id}")
            audiences = f3.multiselect("Visibility", ["customer-visible", "internal-only"],
                                       default=["customer-visible", "internal-only"], key=f"vis_{customer_id}")
            mask = frame["dimension"].isin(dimensions) & frame["direction"].isin(directions)
            wanted = [v for v, name in ((True, "customer-visible"), (False, "internal-only")) if name in audiences]
            mask &= frame["customer_visible"].isin(wanted)
            filtered = frame[mask]
            st.dataframe(filtered, use_container_width=True, hide_index=True)
            st.caption(f"{len(filtered)} of {len(frame)} reason codes shown.")

    render_fired_rules(internal["fired_rules"])


def page_customer360() -> None:
    cid = st.session_state.get("selected_customer")
    entry = st.session_state.queue.get(cid) if cid else None
    if entry is None:
        st.title("Customer 360 & Decision Center")
        st.info("No customer selected. Pick one from the Risk Operations Queue.")
        if st.button("← Back to queue"):
            st.session_state.nav = "queue"
            st.rerun()
        return

    head_l, head_r = st.columns([5, 1])
    head_l.title(f"Customer 360 — {cid}")
    if head_r.button("← Back to queue", use_container_width=True):
        st.session_state.nav = "queue"
        st.rerun()

    render_profile_summary(entry)
    st.markdown(status_chip(entry["status"]), unsafe_allow_html=True)
    st.divider()

    render_result_header(entry["result"])

    st.divider()
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("#### Event timeline")
        render_timeline(entry["timeline"])
    with right:
        render_action_panel(entry)

    st.divider()
    st.markdown("### Explainability engine")
    render_explainability(cid)


def page_simulator() -> None:
    st.title("Real-Time Event Simulator & Sandbox")
    st.caption("For testing rules and the API directly — push a single event against any customer on "
               "record, or score a fully custom payload without touching the Operations Queue.")

    tab_event, tab_sandbox = st.tabs(["Push an event", "Sandbox: score a custom payload"])

    # ---- push an event ----------------------------------------------------
    with tab_event:
        st.markdown(
            "Push a single event and watch the re-scoring engine decide. The caller does **not** resend "
            "the customer's profile — the engine rebuilds the input from the last stored snapshot plus "
            "the event log, which is the whole point of the endpoint."
        )
        st.caption(
            "The customer must already have a score on record — onboard them first (Queue page or the "
            "sandbox tab). Trigger thresholds live in the policy file; the `reason` in the response is "
            "the authoritative account of what happened."
        )

        queue_ids = list(st.session_state.queue)
        options = queue_ids + ["— type a different ID —"]
        default_idx = options.index(st.session_state["selected_customer"]) \
            if st.session_state.get("selected_customer") in queue_ids else 0
        picked = st.selectbox("Customer", options, index=default_idx, key="sim_customer_picker")
        event_customer = (
            st.text_input("Customer ID", value=st.session_state.get("selected_customer") or "CUS-DEMO-001",
                          key="sim_customer_manual")
            if picked == "— type a different ID —" else picked
        )

        with st.form("event_form"):
            c1, c2, c3 = st.columns(3)
            event_type = c1.selectbox(
                "Event type", EVENT_TYPES,
                format_func=lambda t: f"{t}  ·  usually a trigger" if t in LIKELY_TRIGGERS else t,
            )
            amount = c2.number_input("Amount", 0.0, 100_000_000.0, 75_000.0, step=1_000.0)
            counterparty = c3.selectbox("Counterparty country", COUNTRIES)
            c1, c2 = st.columns(2)
            channel = c1.selectbox("Channel", ["online", "branch", "mobile", "atm", "wire"])
            minutes_ago = c2.slider("Occurred (minutes ago)", 0, 720, 0)
            send_event = st.form_submit_button("Send event", type="primary")

        if send_event:
            event = {
                "event_ts": (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=minutes_ago)).isoformat(),
                "event_type": event_type,
                "amount": amount,
                "counterparty_country": counterparty,
                "channel": channel,
            }
            # Read the standing score first so the effect of the event can be shown
            # as a before/after rather than a bare number with nothing to compare to.
            try:
                before = api_explain(event_customer, "internal")
            except ApiError:
                before = None

            try:
                with st.spinner("Sending…"):
                    outcome = api_event(event_customer, event)
            except ApiError as exc:
                st.error(str(exc))
            else:
                record_event(event_customer, event, outcome)
                st.session_state.selected_customer = event_customer
                st.divider()
                reason = outcome["reason"]
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Outcome", reason)
                c2.metric("Re-scored", "yes" if outcome["rescored"] else "no")
                c3.metric("Band changed", "yes" if outcome["band_changed"] else "no")
                c4.metric("Notified", "yes" if outcome["notified"] else "no")
                st.caption(REASON_HELP.get(reason, ""))

                if outcome["rescored"] and outcome.get("result"):
                    result = outcome["result"]
                    st.divider()
                    st.markdown(f"##### New score after `{outcome['triggered_by']}`")

                    if before is not None:
                        d1, d2, d3 = st.columns(3)
                        delta = result["risk_score"] - before["risk_score"]
                        d1.metric("Score before", f"{before['risk_score']:.1f}")
                        d2.metric("Score after", f"{result['risk_score']:.1f}", delta=f"{delta:+.1f}")
                        d3.markdown(
                            f"<div style='color:{INK_MUTED};font-size:0.8rem;margin-bottom:6px;'>Band</div>"
                            + band_chip(before["risk_band"]) + " → " + band_chip(result["risk_band"]),
                            unsafe_allow_html=True,
                        )

                        before_codes = {f["code"] for f in before["top_factors"]}
                        after_codes = {f["code"] for f in result["top_factors"]}
                        dropped_text = {c for c in before_codes - after_codes if c.startswith("TX")}
                        if dropped_text:
                            st.warning(
                                "**The narrative-derived factor(s) "
                                + ", ".join(f"`{c}`" for c in sorted(dropped_text))
                                + " are absent from this re-score.** An event re-score rebuilds "
                                "the input from the customer's stored snapshot plus the event log, "
                                "and narrative notes are not part of that snapshot — so text signal "
                                "present in the original `/score` call does not carry over. Note the "
                                "response is *not* marked `degraded`: no narratives were supplied to "
                                "this call, and by design that counts as a normal request rather than "
                                "a failed extraction. Worth knowing before reading the delta above as "
                                "a real change in the customer's risk."
                            )

                    render_result_header(result)
                    st.markdown("##### Why — top factors (internal view)")
                    render_factor_block(
                        result["top_factors"],
                        "No factor cleared the policy's minimum contribution threshold.",
                    )
                    render_fired_rules(result["fired_rules"])
                elif reason == "not_yet_scored":
                    st.info(
                        "The event was stored. Onboard this customer first (Queue page, or the sandbox "
                        "tab), then send the event again."
                    )
                else:
                    st.info("The event was stored, but no new score was computed — see the reason above.")

    # ---- sandbox ------------------------------------------------------------
    with tab_sandbox:
        show_flash("sandbox")
        st.markdown(
            "Full manual control over every field the API accepts — a tool for testing rules and the API "
            "directly, not the primary way to bring a customer into the queue. **Anything left marked "
            "unknown is sent as a genuine missing value**, never as a zero."
        )

        preset_name = st.selectbox("Start from a profile", list(PRESETS), key="sandbox_preset")
        preset = PRESETS[preset_name]
        preset_narratives = preset["_narratives"]

        with st.form("sandbox_score_form"):
            head_a, head_b, head_c = st.columns([2, 1, 1])
            # Deliberately not defaulted from st.session_state.selected_customer: a
            # sandbox test is meant to be disposable, and pre-filling the ID of
            # whichever customer the operator was just looking at would make it
            # one careless "Add to Queue" click away from silently overwriting
            # that customer's real profile, score and timeline under the same ID.
            default_sandbox_id = f"CUS-SANDBOX-{len(st.session_state.queue) + 1:03d}"
            customer_id = head_a.text_input("Customer ID", value=default_sandbox_id)
            snapshot_date = head_b.date_input("Snapshot date", value=dt.date.today())
            audience = head_c.selectbox(
                "Audience", ["internal", "customer"],
                help="`customer` suppresses reason codes that may not lawfully be disclosed "
                     "to the subject — AML concerns, prior SARs, adverse media.",
            )

            values: dict[str, Any] = {}

            with st.expander("Profile", expanded=True):
                c1, c2, c3, c4 = st.columns(4)
                values["segment"] = c1.selectbox("Segment", SEGMENTS, index=SEGMENTS.index(preset["segment"]))
                values["occupation"] = c2.selectbox("Occupation", OCCUPATIONS, index=OCCUPATIONS.index(preset["occupation"]))
                values["employment_status"] = c3.selectbox(
                    "Employment", EMPLOYMENT_STATUSES, index=EMPLOYMENT_STATUSES.index(preset["employment_status"]))
                values["age"] = c4.slider("Age", 18, 95, preset["age"])
                c1, c2, c3, c4 = st.columns(4)
                values["years_at_employer"] = c1.slider("Years at employer", 0.0, 40.0, preset["years_at_employer"], 0.5)
                values["account_age_months"] = c2.slider("Account age (months)", 0, 480, preset["account_age_months"])
                values["residency_status"] = c3.selectbox(
                    "Residency", RESIDENCY_STATUSES, index=RESIDENCY_STATUSES.index(preset["residency_status"]))
                values["country_of_residence"] = c4.selectbox(
                    "Country", COUNTRIES, index=COUNTRIES.index(preset["country_of_residence"]))
                values["num_products_held"] = st.slider("Products held", 0, 15, preset["num_products_held"])

            with st.expander("Income & credit", expanded=True):
                c1, c2, c3, c4 = st.columns(4)
                values["declared_annual_income"] = c1.number_input(
                    "Declared annual income", 0.0, 50_000_000.0, preset["declared_annual_income"], step=10_000.0)
                values["verified_income_ratio"] = c2.slider("Verified income ratio", 0.0, 2.0, preset["verified_income_ratio"], 0.05)
                values["income_volatility_cv"] = c3.slider("Income volatility (CV)", 0.0, 2.0, preset["income_volatility_cv"], 0.02)
                values["bureau_score"] = c4.slider("Bureau score", 300, 850, preset["bureau_score"])
                c1, c2, c3, c4 = st.columns(4)
                values["total_credit_limit"] = c1.number_input(
                    "Total credit limit", 0.0, 20_000_000.0, preset["total_credit_limit"], step=5_000.0)
                values["credit_utilization_ratio"] = c2.slider(
                    "Credit utilisation", 0.0, 2.0, preset["credit_utilization_ratio"], 0.01)
                values["dti_ratio"] = c3.slider("Debt-to-income", 0.0, 2.0, preset["dti_ratio"], 0.01)
                values["savings_to_income_ratio"] = c4.slider(
                    "Savings-to-income", 0.0, 3.0, preset["savings_to_income_ratio"], 0.05)
                c1, c2, c3, c4 = st.columns(4)
                values["num_open_loans"] = c1.slider("Open loans", 0, 20, preset["num_open_loans"])
                values["num_credit_inquiries_12m"] = c2.slider("Credit inquiries (12m)", 0, 30, preset["num_credit_inquiries_12m"])
                values["delinquencies_30d_12m"] = c3.slider("30d delinquencies (12m)", 0, 20, preset["delinquencies_30d_12m"])
                values["delinquencies_90d_24m"] = c4.slider("90d delinquencies (24m)", 0, 20, preset["delinquencies_90d_24m"])
                c1, c2, c3, c4 = st.columns(4)
                values["max_days_past_due_24m"] = c1.slider("Max days past due (24m)", 0, 365, preset["max_days_past_due_24m"])
                values["prior_default_flag"] = c2.selectbox("Prior default", [0, 1], index=preset["prior_default_flag"])
                values["num_bounced_payments_12m"] = c3.slider("Bounced payments (12m)", 0, 30, preset["num_bounced_payments_12m"])
                values["overdraft_events_12m"] = c4.slider("Overdraft events (12m)", 0, 60, preset["overdraft_events_12m"])
                values["balance_volatility"] = st.slider("Balance volatility", 0.0, 2.0, preset["balance_volatility"], 0.02)

            with st.expander("Transaction behaviour"):
                c1, c2, c3, c4 = st.columns(4)
                values["txn_count_90d"] = c1.slider("Transactions (90d)", 0, 2000, preset["txn_count_90d"])
                values["cash_intensity_ratio"] = c2.slider("Cash intensity", 0.0, 1.0, preset["cash_intensity_ratio"], 0.01)
                values["cross_border_txn_ratio"] = c3.slider("Cross-border ratio", 0.0, 1.0, preset["cross_border_txn_ratio"], 0.01)
                values["night_txn_ratio"] = c4.slider("Night-time ratio", 0.0, 1.0, preset["night_txn_ratio"], 0.01)
                c1, c2, c3, c4 = st.columns(4)
                values["structuring_score"] = c1.slider("Structuring score", 0.0, 1.0, preset["structuring_score"], 0.01)
                values["crypto_exposure_ratio_90d"] = c2.slider("Crypto exposure", 0.0, 1.0, preset["crypto_exposure_ratio_90d"], 0.01)
                values["gambling_spend_ratio_90d"] = c3.slider("Gambling spend", 0.0, 1.0, preset["gambling_spend_ratio_90d"], 0.01)
                values["new_counterparty_ratio_90d"] = c4.slider(
                    "New counterparties", 0.0, 1.0, preset["new_counterparty_ratio_90d"], 0.01)

            with st.expander("AML / KYC"):
                c1, c2, c3, c4 = st.columns(4)
                values["pep_flag"] = c1.selectbox("PEP", [0, 1], index=preset["pep_flag"])
                values["sanctions_screen_hits"] = c2.slider("Sanctions hits", 0, 10, preset["sanctions_screen_hits"])
                values["adverse_media_hits_12m"] = c3.slider("Adverse media (12m)", 0, 20, preset["adverse_media_hits_12m"])
                values["offshore_entity_links"] = c4.slider("Offshore links", 0, 20, preset["offshore_entity_links"])
                c1, c2, c3, c4 = st.columns(4)
                values["high_risk_jurisdiction_exposure"] = c1.selectbox(
                    "High-risk jurisdiction", [0, 1], index=preset["high_risk_jurisdiction_exposure"])
                values["medium_risk_jurisdiction_exposure"] = c2.selectbox(
                    "Medium-risk jurisdiction", [0, 1], index=preset["medium_risk_jurisdiction_exposure"])
                values["sar_filed_prior"] = c3.selectbox("Prior SAR", [0, 1], index=preset["sar_filed_prior"])
                values["edd_required"] = c4.selectbox("EDD required", [0, 1], index=preset["edd_required"])
                c1, c2, c3 = st.columns(3)
                values["source_of_funds_declared"] = c1.selectbox(
                    "Source of funds", SOURCE_OF_FUNDS, index=SOURCE_OF_FUNDS.index(preset["source_of_funds_declared"]))
                values["source_of_funds_verified"] = c2.selectbox(
                    "Source verified", [0, 1], index=preset["source_of_funds_verified"])
                values["kyc_document_completeness"] = c3.slider(
                    "KYC completeness", 0.0, 1.0, preset["kyc_document_completeness"], 0.05)
                values["kyc_refresh_overdue_days"] = st.slider(
                    "KYC refresh overdue (days)", 0, 1000, preset["kyc_refresh_overdue_days"])

            with st.expander("Narrative notes (free text)"):
                st.caption(
                    "Treated as untrusted input. Text reaches the extractor inside a data "
                    "envelope, and the schema it must answer through has no field that "
                    "means a score or a band — an instruction hidden in a note cannot "
                    "become a decision. Try it."
                )
                n1, n2 = st.columns(2)
                support_call = n1.text_area("Support call summary", preset_narratives["support_call_summary"], height=110)
                underwriter = n2.text_area("Underwriter note", preset_narratives["underwriter_note"], height=110)
                kyc_extract = st.text_area("KYC document extract", preset_narratives["kyc_document_extract"], height=80)

            unknown = st.multiselect(
                "Send these fields as unknown (null, not zero)",
                options=sorted(values),
                help="Demonstrates the missing-data contract: an omitted field reaches "
                     "the model as NaN plus a missing-indicator, never as a fabricated 0.",
            )

            submitted = st.form_submit_button("Score customer", type="primary")

        if submitted:
            narratives = {
                k: v.strip()
                for k, v in (
                    ("support_call_summary", support_call),
                    ("underwriter_note", underwriter),
                    ("kyc_document_extract", kyc_extract),
                )
                if v and v.strip()
            }
            customer_payload = build_customer_payload(values, unknown)
            payload = {
                "customer": {
                    "customer_id": customer_id,
                    "snapshot_date": snapshot_date.isoformat(),
                    **customer_payload,
                },
                "explain": True,
                "audience": audience,
            }
            if narratives:
                payload["narratives"] = narratives

            try:
                with st.spinner("Scoring…"):
                    result = api_score(payload)
            except ApiError as exc:
                st.error(str(exc))
            else:
                st.session_state.selected_customer = customer_id
                st.session_state.sandbox_last_result = {
                    "customer_id": customer_id, "profile": customer_payload,
                    "narratives": narratives, "result": result, "preset": preset_name,
                }
                st.divider()
                render_result_header(result)
                if unknown:
                    st.caption(f"{len(unknown)} field(s) sent as unknown: {', '.join(sorted(unknown))}")
                st.divider()
                st.markdown(f"##### Why — top factors ({audience} view)")
                render_factor_block(
                    result["top_factors"],
                    "No factor cleared the policy's minimum contribution threshold.",
                )
                render_fired_rules(result["fired_rules"])

        last = st.session_state.get("sandbox_last_result")
        if last:
            st.divider()
            if last["customer_id"] in st.session_state.queue:
                st.warning(
                    f"`{last['customer_id']}` already exists in the queue — adding will overwrite its "
                    "profile, score and timeline with this sandbox result. Its case status and prior "
                    "decisions are preserved."
                )
            if st.button("➕ Add this result to the Operations Queue", key="sandbox_add_to_queue"):
                add_to_queue(last["customer_id"], last["profile"], last["narratives"], last["result"],
                             archetype=f"sandbox ({last['preset']})")
                st.session_state.sandbox_last_result = None
                flash_success("sandbox", f"{last['customer_id']} added to the queue.")
                st.rerun()


# --------------------------------------------------------------------------
# App shell
# --------------------------------------------------------------------------

st.set_page_config(page_title="Customer Risk Rating", page_icon="◑", layout="wide")

st.markdown(
    f"""
    <style>
      .stApp {{ background: {PAGE_PLANE}; }}
      div[data-testid="stMetricValue"] {{ font-family: {FONT_STACK}; color: {INK}; }}
      div[data-testid="stMetricLabel"] {{ color: {INK_MUTED}; }}
      .block-container {{ padding-top: 2.2rem; max-width: 1400px; }}
      hr {{ border-color: {HAIRLINE}; }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.session_state.setdefault("queue", {})
st.session_state.setdefault("selected_customer", None)
st.session_state.setdefault("nav", "queue")
st.session_state.setdefault("book_load_attempted", False)
st.session_state.setdefault("book_seed", 42)
st.session_state.setdefault("reviewer_name", "Risk Analyst")

with st.sidebar:
    st.markdown("### Customer Risk Rating")
    st.caption("Enterprise Risk & Compliance Workflow")
    st.caption(f"API: `{API_URL}`")
    api_up = False
    try:
        health = api_health()
        st.success(
            f"API healthy · models: {', '.join(health['models_loaded'])} · "
            f"policy v{health['policy_version']} · api {health['version']}"
        )
        api_up = True
    except ApiError as exc:
        st.error(str(exc))
        st.caption(
            "Start it with `python scripts/serve.py`, or set `CRR_API_URL` to a "
            "running instance. Every page below needs it."
        )

    st.divider()
    st.text_input("Reviewer name", key="reviewer_name",
                  help="Attributed on every case decision you record below — session-local, no login.")

    st.divider()
    st.markdown("##### Navigate")
    for key, label in (
        ("queue", "📋 Risk Operations Queue"),
        ("customer360", "🔎 Customer 360 & Decision Center"),
        ("simulator", "🧪 Event Simulator & Sandbox"),
    ):
        disabled = key == "customer360" and not st.session_state.get("selected_customer")
        if st.button(label, key=f"nav_{key}", use_container_width=True,
                     type="primary" if st.session_state.nav == key else "secondary", disabled=disabled):
            st.session_state.nav = key
            st.rerun()

    st.divider()
    st.caption(
        "Scores, explanations and re-scoring all come from the live FastAPI service over its public "
        "endpoints — no model or policy logic is duplicated here. Case status, SLA and review notes are "
        "this session's workflow state only: nothing here is written to a case-management database."
    )

if api_up and not st.session_state.book_load_attempted:
    st.session_state.book_load_attempted = True
    with st.spinner("Loading risk operations queue — scoring demo book against the live API…"):
        seed_queue()

if st.session_state.nav == "customer360":
    page_customer360()
elif st.session_state.nav == "simulator":
    page_simulator()
else:
    page_queue()
