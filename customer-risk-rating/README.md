# Customer Risk Rating (CRR)

A hybrid ML + LLM customer risk rating engine: a gradient-boosted model over
structured financial data, an LLM branch over unstructured text (support calls,
underwriter notes, KYC documents), SHAP explanations, and a policy layer a risk
manager can retune without a deploy.

**Status: phases 1-5 of 8 complete.** The synthetic data foundation, the
point-in-time feature pipeline, a calibrated LightGBM baseline, a SHAP
explainability layer, a FastAPI serving layer with the three endpoints,
persistence and audit, and a safe, versioned, no-code rule engine are built,
tested and measured. See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the full plan
and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the system design.

## Quickstart

```bash
pip install -e ".[dev,model]"

# generate a 10k-customer training set (bilingual narratives)
python scripts/generate_synthetic_data.py -n 10000 --language mixed

# prove the data carries learnable signal before training anything on it
python scripts/validate_dataset.py --data data/raw

# train the calibrated baseline, with a per-feature-block ablation
python scripts/train_baseline.py --ablation

# validate SHAP additivity, map to reason codes, check against ground truth
python scripts/explain_model.py

# run the API (in-memory backends, no infra needed) and measure latency
python scripts/serve.py &
python scripts/benchmark_api.py --requests 500

# verify the rule engine's exit criteria, then try a proposed policy change
python scripts/verify_rule_engine.py
python scripts/simulate_policy.py --proposed config/risk_policy_proposed.example.yaml

pytest
```

`make help` lists the shortcuts.

## What phase 1 produced

A synthetic data generator built as an explicit **structural causal model**:
latent risk factors → observable features → outcomes, through a documented
log-odds equation. That ordering matters. A generator that draws random features
and a random label produces data on which no model can beat AUC 0.5, and training
on it teaches you nothing.

Five joined frames per run:

| file | one row per | purpose |
|---|---|---|
| `customers.csv` | customer | 77 model input columns — profile, financial, behavioural, AML/KYC |
| `narratives.csv` | customer | support call summary, underwriter note, KYC extract (EN + HE) |
| `events.csv` | event | trailing transaction log, drives real-time re-scoring |
| `outcomes.csv` | customer | labels after the performance window + the human decision |
| `ground_truth.csv` | customer | generator internals — **evaluation only, never a feature** |

Plus `manifest.json`: seed, config hash, library versions, realised prevalence and
a SHA-256 per file, so a trained model is traceable back to its exact training data.

### Properties that were designed in on purpose

- **Two independent risk dimensions.** Credit default and financial crime run off
  near-uncorrelated latent factors, because a wealthy, perfectly-performing
  customer can still be an AML risk.
- **Signal that lives only in the text.** 70% of the narrative distress signal is
  independent of the tabular block. This is what the LLM branch has to earn.
- **Non-linearity that a scorecard cannot capture** — products, absolute
  differences, occupation-conditional slopes, and the credit seasoning curve
  (hazard peaks 12-18 months in, then falls).
- **Realistic defects** — MCAR, MAR *and* MNAR missingness, dirty categoricals,
  near-duplicate records, fat tails.
- **Out-of-time splits.** The most recent cohorts are held out. Random row splits
  leak the macro cycle across folds and flatter the model.
- **Deliberately biased human decisions.** Underwriters are modelled as lenient on
  private banking and harsh on high-risk jurisdictions — so the phase 8 feedback
  loop has a real bias to discover, and so nobody trains on decisions by accident.

### Measured (40k customers, out-of-time test split)

| | credit default | financial crime |
|---|---|---|
| prevalence | 5.4% | 1.5% |
| logistic regression (tabular) | 0.761 AUC | 0.780 AUC |
| gradient boosting (tabular) | 0.774 AUC | 0.781 AUC |
| headroom for a perfect text reader | +0.026 | +0.050 |

0.77 AUC is where real credit models live. An earlier version of this generator
produced 0.90 — that was a bug in the generator, and `scripts/validate_dataset.py`
is what caught it.

## Layout

