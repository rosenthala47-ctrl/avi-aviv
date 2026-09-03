"""Streamlit front end for the Customer Risk Rating API — an operations console.

A thin HTTP client to the model, deliberately. Every score, band, SHAP factor
and re-score shown here comes from the live FastAPI service over its documented
endpoints — this module holds no model, no policy and no scoring logic that
could quietly disagree with the API.

The reviewer workflow around those scores — user accounts and login, case
status, review notes and timelines, the Rule Builder's dynamic rules, watchlist
dispositions, and an immutable audit trail — is persisted to a database via the
``crr.workflow`` package (SQLite by default, PostgreSQL via
``CRR_WORKFLOW_DB_URL``). It survives a page refresh, a new tab and a restart;
role is the signed-in account's, read from the database, with no session-state
role switcher. The API still has no case-management endpoints — that layer is
this console's own, and it now has real persistence behind it rather than
``st.session_state`` that evaporated on refresh.

Bilingual (Hebrew default, English toggle) via the ``I18N``/``VOCAB`` tables
and the ``t()``/``vocab_label()`` lookups below — every user-facing string in
this file goes through one of them rather than being hardcoded, so the
sidebar's language selector can switch the whole app on the same rerun. The
API itself is not localised (it always returns English band/status/reason
values); those are translated for DISPLAY only, never for what gets sent back
to the API or used as a lookup key.

    streamlit run app.py

Set ``CRR_API_URL`` to point at a running API (default http://127.0.0.1:8000).
"""

from __future__ import annotations

import datetime as dt
import html
import os
import random
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit.components.v1 import html as components_html

# The workflow store (case management, auth, audit) is real Python that runs
# in-process here, unlike the scoring service which stays behind the HTTP
# boundary. Make the src/ package importable the same way the scripts do.
_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from crr.screening import screen as screen_watchlist  # noqa: E402  (path setup must run first)
from crr.workflow import (  # noqa: E402
    AUTH_HEADER_NAME,
    DEMO_USERS,
    SESSION_COOKIE_NAME,
    WorkflowStore,
    create_session_factory,
    extract_bearer_token,
    resolve_database_url,
)

API_URL = os.environ.get("CRR_API_URL", "http://127.0.0.1:8000").rstrip("/")
REQUEST_TIMEOUT = float(os.environ.get("CRR_UI_TIMEOUT", "30"))
# Same-origin base URL of crr.workflow.gateway's /auth mount — e.g. "/auth"
# behind a reverse proxy that routes that path to the gateway unchanged, or a
# full "https://host/auth" (see deploy/Caddyfile, deploy/nginx.conf; both
# proxy /auth/* straight through with no path rewriting, so this must already
# end in "/auth" to line up with the gateway's own routes, /auth/adopt and
# /auth/logout — the code below appends only "/adopt"/"/logout"). Empty (the
# default, a bare `streamlit run app.py` with no proxy in front) keeps every
# auth flow exactly as it was: a token in the URL query string, no cookie in
# play at all.
AUTH_GATEWAY_URL = os.environ.get("CRR_AUTH_GATEWAY_URL", "").rstrip("/")


@st.cache_resource
def get_store() -> WorkflowStore:
    """Process-wide singleton: one engine + session factory, tables created and
    demo users seeded once. ``@st.cache_resource`` keeps it alive across reruns
    and shares it across browser sessions in the same server process."""
    store = WorkflowStore(create_session_factory(resolve_database_url()))
    store.seed_default_users()
    store.seed_demo_watchlist()
    return store


# --- current-user helpers (the logged-in account is the source of truth for
# role and attribution — there is no session-state role switcher any more) ---


def current_user() -> dict[str, Any]:
    return st.session_state.get("auth_user") or {}


def current_role() -> str:
    return current_user().get("role", "junior_analyst")


def current_actor() -> str:
    """Human-readable attribution for decisions, notes and audit rows."""
    return current_user().get("display_name") or current_user().get("username") or "System"

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

# Bands, status keys, event types etc. below are internal English identifiers
# — used as dict keys, sent to/read from the API, and never translated. Their
# on-screen text goes through band_label()/status_label()/vocab_label() at
# the point of display only.
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
STATUS_KEYS = ("pending_review", "approved", "escalated_aml", "kyc_requested", "blocked")
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

# --------------------------------------------------------------------------
# RBAC — role-based access control. Like case status/SLA above, there is no
# API endpoint or real auth behind this: the sidebar role switcher is a
# session-local role-PLAY, the same honesty already applied to the reviewer
# name field ("attributed... no login"). What it gates is real, though —
# every decide_case/manage_rules call site below actually checks
# has_permission() before acting, not just before showing a button.
#
# A simple two-gate hierarchy, ranked so each role can do everything the
# one below it can plus its own extra capability: Junior Analyst can view
# every page, add a note and request KYC; Risk Manager additionally
# decides cases (approve/escalate/block); Compliance Officer / Admin
# additionally manages the Rule Builder's rules. Nothing needs a third
# capability bucket because nothing in the brief sits between "anyone
# logged in" and those two gated actions.
# --------------------------------------------------------------------------
ROLE_KEYS = ("junior_analyst", "risk_manager", "compliance_admin")
ROLE_RANK = {"junior_analyst": 0, "risk_manager": 1, "compliance_admin": 2}
_CAPABILITY_MIN_ROLE = {"decide_case": "risk_manager", "manage_rules": "compliance_admin"}


def has_permission(capability: str) -> bool:
    role = current_role()
    min_role = _CAPABILITY_MIN_ROLE.get(capability)
    return min_role is None or ROLE_RANK.get(role, 0) >= ROLE_RANK[min_role]


def role_label(role: str) -> str:
    return t(f"role.{role}")


# Shared between the sidebar's language selectbox and audit-log entries so a
# "language switched" row reads "עברית → English" rather than "he → en".
LANGUAGE_LABEL = {"he": "עברית", "en": "English"}

EVENT_TYPES = (
    "missed_payment", "overdraft_breach", "chargeback", "cash_deposit",
    "wire_transfer_out", "crypto_transfer", "card_purchase", "atm_withdrawal",
    "salary_credit", "wire_transfer_in", "direct_debit", "loan_repayment",
)
CHANNELS = ("online", "branch", "mobile", "atm", "wire")
AUDIENCES = ("internal", "customer")
# A hint for the operator, not a rule: the policy decides, and the API's
# `reason` field reports what it actually decided.
LIKELY_TRIGGERS = frozenset(
    {"missed_payment", "overdraft_breach", "chargeback", "cash_deposit",
     "wire_transfer_out", "crypto_transfer"}
)

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
# ISO country codes are language-neutral by construction — the same two/three
# letters read the same in either language, so COUNTRIES needs no VOCAB entry.

# Every field below is optional at the API boundary. What the UI sends as a
# real value versus omits entirely is the whole point of the "unknown fields"
# control on the sandbox tab: an omitted field reaches the model as a genuine
# missing value, not as a fabricated zero. These three profiles double as the
# archetypes the Operations Queue's demo book is jittered from (see
# `generate_book` below) — no fabricated customer names anywhere in this file,
# only the customer_id/segment/occupation fields the real schema carries.
# Keys stay these exact English sentences (used as PRESETS lookups and demo
# customer_id/archetype tags throughout); PRESET_LABEL_HE below is the only
# thing that changes what an operator actually reads on screen.
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
        "expected_vs_actual_turnover_ratio": 1.0, "pass_through_velocity_hours": 240.0,
        "volume_spike_ratio_6m": 1.05, "vpn_or_high_risk_ip_flag": 0, "device_change_frequency_30d": 1,
        "complex_ownership_structure_flag": 0, "recent_ubo_change_flag": 0,
        "cash_to_total_volume_ratio": 0.03, "crypto_vasp_exposure_flag": 0,
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
        "expected_vs_actual_turnover_ratio": 0.85, "pass_through_velocity_hours": 190.0,
        "volume_spike_ratio_6m": 1.2, "vpn_or_high_risk_ip_flag": 0, "device_change_frequency_30d": 2,
        "complex_ownership_structure_flag": 0, "recent_ubo_change_flag": 0,
        "cash_to_total_volume_ratio": 0.10, "crypto_vasp_exposure_flag": 0,
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
        "expected_vs_actual_turnover_ratio": 3.4, "pass_through_velocity_hours": 6.0,
        "volume_spike_ratio_6m": 4.8, "vpn_or_high_risk_ip_flag": 1, "device_change_frequency_30d": 9,
        "complex_ownership_structure_flag": 1, "recent_ubo_change_flag": 1,
        "cash_to_total_volume_ratio": 0.55, "crypto_vasp_exposure_flag": 1,
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

PRESET_LABEL_HE: dict[str, str] = {
    "Low risk — salaried, clean file": "סיכון נמוך — שכיר/ה, תיק נקי",
    "Credit stress — thin buffer, recent arrears": "מצוקת אשראי — כרית דלה, פיגורים אחרונים",
    "AML concern — opaque funds, offshore links": "חשש הלבנת הון — כספים לא שקופים, קשרים אופשוריים",
}

# --------------------------------------------------------------------------
# Watchlist & sanctions screening — reference data and identities.
#
# CustomerPayload (crr/api/schemas.py) has no name or date-of-birth field
# (extra="forbid", and the model was never trained on one) — a full legal
# name is exactly the kind of PII a real scoring API keeps out of its
# feature set. So a customer's name/DOB here is UI-only workflow state,
# generated alongside the demo book the same way case status is: it never
# gets merged into what seed_queue/onboarding/the sandbox actually POST to
# /score (see the explicit exclusion everywhere `values`/`candidate` is
# built into a payload below), only into entry["profile"] for display and
# for this screening module to read.
#
# Watchlist reference data — OFAC/EU/UN sanctions plus PEP/adverse-media —
# now lives in the workflow database (wf_watchlist_entries), not a Python
# constant: crr.workflow.store.WorkflowStore.seed_demo_watchlist() seeds a
# small set of fictional entries on first run, and scripts/refresh_watchlists
# .py replaces a source's rows with a real, freshly parsed OFAC/UN/EU list
# (see crr.screening). This module reads whatever is currently in that table
# via st.session_state.watchlist_entries, hydrated fresh every rerun next to
# the queue/rules/audit log below — see screen_customer().

# Six identities per archetype (matching seed_queue's default per_archetype),
# indexed by position — deterministic, no RNG needed since none of this
# feeds the model. (full_name, date_of_birth) tuples.
_DEMO_IDENTITY_POOL: dict[str, list[tuple[str, str]]] = {
    "Low risk — salaried, clean file": [
        ("Noa Peretz", "1985-05-12"), ("Daniel Cohen", "1978-02-20"), ("Maya Ben-David", "1990-07-08"),
        ("Yossi Mizrahi", "1982-11-15"), ("Tamar Azulay", "1988-03-30"), ("Amit Shalev", "1975-09-01"),
    ],
    "Credit stress — thin buffer, recent arrears": [
        ("Eli Buskila", "1991-06-02"), ("Ronit Malka", "1987-12-11"), ("Avi Peretz", "1993-01-25"),
        ("Shira Amar", "1980-04-17"), ("Moshe Katz", "1979-08-09"), ("Liora Dahan", "1985-10-03"),
    ],
    "AML concern — opaque funds, offshore links": [
        ("Mikael Aslanov", "1975-11-02"),   # near-name + exact-DOB match -> OFAC-2201
        ("Khalid Marwan", "1969-06-19"),    # exact match on EU-3105's alias + DOB
        ("Rustam Aliyev", "1958-09-12"),    # exact match -> PEP-0087
        ("Carlos Mendes", "1972-03-14"),    # clean
        ("Anastasia Popova", "1980-07-22"),  # clean
        ("Hassan Nasser", "1965-05-05"),    # clean
    ],
}

WATCHLIST_CATEGORIES = ("sanctions", "pep", "adverse_media")
WATCHLIST_SOURCES = ("ofac", "eu", "un", "pep", "adverse_media")
# Reuses the existing band palette rather than inventing new hues: a
# watchlist hit's severity is just another "how worried should a reviewer
# be" scale, and Extreme/High/Medium already have colours nobody has to
# relearn.
WATCHLIST_SEVERITY_COLOUR = {"critical": "#d03b3b", "high": "#ec835a", "medium": "#fab219"}


def _watchlist_severity(score: float) -> str:
    if score >= 90:
        return "critical"
    if score >= 75:
        return "high"
    return "medium"


def screen_customer(full_name: str | None, date_of_birth: str | None, country: str | None) -> list[dict[str, Any]]:
    """Fuzzy-match one customer's identity against the watchlist entries
    hydrated into session state for this run (see the "hydrate persisted
    workflow state" block near the bottom of this file). The actual scoring
    (rapidfuzz, DOB/country corroboration bonuses) lives in
    crr.screening.matcher — this is a thin wrapper so none of this module's
    call sites need to know where the entries came from. Recomputed fresh on
    every call; only a disposition recorded against a hit is persisted (see
    entry["watchlist_dispositions"] in page_customer360)."""
    return screen_watchlist(full_name, date_of_birth, country, st.session_state.watchlist_entries)


# --------------------------------------------------------------------------
# i18n — every user-facing string in this file is looked up through t() or
# vocab_label() rather than hardcoded, so the sidebar's language toggle can
# switch the whole app on the same rerun. Hebrew is the default language (see
# the App shell section's session_state.setdefault) per the brief; English is
# the fallback both when a key is missing from the active table and for the
# vocabulary tuples above (SEGMENTS, OCCUPATIONS, ...), where the English
# label is derived from the value itself rather than hand-written twice.
# --------------------------------------------------------------------------

