#!/usr/bin/env python3
"""Prove the synthetic dataset is fit to train on, before anyone trains on it.

Three questions, three answers:

1. **Is there learnable signal, and is its ceiling plausible?**
   A gradient-boosted baseline on the tabular block should land around
   AUC 0.80-0.88 on the out-of-time test split. Much lower means the generator is
   noise; ~0.99 means something leaks.

2. **Do we actually need a non-linear model?**
   The generator contains interaction terms on purpose. If a GBM does not beat
   logistic regression, the "XGBoost/LightGBM core" in the design is unjustified.

3. **Does the free text carry signal the tabular block does not?**
   A bag-of-words model over the narratives is added to the tabular features. If
   the lift is ~0, the whole hybrid LLM branch is decoration and we should say so.

This is a data check, not the model. It deliberately uses plain scikit-learn so it
stays runnable with no GPU and no model dependencies.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse

REPO_ROOT = Path(__file__).resolve().parents[1]

try:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
except ImportError:  # pragma: no cover
    print("error: this check needs scikit-learn (pip install scikit-learn)", file=sys.stderr)
    raise SystemExit(2) from None

#: Columns that are identifiers, PII or outcome-adjacent — never model inputs.
EXCLUDED = {
    "customer_id", "snapshot_date", "split", "full_name", "national_id", "email",
    "phone", "address_line", "jurisdiction_risk_tier",
}


def _load(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    customers = pd.read_csv(data_dir / "customers.csv")
    outcomes = pd.read_csv(data_dir / "outcomes.csv")
    narratives = pd.read_csv(data_dir / "narratives.csv")
    return customers, outcomes, narratives


def _tabular_matrix(customers: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    """Numeric block + one-hot categoricals, with the dirty categoricals normalised."""
    frame = customers.drop(columns=[c for c in EXCLUDED if c in customers.columns])
    categorical = frame.select_dtypes(include=["object", "string"]).columns.tolist()
    for column in categorical:
        frame[column] = frame[column].astype(str).str.strip().str.lower().str.replace(r"[\s-]+", "_", regex=True)
    encoded = pd.get_dummies(frame, columns=categorical, dummy_na=True)
    return encoded.to_numpy(dtype=float), encoded.columns.tolist()


def _make_gbm(seed: int) -> HistGradientBoostingClassifier:
    """One regularised configuration, reused everywhere a tree model is fitted.

    With ~1,100 positives in the training split, 31 leaves overfits and the tree
    model loses to logistic regression for reasons that have nothing to do with
    the data. These settings are a plain practitioner default, not a tuned result.
    """
    return HistGradientBoostingClassifier(
        max_iter=500,
        learning_rate=0.05,
        max_leaf_nodes=15,
        min_samples_leaf=40,
        l2_regularization=1.0,
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=25,
        random_state=seed,
    )


def _fit_gbm_mean_auc(X_train, y_train, X_test, y_test, seeds: tuple[int, ...]) -> tuple[float, float]:
    """Mean and spread of test AUC over several seeds.

    A single fit varies by roughly +-0.003 AUC, which is the same size as the
    effects we are trying to detect. Averaging makes the comparison meaningful.
    """
    scores = []
    for seed in seeds:
        model = _make_gbm(seed)
        model.fit(X_train, y_train)
        scores.append(roc_auc_score(y_test, model.predict_proba(X_test)[:, 1]))
    return float(np.mean(scores)), float(np.std(scores))


def _report(name: str, y_true: np.ndarray, scores: np.ndarray) -> float:
    auc = roc_auc_score(y_true, scores)
    ap = average_precision_score(y_true, scores)
    print(f"  {name:<44} AUC {auc:.4f}   PR-AUC {ap:.4f}")
    return float(auc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate that the synthetic dataset carries learnable signal.")
    parser.add_argument("--data", type=Path, default=REPO_ROOT / "data" / "raw", help="directory holding the CSVs")
    parser.add_argument("--target", default="default_12m", choices=["default_12m", "financial_crime_12m"])
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--n-seeds", type=int, default=3, help="tree-model fits to average over")
    args = parser.parse_args(argv)

    customers, outcomes, narratives = _load(args.data)
    oracle_column = "narrative_distress_level" if args.target == "default_12m" else "narrative_concealment_level"
    ground_truth_path = args.data / "ground_truth.csv"
    ground_truth = pd.read_csv(ground_truth_path) if ground_truth_path.exists() else None
    frame = customers.merge(outcomes[["customer_id", args.target]], on="customer_id").merge(
        narratives[["customer_id", "support_call_summary", "underwriter_note", "kyc_document_extract"]],
        on="customer_id",
    )

    oracle = None
    if ground_truth is not None and oracle_column in ground_truth.columns:
        frame = frame.merge(ground_truth[["customer_id", oracle_column]], on="customer_id", how="left")
        oracle = frame.pop(oracle_column).to_numpy(dtype=float)

    is_train = frame["split"].isin(["train", "validation"]).to_numpy()
    is_test = (frame["split"] == "test").to_numpy()
    y = frame[args.target].to_numpy()

    X, _ = _tabular_matrix(frame.drop(columns=[args.target, "support_call_summary", "underwriter_note", "kyc_document_extract"]))

    print("=" * 72)
    print(f"DATASET VALIDATION — target: {args.target}")
    print("=" * 72)
    print(f"  rows {len(frame):,}   train+val {is_train.sum():,}   test {is_test.sum():,}   prevalence {y.mean():.2%}")
    print()
    print("1. Is there learnable signal? (out-of-time test split)")

    logistic = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=0.5))
    logistic.fit(np.nan_to_num(X[is_train]), y[is_train])
    auc_lr = _report("logistic regression (tabular)", y[is_test], logistic.predict_proba(np.nan_to_num(X[is_test]))[:, 1])

    seeds = tuple(range(args.seed, args.seed + args.n_seeds))
    auc_gbm, sd_gbm = _fit_gbm_mean_auc(X[is_train], y[is_train], X[is_test], y[is_test], seeds)
    print(f"  {'gradient boosting (tabular)':<44} AUC {auc_gbm:.4f}   (+-{sd_gbm:.4f} over {len(seeds)} seeds)")

    print()
    print("2. Does the non-linear model earn its place?")
    print(f"  GBM - logistic = {auc_gbm - auc_lr:+.4f} AUC")

    print()
    print("3. Does the free text add signal the tabular block lacks?")
    # Two questions, kept separate on purpose.
    #
    #   HEADROOM  — what a perfect reader of the notes could add. Measured with the
    #               generator's own narrative level as an oracle feature. This is the
    #               number that decides whether the LLM branch is worth building.
    #   ACHIEVED  — what a cheap bag-of-words model actually extracts today.
    #
    # Both comparisons stay inside one model family so the difference is the text
    # and nothing else. An earlier version of this script stacked a fitted text
    # model into the GBM instead; the out-of-fold train scores and full-fit test
    # scores had different distributions and the "lift" came out negative.
    text = (
        frame["support_call_summary"].fillna("")
        + " \n "
        + frame["underwriter_note"].fillna("")
        + " \n "
        + frame["kyc_document_extract"].fillna("")
    ).to_numpy()
    vectoriser = TfidfVectorizer(max_features=3000, ngram_range=(1, 2), min_df=3, sublinear_tf=True)
    text_train = vectoriser.fit_transform(text[is_train])
    text_test = vectoriser.transform(text[is_test])

    text_model = LogisticRegression(max_iter=3000, C=1.0)
    text_model.fit(text_train, y[is_train])
    _report("text only (tf-idf + logistic)", y[is_test], text_model.predict_proba(text_test)[:, 1])

    scaler = StandardScaler().fit(np.nan_to_num(X[is_train]))
    union_train = sparse.hstack([sparse.csr_matrix(scaler.transform(np.nan_to_num(X[is_train]))), text_train]).tocsr()
    union_test = sparse.hstack([sparse.csr_matrix(scaler.transform(np.nan_to_num(X[is_test]))), text_test]).tocsr()
    union = LogisticRegression(max_iter=4000, C=0.5)
    union.fit(union_train, y[is_train])
    auc_union = _report("logistic (tabular + tf-idf)", y[is_test], union.predict_proba(union_test)[:, 1])
    print(f"  ACHIEVED by bag-of-words: {auc_union - auc_lr:+.4f} AUC over logistic(tabular)")

    oracle_auc = None
    if oracle is not None:
        X_oracle_train = np.column_stack([X[is_train], oracle[is_train]])
        X_oracle_test = np.column_stack([X[is_test], oracle[is_test]])
        oracle_auc, sd_oracle = _fit_gbm_mean_auc(X_oracle_train, y[is_train], X_oracle_test, y[is_test], seeds)
        print(f"  {'gbm (tabular + oracle narrative level)':<44} AUC {oracle_auc:.4f}   (+-{sd_oracle:.4f} over {len(seeds)} seeds)")
        print(f"  HEADROOM for a perfect reader: {oracle_auc - auc_gbm:+.4f} AUC over gbm(tabular)")
    else:
        print("  (ground_truth.csv not found — skipping the headroom measurement)")
    print()

    verdict = []
    verdict.append(("learnable signal", 0.72 <= auc_gbm <= 0.95))
    verdict.append(("non-linearity justified", auc_gbm > auc_lr))
    verdict.append(("bag-of-words text lift", auc_union > auc_lr))
    if oracle_auc is not None:
        verdict.append(("headroom for the LLM branch", oracle_auc - auc_gbm >= 0.01))
    print("VERDICT")
    for label, ok in verdict:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print("=" * 72)
    return 0 if all(ok for _, ok in verdict) else 1


if __name__ == "__main__":
    raise SystemExit(main())
