.PHONY: help install data data-large validate train test lint clean

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

test:  ## run the test suite
	pytest

lint:  ## ruff + mypy
	ruff check src scripts tests
	mypy src

clean:  ## remove generated data, models and caches
	rm -rf data/raw/* data/interim/* data/processed/* models/*
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