I18N: dict[str, dict[str, str]] = {
    "en": {
        "sidebar.title": "Customer Risk Rating",
        "sidebar.subtitle": "Enterprise Risk & Compliance Workflow",
        "sidebar.api_label": "API:",
        "sidebar.api_healthy": "API healthy · models: {models} · policy v{policy} · api {api}",
        "sidebar.api_start_hint": "Start it with `python scripts/serve.py`, or set `CRR_API_URL` to a "
                                  "running instance. Every page below needs it.",
        "sidebar.language_label": "Language",
        "sidebar.reviewer_name_label": "Reviewer name",
        "sidebar.reviewer_name_help": "Attributed on every case decision you record below — session-local, "
                                      "no login.",
        "sidebar.nav_header": "Navigate",
        "sidebar.nav_queue": "📋 Risk Operations Queue",
        "sidebar.nav_customer360": "🔎 Customer 360 & Decision Center",
        "sidebar.nav_simulator": "🧪 Event Simulator & Sandbox",
        "sidebar.nav_rulebuilder": "⚙️ Rule Builder & Policy Engine",
        "sidebar.nav_auditlog": "🛡️ Audit Log",
        "sidebar.role_label": "Role",
        "sidebar.role_help": "Your role comes from your signed-in account and controls which actions you "
                             "can take. To change it, an administrator edits the account.",
        "sidebar.sign_out": "Sign out",
        "role.junior_analyst": "Junior Analyst",
        "role.risk_manager": "Risk Manager",
        "role.compliance_admin": "Compliance Officer / Admin",
        "login.title": "Sign in",
        "login.subtitle": "Enterprise Risk & Compliance Console",
        "login.username": "Username",
        "login.password": "Password",
        "login.submit": "Sign in",
        "login.failed": "Incorrect username or password.",
        "login.demo_header": "Demo accounts",
        "login.demo_hint": "This is a demo. Sign in with one of the seeded accounts below; a real "
                           "deployment disables these and provisions accounts per user.",
        "users.header": "User management",
        "users.new_username": "Username",
        "users.new_display_name": "Display name",
        "users.new_role": "Role",
        "users.new_password": "Password",
        "users.create": "Create user",
        "users.created": "Created user {username}.",
        "users.error": "Could not create user: {detail}",
        "users.existing": "{n} accounts",
        "watchlist_sources.header": "Watchlist data sources",
        "watchlist_sources.caption": "Where each screening source's data currently comes from and how "
                                     "current it is. \"ofac\"/\"eu\"/\"un\" start as fictional demo entries and "
                                     "are replaced by a real, freshly parsed list the first time "
                                     "`scripts/refresh_watchlists.py` runs for that source; \"pep\"/"
                                     "\"adverse media\" have no free authoritative source and stay demo data.",
        "watchlist_sources.col_source": "Source",
        "watchlist_sources.col_count": "Entries",
        "watchlist_sources.col_refreshed": "Last refreshed",
        "watchlist_sources.never": "never (demo data)",
        "watchlist_sources.refresh_hint": "Run from a shell with access to this deployment's database "
                                          "(`CRR_WORKFLOW_DB_URL`) — not from this page, which only reads "
                                          "what is already there:",
        "sidebar.footer": "Scores, explanations and re-scoring all come from the live FastAPI service over "
                          "its public endpoints — no model or policy logic is duplicated here. Case "
                          "decisions, notes, rules, watchlist rulings and the audit trail are persisted to "
                          "the workflow database and survive a refresh, a new tab and a restart.",

        "common.band_prefix": "band",
        "common.case_prefix": "case:",
        "common.yes": "yes",
        "common.no": "no",

        "band.Low": "Low",
        "band.Medium": "Medium",
        "band.High": "High",
        "band.Extreme": "Extreme",

        "status.pending_review": "Pending Review",
        "status.approved": "Approved",
        "status.escalated_aml": "Escalated to AML",
        "status.kyc_requested": "KYC Requested",
        "status.blocked": "Blocked",

        "archetype.sandbox": "sandbox ({preset})",

        "reason_help.triggered": "The event matched a policy trigger and the customer was re-scored.",
        "reason_help.debounced": "A matching trigger fired too recently — suppressed to stop alert storms.",
        "reason_help.no_trigger": "Stored, but this event type/amount matches no trigger in the policy.",
        "reason_help.not_yet_scored": "No score on record yet. Score the customer once first.",
        "reason_help.stale": "The stored score was too old to re-use as a base for re-scoring.",

        "chart.model_band": "model band {band}",
        "chart.policy_floored": " · policy floored to {band}",
        "chart.score_word": "score",
        "chart.axis_title": "← lowers risk    ·    raises risk →",
        "chart.hover_contribution": "contribution",
        "chart.hover_dimension": "dimension",
        "chart.raises_risk": "raises risk",
        "chart.lowers_risk": "lowers risk",
        "chart.table_view": "Table view (exact values)",

        "rules.header": "Policy rules that fired",
        "rules.empty": "No deterministic policy rule matched this customer.",
        "rules.caption": "Kept separate from the model factors above on purpose: a rule is a pass/fail "
                         "policy override, not a learned contribution.",

        "result.composite_score": "composite risk score (0-100)",
        "result.credit_default": "Credit default (12m)",
        "result.financial_crime": "Financial crime (12m)",
        "result.latency": "Latency",
        "result.flag_band_floor": "A policy rule raised this band above the model's own reading.",
        "result.flag_review": "Flagged for human review by policy.",
        "result.flag_degraded": "**Degraded**: narrative text was supplied but no extraction ran, so this "
                                "score is tabular-only. The request did not fail — the gap is recorded.",
        "result.caption": "model `{model}` · policy v{policy} · scored {scored_at}",

        "profile.segment": "Segment",
        "profile.occupation": "Occupation",
        "profile.employment": "Employment",
        "profile.residency": "Residency",
        "profile.age": "Age",
        "profile.account_age": "Account age",
        "profile.account_age_unit": "mo",
        "profile.products_held": "Products held",
        "profile.source": "Source profile: {archetype}",

        "timeline.empty": "No activity recorded yet.",
        "timeline.scored": "<b>Scored</b> — {band} band, {score} — {note}",
        "timeline.event": "<b>Event</b> <code>{event_type}</code> (amount {amount}) — {status}{change}",
        "timeline.event_rescored": "re-scored",
        "timeline.event_band_changed": "  ·  <b>band changed</b>",
        "timeline.decision": "<b>{label}</b> by {actor} — &ldquo;{note}&rdquo;",
        "timeline.note": "<b>Note</b> by {actor} — &ldquo;{note}&rdquo;",

        "queue.title": "Risk Operations Queue",
        "queue.caption": "Every customer scored in this session, highest risk first. Score, band and "
                         "factors come from the API; case status, SLA and notes are this session's "
                         "workflow state (see the sidebar).",
        "queue.empty": "The queue is empty.",
        "queue.load_demo": "Load demo book (18 synthetic customers)",
        "queue.load_fail": "Every candidate failed to score — is the API reachable? {detail}",
        "queue.kpi_total": "Total scored",
        "queue.kpi_high_risk": "High-risk pending review",
        "queue.kpi_sla": "SLA breaches",
        "queue.kpi_escalated": "Escalated to AML",
        "queue.kpi_watchlist": "Watchlist pending",
        "queue.sla_caption": "SLA windows while a case sits at Pending Review (workflow convention, not "
                             "API policy): Extreme {extreme}h · High {high}h · Medium {medium}h · "
                             "Low {low}h from the scoring timestamp.",
        "queue.search_label": "Search — Customer ID, segment, occupation or country",
        "queue.band_filter_label": "Risk band",
        "queue.status_filter_label": "Case status",
        "queue.reload_button": "↻ Reload book",
        "queue.reload_help": "Score a fresh synthetic book and refresh the demo customers in the queue",
        "queue.no_match": "No customers match these filters.",
        "queue.shown_caption": "{shown} of {total} customers shown. Click a row to open Customer 360.",
        "queue.onboard_expander": "+ Onboard a new customer from a preset",
        "queue.onboard_caption": "Loads a full, realistic pre-configured profile automatically — no manual "
                                 "field entry. For hands-on control over every field, use the sandbox on "
                                 "the Event Simulator page.",
        "queue.onboard_profile_label": "Profile",
        "queue.onboard_id_label": "Customer ID",
        "queue.onboard_submit": "Score & add",
        "queue.onboard_success": "{id} scored — {band} band — added to the queue.",

        "col.customer_id": "Customer ID",
        "col.band": "Band",
        "col.score": "Score",
        "col.custom_rules": "Custom rules",
        "col.watchlist": "Watchlist",
        "col.segment": "Segment",
        "col.country": "Country",
        "col.credit_risk": "Credit risk",
        "col.crime_risk": "Fin. crime risk",
        "col.status": "Status",
        "col.sla": "SLA",
        "col.scored": "Scored",
        "col.breached": "BREACHED",

        "c360.title": "Customer 360 — {id}",
        "c360.no_customer_title": "Customer 360 & Decision Center",
        "c360.no_customer_info": "No customer selected. Pick one from the Risk Operations Queue.",
        "c360.back_to_queue": "← Back to queue",
        "c360.event_timeline_header": "Event timeline",
        "c360.explainability_header": "Explainability engine",

        "action.header": "Case decision",
        "action.already_actioned": "This case has already been actioned. Recording another decision below "
                                   "updates the status again and appends to the timeline.",
        "action.note_label": "Review note (required)",
        "action.note_placeholder": "Document the reason for this decision — required for the audit trail.",
        "action.approve": "✅ Approve Customer",
        "action.escalate": "🚨 Escalate to AML",
        "action.kyc": "📋 Request KYC Verification",
        "action.block": "⛔ Block Account",
        "action.add_note": "🗒️ Add Note",
        "action.note_required": "A review note is required before this can be recorded.",
        "action.recorded": "Recorded: {label}.",
        "action.note_added": "Note added to the timeline.",
        "permission.decide_case_denied": "Your role ({role}) can view this case but not decide it — Risk "
                                         "Manager or Compliance Officer / Admin is required.",
        "permission.manage_rules_denied": "Your role ({role}) can view rules but not create, edit or delete "
                                          "them — Compliance Officer / Admin is required.",

        "explain.caption": "The stored explanation for this customer's most recent score — read back from "
                           "the API rather than recomputed, so this is provably the same event as the "
                           "score above.",
        "explain.internal_header": "Internal reviewer · {n} codes",
        "explain.customer_header": "Customer-facing · {n} codes",
        "explain.no_codes": "No reason codes on record.",
        "explain.no_customer_codes": "No reason code on this decision may be shown to the customer.",
        "explain.withheld_codes": "**{n} reason code(s) withheld:** {codes}",
        "explain.withheld_rules": "**{n} policy rule(s) withheld:** {rules}",
        "explain.not_shown_prefix": "Not shown to the customer — {detail}",
        "explain.all_disclosable": "Every reason code and rule on this decision is disclosable to the "
                                   "customer.",
        "explain.filter_expander": "Filter all reason codes",
        "explain.no_codes_decision": "No reason codes on this decision.",
        "explain.dimension_label": "Dimension",
        "explain.direction_label": "Direction",
        "explain.visibility_label": "Visibility",
        "explain.customer_visible": "customer-visible",
        "explain.internal_only": "internal-only",
        "explain.shown_count": "{shown} of {total} reason codes shown.",

        "sim.title": "Real-Time Event Simulator & Sandbox",
        "sim.caption": "For testing rules and the API directly — push a single event against any customer "
                       "on record, or score a fully custom payload without touching the Operations Queue.",
        "sim.tab_event": "Push an event",
        "sim.tab_sandbox": "Sandbox: score a custom payload",

        "sim.event_intro": "Push a single event and watch the re-scoring engine decide. The caller does "
                           "**not** resend the customer's profile — the engine rebuilds the input from the "
                           "last stored snapshot plus the event log, which is the whole point of the "
                           "endpoint.",
        "sim.event_caption": "The customer must already have a score on record — onboard them first "
                             "(Queue page or the sandbox tab). Trigger thresholds live in the policy file; "
                             "the `reason` in the response is the authoritative account of what happened.",
        "sim.customer_other": "— type a different ID —",
        "sim.customer_label": "Customer",
        "sim.customer_id_label": "Customer ID",
        "sim.event_type_label": "Event type",
        "sim.usually_trigger": "usually a trigger",
        "sim.amount_label": "Amount",
        "sim.counterparty_label": "Counterparty country",
        "sim.channel_label": "Channel",
        "sim.occurred_label": "Occurred (minutes ago)",
        "sim.send_event": "Send event",
        "sim.outcome_label": "Outcome",
        "sim.rescored_label": "Re-scored",
        "sim.band_changed_label": "Band changed",
        "sim.notified_label": "Notified",
        "sim.new_score_header": "New score after `{trigger}`",
        "sim.score_before": "Score before",
        "sim.score_after": "Score after",
        "sim.band_label": "Band",
        "sim.narrative_dropped_warning": "**The narrative-derived factor(s) {codes} are absent from this "
                                         "re-score.** An event re-score rebuilds the input from the "
                                         "customer's stored snapshot plus the event log, and narrative "
                                         "notes are not part of that snapshot — so text signal present in "
                                         "the original `/score` call does not carry over. Note the response "
                                         "is *not* marked `degraded`: no narratives were supplied to this "
                                         "call, and by design that counts as a normal request rather than a "
                                         "failed extraction. Worth knowing before reading the delta above "
                                         "as a real change in the customer's risk.",
        "sim.why_internal": "Why — top factors (internal view)",
        "sim.no_factor": "No factor cleared the policy's minimum contribution threshold.",
        "sim.event_stored_not_scored": "The event was stored. Onboard this customer first (Queue page, or "
                                       "the sandbox tab), then send the event again.",
        "sim.event_stored_no_score": "The event was stored, but no new score was computed — see the reason "
                                     "above.",

        "sim.sandbox_intro": "Full manual control over every field the API accepts — a tool for testing "
                             "rules and the API directly, not the primary way to bring a customer into the "
                             "queue. **Anything left marked unknown is sent as a genuine missing value**, "
                             "never as a zero.",
        "sim.start_from_profile": "Start from a profile",
        "sim.snapshot_date_label": "Snapshot date",
        "sim.audience_label": "Audience",
        "sim.audience_help": "`customer` suppresses reason codes that may not lawfully be disclosed to the "
                             "subject — AML concerns, prior SARs, adverse media.",

        "sim.expander_profile": "Profile",
        "sim.expander_income": "Income & credit",
        "sim.expander_txn": "Transaction behaviour",
        "sim.expander_aml": "AML / KYC",
        "sim.expander_tier1_aml": "Advanced Risk Indicators (Tier-1 AML/KYC)",
        "sim.tier1_aml_caption": "Behavioral, digital, corporate and cash/crypto indicators used by tier-1 "
                                 "banks for advanced AML/KYC screening.",
        "sim.group_behavioral": "Behavioral",
        "sim.group_digital": "Digital & Device",
        "sim.group_corporate": "Corporate / Legal",
        "sim.group_cash_crypto": "Cash & Crypto",
        "sim.expander_narrative": "Narrative notes (free text)",

        "field.segment": "Segment",
        "field.occupation": "Occupation",
        "field.employment": "Employment",
        "field.age": "Age",
        "field.years_at_employer": "Years at employer",
        "field.account_age_months": "Account age (months)",
        "field.residency": "Residency",
        "field.country": "Country",
        "field.products_held": "Products held",
        "field.declared_income": "Declared annual income",
        "field.verified_income_ratio": "Verified income ratio",
        "field.income_volatility": "Income volatility (CV)",
        "field.bureau_score": "Bureau score",
        "field.total_credit_limit": "Total credit limit",
        "field.credit_utilization": "Credit utilisation",
        "field.dti": "Debt-to-income",
        "field.savings_to_income": "Savings-to-income",
        "field.open_loans": "Open loans",
        "field.credit_inquiries": "Credit inquiries (12m)",
        "field.delinq_30d": "30d delinquencies (12m)",
        "field.delinq_90d": "90d delinquencies (24m)",
        "field.max_days_past_due": "Max days past due (24m)",
        "field.prior_default": "Prior default",
        "field.bounced_payments": "Bounced payments (12m)",
        "field.overdraft_events": "Overdraft events (12m)",
        "field.balance_volatility": "Balance volatility",
        "field.txn_count": "Transactions (90d)",
        "field.cash_intensity": "Cash intensity",
        "field.cross_border": "Cross-border ratio",
        "field.night_txn": "Night-time ratio",
        "field.structuring_score": "Structuring score",
        "field.crypto_exposure": "Crypto exposure",
        "field.gambling_spend": "Gambling spend",
        "field.new_counterparties": "New counterparties",
        "field.pep": "PEP",
        "field.sanctions_hits": "Sanctions hits",
        "field.adverse_media": "Adverse media (12m)",
        "field.offshore_links": "Offshore links",
        "field.high_risk_jurisdiction": "High-risk jurisdiction",
        "field.medium_risk_jurisdiction": "Medium-risk jurisdiction",
        "field.prior_sar": "Prior SAR",
        "field.edd_required": "EDD required",
        "field.source_of_funds": "Source of funds",
        "field.source_verified": "Source verified",
        "field.kyc_completeness": "KYC completeness",
        "field.kyc_refresh_overdue": "KYC refresh overdue (days)",
        "field.turnover_ratio": "Expected vs. actual turnover ratio",
        "field.pass_through_hours": "Pass-through velocity (hours funds rest)",
        "field.volume_spike": "6-month volume spike ratio",
        "field.vpn_flag": "VPN / high-risk IP",
        "field.device_changes": "Device changes (30d)",
        "field.complex_ownership": "Complex ownership structure",
        "field.ubo_change": "Recent UBO change",
        "field.cash_to_volume": "Cash share of total volume",
        "field.crypto_vasp": "Crypto VASP exposure",

        "sim.narrative_caption": "Treated as untrusted input. Text reaches the extractor inside a data "
                                 "envelope, and the schema it must answer through has no field that means "
                                 "a score or a band — an instruction hidden in a note cannot become a "
                                 "decision. Try it.",
        "field.support_call": "Support call summary",
        "field.underwriter_note": "Underwriter note",
        "field.kyc_extract": "KYC document extract",
        "field.full_name": "Full name",
        "field.date_of_birth": "Date of birth",

        "sim.unknown_label": "Send these fields as unknown (null, not zero)",
        "sim.unknown_help": "Demonstrates the missing-data contract: an omitted field reaches the model as "
                            "NaN plus a missing-indicator, never as a fabricated 0.",
        "sim.score_customer": "Score customer",
        "sim.unknown_sent": "{n} field(s) sent as unknown: {fields}",
        "sim.why_audience": "Why — top factors ({audience} view)",
        "sim.watchlist_header": "Watchlist screening (test)",
        "sim.watchlist_caption": "Fuzzy-matches the name and date of birth above against whatever is "
                                 "currently in the watchlist database — demo entries, or a real OFAC/UN/EU "
                                 "list if one has been loaded (see the sidebar's Watchlist data sources "
                                 "panel). Nothing here is sent to the API or saved until this result is "
                                 "added to the queue.",
        "sim.watchlist_none": "No watchlist matches for this identity.",
        "sim.overwrite_warning": "`{id}` already exists in the queue — adding will overwrite its profile, "
                                 "score and timeline with this sandbox result. Its case status and prior "
                                 "decisions are preserved.",
        "sim.add_to_queue": "➕ Add this result to the Operations Queue",
        "sim.added_to_queue": "{id} added to the queue.",

        "rulebuilder.title": "Rule Builder & Dynamic Policy Engine",
        "rulebuilder.caption": "Add custom risk rules without touching code, built entirely from dropdowns. "
                               "Like the rest of the Operations Queue, this is session-local workflow state "
                               "layered on top of the API's own score — it is applied live to every customer "
                               "already in the queue, not just new ones, and can only ADD to a customer's "
                               "risk reading, never soften it. The model's own score is always shown "
                               "alongside the adjustment, never overwritten by it.",
        "rulebuilder.active_rules_header": "Active rules ({n})",
        "rulebuilder.no_rules": "No custom rules yet. Build one below — it applies live to every customer "
                                "already in the queue, not just new ones.",
        "rulebuilder.enabled_label": "Enabled",
        "rulebuilder.delete_rule": "🗑️ Delete",
        "rulebuilder.rule_deleted": "Deleted rule: {name}.",
        "rulebuilder.matches_count": "Matches {n} of {total} customers in the queue right now",
        "rulebuilder.new_rule_header": "➕ New rule",
        "rulebuilder.admin_only_notice": "Only Compliance Officer / Admin can create, edit or delete custom "
                                         "rules. You can still see every active rule above and how many "
                                         "customers it currently matches.",
        "rulebuilder.name_label": "Rule name",
        "rulebuilder.name_placeholder": "e.g. Private banking + Cyprus exposure",
        "rulebuilder.conditions_label": "When…",
        "rulebuilder.condition_field": "Field",
        "rulebuilder.condition_operator": "Operator",
        "rulebuilder.condition_value": "Value",
        "rulebuilder.remove_condition": "Remove this condition",
        "rulebuilder.add_condition": "+ Add condition",
        "rulebuilder.no_conditions_yet": "Add at least one condition below.",
        "rulebuilder.combine_label": "Combine conditions with",
        "rulebuilder.combine_and": "AND — all conditions must match",
        "rulebuilder.combine_or": "OR — any condition matches",
        "rulebuilder.action_label": "Then…",
        "rulebuilder.action_type_label": "Action",
        "rulebuilder.action_add_points": "➕ Add risk score points",
        "rulebuilder.action_force_band": "⬆️ Force minimum band",
        "rulebuilder.points_label": "Points to add",
        "rulebuilder.points_help": "Added to the customer's composite risk score (capped at 100). Only "
                                   "non-negative values are offered — a custom rule can raise risk, never "
                                   "lower it.",
        "rulebuilder.band_label": "Minimum band",
        "rulebuilder.band_help": "Acts as a floor: if the customer's own reading already implies a more "
                                 "severe band, that more severe band wins. This rule can only raise the "
                                 "band, never lower it.",
        "rulebuilder.action_summary_points": "+{points:.0f} pts",
        "rulebuilder.action_summary_band": "Force ≥ {band}",
        "rulebuilder.preview_label": "This rule would currently match {n} of {total} customers in the queue.",
        "rulebuilder.add_rule_button": "Add rule",
        "rulebuilder.rule_added": "Rule added: {name}.",
        "rulebuilder.safety_note": "By design, a custom rule can only add points or raise a customer's "
                                   "floor band — never lower either. This mirrors the same raise-only "
                                   "guarantee enforced by the underlying policy engine (src/crr/rules).",
        "rulebuilder.field_risk_band": "Model risk band (current)",
        "rulebuilder.field_risk_score": "Composite risk score (current)",
        "rulebuilder.field_watchlist_score": "Watchlist match score (current)",
        "rulebuilder.field_watchlist_category": "Watchlist category (current)",
        "rulebuilder.op_eq": "is equal to",
        "rulebuilder.op_neq": "is not equal to",
        "rulebuilder.op_gt": "is greater than",
        "rulebuilder.op_gte": "is at least",
        "rulebuilder.op_lt": "is less than",
        "rulebuilder.op_lte": "is at most",
        "rulebuilder.op_in": "is one of",
        "rulebuilder.c360_header": "Custom rule overlay",
        "rulebuilder.c360_none": "No custom rule fired for this customer — the score and band above are "
                                 "the API's own, unadjusted.",
        "rulebuilder.c360_summary": "{n} custom rule(s) fired → +{points:.0f} pts · effective score "
                                    "{score:.1f} · effective band {band}.",

        "auditlog.title": "Audit Log",
        "auditlog.caption": "Every recorded action this session — case decisions, notes, rule changes, and "
                            "language/role switches — with who did it, their role at the time, and when. "
                            "Append-only: nothing on this page can be edited or removed once written, the "
                            "same one-way guarantee a real compliance audit trail enforces server-side.",
        "auditlog.kpi_total": "Total events",
        "auditlog.kpi_decisions": "Case decisions",
        "auditlog.kpi_rule_changes": "Rule changes",
        "auditlog.empty": "No actions recorded yet this session.",
        "auditlog.search_label": "Search — actor or customer ID",
        "auditlog.action_filter_label": "Action type",
        "auditlog.role_filter_label": "Role",
        "auditlog.no_match": "No log entries match these filters.",
        "auditlog.shown_caption": "{shown} of {total} events shown.",
        "auditlog.col_timestamp": "Timestamp",
        "auditlog.col_actor": "Actor",
        "auditlog.col_role": "Role",
        "auditlog.col_action": "Action",
        "auditlog.col_customer": "Customer ID",
        "auditlog.col_details": "Details",
        "auditlog.action_case_decision": "Case decision",
        "auditlog.action_note_added": "Note added",
        "auditlog.action_rule_created": "Rule created",
        "auditlog.action_rule_deleted": "Rule deleted",
        "auditlog.action_rule_toggled": "Rule enabled/disabled",
        "auditlog.action_language_switched": "Language switched",
        "auditlog.action_role_switched": "Role switched",
        "auditlog.action_watchlist_disposition": "Watchlist disposition",
        "auditlog.action_login": "Signed in",
        "auditlog.action_logout": "Signed out",
        "auditlog.action_user_created": "User created",
        "auditlog.detail_decision": "{label} — “{note}”",
        "auditlog.detail_note": "“{note}”",
        "auditlog.detail_rule_created": "{name}: {conditions} → {action}",
        "auditlog.detail_rule_deleted": "{name}",
        "auditlog.detail_rule_toggled_on": "{name} → enabled",
        "auditlog.detail_rule_toggled_off": "{name} → disabled",
        "auditlog.detail_language": "{previous} → {current}",
        "auditlog.detail_role": "{previous} → {current}",
        "auditlog.detail_watchlist": "{name} ({source}) → {label} — “{note}”",
        "auditlog.detail_user_created": "{username} ({role})",

        "watchlist.panel_header": "Watchlist Hits",
        "watchlist.panel_caption": "Fuzzy name/date-of-birth/country match against the OFAC/EU/UN/PEP/"
                                   "adverse-media reference list currently loaded (demo data, or a real "
                                   "ingested list — see the sidebar's Watchlist data sources panel) — "
                                   "recomputed live, never cached. The API's own AML signals above (PEP "
                                   "flag, sanctions hits, adverse media) are a separate thing and are "
                                   "unaffected by this module.",
        "watchlist.none": "No watchlist hits for this identity.",
        "watchlist.note_label": "Review note (required)",
        "watchlist.note_placeholder": "Document why this is, or isn't, a genuine match — required for the "
                                      "audit trail.",
        "watchlist.note_required": "A review note is required before recording this disposition.",
        "watchlist.mark_false_positive": "✅ Mark False Positive",
        "watchlist.mark_true_positive": "🚨 Mark True Positive",
        "watchlist.disposition_recorded": "Disposition recorded.",
        "watchlist.disp_false_positive": "False Positive",
        "watchlist.disp_true_positive": "True Positive",
        "watchlist.disposition_line": "{label} — recorded by {actor} — “{note}”",
        "watchlist.auto_escalated_note": "Watchlist true positive — {name} ({source}): {note}",
        "watchlist.source_ofac": "OFAC",
        "watchlist.source_eu": "EU Sanctions",
        "watchlist.source_un": "UN Sanctions",
        "watchlist.source_pep": "PEP List",
        "watchlist.source_adverse_media": "Adverse Media",
        "watchlist.category_sanctions": "Sanctions",
        "watchlist.category_pep": "PEP",
        "watchlist.category_adverse_media": "Adverse Media",
        "watchlist.category_none": "None",
    },
    "he": {
        "sidebar.title": "דירוג סיכון לקוחות",
        "sidebar.subtitle": "מערכת ניהול סיכונים וציות ארגונית",
        "sidebar.api_label": "API:",
        "sidebar.api_healthy": "ה-API תקין · מודלים: {models} · מדיניות גרסה {policy} · api {api}",
        "sidebar.api_start_hint": "הפעל אותו עם `python scripts/serve.py`, או הגדר `CRR_API_URL` למופע "
                                  "פעיל. כל עמוד למטה זקוק לו.",
        "sidebar.language_label": "שפה",
        "sidebar.reviewer_name_label": "שם הבודק",
        "sidebar.reviewer_name_help": "מיוחס לכל החלטת תיק שתתעד למטה — מקומי לסשן זה בלבד, ללא התחברות.",
        "sidebar.nav_header": "ניווט",
        "sidebar.nav_queue": "📋 תור פעולות סיכון",
        "sidebar.nav_customer360": "🔎 תמונת לקוח 360 ומרכז החלטות",
        "sidebar.nav_simulator": "🧪 סימולטור אירועים וארגז חול",
        "sidebar.nav_rulebuilder": "⚙️ בונה כללים ומנוע מדיניות",
        "sidebar.nav_auditlog": "🛡️ יומן ביקורת",
        "sidebar.role_label": "תפקיד",
        "sidebar.role_help": "התפקיד נקבע לפי החשבון שאיתו התחברת ושולט באילו פעולות תוכל/י לבצע. "
                             "לשינוי, מנהל/ת מערכת עורך/ת את החשבון.",
        "sidebar.sign_out": "התנתקות",
        "role.junior_analyst": "אנליסט/ית זוטר/ה",
        "role.risk_manager": "מנהל/ת סיכונים",
        "role.compliance_admin": "קצין/ת ציות / מנהל/ת מערכת",
        "login.title": "התחברות",
        "login.subtitle": "קונסולת ניהול סיכונים וציות ארגונית",
        "login.username": "שם משתמש",
        "login.password": "סיסמה",
        "login.submit": "התחברות",
        "login.failed": "שם משתמש או סיסמה שגויים.",
        "login.demo_header": "חשבונות הדגמה",
        "login.demo_hint": "זוהי הדגמה. התחבר/י עם אחד מחשבונות ההדגמה למטה; פריסה אמיתית משביתה אותם "
                           "ומקצה חשבונות לכל משתמש.",
        "users.header": "ניהול משתמשים",
        "users.new_username": "שם משתמש",
        "users.new_display_name": "שם לתצוגה",
        "users.new_role": "תפקיד",
        "users.new_password": "סיסמה",
        "users.create": "צור משתמש",
        "users.created": "המשתמש {username} נוצר.",
        "users.error": "לא ניתן ליצור משתמש: {detail}",
        "users.existing": "{n} חשבונות",
        "watchlist_sources.header": "מקורות נתוני רשימת מעקב",
        "watchlist_sources.caption": "מהיכן מגיעים נתוני כל מקור סינון וכמה הם עדכניים. \"OFAC\"/\"האיחוד "
                                     "האירופי\"/\"האו״ם\" מתחילים כרשומות הדגמה בדויות ומוחלפים ברשימה "
                                     "אמיתית ועדכנית בפעם הראשונה שמריצים את `scripts/refresh_watchlists.py` "
                                     "עבור אותו מקור; ל-PEP ותקשורת שלילית אין מקור חינמי ורשמי, ולכן הם "
                                     "נשארים נתוני הדגמה.",
        "watchlist_sources.col_source": "מקור",
        "watchlist_sources.col_count": "רשומות",
        "watchlist_sources.col_refreshed": "עדכון אחרון",
        "watchlist_sources.never": "מעולם לא (נתוני הדגמה)",
        "watchlist_sources.refresh_hint": "יש להריץ ממסוף עם גישה למסד הנתונים של הפריסה הזו "
                                          "(`CRR_WORKFLOW_DB_URL`) — לא מהעמוד הזה, שרק קורא את מה שכבר קיים:",
        "sidebar.footer": "כל הציונים, ההסברים והניקוד מחדש מגיעים משירות ה-FastAPI החי דרך נקודות "
                          "הקצה הציבוריות שלו — אין כאן שכפול של לוגיקת מודל או מדיניות. החלטות תיק, "
                          "הערות, כללים, החלטות רשימת מעקב ויומן הביקורת נשמרים במסד נתוני העבודה "
                          "ושורדים רענון, טאב חדש והפעלה מחדש.",

        "common.band_prefix": "רמה",
        "common.case_prefix": "תיק:",
        "common.yes": "כן",
        "common.no": "לא",

        "band.Low": "נמוך",
        "band.Medium": "בינוני",
        "band.High": "גבוה",
        "band.Extreme": "קיצוני",

        "status.pending_review": "ממתין לבדיקה",
        "status.approved": "אושר",
        "status.escalated_aml": "הוסלם להלבנת הון",
        "status.kyc_requested": "נדרש KYC",
        "status.blocked": "חסום",

        "archetype.sandbox": "ארגז חול ({preset})",

        "reason_help.triggered": "האירוע תאם מפעיל במדיניות והלקוח נוקד מחדש.",
        "reason_help.debounced": "מפעיל תואם הופעל לאחרונה מדי — הודחק כדי למנוע גל התראות.",
        "reason_help.no_trigger": "נשמר, אך סוג/סכום האירוע אינו תואם אף מפעיל במדיניות.",
        "reason_help.not_yet_scored": "אין ציון רשום עדיין. נקד את הלקוח פעם אחת קודם.",
        "reason_help.stale": "הציון השמור היה ישן מדי לשמש כבסיס לניקוד מחדש.",

        "chart.model_band": "רמת מודל {band}",
        "chart.policy_floored": " · מדיניות הורידה לרמה {band}",
        "chart.score_word": "ציון",
        "chart.axis_title": "← מוריד סיכון    ·    מעלה סיכון →",
        "chart.hover_contribution": "תרומה",
        "chart.hover_dimension": "ממד",
        "chart.raises_risk": "מעלה סיכון",
        "chart.lowers_risk": "מוריד סיכון",
        "chart.table_view": "תצוגת טבלה (ערכים מדויקים)",

        "rules.header": "כללי מדיניות שהופעלו",
        "rules.empty": "אין כלל מדיניות דטרמיניסטי שהתאים ללקוח זה.",
        "rules.caption": "נשמר בנפרד מגורמי המודל למעלה בכוונה: כלל הוא דריסת מדיניות של עובר/נכשל, "
                         "לא תרומה נלמדת.",

        "result.composite_score": "ציון סיכון מצרפי (0-100)",
        "result.credit_default": "כשל אשראי (12 חודשים)",
        "result.financial_crime": "פשע פיננסי (12 חודשים)",
        "result.latency": "זמן תגובה",
        "result.flag_band_floor": "כלל מדיניות העלה את הרמה מעל הקריאה של המודל עצמו.",
        "result.flag_review": "סומן לבדיקה אנושית על ידי המדיניות.",
        "result.flag_degraded": "**נחלש**: סופק טקסט נרטיבי אך לא בוצעה חילוץ, כך שהציון הזה מבוסס נתונים "
                                "טבלאיים בלבד. הבקשה לא נכשלה — הפער תועד.",
        "result.caption": "מודל `{model}` · מדיניות גרסה {policy} · נוקד {scored_at}",

        "profile.segment": "מגזר",
        "profile.occupation": "עיסוק",
        "profile.employment": "תעסוקה",
        "profile.residency": "מעמד תושבות",
        "profile.age": "גיל",
        "profile.account_age": "ותק חשבון",
        "profile.account_age_unit": "חודשים",
        "profile.products_held": "מוצרים מוחזקים",
        "profile.source": "פרופיל מקור: {archetype}",

        "timeline.empty": "טרם נרשמה פעילות.",
        "timeline.scored": "<b>נוקד</b> — רמה {band}, {score} — {note}",
        "timeline.event": "<b>אירוע</b> <code>{event_type}</code> (סכום {amount}) — {status}{change}",
        "timeline.event_rescored": "נוקד מחדש",
        "timeline.event_band_changed": "  ·  <b>הרמה השתנתה</b>",
        "timeline.decision": "<b>{label}</b> על ידי {actor} — &ldquo;{note}&rdquo;",
        "timeline.note": "<b>הערה</b> מאת {actor} — &ldquo;{note}&rdquo;",

        "queue.title": "תור פעולות סיכון",
        "queue.caption": "כל לקוח שנוקד בסשן זה, מהסיכון הגבוה ביותר תחילה. הציון, הרמה והגורמים מגיעים "
                         "מה-API; סטטוס התיק, ה-SLA וההערות הם מצב העבודה של הסשן הזה (ראו בסרגל הצד).",
        "queue.empty": "התור ריק.",
        "queue.load_demo": "טען ספר הדגמה (18 לקוחות סינתטיים)",
        "queue.load_fail": "כל המועמדים נכשלו בניקוד — האם ה-API זמין? {detail}",
        "queue.kpi_total": "סה״כ נוקדו",
        "queue.kpi_high_risk": "סיכון גבוה ממתין לבדיקה",
        "queue.kpi_sla": "חריגות SLA",
        "queue.kpi_escalated": "הוסלם להלבנת הון",
        "queue.kpi_watchlist": "רשימת מעקב ממתינה",
        "queue.sla_caption": "חלונות SLA בזמן שתיק נמצא בסטטוס ממתין לבדיקה (מוסכמת עבודה, לא מדיניות "
                             "API): קיצוני {extreme} שעות · גבוה {high} שעות · בינוני {medium} שעות · "
                             "נמוך {low} שעות ממועד הניקוד.",
        "queue.search_label": "חיפוש — מזהה לקוח, מגזר, עיסוק או מדינה",
        "queue.band_filter_label": "רמת סיכון",
        "queue.status_filter_label": "סטטוס תיק",
        "queue.reload_button": "↻ טען מחדש",
        "queue.reload_help": "נקד ספר סינתטי חדש ורענן את לקוחות ההדגמה בתור",
        "queue.no_match": "אין לקוחות התואמים למסננים אלו.",
        "queue.shown_caption": "{shown} מתוך {total} לקוחות מוצגים. לחץ על שורה כדי לפתוח תמונת לקוח 360.",
        "queue.onboard_expander": "+ קליטת לקוח חדש מפרופיל מוכן",
        "queue.onboard_caption": "טוען פרופיל ריאליסטי מוכן מראש באופן אוטומטי — ללא הזנת שדות ידנית. "
                                 "לשליטה מלאה בכל שדה, השתמש בארגז החול בעמוד סימולטור האירועים.",
        "queue.onboard_profile_label": "פרופיל",
        "queue.onboard_id_label": "מזהה לקוח",
        "queue.onboard_submit": "נקד והוסף",
        "queue.onboard_success": "{id} נוקד — רמה {band} — נוסף לתור.",

        "col.customer_id": "מזהה לקוח",
        "col.band": "רמה",
        "col.score": "ציון",
        "col.custom_rules": "כללים מותאמים",
        "col.watchlist": "רשימת מעקב",
        "col.segment": "מגזר",
        "col.country": "מדינה",
        "col.credit_risk": "סיכון אשראי",
        "col.crime_risk": "סיכון פשע פיננסי",
        "col.status": "סטטוס",
        "col.sla": "SLA",
        "col.scored": "מועד ניקוד",
        "col.breached": "חריגה",

        "c360.title": "תמונת לקוח 360 — {id}",
        "c360.no_customer_title": "תמונת לקוח 360 ומרכז החלטות",
        "c360.no_customer_info": "לא נבחר לקוח. בחר לקוח מתור פעולות הסיכון.",
        "c360.back_to_queue": "← חזרה לתור",
        "c360.event_timeline_header": "ציר זמן אירועים",
        "c360.explainability_header": "מנוע הסבר",

        "action.header": "החלטת תיק",
        "action.already_actioned": "תיק זה כבר טופל. רישום החלטה נוספת למטה יעדכן את הסטטוס שוב "
                                   "ויתווסף לציר הזמן.",
        "action.note_label": "הערת סקירה (חובה)",
        "action.note_placeholder": "תעד את הסיבה להחלטה זו — נדרש למסלול הביקורת.",
        "action.approve": "✅ אשר לקוח",
        "action.escalate": "🚨 הסלם להלבנת הון",
        "action.kyc": "📋 בקש אימות KYC",
        "action.block": "⛔ חסום חשבון",
        "action.add_note": "🗒️ הוסף הערה",
        "action.note_required": "נדרשת הערת סקירה לפני שניתן לרשום זאת.",
        "action.recorded": "נרשם: {label}.",
        "action.note_added": "ההערה נוספה לציר הזמן.",
        "permission.decide_case_denied": "התפקיד שלך ({role}) יכול לצפות בתיק זה אך לא להחליט לגביו — "
                                         "נדרש/ת מנהל/ת סיכונים או קצין/ת ציות / מנהל/ת מערכת.",
        "permission.manage_rules_denied": "התפקיד שלך ({role}) יכול לצפות בכללים אך לא ליצור, לערוך או "
                                          "למחוק אותם — נדרש/ת קצין/ת ציות / מנהל/ת מערכת.",

        "explain.caption": "ההסבר השמור לציון האחרון של לקוח זה — נקרא בחזרה מה-API ולא חושב מחדש, כך "
                           "שזהו באופן מוכח אותו אירוע כמו הציון למעלה.",
        "explain.internal_header": "סוקר פנימי · {n} קודים",
        "explain.customer_header": "פונה ללקוח · {n} קודים",
        "explain.no_codes": "אין קודי נימוק רשומים.",
        "explain.no_customer_codes": "אין קוד נימוק בהחלטה זו שניתן להציג ללקוח.",
        "explain.withheld_codes": "**{n} קוד/י נימוק לא נחשפו:** {codes}",
        "explain.withheld_rules": "**{n} כלל/י מדיניות לא נחשפו:** {rules}",
        "explain.not_shown_prefix": "לא מוצג ללקוח — {detail}",
        "explain.all_disclosable": "כל קוד נימוק וכלל בהחלטה זו ניתנים לחשיפה ללקוח.",
        "explain.filter_expander": "סינון כל קודי הנימוק",
        "explain.no_codes_decision": "אין קודי נימוק בהחלטה זו.",
        "explain.dimension_label": "ממד",
        "explain.direction_label": "כיוון",
        "explain.visibility_label": "חשיפה",
        "explain.customer_visible": "גלוי ללקוח",
        "explain.internal_only": "פנימי בלבד",
        "explain.shown_count": "{shown} מתוך {total} קודי נימוק מוצגים.",

        "sim.title": "סימולטור אירועים בזמן אמת וארגז חול",
        "sim.caption": "לבדיקת כללים וה-API ישירות — שלח אירוע בודד כנגד לקוח קיים, או נקד נתונים "
                       "מותאמים אישית לחלוטין מבלי לגעת בתור הפעולות.",
        "sim.tab_event": "שליחת אירוע",
        "sim.tab_sandbox": "ארגז חול: ניקוד נתונים מותאמים אישית",

        "sim.event_intro": "שלח אירוע בודד וצפה במנוע הניקוד מחדש מחליט. הקורא **אינו** שולח מחדש את "
                           "פרופיל הלקוח — המנוע בונה מחדש את הקלט מהתמונה השמורה האחרונה בתוספת יומן "
                           "האירועים, וזה כל הרעיון של נקודת הקצה.",
        "sim.event_caption": "ללקוח כבר חייב להיות ציון רשום — קלוט אותו קודם (עמוד התור או לשונית ארגז "
                             "החול). ספי ההפעלה נמצאים בקובץ המדיניות; ה-`reason` בתשובה הוא הדיווח "
                             "הרשמי של מה שקרה.",
        "sim.customer_other": "— הקלד מזהה אחר —",
        "sim.customer_label": "לקוח",
        "sim.customer_id_label": "מזהה לקוח",
        "sim.event_type_label": "סוג אירוע",
        "sim.usually_trigger": "בדרך כלל מפעיל",
        "sim.amount_label": "סכום",
        "sim.counterparty_label": "מדינת הצד שכנגד",
        "sim.channel_label": "ערוץ",
        "sim.occurred_label": "התרחש (לפני כמה דקות)",
        "sim.send_event": "שלח אירוע",
        "sim.outcome_label": "תוצאה",
        "sim.rescored_label": "נוקד מחדש",
        "sim.band_changed_label": "הרמה השתנתה",
        "sim.notified_label": "נשלחה התראה",
        "sim.new_score_header": "ציון חדש לאחר `{trigger}`",
        "sim.score_before": "ציון לפני",
        "sim.score_after": "ציון אחרי",
        "sim.band_label": "רמה",
        "sim.narrative_dropped_warning": "**גורם/י הנרטיב {codes} נעדרים מהניקוד מחדש הזה.** ניקוד מחדש "
                                         "של אירוע בונה את הקלט מהתמונה השמורה של הלקוח בתוספת יומן "
                                         "האירועים, והערות נרטיביות אינן חלק מאותה תמונה — כך שאות טקסט "
                                         "שהיה נוכח בקריאת `/score` המקורית אינו עובר הלאה. שים לב שהתשובה "
                                         "*אינה* מסומנת `degraded`: לא סופקו נרטיבים לקריאה זו, ומעצם "
                                         "העיצוב זה נחשב לבקשה רגילה ולא לחילוץ שנכשל. כדאי לדעת זאת לפני "
                                         "שקוראים את ההפרש למעלה כשינוי אמיתי בסיכון של הלקוח.",
        "sim.why_internal": "מדוע — גורמים מובילים (תצוגה פנימית)",
        "sim.no_factor": "אף גורם לא עבר את סף התרומה המינימלי של המדיניות.",
        "sim.event_stored_not_scored": "האירוע נשמר. קלוט לקוח זה קודם (עמוד התור, או לשונית ארגז החול), "
                                       "ואז שלח את האירוע שוב.",
        "sim.event_stored_no_score": "האירוע נשמר, אך לא חושב ציון חדש — ראה את הסיבה למעלה.",

        "sim.sandbox_intro": "שליטה ידנית מלאה בכל שדה שה-API מקבל — כלי לבדיקת כללים וה-API ישירות, "
                             "לא הדרך העיקרית להביא לקוח לתור. **כל מה שמסומן כלא ידוע נשלח כערך חסר "
                             "אמיתי**, לעולם לא כאפס.",
        "sim.start_from_profile": "התחל מפרופיל",
        "sim.snapshot_date_label": "תאריך תמונת מצב",
        "sim.audience_label": "קהל יעד",
        "sim.audience_help": "`customer` מדחיק קודי נימוק שאסור לחשוף כדין לנשוא — חששות הלבנת הון, "
                             "דיווחים קודמים, תקשורת שלילית.",

        "sim.expander_profile": "פרופיל",
        "sim.expander_income": "הכנסה ואשראי",
        "sim.expander_txn": "התנהגות עסקאות",
        "sim.expander_aml": "הלבנת הון / KYC",
        "sim.expander_tier1_aml": "מדדי סיכון מתקדמים (הלבנת הון/הכרת הלקוח — בנקים מובילים)",
        "sim.tier1_aml_caption": "מדדים התנהגותיים, דיגיטליים, תאגידיים ומזומן/קריפטו כפי שמיושמים "
                                 "בבנקים מובילים לצורך סינון מתקדם נגד הלבנת הון והכרת הלקוח.",
        "sim.group_behavioral": "התנהגותי",
        "sim.group_digital": "דיגיטלי ומכשירים",
        "sim.group_corporate": "תאגידי / משפטי",
        "sim.group_cash_crypto": "מזומן וקריפטו",
        "sim.expander_narrative": "הערות נרטיביות (טקסט חופשי)",

        "field.segment": "מגזר",
        "field.occupation": "עיסוק",
        "field.employment": "תעסוקה",
        "field.age": "גיל",
        "field.years_at_employer": "שנות ותק אצל המעסיק",
        "field.account_age_months": "ותק חשבון (חודשים)",
        "field.residency": "מעמד תושבות",
        "field.country": "מדינה",
        "field.products_held": "מוצרים מוחזקים",
        "field.declared_income": "הכנסה שנתית מוצהרת",
        "field.verified_income_ratio": "יחס הכנסה מאומתת",
        "field.income_volatility": "תנודתיות הכנסה (CV)",
        "field.bureau_score": "ציון לשכת אשראי",
        "field.total_credit_limit": "מסגרת אשראי כוללת",
        "field.credit_utilization": "ניצול אשראי",
        "field.dti": "יחס חוב להכנסה",
        "field.savings_to_income": "יחס חיסכון להכנסה",
        "field.open_loans": "הלוואות פתוחות",
        "field.credit_inquiries": "פניות אשראי (12 חודשים)",
        "field.delinq_30d": "פיגורי 30 יום (12 חודשים)",
        "field.delinq_90d": "פיגורי 90 יום (24 חודשים)",
        "field.max_days_past_due": "מקסימום ימי פיגור (24 חודשים)",
        "field.prior_default": "כשל קודם",
        "field.bounced_payments": "תשלומים שחזרו (12 חודשים)",
        "field.overdraft_events": "אירועי חריגה ממסגרת (12 חודשים)",
        "field.balance_volatility": "תנודתיות יתרה",
        "field.txn_count": "עסקאות (90 יום)",
        "field.cash_intensity": "עצימות מזומן",
        "field.cross_border": "יחס חוצה גבולות",
        "field.night_txn": "יחס פעילות לילית",
        "field.structuring_score": "ציון פיצול עסקאות",
        "field.crypto_exposure": "חשיפה לקריפטו",
        "field.gambling_spend": "הוצאות הימורים",
        "field.new_counterparties": "צדדים שכנגד חדשים",
        "field.pep": "גורם פוליטי חשוף (PEP)",
        "field.sanctions_hits": "פגיעות בסנקציות",
        "field.adverse_media": "תקשורת שלילית (12 חודשים)",
        "field.offshore_links": "קשרים אופשוריים",
        "field.high_risk_jurisdiction": "סמכות שיפוט בסיכון גבוה",
        "field.medium_risk_jurisdiction": "סמכות שיפוט בסיכון בינוני",
        "field.prior_sar": "דיווח קודם (SAR)",
        "field.edd_required": "נדרשת בדיקת נאותות מוגברת (EDD)",
        "field.source_of_funds": "מקור כספים",
        "field.source_verified": "מקור מאומת",
        "field.kyc_completeness": "שלמות מסמכי KYC",
        "field.kyc_refresh_overdue": "עדכון KYC באיחור (ימים)",
        "field.turnover_ratio": "יחס מחזור צפוי מול בפועל",
        "field.pass_through_hours": "מהירות מעבר כספים (שעות מנוחה בחשבון)",
        "field.volume_spike": "קפיצת נפח פעילות (6 חודשים)",
        "field.vpn_flag": "VPN / כתובת IP בסיכון גבוה",
        "field.device_changes": "שינויי מכשיר (30 יום)",
        "field.complex_ownership": "מבנה בעלות מורכב",
        "field.ubo_change": "שינוי בעל שליטה מיטיב לאחרונה",
        "field.cash_to_volume": "חלק המזומן מסך המחזור",
        "field.crypto_vasp": "חשיפה לספק נכסים וירטואליים (VASP)",

        "sim.narrative_caption": "מטופל כקלט לא מהימן. הטקסט מגיע לחולץ בתוך מעטפת נתונים, והסכימה שהוא "
                                 "חייב לענות דרכה אינה כוללת שדה שמשמעותו ציון או רמה — הוראה מוסתרת "
                                 "בהערה אינה יכולה להפוך להחלטה. נסה זאת.",
        "field.support_call": "סיכום שיחת תמיכה",
        "field.underwriter_note": "הערת חתם",
        "field.kyc_extract": "תמצית מסמך KYC",
        "field.full_name": "שם מלא",
        "field.date_of_birth": "תאריך לידה",

        "sim.unknown_label": "שלח שדות אלו כלא ידועים (ריק, לא אפס)",
        "sim.unknown_help": "מדגים את חוזה הנתונים החסרים: שדה שהושמט מגיע למודל כ-NaN בתוספת מחוון "
                            "חוסר, לעולם לא כ-0 מזויף.",
        "sim.score_customer": "נקד לקוח",
        "sim.unknown_sent": "{n} שדה/שדות נשלחו כלא ידועים: {fields}",
        "sim.why_audience": "מדוע — גורמים מובילים (תצוגת {audience})",
        "sim.watchlist_header": "סינון רשימת מעקב (בדיקה)",
        "sim.watchlist_caption": "מבצע התאמה מטושטשת של השם ותאריך הלידה שלמעלה מול מה שקיים כרגע במסד "
                                 "נתוני רשימת המעקב — נתוני הדגמה, או רשימת OFAC/UN/EU אמיתית אם כבר נטענה "
                                 "(ראו את פאנל מקורות נתוני רשימת המעקב בסרגל הצד). שום דבר כאן לא נשלח "
                                 "ל-API או נשמר עד שהתוצאה הזו תתווסף לתור.",
        "sim.watchlist_none": "אין התאמות ברשימת המעקב לזהות זו.",
        "sim.overwrite_warning": "`{id}` כבר קיים בתור — ההוספה תחליף את הפרופיל, הציון וציר הזמן שלו "
                                 "בתוצאת ארגז החול הזו. סטטוס התיק וההחלטות הקודמות שלו נשמרים.",
        "sim.add_to_queue": "➕ הוסף תוצאה זו לתור הפעולות",
        "sim.added_to_queue": "{id} נוסף לתור.",

        "rulebuilder.title": "בונה כללים ומנוע מדיניות דינמי",
        "rulebuilder.caption": "הוסיפו כללי סיכון מותאמים אישית בלי לגעת בקוד — בנויים כולם מתפריטים "
                               "נפתחים. כמו שאר תור הפעולות, זהו מצב עבודה מקומי לסשן זה, הנשכב מעל הציון "
                               "של ה-API עצמו — הוא מופעל באופן חי על כל לקוח שכבר נמצא בתור, לא רק על "
                               "חדשים, ויכול רק להוסיף לקריאת הסיכון של לקוח, לעולם לא לרכך אותה. הציון "
                               "המקורי של המודל תמיד מוצג לצד ההתאמה, ולעולם אינו נדרס על ידה.",
        "rulebuilder.active_rules_header": "כללים פעילים ({n})",
        "rulebuilder.no_rules": "עדיין אין כללים מותאמים. בנו אחד למטה — הוא יופעל באופן חי על כל לקוח "
                                "שכבר נמצא בתור, לא רק על חדשים.",
        "rulebuilder.enabled_label": "פעיל",
        "rulebuilder.delete_rule": "🗑️ מחק",
        "rulebuilder.rule_deleted": "הכלל נמחק: {name}.",
        "rulebuilder.matches_count": "תואם כרגע {n} מתוך {total} לקוחות בתור",
        "rulebuilder.new_rule_header": "➕ כלל חדש",
        "rulebuilder.admin_only_notice": "רק קצין/ת ציות / מנהל/ת מערכת יכול/ה ליצור, לערוך או למחוק "
                                         "כללים מותאמים. עדיין ניתן לראות למעלה כל כלל פעיל וכמה לקוחות "
                                         "הוא תואם כרגע.",
        "rulebuilder.name_label": "שם הכלל",
        "rulebuilder.name_placeholder": "לדוגמה: בנקאות פרטית + חשיפה לקפריסין",
        "rulebuilder.conditions_label": "כאשר…",
        "rulebuilder.condition_field": "שדה",
        "rulebuilder.condition_operator": "אופרטור",
        "rulebuilder.condition_value": "ערך",
        "rulebuilder.remove_condition": "הסר תנאי זה",
        "rulebuilder.add_condition": "+ הוסף תנאי",
        "rulebuilder.no_conditions_yet": "הוסיפו לפחות תנאי אחד למטה.",
        "rulebuilder.combine_label": "שילוב התנאים באמצעות",
        "rulebuilder.combine_and": "AND — כל התנאים חייבים להתקיים",
        "rulebuilder.combine_or": "OR — מספיק שתנאי אחד יתקיים",
        "rulebuilder.action_label": "אז…",
        "rulebuilder.action_type_label": "פעולה",
        "rulebuilder.action_add_points": "➕ הוסף נקודות לציון הסיכון",
        "rulebuilder.action_force_band": "⬆️ קבע רמה מינימלית",
        "rulebuilder.points_label": "נקודות להוספה",
        "rulebuilder.points_help": "מתווסף לציון הסיכון המצרפי של הלקוח (מוגבל ל-100). מוצעים כאן רק ערכים "
                                   "לא-שליליים — כלל מותאם יכול להעלות סיכון, לעולם לא להוריד אותו.",
        "rulebuilder.band_label": "רמה מינימלית",
        "rulebuilder.band_help": "פועל כרצפה: אם הקריאה של הלקוח עצמה כבר מרמזת על רמה חמורה יותר, הרמה "
                                 "החמורה יותר גוברת. כלל זה יכול רק להעלות את הרמה, לעולם לא להוריד אותה.",
        "rulebuilder.action_summary_points": "+{points:.0f} נק'",
        "rulebuilder.action_summary_band": "קבע ≥ {band}",
        "rulebuilder.preview_label": "כלל זה יתאים כרגע ל-{n} מתוך {total} לקוחות בתור.",
        "rulebuilder.add_rule_button": "הוסף כלל",
        "rulebuilder.rule_added": "הכלל נוסף: {name}.",
        "rulebuilder.safety_note": "מבחינה עיצובית, כלל מותאם יכול רק להוסיף נקודות או להעלות את רמת "
                                   "הרצפה של לקוח — לעולם לא להוריד אף אחת מהן. זה משקף את אותה ערבות "
                                   "העלאה-בלבד שנאכפת על ידי מנוע המדיניות הבסיסי (src/crr/rules).",
        "rulebuilder.field_risk_band": "רמת סיכון המודל (נוכחית)",
        "rulebuilder.field_risk_score": "ציון סיכון מצרפי (נוכחי)",
        "rulebuilder.field_watchlist_score": "ציון התאמה לרשימת מעקב (נוכחי)",
        "rulebuilder.field_watchlist_category": "קטגוריית רשימת מעקב (נוכחית)",
        "rulebuilder.op_eq": "שווה ל-",
        "rulebuilder.op_neq": "שונה מ-",
        "rulebuilder.op_gt": "גדול מ-",
        "rulebuilder.op_gte": "גדול או שווה ל-",
        "rulebuilder.op_lt": "קטן מ-",
        "rulebuilder.op_lte": "קטן או שווה ל-",
        "rulebuilder.op_in": "הוא אחד מ-",
        "rulebuilder.c360_header": "שכבת כללים מותאמים",
        "rulebuilder.c360_none": "לא הופעל אף כלל מותאם עבור לקוח זה — הציון והרמה למעלה הם אלה של ה-API "
                                 "עצמו, ללא התאמה.",
        "rulebuilder.c360_summary": "{n} כלל/ים מותאמים הופעלו → +{points:.0f} נק' · ציון אפקטיבי "
                                    "{score:.1f} · רמה אפקטיבית {band}.",

        "auditlog.title": "יומן ביקורת",
        "auditlog.caption": "כל פעולה שנרשמה בסשן זה — החלטות תיק, הערות, שינויי כללים ומעברי שפה/תפקיד — "
                            "עם מי ביצע אותה, התפקיד שלו/ה באותו רגע, ומתי. הוספה בלבד: דבר בעמוד הזה אינו "
                            "ניתן לעריכה או למחיקה לאחר שנכתב, אותה ערבות חד-כיוונית שמסלול ביקורת ציות "
                            "אמיתי אוכף בצד השרת.",
        "auditlog.kpi_total": "סה״כ אירועים",
        "auditlog.kpi_decisions": "החלטות תיק",
        "auditlog.kpi_rule_changes": "שינויי כללים",
        "auditlog.empty": "עדיין לא נרשמה אף פעולה בסשן זה.",
        "auditlog.search_label": "חיפוש — מבצע/ת או מזהה לקוח",
        "auditlog.action_filter_label": "סוג פעולה",
        "auditlog.role_filter_label": "תפקיד",
        "auditlog.no_match": "אין רשומות יומן התואמות למסננים אלה.",
        "auditlog.shown_caption": "{shown} מתוך {total} אירועים מוצגים.",
        "auditlog.col_timestamp": "חותמת זמן",
        "auditlog.col_actor": "מבצע/ת",
        "auditlog.col_role": "תפקיד",
        "auditlog.col_action": "פעולה",
        "auditlog.col_customer": "מזהה לקוח",
        "auditlog.col_details": "פרטים",
        "auditlog.action_case_decision": "החלטת תיק",
        "auditlog.action_note_added": "הערה נוספה",
        "auditlog.action_rule_created": "כלל נוצר",
        "auditlog.action_rule_deleted": "כלל נמחק",
        "auditlog.action_rule_toggled": "כלל הופעל/כובה",
        "auditlog.action_language_switched": "שפה הוחלפה",
        "auditlog.action_role_switched": "תפקיד הוחלף",
        "auditlog.action_watchlist_disposition": "החלטת רשימת מעקב",
        "auditlog.action_login": "התחברות",
        "auditlog.action_logout": "התנתקות",
        "auditlog.action_user_created": "משתמש נוצר",
        "auditlog.detail_decision": "{label} — “{note}”",
        "auditlog.detail_note": "“{note}”",
        "auditlog.detail_rule_created": "{name}: {conditions} → {action}",
        "auditlog.detail_rule_deleted": "{name}",
        "auditlog.detail_rule_toggled_on": "{name} → הופעל",
        "auditlog.detail_rule_toggled_off": "{name} → כובה",
        "auditlog.detail_language": "{previous} → {current}",
        "auditlog.detail_role": "{previous} → {current}",
        "auditlog.detail_watchlist": "{name} ({source}) → {label} — “{note}”",
        "auditlog.detail_user_created": "{username} ({role})",

        "watchlist.panel_header": "פגיעות רשימת מעקב",
        "watchlist.panel_caption": "התאמה מטושטשת של שם/תאריך לידה/מדינה מול רשימת הייחוס הטעונה כרגע של "
                                   "OFAC/EU/UN/PEP/תקשורת שלילית (נתוני הדגמה, או רשימה אמיתית שנקלטה — "
                                   "ראו את פאנל מקורות נתוני רשימת המעקב בסרגל הצד) — מחושבת מחדש בכל פעם, "
                                   "לעולם לא נשמרת במטמון. האותות למניעת הלבנת הון של ה-API עצמו למעלה "
                                   "(דגל PEP, פגיעות סנקציות, תקשורת שלילית) הם דבר נפרד ואינם מושפעים "
                                   "ממודול זה.",
        "watchlist.none": "אין פגיעות ברשימת המעקב לזהות זו.",
        "watchlist.note_label": "הערת סקירה (חובה)",
        "watchlist.note_placeholder": "תעד/י מדוע זו התאמה אמיתית או לא — נדרש למסלול הביקורת.",
        "watchlist.note_required": "נדרשת הערת סקירה לפני רישום החלטה זו.",
        "watchlist.mark_false_positive": "✅ סמן כחיובי שגוי",
        "watchlist.mark_true_positive": "🚨 סמן כחיובי אמיתי",
        "watchlist.disposition_recorded": "ההחלטה נרשמה.",
        "watchlist.disp_false_positive": "חיובי שגוי",
        "watchlist.disp_true_positive": "חיובי אמיתי",
        "watchlist.disposition_line": "{label} — נרשם על ידי {actor} — “{note}”",
        "watchlist.auto_escalated_note": "חיובי אמיתי ברשימת מעקב — {name} ({source}): {note}",
        "watchlist.source_ofac": "OFAC",
        "watchlist.source_eu": "סנקציות האיחוד האירופי",
        "watchlist.source_un": "סנקציות האו״ם",
        "watchlist.source_pep": "רשימת PEP",
        "watchlist.source_adverse_media": "תקשורת שלילית",
        "watchlist.category_sanctions": "סנקציות",
        "watchlist.category_pep": "PEP",
        "watchlist.category_adverse_media": "תקשורת שלילית",
        "watchlist.category_none": "ללא",
    },
}


