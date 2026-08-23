#!/usr/bin/env python3
"""Phase 8 feedback loop: retrain a challenger on true outcomes (never on
human decisions), measure human-model disagreement and the segment bias a
naive decision-trained model would have reproduced, run the fairness and
drift checks, and evaluate the champion/challenger promotion gate.

Automatic gates decide *eligibility* (a measured out-of-time AUC gain, and no
non-exempt fairness failure); a human decides *promotion* — pass --approve to
actually promote once you have reviewed the printed report. Without
--approve, the challenger is always evaluated and saved as a pending
candidate, never auto-promoted, which is the honest default for a system
config/risk_policy.yaml marks require_human_approval: true.

Examples
--------
    python scripts/retrain.py --target default_12m
    python scripts/retrain.py --target financial_crime_12m --approve
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from crr.features import FeaturePipeline  # noqa: E402
from crr.governance import (  # noqa: E402
    bias_reproduction_report,
    calibration_drift,
    decision_label,
    evaluate_promotion,
    fairness_report,
    human_model_disagreement,
    psi_report,
)
from crr.llm.anthropic_extractor import AnthropicExtractor  # noqa: E402
from crr.llm.batch import extract_all  # noqa: E402
from crr.llm.reference_extractor import ReferenceExtractor  # noqa: E402
from crr.models import ModelArtifact, build_artifact, summarise, train_booster  # noqa: E402
from crr.models.metrics import expected_calibration_error  # noqa: E402
from crr.policy import DEFAULT_POLICY_PATH, load_policy  # noqa: E402

TARGETS = ("default_12m", "financial_crime_12m")


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
    extractor = AnthropicExtractor(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    if not extractor.available:
        print("warning: --extractor anthropic requested but unavailable — extractions will be degraded.", file=sys.stderr)
    return extractor


def split_masks(customers: pd.DataFrame) -> dict[str, np.ndarray]:
    return {name: (customers["split"] == name).to_numpy() for name in ("train", "validation", "test")}


def single_target_score(probability: np.ndarray, policy, dimension: str) -> np.ndarray:
    """This target's own probability run through the real composite formula
    with the other dimension held at zero risk — the exact production curve
    (crr.policy.RiskPolicy.composite_score), not a re-derived approximation,
    evaluated as if this were the only axis being scored. Used for governance
    reporting on a single freshly-trained target; the live API always scores
    both dimensions together for the real composite band."""
    if dimension == "credit":
        return np.array([policy.composite_score(p, 0.0) for p in probability])
    return np.array([policy.composite_score(0.0, p) for p in probability])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=REPO_ROOT / "data" / "raw")
    parser.add_argument("--target", choices=TARGETS, default="default_12m")
    parser.add_argument("--champion", type=Path, default=None, help="incumbent artefact directory (default: models/<target>)")
    parser.add_argument("--out", type=Path, default=None, help="challenger candidate directory (default: models/<target>_challenger)")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--calibration", choices=["platt", "isotonic"], default="platt")
    parser.add_argument("--extractor", choices=["reference", "anthropic"], default="reference")
    parser.add_argument("--approve", action="store_true", help="actually promote the challenger if it is eligible")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)

    champion_dir = args.champion or (REPO_ROOT / "models" / args.target)
    out_dir = args.out or (REPO_ROOT / "models" / f"{args.target}_challenger")
    policy = load_policy(args.policy)
    dimension = "credit" if args.target == "default_12m" else "financial_crime"

    print("=" * 78)
    print(f"PHASE 8 FEEDBACK LOOP — target: {args.target}")
    print("=" * 78)

    customers, outcomes, events, narratives = load_data(args.data)
    labels = outcomes.set_index("customer_id")
    y_outcome = labels.loc[customers["customer_id"], args.target].to_numpy(dtype=int)
    decision = labels.loc[customers["customer_id"], "underwriter_decision"].reset_index(drop=True)
    y_decision = decision_label(decision)
    masks = split_masks(customers)

    # ---- gate 1: enough freshly labelled cases to even attempt a retrain --
    min_cases = int(policy.feedback.get("min_labelled_cases_before_retrain", 0))
    n_labelled = int(len(y_outcome))
    print(f"\n[{'PASS' if n_labelled >= min_cases else 'FAIL'}] {n_labelled:,} outcome-labelled cases "
          f">= {min_cases:,} required (policy.feedback.min_labelled_cases_before_retrain)")
    if n_labelled < min_cases:
        print("not enough labelled outcomes yet — declining to retrain this cycle.")
        return 1

    text_features = None
    if narratives is not None:
        extractor = load_extractor(args.extractor)
        text_features = extract_all(narratives, extractor)

    # ---- train the challenger on OUTCOMES, never on the human decision ----
    pipeline = FeaturePipeline().fit(customers[masks["train"]], events, text_features)
    X = pipeline.transform(customers, events, text_features)
    booster = train_booster(
        X[masks["train"]], y_outcome[masks["train"]],
        X[masks["validation"]], y_outcome[masks["validation"]],
        pipeline.contract.categorical_names, seed=args.seed,
    )
    challenger = build_artifact(
        booster, pipeline.contract, args.target, X[masks["validation"]], y_outcome[masks["validation"]],
        calibration=args.calibration, extra_metadata={"seed": args.seed, "trained_on": "outcomes"},
    )
    challenger.metrics = {name: summarise(y_outcome[mask], challenger.predict_proba(X[mask])) for name, mask in masks.items()}
    print(f"\nchallenger: test AUC {challenger.metrics['test']['auc']:.4f}  "
          f"ECE {challenger.metrics['test']['expected_calibration_error']:.4f}")

    champion: ModelArtifact | None = None
    if (champion_dir / "metadata.json").exists():
        champion = ModelArtifact.load(champion_dir)
        print(f"champion:   test AUC {champion.metrics['test']['auc']:.4f}  "
              f"(from {champion_dir})")
    else:
        print(f"champion:   none on record at {champion_dir} — this would be the first model for this target")

    # ---- human-model disagreement (monitoring only — never a training label) --
    band_test = pd.Series(policy.band_for_score(s) for s in single_target_score(
        challenger.predict_proba(X[masks["test"]]), policy, dimension
    ))
    disagreement = human_model_disagreement(decision[masks["test"]].reset_index(drop=True), band_test)
    print(f"\nHUMAN-MODEL DISAGREEMENT (test split, {disagreement['n']:,} cases)")
    print(f"  agreement rate: {disagreement['agreement_rate']:.2%}")

    # ---- does training on the decision instead of the outcome reproduce ---
    # ---- the bias the generator gave these humans on purpose? -------------
    bias_report = bias_reproduction_report(
        X, masks, pipeline.contract.categorical_names, y_decision,
        challenger.predict_proba(X[masks["test"]]), customers["segment"][masks["test"]].reset_index(drop=True),
        seed=args.seed,
    )
    print("\nBIAS REPRODUCTION CHECK (decision-trained vs outcome-trained, by segment)")
    if len(bias_report):
        print(f"  {'segment':<18}{'n':>7}{'outcome gap':>14}{'decision gap':>15}{'reproduced bias':>18}")
        for row in bias_report.itertuples():
            print(f"  {row.segment:<18}{row.n:>7,}{row.outcome_trained_gap:>14.3f}"
                  f"{row.decision_trained_gap:>15.3f}{row.reproduced_bias:>18.3f}")
    else:
        print("  not enough customers per segment on this split to report.")

    # ---- fairness (test split — the out-of-time, honest evaluation slice) --
    fairness_results = fairness_report(customers[masks["test"]].reset_index(drop=True), y_outcome[masks["test"]], band_test, args.target)
    print("\nFAIRNESS (four-fifths rule, tolerance 0.8, test split)")
    for result in fairness_results:
        tag = "EXEMPT" if result.exempt_reason else ("PASS" if result.passes else "FAIL")
        print(f"  [{tag}] {result.attribute}: disparate-impact {'ok' if result.disparate_impact_pass else 'BELOW TOLERANCE'}, "
              f"equal-opportunity {'ok' if result.equal_opportunity_pass else 'BELOW TOLERANCE'}")
        if result.exempt_reason:
            print(f"         exemption on record: {result.exempt_reason}")
        if not result.passes and not result.exempt_reason:
            for row in result.groups.itertuples():
                eo = f"{row.fpr_parity_ratio:.2f}" if row.fp_count >= 5 else "n/a (<5 FP)"
                print(f"           {row.group!s:<22}{row.n:>7,}  favourable {row.favourable_rate:>6.2%}  "
                      f"DI {row.disparate_impact_ratio:>5.2f}  fpr {row.fpr:>6.2%} (fp={row.fp_count})  EO {eo}")

    # ---- drift: train+validation (what the model was built on) vs test ----
    # ---- (what "today" looks like) -----------------------------------------
    reference_mask = masks["train"] | masks["validation"]
    psi = psi_report(X[reference_mask], X[masks["test"]], pipeline.contract.names)
    worst = psi.head(8)
    print(f"\nPOPULATION DRIFT (PSI, train+validation vs test, worst {len(worst)} of {len(psi)} features)")
    for row in worst.itertuples():
        print(f"  {row.feature:<32}{row.psi:>8.4f}  {row.severity}")
    major = int((psi["severity"] == "major").sum())
    print(f"  {major} of {len(psi)} features show major drift (PSI >= 0.25)" if major
          else "  no feature shows major drift (PSI >= 0.25)")

    if champion is not None:
        champion_test_scores = champion.predict_proba(X[masks["test"]])
        current_ece = expected_calibration_error(y_outcome[masks["test"]], champion_test_scores)
        drift = calibration_drift(current_ece, champion.metrics["validation"]["expected_calibration_error"])
    else:
        drift = calibration_drift(
            challenger.metrics["test"]["expected_calibration_error"],
            challenger.metrics["validation"]["expected_calibration_error"],
        )
    print(f"\nCALIBRATION DRIFT ({'champion' if champion is not None else 'challenger'}, validation baseline vs test)")
    print(f"  baseline ECE {drift['baseline_ece']:.4f}  current ECE {drift['current_ece']:.4f}  "
          f"{'DRIFTED' if drift['drifted'] else 'stable'}")

    # ---- promotion gate -----------------------------------------------------
    decision_obj = evaluate_promotion(
        args.target, challenger.metrics["test"], champion.metrics["test"] if champion is not None else None,
        fairness_results, policy.feedback, approved_by_human=args.approve,
    )
    print("\nPROMOTION DECISION")
    for reason in decision_obj.reasons:
        print(f"  - {reason}")
    print(f"  eligible: {decision_obj.eligible}   requires human approval: {decision_obj.requires_human_approval}   "
          f"approved this run: {decision_obj.approved_by_human}")
    print(f"  [{'PROMOTED' if decision_obj.promoted else 'NOT PROMOTED'}]")
    print("=" * 78)

    if args.approve and not decision_obj.eligible:
        print("\n--approve was passed but the challenger is not eligible — nothing was promoted.", file=sys.stderr)
        return 1

    if not args.no_save:
        out_dir.mkdir(parents=True, exist_ok=True)
        pipeline.save(out_dir)
        challenger.save(out_dir)
        governance_report = {
            "target": args.target,
            "disagreement": disagreement,
            "bias_reproduction": bias_report.to_dict(orient="records"),
            "fairness": [r.to_dict() for r in fairness_results],
            "drift": {"psi": psi.to_dict(orient="records"), "calibration": drift},
            "promotion": decision_obj.to_dict(),
        }
        (out_dir / "governance_report.json").write_text(json.dumps(governance_report, indent=2, default=str), encoding="utf-8")
        print(f"\ncandidate written to {out_dir}")
        if decision_obj.promoted:
            champion_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(out_dir, champion_dir, dirs_exist_ok=True)
            print(f"promoted: {out_dir} -> {champion_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
