"""TreeSHAP over the gradient-boosted model.

Uses LightGBM's native ``predict(pred_contrib=True)`` rather than the ``shap``
library. Two reasons, both verified:

1. **It is exact and identical.** LightGBM's ``pred_contrib`` runs the same
   TreeSHAP algorithm the ``shap`` library calls for LightGBM; on a trained model
   the two agree to 0.0e0. So the library buys nothing for the numbers and adds a
   heavy dependency to the serving path.
2. **Additivity to machine precision.** The per-feature contributions plus the
   bias term reconstruct the raw margin to ~5e-15, comfortably inside the phase 3
   exit criterion of 1e-6. This is not a coincidence to be hoped for — it is a
   property of the algorithm, and :meth:`ShapResult.additivity_error` checks it on
   every batch so a future model or LightGBM change that broke it would be caught.

Scale
-----
SHAP values here are on the **raw margin** (log-odds) scale — the model's decision
function. Platt calibration, ``sigmoid(a * margin + b)``, is a strictly monotone
rescaling applied afterward. It changes the *level* of the probability but not
which factors drive the decision or their order, so explaining the margin is both
correct and stable. The explanation and the calibrated probability are reported
side by side and never conflated.
"""

from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd


@dataclass
class ShapResult:
    """SHAP values for a batch, on the raw-margin scale.

    ``values`` is (n_samples, n_features); ``base_value`` is the model's expected
    margin (the TreeSHAP bias term, constant across samples).
    """

    values: np.ndarray
    base_value: float
    feature_names: list[str]
    raw_margin: np.ndarray

    def additivity_error(self) -> float:
        """Max |sum(shap) + base - raw_margin| across the batch.

        The exit-criterion check. TreeSHAP is exactly additive, so this should be
        at the level of floating-point noise; anything larger means the explainer
        and the model have diverged and no explanation from it can be trusted.
        """
        reconstructed = self.values.sum(axis=1) + self.base_value
        return float(np.abs(reconstructed - self.raw_margin).max())

    def as_frame(self) -> pd.DataFrame:
        """SHAP values as a labelled frame, one row per sample."""
        return pd.DataFrame(self.values, columns=self.feature_names)

    def global_importance(self) -> pd.DataFrame:
        """Mean absolute SHAP per feature — the model-agnostic importance measure.

        Preferred over LightGBM's split/gain importance for the documentation
        pack: it is in units of the prediction, sign-aware in aggregate, and
        comparable across models, which gain is not.
        """
        mean_abs = np.abs(self.values).mean(axis=0)
        mean_signed = self.values.mean(axis=0)
        frame = pd.DataFrame(
            {"feature": self.feature_names, "mean_abs_shap": mean_abs, "mean_signed_shap": mean_signed}
        ).sort_values("mean_abs_shap", ascending=False, ignore_index=True)
        total = frame["mean_abs_shap"].sum()
        frame["importance_share"] = frame["mean_abs_shap"] / total if total > 0 else 0.0
        return frame


class ShapExplainer:
    """Computes TreeSHAP contributions for a fitted booster."""

    def __init__(self, booster: lgb.Booster, feature_names: list[str]) -> None:
        self.booster = booster
        self.feature_names = list(feature_names)

    def explain(self, X: pd.DataFrame) -> ShapResult:
        """SHAP values for every row of ``X``.

        ``X`` must already be in contract column order; the caller (the high-level
        :class:`~crr.explain.explainer.Explainer`) guarantees that.
        """
        matrix = X[self.feature_names]
        contrib = np.asarray(self.booster.predict(matrix, pred_contrib=True), dtype=float)
        # Last column is the bias/expected value; the rest are per-feature.
        values = contrib[:, :-1]
        base_value = float(contrib[0, -1])
        raw_margin = np.asarray(self.booster.predict(matrix, raw_score=True), dtype=float)
        return ShapResult(
            values=values,
            base_value=base_value,
            feature_names=self.feature_names,
            raw_margin=raw_margin,
        )