```
customer-risk-rating/
├── config/
│   ├── data_generation.yaml               generation profiles (smoke / alpha / adversarial)
│   ├── risk_policy.yaml                   bands, rules, triggers — the no-code control surface
│   ├── risk_policy_proposed.example.yaml  a ready-to-try proposed edit for simulate_policy.py
│   └── policy_history/                    every policy version ever loaded, immutable, archived
├── data/                        generated output (git-ignored, reproducible from seed)
├── docs/
│   ├── ROADMAP.md               eight phases with measurable exit criteria
│   ├── ARCHITECTURE.md          system design and the decisions behind it
│   └── DATA_DICTIONARY.md       generated from live data, never hand-edited
├── scripts/
│   ├── generate_synthetic_data.py
│   ├── validate_dataset.py      proves the data is fit to train on
│   ├── train_baseline.py        trains, calibrates, ablates, judges exit criteria
│   ├── explain_model.py         SHAP additivity, reason codes, ground-truth check
│   ├── serve.py                 run the API with uvicorn
│   ├── benchmark_api.py         measure scoring latency against the p99 target
│   ├── verify_rule_engine.py    measures the phase 5 exit criteria, judges PASS/FAIL
│   ├── simulate_policy.py       what would change if a proposed policy went live
│   ├── manage_policy.py         list / show / diff / rollback policy versions
│   └── render_data_dictionary.py
├── src/crr/
│   ├── data/                    ✅ phase 1 — synthetic generator, taxonomy, narratives
│   ├── features/                ✅ phase 2 — point-in-time pipeline, feature contract
│   ├── models/                  ✅ phase 2 — LightGBM core, calibration, metrics
│   ├── explain/                 ✅ phase 3 — TreeSHAP → reason codes, two audiences
│   ├── api/                     ✅ phase 4 — FastAPI, scoring service, persistence
│   ├── db/                      ✅ phase 4 — SQLAlchemy models (Postgres / SQLite)
│   ├── policy.py                ✅ phase 4/5 — loader, versioning, archive, rollback
│   ├── rules/                   ✅ phase 5 — safe expressions, engine, simulation
│   ├── security/                ◻ phase 4 — anonymisation, crypto
│   └── pipelines/               ◻ phase 6 — real-time re-scoring
└── tests/
```

## What phase 2 produced

One `FeaturePipeline` used for **both** batch training and single-customer
serving — there is no second implementation to drift. It emits 134 features
across five blocks, validated against a contract that is saved with the model.

- **Point-in-time correctness is enforced.** Event aggregates are filtered at the
  snapshot date, and the test suite injects 300 future-dated events worth
  −9,999,999 each and asserts the matrix is byte-identical.
- **Leakage is rejected by construction.** Outcome labels, generator latents and
  PII are refused by name and by pattern on every frame the pipeline builds, not
  just in tests.
- **Missingness is preserved, not imputed** — `source_of_funds_verified` goes
  missing precisely when the declared source is 'undeclared', and imputing that
  away destroys more signal than it recovers.
- **Calibrated output.** Platt scaling on the validation split: expected
  calibration error 0.0055, and 8 of 10 deciles within two standard errors.

### Measured (60k customers, out-of-time test split)

| | credit default | financial crime |
|---|---|---|
| test AUC / Gini | 0.769 / 0.538 | 0.799 / 0.598 |
| expected calibration error | 0.0055 | 0.0016 |
| top-decile lift | 4.5x | — |

### The finding worth reading

The feature blocks **do not move AUC beyond the noise floor**. 57 raw columns
score 0.7722; all 134 score 0.7710, with ±0.002 seed noise. The pipeline's value
in this phase is correctness — point-in-time safety, training/serving parity,
dirty-data handling, calibration — not accuracy. Three specific things the
ablation caught: six engineered features that were monotone transforms of a
single column and therefore invisible to a tree; LightGBM categorical splits
overfitting two 30-level columns at a cost of 0.005 AUC; and isotonic calibration
collapsing 8,217 scores into 43 steps. All three are written up in
[`docs/ROADMAP.md`](docs/ROADMAP.md).

## What phase 3 produced

TreeSHAP over the model, aggregated into **31 policy-owned reason codes** that a
regulator or an adverse-action notice can use. Not the `shap` library — LightGBM's
native `pred_contrib` is byte-identical to it (0.0e0 difference) and additive to
5e-15, so the serving path stays dependency-light.

- **Every one of the 134 features maps to a reason code.** All the delinquency
  features become "adverse repayment history"; the customer is owed a reason, not
  a coefficient.
- **Two audiences.** The internal view shows every code and the SHAP value behind
  each feature; the customer view drops the codes suppressed by
  `config/risk_policy.yaml` (PEP, prior SAR, sanctions, structuring) and never
  exposes a raw value.
- **Additivity is the guarantee.** SHAP values plus the bias reconstruct the raw
  margin to 5e-15, so every reason code is a real share of the decision, not a
  plausible story.

### The finding that matters

Because phase 1 wrote the true generative coefficients, we can check whether SHAP
importance tracks the drivers the generator actually used. The credit model
does (Spearman 0.62). **The financial-crime model does not (0.03):** it rides
`structuring_score` and all but ignores the rare regulatory flags — PEP,
sanctions, opaque ownership — that any compliance regime treats as primary. SHAP
is faithful (additivity 1e-14); the *model* is memorising one dense feature. A
compliance model that scores acceptably but explains through the wrong factors is
exactly what model-risk review exists to catch, and here the ground-truth check
caught it. Written up in [`docs/ROADMAP.md`](docs/ROADMAP.md).

## What phase 4 produced

A FastAPI service with the three endpoints, built to run with no infrastructure
(in-memory backends) and to swap to PostgreSQL + Redis by setting two env vars.

- **`POST /api/v1/score`** — composite 0-100 score, band, per-dimension
  probabilities, and the top reason factors merged from both models.
