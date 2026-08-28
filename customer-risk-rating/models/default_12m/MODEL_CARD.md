# Model card — default_12m

Generated from `/home/user/avi-aviv/customer-risk-rating/models/default_12m` by `scripts/generate_model_card.py`. Regenerate after every retrain; do not hand-edit the numbers below, edit the run that produced them.

## Identity

- Target: `default_12m`
- Trained at: 2026-08-28T07:20:01+00:00
- Model: LightGBM 4.7.0, platt calibration, best iteration 160
- Feature pipeline version: 1.1.0  (contract version 1.0.0, 147 features)
- Trained on: outcomes (crr.models baseline path)

## Data lineage

- Dataset seed: 42
- Dataset config hash: `ddcd91d68e01d83c`
- Dataset schema version: 1.0.0
- Generated at: 2026-08-28T07:19:06+00:00
- Model artefact's recorded data hash: `ddcd91d68e01d83c` (matches dataset)
- Row counts: customers=80,320, narratives=80,320, events=1,075,832, outcomes=80,320, ground_truth=80,320

## Performance (out-of-time test split)

- AUC: 0.8017  (Gini 0.6034, KS 0.4854, PR-AUC 0.2728)
- Calibration: ECE 0.0067, 8/10 bins within 2 SE
- n = 13,505, prevalence 5.69%
- Validation -> test AUC gap: +0.0100 (phase 2's overfit criterion; see docs/ROADMAP.md)

## Fairness

Four-fifths rule (disparate-impact and equal-opportunity ratio >= 0.8), measured on age bucket, AML jurisdiction tier, and residency status — see `crr.governance.fairness`.

- Not yet measured for this artefact — run `scripts/retrain.py --target default_12m --no-save` to produce a governance report, then regenerate this card.

## Drift monitoring baseline

PSI thresholds: >= 0.1 moderate, >= 0.25 major (train+validation population vs out-of-time test population). Calibration drift: current ECE vs the validation-time baseline, same 0.02 tolerance as the phase 2 exit criterion.

- Not yet measured for this artefact — run `scripts/retrain.py --target default_12m --no-save`.

## Monitoring plan

- Retrain cadence: monthly
- Minimum labelled cases before retrain: 500
- Promotion gate: challenger must beat the champion by >= 0.005 out-of-time AUC, with no non-exempt fairness failure
- Human approval required before promotion: True
- Drift alerting: any feature PSI >= 0.25, or calibration drift past the 0.02 ECE tolerance, should trigger an off-cycle review even before the scheduled retrain date

## Assumptions and limitations

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
