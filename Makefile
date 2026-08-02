PYTHON ?= python3.12
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python

.PHONY: help doctor setup demo run run-standalone test check docs-check connector-check examples-check runtime-check lock-python clean

help:
	@printf '%s\n' \
		'Cortex contributor commands:' \
		'  make doctor          Check local prerequisites without changing anything' \
		'  make setup           Create .venv with Python 3.12 and install test dependencies' \
		'  make demo            Set up if needed, then run an isolated cited-answer demo' \
		'  make run             Start the local FastAPI dev server (suggests a free port on conflict)' \
		'  make run-standalone  Start the stdlib engine shipped inside the macOS app' \
		'  make test            Run the backend test suite' \
		'  make check           Run the fast local pre-PR checks' \
		'  make docs-check      Validate documentation, site links, and release metadata' \
		'  make connector-check Validate all 13 connector contracts' \
		'  make examples-check  Run every public Python example against an isolated server' \
		'  make runtime-check   Smoke-test the standalone runtime shipped in the macOS app' \
		'  make lock-python     Regenerate hash-locked Python dependency graphs' \
		'  make clean           Remove local Python caches (keeps .venv)'

doctor:
	@printf '%s\n' 'Cortex backend prerequisites:'
	@command -v git >/dev/null 2>&1 \
		&& printf '%s\n' '  [ok] git' \
		|| { printf '%s\n' '  [missing] git — install Xcode Command Line Tools or Git' >&2; exit 1; }
	@PYTHON_BIN="$(PYTHON)" ./scripts/bootstrap_dev.sh --check
	@printf '%s\n' '  [optional] Node.js 18+ is needed for the TypeScript SDK; Node.js 22 for plugins'
	@printf '%s\n' '  [optional] Xcode Command Line Tools are needed only for the macOS app'
	@printf '%s\n' 'Ready for: make demo'

setup:
	PYTHON_BIN="$(PYTHON)" CORTEX_VENV="$(abspath $(VENV))" ./scripts/bootstrap_dev.sh

demo:
	@if ! test -x "$(VENV_PYTHON)" \
		|| ! "$(VENV_PYTHON)" -c 'import sys, fastapi, uvicorn, cortex_client; raise SystemExit(sys.version_info < (3, 12))' >/dev/null 2>&1; then \
		printf '%s\n' 'Cortex dependencies are missing or incomplete; running make setup first...'; \
		$(MAKE) setup PYTHON="$(PYTHON)" VENV="$(VENV)"; \
	fi
	"$(VENV_PYTHON)" scripts/examples_smoke.py --quickstart

run:
	@test -x "$(VENV_PYTHON)" || { echo "Missing $(VENV_PYTHON). Run 'make setup' first." >&2; exit 1; }
	CORTEX_PYTHON="$(abspath $(VENV_PYTHON))" ./scripts/dev_backend.sh

run-standalone:
	@test -x "$(VENV_PYTHON)" || { echo "Missing $(VENV_PYTHON). Run 'make setup' first." >&2; exit 1; }
	cd backend && \
		CORTEX_API_KEY="$${CORTEX_API_KEY:-dev-local-key}" \
		CORTEX_ALLOW_INSECURE_DEV_TOKEN="$${CORTEX_ALLOW_INSECURE_DEV_TOKEN:-1}" \
		CORTEX_VAULT_PATH="$${CORTEX_VAULT_PATH:-./data/Cortex.vault}" \
		CORTEX_DB_PATH="$${CORTEX_DB_PATH:-./data/Cortex.vault/index.sqlite}" \
			"$(abspath $(VENV_PYTHON))" -m app.standalone_server \
			--host "$${CORTEX_HOST:-127.0.0.1}" \
			--port "$${CORTEX_PORT:-8766}"

test:
	@test -x "$(VENV_PYTHON)" || { echo "Missing $(VENV_PYTHON). Run 'make setup' first." >&2; exit 1; }
	"$(VENV_PYTHON)" -m pytest backend/tests -q

docs-check:
	@test -x "$(VENV_PYTHON)" || { echo "Missing $(VENV_PYTHON). Run 'make setup' first." >&2; exit 1; }
	"$(VENV_PYTHON)" scripts/check_markdown_links.py
	"$(VENV_PYTHON)" scripts/check_docs_current.py
	"$(VENV_PYTHON)" scripts/check_distribution_site.py
	"$(VENV_PYTHON)" scripts/validate_update_manifest.py --allow-remote-artifacts site/downloads/latest.json

connector-check:
	@test -x "$(VENV_PYTHON)" || { echo "Missing $(VENV_PYTHON). Run 'make setup' first." >&2; exit 1; }
	"$(VENV_PYTHON)" scripts/check_connector_baseline.py

examples-check:
	@test -x "$(VENV_PYTHON)" || { echo "Missing $(VENV_PYTHON). Run 'make setup' first." >&2; exit 1; }
	"$(VENV_PYTHON)" scripts/examples_smoke.py

runtime-check:
	@test -x "$(VENV_PYTHON)" || { echo "Missing $(VENV_PYTHON). Run 'make setup' first." >&2; exit 1; }
	"$(VENV_PYTHON)" scripts/examples_smoke.py --quickstart --runtime standalone

check: docs-check
	"$(VENV_PYTHON)" scripts/retrieval_eval.py
	"$(VENV_PYTHON)" scripts/adaptation_eval.py
	"$(VENV_PYTHON)" -m pytest sdk/python/tests -q
	"$(VENV_PYTHON)" scripts/examples_smoke.py
	"$(VENV_PYTHON)" scripts/examples_smoke.py --quickstart --runtime standalone

lock-python:
	@"$(PYTHON)" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))' \
		|| { printf '%s\n' 'make lock-python requires Python 3.12 (override PYTHON=/path/to/python3.12).' >&2; exit 1; }
	@LOCK_TMP="$$(mktemp -d "$${TMPDIR:-/tmp}/cortex-lock.XXXXXX")"; \
		trap 'rm -r -- "$$LOCK_TMP"' EXIT; \
		"$(PYTHON)" -m venv "$$LOCK_TMP/venv"; \
		"$$LOCK_TMP/venv/bin/python" -m pip install --disable-pip-version-check --quiet \
			'pip==25.3' 'pip-tools==7.5.2'; \
		CUSTOM_COMPILE_COMMAND='make lock-python' "$$LOCK_TMP/venv/bin/pip-compile" \
			--quiet --generate-hashes --resolver=backtracking --strip-extras \
			--output-file=requirements-dev.lock requirements-dev.in; \
		CUSTOM_COMPILE_COMMAND='make lock-python' "$$LOCK_TMP/venv/bin/pip-compile" \
			--quiet --generate-hashes --resolver=backtracking --strip-extras \
			--output-file=backend/requirements.lock backend/requirements.txt; \
		CUSTOM_COMPILE_COMMAND='make lock-python' "$$LOCK_TMP/venv/bin/pip-compile" \
			--quiet --generate-hashes --resolver=backtracking --strip-extras \
			--output-file=backend/runtime-requirements.lock backend/runtime-requirements.txt; \
		printf '%s\n' 'Updated contributor, hosted, and bundled-runtime Python locks'

clean:
	find backend scripts sdk -type d -name __pycache__ -prune -exec rm -r {} +
	find backend scripts sdk -type f -name '*.pyc' -delete
