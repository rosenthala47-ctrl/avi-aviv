"""Extract every customer's narratives into one frame — the offline/training
path. The live serving path (one customer per request) calls
``Extractor.extract`` directly; this is for ``scripts/train_baseline.py`` and
``scripts/verify_extraction.py``, where the whole narratives table is on disk
already.

Kept separate from ``crr.features`` on purpose: extraction is slow (network,
cost, caching) where the rest of the feature pipeline is fast, pure pandas —
this is the step that produces the small, already-computed frame
``crr.features.text.build_text_features`` merely joins in.
"""

from __future__ import annotations

import pandas as pd

from crr.llm.extraction import Extractor, NarrativeBundle, TextExtraction

#: Every column produced per customer, including provenance the model
#: features (crr.features.text) don't need but validation and audit do.
EXTRACTION_COLUMNS: tuple[str, ...] = (
    "customer_id", "source", "extractor_version", "degraded",
    "distress_level", "distress_confidence", "concealment_level", "concealment_confidence",
    "stated_life_events", "evasiveness_detected", "evasiveness_confidence",
)


def _row(extraction: TextExtraction) -> dict:
    return {
        "customer_id": extraction.customer_id, "source": extraction.source,
        "extractor_version": extraction.extractor_version, "degraded": extraction.degraded,
        "distress_level": extraction.distress_level, "distress_confidence": extraction.distress_confidence,
        "concealment_level": extraction.concealment_level, "concealment_confidence": extraction.concealment_confidence,
        "stated_life_events": extraction.stated_life_events, "evasiveness_detected": extraction.evasiveness_detected,
        "evasiveness_confidence": extraction.evasiveness_confidence,
    }


def _clean_text(value: object) -> str:
    """Empty string for a genuinely missing value (None, NaN — a customer with
    no note of that type on file yet), ``str(value)`` otherwise. Not
    ``value or ""``: ``float('nan')`` is truthy in Python, so that idiom would
    turn a missing column into the literal 3-character string "nan" instead
    of empty — found by a test exercising a customer with no support-call
    note at all."""
    return "" if pd.isna(value) else str(value)


def extract_all(narratives: pd.DataFrame, extractor: Extractor) -> pd.DataFrame:
    """One extraction per row of ``narratives`` (must carry ``customer_id``
    plus the three narrative text columns; missing columns are treated as
    empty). Never raises — a single bad row degrades to
    ``TextExtraction.unavailable`` and the loop continues, matching
    ``Extractor``'s own contract."""
    required = {"customer_id"}
    missing = required - set(narratives.columns)
    if missing:
        raise ValueError(f"narratives frame is missing {sorted(missing)}")

    rows = []
    for record in narratives.to_dict(orient="records"):
        bundle = NarrativeBundle(
            customer_id=str(record["customer_id"]),
            support_call_summary=_clean_text(record.get("support_call_summary")),
            underwriter_note=_clean_text(record.get("underwriter_note")),
            kyc_document_extract=_clean_text(record.get("kyc_document_extract")),
        )
        rows.append(_row(extractor.extract(bundle)))
    return pd.DataFrame(rows, columns=list(EXTRACTION_COLUMNS))
