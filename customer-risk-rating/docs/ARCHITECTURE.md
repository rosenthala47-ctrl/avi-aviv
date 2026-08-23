# Architecture

## System shape

```
                    ┌──────────────────────────────────────────────┐
   client systems   │            FastAPI service (crr.api)         │
   (core banking,   │                                              │
    CRM, LOS)  ────▶│  POST /api/v1/score          (sync, <150ms)  │
                    │  POST /api/v1/batch-score    (async, job id) │
                    │  GET  /api/v1/explain/{id}   (stored SHAP)   │
                    └───────┬──────────────────────────────┬───────┘
                            │                              │
                  ┌─────────▼─────────┐          ┌─────────▼─────────┐
                  │  feature pipeline │          │   rule engine     │
                  │  (crr.features)   │          │   (crr.rules)     │
                  │  point-in-time    │          │  risk_policy.yaml │
                  └─────────┬─────────┘          └─────────┬─────────┘
                            │                              │
        ┌───────────────────┴────────┐                     │
        │                            │                     │
┌───────▼────────┐         ┌─────────▼────────┐            │
│ tabular model  │         │   LLM extractor  │            │
│ LightGBM       │         │  narrative →     │            │
│ (crr.models)   │◀────────│  structured      │            │
│                │ features│  features        │            │
└───────┬────────┘         └──────────────────┘            │
        │                                                  │
        │  raw score + SHAP                                │  floor / review
        └──────────────────────┬───────────────────────────┘
                               ▼
                    ┌─────────────────────┐
                    │  composite score    │  0-100 + band + reason codes
                    │  (crr.explain)      │
                    └──────────┬──────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
┌───────────────┐    ┌──────────────────┐   ┌──────────────────┐
│  PostgreSQL   │    │      Redis       │   │  event consumer  │
│  customers    │    │  feature cache   │   │  real-time       │
│  score history│    │  policy cache    │   │  re-scoring      │
│  audit log    │    │  idempotency     │   │  (crr.pipelines) │
└───────────────┘    └──────────────────┘   └──────────────────┘
```

## Decisions worth defending

**The LLM produces features, not scores.** It would be simpler to ask a model to
read the file and return a risk rating. We do not, for three reasons: an LLM's
output is not calibrated (it cannot tell you that 63 means a 7% default rate), it
is not monotone (adding an adverse fact can lower the score), and it cannot be
explained in the way a regulator expects. Extraction → features → gradient-boosted
tree keeps calibration, monotonicity and SHAP intact, and confines a hallucination
to a single feature value instead of to the decision.

**Two risk dimensions, not one.** Credit default and financial crime are driven by
near-independent factors. A private banking client with a spotless repayment
record and an opaque ownership chain is low credit risk and high AML risk. Scoring
them as one number either buries the AML signal or fails good borrowers. We model
both and blend them only at the final, policy-controlled step.

**Rules run after the model and can only raise risk.** A sanctions match is not a
probability, it is a hard stop, and it should not be diluted by a model that has
never seen one. Constraining rules to raise-only means a policy edit can never
quietly disable a control — the loader rejects a rule that lowers risk.

**Everything is versioned onto the score.** Model version, policy version, feature
set version, input hash. In two years someone will ask why this customer was
declined; without those four fields the answer is a shrug.

**Point-in-time correctness is enforced, not encouraged.** Event aggregates are
filtered at the customer's snapshot date, and the leakage guard runs on every
frame the pipeline builds. This is the single most common way credit models get
accidentally excellent in development and useless in production.

**The model outputs a calibrated probability, not a score.** Platt scaling on the
validation split, because the number feeds pricing, provisioning and a policy
band cut-off — all of which need "7%" to mean seven percent. Isotonic was
measured and rejected: it collapsed 8,217 distinct scores into 43 flat steps,
which is unusable granularity for a 0-100 score with bands at 25/50/75.

## The feature contract

Built when the pipeline is fitted, saved beside the model, and validated on every
frame at scoring time. It carries the exact column list and order, the type and
allowed range of each feature, and the category vocabulary learned at fit time.

It refuses forbidden columns by name *and* by pattern — outcome labels, generator
latents, PII — on every frame the pipeline produces, not only under test. The
cost is a set lookup per column. The cost of missing one is a model that scores
0.99, passes review, and is worthless.

Two properties the tests hold it to:

- **Point-in-time.** Event aggregates filter at the snapshot date. Injecting 300
  future-dated events worth −9,999,999 each must leave the matrix byte-identical.
- **Parity.** Transforming one customer must produce exactly what batch scoring
  produced for that customer, across all 134 features. This is what makes "one
  code path for training and serving" a checked claim rather than an intention.

## Data flow (training)