def t(key: str, **kwargs: Any) -> str:
    """Look up a UI string in the active language, falling back to English
    for a key missing from Hebrew and to the raw key if missing from English
    too — a translation gap degrades to readable text, never a crash or a
    blank label."""
    lang = st.session_state.get("language", "he")
    table = I18N.get(lang, I18N["en"])
    text = table.get(key, I18N["en"].get(key, key))
    return text.format(**kwargs) if kwargs else text


def band_label(band: str) -> str:
    return t(f"band.{band}")


def status_label(status: str) -> str:
    return t(f"status.{status}")


def preset_label(key: str) -> str:
    if st.session_state.get("language", "he") == "he":
        return PRESET_LABEL_HE.get(key, key)
    return key


def archetype_label(archetype: str) -> str:
    """Translate an ``entry["archetype"]`` tag for display: a bare preset key,
    the ``sandbox ({preset})`` composite the sandbox tab writes, or an
    untranslatable fallback (e.g. ``"custom"``) passed straight through."""
    if archetype in PRESETS:
        return preset_label(archetype)
    if archetype.startswith("sandbox (") and archetype.endswith(")"):
        inner = archetype[len("sandbox ("):-1]
        if inner in PRESETS:
            return t("archetype.sandbox", preset=preset_label(inner))
    return archetype


