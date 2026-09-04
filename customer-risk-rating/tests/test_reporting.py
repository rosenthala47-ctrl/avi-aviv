"""Tests for crr.reporting: the SarReport model, the goAML-style XML
serializer, and the case-to-report builder. All pure functions, no
Streamlit or database dependency."""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET

import pytest

from crr.reporting.builder import build_report_from_case
from crr.reporting.goaml_xml import to_element, to_xml
from crr.reporting.models import INDICATOR_CODES, ReportParty, ReportTransaction, SarReport

SUBMITTED_AT = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.UTC)


def _minimal_report(**overrides) -> SarReport:
    defaults = dict(
        report_ref="CRR-CUS-1-20260601120000",
        customer_id="CUS-1",
        submitted_at=SUBMITTED_AT,
        reporting_officer_name="Noa Levi",
        reporting_officer_role="compliance_admin",
        subject=ReportParty(first_name="Jane", last_name="Doe"),
        reason="Unusual cash activity.",
    )
    defaults.update(overrides)
    return SarReport(**defaults)


# --------------------------------------------------------------------------
# goaml_xml serialization
# --------------------------------------------------------------------------


def test_to_xml_produces_well_formed_xml_that_round_trips():
    xml_str = to_xml(_minimal_report())
    root = ET.fromstring(xml_str)  # raises on malformed XML
    assert root.tag == "report"


def test_to_xml_starts_with_the_xml_declaration():
    assert to_xml(_minimal_report()).startswith('<?xml version="1.0" encoding="UTF-8"?>')


def test_required_top_level_elements_are_present():
    root = to_element(_minimal_report(report_code="SAR", submission_code="A"))
    assert root.find("report_code").text == "SAR"
    assert root.find("submission_code").text == "A"
    assert root.find("entity_reference").text == "CRR-CUS-1-20260601120000"
    assert root.find("submission_date").text == "2026-06-01"


def test_reporting_person_element():
    root = to_element(_minimal_report())
    officer = root.find("reporting_person")
    assert officer.find("full_name").text == "Noa Levi"
    assert officer.find("role").text == "compliance_admin"


def test_subject_person_fields():
    subject = ReportParty(
        first_name="Mikhail", last_name="Aslanov", date_of_birth="1975-11-02",
        nationality="CY", id_number="AB123", occupation="Trader", is_politically_exposed=True,
    )
    root = to_element(_minimal_report(subject=subject))
    person = root.find("subject/person")
    assert person.find("first_name").text == "Mikhail"
    assert person.find("last_name").text == "Aslanov"
    assert person.find("birthdate").text == "1975-11-02"
    assert person.find("nationality1").text == "CY"
    assert person.find("id_number").text == "AB123"
    assert person.find("id_type").text == "national_id"
    assert person.find("occupation").text == "Trader"
    assert person.find("is_politically_exposed").text == "true"


def test_subject_optional_fields_are_omitted_not_emitted_empty():
    root = to_element(_minimal_report(subject=ReportParty(first_name="Jane", last_name="Doe")))
    person = root.find("subject/person")
    assert person.find("birthdate") is None
    assert person.find("nationality1") is None
    assert person.find("id_number") is None
    assert person.find("is_politically_exposed").text == "false"


def test_indicators_serialize_as_repeated_indicator_string_elements():
    root = to_element(_minimal_report(indicators=("STRUCTURING", "ADVERSE_MEDIA")))
    codes = [e.text for e in root.findall("report_indicators/indicator_string")]
    assert codes == ["STRUCTURING", "ADVERSE_MEDIA"]


def test_no_indicators_still_produces_an_empty_indicators_element():
    root = to_element(_minimal_report(indicators=()))
    assert root.find("report_indicators") is not None
    assert root.findall("report_indicators/indicator_string") == []


def test_transactions_serialize_with_expected_fields():
    txn = ReportTransaction(transaction_ref="T1", date="2026-05-01", amount=1234.5, currency="ILS",
                            direction="out", mode="wire", counterparty_country="AE", description="wire out")
    root = to_element(_minimal_report(transactions=(txn,)))
    el = root.find("transactions/transaction")
    assert el.find("transactionnumber").text == "T1"
    assert el.find("date_transaction").text == "2026-05-01"
    assert el.find("amount_local").text == "1234.50"
    assert el.find("amount_local_currency").text == "ILS"
    assert el.find("t_direction").text == "out"
    assert el.find("t_mode").text == "wire"
    assert el.find("counterparty_country").text == "AE"
    assert el.find("description").text == "wire out"


def test_reason_narrative_includes_officer_text_verbatim():
    root = to_element(_minimal_report(reason="Cash-intensive activity inconsistent with profile."))
    assert root.find("reason").text.startswith("Cash-intensive activity inconsistent with profile.")


