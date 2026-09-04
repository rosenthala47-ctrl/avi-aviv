"""Assembles a SarReport from an existing case — the queue entry dict shape
app.py already builds (profile, timeline, result) — so filing a report never
means re-typing what the system already knows about a customer.

Kept as pure functions with no Streamlit or persistence dependency, same as
crr.screening.matcher: app.py calls this with data it already has in
session state (the case entry, the watchlist hits screen_customer() already
computed) and hands the result to WorkflowStore.create_report — this module
itself never touches a database.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from crr.reporting.models import ReportParty, ReportTransaction, SarReport


def _split_name(full_name: str) -> tuple[str, str]:
    """"First Last" -> ("First", "Last"); a middle/compound name keeps
    everything after the first token as the last name (goAML's schema wants
    exactly first_name/last_name, not a single free-text name field) — a
    real deployment with structured KYC name fields would pass first/last
    directly instead of splitting a display name."""
    parts = full_name.strip().split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return (parts[0], "") if parts else ("", "")


def _transactions_from_timeline(timeline: list[dict[str, Any]]) -> tuple[ReportTransaction, ...]:
    """Every "event" timeline entry as a supporting transaction line.
    Direction/counterparty/currency are NOT in the timeline payload
    (record_event only persists event_type/amount/reason/rescored/
    band_changed — see app.py) so those come through as "unknown"/blank
    rather than fabricated; a real deployment sourcing transactions from a
    core banking feed would have the genuine values here instead."""
    out = []
    for i, item in enumerate(e for e in timeline if e.get("kind") == "event"):
        at = item["at"]
        date_str = at.strftime("%Y-%m-%d") if isinstance(at, dt.datetime) else str(at)[:10]
        out.append(ReportTransaction(
            transaction_ref=f"EVT-{i + 1}",
            date=date_str,
            amount=float(item.get("amount") or 0),
            description=f"{item.get('event_type', '')}: {item.get('reason', '')}".strip(": "),
        ))
    return tuple(out)


def build_report_from_case(
    entry: dict[str, Any], *, reason: str, indicators: list[str],
    officer_name: str, officer_role: str, watchlist_hits: list[dict[str, Any]] | None = None,
    report_code: str = "STR", submitted_at: dt.datetime | None = None,
) -> SarReport:
    """Build a SarReport for ``entry`` (a queue/case dict — see
    WorkflowStore.load_queue). ``reason`` is the filing officer's own
    narrative; ``watchlist_hits`` should be whatever screen_customer() just
    returned for this customer, if any — folded into the XML's reason
    narrative as supporting evidence (see goaml_xml._narrative)."""
    profile = entry.get("profile", {})
    first, last = _split_name(profile.get("full_name", "") or entry["customer_id"])
    submitted_at = submitted_at or dt.datetime.now(dt.UTC)

    subject = ReportParty(
        first_name=first,
        last_name=last,
        date_of_birth=profile.get("date_of_birth"),
        nationality=profile.get("country_of_residence"),
        occupation=profile.get("occupation"),
        is_politically_exposed=bool(profile.get("pep_flag")),
    )

    return SarReport(
        report_ref=f"CRR-{entry['customer_id']}-{submitted_at.strftime('%Y%m%d%H%M%S')}",
        customer_id=entry["customer_id"],
        submitted_at=submitted_at,
        reporting_officer_name=officer_name,
        reporting_officer_role=officer_role,
        subject=subject,
        reason=reason,
        report_code=report_code,
        indicators=tuple(indicators),
        transactions=_transactions_from_timeline(entry.get("timeline", [])),
        watchlist_hits=tuple(watchlist_hits or ()),
    )