def yes_no_label(value: int) -> str:
    return t("common.yes") if value == 1 else t("common.no")


# Hebrew labels for the domain-vocabulary tuples above (SEGMENTS, OCCUPATIONS,
# ...) used via format_func wherever they populate a selectbox. English has
# no equivalent hand-written table: _humanize derives "Private Banking" from
# "private_banking" mechanically, which is exactly what these already showed
# before this file had a language toggle — nothing to translate twice.
VOCAB: dict[str, dict[str, str]] = {
    "segment": {
        "retail": "קמעונאות", "sme": "עסקים קטנים ובינוניים",
        "private_banking": "בנקאות פרטית", "corporate": "תאגידי",
    },
    "occupation": {
        "software_engineer": "מהנדס/ת תוכנה", "physician": "רופא/ה", "nurse": "אח/ות",
        "teacher": "מורה", "accountant": "רואה חשבון", "lawyer": "עורך/ת דין",
        "construction_worker": "פועל/ת בניין", "retail_worker": "עובד/ת קמעונאות",
        "driver": "נהג/ת", "chef": "שף/ית", "business_owner": "בעל/ת עסק",
        "consultant": "יועץ/ת", "civil_servant": "עובד/ת ציבור", "police_officer": "שוטר/ת",
        "military": "חייל/ת", "artist": "אמן/ית", "researcher": "חוקר/ת",
        "sales_manager": "מנהל/ת מכירות", "real_estate_agent": "סוכן/ת נדל\"ן",
        "jeweller": "תכשיטן/ית", "import_exporter": "יבואן/ית-יצואן/ית",
        "crypto_trader": "סוחר/ת קריפטו", "casino_employee": "עובד/ת קזינו",
        "money_changer": "חלפן/ית כספים", "student": "סטודנט/ית", "retired": "גמלאי/ת",
        "unemployed": "מובטל/ת", "homemaker": "עקר/ת בית", "freelancer": "עצמאי/ת (פרילנס)",
        "other": "אחר",
    },
    "employment_status": {
        "salaried": "שכיר/ה", "self_employed": "עצמאי/ת", "business_owner": "בעל/ת עסק",
        "unemployed": "מובטל/ת", "retired": "גמלאי/ת", "student": "סטודנט/ית",
    },
    "residency_status": {
        "citizen": "אזרח/ית", "permanent_resident": "תושב/ת קבע",
        "temporary_visa": "ויזה זמנית", "non_resident": "תושב/ת חוץ",
    },
    "source_of_funds": {
        "salary": "משכורת", "business_income": "הכנסה עסקית",
        "investment_returns": "תשואות השקעה", "inheritance": "ירושה",
        "property_sale": "מכירת נכס", "gift": "מתנה", "loan_proceeds": "תמורת הלוואה",
        "crypto_disposal": "מימוש קריפטו", "undeclared": "לא מוצהר",
    },
    "event_type": {
        "missed_payment": "תשלום שהוחמץ", "overdraft_breach": "חריגה ממסגרת אשראי",
        "chargeback": "חיוב חוזר (צ'רג'בק)", "cash_deposit": "הפקדת מזומן",
        "wire_transfer_out": "העברה בנקאית יוצאת", "crypto_transfer": "העברת קריפטו",
        "card_purchase": "רכישה בכרטיס", "atm_withdrawal": "משיכה מכספומט",
        "salary_credit": "זיכוי משכורת", "wire_transfer_in": "העברה בנקאית נכנסת",
        "direct_debit": "הוראת קבע", "loan_repayment": "החזר הלוואה",
    },
    "channel": {
        "online": "אונליין", "branch": "סניף", "mobile": "נייד",
        "atm": "כספומט", "wire": "העברה בנקאית",
    },
    "audience": {"internal": "פנימי", "customer": "לקוח"},
}

_HUMANIZE_OVERRIDES = {"sme": "SME", "pep": "PEP", "kyc": "KYC", "edd": "EDD",
                        "sar": "SAR", "aml": "AML", "atm": "ATM"}


def _humanize(value: str) -> str:
    return " ".join(_HUMANIZE_OVERRIDES.get(w, w.capitalize()) for w in value.split("_"))


def vocab_label(category: str, value: str) -> str:
    if st.session_state.get("language", "he") == "he":
        return VOCAB.get(category, {}).get(value, _humanize(value))
    return _humanize(value)


# --------------------------------------------------------------------------
# Rule Builder — field taxonomy. A curated subset of CustomerPayload
# (crr.api.schemas) plus the two live scoring fields, picked to match the
# categories config/risk_policy.yaml's own example rules draw from rather
# than exposing all ~65 payload fields. Each entry is
# (payload/result key, field.* or rulebuilder.field_* i18n key, kind); kind
# drives both the operator set offered and which value widget is shown —
# there is no free-text expression box anywhere in the builder, so nothing
# a risk manager enters here is ever eval()'d.
# --------------------------------------------------------------------------
RULE_FIELDS: list[tuple[str, str, str]] = [
    ("segment", "field.segment", "categorical"),
    ("country_of_residence", "field.country", "categorical"),
    ("occupation", "field.occupation", "categorical"),
    ("employment_status", "field.employment", "categorical"),
    ("residency_status", "field.residency", "categorical"),
    ("source_of_funds_declared", "field.source_of_funds", "categorical"),
    ("age", "field.age", "numeric"),
    ("account_age_months", "field.account_age_months", "numeric"),
    ("num_products_held", "field.products_held", "numeric"),
    ("declared_annual_income", "field.declared_income", "numeric"),
    ("credit_utilization_ratio", "field.credit_utilization", "numeric"),
    ("dti_ratio", "field.dti", "numeric"),
    ("bureau_score", "field.bureau_score", "numeric"),
    ("max_days_past_due_24m", "field.max_days_past_due", "numeric"),
    ("kyc_refresh_overdue_days", "field.kyc_refresh_overdue", "numeric"),
    ("cash_intensity_ratio", "field.cash_intensity", "numeric"),
    ("cross_border_txn_ratio", "field.cross_border", "numeric"),
    ("structuring_score", "field.structuring_score", "numeric"),
    ("crypto_exposure_ratio_90d", "field.crypto_exposure", "numeric"),
    ("adverse_media_hits_12m", "field.adverse_media", "numeric"),
    ("sanctions_screen_hits", "field.sanctions_hits", "numeric"),
    ("offshore_entity_links", "field.offshore_links", "numeric"),
    ("pep_flag", "field.pep", "flag"),
    ("high_risk_jurisdiction_exposure", "field.high_risk_jurisdiction", "flag"),
    ("medium_risk_jurisdiction_exposure", "field.medium_risk_jurisdiction", "flag"),
    ("source_of_funds_verified", "field.source_verified", "flag"),
    ("prior_default_flag", "field.prior_default", "flag"),
    ("sar_filed_prior", "field.prior_sar", "flag"),
    ("edd_required", "field.edd_required", "flag"),
    ("expected_vs_actual_turnover_ratio", "field.turnover_ratio", "numeric"),
    ("pass_through_velocity_hours", "field.pass_through_hours", "numeric"),
    ("volume_spike_ratio_6m", "field.volume_spike", "numeric"),
    ("device_change_frequency_30d", "field.device_changes", "numeric"),
    ("cash_to_total_volume_ratio", "field.cash_to_volume", "numeric"),
    ("vpn_or_high_risk_ip_flag", "field.vpn_flag", "flag"),
    ("complex_ownership_structure_flag", "field.complex_ownership", "flag"),
    ("recent_ubo_change_flag", "field.ubo_change", "flag"),
    ("crypto_vasp_exposure_flag", "field.crypto_vasp", "flag"),
    ("risk_band", "rulebuilder.field_risk_band", "band"),
    ("risk_score", "rulebuilder.field_risk_score", "numeric"),
    ("watchlist_match_score", "rulebuilder.field_watchlist_score", "numeric"),
    ("watchlist_category", "rulebuilder.field_watchlist_category", "watchlist_category"),
]
_RULE_FIELD_KIND: dict[str, str] = {key: kind for key, _, kind in RULE_FIELDS}
_RULE_FIELD_I18N: dict[str, str] = {key: label_key for key, label_key, _ in RULE_FIELDS}
# Only categorical fields with a hand-written VOCAB table need one here;
# country_of_residence and risk_score/risk_band are handled separately below.
_RULE_FIELD_VOCAB: dict[str, str] = {
    "segment": "segment", "occupation": "occupation", "employment_status": "employment_status",
    "residency_status": "residency_status", "source_of_funds_declared": "source_of_funds",
}
_RULE_FIELD_OPTIONS: dict[str, tuple] = {
    "segment": SEGMENTS, "country_of_residence": COUNTRIES, "occupation": OCCUPATIONS,
    "employment_status": EMPLOYMENT_STATUSES, "residency_status": RESIDENCY_STATUSES,
    "source_of_funds_declared": SOURCE_OF_FUNDS,
}
_RULE_OPERATORS: dict[str, tuple[str, ...]] = {
    "categorical": ("==", "!=", "in"),
    "band": ("==", "!=", "in"),
    "watchlist_category": ("==", "!=", "in"),
    "flag": ("==", "!="),
    "numeric": (">", ">=", "<", "<=", "==", "!="),
}
_OP_LABEL_KEY: dict[str, str] = {
    "==": "rulebuilder.op_eq", "!=": "rulebuilder.op_neq", ">": "rulebuilder.op_gt",
    ">=": "rulebuilder.op_gte", "<": "rulebuilder.op_lt", "<=": "rulebuilder.op_lte", "in": "rulebuilder.op_in",
}
_RULE_NUMERIC_STEP: dict[str, float] = {
    "declared_annual_income": 1000.0, "credit_utilization_ratio": 0.01, "dti_ratio": 0.01,
    "cash_intensity_ratio": 0.01, "cross_border_txn_ratio": 0.01, "structuring_score": 0.01,
    "crypto_exposure_ratio_90d": 0.01,
    "expected_vs_actual_turnover_ratio": 0.01, "volume_spike_ratio_6m": 0.01,
    "cash_to_total_volume_ratio": 0.01, "pass_through_velocity_hours": 0.5,
}


def _rule_field_options(field_key: str) -> list | None:
    kind = _RULE_FIELD_KIND[field_key]
    if kind == "band":
        return list(BAND_ORDER)
    if kind == "watchlist_category":
        return ["none", *WATCHLIST_CATEGORIES]
    if kind == "flag":
        return [0, 1]
    options = _RULE_FIELD_OPTIONS.get(field_key)
    return list(options) if options else None


def _watchlist_category_option_label(category: str) -> str:
    return t("watchlist.category_none") if category == "none" else watchlist_category_label(category)


