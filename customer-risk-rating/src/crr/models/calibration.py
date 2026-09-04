"""Probability calibration.

LightGBM's raw output ranks well but is not a probability you can quote. A
calibrator fixes the level without disturbing the ranking.

Two methods, and the default is **Platt scaling**, chosen on measurement rather
than habit. On the phase 2 test split:

    method          test AUC     ECE   distinct values
    uncalibrated      0.7692  0.0128             8,217
    isotonic          0.7678  0.0087                43
    platt             0.7692  0.0056             8,217

Isotonic is the usual reflex and it loses on both axes here. It is monotone
*non-decreasing*, so it merges scores into flat steps — 8,217 distinct scores
collapse to 43 — which costs a little AUC and, more importantly for this product,
destroys the granularity of a 0-100 risk score whose policy bands cut at 25, 50
and 75. Platt is strictly monotone, so AUC is preserved exactly.

Isotonic remains available: it fits any monotone distortion, so it wins when
miscalibration is not sigmoid-shaped and there is enough validation data to
support it. That is a decision to make with the numbers in front of you.

Both are fitted on the **validation** split — never on training, where the model
is overconfident by construction, and never on test, which would make the
reported calibration self-fulfilling.

Persisted as JSON rather than pickle: a pickle is a code-execution primitive, and
a model artefact is exactly the kind of file that gets copied between
environments by people who did not create it.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


class IsotonicCalibrator:
    """Monotone mapping from raw score to calibrated probability."""

    def __init__(self) -> None:
        self.x_thresholds: np.ndarray | None = None
        self.y_thresholds: np.ndarray | None = None

    @property
    def is_fitted(self) -> bool:
        return self.x_thresholds is not None

    def fit(self, scores: np.ndarray, y_true: np.ndarray) -> IsotonicCalibrator:
        model = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip", increasing=True)
        model.fit(np.asarray(scores, dtype=float), np.asarray(y_true, dtype=float))
        self.x_thresholds = np.asarray(model.X_thresholds_, dtype=float)
        self.y_thresholds = np.asarray(model.y_thresholds_, dtype=float)
        return self

    def transform(self, scores: np.ndarray) -> np.ndarray:
        if not self.is_fitted:
            raise RuntimeError("calibrator has not been fitted")
        return np.clip(
            np.interp(np.asarray(scores, dtype=float), self.x_thresholds, self.y_thresholds), 0.0, 1.0
        )

    def save(self, path: str | Path) -> None:
        if not self.is_fitted:
            raise RuntimeError("cannot save an unfitted calibrator")
        Path(path).write_text(
            json.dumps(
                {
                    "method": "isotonic",
                    "x_thresholds": self.x_thresholds.tolist(),
                    "y_thresholds": self.y_thresholds.tolist(),
                }
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> IsotonicCalibrator:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        calibrator = cls()
        calibrator.x_thresholds = np.asarray(payload["x_thresholds"], dtype=float)
        calibrator.y_thresholds = np.asarray(payload["y_thresholds"], dtype=float)
        return calibrator


def _logit(probabilities: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(probabilities, dtype=float), 1e-9, 1 - 1e-9)
    return np.log(clipped / (1 - clipped))


class PlattCalibrator:
    """Logistic recalibration of the model's log-odds.

    Fits ``P(y=1) = sigmoid(a * logit(raw) + b)``. Two parameters, strictly
    monotone, so the ranking — and therefore AUC, Gini and KS — is preserved
    exactly. The default for this project; see the module docstring.
    """

    def __init__(self) -> None:
        self.slope: float | None = None
        self.intercept: float | None = None

    @property
    def is_fitted(self) -> bool:
        return self.slope is not None

    def fit(self, scores: np.ndarray, y_true: np.ndarray) -> PlattCalibrator:
        model = LogisticRegression(C=1e6, solver="lbfgs")
        model.fit(_logit(scores).reshape(-1, 1), np.asarray(y_true, dtype=int))
        self.slope = float(model.coef_[0][0])
        self.intercept = float(model.intercept_[0])
        return self

    def transform(self, scores: np.ndarray) -> np.ndarray:
        if not self.is_fitted:
            raise RuntimeError("calibrator has not been fitted")
        linear = self.slope * _logit(scores) + self.intercept
        return 1.0 / (1.0 + np.exp(-np.clip(linear, -35.0, 35.0)))

    def save(self, path: str | Path) -> None:
        if not self.is_fitted:
            raise RuntimeError("cannot save an unfitted calibrator")
        Path(path).write_text(
            json.dumps({"method": "platt", "slope": self.slope, "intercept": self.intercept}), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> PlattCalibrator:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        calibrator = cls()
        calibrator.slope = float(payload["slope"])
        calibrator.intercept = float(payload["intercept"])
        return calibrator


#: Calibrator implementations by name, for the artefact loader.
CALIBRATORS = {"platt": PlattCalibrator, "isotonic": IsotonicCalibrator}


def load_calibrator(path: str | Path) -> PlattCalibrator | IsotonicCalibrator:
    """Load whichever calibrator was saved, identified by the method field."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    method = payload.get("method", "isotonic")
    return CALIBRATORS[method].load(path)