def test_reason_narrative_folds_in_watchlist_hits_as_supporting_evidence():
    hits = ({"name": "Mikhail Aslanov", "list_source": "ofac", "match_score": 96.5,
             "reason": "SDN — asset freeze."},)
    root = to_element(_minimal_report(reason="Screening hit under investigation.", watchlist_hits=hits))
    reason_text = root.find("reason").text
    assert "Screening hit under investigation." in reason_text
    assert "Mikhail Aslanov" in reason_text
    assert "OFAC" in reason_text
    assert "96%" in reason_text


def test_reason_narrative_has_no_evidence_section_without_hits():
    root = to_element(_minimal_report(reason="No screening hits on this case.", watchlist_hits=()))
    assert "Supporting watchlist" not in root.find("reason").text


# --------------------------------------------------------------------------
# builder.build_report_from_case
# --------------------------------------------------------------------------


def _entry(**overrides) -> dict:
    base = {
        "customer_id": "CUS-100013",
        "profile": {"full_name": "Mikhail Aslanov", "date_of_birth": "1975-11-02",
                    "country_of_residence": "CY", "occupation": "Import/export trader", "pep_flag": 0},
        "timeline": [],
    }
    base.update(overrides)
    return base


def test_builder_splits_full_name_into_first_and_last():
    report = build_report_from_case(_entry(), reason="r", indicators=[], officer_name="X", officer_role="Y")
    assert report.subject.first_name == "Mikhail"
    assert report.subject.last_name == "Aslanov"


def test_builder_handles_a_single_token_name():
    entry = _entry(profile={"full_name": "Cher"})
    report = build_report_from_case(entry, reason="r", indicators=[], officer_name="X", officer_role="Y")
    assert report.subject.first_name == "Cher"
    assert report.subject.last_name == ""


def test_builder_falls_back_to_customer_id_when_no_name_on_file():
    entry = _entry(profile={})
    report = build_report_from_case(entry, reason="r", indicators=[], officer_name="X", officer_role="Y")
    assert report.subject.first_name == entry["customer_id"]


def test_builder_maps_pep_flag_to_is_politically_exposed():
    report = build_report_from_case(_entry(profile={"full_name": "A B", "pep_flag": 1}),
                                    reason="r", indicators=[], officer_name="X", officer_role="Y")
    assert report.subject.is_politically_exposed is True


def test_builder_sets_report_ref_and_customer_id():
    report = build_report_from_case(_entry(), reason="r", indicators=["STRUCTURING"],
                                    officer_name="X", officer_role="Y", submitted_at=SUBMITTED_AT)
    assert report.customer_id == "CUS-100013"
    assert report.report_ref == "CRR-CUS-100013-20260601120000"
    assert report.indicators == ("STRUCTURING",)


def test_builder_extracts_transactions_only_from_event_kind_timeline_entries():
    timeline = [
        {"kind": "scored", "at": SUBMITTED_AT, "risk_band": "High", "risk_score": 80.0},
        {"kind": "event", "at": SUBMITTED_AT, "event_type": "wire_transfer_out", "amount": 5000,
         "reason": "matched trigger", "rescored": True, "band_changed": False},
        {"kind": "note", "at": SUBMITTED_AT, "actor": "x", "note": "n"},
        {"kind": "event", "at": SUBMITTED_AT, "event_type": "cash_deposit", "amount": 900,
         "reason": "no trigger", "rescored": False, "band_changed": False},
    ]
    report = build_report_from_case(_entry(timeline=timeline), reason="r", indicators=[],
                                    officer_name="X", officer_role="Y")
    assert len(report.transactions) == 2
    assert report.transactions[0].amount == 5000
    assert "wire_transfer_out" in report.transactions[0].description
    assert report.transactions[1].amount == 900


def test_builder_transaction_date_handles_datetime_and_string_at_values():
    timeline = [
        {"kind": "event", "at": SUBMITTED_AT, "event_type": "x", "amount": 1, "reason": "r",
         "rescored": False, "band_changed": False},
        {"kind": "event", "at": "2026-07-01T10:00:00", "event_type": "y", "amount": 2, "reason": "r",
         "rescored": False, "band_changed": False},
    ]
    report = build_report_from_case(_entry(timeline=timeline), reason="r", indicators=[],
                                    officer_name="X", officer_role="Y")
    assert report.transactions[0].date == "2026-06-01"
    assert report.transactions[1].date == "2026-07-01"


def test_builder_passes_through_watchlist_hits_untouched():
    hits = [{"name": "Mikhail Aslanov", "list_source": "ofac", "match_score": 96.5, "reason": "SDN"}]
    report = build_report_from_case(_entry(), reason="r", indicators=[], officer_name="X", officer_role="Y",
                                    watchlist_hits=hits)
    assert report.watchlist_hits == tuple(hits)


def test_builder_default_report_code_is_str():
    report = build_report_from_case(_entry(), reason="r", indicators=[], officer_name="X", officer_role="Y")
    assert report.report_code == "STR"


@pytest.mark.parametrize("code", INDICATOR_CODES)
def test_every_indicator_code_serializes_cleanly(code):
    root = to_element(_minimal_report(indicators=(code,)))
    assert root.find("report_indicators/indicator_string").text == code