def _rule_value_format(field_key: str):
    kind = _RULE_FIELD_KIND[field_key]
    if kind == "band":
        return band_label
    if kind == "watchlist_category":
        return _watchlist_category_option_label
    if kind == "flag":
        return yes_no_label
    vocab_cat = _RULE_FIELD_VOCAB.get(field_key)
    if vocab_cat:
        return lambda v: vocab_label(vocab_cat, v)
    return str


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
    "expected_vs_actual_turnover_ratio": (0.3, None, 0.0, 10.0, False),
    "pass_through_velocity_hours": (0.4, None, 0.5, 2000.0, False),
    "volume_spike_ratio_6m": (0.3, None, 0.0, 10.0, False),
    "device_change_frequency_30d": (None, 3, 0, 100, True),
    "cash_to_total_volume_ratio": (0.3, None, 0.0, 1.0, False),
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
    ``per_archetype`` variants of each of the three profiles above.

    Each candidate also carries an ``identity`` (full name, date of birth)
    from _DEMO_IDENTITY_POOL, cycled by position rather than drawn from
    ``rng`` — it never reaches the model, only entry["profile"] for the
    watchlist screener, so it does not need to consume the same
    deterministic random stream the scored fields do.
    """
    rng = random.Random(seed)
    book = []
    counter = 1
    for archetype in PRESETS:
        identities = _DEMO_IDENTITY_POOL[archetype]
        for i in range(per_archetype):
            full_name, date_of_birth = identities[i % len(identities)]
            book.append({
                "customer_id": f"CUS-{100000 + counter}",
                "archetype": archetype,
                "values": make_variant(archetype, rng),
                "identity": {"full_name": full_name, "date_of_birth": date_of_birth},
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


def band_chip(band: str, show_prefix: bool = False) -> str:
    colour = BAND_COLOUR.get(band, INK_MUTED)
    prefix = f"{t('common.band_prefix')} " if show_prefix else ""
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
        f'background:{colour};color:#fff;font-weight:600;font-size:0.85rem;'
        f'font-family:{FONT_STACK};">{prefix}{band_label(band)}</span>'
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
    return (
        f'<div style="display:inline-block;padding:2px 10px;border-radius:999px;'
        f'background:{colour};color:#fff;font-weight:600;font-size:0.85rem;'
        f'font-family:{FONT_STACK};">{t("common.case_prefix")} {status_label(status)}</div>'
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
            x=(lower + upper) / 2, y=-0.55, text=band_label(band), showarrow=False,
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
        hovertemplate=f"{t('chart.score_word')} %{{x}}<extra></extra>",
    ))
    fig.update_xaxes(
        range=[0, 100], tickvals=[0, 25, 50, 75, 100], showgrid=False,
        zeroline=False, linecolor=AXIS_LINE, tickfont=dict(size=11, color=INK_MUTED),
    )
    fig.update_yaxes(range=[-1.0, 2.1], visible=False)
    caption = t("chart.model_band", band=band_label(model_band))
    if model_band != risk_band:
        caption += t("chart.policy_floored", band=band_label(risk_band))
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

    hover = (
        f"<b>%{{customdata[1]}}</b><br>{t('chart.hover_contribution')} %{{x:+.4f}} (log-odds)"
        f"<br>{t('chart.hover_dimension')}: %{{customdata[0]}}<extra></extra>"
    )
    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker=dict(color=colours, cornerradius=4),
        text=[f"{v:+.3f}" for v in values],
        textposition="outside",
        textfont=dict(size=11, color=INK_SECONDARY, family=FONT_STACK),
        cliponaxis=False,
        customdata=[[d, f["statement"]] for d, f in zip(dimensions, ordered, strict=True)],
        hovertemplate=hover,
    ))
    # Generous headroom on both arms: these charts sit two-to-a-row, so an
    # outside value label on a negative bar has little space before it runs
    # into the tick labels in the gutter. Widening the range buys that gap.
    span = max((abs(v) for v in values), default=1.0) * 1.95 or 1.0
    fig.update_xaxes(
        range=[-span, span], gridcolor=GRIDLINE, griddash="solid", zeroline=True,
        zerolinecolor=AXIS_LINE, zerolinewidth=2, linecolor=AXIS_LINE,
        title=dict(text=t("chart.axis_title"),
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
        f'<span style="color:{INK_SECONDARY};font-size:0.85rem;">{t("chart.raises_risk")}</span>'
        f'&nbsp;&nbsp;&nbsp;<span style="color:{LOWERS_RISK};font-weight:600;">■</span> '
        f'<span style="color:{INK_SECONDARY};font-size:0.85rem;">{t("chart.lowers_risk")}</span>',
        unsafe_allow_html=True,
    )
    with st.expander(t("chart.table_view")):
        st.dataframe(factor_frame(factors), use_container_width=True, hide_index=True)


def render_fired_rules(rules: list[dict[str, Any]]) -> None:
    st.markdown(f"##### {t('rules.header')}")
    if not rules:
        st.caption(t("rules.empty"))
        return
    st.caption(t("rules.caption"))
    st.dataframe(
        pd.DataFrame(rules)[["id", "reason_code", "description", "floor_band", "require_review"]],
        use_container_width=True, hide_index=True,
    )


def watchlist_source_label(source: str) -> str:
    return t(f"watchlist.source_{source}")


def watchlist_category_label(category: str) -> str:
    return t(f"watchlist.category_{category}")


def watchlist_hit_chip(hit: dict[str, Any]) -> str:
    colour = WATCHLIST_SEVERITY_COLOUR[_watchlist_severity(hit["match_score"])]
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
        f'background:{colour};color:#fff;font-weight:600;font-size:0.85rem;'
        f'font-family:{FONT_STACK};">{hit["match_score"]:.0f}%</span>'
    )


def render_watchlist_preview(hits: list[dict[str, Any]]) -> None:
    """Read-only preview for the sandbox tab: nothing has been added to the
    queue yet at this point, so there is no entry to record a disposition
    against — see render_watchlist_panel (Customer 360) for the full
    investigate/dispose workflow. hit["name"]/["reason"] can now come from a
    real, network-ingested OFAC/UN/EU list (scripts/refresh_watchlists.py),
    not just this module's own fictional seed data — HTML-escaped before
    going through unsafe_allow_html for the same reason a disposition note
    is, even though this text is ops-ingested rather than end-user-typed."""
    if not hits:
        st.caption(t("sim.watchlist_none"))
        return
    for hit in hits:
        st.markdown(
            f'<div style="padding:6px 0;border-bottom:1px solid {HAIRLINE};">'
            f'<span style="font-weight:600;font-family:{FONT_STACK};">{html.escape(hit["name"])}</span> '
            f'{watchlist_hit_chip(hit)} '
            f'<span style="color:{INK_SECONDARY};font-size:0.85rem;">'
            f'{watchlist_source_label(hit["list_source"])} · {watchlist_category_label(hit["category"])}</span><br>'
            f'<span style="color:{INK_MUTED};font-size:0.85rem;">{html.escape(hit["reason"])}</span></div>',
            unsafe_allow_html=True,
        )


def watchlist_badge_text(entry: dict[str, Any]) -> str:
    """The Operations Queue table's compact watchlist indicator — full
    detail (match %, source, category, disposition) lives in Customer 360's
    panel, reached by opening that customer. Mirrors rule_badge_text's
    shape: a dash when clean, otherwise a count plus the most severe
    unresolved hit's score — resolved (disposed) hits do not count toward
    it, since they no longer need a reviewer's attention."""
    profile = entry["profile"]
    hits = screen_customer(profile.get("full_name"), profile.get("date_of_birth"),
                            profile.get("country_of_residence"))
    if not hits:
        return "—"
    dispositions = entry.get("watchlist_dispositions", {})
    unresolved = [h for h in hits if h["id"] not in dispositions]
    if not unresolved:
        return f"✅ {len(hits)}"
    return f"⚠️ {len(unresolved)} · {unresolved[0]['match_score']:.0f}%"


def render_result_header(result: dict[str, Any]) -> None:
    score = result["risk_score"]
    left, right = st.columns([2, 3], gap="large")
    with left:
        st.markdown(
            f'<div style="font-size:3.4rem;line-height:1.05;font-weight:650;color:{INK};'
            f'font-family:{FONT_STACK};">{score:.1f}</div>'
            f'<div style="color:{INK_MUTED};font-size:0.85rem;margin-bottom:8px;'
            f'font-family:{FONT_STACK};">{t("result.composite_score")}</div>'
            + band_chip(result["risk_band"], show_prefix=True),
            unsafe_allow_html=True,
        )
    with right:
        a, b, c = st.columns(3)
        a.metric(t("result.credit_default"), f"{result['credit']['probability']:.2%}")
        b.metric(t("result.financial_crime"), f"{result['financial_crime']['probability']:.2%}")
        c.metric(t("result.latency"), f"{result['latency_ms']:.0f} ms")

    st.plotly_chart(
        score_position_strip(score, result["model_band"], result["risk_band"]),
        use_container_width=True, config={"displayModeBar": False},
    )

    flags = []
    if result.get("band_floor_applied"):
        flags.append(t("result.flag_band_floor"))
    if result.get("requires_review"):
        flags.append(t("result.flag_review"))
    if result.get("degraded"):
        flags.append(t("result.flag_degraded"))
    for flag in flags:
        st.warning(flag)

    st.caption(t("result.caption", model=result["model_version"], policy=result["policy_version"],
                  scored_at=result["scored_at"]))


def render_profile_summary(entry: dict[str, Any]) -> None:
    p = entry["profile"]
    dash = "—"
    st.markdown(
        f"**{t('field.full_name')}** {html.escape(p['full_name']) if p.get('full_name') else dash}"
        f"&nbsp;&nbsp;·&nbsp;&nbsp;"
        f"**{t('profile.segment')}** {vocab_label('segment', p['segment']) if p.get('segment') else dash}"
        f"&nbsp;&nbsp;·&nbsp;&nbsp;"
        f"**{t('profile.occupation')}** "
        f"{vocab_label('occupation', p['occupation']) if p.get('occupation') else dash}"
        f"&nbsp;&nbsp;·&nbsp;&nbsp;"
        f"**{t('profile.employment')}** "
        f"{vocab_label('employment_status', p['employment_status']) if p.get('employment_status') else dash}"
        f"&nbsp;&nbsp;·&nbsp;&nbsp;"
        f"**{t('profile.residency')}** "
        f"{vocab_label('residency_status', p['residency_status']) if p.get('residency_status') else dash} "
        f"({p.get('country_of_residence', dash)})"
        f"&nbsp;&nbsp;·&nbsp;&nbsp;**{t('profile.age')}** {p.get('age', dash)}"
        f"&nbsp;&nbsp;·&nbsp;&nbsp;**{t('profile.account_age')}** "
        f"{p.get('account_age_months', dash)} {t('profile.account_age_unit')}"
        f"&nbsp;&nbsp;·&nbsp;&nbsp;**{t('profile.products_held')}** {p.get('num_products_held', dash)}",
        unsafe_allow_html=True,
    )
    st.caption(t("profile.source", archetype=archetype_label(entry.get("archetype", "custom"))))


_DECISION_ICON = {"approved": "✅", "escalated_aml": "🚨", "kyc_requested": "📋", "blocked": "⛔"}


def render_timeline(entries: list[dict[str, Any]]) -> None:
    """Newest first. Combines score events, pushed transactions/AML events,
    case decisions and standalone notes into one chronological feed — the
    note/actor text is the one piece of this that is operator-typed free
    text, so it is HTML-escaped before going into an ``unsafe_allow_html``
    block; everything else here is an enum-like value this module controls."""
    if not entries:
        st.caption(t("timeline.empty"))
        return
    for item in sorted(entries, key=lambda e: _parse_dt(e["at"]), reverse=True):
        at = _parse_dt(item["at"]).strftime("%Y-%m-%d %H:%M UTC")
        kind = item["kind"]
        if kind == "scored":
            icon = "📊"
            text = t("timeline.scored", band=band_label(item["risk_band"]), score=f"{item['risk_score']:.1f}",
                      note=html.escape(item.get("note", "")))
        elif kind == "event":
            status_txt = t("timeline.event_rescored") if item["rescored"] else item["reason"]
            change = t("timeline.event_band_changed") if item.get("band_changed") else ""
            icon = "🔔"
            text = t("timeline.event", event_type=vocab_label("event_type", item["event_type"]),
                      amount=f"{item['amount']:,.0f}", status=status_txt, change=change)
        elif kind == "note":
            icon = "🗒️"
            text = t("timeline.note", actor=html.escape(item["actor"]), note=html.escape(item["note"]))
        else:
            icon = _DECISION_ICON.get(item["action"], "📌")
            text = t("timeline.decision", label=status_label(item["action"]), actor=html.escape(item["actor"]),
                      note=html.escape(item["note"]))
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
    """Persist a scored case (insert or re-score). The store preserves an
    existing case's status and appends a 'scored' timeline row; a re-score never
    un-decides a case (see WorkflowStore.upsert_case)."""
    get_store().upsert_case(
        customer_id, profile, narratives, result,
        archetype=archetype, actor=current_actor(), note=note,
    )


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
        # identity (name/DOB) merged into the stored profile here, never into
        # the payload above — see the Watchlist screening section's docstring.
        profile = {**candidate["values"], **candidate["identity"]}
        add_to_queue(candidate["customer_id"], profile, candidate["narratives"], result,
                     archetype=candidate["archetype"])
        succeeded += 1
    return succeeded, errors


def record_event(customer_id: str, event: dict[str, Any], outcome: dict[str, Any]) -> None:
    store = get_store()
    if customer_id not in st.session_state.queue:
        return
    store.add_timeline(
        customer_id, "event", current_actor(), event_type=event["event_type"],
        amount=event["amount"], reason=outcome["reason"], rescored=outcome["rescored"],
        band_changed=outcome["band_changed"],
    )
    if outcome["rescored"] and outcome.get("result"):
        store.update_result(customer_id, outcome["result"])


def compute_kpis(queue: dict[str, dict[str, Any]], rules: list[dict[str, Any]] = ()) -> dict[str, int]:
    """``rules`` defaults to empty so this is a strict no-op — identical
    output to before the Rule Builder existed — for any caller that doesn't
    pass one. When rules ARE passed, the high-risk and SLA counts are read
    off the live custom-rule-adjusted band (apply_custom_rules below), not
    the API's own band: a rule that escalates a customer is meant to show up
    here immediately, the same way a real trigger would."""
    now = dt.datetime.now(dt.UTC)
    high_risk_pending = sla_breaches = escalated = watchlist_pending = 0
    for entry in queue.values():
        band = apply_custom_rules(entry, rules)["band"]
        status = entry["status"]
        if status == "pending_review" and band in ("High", "Extreme"):
            high_risk_pending += 1
        if status == "pending_review":
            due = _parse_dt(entry["result"]["scored_at"]) + dt.timedelta(hours=SLA_HOURS.get(band, 72))
            if now > due:
                sla_breaches += 1
        if status == "escalated_aml":
            escalated += 1
        profile = entry["profile"]
        hits = screen_customer(profile.get("full_name"), profile.get("date_of_birth"),
                                profile.get("country_of_residence"))
        dispositions = entry.get("watchlist_dispositions", {})
        if any(h["id"] not in dispositions for h in hits):
            watchlist_pending += 1
    return {"total": len(queue), "high_risk_pending": high_risk_pending,
            "sla_breaches": sla_breaches, "escalated": escalated, "watchlist_pending": watchlist_pending}


