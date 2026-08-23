#!/usr/bin/env python3
"""Train and evaluate the phase 2 tabular baseline.

Fits the feature pipeline on the training split only, trains a LightGBM model
with out-of-time early stopping, calibrates on validation, and evaluates on a
held-out later period. Reports the phase 2 exit criteria explicitly so the
result is a pass/fail, not an impression.

Examples
--------
    python scripts/train_baseline.py
    python scripts/train_baseline.py --target financial_crime_12m --ablation
    python scripts/train_baseline.py --data data/raw --out models/credit --seed 7
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from crr.features import FeaturePipeline  # noqa: E402
from crr.llm.anthropic_extractor import AnthropicExtractor  # noqa: E402
from crr.llm.batch import extract_all  # noqa: E402
from crr.llm.reference_extractor import ReferenceExtractor  # noqa: E402
from crr.models import build_artifact, format_metrics, summarise, train_booster  # noqa: E402
from crr.models.metrics import calibration_table, decile_table  # noqa: E402

TARGETS = ("default_12m", "financial_crime_12m")

#: Exit criteria for phase 2. Stated as numbers so the script can judge itself.
MAX_ECE = 0.02
"""Expected calibration error. The roadmap said 'under 2 percentage points per
decile'; that was refined to a count-weighted mean because the worst of ten bins
is an extreme order statistic dominated by sampling noise at these prevalences.
The worst bin is still reported, with its standard error, alongside."""

OVERFIT_SIGMA = 2.0
"""A validation-to-test AUC gap is called overfitting only if it exceeds this many
standard errors of the gap. The original 'within 0.01 AUC' wording is below the
noise floor for a split of this size, so it would fail good models at random."""


def load_data(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    customers = pd.read_csv(data_dir / "customers.csv", parse_dates=["snapshot_date"])
    outcomes = pd.read_csv(data_dir / "outcomes.csv")
    events_path = data_dir / "events.csv"
    events = pd.read_csv(events_path, parse_dates=["event_ts"]) if events_path.exists() else pd.DataFrame()
    narratives_path = data_dir / "narratives.csv"
    narratives = pd.read_csv(narratives_path) if narratives_path.exists() else None
    return customers, outcomes, events, narratives


def load_extractor(name: str) -> AnthropicExtractor | ReferenceExtractor:
    if name == "reference":
        return ReferenceExtractor()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    extractor = AnthropicExtractor(api_key=api_key)
    if not extractor.available:
        print("warning: --extractor anthropic requested but ANTHROPIC_API_KEY is not set "
              "(or the anthropic package is not installed) — every extraction will be degraded.", file=sys.stderr)
    return extractor


def split_masks(customers: pd.DataFrame) -> dict[str, np.ndarray]:
    return {name: (customers["split"] == name).to_numpy() for name in ("train", "validation", "test")}


def run_ablation(
    X: pd.DataFrame,
    y: np.ndarray,
    masks: dict[str, np.ndarray],
    pipeline: FeaturePipeline,
    seed: int,
    n_seeds: int = 3,
) -> pd.DataFrame:
    """Does each feature block earn its place? Train on cumulative subsets.

    Without this, 134 features is an assertion. With it, it is a measurement —
    and if a block adds nothing, that is worth knowing before it becomes 134
    columns of serving latency, monitoring surface and SHAP output.

    Averaged over several seeds, and the seed-to-seed spread is reported next to
    every delta. A single-seed ablation at this sample size produces swings of
    +-0.007 AUC from the random seed alone, which reads as a series of real
    regressions and is nothing of the sort. Reporting the noise floor is what
    stops the next person tuning against it.
    """
    by_block: dict[str, list[str]] = {}
    for spec in pipeline.contract.specs:
        by_block.setdefault(spec.block, []).append(spec.name)

    steps = [
        ("raw customer columns", ["customer"]),
        ("+ categorical encoding", ["customer", "categorical"]),
        ("+ missing indicators", ["customer", "categorical", "indicator"]),
        ("+ engineered ratios", ["customer", "categorical", "indicator", "derived"]),
        ("+ event aggregates (all)", ["customer", "categorical", "indicator", "derived", "event"]),
        ("+ text extraction (phase 7)",
         ["customer", "categorical", "indicator", "derived", "event", "text_extraction"]),
    ]

    rows = []
    previous: float | None = None
    for label, blocks in steps:
        columns = [name for block in blocks for name in by_block.get(block, [])]
        if not columns:
            continue
        subset = X[columns]
        categorical = [c for c in pipeline.contract.categorical_names if c in columns]
        aucs, pr_aucs = [], []
        for offset in range(n_seeds):
            booster = train_booster(
                subset[masks["train"]], y[masks["train"]],
                subset[masks["validation"]], y[masks["validation"]],
                categorical, seed=seed + offset,
            )
            metrics = summarise(y[masks["test"]], np.asarray(booster.predict(subset[masks["test"]]), dtype=float))
            aucs.append(metrics["auc"])
            pr_aucs.append(metrics["pr_auc"])
        mean_auc = float(np.mean(aucs))
        rows.append(
            {
                "feature_set": label,
                "n_features": len(columns),
                "test_auc": round(mean_auc, 4),
                "seed_sd": round(float(np.std(aucs)), 4),
                "test_pr_auc": round(float(np.mean(pr_aucs)), 4),
                "delta_auc": None if previous is None else round(mean_auc - previous, 4),
            }
        )
        previous = mean_auc
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=REPO_ROOT / "data" / "raw")
    parser.add_argument("--target", choices=TARGETS, default="default_12m")
    parser.add_argument("--out", type=Path, default=None, help="artefact directory (default: models/<target>)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--calibration", choices=["platt", "isotonic"], default="platt",
                        help="see crr.models.calibration for why platt is the default")
    parser.add_argument("--ablation", action="store_true", help="measure the contribution of each feature block")
    parser.add_argument("--ablation-seeds", type=int, default=3, help="seeds to average each ablation row over")
    parser.add_argument("--extractor", choices=["reference", "anthropic"], default="reference",
                        help="reference: deterministic, no network, the honest floor (default). "
                             "anthropic: the real extractor, needs ANTHROPIC_API_KEY.")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)

    out_dir = args.out or (REPO_ROOT / "models" / args.target)

    customers, outcomes, events, narratives = load_data(args.data)
    labels = outcomes.set_index("customer_id")[args.target]
    y = labels.loc[customers["customer_id"]].to_numpy(dtype=int)
    masks = split_masks(customers)

    print("=" * 78)
    print(f"PHASE 2 BASELINE — target: {args.target}")
    print("=" * 78)
    for name, mask in masks.items():
        print(f"  {name:<12}{int(mask.sum()):>8,} rows   prevalence {y[mask].mean():>7.2%}")
    print()

    text_features = None
    if narratives is not None:
        extractor = load_extractor(args.extractor)
        print(f"  extracting text signals ({args.extractor}) for {len(narratives):,} customers...")
        text_features = extract_all(narratives, extractor)
        degraded_share = text_features["degraded"].mean()
        note = " (expected: no API key/package for --extractor anthropic)" if degraded_share and args.extractor == "anthropic" else ""
        print(f"  extraction done: {degraded_share:.1%} degraded{note}")
        print()

    # Fit on TRAIN ONLY. Fitting the vocabulary on all splits would let the
    # encoder see categories that exist only in the future.
    pipeline = FeaturePipeline().fit(customers[masks["train"]], events, text_features)
    X = pipeline.transform(customers, events, text_features)
    print(f"  feature matrix: {X.shape[0]:,} x {X.shape[1]} "
          f"({len(pipeline.contract.categorical_names)} categorical, {X.isna().to_numpy().mean():.1%} null)")

    booster = train_booster(
        X[masks["train"]], y[masks["train"]],
        X[masks["validation"]], y[masks["validation"]],
        pipeline.contract.categorical_names, seed=args.seed,
    )
    manifest_path = args.data / "manifest.json"
    data_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    artifact = build_artifact(
        booster, pipeline.contract, args.target,
        X[masks["validation"]], y[masks["validation"]],
        calibration=args.calibration,
        extra_metadata={
            "seed": args.seed,
            "n_train": int(masks["train"].sum()),
            "n_validation": int(masks["validation"].sum()),
            "n_test": int(masks["test"].sum()),
            # Ties the model to the exact dataset that produced it.
            "data_config_hash": data_manifest.get("config_hash"),
            "data_schema_version": data_manifest.get("schema_version"),
        },
    )
    print(f"  stopped at iteration {artifact.metadata['best_iteration']}")
    print()

    metrics = {name: summarise(y[mask], artifact.predict_proba(X[mask])) for name, mask in masks.items()}
    artifact.metrics = metrics

    print("PERFORMANCE (calibrated probabilities)")
    for name in ("train", "validation", "test"):
        print(format_metrics(name, metrics[name]))

    # Reported, not gated. Some train-to-holdout gap is expected with early
    # stopping, so a hard threshold would be arbitrary. But a large gap means the
    # model is fitting individuals rather than patterns, which is invisible in the
    # test metrics and shows up later as instability under drift. The low-
    # prevalence financial-crime target sits here: ~650 positives against 134
    # features memorises easily.
    memorisation = metrics["train"]["auc"] - metrics["test"]["auc"]
    note = "  <- large; model is memorising, expect instability under drift" if memorisation > 0.12 else ""
    print(f"  {'memorisation':<12} train - test AUC = {memorisation:+.4f}{note}")
    print()

    test_scores = artifact.predict_proba(X[masks["test"]])
    print("TEST DECILES")
    deciles = decile_table(y[masks["test"]], test_scores)
    print(f"  {'decile':<8}{'n':>7}{'mean p':>10}{'observed':>11}{'lift':>8}")
    for row in deciles.itertuples():
        print(f"  {row.decile:<8}{row.n:>7,}{row.mean_score:>10.4f}{row.rate:>11.2%}{row.lift:>8.2f}x")
    print()

    print("TEST CALIBRATION (predicted vs observed, +-2 standard errors)")
    table = calibration_table(y[masks["test"]], test_scores)
    print(f"  {'bin':<6}{'n':>7}{'predicted':>11}{'observed':>10}{'diff':>9}{'2se':>8}   ok")
    for row in table.itertuples():
        print(f"  {row.bin:<6}{row.n:>7,}{row.predicted:>11.4f}{row.observed:>10.4f}"
              f"{row.difference:>+9.4f}{2 * row.std_error:>8.4f}   {'yes' if row.within_2se else 'NO'}")
    print()

    print("TOP FEATURES BY GAIN")
    for row in artifact.feature_importance(12).itertuples():
        print(f"  {row.feature:<38}{row.gain_share:>7.2%}")
    print()

    if args.ablation:
        print(f"FEATURE BLOCK ABLATION (uncalibrated test AUC, mean of {args.ablation_seeds} seeds)")
        ablation = run_ablation(X, y, masks, pipeline, args.seed, args.ablation_seeds)
        print(f"  {'feature set':<28}{'n':>5}{'test AUC':>10}{'sd':>8}{'PR-AUC':>9}{'delta':>9}")
        for row in ablation.itertuples():
            delta = "" if row.delta_auc is None else f"{row.delta_auc:+.4f}"
            print(f"  {row.feature_set:<28}{row.n_features:>5}{row.test_auc:>10.4f}"
                  f"{row.seed_sd:>8.4f}{row.test_pr_auc:>9.4f}{delta:>9}")
        noise = float(ablation["seed_sd"].mean())
        biggest = ablation["delta_auc"].dropna().abs().max() if len(ablation) > 1 else 0.0
        print(f"\n  seed noise ~{noise:.4f} AUC; largest block delta {biggest:.4f}.")
        print("  " + ("No block moves AUC beyond the noise floor — see docs/ROADMAP.md."
                      if biggest <= 2 * noise else
                      "At least one block moves AUC beyond the noise floor."))
        artifact.metrics["ablation"] = ablation.to_dict(orient="records")
        print()

    # ---- exit criteria ---------------------------------------------------
    gap = metrics["test"]["auc"] - metrics["validation"]["auc"]
    gap_se = float(np.hypot(metrics["test"]["auc_std_error"], metrics["validation"]["auc_std_error"]))
    tolerance = OVERFIT_SIGMA * gap_se
    checks = [
        (
            f"no overfit: |test - validation| AUC {abs(gap):.4f} <= {tolerance:.4f} ({OVERFIT_SIGMA:.0f} SE)",
            abs(gap) <= tolerance,
        ),
        (f"calibration: test ECE {metrics['test']['expected_calibration_error']:.4f} <= {MAX_ECE}",
         metrics["test"]["expected_calibration_error"] <= MAX_ECE),
        (f"calibration bins within 2 SE: {metrics['test']['calibration_bins_within_2se']}/{metrics['test']['calibration_bins']} >= 8",
         metrics["test"]["calibration_bins_within_2se"] >= 8),
        (f"discrimination: test AUC {metrics['test']['auc']:.4f} >= 0.70", metrics["test"]["auc"] >= 0.70),
    ]
    print("PHASE 2 EXIT CRITERIA")
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print("=" * 78)

    if not args.no_save:
        out_dir.mkdir(parents=True, exist_ok=True)
        pipeline.save(out_dir)
        artifact.save(out_dir)
        print(f"\nartefacts written to {out_dir}")

    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
