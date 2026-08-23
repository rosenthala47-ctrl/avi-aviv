.PHONY: help install data data-large validate train explain serve benchmark rules simulate-policy test lint clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## install the package plus dev and model extras
	pip install -e ".[dev,model]"

data:  ## generate the default 10k-customer alpha dataset
	python scripts/generate_synthetic_data.py -n 10000 --language mixed

data-large:  ## generate a 50k-customer dataset in csv + parquet
	python scripts/generate_synthetic_data.py -n 50000 --language mixed --format csv --format parquet

validate:  ## check the generated dataset carries learnable signal
	python scripts/validate_dataset.py --data data/raw
	python scripts/validate_dataset.py --data data/raw --target financial_crime_12m

train:  ## train and evaluate the calibrated baseline on both targets
	python scripts/train_baseline.py --ablation
	python scripts/train_baseline.py --target financial_crime_12m

explain:  ## validate SHAP explanations and check them against ground truth
	python scripts/explain_model.py
	python scripts/explain_model.py --target financial_crime_12m

serve:  ## run the scoring API (in-memory backends, no infra needed)
	python scripts/serve.py

benchmark:  ## measure single-score latency against the p99 < 150ms target
	python scripts/benchmark_api.py --requests 1000

rules:  ## verify the phase 5 rule-engine exit criteria (hot reload, audit, rollback, raise-only)
	python scripts/verify_rule_engine.py

simulate-policy:  ## simulate the example proposed policy against fresh demo traffic
	python scripts/simulate_policy.py --proposed config/risk_policy_proposed.example.yaml

test:  ## run the test suite
	pytest

lint:  ## ruff + mypy
	ruff check src scripts tests
	mypy src

clean:  ## remove generated data, models and caches
	rm -rf data/raw/* data/interim/* data/processed/* models/*
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
