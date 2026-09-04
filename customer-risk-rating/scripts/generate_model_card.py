#!/usr/bin/env python3
"""Phase 8 documentation pack: assemble a model card from what is actually on
disk — the trained artefact's own metadata/metrics, the dataset manifest that
produced it, the current risk policy, and the governance report from the last
scripts/retrain.py run, if one exists.

Deliberately not hand-written prose about "the model" in the abstract: every
number in the card is read from a file another script produced, so the card
goes stale exactly when the artefact it describes changes, and regenerating
it after a retrain is a one-line command rather than a memory to maintain.

Examples
--------
    python scripts/generate_model_card.py --target default_12m
    python scripts/generate_model_card.py --target financial_crime_12m
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from crr.governance import FAIRNESS_TOLERANCE, PSI_MAJOR_THRESHOLD, PSI_MODERATE_THRESHOLD  # noqa: E402
from crr.policy import DEFAULT_POLICY_PATH, load_policy  # noqa: E402

ASSUMPTIONS_AND_LIMITS = """\
- **Synthetic data only.** Every number in this card is measured on
  `crr.data.synthetic`'s structural-causal generator, not real customer data.
  The generator's exit criteria (docs/ROADMAP.md, phase 1) hold the ceiling
  deliberately low (0.77-0.78 AUC) so the model learns a realistic problem,
  but no synthetic dataset proves external validity — the numbers here
  transfer to a real deployment only after the same measurements are
  repeated on real, labelled outcomes.
- **The text-extraction Cohen's kappa exit criterion is not yet met** with
  the deterministic reference extractor (0.72 distress / 0.79 concealment,
  quadratic-weighted, vs a 0.8 target) — see docs/ROADMAP.md phase 7. The
  reference extractor is deliberately not tuned to this corpus; the AUC-lift
  and prompt-injection criteria are met and do not depend on closing this
  gap, but re-run `scripts/verify_extraction.py --extractor anthropic` with
  real credentials before treating extraction quality as validated.
- **`country_of_residence` and `age` carry a documented fairness tension.**
  Jurisdiction risk is a real, intended driver of `financial_crime_12m`
  (`BETA_CRIME` in `crr.data.synthetic`) but has no legitimate role in
  `default_12m` (`BETA_CREDIT`); age has one legitimate credit-risk term
  (`x_age_distance`) but is a protected characteristic in most fair-lending
  regimes regardless. See `crr.governance.fairness`'s module docstring and
  the fairness section below for how this is currently handled — as a
  measured, target-specific finding, not a resolved question.
- **Human decisions were never used as a training label.** Retraining
  (`scripts/retrain.py`) fits exclusively on observed outcomes; underwriter
  decisions are used only to measure human-model disagreement and to
  quantify what a naively decision-trained model would have reproduced (see
  the bias-reproduction section below, where present).
