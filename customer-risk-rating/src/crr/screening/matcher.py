"""Fuzzy identity matching against watchlist entries.

Pure functions, no Streamlit or persistence dependency — ``app.py``'s
``screen_customer`` is a thin wrapper over :func:`screen` that supplies the
entries already hydrated into session state for this run (see
``WorkflowStore.list_watchlist_entries``).

Scored with `rapidfuzz <https://github.com/rapidfuzz/RapidFuzz>`_'s
``WRatio`` rather than the standard library's ``difflib.SequenceMatcher``:
``WRatio`` is specifically tuned for exactly this problem (name variants —
reordered tokens, a dropped middle name, transliteration spelling
differences) by taking the best of several comparison strategies per pair,
which is what every production screening vendor's matcher does under the
hood. A same-value date of birth or country then nudges the score up rather
than gating on it: a real screening hit is corroborating evidence, not a
second independent filter — two different people can share a country, and a
transliterated name alone should already be enough to surface for human
review.
"""

from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz

#: Below this fuzzy-match score, an entry does not surface as a hit at all.
DEFAULT_THRESHOLD = 60.0

#: Corroborating evidence, not independent filters — see the module docstring.
_DOB_MATCH_BONUS = 15.0
_COUNTRY_MATCH_BONUS = 5.0


def _normalize_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _normalize_country(country: str) -> str:
    return country.strip().lower()


def name_similarity(a: str, b: str) -> float:
    """0-100 similarity between two names, robust to reordered tokens and
    small spelling differences (see the module docstring for why WRatio)."""
    return fuzz.WRatio(_normalize_name(a), _normalize_name(b))


def score_entry(full_name: str, date_of_birth: str | None, country: str | None, entry: dict[str, Any]) -> float:
    """The match score for one entry: best fuzzy match across its primary
    name and every alias, plus DOB/country corroboration bonuses, capped at
    100."""
    candidates = [entry["name"], *entry.get("aliases", [])]
    score = max(name_similarity(full_name, candidate) for candidate in candidates)

    entry_dobs = entry.get("dates_of_birth") or ([entry["dob"]] if entry.get("dob") else [])
    if date_of_birth and date_of_birth in entry_dobs:
        score = min(100.0, score + _DOB_MATCH_BONUS)

    entry_countries = entry.get("countries") or ([entry["country"]] if entry.get("country") else [])
    if country and entry_countries:
        norm_country = _normalize_country(country)
        if norm_country in {_normalize_country(c) for c in entry_countries}:
            score = min(100.0, score + _COUNTRY_MATCH_BONUS)

    return round(score, 1)


def screen(
    full_name: str | None, date_of_birth: str | None, country: str | None,
    entries: list[dict[str, Any]], *, threshold: float = DEFAULT_THRESHOLD,
) -> list[dict[str, Any]]:
    """Every entry whose match score clears ``threshold``, most severe first.

    Returns each matched entry's dict with ``match_score`` added — nothing
    else about the entry is copied or mutated. Recomputed fresh on every
    call; nothing here is cached."""
    if not full_name or not full_name.strip():
        return []
    hits = []
    for entry in entries:
        score = score_entry(full_name, date_of_birth, country, entry)
        if score >= threshold:
            hits.append({**entry, "match_score": score})
    return sorted(hits, key=lambda h: h["match_score"], reverse=True)
