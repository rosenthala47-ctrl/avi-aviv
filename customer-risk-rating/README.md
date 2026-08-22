# Customer Risk Rating (CRR)

A hybrid ML + LLM customer risk rating engine: a gradient-boosted model over
structured financial data, an LLM branch over unstructured text (support calls,
underwriter notes, KYC documents), SHAP explanations, and a policy layer a risk
manager can retune without a deploy.

**Status: phase 1 of 8 complete.** The synthetic data foundation is built,
tested and validated. See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the full plan
and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the system design.

## Quickstart

```bash
pip install -e ".[dev,model]"

# generate a 10k-customer training set (bilingual narratives)
python scripts/generate_synthetic_data.py -n 10000 --language mixed

# prove the data carries learnable signal before training anything on it
python scripts/validate_dataset.py --data data/raw

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
│   ├── data_generation.yaml     generation profiles (smoke / alpha / adversarial)
│   └── risk_policy.yaml         bands, rules, triggers — the no-code control surface
├── data/                        generated output (git-ignored, reproducible from seed)
├── docs/
│   ├── ROADMAP.md               eight phases with measurable exit criteria
│   ├── ARCHITECTURE.md          system design and the decisions behind it
│   └── DATA_DICTIONARY.md       generated from live data, never hand-edited
├── scripts/
│   ├── generate_synthetic_data.py
│   ├── validate_dataset.py      proves the data is fit to train on
│   └── render_data_dictionary.py
├── src/crr/
│   ├── data/                    ✅ phase 1 — synthetic generator, taxonomy, narratives
│   ├── features/                ◻ phase 2 — point-in-time feature pipeline
│   ├── models/                  ◻ phase 2 — LightGBM core
│   ├── explain/                 ◻ phase 3 — SHAP → reason codes
│   ├── api/                     ◻ phase 4 — FastAPI service
│   ├── db/                      ◻ phase 4 — PostgreSQL + Redis
│   ├── rules/                   ◻ phase 5 — policy-driven rule engine
│   ├── security/                ◻ phase 4 — anonymisation, crypto
│   └── pipelines/               ◻ phase 6 — real-time re-scoring
└── tests/
```

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
