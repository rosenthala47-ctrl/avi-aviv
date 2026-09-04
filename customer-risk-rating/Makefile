.PHONY: help install data data-large validate train explain serve ui app benchmark rules simulate-policy rescoring extraction retrain model-card governance test lint clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## install the package plus dev, model and frontend extras
	pip install -e ".[dev,model,frontend]"

data:  ## generate the default 10k-customer alpha dataset
	python scripts/generate_synthetic_data.py -n 10000 --language mixed

data-large:  ## generate a 50k-customer dataset in csv + parquet
	python scripts/generate_synthetic_data.py -n 50000 --language mixed --format csv --format parquet

validate:  ## check the generated dataset carries learnable signal
	python scripts/validate_dataset.py --data data/raw
	python scripts/validate_dataset.py --data data/raw --target financial_crime_12m

train:  ## train and evaluate the calibrated baseline on both targets, with ablation
	python scripts/train_baseline.py --ablation
	python scripts/train_baseline.py --target financial_crime_12m --ablation

explain:  ## validate SHAP explanations and check them against ground truth
	python scripts/explain_model.py
	python scripts/explain_model.py --target financial_crime_12m

serve:  ## run the scoring API (in-memory backends, no infra needed)
	python scripts/serve.py

ui:  ## run the Streamlit front end (needs the API up: make serve, in another shell)
	streamlit run app.py

app:  ## run API + Streamlit together, exactly as the container does
	./scripts/start_all.sh

benchmark:  ## measure single-score latency against the p99 < 150ms target
	python scripts/benchmark_api.py --requests 1000

rules:  ## verify the phase 5 rule-engine exit criteria (hot reload, audit, rollback, raise-only)
	python scripts/verify_rule_engine.py

simulate-policy:  ## simulate the example proposed policy against fresh demo traffic
	python scripts/simulate_policy.py --proposed config/risk_policy_proposed.example.yaml

rescoring:  ## verify the phase 6 re-scoring exit criteria (trigger-to-score p95, false-alert rate)
	python scripts/verify_rescoring.py

extraction:  ## verify the phase 7 LLM-extraction exit criteria (AUC lift, kappa, prompt injection)
	python scripts/verify_extraction.py

retrain:  ## retrain on outcomes, evaluate fairness/drift and the promotion gate (never auto-promotes)
	python scripts/retrain.py --target default_12m
	python scripts/retrain.py --target financial_crime_12m

model-card:  ## regenerate the documentation pack for both trained models
	python scripts/generate_model_card.py --target default_12m
	python scripts/generate_model_card.py --target financial_crime_12m

governance:  ## verify the phase 8 exit criteria (promotion gate, fairness coverage, doc pack)
	python scripts/verify_governance.py

test:  ## run the test suite
	pytest

lint:  ## ruff + mypy
	ruff check src scripts tests app.py
	mypy src

clean:  ## remove generated data, models and caches
	rm -rf data/raw/* data/interim/* data/processed/* models/*
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
