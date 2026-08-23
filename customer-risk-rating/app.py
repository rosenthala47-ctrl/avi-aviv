"""Streamlit front end for the Customer Risk Rating API.

A thin HTTP client, deliberately. Everything shown here comes from the live
FastAPI service over the documented endpoints — this module holds no model, no
policy, no scoring logic and no copy of the reason-code vocabulary. That keeps
the demo honest: if the number on screen is wrong, the API is wrong, and there
is no second implementation in the UI layer that could quietly disagree with
the one a real integrator would call.

    streamlit run app.py

Set ``CRR_API_URL`` to point at a running API (default http://127.0.0.1:8000).
"""

from __future__ import annotations

import datetime as dt
import os
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
# table view below each chart repeats the value).
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

FONT_STACK = 'system-ui, -apple-system, "Segoe UI", sans-serif'

# Mirrors config/risk_policy.yaml's `bands:` block for the score-position strip
# only. The API remains authoritative for the band a customer actually gets —
# this is drawn from the returned band, never recomputed from these numbers.
BAND_CUTOFFS = {"Low": 25.0, "Medium": 50.0, "High": 75.0, "Extreme": 100.0}

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
    "not_yet_scored": "No score on record yet. Score the customer once on the Score tab first.",
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
# control: an omitted field reaches the model as a genuine missing value, not
# as a fabricated zero.
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


def build_customer_payload(values: dict[str, Any], unknown: list[str]) -> dict[str, Any]:
    """Drop the fields the operator marked unknown.

    An omitted field is not a zero. The API treats it as genuinely missing and
    the pipeline's missing-value machinery keeps it that way, which is the
    whole reason this control exists rather than defaulting everything to 0.
    """
    return {k: v for k, v in values.items() if k not in unknown and v is not None}


# --------------------------------------------------------------------------
# Page
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

if "last_customer_id" not in st.session_state:
    st.session_state.last_customer_id = "CUS-DEMO-001"

with st.sidebar:
    st.markdown("### Customer Risk Rating")
    st.caption(f"API: `{API_URL}`")
    try:
        health = api_health()
        st.success(
            f"API healthy · models: {', '.join(health['models_loaded'])} · "
            f"policy v{health['policy_version']} · api {health['version']}"
        )
    except ApiError as exc:
        st.error(str(exc))
        st.caption(
            "Start it with `python scripts/serve.py`, or set `CRR_API_URL` to a "
            "running instance. Every tab below needs it."
        )
    st.divider()
    st.caption(
        "This page is a front end only. Scores, explanations and re-scoring all "
        "come from the FastAPI service over its public endpoints — no model or "
        "policy logic is duplicated here."
    )

st.title("Customer Risk Rating")

tab_score, tab_explain, tab_events = st.tabs(
    ["Score a customer", "Explanation & reason codes", "Real-time re-scoring"]
)

# ---- Tab 1: score --------------------------------------------------------

