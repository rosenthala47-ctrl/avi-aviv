"""Parser tests against small, schema-accurate fixture files (tests/fixtures/
screening/) — real OFAC/UN/EU XML shapes, fictional names. Each fixture is
built from the publisher's actual, documented schema (see the module
docstrings in crr/screening/parsers.py) rather than invented ad hoc, so a
passing test here is evidence the parser reads the real format, not just
whatever shape the test happened to construct."""

from __future__ import annotations

from pathlib import Path

import pytest

from crr.screening.models import WatchlistRecord
from crr.screening.parsers import SOURCES, parse_eu_fsf, parse_ofac_sdn, parse_source, parse_un_consolidated

FIXTURES = Path(__file__).parent / "fixtures" / "screening"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# --------------------------------------------------------------------------
# OFAC SDN
# --------------------------------------------------------------------------


def test_ofac_parses_both_entries():
    records = parse_ofac_sdn(_read("ofac_sample.xml"))
    assert len(records) == 2
    assert {r.name for r in records} == {"Mikhail Aslanov", "Elena Petrova"}


def test_ofac_extracts_aliases_dob_country_program():
    records = parse_ofac_sdn(_read("ofac_sample.xml"))
    aslanov = next(r for r in records if r.name == "Mikhail Aslanov")
    assert aslanov.source == "ofac"
    assert aslanov.source_id == "90001"
    assert aslanov.aliases == ("Michael Aslanov",)
    assert aslanov.dates_of_birth == ("1975-11-02",)
    assert aslanov.countries == ("Cyprus",)
    assert aslanov.program == "SDGT"
    assert "fictional" in aslanov.remarks


def test_ofac_entry_with_no_akalist_element_has_no_aliases():
    petrova = next(r for r in parse_ofac_sdn(_read("ofac_sample.xml")) if r.name == "Elena Petrova")
    assert petrova.aliases == ()


def test_ofac_ignores_the_namespace_uri():
    """The fixture uses a 2026-ish namespace URI; the parser must not hardcode
    a specific version, since Treasury has changed it before."""
    content = _read("ofac_sample.xml")
    assert b"DownloadableOFACData" in content  # the fixture really is namespaced
    assert len(parse_ofac_sdn(content)) == 2


# --------------------------------------------------------------------------
# UN Consolidated List
# --------------------------------------------------------------------------


def test_un_parses_individuals_and_entities():
    records = parse_un_consolidated(_read("un_sample.xml"))
    assert len(records) == 2
    names = {r.name for r in records}
    assert names == {"Khaled Marwan", "Northgate Trading FZE"}


def test_un_individual_fields():
    records = parse_un_consolidated(_read("un_sample.xml"))
    marwan = next(r for r in records if r.name == "Khaled Marwan")
    assert marwan.source == "un"
    assert marwan.source_id == "QDi.9001"
    assert marwan.aliases == ("Khalid Marwan",)
    assert marwan.dates_of_birth == ("1969-06-19",)
    assert marwan.countries == ("United Arab Emirates",)
    assert marwan.program == "Al-Qaida"


def test_un_entity_has_no_individual_only_fields():
    entity = next(r for r in parse_un_consolidated(_read("un_sample.xml")) if r.name == "Northgate Trading FZE")
    assert entity.source_id == "QDe.9002"
    assert entity.dates_of_birth == ()
    assert entity.countries == ("United Arab Emirates",)


# --------------------------------------------------------------------------
# EU FSF
# --------------------------------------------------------------------------


def test_eu_parses_both_entities():
    records = parse_eu_fsf(_read("eu_sample.xml"))
    assert len(records) == 2


def test_eu_strong_name_alias_becomes_primary_others_become_aliases():
    aliyev = next(r for r in parse_eu_fsf(_read("eu_sample.xml")) if r.source_id == "EU.9001")
    assert aliyev.name == "Rustam Aliyev"  # strong="true"
    assert aliyev.aliases == ("Rustam Aliev",)  # strong="false"
    assert aliyev.dates_of_birth == ("1958-09-12",)
    assert aliyev.countries == ("TR",)
    assert aliyev.program == "EU-ARMS-EMBARGO"


def test_eu_entity_with_a_single_strong_alias_has_no_aliases():
    entity = next(r for r in parse_eu_fsf(_read("eu_sample.xml")) if r.source_id == "EU.9002")
    assert entity.name == "Silverline Holdings Ltd"
    assert entity.aliases == ()
    assert entity.countries == ("CY",)


# --------------------------------------------------------------------------
# Dispatch + shared record contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize("source,fixture", [("ofac", "ofac_sample.xml"), ("un", "un_sample.xml"),
                                            ("eu", "eu_sample.xml")])
def test_parse_source_dispatches_to_the_right_parser(source, fixture):
    records = parse_source(source, _read(fixture))
    assert records
    assert all(isinstance(r, WatchlistRecord) for r in records)
    assert all(r.source == source for r in records)


def test_parse_source_rejects_an_unknown_source():
    with pytest.raises(ValueError, match="unknown watchlist source"):
        parse_source("interpol", b"<x/>")


def test_sources_tuple_matches_every_registered_parser():
    assert set(SOURCES) == {"ofac", "un", "eu"}


def test_every_record_has_a_usable_reason_line():
    for source, fixture in (("ofac", "ofac_sample.xml"), ("un", "un_sample.xml"), ("eu", "eu_sample.xml")):
        for record in parse_source(source, _read(fixture)):
            assert record.reason  # program, remarks, or the generic fallback — never blank
