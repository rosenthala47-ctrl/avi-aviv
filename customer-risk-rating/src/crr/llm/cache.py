"""Cache extraction results aggressively (roadmap phase 7): notes change
rarely and LLM calls dominate cost, so the same narrative text should never
be sent to the model twice.

``CachingExtractor`` wraps any ``Extractor`` behind the same interface, so
caching is one cross-cutting concern implemented once rather than duplicated
inside every extractor implementation — the same reason idempotency lives at
the API layer (``crr.api.cache``) rather than inside ``ScoringService``.

The cache key is a content hash of the narrative text **and** the extractor's
own version string. Keying on text alone would mean a prompt rewrite or a
model upgrade kept serving pre-upgrade results forever, silently — the whole
point of versioning the extractor is to be able to invalidate its output.

Only successful extractions are cached. Caching a degraded result would turn
one transient API failure into a permanent one for that customer: the next
real request would keep finding the cached "unavailable" and never retry.
"""

from __future__ import annotations

import datetime as dt
import hashlib

from crr.api.repository import ExtractionRepository, StoredExtraction
from crr.llm.extraction import Extractor, NarrativeBundle, TextExtraction


def content_hash(bundle: NarrativeBundle, extractor_version: str) -> str:
    payload = "\x1f".join(
        (extractor_version, bundle.support_call_summary, bundle.underwriter_note, bundle.kyc_document_extract)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _to_stored(extraction: TextExtraction, key: str, now: dt.datetime) -> StoredExtraction:
    # Precondition enforced by the only caller (extract(), guarded by `if not
    # extraction.degraded`) -- narrows the Optional fields for the type checker.
    assert not extraction.degraded
    assert extraction.distress_level is not None
    assert extraction.concealment_level is not None
    return StoredExtraction(
        content_hash=key, customer_id=extraction.customer_id, source=extraction.source,
        extractor_version=extraction.extractor_version, distress_level=extraction.distress_level,
        distress_confidence=extraction.distress_confidence or 0.0, concealment_level=extraction.concealment_level,
        concealment_confidence=extraction.concealment_confidence or 0.0, created_at=now,
        stated_life_events=extraction.stated_life_events, evasiveness_detected=bool(extraction.evasiveness_detected),
        evasiveness_confidence=extraction.evasiveness_confidence or 0.0,
    )


def _from_stored(stored: StoredExtraction) -> TextExtraction:
    return TextExtraction(
        customer_id=stored.customer_id, source=stored.source, extractor_version=stored.extractor_version,
        degraded=False, distress_level=stored.distress_level, distress_confidence=stored.distress_confidence,
        concealment_level=stored.concealment_level, concealment_confidence=stored.concealment_confidence,
        stated_life_events=stored.stated_life_events, evasiveness_detected=stored.evasiveness_detected,
        evasiveness_confidence=stored.evasiveness_confidence,
    )


class CachingExtractor:
    """Wraps ``inner`` with a content-hash cache. Implements ``Extractor``."""

    def __init__(self, inner: Extractor, cache: ExtractionRepository) -> None:
        self._inner = inner
        self._cache = cache
        self.version = inner.version

    def extract(self, bundle: NarrativeBundle) -> TextExtraction:
        key = content_hash(bundle, self._inner.version)
        cached = self._cache.get(key)
        if cached is not None:
            return _from_stored(cached)

        extraction = self._inner.extract(bundle)
        if not extraction.degraded:
            self._cache.put(_to_stored(extraction, key, dt.datetime.now(dt.UTC)))
        return extraction
