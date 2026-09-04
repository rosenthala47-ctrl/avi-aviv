"""Reference taxonomies used by the synthetic data generator.

IMPORTANT — these lists are ILLUSTRATIVE and are meant only to give the alpha
model realistic-looking categorical structure. They are **not** an authoritative
compliance source. In production every list below must be replaced by a live,
versioned feed:

  * jurisdiction risk  -> FATF public statements + the bank's own country model
  * sanctions          -> OFAC / EU / UN / local consolidated lists
  * PEP                -> a licensed PEP data vendor
  * adverse media      -> a licensed screening vendor

The generator never claims these values describe any real person or entity.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------
# Segments
# --------------------------------------------------------------------------

SEGMENTS: dict[str, float] = {
    "retail": 0.70,
    "sme": 0.18,
    "private_banking": 0.08,
    "corporate": 0.04,
}


# --------------------------------------------------------------------------
# Occupations
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Occupation:
    """An occupation and the AML/credit priors we attach to it.

    cash_intensity   0..1  how much of the customer's activity is plausibly cash
    pep_propensity   0..1  relative likelihood of being politically exposed
    income_mu        lognormal mu of annual income (local currency units)
    income_sigma     lognormal sigma
    self_employed_p  probability the customer is self-employed in this role
    """

    name: str
    cash_intensity: float
    pep_propensity: float
    income_mu: float
    income_sigma: float
    self_employed_p: float


OCCUPATIONS: tuple[Occupation, ...] = (
    Occupation("software_engineer", 0.05, 0.01, 12.45, 0.45, 0.12),
    Occupation("physician", 0.10, 0.02, 12.75, 0.50, 0.35),
    Occupation("nurse", 0.06, 0.01, 12.05, 0.35, 0.04),
    Occupation("teacher", 0.05, 0.02, 11.85, 0.30, 0.03),
    Occupation("accountant", 0.08, 0.03, 12.25, 0.42, 0.22),
    Occupation("lawyer", 0.12, 0.09, 12.60, 0.60, 0.40),
    Occupation("civil_servant", 0.05, 0.22, 11.95, 0.33, 0.01),
    Occupation("military_officer", 0.05, 0.14, 12.10, 0.32, 0.00),
    Occupation("politician_aide", 0.07, 0.55, 12.05, 0.45, 0.02),
    Occupation("real_estate_agent", 0.28, 0.05, 12.35, 0.75, 0.55),
    Occupation("construction_contractor", 0.42, 0.04, 12.20, 0.72, 0.62),
    Occupation("restaurant_owner", 0.55, 0.02, 12.00, 0.70, 0.85),
    Occupation("retail_shop_owner", 0.50, 0.02, 11.90, 0.68, 0.88),
    Occupation("taxi_driver", 0.60, 0.01, 11.45, 0.45, 0.72),
    Occupation("truck_driver", 0.20, 0.01, 11.70, 0.35, 0.30),
    Occupation("import_export_trader", 0.45, 0.06, 12.55, 0.85, 0.70),
    Occupation("jewellery_dealer", 0.62, 0.03, 12.40, 0.80, 0.75),
    Occupation("art_antiques_dealer", 0.58, 0.04, 12.35, 0.85, 0.78),
    Occupation("crypto_trader", 0.35, 0.02, 12.30, 1.05, 0.80),
    Occupation("money_service_operator", 0.70, 0.05, 12.25, 0.75, 0.68),
    Occupation("casino_operator", 0.68, 0.04, 12.60, 0.80, 0.55),
    Occupation("logistics_manager", 0.12, 0.02, 12.15, 0.40, 0.08),
    Occupation("financial_analyst", 0.05, 0.04, 12.50, 0.50, 0.06),
    Occupation("consultant", 0.15, 0.06, 12.40, 0.70, 0.58),
    Occupation("student", 0.10, 0.01, 10.60, 0.55, 0.02),
    Occupation("retired", 0.12, 0.03, 11.35, 0.45, 0.00),
    Occupation("unemployed", 0.18, 0.01, 10.30, 0.60, 0.00),
    Occupation("factory_worker", 0.15, 0.01, 11.55, 0.28, 0.02),
    Occupation("sales_representative", 0.18, 0.01, 11.90, 0.45, 0.15),
    Occupation("nonprofit_director", 0.22, 0.10, 11.95, 0.45, 0.05),
)

OCCUPATION_NAMES: tuple[str, ...] = tuple(o.name for o in OCCUPATIONS)

EMPLOYMENT_STATUSES: tuple[str, ...] = (
    "salaried",
    "self_employed",
    "business_owner",
    "unemployed",
    "retired",
    "student",
)

EDUCATION_LEVELS: tuple[str, ...] = (
    "high_school",
    "vocational",
    "bachelor",
    "master",
    "doctorate",
)

MARITAL_STATUSES: tuple[str, ...] = ("single", "married", "divorced", "widowed")

CHANNELS: tuple[str, ...] = ("branch", "online", "mobile", "relationship_manager")

RESIDENCY_STATUSES: tuple[str, ...] = ("citizen", "permanent_resident", "temporary_visa", "non_resident")


# --------------------------------------------------------------------------
# Jurisdictions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Jurisdiction:
    """A country/territory with an illustrative AML risk tier.

    tier: "low" | "medium" | "high" | "prohibited"
    """

    code: str
    name: str
    tier: str
    base_weight: float  # relative share of the synthetic customer book


JURISDICTIONS: tuple[Jurisdiction, ...] = (
    # Domestic / low risk
    Jurisdiction("IL", "Israel", "low", 55.0),
    Jurisdiction("US", "United States", "low", 8.0),
    Jurisdiction("GB", "United Kingdom", "low", 5.0),
    Jurisdiction("DE", "Germany", "low", 4.0),
    Jurisdiction("FR", "France", "low", 3.0),
    Jurisdiction("NL", "Netherlands", "low", 2.0),
    Jurisdiction("CA", "Canada", "low", 2.0),
    Jurisdiction("AU", "Australia", "low", 1.5),
    Jurisdiction("JP", "Japan", "low", 1.2),
    Jurisdiction("SG", "Singapore", "low", 1.5),
    Jurisdiction("CH", "Switzerland", "low", 1.8),
    # Medium risk / offshore-leaning financial centres
    Jurisdiction("CY", "Cyprus", "medium", 1.6),
    Jurisdiction("MT", "Malta", "medium", 0.9),
    Jurisdiction("AE", "United Arab Emirates", "medium", 2.2),
    Jurisdiction("TR", "Turkey", "medium", 1.4),
    Jurisdiction("HK", "Hong Kong SAR", "medium", 1.3),
    Jurisdiction("PA", "Panama", "medium", 0.6),
    Jurisdiction("KY", "Cayman Islands", "medium", 0.5),
    Jurisdiction("VG", "British Virgin Islands", "medium", 0.4),
    Jurisdiction("SC", "Seychelles", "medium", 0.3),
    Jurisdiction("BZ", "Belize", "medium", 0.25),
    Jurisdiction("RU", "Russia", "medium", 0.8),
    Jurisdiction("UA", "Ukraine", "medium", 0.6),
    Jurisdiction("NG", "Nigeria", "medium", 0.5),
    Jurisdiction("ZA", "South Africa", "medium", 0.5),
    # High risk (illustrative; mirrors the shape of an FATF increased-monitoring list)
    Jurisdiction("SY", "Syria", "high", 0.10),
    Jurisdiction("YE", "Yemen", "high", 0.10),
    Jurisdiction("AF", "Afghanistan", "high", 0.10),
    Jurisdiction("MM", "Myanmar", "high", 0.10),
    Jurisdiction("VU", "Vanuatu", "high", 0.08),
    Jurisdiction("HT", "Haiti", "high", 0.08),
    Jurisdiction("SS", "South Sudan", "high", 0.08),
    Jurisdiction("LY", "Libya", "high", 0.10),
)

HIGH_RISK_CODES: frozenset[str] = frozenset(j.code for j in JURISDICTIONS if j.tier == "high")
MEDIUM_RISK_CODES: frozenset[str] = frozenset(j.code for j in JURISDICTIONS if j.tier == "medium")
OFFSHORE_CODES: frozenset[str] = frozenset({"KY", "VG", "SC", "BZ", "PA", "MT", "CY"})

JURISDICTION_TIER: dict[str, str] = {j.code: j.tier for j in JURISDICTIONS}
JURISDICTION_NAME: dict[str, str] = {j.code: j.name for j in JURISDICTIONS}

# Numeric risk weight per tier, used inside the ground-truth structural model.
TIER_RISK_WEIGHT: dict[str, float] = {"low": 0.0, "medium": 0.45, "high": 1.0, "prohibited": 1.6}


# --------------------------------------------------------------------------
# AML / KYC vocabularies
# --------------------------------------------------------------------------

SOURCE_OF_FUNDS: tuple[str, ...] = (
    "salary",
    "business_income",
    "investment_returns",
    "inheritance",
    "property_sale",
    "gift",
    "loan_proceeds",
    "crypto_disposal",
    "undeclared",
)

# Relative weight for a "clean" customer vs. a customer with high latent concealment.
SOF_WEIGHTS_CLEAN: dict[str, float] = {
    "salary": 52.0,
    "business_income": 22.0,
    "investment_returns": 9.0,
    "inheritance": 5.0,
    "property_sale": 5.0,
    "gift": 3.0,
    "loan_proceeds": 2.5,
    "crypto_disposal": 1.0,
    "undeclared": 0.5,
}

SOF_WEIGHTS_OPAQUE: dict[str, float] = {
    "salary": 12.0,
    "business_income": 20.0,
    "investment_returns": 12.0,
    "inheritance": 10.0,
    "property_sale": 8.0,
    "gift": 12.0,
    "loan_proceeds": 6.0,
    "crypto_disposal": 10.0,
    "undeclared": 10.0,
}

# Source-of-funds categories that require enhanced documentation.
SOF_REQUIRING_EDD: frozenset[str] = frozenset({"gift", "crypto_disposal", "undeclared", "loan_proceeds"})

PEP_RELATIONSHIPS: tuple[str, ...] = ("none", "self", "family_member", "close_associate")

OWNERSHIP_TRANSPARENCY: tuple[str, ...] = ("clear", "partial", "opaque")

PRODUCT_TYPES: tuple[str, ...] = (
    "current_account",
    "savings_account",
    "credit_card",
    "personal_loan",
    "mortgage",
    "business_loan",
    "investment_portfolio",
    "fx_account",
)

UNDERWRITER_DECISIONS: tuple[str, ...] = ("approve", "approve_with_conditions", "refer", "decline")

RISK_BANDS: tuple[str, ...] = ("Low", "Medium", "High", "Extreme")

# Default band cut-offs on the 0-100 risk score. Overridable from config/risk_policy.yaml
# so a risk manager can retune them without touching code.
DEFAULT_BAND_THRESHOLDS: dict[str, float] = {"Low": 25.0, "Medium": 50.0, "High": 75.0}


# --------------------------------------------------------------------------
# Event stream
# --------------------------------------------------------------------------

EVENT_TYPES: tuple[str, ...] = (
    "card_purchase",
    "atm_withdrawal",
    "cash_deposit",
    "salary_credit",
    "wire_transfer_in",
    "wire_transfer_out",
    "direct_debit",
    "loan_repayment",
    "missed_payment",
    "overdraft_breach",
    "crypto_transfer",
    "chargeback",
)

# Events that should force an immediate re-score in the real-time engine.
TRIGGER_EVENT_TYPES: frozenset[str] = frozenset(
    {"missed_payment", "overdraft_breach", "chargeback", "cash_deposit", "wire_transfer_out", "crypto_transfer"}
)


def occupation_by_name(name: str) -> Occupation:
    """Look up an :class:`Occupation` by its canonical name."""
    for occ in OCCUPATIONS:
        if occ.name == name:
            return occ
    raise KeyError(f"unknown occupation: {name!r}")
