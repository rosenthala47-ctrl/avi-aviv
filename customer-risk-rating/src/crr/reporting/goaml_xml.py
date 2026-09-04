"""Serializes a SarReport into a goAML-style XML document.

goAML is the IBM-built AML case/reporting platform UNODC provides to
national Financial Intelligence Units — Israel's Money Laundering and
Terror Financing Prohibition Authority (IMPA) included — for suspicious
transaction/activity report (STR/SAR) intake. Its XML import schema's
overall shape (``report_code``, ``submission_code``, ``entity_reference``,
``reporting_person``, ``reason``, ``report_indicators``, ``transactions``,
``persons``) is well documented and stable across the FIUs that run it,
which is what this module builds against.

**Honest disclosure, matching this project's disclosure for
crr.screening's OFAC/UN/EU parsers**: this was written from documented
schema knowledge, not validated against a live, current XSD — this
environment has no outbound network access to any FIU's portal or published
schema files. Every FIU running goAML — IMPA included — layers its own
customized field set and indicator-code table on top of the base platform,
and that customization is exactly the part no amount of general schema
knowledge can substitute for. Treat this module's output as a **filing-ready
draft that correctly assembles the case evidence into the right shape**, not
as a submission guaranteed to validate against a specific FIU's importer
unmodified — the missing step before a real filing is handing one generated
report to compliance/legal to check against IMPA's current reporting guide,
the same "verify against the real thing" step this project's README already
asks for after a live sanctions-list refresh.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from crr.reporting.models import SarReport


def _add(parent: ET.Element, tag: str, text: str | None) -> ET.Element:
    child = ET.SubElement(parent, tag)
    if text is not None:
        child.text = text
    return child


def _narrative(report: SarReport) -> str:
    """The free-text reason-for-suspicion narrative: the officer's own
    explanation, followed by the supporting evidence this system already
    holds (watchlist hits) — folded into prose here rather than invented as
    non-standard XML elements, since a real SAR narrative citing screening
    hits by name is exactly how compliance officers write these today."""
    lines = [report.reason.strip()]
    if report.watchlist_hits:
        lines.append("")
        lines.append("Supporting watchlist screening evidence:")
        for hit in report.watchlist_hits:
            lines.append(
                f"- {hit.get('name', '?')} ({hit.get('list_source', '?').upper()}, "
                f"{hit.get('match_score', 0):.0f}% match): {hit.get('reason', '')}"
            )
    return "\n".join(lines)


def to_element(report: SarReport) -> ET.Element:
    root = ET.Element("report")
    _add(root, "report_code", report.report_code)
    _add(root, "submission_code", report.submission_code)
    _add(root, "entity_reference", report.report_ref)
    _add(root, "submission_date", report.submitted_at.strftime("%Y-%m-%d"))

    indicators_el = ET.SubElement(root, "report_indicators")
    for code in report.indicators:
        _add(indicators_el, "indicator_string", code)

    officer_el = ET.SubElement(root, "reporting_person")
    _add(officer_el, "full_name", report.reporting_officer_name)
    _add(officer_el, "role", report.reporting_officer_role)

    _add(root, "reason", _narrative(report))

    subject_el = ET.SubElement(root, "subject")
    person_el = ET.SubElement(subject_el, "person")
    _add(person_el, "first_name", report.subject.first_name)
    _add(person_el, "last_name", report.subject.last_name)
    if report.subject.date_of_birth:
        _add(person_el, "birthdate", report.subject.date_of_birth)
    if report.subject.nationality:
        _add(person_el, "nationality1", report.subject.nationality)
    if report.subject.id_number:
        _add(person_el, "id_number", report.subject.id_number)
        _add(person_el, "id_type", report.subject.id_type)
    if report.subject.occupation:
        _add(person_el, "occupation", report.subject.occupation)
    _add(person_el, "is_politically_exposed", "true" if report.subject.is_politically_exposed else "false")

    transactions_el = ET.SubElement(root, "transactions")
    for txn in report.transactions:
        txn_el = ET.SubElement(transactions_el, "transaction")
        _add(txn_el, "transactionnumber", txn.transaction_ref)
        _add(txn_el, "date_transaction", txn.date)
        _add(txn_el, "amount_local", f"{txn.amount:.2f}")
        _add(txn_el, "amount_local_currency", txn.currency)
        _add(txn_el, "t_direction", txn.direction)
        _add(txn_el, "t_mode", txn.mode)
        if txn.counterparty_country:
            _add(txn_el, "counterparty_country", txn.counterparty_country)
        if txn.description:
            _add(txn_el, "description", txn.description)

    return root


def to_xml(report: SarReport) -> str:
    """Pretty-printed UTF-8 XML string, ready to write to a file."""
    root = to_element(report)
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
