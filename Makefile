# solardata pipeline
#
#   make setup    install dependencies
#   make build    full rebuild: ingest -> regimes -> parquet -> export -> report
#   make test     run the test suite
#   make check    lint + test
#
# `make build` takes roughly three minutes; the ingest is the slow part because
# it opens 364 XLSX files.

PYTHON ?= python
PYTEST ?= $(PYTHON) -m pytest
RUFF   ?= $(PYTHON) -m ruff
ETL    := $(PYTHON) -m etl
PQ     := $(PYTHON) scripts/parquet_manifest.py

.DEFAULT_GOAL := help
.PHONY: help setup build ingest regimes parquet export report verify test lint fmt check clean distclean query baseline

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Install Python dependencies
	$(PYTHON) -m pip install -r requirements.txt

build: ## Full rebuild of every artefact
	$(ETL) all

ingest: ## XLSX -> SQLite
	$(ETL) ingest

regimes: ## Detect unit-scale changes
	$(ETL) regimes

parquet: ## SQLite -> partitioned Parquet
	$(ETL) parquet

export: ## SQLite -> CSV rollups for the website
	$(ETL) export

report: ## Write the data-quality report
	$(ETL) report

verify: ## Fail if the build does not match data/baseline.json
	$(ETL) verify

baseline: ## Re-record the baseline (requires REASON=...)
	@test -n "$(REASON)" || { echo "usage: make baseline REASON=\"why the numbers moved\""; exit 2; }
	$(ETL) verify --update-baseline --reason "$(REASON)"

query: ## Ad-hoc SQL, e.g. make query SQL="SELECT 1"
	$(ETL) query "$(SQL)"

test: ## Run the test suite
	$(PYTEST)

lint: ## Lint
	$(RUFF) check etl tests scripts

fmt: ## Auto-format
	$(RUFF) format etl tests scripts
	$(RUFF) check --fix etl tests scripts

check: lint test ## Lint and test

clean: ## Remove generated artefacts (raw data is never touched)
	rm -rf data/processed data/exports
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache

distclean: clean ## Also remove the node frontend build
	rm -rf dist