"""


def _load_json(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _fmt_pct(value: float) -> str:
    return f"{value:.2%}"


def render(target: str, model_dir: Path, data_dir: Path, policy_path: Path) -> str:
    metadata = _load_json(model_dir / "metadata.json") or {}
    metrics = _load_json(model_dir / "metrics.json") or {}
    manifest = _load_json(data_dir / "manifest.json") or {}
    governance = _load_json(model_dir / "governance_report.json")
    policy = load_policy(policy_path)

    test = metrics.get("test", {})
    validation = metrics.get("validation", {})
    lines: list[str] = []
    add = lines.append

    add(f"# Model card — {target}")
    add("")
    add(f"Generated from `{model_dir}` by `scripts/generate_model_card.py`. Regenerate after every "
        "retrain; do not hand-edit the numbers below, edit the run that produced them.")
    add("")

    add("## Identity")
    add("")
    add(f"- Target: `{target}`")
    add(f"- Trained at: {metadata.get('trained_at_utc', 'unknown')}")
    add(f"- Model: LightGBM {metadata.get('lightgbm_version', 'unknown')}, "
        f"{metadata.get('calibration', 'unknown')} calibration, best iteration {metadata.get('best_iteration', 'unknown')}")
    add(f"- Feature pipeline version: {metadata.get('pipeline_version', 'unknown')}  "
        f"(contract version {metadata.get('contract_version', 'unknown')}, {metadata.get('n_features', '?')} features)")
    add(f"- Trained on: {metadata.get('trained_on', 'outcomes (crr.models baseline path)')}")
    add("")

    add("## Data lineage")
    add("")
    add(f"- Dataset seed: {manifest.get('config', {}).get('seed', 'unknown')}")
    add(f"- Dataset config hash: `{manifest.get('config_hash', 'unknown')}`")
    add(f"- Dataset schema version: {manifest.get('schema_version', 'unknown')}")
    add(f"- Generated at: {manifest.get('generated_at_utc', manifest.get('generated_at', 'unknown'))}")
    hash_matches = metadata.get("data_config_hash") == manifest.get("config_hash")
    hash_note = "(matches dataset)" if hash_matches else (
        f"(** does not match the dataset currently at {data_dir} — retrain before trusting this card **)"
    )
    add(f"- Model artefact's recorded data hash: `{metadata.get('data_config_hash', 'unknown')}` {hash_note}")
    counts = manifest.get("row_counts", {})
    if counts:
        add(f"- Row counts: {', '.join(f'{k}={v:,}' for k, v in counts.items())}")
    add("")

    add("## Performance (out-of-time test split)")
    add("")
    add(f"- AUC: {test.get('auc', float('nan')):.4f}  (Gini {test.get('gini', float('nan')):.4f}, "
        f"KS {test.get('ks', float('nan')):.4f}, PR-AUC {test.get('pr_auc', float('nan')):.4f})")
    add(f"- Calibration: ECE {test.get('expected_calibration_error', float('nan')):.4f}, "
        f"{test.get('calibration_bins_within_2se', '?')}/{test.get('calibration_bins', '?')} bins within 2 SE")
    if test.get("n"):
        add(f"- n = {test['n']:,}, prevalence {_fmt_pct(test.get('prevalence', 0.0))}")
    else:
        add("- test metrics unavailable")
    gap = test.get("auc", float("nan")) - validation.get("auc", float("nan"))
    add(f"- Validation -> test AUC gap: {gap:+.4f} (phase 2's overfit criterion; see docs/ROADMAP.md)")
    add("")

    add("## Fairness")
    add("")
    add(f"Four-fifths rule (disparate-impact and equal-opportunity ratio >= {FAIRNESS_TOLERANCE}), "
        "measured on age bucket, AML jurisdiction tier, and residency status — see `crr.governance.fairness`.")
    add("")
    if governance and governance.get("fairness"):
        for result in governance["fairness"]:
            tag = "EXEMPT" if result["exempt_reason"] else ("PASS" if result["passes"] else "FAIL")
            add(f"- **[{tag}]** {result['attribute']}"
                + (f" — {result['exempt_reason']}" if result["exempt_reason"] else ""))
    else:
        add("- Not yet measured for this artefact — run `scripts/retrain.py --target "
            f"{target} --no-save` to produce a governance report, then regenerate this card.")
    add("")

    add("## Drift monitoring baseline")
    add("")
    add(f"PSI thresholds: >= {PSI_MODERATE_THRESHOLD} moderate, >= {PSI_MAJOR_THRESHOLD} major "
        "(train+validation population vs out-of-time test population). Calibration drift: current ECE "
        "vs the validation-time baseline, same 0.02 tolerance as the phase 2 exit criterion.")
    add("")
    if governance and governance.get("drift"):
        drift = governance["drift"]
        major = [row for row in drift["psi"] if row["severity"] == "major"]
        moderate = [row for row in drift["psi"] if row["severity"] == "moderate"]
        add(f"- Feature PSI: {len(major)} major, {len(moderate)} moderate, "
            f"{len(drift['psi']) - len(major) - len(moderate)} stable (of {len(drift['psi'])} features)")
        cal = drift["calibration"]
        add(f"- Calibration drift: baseline ECE {cal['baseline_ece']:.4f}, current ECE {cal['current_ece']:.4f}, "
            f"{'DRIFTED' if cal['drifted'] else 'stable'}")
    else:
        add(f"- Not yet measured for this artefact — run `scripts/retrain.py --target {target} --no-save`.")
    add("")

    if governance and governance.get("bias_reproduction"):
        add("## Bias-reproduction check")
        add("")
        add("Would training on the human decision instead of the true outcome have reproduced the "
            "generator's deliberate underwriter bias (leniency toward private banking / corporate, with "
            "no legitimate causal basis)? Positive `reproduced_bias` for private_banking/corporate means yes.")
        add("")
        add("| segment | n | outcome-trained gap | decision-trained gap | reproduced bias |")
        add("|---|---:|---:|---:|---:|")
        for row in governance["bias_reproduction"]:
            add(f"| {row['segment']} | {row['n']:,} | {row['outcome_trained_gap']:+.3f} | "
                f"{row['decision_trained_gap']:+.3f} | {row['reproduced_bias']:+.3f} |")
        add("")

    add("## Monitoring plan")
    add("")
    add(f"- Retrain cadence: {policy.feedback.get('retrain_schedule', 'unspecified')}")
    min_cases = policy.feedback.get("min_labelled_cases_before_retrain")
    min_cases_text = f"{min_cases:,}" if isinstance(min_cases, int) else "unspecified"
    add(f"- Minimum labelled cases before retrain: {min_cases_text}")
    add("- Promotion gate: challenger must beat the champion by >= "
        f"{policy.feedback.get('promotion_min_auc_gain', 'unspecified')} out-of-time AUC, with no "
        "non-exempt fairness failure")
    add(f"- Human approval required before promotion: {policy.feedback.get('require_human_approval', 'unspecified')}")
    add("- Drift alerting: any feature PSI >= 0.25, or calibration drift past the 0.02 ECE tolerance, "
        "should trigger an off-cycle review even before the scheduled retrain date")
    add("")

    add("## Assumptions and limitations")
    add("")
    add(ASSUMPTIONS_AND_LIMITS)

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=("default_12m", "financial_crime_12m"), default="default_12m")
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--data", type=Path, default=REPO_ROOT / "data" / "raw")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    args = parser.parse_args(argv)

    model_dir = args.model_dir or (REPO_ROOT / "models" / args.target)
    if not (model_dir / "metadata.json").exists():
        print(f"no trained artefact at {model_dir} — train one first (scripts/train_baseline.py "
              "or scripts/retrain.py).", file=sys.stderr)
        return 1

    card = render(args.target, model_dir, args.data, args.policy)
    out_path = model_dir / "MODEL_CARD.md"
    out_path.write_text(card, encoding="utf-8")
    print(f"model card written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
