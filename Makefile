PYTHON ?= python3.12
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python

.PHONY: help setup run test check docs-check connector-check clean

help:
	@printf '%s\n' \
		'Cortex contributor commands:' \
		'  make setup           Create .venv with Python 3.12 and install test dependencies' \
		'  make run             Start the FastAPI development server on 127.0.0.1:8766' \
		'  make test            Run the backend test suite' \
		'  make check           Run the fast local pre-PR checks' \
		'  make connector-check Validate all 13 connector contracts' \
		'  make clean           Remove local Python caches (keeps .venv)'

setup:
	PYTHON_BIN="$(PYTHON)" ./scripts/bootstrap_dev.sh

run:
	@test -x "$(VENV_PYTHON)" || { echo "Missing $(VENV_PYTHON). Run 'make setup' first." >&2; exit 1; }
	CORTEX_PYTHON="$(abspath $(VENV_PYTHON))" ./scripts/dev_backend.sh

test:
	@test -x "$(VENV_PYTHON)" || { echo "Missing $(VENV_PYTHON). Run 'make setup' first." >&2; exit 1; }
	"$(VENV_PYTHON)" -m pytest backend/tests -q

docs-check:
	@test -x "$(VENV_PYTHON)" || { echo "Missing $(VENV_PYTHON). Run 'make setup' first." >&2; exit 1; }
	"$(VENV_PYTHON)" scripts/check_docs_current.py
	"$(VENV_PYTHON)" scripts/check_distribution_site.py
	"$(VENV_PYTHON)" scripts/validate_update_manifest.py --allow-remote-artifacts site/downloads/latest.json

connector-check:
	@test -x "$(VENV_PYTHON)" || { echo "Missing $(VENV_PYTHON). Run 'make setup' first." >&2; exit 1; }
	"$(VENV_PYTHON)" scripts/check_connector_baseline.py

check: docs-check
	"$(VENV_PYTHON)" scripts/retrieval_eval.py
	"$(VENV_PYTHON)" scripts/adaptation_eval.py
	"$(VENV_PYTHON)" -m pytest sdk/python/tests -q

clean:
	find backend scripts sdk -type d -name __pycache__ -prune -exec rm -r {} +
	find backend scripts sdk -type f -name '*.pyc' -delete
