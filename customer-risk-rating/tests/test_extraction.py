"""Tests for the extraction pipeline: the reference extractor's actual
judgement on realistic (non-adversarial) text, the content-hash cache, batch
extraction, and the feature-pipeline merge. Prompt-injection and schema
safety live in ``tests/test_extraction_security.py``.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from crr.api.repository import InMemoryExtractionRepository, SqlAlchemyExtractionRepository, create_session_factory
from crr.features.text import TEXT_FEATURE_COLUMNS, build_text_features
from crr.llm.batch import extract_all
from crr.llm.cache import CachingExtractor, content_hash
from crr.llm.extraction import NarrativeBundle, TextExtraction
from crr.llm.reference_extractor import ReferenceExtractor

# --------------------------------------------------------------------------
# ReferenceExtractor: does it read as a reasonable (if cheap) judge?
# --------------------------------------------------------------------------


def test_routine_note_reads_as_clean():
    bundle = NarrativeBundle(
        customer_id="C1",
        support_call_summary="Customer asked about the interest rate on the savings product and closed the call satisfied.",
    )
    result = ReferenceExtractor().extract(bundle)
    assert result.distress_level == 0
    assert result.concealment_level == 0
    assert result.evasiveness_detected is False


def test_severe_distress_language_reads_as_high_distress():
    bundle = NarrativeBundle(
        customer_id="C1",
        support_call_summary="Customer's employment ended last week; severance covers roughly one month of obligations.",
        underwriter_note="Cash flow is negative in five of the last six months and the shortfall is widening. Recommend decline.",
    )
    result = ReferenceExtractor().extract(bundle)
    assert result.distress_level == 3
    assert "job_loss" in result.stated_life_events


def test_refusal_language_reads_as_high_concealment_and_evasive():
    bundle = NarrativeBundle(
        customer_id="C1",
        support_call_summary="Customer refused to identify the counterparty of a large incoming wire and ended the call abruptly.",
    )
    result = ReferenceExtractor().extract(bundle)
    assert result.concealment_level == 3
    assert result.evasiveness_detected is True


def test_structural_opacity_without_a_dodge_is_concealment_not_evasiveness():
    """A disclosed but complex structure is opaque, not evasive — the note
    describes no one avoiding a question about it."""
    bundle = NarrativeBundle(
        customer_id="C1",
        underwriter_note="Ownership chain runs through two intermediate holding entities; "
        "the ultimate beneficial owner is asserted but not evidenced.",
    )
    result = ReferenceExtractor().extract(bundle)
    assert result.concealment_level >= 2
    assert result.evasiveness_detected is False


def test_no_narratives_at_all_is_unavailable_not_zero():
    result = ReferenceExtractor().extract(NarrativeBundle(customer_id="C1"))
    assert result.degraded is True
    assert result.distress_level is None


def test_hebrew_severe_distress_is_detected():
    bundle = NarrativeBundle(
        customer_id="C1",
        support_call_summary="הלקוח מסר שלא יוכל לעמוד בהחזר של החודש הבא ושאל מהן ההשלכות.",
    )
    result = ReferenceExtractor().extract(bundle)
    assert result.distress_level >= 2


# --------------------------------------------------------------------------
# crr.llm.batch.extract_all
# --------------------------------------------------------------------------


def test_extract_all_round_trips_every_row():
    narratives = pd.DataFrame(
        [
            {"customer_id": "A", "support_call_summary": "Customer employment ended last week."},
            {"customer_id": "B", "support_call_summary": "Routine question, resolved."},
            {"customer_id": "C"},  # no text columns at all -> treated as empty
        ]
    )
    out = extract_all(narratives, ReferenceExtractor())
    assert list(out["customer_id"]) == ["A", "B", "C"]
    assert out.set_index("customer_id").loc["C", "degraded"]


def test_extract_all_requires_customer_id():
    with pytest.raises(ValueError, match="customer_id"):
        extract_all(pd.DataFrame({"support_call_summary": ["x"]}), ReferenceExtractor())


# --------------------------------------------------------------------------
# crr.llm.cache.CachingExtractor
# --------------------------------------------------------------------------


class _CountingExtractor:
    """Wraps another Extractor and counts calls. Forwards ``version`` from
    the wrapped extractor rather than declaring its own — CachingExtractor
    keys its cache on the version of whatever it directly wraps, so a
    counting wrapper with a version of its own would (correctly) look like a
    different extractor and never hit the cache the inner one populated."""

    def __init__(self, inner):
        self._inner = inner
        self.version = inner.version
        self.calls = 0

    def extract(self, bundle):
        self.calls += 1
        return self._inner.extract(bundle)


def test_cache_hit_avoids_a_second_call():
    counting = _CountingExtractor(ReferenceExtractor())
    cached = CachingExtractor(counting, InMemoryExtractionRepository())
    bundle = NarrativeBundle(customer_id="C1", support_call_summary="Customer employment ended last week.")
    first = cached.extract(bundle)
    second = cached.extract(bundle)
    assert counting.calls == 1
    assert first.distress_level == second.distress_level


def test_different_narrative_text_is_a_cache_miss():
    counting = _CountingExtractor(ReferenceExtractor())
    cached = CachingExtractor(counting, InMemoryExtractionRepository())
    cached.extract(NarrativeBundle(customer_id="C1", support_call_summary="a"))
    cached.extract(NarrativeBundle(customer_id="C2", support_call_summary="b"))
    assert counting.calls == 2


def test_degraded_extraction_is_never_cached():
    class AlwaysDegraded:
        version = "degraded-v1"

        def extract(self, bundle):
            return TextExtraction.unavailable(bundle.customer_id, extractor_version=self.version)

    counting = _CountingExtractor(AlwaysDegraded())
    cached = CachingExtractor(counting, InMemoryExtractionRepository())
    bundle = NarrativeBundle(customer_id="C1", support_call_summary="text")
    cached.extract(bundle)
    cached.extract(bundle)
    assert counting.calls == 2  # a real retry each time, not a remembered failure


def test_extractor_version_change_invalidates_the_cache_key():
    """A prompt rewrite or model upgrade must not keep serving pre-upgrade
    results — the whole point of versioning the extractor."""
    bundle = NarrativeBundle(customer_id="C1", support_call_summary="text")
    key_v1 = content_hash(bundle, "v1")
    key_v2 = content_hash(bundle, "v2")
    assert key_v1 != key_v2


def test_sqlalchemy_extraction_cache_round_trips():
    session_factory = create_session_factory("sqlite:///:memory:")
    repo = SqlAlchemyExtractionRepository(session_factory)
    cached = CachingExtractor(ReferenceExtractor(), repo)
    bundle = NarrativeBundle(customer_id="C1", support_call_summary="Customer employment ended last week.")
    first = cached.extract(bundle)

    # A second CachingExtractor instance against the same DB must see the
    # cached row -- the cache is durable, not process-local.
    second_wrapper = CachingExtractor(_CountingExtractor(ReferenceExtractor()), repo)
    second = second_wrapper.extract(bundle)
    assert second.distress_level == first.distress_level
    assert second_wrapper._inner.calls == 0


def test_concurrent_put_of_identical_content_does_not_raise():
    """Same content_hash, saved twice -- a benign race, not a conflict."""
    session_factory = create_session_factory("sqlite:///:memory:")
    repo = SqlAlchemyExtractionRepository(session_factory)
    from crr.api.repository import StoredExtraction

    stored = StoredExtraction(
        content_hash="dup", customer_id="C1", source="reference", extractor_version="v1",
        distress_level=1, distress_confidence=0.5, concealment_level=0, concealment_confidence=0.5,
        created_at=dt.datetime.now(dt.UTC),
    )
    repo.put(stored)
    repo.put(stored)  # must not raise
    assert repo.get("dup") is not None


# --------------------------------------------------------------------------
# crr.features.text.build_text_features
# --------------------------------------------------------------------------


def test_build_text_features_none_input_is_all_nan():
    customers = pd.DataFrame({"customer_id": ["A", "B"]})
    block = build_text_features(customers, None)
    assert list(block.columns) == list(TEXT_FEATURE_COLUMNS)
    assert block.isna().all().all()


def test_build_text_features_excludes_degraded_rows():
    customers = pd.DataFrame({"customer_id": ["A"]})
    extractions = pd.DataFrame(
        [{"customer_id": "A", "degraded": True, "distress_level": 3, "distress_confidence": 0.9,
          "concealment_level": 2, "concealment_confidence": 0.8}]
    )
    block = build_text_features(customers, extractions)
    assert block["text_distress_level"].isna().all()


def test_build_text_features_missing_customer_gets_nan_not_an_error():
    customers = pd.DataFrame({"customer_id": ["A", "B"]})
    extractions = pd.DataFrame(
        [{"customer_id": "A", "degraded": False, "distress_level": 2, "distress_confidence": 0.9,
          "concealment_level": 1, "concealment_confidence": 0.7}]
    )
    block = build_text_features(customers, extractions)
    assert block.loc[0, "text_distress_level"] == 2.0
    assert pd.isna(block.loc[1, "text_distress_level"])


def test_build_text_features_requires_customer_id():
    with pytest.raises(ValueError, match="customer_id"):
        build_text_features(pd.DataFrame({"x": [1]}), None)
