# Architecture

## System shape

```
                    ┌──────────────────────────────────────────────┐
   client systems   │            FastAPI service (crr.api)         │
   (core banking,   │                                              │
    CRM, LOS)  ────▶│  POST /api/v1/score          (sync, <150ms)  │
                    │  POST /api/v1/batch-score    (async, job id) │
                    │  GET  /api/v1/explain/{id}   (stored SHAP)   │
                    │  POST /api/v1/events/{id}    (re-score, <5s) │
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
│  PostgreSQL   │    │      Redis       │   │ RescoringEngine  │
│  customers    │    │  feature cache   │   │  trigger match,  │
│  score history│    │  policy cache    │   │  debounce, band- │
│  event log    │    │  idempotency,    │   │  change notify   │
│  audit log    │    │  debounce TTL    │   │  (crr.pipelines) │
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
both models, blends them into the 0-100 composite via the policy, applies the rule
engine, and returns the band plus the model's reason factors and any fired rules
as two separate lists. `POST /batch-score` returns a job id and scores in the
background. `GET /explain/{id}` reads the stored explanation rather than
recomputing it — the score a customer was given and the explanation a reviewer sees
must be the same event, and a recompute could differ if the model or policy moved.

Choices that carry weight:

- **Backends are behind interfaces.** In-memory by default so the service runs with
  no infrastructure; SQLAlchemy (PostgreSQL in production, SQLite in tests) and
  Redis swap in through two env vars. The score history is append-only, and every
  record carries the exact customer payload that produced it (`customer_snapshot`),
  not just a hash of it — the difference between being able to *verify* a stored
  score and being able to *recompute* one from nothing, and what lets policy
  simulation replay history without a live upstream system.
- **Missing is modelled, never zero-filled.** An absent input field is NaN, which
  the pipeline's missing-value machinery already handles; fabricating a zero would
  invent a customer attribute and score it with false confidence.
- **Filtering by audience happens once, at read time.** SHAP factors and fired
  rules are always computed and stored in full; a customer-facing request filters
  the *response*, never what gets persisted. Baking the filter into computation
  would mean a score first shown to a customer could never later be reviewed
  internally in full — exactly the inconsistency a regulator would flag.
- **Latency came from measurement.** The p99 tail was GC pauses, not compute, so the
  service calls `gc.freeze()` after loading the model — the large model objects are
  permanent and do not belong in any collection pass. Real-time decisions use the
  fast score-only path (p99 91 ms); explanations add SHAP (p99 107 ms) and can also
  be served off the hot path. The rule engine runs on *both* paths unconditionally —
  a sanctions floor is cheap (no SHAP) and must never be something `explain=False`
  can skip.
- **Throughput is a process-count question.** Scoring is GIL-bound Python/pandas, so
  100 rps is reached with ~9 worker processes, not threads.

## The rule engine: a no-code control surface that cannot weaken itself

`config/risk_policy.yaml` is designed to be edited by a risk manager with no code
review — which makes its `when` clauses an untrusted-enough input. The evaluator
(`crr/rules/expressions.py`) parses each one with `ast` and walks a whitelist of
node types (booleans, comparisons, `in`, literals, bare names); everything else —
attribute access, calls, comprehensions, imports — is rejected at policy-load
time. There is no code path from a rule string to arbitrary execution, and no
`eval()` anywhere in the chain.

The engine evaluates rules against the **raw customer record** — the same field
names in the API's input contract and the data dictionary — never the pipeline's
internally encoded feature matrix. A risk manager writes
`source_of_funds_declared in ['undeclared', 'gift']` in the same vocabulary they
already use to read a customer file, not against categorical-encoding internals.

**Raise-only is structural, not conventional.** A fired rule's floor combines with
the model's band through `max(...)` on the band's ordinal rank. Applying zero,
one, or many rules is monotone by construction: there is no code path by which a
rule — however the policy file is edited, including by mistake — can lower a
band the model alone produced. A separate, simpler policy lever
(`review.require_for_bands`) sends High/Extreme to review from the model score
alone, with no rule needing to fire, because it compares the *computed* band,
which a `when` expression scoped to raw input cannot see.

**Versions are immutable and durable across a restart.** Reusing a version number
for different content is a load error, checked against an on-disk archive (every
version ever loaded, written once, never overwritten) — not only an in-process
cache, which would make "immutable" a claim that only held between restarts. A
broken edit degrades to serving the last known-good policy with a loud audit
event, rather than failing every subsequent scoring request.

**Policy simulation needs no model re-inference.** Everything downstream of the
model's two probabilities — the composite blend, band cut-offs, rule floors, the
review threshold — is a pure function of policy content. Replaying it against
stored probabilities and the stored customer snapshot is exact, and cheap enough
to run months of history in under a second; only a change to the model itself
would need re-inference, and that is a retrain, not a policy edit.

## Real-time re-scoring: three thresholds for three concerns

Event-driven re-scoring conflates three questions that need separate answers:
does this event get stored, does it cause a recompute, and does it interrupt a
human. `RescoringEngine` (`crr/pipelines/rescoring.py`) keeps them separate —
every event is stored regardless; a recompute happens only for a matched,
non-debounced trigger; a notification fires only when the published band
actually moves. Conflating any two produces either missed signal or alert
fatigue, and a system that pages someone twelve times an hour for twelve "Low"
results trains its own operators to ignore it.

**Recomputing without resending the profile.** A caller pushing one event does
not resend the customer's ~65-field profile. The engine rebuilds the scoring
input from the *stored snapshot* of the last score (`StoredScore.customer_snapshot`,
which phase 5 already needed for policy simulation) plus the *full event log*
— the same "aggregate from raw events, never accept pre-aggregated columns"
principle the feature pipeline uses for training, applied to the live path.

**The snapshot's own as-of date has to move.** `customer_snapshot` carries a
`snapshot_date` fixed at the time the *profile* was true, which is not when a
re-score is happening. The feature pipeline's leakage guard drops any event
dated after its `as_of` boundary as "future" — necessary for training, where
`as_of` is always genuinely "now" for that record. It is actively wrong for
re-scoring, where the event that triggered the recompute is, by definition,
dated after the stored `as_of`. `RescoringEngine` advances `snapshot_date` to
the current re-score time before handing the snapshot back to the same
pipeline code training uses; nothing else about the profile changes, since
`snapshot_date` is dropped before modelling and used for nothing but anchoring
that one boundary. Getting this wrong doesn't crash anything — the recompute
still runs and returns a plausible score — which is exactly why it survived
until a test explicitly compared the score before and after a post-snapshot
event and found no difference.

**Debounce is a real-time-only concern, deliberately.** The debounce cache
(the same TTL `KeyValueStore` behind the phase-4 idempotency and hot-score
caches) keys on wall-clock time, not on the timestamp carried by any
particular event. That is the correct choice for what debounce is actually
protecting — compute budget against a real burst of live events — and the
deliberate cost is that a *replay* of historical events at processing speed
can under-count triggers relative to how they would have landed in real time.

**Rules stay scoped to the static profile.** The rule engine (phase 5)
evaluates the raw customer record a CRM supplies, never the event-derived
aggregates this pipeline recomputes. A compliance rule reacts to a refreshed
profile, not a transaction event streamed through this endpoint — a
deliberate layering, not an oversight, and the reason a single triggered
event moves the model's score but essentially never moves a rule-forced band
floor.

## The LLM extraction branch: bounded signals, not a delegated decision

The LLM's whole job is four numbers: `distress_level`, `concealment_level`
(0-3, each with a confidence, and the two that become model features),
`stated_life_events` and `evasiveness_detected` (explainability-only — see
below). It never sees the model's output, never proposes a score or a band,
and structurally cannot: the tool schema it must answer through
(`crr.llm.extraction.ExtractionResult`, generated once and reused for both
the tool definition and the response validation) has no field that means a
decision. A hallucination degrades one bounded feature; it cannot fabricate
one.

**Why only two of the four signals are model features.** `distress_level` and
`concealment_level` are not an arbitrary product choice — they are exactly
the two latents `crr.data.synthetic`'s outcome equations weight
(`BETA_CREDIT`'s `text_distress`, `BETA_CRIME`'s `text_concealment`), and the
narrative text is quantile-binned from those same latents, nothing finer.
Recovering the level is therefore, by construction, the signal a tabular-only
model is missing. `stated_life_events` and `evasiveness_detected` are surface
realisations of the *same* draw, not an independent causal channel — they
exist for a reviewer's benefit (the same "give a reason, not just a number"
principle as the SHAP reason codes), not to move AUC, and are not expected to.

**Prompt injection: two layers, one of which does not depend on the model
behaving.** A KYC document or a call transcript is exactly the kind of field
a subject — or a compromised document — can influence, so narrative text is
untrusted input by default, the same posture this document's security table
already states. Layer one is the prompt: every narrative is wrapped in an
escaped `<customer_notes>` envelope, and the system prompt states, before the
model sees any note, that its content is data to analyse, never instructions
— an embedded attempt to instruct is itself scored as evasiveness, not
obeyed. Layer two is the schema, and it is the one that matters if layer one
ever fails against a more persuadable future model: `tool_choice` forces the
answer through `ExtractionResult`'s bounded fields, so even a fully
successful injection cannot express "set risk_band to Low" — no field means
that, every numeric field is range-checked, and the result is re-validated by
Pydantic independent of what the model claims to have done.

**Two extractors, one interface, the same pattern as every other backend in
this project.** `ReferenceExtractor` — deterministic keyword scoring, no
network — is the zero-infrastructure default and the honest floor: it is
written from general judgement about how English and Hebrew financial notes
read, not tuned against `crr.data.narratives`'s own phrase banks, because an
extractor built by matching the exact vocabulary it will be measured against
would make its own accuracy a rigged number rather than a real one.
`AnthropicExtractor` is the production swap, forcing its answer through the
same schema. `CachingExtractor` wraps either one behind a content-hash cache
keyed on the narrative text *and* the extractor's version string — a prompt
rewrite or model upgrade must invalidate old results, not silently keep
serving them — and only successful extractions are cached, so one transient
API failure retries on the next real request instead of being remembered as
a permanent "unavailable".

**Degradation is structural, not a try/except bolted on afterward.** No
narratives supplied and no extractor configured both score tabular-only
silently — neither is a failure. Only a genuine attempted-and-failed
extraction (no API key, a timeout, a response that fails schema validation)
sets `degraded=True`, propagated through `Assessment` into the stored score
and the `/score` response, so "this decision used less signal than usual" is
an auditable fact, not a silent gap.

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

## Model risk management: gates that decide eligibility, never promotion

`crr.governance` is deliberately structured so no automatic process can both
decide a challenger is good enough *and* put it into production. Those are
two different questions, answered by two different actors.

**Train on outcomes; decisions are a monitoring signal, never a label.** The
generator gives its underwriters a real bias — lenient toward private
banking and corporate relationships, harsh on jurisdiction exposure, with no
legitimate causal role in either outcome model — on purpose, because a
feedback loop that silently learns to imitate the humans it is meant to
audit is a real failure mode, not a hypothetical one. `scripts/retrain.py`
fits only on the true outcome label. `underwriter_decision` is used for
exactly two things: computing an agreement rate against the model's own
band (a monitoring number — rising disagreement over time is a signal worth
investigating, not evidence either side is wrong), and training a second,
throwaway booster on the decision label specifically to *measure* what
naive imitation would have reproduced. That second model is never saved,
never served, and exists only to turn the roadmap's warning into a number:
on this dataset it reproduces a −0.02 to −0.03 private-banking leniency gap
beyond what the outcome-trained model shows, in the same direction the
generator's bias points.

**Eligibility is automatic; promotion is not.** `crr.governance.promotion`
computes a `PromotionDecision` whose `eligible` property depends only on
measured numbers — an out-of-time AUC gain past
`policy.feedback.promotion_min_auc_gain`, and no non-exempt fairness
failure — and whose `promoted` property additionally requires
`approved_by_human=True` whenever the policy (or an active fairness
exemption) demands it. `scripts/retrain.py` always computes and saves the
eligibility verdict; it only ever copies a candidate into the live
`models/<target>/` directory when `--approve` was passed on that specific
invocation. A script that could promote itself would make the human-approval
requirement in `risk_policy.yaml` decorative.

**Fairness is measured identically everywhere and interpreted by causal
structure, not assumption.** `country_of_residence` is a real example of a
protected-attribute tension that most systems either ignore or resolve by
fiat: it is a legitimate, direct driver of `financial_crime_12m` risk
(`BETA_CRIME` contains `high_risk_jurisdiction`, `medium_risk_jurisdiction`,
`cross_border` terms) and has no legitimate role in `default_12m` at all
(`BETA_CREDIT` contains none). `crr.governance.fairness` computes the same
four-fifths-rule disparate-impact and equal-opportunity ratios on every
protected axis for every target — it does not special-case jurisdiction — and
a lookup table (`EXEMPT_DISPARITIES`) records *which* (target, axis)
combinations have a documented legitimate basis, with the reasoning inline.
An exemption changes the promotion consequence (named human sign-off
instead of an automatic block); it never changes the measurement, and it
never suppresses the number. The identical jurisdiction disparity that is
exempt on `financial_crime_12m` is an ordinary blocking failure on
`default_12m`, because there it has no such basis.

**A model card that cannot drift from what it describes.**
`scripts/generate_model_card.py` reads the trained artefact's own
`metadata.json`/`metrics.json`, the dataset's `manifest.json`, the last
`retrain.py` run's governance report and the live policy — never hand-written
numbers — so the card goes stale exactly when the files it reads change, and
regenerating it after a retrain is one command.

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

The phase-7 LLM branch's measured AUC lift is real (see `docs/ROADMAP.md`) but
is not this claim either — it is closing a gap the tabular block structurally
cannot see (signal that exists only in free text), not out-competing another
vendor's tree on the same numeric features. The competitive claim it actually
supports is explanation quality (bullet 2): a reason like "distress=2, cited
employer restructuring and an unprompted hardship enquiry" is a materially
better adverse-action explanation than a numeric feature contribution most
customers and most vendors' own rule traces cannot produce.
