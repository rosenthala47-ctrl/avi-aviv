"""The text-extraction feature block: turns an already-extracted frame
(``crr.llm.batch.extract_all``) into the numeric columns the model trains on.

Only ``distress_level``/``concealment_level`` and their confidences become
model features — see ``crr.llm.extraction``'s module docstring for why
``stated_life_events``/``evasiveness_detected`` stay explainability-only. A
customer with no extraction on file (never had notes, or extraction was
degraded) gets NaN here, never a zero: "no signal" and "confirmed clean" are
different facts, the same "missing is modelled, never zero-filled" rule the
rest of this pipeline follows throughout.
"""

from __future__ import annotations

import pandas as pd

#: Declared statically, like DERIVED_COLUMNS/INDICATOR_COLUMNS in
#: crr.features.pipeline, so the ablation study can isolate this block by name.
TEXT_FEATURE_COLUMNS: tuple[str, ...] = (
    "text_distress_level", "text_distress_confidence",
    "text_concealment_level", "text_concealment_confidence",
)

_SOURCE_COLUMNS: dict[str, str] = {
    "distress_level": "text_distress_level",
    "distress_confidence": "text_distress_confidence",
    "concealment_level": "text_concealment_level",
    "concealment_confidence": "text_concealment_confidence",
}


def build_text_features(customers: pd.DataFrame, extractions: pd.DataFrame | None) -> pd.DataFrame:
    """Left-join extractions onto ``customers`` by ``customer_id``, aligned to
    ``customers.index``. ``extractions`` is the frame ``crr.llm.batch.extract_all``
    returns; ``None`` (or a customer with no matching row, or a degraded
    extraction) all mean the same thing here — no feature, not a zero."""
    if "customer_id" not in customers.columns:
        raise ValueError("customers frame must carry customer_id")

    if extractions is None or extractions.empty:
        return pd.DataFrame(
            {name: pd.Series(dtype="float64") for name in TEXT_FEATURE_COLUMNS}, index=customers.index
        ).reindex(customers.index)

    usable = extractions[~extractions["degraded"]] if "degraded" in extractions.columns else extractions
    merged = customers[["customer_id"]].merge(
        usable[["customer_id", *_SOURCE_COLUMNS]], on="customer_id", how="left"
    )
    block = merged.rename(columns=_SOURCE_COLUMNS)[list(TEXT_FEATURE_COLUMNS)]
    block.index = customers.index
    return block.astype("float64")