- **`POST /api/v1/batch-score`** — returns a job id; polled for status.
- **`GET /api/v1/explain/{customer_id}`** — the stored explanation, reason-code
  factors, with a customer/internal audience switch.

Design decisions that matter:

- **Missing is not zero.** Every input field is optional; a missing field becomes
  NaN and flows through the missing-value machinery. Zero-filling an absent
  `credit_utilization_ratio` would invent a perfect borrower. Malformed data is a
  422.
- **Every score is reproducible.** The append-only history stores the model
  version, policy version and input hash, so any decision recomputes bit-for-bit.
  A structured JSON audit line is emitted per score.
- **Persistence is behind an interface.** In-memory by default; the SQLAlchemy
  path is the same ORM code in production (PostgreSQL) and tests (SQLite).

### Latency: the exit criterion, and how it was met

Naive scoring was p99 234 ms — over the 150 ms budget. The fix was measurement,
not guessing: an empty-event fast path, single-pass categorical normalisation, and
— the big one — `gc.freeze()` after model load, because the p99 tail was **entirely
GC pauses, not compute**. Final, with the GC tuning the service applies at startup:

| path | p50 | p99 | target |
|---|---|---|---|
| score only | 64 ms | **91 ms** | < 150 ms ✅ |
| score + explanation | 89 ms | **107 ms** | < 150 ms ✅ |

The honest caveat: p99 < 150 ms is latency; 100 rps is throughput. A score is
~90 ms of GIL-bound CPU, so 100 rps needs ~9 worker processes, not threads. The
benchmark's throughput probe shows the GIL ceiling directly. Written up in
[`docs/ROADMAP.md`](docs/ROADMAP.md).

## What phase 5 produced

A rule engine that lets a risk manager change what the system does by editing
`config/risk_policy.yaml` — no code, no deploy — without that editability
becoming a way to silently weaken a control.

- **A safe expression evaluator**, not `eval()`. The policy file is explicitly
  designed for a non-engineer to edit, which makes its `when` clauses an
  untrusted-enough input; the evaluator parses with `ast` and walks a
  whitelist (booleans, comparisons, `in`, literals, names — nothing else), so
  every one of 24 tested injection attempts (`__import__`, attribute access,
  comprehensions, `exec`, …) is rejected at policy-load time, before a bad
  rule ever reaches a scoring request.
- **Raise-only, structurally.** A fired rule's floor combines with the model's
  band via `max(...)` on ordinal rank. A test tries to build a rule that
  lowers a band — an always-firing `floor_band: Low` rule against a model band
  of `Extreme` — and confirms it cannot.
- **Immutable, archived, rollback-able versions.** Reusing a version number for
  different content is a load error, checked against a durable on-disk
  archive (not only in-memory state, so the guarantee survives a restart).
  `rollback_to(N)` restores the exact bytes that ran under version N.
- **Fails safe.** A broken edit degrades to "serve the last known-good policy,
  log loudly" rather than 500ing every request.
- **Simulation before go-live**: `scripts/simulate_policy.py` replays stored
  probabilities against a proposed policy — no ML re-inference needed, since
  everything downstream of the model's two probabilities is a pure function
  of policy content — and reports exactly which customers would change band
  or review status.

### Exit criteria (measured by `scripts/verify_rule_engine.py`)

| criterion | measured |
|---|---|
| policy change takes effect within 60s, no deploy | **9.4 ms** |
| fully audit-logged | `policy.changed` JSON event on every version change |
| rollback to any prior version | exact byte-for-byte restore, verified |
| rules can only raise risk | tested against all 4 bands; none lowered |

Two real bugs surfaced along the way, both fixed: a Python default-argument
late-binding gotcha that silently defeated archive isolation in tests (and
would have made `DEFAULT_ARCHIVE_DIR` unoverridable anywhere), and a
`datetime.date` inside a JSON column that would have failed every score save
against the real SQLAlchemy backend — invisible against the in-memory
repository, caught only because the SQLite-backed path is tested too. Both
are written up in [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Two things to know before phase 7

**A bag-of-words model already captures nearly all the text lift on this corpus**
(+0.025 of a +0.026 headroom). That is a property of template-generated
narratives, whose vocabulary is finite — not evidence that the LLM branch is
unnecessary. Real notes are paraphrased, misspelled and multilingual, which is
where bag-of-words collapses. But it does mean the LLM branch cannot be justified
on synthetic data alone; validate it on real held-out notes before scaling spend.

**`country_of_residence` is both a legitimate AML factor and a proxy for national
origin.** That tension is real and needs a documented decision, not a silent one.
It is on the phase 8 checklist for a reason.

## Data provenance

All data is synthetic and generated locally — no external API, no real customer
data, nothing leaves the machine. The jurisdiction, occupation and sanctions
taxonomies in `src/crr/data/taxonomy.py` are illustrative structure for the
alpha, **not** a compliance source; production must replace each with a live,
versioned feed (FATF, OFAC/EU/UN, a licensed PEP and adverse-media vendor).
