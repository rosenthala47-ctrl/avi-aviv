"""Tests for crr.screening.matcher — pure, no Streamlit/DB dependency."""

from __future__ import annotations

from crr.screening.matcher import DEFAULT_THRESHOLD, name_similarity, score_entry, screen

ENTRY = {
    "id": "ofac:OFAC-2201", "name": "Mikhail Aslanov", "aliases": ["Michael Aslanov"],
    "dates_of_birth": ["1975-11-02"], "countries": ["CY"], "list_source": "ofac",
    "category": "sanctions", "reason": "Specially Designated National.",
}


def test_exact_name_scores_100():
    assert name_similarity("Mikhail Aslanov", "Mikhail Aslanov") == 100.0


def test_close_transliteration_scores_high_but_not_100():
    score = name_similarity("Mikael Aslanov", "Mikhail Aslanov")
    assert 70 <= score < 100


def test_unrelated_names_score_low():
    assert name_similarity("Noa Peretz", "Mikhail Aslanov") < 40


def test_score_entry_matches_best_of_primary_name_and_aliases():
    # "Michael Aslanov" is an exact match on the ALIAS, not the primary name.
    assert score_entry("Michael Aslanov", None, None, ENTRY) == 100.0


def test_score_entry_dob_match_adds_a_bonus_capped_at_100():
    without_dob = score_entry("Mikael Aslanov", None, None, ENTRY)
    with_dob = score_entry("Mikael Aslanov", "1975-11-02", None, ENTRY)
    assert with_dob > without_dob
    assert with_dob <= 100.0


def test_score_entry_wrong_dob_gives_no_bonus():
    no_bonus = score_entry("Mikael Aslanov", "1990-01-01", None, ENTRY)
    without_dob = score_entry("Mikael Aslanov", None, None, ENTRY)
    assert no_bonus == without_dob


def test_score_entry_country_match_adds_a_smaller_bonus():
    without = score_entry("Mikael Aslanov", None, None, ENTRY)
    with_country = score_entry("Mikael Aslanov", None, "CY", ENTRY)
    assert with_country > without
    assert with_country - without <= 5.0 + 1e-9


def test_score_entry_country_match_is_case_insensitive():
    a = score_entry("Mikael Aslanov", None, "cy", ENTRY)
    b = score_entry("Mikael Aslanov", None, "CY", ENTRY)
    assert a == b


def test_score_entry_falls_back_to_singular_dob_country_keys():
    """Entries built before dates_of_birth/countries lists existed (or any
    caller that only sets the singular dob/country display fields) still
    match correctly. Uses a near-, not exact-, name match so the DOB bonus
    is visible rather than swallowed by the 100-point cap."""
    singular_entry = {"name": "Jon Doe", "aliases": [], "dob": "1980-01-01", "country": "IL"}
    with_bonus = score_entry("John Doe", "1980-01-01", "IL", singular_entry)
    without_bonus = score_entry("John Doe", None, None, singular_entry)
    assert with_bonus > without_bonus


def test_screen_returns_empty_for_blank_or_missing_name():
    assert screen(None, None, None, [ENTRY]) == []
    assert screen("", None, None, [ENTRY]) == []
    assert screen("   ", None, None, [ENTRY]) == []


def test_screen_filters_by_threshold():
    hits = screen("Someone Completely Different", None, None, [ENTRY])
    assert hits == []


def test_screen_returns_hits_sorted_most_severe_first():
    other = {**ENTRY, "id": "ofac:OTHER", "name": "Mikael Aslanoff"}  # weaker match
    hits = screen("Mikhail Aslanov", None, None, [ENTRY, other])
    assert len(hits) == 2
    assert hits[0]["match_score"] >= hits[1]["match_score"]
    assert hits[0]["id"] == "ofac:OFAC-2201"


def test_screen_adds_match_score_without_mutating_the_input_entry():
    original = dict(ENTRY)
    screen("Mikhail Aslanov", None, None, [ENTRY])
    assert original == ENTRY  # the entry dict passed in is untouched
    hits = screen("Mikhail Aslanov", None, None, [ENTRY])
    assert "match_score" in hits[0] and "match_score" not in ENTRY


def test_default_threshold_is_reasonable():
    assert 0 < DEFAULT_THRESHOLD < 100
