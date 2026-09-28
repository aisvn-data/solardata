# solardata pipeline
#
#   make setup    install dependencies
#   make build    full rebuild: ingest -> aggregate -> export -> report -> audit
#   make test     run the test suite
#   make check    lint + test
#
# `make build` takes about two minutes; the ingest is the slow part because it
# opens 364 XLSX files, and the audit then reads them a second time to check that
# every (station, width) resolves to a layout the catalog declares.

PYTHON ?= python
PYTEST ?= $(PYTHON) -m pytest
RUFF   ?= $(PYTHON) -m ruff
ETL    := $(PYTHON) -m etl

.DEFAULT_GOAL := help
.PHONY: help setup build ingest aggregate export report audit verify test lint fmt check clean distclean query baseline

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Install Python dependencies
	$(PYTHON) -m pip install -r requirements.txt

build: ## Full rebuild of every artefact
	$(ETL) all

ingest: ## XLSX -> eight station tables, rebuilt from scratch
	$(ETL) ingest

aggregate: ## Hourly and daily rollups, and the per-station channel measurements
	$(ETL) aggregate

export: ## public/data: one CSV set per station, plus stations.json and metrics.json
	$(ETL) export

report: ## Write the per-station data-quality report
	$(ETL) report

audit: ## Check the build against the real archive: layouts, bands, flags, exclusions
	$(ETL) audit

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
	rm -rf data/processed
	rm -rf dist
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache

distclean: clean ## Also remove the node frontend build and the exported site data
	rm -rf node_modules/.vite node_modules/.render-check
