"""Tests for crr.screening.ingest — the load_file/fetch -> parse plumbing.
No test here reaches the real OFAC/UN/EU hosts: fetch()'s network path is
exercised structurally (a missing URL raises before any request is made),
and load_file()/load_and_parse() are exercised for real against the same
fixtures the parser tests use."""

from __future__ import annotations

from pathlib import Path

import pytest

from crr.screening.ingest import DEFAULT_URLS, MAX_RESPONSE_BYTES, fetch, load_and_parse, load_file

FIXTURES = Path(__file__).parent / "fixtures" / "screening"


def test_load_file_reads_bytes():
    data = load_file(FIXTURES / "ofac_sample.xml")
    assert data.startswith(b"<?xml")


def test_load_file_rejects_oversized_files(tmp_path, monkeypatch):
    monkeypatch.setattr("crr.screening.ingest.MAX_RESPONSE_BYTES", 10)
    big = tmp_path / "big.xml"
    big.write_bytes(b"x" * 100)
    with pytest.raises(ValueError, match="exceeded"):
        load_file(big)


def test_load_and_parse_round_trips_through_the_real_parser():
    records = load_and_parse("ofac", FIXTURES / "ofac_sample.xml")
    assert len(records) == 2
    assert records[0].source == "ofac"


def test_fetch_without_a_url_and_no_default_raises_before_any_request():
    """The EU source has no stable default URL (see the module docstring) —
    calling fetch() for it without --url must fail fast with a clear error,
    not attempt a request to an empty string."""
    with pytest.raises(ValueError, match="no default URL"):
        fetch("eu", url=None)


def test_default_urls_cover_ofac_and_un_but_not_eu():
    assert DEFAULT_URLS["ofac"].startswith("https://")
    assert DEFAULT_URLS["un"].startswith("https://")
    assert DEFAULT_URLS["eu"] == ""


def test_max_response_bytes_is_a_sane_positive_bound():
    assert 0 < MAX_RESPONSE_BYTES < 10 * 1024 * 1024 * 1024
