"""Gradient-boosted baseline over the tabular feature block.

Deliberate choices worth defending:

**No class re-weighting.** ``scale_pos_weight`` and ``is_unbalance`` are the
reflex for a 5%-prevalence target. Both destroy calibration: the model then
predicts a rebalanced world's probabilities, not this one's. Discrimination is
unaffected by them either way, so they buy nothing and cost the thing we need.
The imbalance is handled where it belongs — in the metrics (PR-AUC, not accuracy)
and in isotonic calibration afterwards.

**Native categorical handling** rather than one-hot. Country of residence has ~33
levels; one-hot would scatter its signal across 33 sparse columns, and make the
phase 3 SHAP output unreadable.

**Early stopping on the validation split**, which is a distinct time period from
both training and test. Stopping on a random slice of training data would tune
the iteration count on the same distribution it fits.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from crr.features.contract import FeatureContract
from crr.models.calibration import CALIBRATORS, IsotonicCalibrator, PlattCalibrator, load_calibrator

#: Starting point, not a tuned result. Conservative depth and strong
#: regularisation because the positive class is small: with ~1,500 defaults in
#: training, a deep tree memorises individuals.
DEFAULT_PARAMS: dict[str, Any] = {
    "objective": "binary",
    "metric": "auc",
    "boosting_type": "gbdt",
    "learning_rate": 0.03,
    "num_leaves": 24,
    "min_data_in_leaf": 40,
    "feature_fraction": 0.75,
    "bagging_fraction": 0.80,
    "bagging_freq": 1,
    "lambda_l1": 0.0,
    "lambda_l2": 2.0,
    # Categorical splits are held back hard, and that is a measured decision, not
    # a default. LightGBM picks category subsets by gradient statistics, which
    # overfits badly on high-cardinality columns when the positive class is small:
    # with country_of_residence (33 levels) and occupation (30 levels) against
    # ~2,100 defaults, the loose settings cost 0.005 test AUC versus dropping the
    # categoricals entirely. These values recover most of that. If occupation and
    # country need to contribute more than this, the answer is out-of-fold target
    # or WOE encoding, not looser splits.
    "max_cat_threshold": 8,
    "cat_smooth": 200.0,
    "cat_l2": 25.0,
    "min_data_per_group": 200,
    "verbosity": -1,
}

MAX_ROUNDS = 3000
EARLY_STOPPING_ROUNDS = 150


@dataclass
class ModelArtifact:
    """A trained model plus everything needed to reproduce and audit its output."""

    booster: lgb.Booster
    calibrator: PlattCalibrator | IsotonicCalibrator
    contract: FeatureContract
    target: str
    metrics: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ---- inference -------------------------------------------------------

    def predict_raw(self, X: pd.DataFrame) -> np.ndarray:
        """Uncalibrated model output. Correctly ranked, not a probability."""
        self.contract.validate(X, check_ranges=False)
        return np.asarray(self.booster.predict(X[self.contract.names]), dtype=float)

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Calibrated probability of the modelled outcome."""
        return self.calibrator.transform(self.predict_raw(X))

    def feature_importance(self, top: int | None = None) -> pd.DataFrame:
        frame = pd.DataFrame(
            {
                "feature": self.booster.feature_name(),
                "gain": self.booster.feature_importance("gain"),
                "split": self.booster.feature_importance("split"),
            }
        ).sort_values("gain", ascending=False, ignore_index=True)
        frame["gain_share"] = frame["gain"] / max(frame["gain"].sum(), 1e-9)
        return frame.head(top) if top else frame

    # ---- persistence -----------------------------------------------------

    def save(self, directory: str | Path) -> None:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(str(path / "model.txt"))
        self.calibrator.save(path / "calibrator.json")
        self.contract.save(path / "feature_contract.json")
        (path / "metrics.json").write_text(json.dumps(self.metrics, indent=2, default=str), encoding="utf-8")
        (path / "metadata.json").write_text(json.dumps(self.metadata, indent=2, default=str), encoding="utf-8")

    @classmethod
    def load(cls, directory: str | Path) -> ModelArtifact:
        path = Path(directory)
        metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        return cls(
            booster=lgb.Booster(model_file=str(path / "model.txt")),
            calibrator=load_calibrator(path / "calibrator.json"),
            contract=FeatureContract.load(path / "feature_contract.json"),
            target=metadata["target"],
            metrics=json.loads((path / "metrics.json").read_text(encoding="utf-8")),
            metadata=metadata,
        )


def train_booster(
    X_train: pd.DataFrame,
    y_train: np.ndarray,
    X_valid: pd.DataFrame,
    y_valid: np.ndarray,
    categorical_features: list[str],
    params: dict[str, Any] | None = None,
    seed: int = 42,
) -> lgb.Booster:
    """Fit a booster, stopping when the out-of-time validation AUC stops improving."""
    settings = {**DEFAULT_PARAMS, **(params or {}), "seed": seed, "deterministic": True, "num_threads": 0}

    train_set = lgb.Dataset(
        X_train, label=y_train, categorical_feature=categorical_features, free_raw_data=False
    )
    valid_set = lgb.Dataset(
        X_valid, label=y_valid, categorical_feature=categorical_features, reference=train_set, free_raw_data=False
    )
    return lgb.train(
        settings,
        train_set,
        num_boost_round=MAX_ROUNDS,
        valid_sets=[valid_set],
        valid_names=["validation"],
        callbacks=[
            lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
            lgb.log_evaluation(0),
        ],
    )


def build_artifact(
    booster: lgb.Booster,
    contract: FeatureContract,
    target: str,
    X_valid: pd.DataFrame,
    y_valid: np.ndarray,
    *,
    params: dict[str, Any] | None = None,
    calibration: str = "platt",
    extra_metadata: dict[str, Any] | None = None,
) -> ModelArtifact:
    """Wrap a fitted booster, calibrating on the validation split."""
    if calibration not in CALIBRATORS:
        raise ValueError(f"unknown calibration method {calibration!r}; expected one of {sorted(CALIBRATORS)}")
    raw_valid = np.asarray(booster.predict(X_valid[contract.names]), dtype=float)
    calibrator = CALIBRATORS[calibration]().fit(raw_valid, y_valid)

    metadata = {
        "target": target,
        "trained_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "lightgbm_version": lgb.__version__,
        "pipeline_version": contract.pipeline_version,
        "contract_version": contract.version,
        "n_features": len(contract.names),
        "n_categorical": len(contract.categorical_names),
        "calibration": calibration,
        "best_iteration": int(booster.best_iteration or booster.current_iteration()),
        "params": {**DEFAULT_PARAMS, **(params or {})},
        **(extra_metadata or {}),
    }
    return ModelArtifact(
        booster=booster, calibrator=calibrator, contract=contract, target=target, metadata=metadata
    )