with tab_score:
    st.markdown(
        "Fill in what you know. **Anything you leave marked unknown is sent as a "
        "genuine missing value**, never as a zero — a customer whose utilisation "
        "we simply do not have must not be scored as if it were 0%."
    )

    preset_name = st.selectbox("Start from a profile", list(PRESETS), key="preset")
    preset = PRESETS[preset_name]
    preset_narratives = preset["_narratives"]

    with st.form("score_form"):
        head_a, head_b, head_c = st.columns([2, 1, 1])
        customer_id = head_a.text_input("Customer ID", value=st.session_state.last_customer_id)
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
        payload = {
            "customer": {
                "customer_id": customer_id,
                "snapshot_date": snapshot_date.isoformat(),
                **build_customer_payload(values, unknown),
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
            st.session_state.last_customer_id = customer_id
            st.session_state.last_audience = audience
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
            st.info(
                "Open the **Explanation & reason codes** tab to compare what an "
                "internal reviewer sees against what may be shown to the customer."
            )

# ---- Tab 2: explanation & reason codes -----------------------------------

with tab_explain:
    st.markdown(
        "The **stored** explanation for a customer's most recent score — read back "
        "from the API rather than recomputed, so the explanation shown to a reviewer "
        "is provably the same event as the score that was served."
    )

    c1, c2 = st.columns([3, 1])
    explain_id = c1.text_input("Customer ID", value=st.session_state.last_customer_id, key="explain_id")
    fetch = c2.button("Fetch explanation", type="primary", use_container_width=True)

    if fetch or st.session_state.get("explain_loaded") == explain_id:
        try:
            internal = api_explain(explain_id, "internal")
            customer_view = api_explain(explain_id, "customer")
        except ApiError as exc:
            st.error(str(exc))
        else:
            st.session_state.explain_loaded = explain_id
            st.divider()

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Risk score", f"{internal['risk_score']:.1f}")
            m2.metric("Credit default", f"{internal['credit_probability']:.2%}")
            m3.metric("Financial crime", f"{internal['financial_crime_probability']:.2%}")
            m4.markdown(
                f"<div style='color:{INK_MUTED};font-size:0.8rem;margin-bottom:6px;'>Band</div>"
                + band_chip(internal["risk_band"]),
                unsafe_allow_html=True,
            )
            st.caption(
                f"model `{internal['model_version']}` · policy v{internal['policy_version']} · "
                f"scored {internal['scored_at']}"
            )

            visible_codes = {f["code"] for f in customer_view["top_factors"]}
            visible_codes |= {f["code"] for f in customer_view["protective_factors"]}
            all_factors = internal["top_factors"] + internal["protective_factors"]
            suppressed = [f for f in all_factors if f["code"] not in visible_codes]

            st.divider()
            st.markdown("#### Customer view vs internal view")
            st.caption(
                "Both views are filtered from one stored record by a single function, so "
                "they can never disagree about what is safe to disclose. A code is "
                "withheld when telling the subject would tip off an AML investigation — "
                "not because it is unflattering."
            )

            customer_all = customer_view["top_factors"] + customer_view["protective_factors"]

            left, right = st.columns(2, gap="large")
            with left:
                st.markdown(f"##### Internal reviewer · {len(all_factors)} codes")
                render_factor_block(all_factors, "No reason codes on record.")
            with right:
                st.markdown(f"##### Customer-facing · {len(customer_all)} codes")
                render_factor_block(
                    customer_all, "No reason code on this decision may be shown to the customer."
                )

            visible_rule_ids = {r["id"] for r in customer_view["fired_rules"]}
            suppressed_rules = [r for r in internal["fired_rules"] if r["id"] not in visible_rule_ids]

            if suppressed or suppressed_rules:
                parts = []
                if suppressed:
                    parts.append(
                        f"**{len(suppressed)} reason code(s) withheld:** "
                        + ", ".join(f"`{f['code']}`" for f in suppressed)
                    )
                if suppressed_rules:
                    parts.append(
                        f"**{len(suppressed_rules)} policy rule(s) withheld:** "
                        + ", ".join(f"`{r['id']}`" for r in suppressed_rules)
                    )
                st.warning("Not shown to the customer — " + "  \n".join(parts))
                if suppressed:
                    st.dataframe(factor_frame(suppressed), use_container_width=True, hide_index=True)
                if suppressed_rules:
                    st.dataframe(
                        pd.DataFrame(suppressed_rules)[["id", "reason_code", "description", "floor_band"]],
                        use_container_width=True, hide_index=True,
                    )
            else:
                st.success("Every reason code and rule on this decision is disclosable to the customer.")

            st.divider()
            st.markdown("#### Filter all reason codes")
            frame = factor_frame(all_factors)
            if frame.empty:
                st.caption("No reason codes on this decision.")
            else:
                frame["customer_visible"] = frame["code"].isin(visible_codes)
                f1, f2, f3 = st.columns(3)
                dimensions = f1.multiselect(
                    "Dimension", sorted(frame["dimension"].unique()), default=list(sorted(frame["dimension"].unique())))
                directions = f2.multiselect(
                    "Direction", sorted(frame["direction"].unique()), default=list(sorted(frame["direction"].unique())))
                audiences = f3.multiselect(
                    "Visibility", ["customer-visible", "internal-only"],
                    default=["customer-visible", "internal-only"])

                mask = frame["dimension"].isin(dimensions) & frame["direction"].isin(directions)
                wanted = []
                if "customer-visible" in audiences:
                    wanted.append(True)
                if "internal-only" in audiences:
                    wanted.append(False)
                mask &= frame["customer_visible"].isin(wanted)

                filtered = frame[mask]
                st.dataframe(filtered, use_container_width=True, hide_index=True)
                st.caption(f"{len(filtered)} of {len(frame)} reason codes shown.")

            render_fired_rules(internal["fired_rules"])

# ---- Tab 3: real-time re-scoring -----------------------------------------

with tab_events:
    st.markdown(
        "Push a single event and watch the re-scoring engine decide. The caller does "
        "**not** resend the customer's profile — the engine rebuilds the input from "
        "the last stored snapshot plus the event log, which is the whole point of "
        "the endpoint."
    )
    st.caption(
        "The customer must already have a score on record, so run the **Score a "
        "customer** tab first. Trigger thresholds live in the policy file; the "
        "`reason` in the response is the authoritative account of what happened."
    )

    with st.form("event_form"):
        c1, c2, c3 = st.columns([2, 2, 2])
        event_customer = c1.text_input("Customer ID", value=st.session_state.last_customer_id)
        event_type = c2.selectbox(
            "Event type", EVENT_TYPES,
            format_func=lambda t: f"{t}  ·  usually a trigger" if t in LIKELY_TRIGGERS else t,
        )
        amount = c3.number_input("Amount", 0.0, 100_000_000.0, 75_000.0, step=1_000.0)
        c1, c2, c3 = st.columns(3)
        counterparty = c1.selectbox("Counterparty country", COUNTRIES)
        channel = c2.selectbox("Channel", ["online", "branch", "mobile", "atm", "wire"])
        minutes_ago = c3.slider("Occurred (minutes ago)", 0, 720, 0)
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
        before: dict[str, Any] | None = None
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
                    "The event was stored. Score this customer once on the **Score a "
                    "customer** tab, then send the event again."
                )
            else:
                st.info("The event was stored, but no new score was computed — see the reason above.")
