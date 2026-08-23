"""Categorical normalisation, vocabularies and missingness indicators.

The generated data deliberately contains the mess a real CRM extract contains:
``Self-Employed``, ``self employed`` and ``SELF_EMPLOYED`` are the same thing, and
a model that treats them as three categories wastes a third of the evidence for
each. Normalisation happens here, in one place, so training and serving cannot
disagree about it.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

#: Value used for a category not seen during fitting. Serving WILL encounter
#: these — a new occupation code, a country that was not in the training book —
#: and the pipeline must map them to something rather than emit a null the model
#: has never seen in that column.
UNKNOWN_CATEGORY = "__unknown__"

#: Value used where the source itself was null.
MISSING_CATEGORY = "__missing__"

_WHITESPACE = re.compile(r"[\s\-/]+")
_NON_WORD = re.compile(r"[^\w_]+")

#: Genuine synonyms — different words for the same thing, which case-folding
#: alone cannot merge. Written after inspecting the actual value distribution,
#: which is how this list should be maintained.
SYNONYMS: dict[str, str] = {
    "pensioner": "retired",
    "ba": "bachelor",
    "bachelors": "bachelor",
    "ma": "master",
    "masters": "master",
    "phd": "doctorate",
    "highschool": "high_school",
}


_MULTI_UNDERSCORE = re.compile(r"_+")


def _normalise_value(value: object) -> object:
    """Normalise one category value. Null in, null out.

    A single pass in Python rather than five chained pandas ``.str`` calls: the
    output is identical, but on the one-row frames the serving path builds, five
    pandas passes per column dominated the request. Batch callers see the same
    result at no extra cost.
    """
    if pd.isna(value):
        return value
    text = str(value).strip().lower()
    text = _WHITESPACE.sub("_", text)
    text = _NON_WORD.sub("", text)
    text = _MULTI_UNDERSCORE.sub("_", text).strip("_")
    return SYNONYMS.get(text, text)


def normalise_category(series: pd.Series) -> pd.Series:
    """Case-fold, collapse separators and apply the synonym map.

    ``"  Self-Employed "`` and ``"SELF_EMPLOYED"`` both become ``"self_employed"``.
    Nulls are preserved as nulls; deciding what to do with them is the caller's job.
    """
    return series.map(_normalise_value)


class CategoricalEncoder:
    """Learns a vocabulary per column at fit time and applies it consistently.

    Emits pandas ``category`` dtype, which LightGBM consumes natively — better
    than one-hot for high-cardinality columns like country, and it keeps the
    feature count (and therefore the SHAP output in phase 3) interpretable.
    """

    def __init__(self, min_frequency: int = 1) -> None:
        self.min_frequency = min_frequency
        self.vocabularies: dict[str, list[str]] = {}

    def fit(self, frame: pd.DataFrame, columns: list[str]) -> CategoricalEncoder:
        self.vocabularies = {}
        for column in columns:
            values = normalise_category(frame[column])
            counts = values.value_counts()
            kept = sorted(str(v) for v, n in counts.items() if n >= self.min_frequency)
            # Both sentinels are always in the vocabulary, even if unused at fit
            # time, so the encoded column has a stable category set for the life
            # of the contract.
            self.vocabularies[column] = kept + [MISSING_CATEGORY, UNKNOWN_CATEGORY]
        return self

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        if not self.vocabularies:
            raise RuntimeError("CategoricalEncoder.transform called before fit")
        out = {}
        for column, vocabulary in self.vocabularies.items():
            if column not in frame.columns:
                raise KeyError(f"categorical column {column!r} is missing from the frame")
            values = normalise_category(frame[column])
            known = set(vocabulary)
            mapped = values.map(
                lambda v, known=known: MISSING_CATEGORY
                if pd.isna(v)
                else (str(v) if str(v) in known else UNKNOWN_CATEGORY)
            )
            out[column] = pd.Categorical(mapped, categories=vocabulary)
        return pd.DataFrame(out, index=frame.index)


def missing_indicators(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """One 0/1 column per input, marking where the source value was absent.

    Absence is information, not an inconvenience. In this data
    ``source_of_funds_verified`` goes missing mostly when the declared source is
    'undeclared' — the missingness is MNAR and carries more signal than the value
    would. Imputing it away, which is the reflex, destroys that.
    """
    data = {f"{column}_is_missing": frame[column].isna().astype(np.float32) for column in columns}
    return pd.DataFrame(data, index=frame.index)


def safe_ratio(numerator: pd.Series | np.ndarray, denominator: pd.Series | np.ndarray, *, cap: float | None = None) -> np.ndarray:
    """Elementwise ratio that yields NaN rather than inf where the denominator is zero."""
    num = np.asarray(numerator, dtype=float)
    den = np.asarray(denominator, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        result = np.where(np.abs(den) > 1e-9, num / den, np.nan)
    if cap is not None:
        result = np.clip(result, -cap, cap)
    return result
