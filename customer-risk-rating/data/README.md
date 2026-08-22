# data/

Generated data is **not committed**. It is fully reproducible:

```bash
python scripts/generate_synthetic_data.py -n 50000 --seed 42
```

Every run writes `manifest.json` next to the data, recording the seed, the full
generator config, library versions, row counts, realised outcome prevalence and a
SHA-256 per file. That manifest is what makes a trained model traceable back to
its training data — quote its `config_hash` in any model documentation.

| directory    | contents                                                   |
|--------------|------------------------------------------------------------|
| `raw/`       | generator output, untouched                                 |
| `interim/`   | intermediate artefacts from the feature pipeline            |
| `processed/` | model-ready feature matrices                                |

## Files a run produces

| file                | one row per | purpose                                                    |
|---------------------|-------------|------------------------------------------------------------|
| `customers.csv`     | customer    | model inputs — what an API caller could plausibly send      |
| `narratives.csv`    | customer    | the three free-text fields for the LLM branch               |
| `events.csv`        | event       | trailing transaction log, drives real-time re-scoring       |
| `outcomes.csv`      | customer    | labels observed after the performance window + human decision |
| `ground_truth.csv`  | customer    | generator internals — **evaluation only, never a feature**  |

`ground_truth.csv` holds the latent factors, the true probabilities and the
narrative levels. Joining it into a training frame will produce a model that
looks perfect and is worthless. `crr.data.synthetic.GROUND_TRUTH_COLUMNS` lists
the columns to exclude.
