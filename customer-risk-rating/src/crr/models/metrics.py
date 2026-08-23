"""Evaluation metrics for a binary risk model.

Discrimination (AUC, Gini, KS) answers "does the model rank customers correctly?"
Calibration answers "when it says 7%, do 7% of them default?" Both matter, and
they are independent: a model can rank perfectly and still be systematically
wrong about the level, which is fatal here because the score feeds pricing,
provisioning and a policy band cut-off.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score


def gini(y_true: np.ndarray, scores: np.ndarray) -> float:
    """Gini coefficient — the form the credit-risk world actually quotes."""
    return 2.0 * roc_auc_score(y_true, scores) - 1.0


def ks_statistic(y_true: np.ndarray, scores: np.ndarray) -> float:
    """Kolmogorov-Smirnov: the widest gap between the good and bad score CDFs."""
    y_true = np.asarray(y_true)
    order = np.argsort(scores)
    positives = np.cumsum(y_true[order]) / max(y_true.sum(), 1)
    negatives = np.cumsum(1 - y_true[order]) / max((1 - y_true).sum(), 1)
    return float(np.max(np.abs(positives - negatives)))


def auc_standard_error(y_true: np.ndarray, scores: np.ndarray) -> float:
    """Hanley-McNeil standard error of an AUC estimate.

    Needed because "test AUC within 0.01 of validation AUC" is not a usable
    overfit test on its own: with ~500 positives in a split, the sampling
    standard error of a single AUC is already around 0.012. A gap smaller than
    the noise proves nothing, and a gap slightly larger than it is not evidence
    of overfitting either. Comparing the gap against its own standard error is
    the honest version of that check.
    """
    y_true = np.asarray(y_true)
    n_pos, n_neg = int(y_true.sum()), int((1 - y_true).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    auc = roc_auc_score(y_true, scores)
    q1 = auc / (2.0 - auc)
    q2 = 2.0 * auc**2 / (1.0 + auc)
    variance = (
        auc * (1 - auc) + (n_pos - 1) * (q1 - auc**2) + (n_neg - 1) * (q2 - auc**2)
    ) / (n_pos * n_neg)
    return float(np.sqrt(max(variance, 0.0)))


def calibration_table(y_true: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> pd.DataFrame:
    """Predicted vs observed rate per equal-count bin, with a binomial standard error.

    The standard error column is not decoration. In the top bin of a 5%-prevalence
    test split there may be only a few hundred customers, where a 2-point gap
    between predicted and observed is entirely consistent with perfect
    calibration. Reporting the deviation without its uncertainty invites chasing
    noise.
    """
    frame = pd.DataFrame({"y": np.asarray(y_true, dtype=float), "p": np.asarray(probabilities, dtype=float)})
    frame = frame.sort_values("p", kind="stable").reset_index(drop=True)
    frame["bin"] = np.minimum((np.arange(len(frame)) * bins) // max(len(frame), 1) + 1, bins)

    grouped = frame.groupby("bin", observed=True).agg(n=("y", "size"), predicted=("p", "mean"), observed=("y", "mean"))
    grouped["difference"] = grouped["observed"] - grouped["predicted"]
    grouped["std_error"] = np.sqrt(grouped["observed"].clip(1e-9, 1 - 1e-9) * (1 - grouped["observed"]) / grouped["n"])
    grouped["within_2se"] = grouped["difference"].abs() <= 2 * grouped["std_error"]
    return grouped.reset_index()


def expected_calibration_error(y_true: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> float:
    """Count-weighted mean absolute gap between predicted and observed rates.

    Preferred over the worst-bin gap as a headline number: the worst bin of ten is
    an extreme order statistic and is dominated by sampling noise at these
    prevalences. The worst bin is still reported alongside, for transparency.
    """
    table = calibration_table(y_true, probabilities, bins)
    weights = table["n"].to_numpy(dtype=float)
    return float(np.average(table["difference"].abs().to_numpy(), weights=weights))


def decile_table(y_true: np.ndarray, scores: np.ndarray) -> pd.DataFrame:
    """Observed outcome rate by score decile, with lift over the base rate."""
    frame = pd.DataFrame({"y": np.asarray(y_true, dtype=float), "s": np.asarray(scores, dtype=float)})
    frame = frame.sort_values("s", kind="stable").reset_index(drop=True)
    frame["decile"] = np.minimum((np.arange(len(frame)) * 10) // max(len(frame), 1) + 1, 10)
    base = frame["y"].mean()
    grouped = frame.groupby("decile", observed=True).agg(n=("y", "size"), mean_score=("s", "mean"), rate=("y", "mean"))
    grouped["lift"] = grouped["rate"] / base if base > 0 else np.nan
    return grouped.reset_index()


def summarise(y_true: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> dict[str, Any]:
    """Every headline number for one split.

    Accepts either a calibrated probability or a raw score. Discrimination
    (AUC, Gini, KS, PR-AUC) is rank-based and valid for both. Brier and the
    calibration metrics only mean anything on a [0, 1] probability, so they are
    reported as NaN for a raw score rather than computed on a scale where they
    would be arithmetically valid and semantically meaningless.
    """
    y_true = np.asarray(y_true)
    probabilities = np.asarray(probabilities, dtype=float)
    is_probability = bool(np.all((probabilities >= 0.0) & (probabilities <= 1.0)))
    table = calibration_table(y_true, probabilities, bins)
    calibration: dict[str, Any] = {
        "brier": float("nan"),
        "expected_calibration_error": float("nan"),
        "max_calibration_error": float("nan"),
        "calibration_bins_within_2se": 0,
    }
    if is_probability:
        calibration = {
            "brier": float(brier_score_loss(y_true, probabilities)),
            "expected_calibration_error": float(expected_calibration_error(y_true, probabilities, bins)),
            "max_calibration_error": float(table["difference"].abs().max()),
            "calibration_bins_within_2se": int(table["within_2se"].sum()),
        }
    return {
        "is_probability": is_probability,
        **calibration,
        "n": int(len(y_true)),
        "positives": int(y_true.sum()),
        "prevalence": float(y_true.mean()),
        "auc": float(roc_auc_score(y_true, probabilities)),
        "auc_std_error": float(auc_standard_error(y_true, probabilities)),
        "gini": float(gini(y_true, probabilities)),
        "pr_auc": float(average_precision_score(y_true, probabilities)),
        "ks": float(ks_statistic(y_true, probabilities)),
        "calibration_bins": int(len(table)),
        "mean_predicted": float(probabilities.mean()),
    }


def format_metrics(name: str, metrics: dict[str, Any]) -> str:
    """One-line summary for the training report."""
    return (
        f"  {name:<12} n={metrics['n']:>7,}  prev={metrics['prevalence']:>6.2%}  "
        f"AUC={metrics['auc']:.4f}  Gini={metrics['gini']:.4f}  KS={metrics['ks']:.4f}  "
        f"PR-AUC={metrics['pr_auc']:.4f}  ECE={metrics['expected_calibration_error']:.4f}"
    )