def queue_dataframe(
    queue: dict[str, dict[str, Any]], search: str, bands: list[str], statuses: list[str],
    rules: list[dict[str, Any]] = (),
) -> pd.DataFrame:
    """Filters (band/status/search) still read the API's own unadjusted band —
    what the customer's model reading actually is — but the queue is SORTED
    by the live custom-rule-adjusted band/score (a rule that escalates a
    customer should visibly move them up the triage order), and a new
    "Custom rules" column surfaces the audit-trail badge described in the
    module docstring: which rule(s) fired and where they pushed this
    customer, without ever touching the original ``col.band``/``col.score``
    values next to it."""
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
        overlay = apply_custom_rules(entry, rules)
        scored_at = _parse_dt(result["scored_at"])
        due = scored_at + dt.timedelta(hours=SLA_HOURS.get(overlay["band"], 72))
        breached = status == "pending_review" and now > due
        rows.append({
            t("col.customer_id"): entry["customer_id"],
            t("col.band"): f"{BAND_DOT.get(band, '⚪')} {band_label(band)}",
            t("col.score"): round(result["risk_score"], 1),
            t("col.custom_rules"): rule_badge_text(overlay),
            t("col.watchlist"): watchlist_badge_text(entry),
            t("col.segment"): vocab_label("segment", entry["profile"]["segment"])
                              if entry["profile"].get("segment") else "—",
            t("col.country"): entry["profile"].get("country_of_residence", "—"),
            t("col.credit_risk"): f"{result['credit']['probability']:.1%}",
            t("col.crime_risk"): f"{result['financial_crime']['probability']:.1%}",
            t("col.status"): status_label(status),
            t("col.sla"): t("col.breached") if breached else due.strftime("%Y-%m-%d %H:%M UTC"),
            t("col.scored"): scored_at.strftime("%Y-%m-%d %H:%M UTC"),
            "_band_rank": BAND_RANK.get(overlay["band"], 9),
            "_effective_score": overlay["score"],
            "_customer_id": entry["customer_id"],
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["_band_rank", "_effective_score"], ascending=[True, False], ignore_index=True)


# --------------------------------------------------------------------------
# Rule Builder engine — the Visual Rule Builder & Dynamic Policy Engine's
# evaluation core. Session-local, same workflow-layer convention as the
# Operations Queue above (see module docstring): st.session_state.custom_rules
# is a list of rule dicts built entirely by the UI in page_rulebuilder()
# below, never by parsing user-typed text, so nothing in this module ever
# calls eval() on anything a risk manager enters. Every rule is evaluated
# fresh on every rerun against the field values already sitting in
# entry["profile"]/entry["result"] — never against another rule's output —
# so an overlay is always a pure function of (current rule set, ORIGINAL API
# result). It is computed here and only here; nothing below ever assigns
# into entry["result"].
#
# The one property this section exists to guarantee, mirroring
# src/crr/rules/engine.py's own structurally-enforced contract: a custom
# rule can only ADD points or RAISE the effective band floor. There is no
# "subtract points" action and no "lower band" verb anywhere in the schema
# below — the UI simply never offers one — so this tool cannot be used to
# quietly soften a model's read on a customer.
# --------------------------------------------------------------------------


def _resolve_field_value(field_key: str, profile: dict[str, Any], result: dict[str, Any]) -> Any:
    if field_key == "risk_score":
        return result.get("risk_score")
    if field_key == "risk_band":
        return result.get("risk_band")
    if field_key in ("watchlist_match_score", "watchlist_category"):
        hits = screen_customer(profile.get("full_name"), profile.get("date_of_birth"),
                                profile.get("country_of_residence"))
        if field_key == "watchlist_match_score":
            return hits[0]["match_score"] if hits else 0.0
        return hits[0]["category"] if hits else "none"
    return profile.get(field_key)


def _condition_matches(cond: dict[str, Any], profile: dict[str, Any], result: dict[str, Any]) -> bool:
    """The only place a stored condition is interpreted. ``cond["value"]``
    was produced by a selectbox/multiselect/number_input when the rule was
    built, never by parsing free text, so there is no expression grammar to
    defend here. A missing field value evaluates to False for every
    operator — mirroring how src/crr/rules/expressions.py treats a missing
    comparison operand — rather than raising, so one incomplete profile can
    never crash the whole queue's evaluation."""
    actual = _resolve_field_value(cond["field"], profile, result)
    if actual is None:
        return False
    op, target = cond["operator"], cond["value"]
    try:
        if op == "==":
            return actual == target
        if op == "!=":
            return actual != target
        if op == "in":
            return actual in target
        if op == ">":
            return float(actual) > float(target)
        if op == ">=":
            return float(actual) >= float(target)
        if op == "<":
            return float(actual) < float(target)
        if op == "<=":
            return float(actual) <= float(target)
    except (TypeError, ValueError):
        return False
    return False


def _rule_matches(rule: dict[str, Any], profile: dict[str, Any], result: dict[str, Any]) -> bool:
    conditions = rule.get("conditions") or []
    if not conditions:
        return False
    outcomes = [_condition_matches(c, profile, result) for c in conditions]
    return all(outcomes) if rule.get("combine", "AND") == "AND" else any(outcomes)


def _band_for_score(score: float) -> str:
    """Same BAND_CUTOFFS scale score_position_strip already draws — used
    ONLY inside this raise-only overlay, never to second-guess the API's own
    authoritative band (see BAND_CUTOFFS's own docstring above)."""
    for band in BAND_ORDER:
        if score <= BAND_CUTOFFS[band]:
            return band
    return "Extreme"


def _max_band(a: str, b: str) -> str:
    """Higher-severity of the two bands — the same raise-only combinator
    src/crr/rules/engine.py's own ``_max_band`` implements, reproduced here
    because this module borrows nothing from the API's process."""
    return a if BAND_RANK.get(a, 9) <= BAND_RANK.get(b, 9) else b


def apply_custom_rules(entry: dict[str, Any], rules: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the live custom-rule overlay for one queue entry from the
    CURRENT rule set. Raise-only by construction: ``points_added`` sums only
    non-negative numbers (the builder never lets an "add points" rule take a
    negative value), and ``band`` is the max-severity of the model's own
    band, every fired rule's floor, and the band the points-adjusted score
    would occupy — so the result can only ever be a MORE severe read than
    the API returned, never softer. Disabled rules are skipped. Never
    mutates ``entry``."""
    result = entry["result"]
    profile = entry["profile"]
    base_score = float(result["risk_score"])
    base_band = result["risk_band"]
    points_added = 0.0
    band_floor = base_band
    fired: list[dict[str, Any]] = []
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        if _rule_matches(rule, profile, result):
            fired.append(rule)
            if rule["action_type"] == "add_points":
                points_added += max(0.0, float(rule.get("action_points") or 0.0))
            else:
                band_floor = _max_band(band_floor, rule.get("action_band", base_band))
    effective_score = min(100.0, base_score + points_added)
    effective_band = _max_band(band_floor, _band_for_score(effective_score))
    return {
        "score": effective_score, "band": effective_band,
        "base_score": base_score, "base_band": base_band,
        "points_added": points_added, "fired_rules": fired,
        "adjusted": bool(fired),
    }


def rule_badge_text(overlay: dict[str, Any]) -> str:
    """The Operations Queue table's compact audit-trail badge — full detail
    (rule names, conditions, actions) lives in Customer 360's overlay
    section (render_rule_overlay), reached by opening that customer."""
    if not overlay["adjusted"]:
        return "—"
    dot = BAND_DOT.get(overlay["band"], "⚪")
    return f'🏷️ {len(overlay["fired_rules"])} · {dot} {band_label(overlay["band"])}'


def _max_rule_number(rules: list[dict[str, Any]]) -> int:
    """Highest N across the persisted rules' ``CR-NNN`` ids, so a new session
    continues the numbering rather than restarting at 1 and colliding with an
    existing rule's primary key."""
    highest = 0
    for rule in rules:
        rid = str(rule.get("id", ""))
        if rid.startswith("CR-") and rid[3:].isdigit():
            highest = max(highest, int(rid[3:]))
    return highest


def rule_conditions_text(rule: dict[str, Any]) -> str:
    combine_key = f"rulebuilder.combine_{rule.get('combine', 'AND').lower()}"
    joiner = f" {t(combine_key).split(' — ')[0]} "  # "AND"/"OR" without the trailing explainer
    parts = []
    for cond in rule.get("conditions", []):
        field_key = cond["field"]
        label = t(_RULE_FIELD_I18N[field_key])
        op_label = t(_OP_LABEL_KEY[cond["operator"]])
        fmt = _rule_value_format(field_key)
        value = cond["value"]
        value_text = ", ".join(fmt(v) for v in value) if isinstance(value, list) else fmt(value)
        parts.append(f"{label} {op_label} {value_text}")
    return joiner.join(parts)


def rule_action_text(rule: dict[str, Any]) -> str:
    if rule["action_type"] == "add_points":
        return t("rulebuilder.action_summary_points", points=rule["action_points"])
    return t("rulebuilder.action_summary_band", band=band_label(rule["action_band"]))


def render_rule_overlay(entry: dict[str, Any], rules: list[dict[str, Any]]) -> None:
    """Customer 360's audit trail for the Rule Builder: exactly which custom
    rule(s) fired for THIS customer and what each one did, computed live —
    not read back from anywhere, since nothing here is ever persisted.
    Deliberately not appended to entry["timeline"]: the timeline is a record
    of things that actually happened (a score, a pushed event, a recorded
    decision), and this overlay is a live present-tense reading that can
    change the moment a rule is toggled or edited, which would make a
    timeline entry for it retroactively inaccurate."""
    overlay = apply_custom_rules(entry, rules)
    if not overlay["adjusted"]:
        st.caption(t("rulebuilder.c360_none"))
        return
    st.warning(t("rulebuilder.c360_summary", n=len(overlay["fired_rules"]), points=overlay["points_added"],
                  score=overlay["score"], band=band_label(overlay["band"])))
    for rule in overlay["fired_rules"]:
        st.markdown(
            f'<div style="padding:6px 0;border-bottom:1px solid {HAIRLINE};">'
            f'<span style="font-weight:600;font-family:{FONT_STACK};">🏷️ {html.escape(rule["name"])}</span><br>'
            f'<span style="color:{INK_SECONDARY};font-size:0.85rem;font-family:{FONT_STACK};">'
            f'{rule_conditions_text(rule)} → {rule_action_text(rule)}</span></div>',
            unsafe_allow_html=True,
        )


# --------------------------------------------------------------------------
# Audit Log — an append-only record of who did what, when. Session-local
# like every other workflow-layer list in this file (see module docstring),
# but immutable in the one sense that matters for a demo: no button anywhere
# in this file removes or edits an entry once log_audit() has appended it —
# only new entries are ever added, the same one-way guarantee a real
# compliance audit trail enforces server-side.
# --------------------------------------------------------------------------


def log_audit(action: str, customer_id: str | None = None, **detail: Any) -> None:
    """Append one row to the persisted, hash-chained audit log, attributed to
    the logged-in user. Never edits or removes a prior entry — the store
    exposes no path to (see WorkflowStore.append_audit)."""
    get_store().append_audit(
        action, actor=current_actor(), role=current_role(), customer_id=customer_id, detail=detail,
    )


def audit_action_label(action: str) -> str:
    return t(f"auditlog.action_{action}")


def audit_detail_text(entry: dict[str, Any]) -> str:
    action, d = entry["action"], entry["detail"]
    if action == "case_decision":
        return t("auditlog.detail_decision", label=status_label(d["decision"]), note=d["note"])
    if action == "note_added":
        return t("auditlog.detail_note", note=d["note"])
    if action == "rule_created":
        return t("auditlog.detail_rule_created", name=d["name"], conditions=d["conditions"],
                  action=d["action_summary"])
    if action == "rule_deleted":
        return t("auditlog.detail_rule_deleted", name=d["name"])
    if action == "rule_toggled":
        key = "auditlog.detail_rule_toggled_on" if d["enabled"] else "auditlog.detail_rule_toggled_off"
        return t(key, name=d["name"])
    if action == "language_switched":
        return t("auditlog.detail_language", previous=d["previous"], current=d["current"])
    if action == "role_switched":
        return t("auditlog.detail_role", previous=d["previous"], current=d["current"])
    if action == "watchlist_disposition":
        label = t(f"watchlist.disp_{d['disposition']}")
        return t("auditlog.detail_watchlist", name=d["hit_name"], source=watchlist_source_label(d["list_source"]),
                  label=label, note=d["note"])
    if action == "user_created":
        return t("auditlog.detail_user_created", username=d["username"], role=role_label(d["role"]))
    return ""


# --------------------------------------------------------------------------
# Watchlist & sanctions screening — Customer 360 workflow. screen_customer()
# (crr.screening.matcher-backed) and the demo identity pool live with the
# other constants near the top of this file; this section is the
# investigate/dispose UI built on top of it. A disposition, once recorded,
# is never edited or removed from entry["watchlist_dispositions"] — the
# same append-only discipline as the audit log above, and for the same
# reason: it is a compliance decision, not draft state.
# --------------------------------------------------------------------------


def render_watchlist_panel(entry: dict[str, Any]) -> None:
    cid = entry["customer_id"]
    show_flash(f"watchlist_{cid}")
    profile = entry["profile"]
    hits = screen_customer(profile.get("full_name"), profile.get("date_of_birth"),
                            profile.get("country_of_residence"))
    if not hits:
        st.caption(t("watchlist.none"))
        return

    dispositions = entry.setdefault("watchlist_dispositions", {})
    can_decide = has_permission("decide_case")
    decide_help = None if can_decide else t("permission.decide_case_denied", role=role_label(current_role()))

    for hit in hits:
        disposition = dispositions.get(hit["id"])
        with st.container(border=True):
            st.markdown(
                f'<div><span style="font-weight:600;font-family:{FONT_STACK};">{html.escape(hit["name"])}</span>'
                f'&nbsp;&nbsp;{watchlist_hit_chip(hit)}&nbsp;&nbsp;'
                f'<span style="color:{INK_SECONDARY};font-size:0.85rem;">'
                f'{watchlist_source_label(hit["list_source"])} · {watchlist_category_label(hit["category"])}'
                f'</span></div>',
                unsafe_allow_html=True,
            )
            st.caption(hit["reason"])

            if disposition is not None:
                icon = "✅" if disposition["disposition"] == "false_positive" else "🚨"
                label = t(f"watchlist.disp_{disposition['disposition']}")
                st.markdown(
                    f'<div><span style="font-family:{FONT_STACK};font-size:0.9rem;">{icon} '
                    + t("watchlist.disposition_line", label=label, actor=html.escape(disposition["actor"]),
                        note=html.escape(disposition["note"]))
                    + "</span></div>",
                    unsafe_allow_html=True,
                )
                continue

            with st.form(f"watchlist_disp_{cid}_{hit['id']}"):
                note = st.text_area(t("watchlist.note_label"), placeholder=t("watchlist.note_placeholder"),
                                     height=70, key=f"watchlist_note_{cid}_{hit['id']}")
                b1, b2 = st.columns(2)
                false_positive = b1.form_submit_button(t("watchlist.mark_false_positive"),
                                                         use_container_width=True, disabled=not can_decide,
                                                         help=decide_help)
                true_positive = b2.form_submit_button(t("watchlist.mark_true_positive"),
                                                        use_container_width=True, disabled=not can_decide,
                                                        help=decide_help)

            new_disposition = "false_positive" if false_positive else "true_positive" if true_positive else None
            if new_disposition is None:
                continue
            if not can_decide:
                st.error(decide_help)
                continue
            if not note.strip():
                st.error(t("watchlist.note_required"))
                continue

            store = get_store()
            actor = current_actor()
            store.add_disposition(
                cid, hit["id"], new_disposition, note.strip(), actor, current_role(),
                hit_name=hit["name"], list_source=hit["list_source"],
            )
            log_audit("watchlist_disposition", customer_id=cid, hit_id=hit["id"], hit_name=hit["name"],
                      list_source=hit["list_source"], disposition=new_disposition, note=note.strip())
            if new_disposition == "true_positive":
                escalation_note = t("watchlist.auto_escalated_note", name=hit["name"],
                                     source=watchlist_source_label(hit["list_source"]), note=note.strip())
                store.set_status(cid, "escalated_aml")
                store.add_timeline(cid, "decision", actor, action="escalated_aml", note=escalation_note)
                log_audit("case_decision", customer_id=cid, decision="escalated_aml", note=escalation_note)
            flash_success(f"watchlist_{cid}", t("watchlist.disposition_recorded"))
            st.rerun()


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------


def page_queue() -> None:
    st.title(t("queue.title"))
    st.caption(t("queue.caption"))
    show_flash("queue")

    if not st.session_state.queue:
        st.info(t("queue.empty"))
        if st.button(t("queue.load_demo"), type="primary"):
            with st.spinner("…"):
                ok, errors = seed_queue()
            if ok:
                st.rerun()
            else:
                st.error(t("queue.load_fail", detail=errors[0] if errors else ""))
        return

    kpis = compute_kpis(st.session_state.queue, st.session_state.custom_rules)
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric(t("queue.kpi_total"), kpis["total"])
    k2.metric(t("queue.kpi_high_risk"), kpis["high_risk_pending"])
    k3.metric(t("queue.kpi_sla"), kpis["sla_breaches"])
    k4.metric(t("queue.kpi_escalated"), kpis["escalated"])
    k5.metric(t("queue.kpi_watchlist"), kpis["watchlist_pending"])
    st.caption(t("queue.sla_caption", extreme=SLA_HOURS["Extreme"], high=SLA_HOURS["High"],
                  medium=SLA_HOURS["Medium"], low=SLA_HOURS["Low"]))

    st.divider()
    f1, f2, f3, f4 = st.columns([2.2, 1.3, 1.3, 0.9])
    search = f1.text_input(t("queue.search_label"), "")
    bands = f2.multiselect(t("queue.band_filter_label"), BAND_ORDER, default=list(BAND_ORDER),
                            format_func=band_label)
    statuses = f3.multiselect(t("queue.status_filter_label"), list(STATUS_KEYS), default=list(STATUS_KEYS),
                               format_func=status_label)
    f4.markdown("<div style='height:1.85rem'></div>", unsafe_allow_html=True)
    if f4.button(t("queue.reload_button"), use_container_width=True, help=t("queue.reload_help")):
        st.session_state.book_seed = st.session_state.get("book_seed", 42) + 1
        with st.spinner("…"):
            seed_queue()
        st.rerun()

    frame = queue_dataframe(st.session_state.queue, search, bands, statuses, st.session_state.custom_rules)
    if frame.empty:
        st.caption(t("queue.no_match"))
    else:
        display_cols = [t(k) for k in ("col.customer_id", "col.band", "col.score", "col.custom_rules",
                                        "col.watchlist", "col.segment", "col.country", "col.credit_risk",
                                        "col.crime_risk", "col.status", "col.sla", "col.scored")]
        event = st.dataframe(
            frame[display_cols], use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="single-row",
            column_config={t("col.score"): st.column_config.NumberColumn(format="%.1f")},
        )
        st.caption(t("queue.shown_caption", shown=len(frame), total=len(st.session_state.queue)))
        if event.selection.rows:
            st.session_state.selected_customer = frame.iloc[event.selection.rows[0]]["_customer_id"]
            st.session_state.nav = "customer360"
            st.rerun()

    st.divider()
    with st.expander(t("queue.onboard_expander")):
        st.caption(t("queue.onboard_caption"))
        next_id = f"CUS-{100000 + len(st.session_state.queue) + 1}"
        c1, c2, c3 = st.columns([2, 1, 1])
        # Keying on language too: a stable key across a language switch makes
        # Streamlit treat this as the same widget and keep showing the PRIOR
        # run's format_func output for the already-selected option (observed
        # directly — the other selects below have no explicit key and do not
        # have this problem, which is what points at the key as the cause).
        # Resetting to the first preset on a language switch is a cheap,
        # acceptable trade for never showing a stale-language label.
        onboard_preset = c1.selectbox(t("queue.onboard_profile_label"), list(PRESETS),
                                       format_func=preset_label, key=f"onboard_preset_{st.session_state.language}")
        onboard_id = c2.text_input(t("queue.onboard_id_label"), value=next_id, key="onboard_id")
        c3.markdown("<div style='height:1.85rem'></div>", unsafe_allow_html=True)
        if c3.button(t("queue.onboard_submit"), type="primary", use_container_width=True, key="onboard_submit"):
            base = PRESETS[onboard_preset]
            values = {k: v for k, v in base.items() if k != "_narratives"}
            payload = {
                "customer": {"customer_id": onboard_id, "snapshot_date": dt.date.today().isoformat(), **values},
                "narratives": base["_narratives"],
                "explain": True,
                "audience": "internal",
            }
            try:
                with st.spinner("…"):
                    result = api_score(payload)
            except ApiError as exc:
                st.error(str(exc))
            else:
                full_name, date_of_birth = _DEMO_IDENTITY_POOL[onboard_preset][0]
                profile = {**values, "full_name": full_name, "date_of_birth": date_of_birth}
                add_to_queue(onboard_id, profile, base["_narratives"], result, archetype=onboard_preset)
                st.session_state.selected_customer = onboard_id
                flash_success("queue", t("queue.onboard_success", id=onboard_id, band=band_label(result["risk_band"])))
                st.rerun()


def render_action_panel(entry: dict[str, Any]) -> None:
    st.markdown(f"##### {t('action.header')}")
    show_flash(f"decision_{entry['customer_id']}")
    st.markdown(status_chip(entry["status"]), unsafe_allow_html=True)
    if entry["status"] != "pending_review":
        st.caption(t("action.already_actioned"))

    cid = entry["customer_id"]
    can_decide = has_permission("decide_case")
    decide_help = None if can_decide else t("permission.decide_case_denied", role=role_label(current_role()))
    with st.form(f"decision_form_{cid}"):
        note = st.text_area(
            t("action.note_label"), placeholder=t("action.note_placeholder"),
            height=90, key=f"decision_note_{cid}",
        )
        note_only = st.form_submit_button(t("action.add_note"), use_container_width=True)
        b1, b2 = st.columns(2)
        b3, b4 = st.columns(2)
        approve = b1.form_submit_button(t("action.approve"), use_container_width=True,
                                         disabled=not can_decide, help=decide_help)
        escalate = b2.form_submit_button(t("action.escalate"), use_container_width=True,
                                          disabled=not can_decide, help=decide_help)
        kyc = b3.form_submit_button(t("action.kyc"), use_container_width=True)
        block = b4.form_submit_button(t("action.block"), use_container_width=True,
                                       disabled=not can_decide, help=decide_help)

    action = None
    if approve:
        action = "approved"
    elif escalate:
        action = "escalated_aml"
    elif kyc:
        action = "kyc_requested"
    elif block:
        action = "blocked"

    # Defence in depth: the buttons above are already disabled client-side
    # for a role that lacks decide_case, but the handler re-checks before
    # acting rather than trusting a disabled attribute alone.
    if action in ("approved", "escalated_aml", "blocked") and not can_decide:
        st.error(decide_help)
        return

    if action:
        if not note.strip():
            st.error(t("action.note_required"))
        else:
            store = get_store()
            store.set_status(cid, action)
            store.add_timeline(cid, "decision", current_actor(), action=action, note=note.strip())
            log_audit("case_decision", customer_id=cid, decision=action, note=note.strip())
            flash_success(f"decision_{cid}", t("action.recorded", label=status_label(action)))
            st.rerun()
    elif note_only:
        if not note.strip():
            st.error(t("action.note_required"))
        else:
            get_store().add_timeline(cid, "note", current_actor(), note=note.strip())
            log_audit("note_added", customer_id=cid, note=note.strip())
            flash_success(f"decision_{cid}", t("action.note_added"))
            st.rerun()


def render_explainability(customer_id: str) -> None:
    st.caption(t("explain.caption"))
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
        st.markdown(f"##### {t('explain.internal_header', n=len(all_factors))}")
        render_factor_block(all_factors, t("explain.no_codes"))
    with right:
        st.markdown(f"##### {t('explain.customer_header', n=len(customer_all))}")
        render_factor_block(customer_all, t("explain.no_customer_codes"))

    visible_rule_ids = {r["id"] for r in customer_view["fired_rules"]}
    suppressed_rules = [r for r in internal["fired_rules"] if r["id"] not in visible_rule_ids]

    if suppressed or suppressed_rules:
        parts = []
        if suppressed:
            parts.append(t("explain.withheld_codes", n=len(suppressed),
                            codes=", ".join(f"`{f['code']}`" for f in suppressed)))
        if suppressed_rules:
            parts.append(t("explain.withheld_rules", n=len(suppressed_rules),
                            rules=", ".join(f"`{r['id']}`" for r in suppressed_rules)))
        st.warning(t("explain.not_shown_prefix", detail="  \n".join(parts)))
        if suppressed:
            st.dataframe(factor_frame(suppressed), use_container_width=True, hide_index=True)
        if suppressed_rules:
            st.dataframe(pd.DataFrame(suppressed_rules)[["id", "reason_code", "description", "floor_band"]],
                         use_container_width=True, hide_index=True)
    else:
        st.success(t("explain.all_disclosable"))

    with st.expander(t("explain.filter_expander")):
        frame = factor_frame(all_factors)
        if frame.empty:
            st.caption(t("explain.no_codes_decision"))
        else:
            frame["customer_visible"] = frame["code"].isin(visible_codes)
            f1, f2, f3 = st.columns(3)
            dimensions = f1.multiselect(t("explain.dimension_label"), sorted(frame["dimension"].unique()),
                                        default=list(sorted(frame["dimension"].unique())), key=f"dim_{customer_id}")
            directions = f2.multiselect(t("explain.direction_label"), sorted(frame["direction"].unique()),
                                        default=list(sorted(frame["direction"].unique())), key=f"dir_{customer_id}")
            audiences = f3.multiselect(
                t("explain.visibility_label"), [t("explain.customer_visible"), t("explain.internal_only")],
                default=[t("explain.customer_visible"), t("explain.internal_only")], key=f"vis_{customer_id}")
            mask = frame["dimension"].isin(dimensions) & frame["direction"].isin(directions)
            wanted = [v for v, name in ((True, t("explain.customer_visible")), (False, t("explain.internal_only")))
                      if name in audiences]
            mask &= frame["customer_visible"].isin(wanted)
            filtered = frame[mask]
            st.dataframe(filtered, use_container_width=True, hide_index=True)
            st.caption(t("explain.shown_count", shown=len(filtered), total=len(frame)))

    render_fired_rules(internal["fired_rules"])


def page_customer360() -> None:
    cid = st.session_state.get("selected_customer")
    entry = st.session_state.queue.get(cid) if cid else None
    if entry is None:
        st.title(t("c360.no_customer_title"))
        st.info(t("c360.no_customer_info"))
        if st.button(t("c360.back_to_queue")):
            st.session_state.nav = "queue"
            st.rerun()
        return

    head_l, head_r = st.columns([5, 1])
    head_l.title(t("c360.title", id=cid))
    if head_r.button(t("c360.back_to_queue"), use_container_width=True):
        st.session_state.nav = "queue"
        st.rerun()

    render_profile_summary(entry)
    st.markdown(status_chip(entry["status"]), unsafe_allow_html=True)
    st.divider()

    render_result_header(entry["result"])

    st.markdown(f"##### {t('rulebuilder.c360_header')}")
    render_rule_overlay(entry, st.session_state.custom_rules)

    st.markdown(f"##### {t('watchlist.panel_header')}")
    st.caption(t("watchlist.panel_caption"))
    render_watchlist_panel(entry)

    st.divider()
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown(f"#### {t('c360.event_timeline_header')}")
        render_timeline(entry["timeline"])
    with right:
        render_action_panel(entry)

    st.divider()
    st.markdown(f"### {t('c360.explainability_header')}")
    render_explainability(cid)


def page_simulator() -> None:
    st.title(t("sim.title"))
    st.caption(t("sim.caption"))

    # A manual tab pair, not st.tabs(): st.tabs() ties a tab group's identity
    # to the exact label strings passed in, and those strings change with the
    # language toggle — switching language while on the second tab would
    # otherwise silently reset the view to the first one. Session state here
    # keeps the active tab stable across that rerun the same way the sidebar
    # page nav already does.
    st.session_state.setdefault("sim_active_tab", "event")
    tc1, tc2 = st.columns(2)
    if tc1.button(t("sim.tab_event"), use_container_width=True,
                  type="primary" if st.session_state.sim_active_tab == "event" else "secondary"):
        st.session_state.sim_active_tab = "event"
        st.rerun()
    if tc2.button(t("sim.tab_sandbox"), use_container_width=True,
                  type="primary" if st.session_state.sim_active_tab == "sandbox" else "secondary"):
        st.session_state.sim_active_tab = "sandbox"
        st.rerun()
    st.divider()

    # ---- push an event ----------------------------------------------------
    if st.session_state.sim_active_tab == "event":
        st.markdown(t("sim.event_intro"))
        st.caption(t("sim.event_caption"))

        queue_ids = list(st.session_state.queue)
        options = queue_ids + [t("sim.customer_other")]
        default_idx = options.index(st.session_state["selected_customer"]) \
            if st.session_state.get("selected_customer") in queue_ids else 0
        picked = st.selectbox(t("sim.customer_label"), options, index=default_idx, key="sim_customer_picker")
        event_customer = (
            st.text_input(t("sim.customer_id_label"), value=st.session_state.get("selected_customer") or "CUS-DEMO-001",
                          key="sim_customer_manual")
            if picked == t("sim.customer_other") else picked
        )

        with st.form("event_form"):
            c1, c2, c3 = st.columns(3)
            event_type = c1.selectbox(
                t("sim.event_type_label"), EVENT_TYPES,
                format_func=lambda ev: f"{vocab_label('event_type', ev)}  ·  {t('sim.usually_trigger')}"
                                       if ev in LIKELY_TRIGGERS else vocab_label("event_type", ev),
            )
            amount = c2.number_input(t("sim.amount_label"), 0.0, 100_000_000.0, 75_000.0, step=1_000.0)
            counterparty = c3.selectbox(t("sim.counterparty_label"), COUNTRIES)
            c1, c2 = st.columns(2)
            channel = c1.selectbox(t("sim.channel_label"), CHANNELS, format_func=lambda c: vocab_label("channel", c))
            minutes_ago = c2.slider(t("sim.occurred_label"), 0, 720, 0)
            send_event = st.form_submit_button(t("sim.send_event"), type="primary")

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
                with st.spinner("…"):
                    outcome = api_event(event_customer, event)
            except ApiError as exc:
                st.error(str(exc))
            else:
                record_event(event_customer, event, outcome)
                st.session_state.selected_customer = event_customer
                st.divider()
                reason = outcome["reason"]
                c1, c2, c3, c4 = st.columns(4)
                c1.metric(t("sim.outcome_label"), reason)
                c2.metric(t("sim.rescored_label"), t("common.yes") if outcome["rescored"] else t("common.no"))
                c3.metric(t("sim.band_changed_label"), t("common.yes") if outcome["band_changed"] else t("common.no"))
                c4.metric(t("sim.notified_label"), t("common.yes") if outcome["notified"] else t("common.no"))
                st.caption(t(f"reason_help.{reason}"))

                if outcome["rescored"] and outcome.get("result"):
                    result = outcome["result"]
                    st.divider()
                    st.markdown(f"##### {t('sim.new_score_header', trigger=outcome['triggered_by'])}")

                    if before is not None:
                        d1, d2, d3 = st.columns(3)
                        delta = result["risk_score"] - before["risk_score"]
                        d1.metric(t("sim.score_before"), f"{before['risk_score']:.1f}")
                        d2.metric(t("sim.score_after"), f"{result['risk_score']:.1f}", delta=f"{delta:+.1f}")
                        d3.markdown(
                            f"<div style='color:{INK_MUTED};font-size:0.8rem;margin-bottom:6px;'>{t('sim.band_label')}</div>"
                            + band_chip(before["risk_band"]) + " → " + band_chip(result["risk_band"]),
                            unsafe_allow_html=True,
                        )

                        before_codes = {f["code"] for f in before["top_factors"]}
                        after_codes = {f["code"] for f in result["top_factors"]}
                        dropped_text = {c for c in before_codes - after_codes if c.startswith("TX")}
                        if dropped_text:
                            st.warning(t("sim.narrative_dropped_warning",
                                          codes=", ".join(f"`{c}`" for c in sorted(dropped_text))))

                    render_result_header(result)
                    st.markdown(f"##### {t('sim.why_internal')}")
                    render_factor_block(result["top_factors"], t("sim.no_factor"))
                    render_fired_rules(result["fired_rules"])
                elif reason == "not_yet_scored":
                    st.info(t("sim.event_stored_not_scored"))
                else:
                    st.info(t("sim.event_stored_no_score"))

    # ---- sandbox ------------------------------------------------------------
    else:
        show_flash("sandbox")
        st.markdown(t("sim.sandbox_intro"))

        # See the onboarding preset selectbox above for why the key includes
        # the language.
        preset_name = st.selectbox(t("sim.start_from_profile"), list(PRESETS), format_func=preset_label,
                                    key=f"sandbox_preset_{st.session_state.language}")
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
            customer_id = head_a.text_input(t("sim.customer_id_label"), value=default_sandbox_id)
            snapshot_date = head_b.date_input(t("sim.snapshot_date_label"), value=dt.date.today())
            audience = head_c.selectbox(t("sim.audience_label"), AUDIENCES, help=t("sim.audience_help"),
                                        format_func=lambda a: vocab_label("audience", a))

            values: dict[str, Any] = {}

            with st.expander(t("sim.expander_profile"), expanded=True):
                c1, c2, c3, c4 = st.columns(4)
                values["segment"] = c1.selectbox(t("field.segment"), SEGMENTS, index=SEGMENTS.index(preset["segment"]),
                                                  format_func=lambda v: vocab_label("segment", v))
                values["occupation"] = c2.selectbox(t("field.occupation"), OCCUPATIONS,
                                                     index=OCCUPATIONS.index(preset["occupation"]),
                                                     format_func=lambda v: vocab_label("occupation", v))
                values["employment_status"] = c3.selectbox(
                    t("field.employment"), EMPLOYMENT_STATUSES,
                    index=EMPLOYMENT_STATUSES.index(preset["employment_status"]),
                    format_func=lambda v: vocab_label("employment_status", v))
                values["age"] = c4.slider(t("field.age"), 18, 95, preset["age"])
                c1, c2, c3, c4 = st.columns(4)
                values["years_at_employer"] = c1.slider(t("field.years_at_employer"), 0.0, 40.0,
                                                          preset["years_at_employer"], 0.5)
                values["account_age_months"] = c2.slider(t("field.account_age_months"), 0, 480,
                                                           preset["account_age_months"])
                values["residency_status"] = c3.selectbox(
                    t("field.residency"), RESIDENCY_STATUSES,
                    index=RESIDENCY_STATUSES.index(preset["residency_status"]),
                    format_func=lambda v: vocab_label("residency_status", v))
                values["country_of_residence"] = c4.selectbox(
                    t("field.country"), COUNTRIES, index=COUNTRIES.index(preset["country_of_residence"]))
                values["num_products_held"] = st.slider(t("field.products_held"), 0, 15, preset["num_products_held"])
                # full_name/date_of_birth are deliberately kept OUT of `values`
                # (the dict build_customer_payload below turns into the /score
                # request body) — CustomerPayload has no such field and
                # extra="forbid" would 422 on it. They exist only to drive the
                # watchlist screening preview after scoring, same as every
                # other identity field in this file (see Watchlist section).
                default_name, default_dob = _DEMO_IDENTITY_POOL[preset_name][0]
                c1, c2 = st.columns(2)
                sandbox_full_name = c1.text_input(t("field.full_name"), value=default_name)
                sandbox_dob = c2.date_input(t("field.date_of_birth"),
                                             value=dt.date.fromisoformat(default_dob),
                                             min_value=dt.date(1900, 1, 1), max_value=dt.date.today())

            with st.expander(t("sim.expander_income"), expanded=True):
                c1, c2, c3, c4 = st.columns(4)
                values["declared_annual_income"] = c1.number_input(
                    t("field.declared_income"), 0.0, 50_000_000.0, preset["declared_annual_income"], step=10_000.0)
                values["verified_income_ratio"] = c2.slider(t("field.verified_income_ratio"), 0.0, 2.0,
                                                              preset["verified_income_ratio"], 0.05)
                values["income_volatility_cv"] = c3.slider(t("field.income_volatility"), 0.0, 2.0,
                                                             preset["income_volatility_cv"], 0.02)
                values["bureau_score"] = c4.slider(t("field.bureau_score"), 300, 850, preset["bureau_score"])
                c1, c2, c3, c4 = st.columns(4)
                values["total_credit_limit"] = c1.number_input(
                    t("field.total_credit_limit"), 0.0, 20_000_000.0, preset["total_credit_limit"], step=5_000.0)
                values["credit_utilization_ratio"] = c2.slider(
                    t("field.credit_utilization"), 0.0, 2.0, preset["credit_utilization_ratio"], 0.01)
                values["dti_ratio"] = c3.slider(t("field.dti"), 0.0, 2.0, preset["dti_ratio"], 0.01)
                values["savings_to_income_ratio"] = c4.slider(
                    t("field.savings_to_income"), 0.0, 3.0, preset["savings_to_income_ratio"], 0.05)
                c1, c2, c3, c4 = st.columns(4)
                values["num_open_loans"] = c1.slider(t("field.open_loans"), 0, 20, preset["num_open_loans"])
                values["num_credit_inquiries_12m"] = c2.slider(t("field.credit_inquiries"), 0, 30,
                                                                 preset["num_credit_inquiries_12m"])
                values["delinquencies_30d_12m"] = c3.slider(t("field.delinq_30d"), 0, 20,
                                                              preset["delinquencies_30d_12m"])
                values["delinquencies_90d_24m"] = c4.slider(t("field.delinq_90d"), 0, 20,
                                                              preset["delinquencies_90d_24m"])
                c1, c2, c3, c4 = st.columns(4)
                values["max_days_past_due_24m"] = c1.slider(t("field.max_days_past_due"), 0, 365,
                                                              preset["max_days_past_due_24m"])
                values["prior_default_flag"] = c2.selectbox(t("field.prior_default"), [0, 1],
                                                              index=preset["prior_default_flag"],
                                                              format_func=yes_no_label)
                values["num_bounced_payments_12m"] = c3.slider(t("field.bounced_payments"), 0, 30,
                                                                 preset["num_bounced_payments_12m"])
                values["overdraft_events_12m"] = c4.slider(t("field.overdraft_events"), 0, 60,
                                                             preset["overdraft_events_12m"])
                values["balance_volatility"] = st.slider(t("field.balance_volatility"), 0.0, 2.0,
                                                           preset["balance_volatility"], 0.02)

            with st.expander(t("sim.expander_txn")):
                c1, c2, c3, c4 = st.columns(4)
                values["txn_count_90d"] = c1.slider(t("field.txn_count"), 0, 2000, preset["txn_count_90d"])
                values["cash_intensity_ratio"] = c2.slider(t("field.cash_intensity"), 0.0, 1.0,
                                                             preset["cash_intensity_ratio"], 0.01)
                values["cross_border_txn_ratio"] = c3.slider(t("field.cross_border"), 0.0, 1.0,
                                                               preset["cross_border_txn_ratio"], 0.01)
                values["night_txn_ratio"] = c4.slider(t("field.night_txn"), 0.0, 1.0, preset["night_txn_ratio"], 0.01)
                c1, c2, c3, c4 = st.columns(4)
                values["structuring_score"] = c1.slider(t("field.structuring_score"), 0.0, 1.0,
                                                          preset["structuring_score"], 0.01)
                values["crypto_exposure_ratio_90d"] = c2.slider(t("field.crypto_exposure"), 0.0, 1.0,
                                                                  preset["crypto_exposure_ratio_90d"], 0.01)
                values["gambling_spend_ratio_90d"] = c3.slider(t("field.gambling_spend"), 0.0, 1.0,
                                                                 preset["gambling_spend_ratio_90d"], 0.01)
                values["new_counterparty_ratio_90d"] = c4.slider(
                    t("field.new_counterparties"), 0.0, 1.0, preset["new_counterparty_ratio_90d"], 0.01)

            with st.expander(t("sim.expander_aml")):
                c1, c2, c3, c4 = st.columns(4)
                values["pep_flag"] = c1.selectbox(t("field.pep"), [0, 1], index=preset["pep_flag"],
                                                   format_func=yes_no_label)
                values["sanctions_screen_hits"] = c2.slider(t("field.sanctions_hits"), 0, 10,
                                                              preset["sanctions_screen_hits"])
                values["adverse_media_hits_12m"] = c3.slider(t("field.adverse_media"), 0, 20,
                                                               preset["adverse_media_hits_12m"])
                values["offshore_entity_links"] = c4.slider(t("field.offshore_links"), 0, 20,
                                                              preset["offshore_entity_links"])
                c1, c2, c3, c4 = st.columns(4)
                values["high_risk_jurisdiction_exposure"] = c1.selectbox(
                    t("field.high_risk_jurisdiction"), [0, 1], index=preset["high_risk_jurisdiction_exposure"],
                    format_func=yes_no_label)
                values["medium_risk_jurisdiction_exposure"] = c2.selectbox(
                    t("field.medium_risk_jurisdiction"), [0, 1], index=preset["medium_risk_jurisdiction_exposure"],
                    format_func=yes_no_label)
                values["sar_filed_prior"] = c3.selectbox(t("field.prior_sar"), [0, 1], index=preset["sar_filed_prior"],
                                                          format_func=yes_no_label)
                values["edd_required"] = c4.selectbox(t("field.edd_required"), [0, 1], index=preset["edd_required"],
                                                        format_func=yes_no_label)
                c1, c2, c3 = st.columns(3)
                values["source_of_funds_declared"] = c1.selectbox(
                    t("field.source_of_funds"), SOURCE_OF_FUNDS,
                    index=SOURCE_OF_FUNDS.index(preset["source_of_funds_declared"]),
                    format_func=lambda v: vocab_label("source_of_funds", v))
                values["source_of_funds_verified"] = c2.selectbox(
                    t("field.source_verified"), [0, 1], index=preset["source_of_funds_verified"],
                    format_func=yes_no_label)
                values["kyc_document_completeness"] = c3.slider(
                    t("field.kyc_completeness"), 0.0, 1.0, preset["kyc_document_completeness"], 0.05)
                values["kyc_refresh_overdue_days"] = st.slider(
                    t("field.kyc_refresh_overdue"), 0, 1000, preset["kyc_refresh_overdue_days"])

            with st.expander(t("sim.expander_tier1_aml")):
                st.caption(t("sim.tier1_aml_caption"))
                st.markdown(f"**{t('sim.group_behavioral')}**")
                c1, c2, c3 = st.columns(3)
                values["expected_vs_actual_turnover_ratio"] = c1.slider(
                    t("field.turnover_ratio"), 0.0, 10.0, preset["expected_vs_actual_turnover_ratio"], 0.05)
                values["pass_through_velocity_hours"] = c2.slider(
                    t("field.pass_through_hours"), 0.0, 720.0, preset["pass_through_velocity_hours"], 1.0)
                values["volume_spike_ratio_6m"] = c3.slider(
                    t("field.volume_spike"), 0.0, 10.0, preset["volume_spike_ratio_6m"], 0.05)

                st.markdown(f"**{t('sim.group_digital')}**")
                c1, c2 = st.columns(2)
                values["vpn_or_high_risk_ip_flag"] = c1.selectbox(
                    t("field.vpn_flag"), [0, 1], index=preset["vpn_or_high_risk_ip_flag"], format_func=yes_no_label)
                values["device_change_frequency_30d"] = c2.slider(
                    t("field.device_changes"), 0, 30, preset["device_change_frequency_30d"])

                st.markdown(f"**{t('sim.group_corporate')}**")
                c1, c2 = st.columns(2)
                values["complex_ownership_structure_flag"] = c1.selectbox(
                    t("field.complex_ownership"), [0, 1], index=preset["complex_ownership_structure_flag"],
                    format_func=yes_no_label)
                values["recent_ubo_change_flag"] = c2.selectbox(
                    t("field.ubo_change"), [0, 1], index=preset["recent_ubo_change_flag"], format_func=yes_no_label)

                st.markdown(f"**{t('sim.group_cash_crypto')}**")
                c1, c2 = st.columns(2)
                values["cash_to_total_volume_ratio"] = c1.slider(
                    t("field.cash_to_volume"), 0.0, 1.0, preset["cash_to_total_volume_ratio"], 0.01)
                values["crypto_vasp_exposure_flag"] = c2.selectbox(
                    t("field.crypto_vasp"), [0, 1], index=preset["crypto_vasp_exposure_flag"],
                    format_func=yes_no_label)

            with st.expander(t("sim.expander_narrative")):
                st.caption(t("sim.narrative_caption"))
                n1, n2 = st.columns(2)
                support_call = n1.text_area(t("field.support_call"), preset_narratives["support_call_summary"],
                                             height=110)
                underwriter = n2.text_area(t("field.underwriter_note"), preset_narratives["underwriter_note"],
                                            height=110)
                kyc_extract = st.text_area(t("field.kyc_extract"), preset_narratives["kyc_document_extract"],
                                            height=80)

            unknown = st.multiselect(t("sim.unknown_label"), options=sorted(values), help=t("sim.unknown_help"))

            submitted = st.form_submit_button(t("sim.score_customer"), type="primary")

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
                with st.spinner("…"):
                    result = api_score(payload)
            except ApiError as exc:
                st.error(str(exc))
            else:
                st.session_state.selected_customer = customer_id
                identity = {"full_name": sandbox_full_name.strip(), "date_of_birth": sandbox_dob.isoformat()}
                st.session_state.sandbox_last_result = {
                    "customer_id": customer_id, "profile": customer_payload, "identity": identity,
                    "narratives": narratives, "result": result, "preset": preset_name,
                }
                st.divider()
                render_result_header(result)
                if unknown:
                    st.caption(t("sim.unknown_sent", n=len(unknown), fields=", ".join(sorted(unknown))))
                st.divider()
                st.markdown(f"##### {t('sim.why_audience', audience=vocab_label('audience', audience))}")
                render_factor_block(result["top_factors"], t("sim.no_factor"))
                render_fired_rules(result["fired_rules"])
                st.divider()
                st.markdown(f"##### {t('sim.watchlist_header')}")
                st.caption(t("sim.watchlist_caption"))
                hits = screen_customer(identity["full_name"], identity["date_of_birth"],
                                        customer_payload.get("country_of_residence"))
                render_watchlist_preview(hits)

        last = st.session_state.get("sandbox_last_result")
        if last:
            st.divider()
            if last["customer_id"] in st.session_state.queue:
                st.warning(t("sim.overwrite_warning", id=last["customer_id"]))
            if st.button(t("sim.add_to_queue"), key="sandbox_add_to_queue"):
                add_to_queue(last["customer_id"], {**last["profile"], **last["identity"]}, last["narratives"],
                             last["result"], archetype=f"sandbox ({last['preset']})")
                st.session_state.sandbox_last_result = None
                flash_success("sandbox", t("sim.added_to_queue", id=last["customer_id"]))
                st.rerun()


def render_rule_condition_row(row_id: int) -> dict[str, Any] | str | None:
    """One condition row's widgets. Returns the condition dict once every
    widget in the row has a value, the sentinel "remove" if its ✕ was just
    clicked, or None while incomplete (e.g. an "is one of" multiselect with
    nothing picked yet — the row is simply not counted as a condition until
    it does). Every widget key folds in the row id plus the field/operator
    currently selected (the same trick the onboarding preset selectbox above
    uses to avoid a stale label after a language switch), so switching this
    row from a numeric field to a categorical one can never hand a
    number_input's leftover float to a selectbox expecting a string."""
    field_keys = [key for key, _, _ in RULE_FIELDS]
    c1, c2, c3, c4 = st.columns([2.3, 1.6, 2.3, 0.5])
    field_key = c1.selectbox(
        t("rulebuilder.condition_field"), field_keys, format_func=lambda k: t(_RULE_FIELD_I18N[k]),
        key=f"rb_field_{row_id}",
    )
    operators = _RULE_OPERATORS[_RULE_FIELD_KIND[field_key]]
    operator = c2.selectbox(
        t("rulebuilder.condition_operator"), operators, format_func=lambda op: t(_OP_LABEL_KEY[op]),
        key=f"rb_op_{row_id}_{field_key}",
    )
    options = _rule_field_options(field_key)
    fmt = _rule_value_format(field_key)
    value: Any
    if options is not None and operator == "in":
        value = c3.multiselect(t("rulebuilder.condition_value"), options, format_func=fmt,
                                key=f"rb_val_{row_id}_{field_key}_{operator}") or None
    elif options is not None:
        value = c3.selectbox(t("rulebuilder.condition_value"), options, format_func=fmt,
                              key=f"rb_val_{row_id}_{field_key}_{operator}")
    else:
        value = c3.number_input(t("rulebuilder.condition_value"), value=0.0,
                                 step=_RULE_NUMERIC_STEP.get(field_key, 1.0),
                                 key=f"rb_val_{row_id}_{field_key}_{operator}")
    if c4.button("✕", key=f"rb_remove_{row_id}", help=t("rulebuilder.remove_condition")):
        return "remove"
    if value is None:
        return None
    return {"field": field_key, "operator": operator, "value": value}


def page_rulebuilder() -> None:
    st.title(t("rulebuilder.title"))
    st.caption(t("rulebuilder.caption"))
    show_flash("rulebuilder")

    st.session_state.setdefault("rb_draft_rows", [0])
    st.session_state.setdefault("rb_next_row_id", 1)
    st.session_state.setdefault("rb_form_gen", 0)
    store = get_store()
    rules = st.session_state.custom_rules
    # Persisted rule IDs must not collide across sessions: a fresh session's
    # counter starts from the highest existing CR-NNN, not from zero.
    st.session_state["rule_id_counter"] = _max_rule_number(rules)
    queue = st.session_state.queue
    can_manage = has_permission("manage_rules")
    manage_help = None if can_manage else t("permission.manage_rules_denied", role=role_label(current_role()))

    st.markdown(f"#### {t('rulebuilder.active_rules_header', n=len(rules))}")
    if not rules:
        st.info(t("rulebuilder.no_rules"))
    else:
        for rule in rules:
            match_count = sum(
                1 for entry in queue.values() if _rule_matches(rule, entry["profile"], entry["result"])
            )
            with st.container(border=True):
                top_l, top_c, top_r = st.columns([4, 1.2, 1])
                top_l.markdown(f"**{html.escape(rule['name'])}**")
                new_enabled = top_c.checkbox(t("rulebuilder.enabled_label"), value=rule["enabled"],
                                              key=f"rb_enabled_{rule['id']}", disabled=not can_manage,
                                              help=manage_help)
                if new_enabled != rule["enabled"] and can_manage:
                    store.set_rule_enabled(rule["id"], new_enabled)
                    log_audit("rule_toggled", name=rule["name"], enabled=new_enabled)
                    st.rerun()
                delete_clicked = top_r.button(t("rulebuilder.delete_rule"), key=f"rb_delete_{rule['id']}",
                                               use_container_width=True, disabled=not can_manage,
                                               help=manage_help)
                if delete_clicked and can_manage:
                    store.delete_rule(rule["id"])
                    log_audit("rule_deleted", name=rule["name"])
                    flash_success("rulebuilder", t("rulebuilder.rule_deleted", name=rule["name"]))
                    st.rerun()
                st.caption(f"{rule_conditions_text(rule)}  →  {rule_action_text(rule)}")
                st.caption(t("rulebuilder.matches_count", n=match_count, total=len(queue)))

    st.divider()
    if not can_manage:
        st.info(t("rulebuilder.admin_only_notice"))
        return

    # key= makes this a real stateful widget: expanded= only seeds the
    # initial (pre-interaction) state, and the user's own toggle then wins
    # on every later rerun. Without a key, expanded=not rules is
    # re-evaluated fresh on every rerun and — since it stays False for the
    # whole rest of the session once a first rule exists — would snap the
    # section shut again the instant any widget inside it is touched
    # (observed directly while building a second rule).
    with st.expander(t("rulebuilder.new_rule_header"), expanded=not rules, key="rb_expander"):
        st.markdown(f"**{t('rulebuilder.conditions_label')}**")
        rows_to_remove = []
        draft_conditions = []
        for row_id in st.session_state.rb_draft_rows:
            outcome = render_rule_condition_row(row_id)
            if outcome == "remove":
                rows_to_remove.append(row_id)
            elif outcome is not None:
                draft_conditions.append(outcome)
        if rows_to_remove:
            st.session_state.rb_draft_rows = [r for r in st.session_state.rb_draft_rows if r not in rows_to_remove]
            st.rerun()
        if st.button(t("rulebuilder.add_condition"), key="rb_add_condition"):
            st.session_state.rb_draft_rows.append(st.session_state.rb_next_row_id)
            st.session_state.rb_next_row_id += 1
            st.rerun()
        if not st.session_state.rb_draft_rows:
            st.caption(t("rulebuilder.no_conditions_yet"))

        combine = "AND"
        if len(st.session_state.rb_draft_rows) > 1:
            combine = st.radio(t("rulebuilder.combine_label"), ["AND", "OR"], horizontal=True,
                                format_func=lambda m: t(f"rulebuilder.combine_{m.lower()}"), key="rb_combine")

        st.markdown(f"**{t('rulebuilder.action_label')}**")
        action_type = st.radio(t("rulebuilder.action_type_label"), ["add_points", "force_band"],
                                format_func=lambda a: t(f"rulebuilder.action_{a}"), horizontal=True,
                                key="rb_action_type")
        if action_type == "add_points":
            action_points: float | None = st.slider(t("rulebuilder.points_label"), 0, 50, 15, step=1,
                                                      help=t("rulebuilder.points_help"), key="rb_action_points")
            action_band = None
        else:
            action_band = st.selectbox(t("rulebuilder.band_label"), BAND_ORDER, index=BAND_ORDER.index("High"),
                                        format_func=band_label, help=t("rulebuilder.band_help"),
                                        key="rb_action_band")
            action_points = None
        st.caption(t("rulebuilder.safety_note"))

        if draft_conditions:
            preview_matches = sum(
                1 for entry in queue.values()
                if _rule_matches({"conditions": draft_conditions, "combine": combine, "enabled": True},
                                  entry["profile"], entry["result"])
            )
            st.caption(t("rulebuilder.preview_label", n=preview_matches, total=len(queue)))

        # Keyed on rb_form_gen rather than a bare "rb_name": deleting a
        # widget's session_state entry is the documented way to reset it,
        # but this text_input's underlying component does not pick that
        # deletion up until the WIDGET ITSELF gets a new key (observed
        # directly — a bare pop("rb_name") left the previous rule's name
        # showing after submit). Bumping the generation counter below forces
        # a genuinely fresh widget instance, the same trick already used for
        # condition rows (row_id) and the language-suffixed preset pickers.
        rule_name = st.text_input(t("rulebuilder.name_label"), placeholder=t("rulebuilder.name_placeholder"),
                                   key=f"rb_name_{st.session_state.rb_form_gen}")
        can_submit = bool(rule_name.strip()) and bool(draft_conditions)
        if st.button(t("rulebuilder.add_rule_button"), type="primary", disabled=not can_submit, key="rb_submit"):
            st.session_state.rule_id_counter += 1
            new_rule = {
                "id": f"CR-{st.session_state.rule_id_counter:03d}",
                "name": rule_name.strip(),
                "enabled": True,
                "combine": combine,
                "conditions": draft_conditions,
                "action_type": action_type,
                "action_points": float(action_points) if action_points is not None else None,
                "action_band": action_band,
                "created_at": dt.datetime.now(dt.UTC).isoformat(),
                "created_by": current_actor(),
            }
            store.add_rule(new_rule, actor=current_actor())
            st.session_state.rb_draft_rows = [st.session_state.rb_next_row_id]
            st.session_state.rb_next_row_id += 1
            st.session_state.rb_form_gen += 1
            log_audit("rule_created", name=new_rule["name"], conditions=rule_conditions_text(new_rule),
                      action_summary=rule_action_text(new_rule))
            flash_success("rulebuilder", t("rulebuilder.rule_added", name=new_rule["name"]))
            st.rerun()


def page_auditlog() -> None:
    st.title(t("auditlog.title"))
    st.caption(t("auditlog.caption"))

    # Read fresh from the store rather than the top-of-run session cache: an
    # audit event written earlier in this same run (e.g. a language switch)
    # would otherwise not show until the next rerun.
    log = get_store().list_audit()
    decisions = sum(1 for e in log if e["action"] == "case_decision")
    rule_changes = sum(1 for e in log if e["action"] in ("rule_created", "rule_deleted", "rule_toggled"))
    k1, k2, k3 = st.columns(3)
    k1.metric(t("auditlog.kpi_total"), len(log))
    k2.metric(t("auditlog.kpi_decisions"), decisions)
    k3.metric(t("auditlog.kpi_rule_changes"), rule_changes)

    if not log:
        st.info(t("auditlog.empty"))
        return

    st.divider()
    f1, f2, f3 = st.columns([2.2, 1.4, 1.4])
    search = f1.text_input(t("auditlog.search_label"), "")
    action_types = sorted({e["action"] for e in log})
    actions_filter = f2.multiselect(t("auditlog.action_filter_label"), action_types, default=action_types,
                                     format_func=audit_action_label)
    roles_present = sorted({e["role"] for e in log}, key=lambda r: ROLE_RANK.get(r, 9))
    roles_filter = f3.multiselect(t("auditlog.role_filter_label"), roles_present, default=roles_present,
                                   format_func=role_label)

    rows = []
    for entry in log:
        if entry["action"] not in actions_filter or entry["role"] not in roles_filter:
            continue
        haystack = " ".join([entry["actor"], entry.get("customer_id") or ""]).lower()
        if search and search.lower() not in haystack:
            continue
        rows.append({
            t("auditlog.col_timestamp"): _parse_dt(entry["at"]).strftime("%Y-%m-%d %H:%M:%S UTC"),
            t("auditlog.col_actor"): entry["actor"],
            t("auditlog.col_role"): role_label(entry["role"]),
            t("auditlog.col_action"): audit_action_label(entry["action"]),
            t("auditlog.col_customer"): entry.get("customer_id") or "—",
            t("auditlog.col_details"): audit_detail_text(entry),
            "_at": entry["at"],
        })
    if not rows:
        st.caption(t("auditlog.no_match"))
        return

    frame = pd.DataFrame(rows).sort_values("_at", ascending=False, ignore_index=True)
    display_cols = [c for c in frame.columns if c != "_at"]
    st.dataframe(frame[display_cols], use_container_width=True, hide_index=True)
    st.caption(t("auditlog.shown_caption", shown=len(frame), total=len(log)))


# --------------------------------------------------------------------------
# Authentication
#
# Role is the logged-in user's, read from the database — there is no
# session-state role switcher. The session token can reach this app three
# ways, tried in this order (crr.workflow.auth.extract_bearer_token):
#
#   1. the X-Auth-Token header — for a deployment where a proxy/gateway in
#      front injects it directly;
#   2. the httpOnly crr_session cookie — set by crr.workflow.gateway, never
#      by this app (Streamlit has no API to issue a Set-Cookie; see that
#      module's docstring for why a separate small service exists at all);
#   3. the ``auth`` URL query parameter — the fallback for a bare
#      ``streamlit run app.py`` with no proxy or gateway in front, and the
#      only path that existed before AUTH_GATEWAY_URL was introduced.
#
# Whichever carried it, resolve_session() (crr.workflow.store) validates the
# exact same way — a 256-bit random token, stored only as a SHA-256 hash, that
# expires. With no gateway configured, behaviour is byte-for-byte what it was
# before this section grew: the query parameter, visible in history/logs/the
# referrer header, is a known, documented limitation of that fallback path —
# not of the system as a whole once a gateway is in front of it.
# --------------------------------------------------------------------------

_AUTH_QP = "auth"


def resolve_current_user(store: WorkflowStore) -> dict[str, Any] | None:
    """The logged-in user for this run: the cached session value, or a token
    from a header/cookie/query-param validated against the store. Caches the
    result — and which token resolved it — for the run."""
    cached = st.session_state.get("auth_user")
    if cached:
        return cached
    token = extract_bearer_token(
        st.context.headers.get(AUTH_HEADER_NAME),
        st.context.cookies.get(SESSION_COOKIE_NAME),
        st.query_params.get(_AUTH_QP),
    )
    user = store.resolve_session(token) if token else None
    if user:
        st.session_state["auth_user"] = user
        st.session_state["auth_token"] = token
    return user


def _navigate_to(url: str, **query: str) -> None:
    """A full top-level browser navigation from inside a Streamlit script —
    the only way anything here can hand a value to a different origin-path
    service (crr.workflow.gateway) as a real HTTP request, and the only way a
    response to that request can set an httpOnly cookie the browser will keep.

    An earlier version of this rendered an auto-submitting <form> inside
    components.v1.html's iframe. That iframe is sandboxed WITHOUT
    allow-top-navigation (neither components.v1.html nor st.iframe expose a
    way to change that), so the browser blocks the form's submit with
    "Unsafe attempt to initiate navigation for frame ... sandboxed" — it
    never actually navigated. A <meta http-equiv="refresh"> tag has no such
    restriction: rendered via st.markdown(unsafe_allow_html=True) it lands in
    the TOP-LEVEL page's own DOM (no iframe at all), and a real redirect from
    there is exactly what a <meta refresh> is for. Confirmed with an isolated
    Streamlit test app that this specific tag survives st.markdown's
    CommonMark pass and triggers a genuine navigation.

    The tradeoff: <meta refresh> is GET-only, so query values travel in the
    target URL rather than a POST body. That is a real, narrow exposure for
    the login hop specifically (the one-time bearer token appears in this
    single redirect's URL/logs, though never again afterward — every
    subsequent request carries only the httpOnly cookie), and is called out
    in the README rather than glossed over. Logout has no such exposure: it
    carries no token, only `next`."""
    target = f"{url}?{urllib.parse.urlencode(query)}" if query else url
    st.markdown(f'<meta http-equiv="refresh" content="0;url={html.escape(target)}">', unsafe_allow_html=True)


def render_login_page(store: WorkflowStore) -> None:
    """The full-screen sign-in gate shown until a valid session exists."""
    st.selectbox(
        "🌐", options=["he", "en"], format_func=lambda code: LANGUAGE_LABEL[code],
        key="language", label_visibility="collapsed",
    )
    left, mid, right = st.columns([1, 1.4, 1])
    with mid:
        st.markdown(f"### {t('login.title')}")
        st.caption(t("login.subtitle"))
        with st.form("login_form"):
            username = st.text_input(t("login.username"), key="login_username")
            password = st.text_input(t("login.password"), type="password", key="login_password")
            submitted = st.form_submit_button(t("login.submit"), type="primary", use_container_width=True)
        if submitted:
            user = store.authenticate(username, password)
            if user is None:
                st.error(t("login.failed"))
            else:
                token = store.create_login_session(user["id"])
                store.append_audit("login", actor=user["display_name"], role=user["role"],
                                   detail={"username": user["username"]})
                if AUTH_GATEWAY_URL:
                    # A real page navigation, not st.rerun(): it is the only
                    # way this token becomes an httpOnly cookie. The browser
                    # lands back on "/" with the cookie already set, and that
                    # fresh page load is what resolve_current_user() sees —
                    # there is nothing left to render on this pass.
                    _navigate_to(f"{AUTH_GATEWAY_URL}/adopt", token=token, next="/")
                    st.stop()
                st.query_params[_AUTH_QP] = token
                st.session_state["auth_user"] = user
                st.session_state["auth_token"] = token
                # Land every fresh login on the queue, not wherever the previous
                # session (possibly a different user) happened to be.
                st.session_state.nav = "queue"
                st.session_state.selected_customer = None
                st.rerun()
        with st.expander(t("login.demo_header")):
            st.caption(t("login.demo_hint"))
            for spec in DEMO_USERS:
                st.markdown(
                    f"- **{spec['username']}** / `{spec['password']}` — {role_label(spec['role'])}"
                )


def render_user_panel(store: WorkflowStore) -> None:
    """Sidebar: who is signed in, their role, and a sign-out button. Replaces
    the old free-text reviewer name and the role switcher. An admin also gets a
    compact user-provisioning form."""
    user = current_user()
    st.markdown(f"**{html.escape(user.get('display_name', ''))}**")
    st.caption(f"{t('sidebar.role_label')}: {role_label(user.get('role', 'junior_analyst'))}")
    if st.button(t("sidebar.sign_out"), use_container_width=True, key="sign_out_btn"):
        log_audit("logout")  # attributed to the current user before it is cleared
        store.end_session(st.session_state.get("auth_token"))
        st.session_state.pop("auth_user", None)
        st.session_state.pop("auth_token", None)
        if AUTH_GATEWAY_URL:
            # Clears the browser's httpOnly cookie too — only a real HTTP
            # response (the gateway's) can do that; a Streamlit rerun cannot.
            _navigate_to(f"{AUTH_GATEWAY_URL}/logout", next="/")
            st.stop()
        st.query_params.pop(_AUTH_QP, None)
        st.rerun()

    # Provisioning real accounts is itself an admin-only action, gated by the
    # same manage_rules capability that guards the policy surface.
    if not has_permission("manage_rules"):
        return
    with st.expander(t("users.header")):
        existing = store.list_users()
        st.caption(t("users.existing", n=len(existing)))
        with st.form("create_user_form"):
            new_username = st.text_input(t("users.new_username"), key="new_user_username")
            new_display = st.text_input(t("users.new_display_name"), key="new_user_display")
            new_role = st.selectbox(t("users.new_role"), ROLE_KEYS, format_func=role_label, key="new_user_role")
            new_password = st.text_input(t("users.new_password"), type="password", key="new_user_password")
            created = st.form_submit_button(t("users.create"), use_container_width=True)
        if created:
            try:
                account = store.create_user(new_username, new_display, new_role, new_password)
            except ValueError as exc:
                st.error(t("users.error", detail=str(exc)))
            else:
                log_audit("user_created", username=account["username"], role=account["role"])
                st.success(t("users.created", username=account["username"]))
                st.rerun()

    with st.expander(t("watchlist_sources.header")):
        st.caption(t("watchlist_sources.caption"))
        status = store.watchlist_source_status()
        rows = []
        for source in WATCHLIST_SOURCES:
            row = next((r for r in status if r["source"] == source), None)
            rows.append({
                t("watchlist_sources.col_source"): watchlist_source_label(source),
                t("watchlist_sources.col_count"): row["count"] if row else 0,
                t("watchlist_sources.col_refreshed"): (
                    _parse_dt(row["last_refreshed"]).strftime("%Y-%m-%d %H:%M UTC")
                    if row and row["last_refreshed"] else t("watchlist_sources.never")
                ),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(t("watchlist_sources.refresh_hint"))
        st.code("python scripts/refresh_watchlists.py --source ofac", language="bash")


# --------------------------------------------------------------------------
# App shell
# --------------------------------------------------------------------------

st.set_page_config(page_title="Customer Risk Rating", page_icon="◑", layout="wide")


def _inject_head_signals(lang: str) -> None:
    """Stop the browser's own translate UI (Chrome's Google Translate prompt
    and similar features elsewhere) from rewriting this page's text nodes,
    and set the real `dir`/`lang` attributes Streamlit's own markup never
    exposes a way to set. Machine translation mutates the DOM out from under
    React's own reconciliation — Streamlit's frontend is a React app — and
    the two fighting over the same nodes is what throws the `removeChild`
    crash. `components.v1.html` renders in a same-origin iframe (Streamlit
    serves it from its own server), so its script can reach the PARENT
    document — the actual page Streamlit built — via `window.parent` and add
    the standard "don't translate me" signals there: the literal
    `<meta name="google" content="notranslate">` tag, the `translate="no"`
    attribute and the `notranslate` CSS class — three different mechanisms
    because different translate implementations respect different ones, and
    this app already ships its own Hebrew/English toggle, so there is
    nothing for a browser's translator to usefully add.

    `dir`/`lang` are set here rather than left to the RTL stylesheet below
    for the same reason: it is the one place with access to the real
    `<html>` element, and this runs on every rerun (language switches
    included), so it always reflects the language just rendered.
    """
    components_html(
        f"""
        <script>
        (function () {{
            var d = window.parent.document;
            if (!d.querySelector('meta[name="google"]')) {{
                var meta = d.createElement('meta');
                meta.name = 'google';
                meta.content = 'notranslate';
                d.head.appendChild(meta);
            }}
            d.documentElement.setAttribute('translate', 'no');
            d.documentElement.classList.add('notranslate');
            if (d.body) {{ d.body.classList.add('notranslate'); }}
            d.documentElement.setAttribute('dir', {'rtl' if lang == 'he' else 'ltr'!r});
            d.documentElement.setAttribute('lang', {lang!r});
        }})();
        </script>
        """,
        height=0,
    )


def _apply_rtl_css() -> None:
    """Right-to-left for Hebrew, applied to the app's text flow (headers,
    captions, labels, markdown) so prose reads naturally right-to-left, but
    deliberately NOT to canvas/grid-rendered widgets — the dataframe grid,
    sliders, Plotly SVGs — where mirroring a risk-score slider or a chart axis
    would confuse rather than help. Called once early (before the login gate,
    so the login page is RTL too); a no-op in English.

    [data-testid="stAppViewContainer"] — the flex row holding the sidebar and
    the main content side by side — is deliberately kept LTR. Streamlit's
    mobile sidebar collapse is a hardcoded translateX(-300px) on the sidebar
    that assumes its un-transformed position is flush against the LEFT edge;
    flipping this container to RTL relocates that to the RIGHT edge, so the
    fixed offset no longer clears the sidebar off-screen on a narrow viewport
    (it lands as a ~40px sliver with Hebrew wrapping one char per line, seen at
    390px). Keeping this one container LTR keeps Streamlit's collapse math
    correct in both languages; RTL is re-applied to the content areas inside
    it (stMain, stSidebarContent), which have no such transform."""
    if st.session_state.get("language") != "he":
        return
    st.markdown(
        """
        <style>
          [data-testid="stAppViewContainer"] { direction: ltr; }

          [data-testid="stMain"], [data-testid="stSidebarContent"],
          [data-testid="stMain"] p, [data-testid="stMain"] span, [data-testid="stMain"] label,
          [data-testid="stSidebarContent"] p, [data-testid="stSidebarContent"] span,
          [data-testid="stSidebarContent"] label,
          div[data-testid="stCaptionContainer"], div[data-testid="stMarkdownContainer"],
          div[data-testid="stMetricLabel"], div[data-testid="stWidgetLabel"] {
            direction: rtl;
            text-align: right;
          }
          div[data-testid="stSlider"], div[data-testid="stDataFrame"], div[data-testid="stPlotlyChart"],
          div[data-testid="stNumberInput"], div[data-testid="stDataFrame"] * {
            direction: ltr;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


_inject_head_signals(st.session_state.get("language", "he"))

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

st.session_state.setdefault("selected_customer", None)
st.session_state.setdefault("nav", "queue")
st.session_state.setdefault("book_load_attempted", False)
st.session_state.setdefault("book_seed", 42)
st.session_state.setdefault("language", "he")
# Workflow data (queue, rules, audit) is hydrated from the database on every
# run — see below — not defaulted here, but the keys are seeded empty so any
# read that happens before hydration (or on the login page) is safe.
st.session_state.setdefault("queue", {})
st.session_state.setdefault("custom_rules", [])
st.session_state.setdefault("audit_log", [])
st.session_state.setdefault("watchlist_entries", [])
# Shadow copy used only to detect a language CHANGE for audit logging.
# Comparing against st.session_state.language directly does not work here:
# Streamlit applies an in-flight widget interaction to its bound
# session_state key before the script body reruns, so by the time this code
# reads it even on the very first line, it already reflects the NEW value —
# a same-render "previous vs current" read sees the new value on both sides
# (observed directly: switching languages never logged an event). This
# shadow key is written only after log_audit() below has already fired, so
# it keeps lagging one step behind until the next real change.
st.session_state.setdefault("_last_seen_language", st.session_state.language)

store = get_store()
_apply_rtl_css()  # defined below; applied before the login gate so login is RTL too

# ---- login gate: nothing below renders until a valid session exists --------
if resolve_current_user(store) is None:
    render_login_page(store)
    st.stop()

# ---- hydrate persisted workflow state from the database, fresh every run ----
# Every UI read below still uses these session keys unchanged; only the writes
# go through the store (which reruns, re-hydrating on the next pass).
st.session_state.queue = store.load_queue()
st.session_state.custom_rules = store.list_rules()
st.session_state.audit_log = store.list_audit()
st.session_state.watchlist_entries = store.list_watchlist_entries()

with st.sidebar:
    st.selectbox(
        "🌐", options=["he", "en"], format_func=lambda code: LANGUAGE_LABEL[code],
        key="language", label_visibility="collapsed",
    )
    _language_just_changed = st.session_state.language != st.session_state._last_seen_language
    if _language_just_changed:
        log_audit("language_switched", previous=LANGUAGE_LABEL[st.session_state._last_seen_language],
                  current=LANGUAGE_LABEL[st.session_state.language])
        st.session_state._last_seen_language = st.session_state.language
    st.markdown(f"### {t('sidebar.title')}")
    st.caption(t("sidebar.subtitle"))
    st.caption(f"{t('sidebar.api_label')} `{API_URL}`")
    api_up = False
    try:
        health = api_health()
        st.success(t("sidebar.api_healthy", models=", ".join(health["models_loaded"]),
                      policy=health["policy_version"], api=health["version"]))
        api_up = True
    except ApiError as exc:
        st.error(str(exc))
        st.caption(t("sidebar.api_start_hint"))

    st.divider()
    render_user_panel(store)

    st.divider()
    st.markdown(f"##### {t('sidebar.nav_header')}")
    for key, label_key in (
        ("queue", "sidebar.nav_queue"),
        ("customer360", "sidebar.nav_customer360"),
        ("simulator", "sidebar.nav_simulator"),
        ("rulebuilder", "sidebar.nav_rulebuilder"),
        ("auditlog", "sidebar.nav_auditlog"),
    ):
        disabled = key == "customer360" and not st.session_state.get("selected_customer")
        if st.button(t(label_key), key=f"nav_{key}", use_container_width=True,
                     type="primary" if st.session_state.nav == key else "secondary", disabled=disabled):
            st.session_state.nav = key
            st.rerun()

    st.divider()
    st.caption(t("sidebar.footer"))

# Seed the synthetic demo book once — only when the persisted case store is
# actually empty, so a refresh or restart loads the saved cases rather than
# re-seeding on top of them. book_load_attempted guards against re-running the
# (possibly failing) seed every rerun within a session; the rerun re-hydrates
# the queue so the freshly seeded cases render immediately.
if api_up and store.case_count() == 0 and not st.session_state.book_load_attempted:
    st.session_state.book_load_attempted = True
    with st.spinner("…"):
        seed_queue()
    st.rerun()

if st.session_state.nav == "customer360":
    page_customer360()
elif st.session_state.nav == "simulator":
    page_simulator()
elif st.session_state.nav == "rulebuilder":
    page_rulebuilder()
elif st.session_state.nav == "auditlog":
    page_auditlog()
else:
    page_queue()
