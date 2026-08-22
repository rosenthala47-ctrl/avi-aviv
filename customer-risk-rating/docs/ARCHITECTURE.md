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

**Point-in-time correctness is enforced, not encouraged.** Every feature carries
the timestamp of the data behind it. A feature dated after the snapshot fails the
build. This is the single most common way credit models get accidentally
excellent in development and useless in production.

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
