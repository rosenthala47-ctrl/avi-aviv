"""The internal shape a suspicious-activity report is assembled into, before
crr.reporting.goaml_xml serializes it. Kept separate from that serializer for
the same reason crr.screening.models is separate from crr.screening.parsers:
one place defines what a report IS, another defines how it is written out —
a future second output format (a PDF filing packet, a different FIU's
variant schema) reuses this shape without touching the XML code at all.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ReportParty:
    """The person the report is about — built from the case's UI-only
    identity fields (see app.py's watchlist-screening comment on why a name/
    DOB never reaches the scoring API), not from anything the model itself
    used."""

    first_name: str
    last_name: str
    date_of_birth: str | None = None
    nationality: str | None = None
    id_number: str | None = None
    id_type: str = "national_id"
    occupation: str | None = None
    is_politically_exposed: bool = False


@dataclass(frozen=True, slots=True)
class ReportTransaction:
    """One supporting transaction line, typically sourced from the case's
    own event timeline (see crr.reporting.builder)."""

    transaction_ref: str
    date: str
    amount: float
    currency: str = "USD"
    direction: str = "unknown"  # "in" | "out" | "unknown"
    mode: str = "unknown"  # cash | wire | card | other
    counterparty_country: str | None = None
    description: str = ""


@dataclass(frozen=True, slots=True)
class SarReport:
    """One suspicious transaction/activity report, ready for
    crr.reporting.goaml_xml.to_xml(). ``report_ref`` is CRR's own internal
    reference (becomes the XML's ``entity_reference``) — the FIU assigns its
    own tracking number on receipt; this is what ties a filed report back to
    the case and audit trail on our side."""

    report_ref: str
    customer_id: str
    submitted_at: dt.datetime
    reporting_officer_name: str
    reporting_officer_role: str
    subject: ReportParty
    reason: str
    report_code: str = "STR"  # STR (suspicious transaction) or SAR (suspicious activity)
    submission_code: str = "N"  # N=new, A=amend, C=cancel
    indicators: tuple[str, ...] = field(default_factory=tuple)
    transactions: tuple[ReportTransaction, ...] = field(default_factory=tuple)
    #: Supporting evidence folded into the narrative (see goaml_xml.to_xml) —
    #: not a standard goAML element, so it never becomes its own XML tag.
    watchlist_hits: tuple[dict, ...] = field(default_factory=tuple)


#: The indicator vocabulary this app offers when filing — a small, clearly
#: labeled-as-illustrative subset of real AML typology codes (structuring,
#: PEP exposure, sanctions proximity, and so on). A real deployment must
#: reconcile this list with the filing FIU's own current indicator-code
#: table (see goaml_xml.py's module docstring) before real submission —
#: FIUs do not all use identical codes even on the same goAML platform.
INDICATOR_CODES: tuple[str, ...] = (
    "STRUCTURING",
    "UNUSUAL_CASH_ACTIVITY",
    "SANCTIONS_OR_PEP_MATCH",
    "ADVERSE_MEDIA",
    "OPAQUE_OWNERSHIP",
    "HIGH_RISK_JURISDICTION",
    "RAPID_MOVEMENT_OF_FUNDS",
    "INCONSISTENT_WITH_PROFILE",
)
