"""Population and calibration drift — phase 8's "is the deployed model still
describing the population in front of it" check.

Two independent questions, both required because neither implies the other:
a population can drift on features while calibration holds (the new customers
are different but the model still reads their risk correctly), and calibration
can drift with no measurable feature shift (the *relationship* between features
and outcome changed, not the inputs — a regime shift). PSI answers the first,
calibration drift answers the second.

No live production stream exists here, so both are computed the same honest
way validate_dataset.py and train_baseline.py already do for every other
out-of-time question in this project: the model's ``train``/``validation``
split stands in for "the population the model was built on", and the
out-of-time ``test`` split stands in for "the population showing up today".
That is a real comparison, not a placeholder — it is the same forward-looking
holdout the phase 2 exit criteria are measured on.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

#: Standard credit-risk-industry PSI bands (also used by SAS/most vendor risk
#: platforms): below this, the shift is noise. Cited rather than invented so a
#: reviewer can check it against their own institution's threshold instead of
#: taking this project's word for it.
PSI_MODERATE_THRESHOLD = 0.10
#: Above this, the population has shifted enough that model performance should
#: not be trusted without investigation.
PSI_MAJOR_THRESHOLD = 0.25

_EPS = 1e-6


def _severity(psi: float) -> str:
    if not np.isfinite(psi):
        return "unknown"
    if psi >= PSI_MAJOR_THRESHOLD:
        return "major"
    if psi >= PSI_MODERATE_THRESHOLD:
        return "moderate"
    return "stable"


def _bucket_edges(reference: pd.Series, buckets: int) -> np.ndarray:
    """Quantile edges from the reference distribution only — PSI compares the
    current population against the *reference's* bucketing, not its own, or
    every distribution would flatter itself into equal-frequency bins."""
    quantiles = np.linspace(0.0, 1.0, buckets + 1)
    edges = np.unique(np.quantile(reference.to_numpy(dtype=float), quantiles))
    if len(edges) < 2:
        # A constant (or near-constant) reference column carries no bucketing
        # information; fall back to a single bucket spanning all values.
        lo = float(reference.min()) if len(reference) else 0.0
        hi = float(reference.max()) if len(reference) else 0.0
        return np.array([lo - 1.0, hi + 1.0])
    edges[0], edges[-1] = -np.inf, np.inf
    return edges


def _psi_numeric(reference: pd.Series, current: pd.Series, buckets: int) -> float:
    edges = _bucket_edges(reference.dropna(), buckets)
    ref_counts = pd.cut(reference.dropna(), edges).value_counts(sort=False)
    cur_counts = pd.cut(current.dropna(), edges).value_counts(sort=False)
    return _psi_from_counts(ref_counts, cur_counts)


def _psi_categorical(reference: pd.Series, current: pd.Series) -> float:
    ref_counts = reference.astype("object").fillna("__missing__").value_counts()
    cur_counts = current.astype("object").fillna("__missing__").value_counts()
    categories = ref_counts.index.union(cur_counts.index)
    ref_counts = ref_counts.reindex(categories, fill_value=0)
    cur_counts = cur_counts.reindex(categories, fill_value=0)
    return _psi_from_counts(ref_counts, cur_counts)


def _psi_from_counts(ref_counts: pd.Series, cur_counts: pd.Series) -> float:
    ref_pct = np.clip(ref_counts.to_numpy(dtype=float) / max(ref_counts.sum(), 1), _EPS, None)
    cur_pct = np.clip(cur_counts.to_numpy(dtype=float) / max(cur_counts.sum(), 1), _EPS, None)
    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


def population_stability_index(reference: pd.Series, current: pd.Series, buckets: int = 10) -> float:
    """PSI of ``current`` against ``reference``, using ``reference``'s own
    quantile buckets for a numeric column or its category set for anything
    else. 0 is identical distributions; conventionally >=0.10 is "moderate"
    drift and >=0.25 is "major" (see the module-level thresholds)."""
    if pd.api.types.is_numeric_dtype(reference) and reference.nunique(dropna=True) > buckets:
        return _psi_numeric(reference, current, buckets)
    return _psi_categorical(reference, current)


def psi_report(reference: pd.DataFrame, current: pd.DataFrame, columns: list[str], buckets: int = 10) -> pd.DataFrame:
    """PSI for every column in ``columns`` present in both frames, sorted worst-first."""
    rows = []
    for column in columns:
        if column not in reference.columns or column not in current.columns:
            continue
        psi = population_stability_index(reference[column], current[column], buckets)
        rows.append({"feature": column, "psi": round(psi, 4), "severity": _severity(psi)})
    frame = pd.DataFrame(rows).sort_values("psi", ascending=False, ignore_index=True)
    return frame


def calibration_drift(current_ece: float, baseline_ece: float, *, tolerance: float = 0.02) -> dict[str, Any]:
    """Has the deployed calibrator's error grown since training time?

    ``baseline_ece`` is the ECE measured at training time (validation or test
    split — whatever the caller recorded as the model's calibration baseline).
    ``current_ece`` is the same metric recomputed on the population being
    monitored now. ``tolerance`` reuses phase 2's own MAX_ECE bar (0.02): the
    question here is not "is calibration good" (phase 2 already answered
    that) but "is it still as good as it was", so drifting past the same bar
    that gated deployment is the natural trigger for the drift alert.
    """
    delta = current_ece - baseline_ece
    return {
        "baseline_ece": round(baseline_ece, 4),
        "current_ece": round(current_ece, 4),
        "delta_ece": round(delta, 4),
        "drifted": bool(current_ece > tolerance and delta > 0),
    }
