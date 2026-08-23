"""Model layer: gradient-boosted baseline, calibration and evaluation metrics."""

from crr.models.baseline import DEFAULT_PARAMS, ModelArtifact, build_artifact, train_booster
from crr.models.calibration import IsotonicCalibrator, PlattCalibrator
from crr.models.metrics import calibration_table, decile_table, format_metrics, summarise

__all__ = [
    "DEFAULT_PARAMS",
    "IsotonicCalibrator",
    "PlattCalibrator",
    "ModelArtifact",
    "build_artifact",
    "calibration_table",
    "decile_table",
    "format_metrics",
    "summarise",
    "train_booster",
]