```
generate_synthetic_data.py
   ├── customers.csv     ─┐
   ├── narratives.csv    ─┼─▶ feature pipeline ─▶ feature matrix ─▶ LightGBM
   ├── events.csv        ─┘                                            │
   ├── outcomes.csv      ────────────────── labels ────────────────────┘
   └── ground_truth.csv  ────▶ evaluation only  ⚠ never a model input
```

`ground_truth.csv` carries the generator's latent factors and true probabilities.
Joining it into a training frame produces a model that scores near-perfectly and
is worthless. `crr.data.synthetic.GROUND_TRUTH_COLUMNS` names the columns to
exclude, and `tests/test_synthetic_data.py` asserts they never appear in an input
frame.

## The serving API

Three endpoints, one scoring service. `POST /score` builds features once, scores
both models, blends them into the 0-100 composite via the policy, and returns the
band plus merged reason factors. `POST /batch-score` returns a job id and scores in
the background. `GET /explain/{id}` reads the stored explanation rather than
recomputing it — the score a customer was given and the explanation a reviewer sees
must be the same event, and a recompute could differ if the model or policy moved.

Choices that carry weight:

- **Backends are behind interfaces.** In-memory by default so the service runs with
  no infrastructure; SQLAlchemy (PostgreSQL in production, SQLite in tests) and
  Redis swap in through two env vars. The score history is append-only.
- **Missing is modelled, never zero-filled.** An absent input field is NaN, which
  the pipeline's missing-value machinery already handles; fabricating a zero would
  invent a customer attribute and score it with false confidence.
- **Latency came from measurement.** The p99 tail was GC pauses, not compute, so the
  service calls `gc.freeze()` after loading the model — the large model objects are
  permanent and do not belong in any collection pass. Real-time decisions use the
  fast score-only path (p99 91 ms); explanations add SHAP (p99 107 ms) and can also
  be served off the hot path.
- **Throughput is a process-count question.** Scoring is GIL-bound Python/pandas, so
  100 rps is reached with ~9 worker processes, not threads.

## Explanations: SHAP into reason codes

The explainer takes the exact per-feature TreeSHAP contributions and aggregates
them into 31 policy-owned reason codes. Three properties make it defensible:

- **Faithful.** SHAP values plus the bias reconstruct the model's raw margin to
  5e-15. Every reason code is a real share of the decision, and grouping features
  into a code is exact because SHAP is additive.
- **On the right scale.** SHAP explains the raw margin — the decision function.
  Calibration is a monotone rescaling applied afterward; it changes the level of
  the probability, never the order of the factors. The two are reported side by
  side, never conflated.
- **Audience-aware.** Codes suppressed by policy (PEP, prior SAR, sanctions,
  structuring) never reach a customer-facing explanation; showing them can tip off
  a subject and is often legally prohibited. The vocabulary and the policy's
  suppression list are cross-checked so they cannot silently disagree.

The `shap` library is not on the serving path: LightGBM's native `pred_contrib`
is byte-identical to it and additive to machine precision, so the endpoint depends
only on LightGBM.

## Security and privacy posture

| requirement | approach |
|---|---|
| in transit | TLS 1.3, mTLS between internal services |
| at rest | AES-256; PostgreSQL TDE or volume encryption, encrypted backups |
| PII minimisation | the model never sees name, national id, email, phone or address — they are stripped at ingest (`crr.security`), and `crr.data.dictionary.pii_columns()` is the authoritative list |
| pseudonymisation | customer keys are salted hashes; the mapping lives in a separate store with its own access control |
| audit | every score records model version, policy version, input hash, output, actor and latency |
| untrusted input | narrative text is treated as hostile — it reaches the LLM inside a data envelope, never as instructions |
| secrets | environment-injected, never in the repo; `.env` is git-ignored |

The synthetic PII in the generated data is deliberately impossible to confuse
with real data: `SYN-` prefixed identifiers with no valid checksum, the reserved
`.invalid` e-mail TLD, and phone numbers from the `+1-555-01xx` fiction range.

## Where the competitive claim actually lives

Enterprise vendors are strong on coverage and compliance paperwork and weak on
three things. Those are the places to compete:

1. **Latency of insight.** Annual or quarterly re-rating is the industry norm.
   Event-driven re-scoring in seconds is a genuine product difference (phase 6).
2. **Explanation quality.** Most vendors emit a score and a rule trace. Per-decision
   SHAP mapped to a policy-owned reason-code vocabulary is better, and it is what
   the regulator is actually asking for (phase 3).
3. **Time to retune.** Changing a weight at a large vendor is a change request and
   a release. Here it is a YAML edit with a simulation preview (phase 5).

What *not* to claim: better raw accuracy. On tabular credit data everyone is
using gradient-boosted trees on similar features and lands within a few points of
Gini of each other. Accuracy is table stakes; the differentiators above are not.
