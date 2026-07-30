#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
export CORTEX_VAULT_PATH="${CORTEX_VAULT_PATH:-./data/Cortex.vault}"
export CORTEX_DB_PATH="${CORTEX_DB_PATH:-$CORTEX_VAULT_PATH/index.sqlite}"
export CORTEX_API_KEY="${CORTEX_API_KEY:-dev-local-key}"
export CORTEX_ALLOW_INSECURE_DEV_TOKEN="${CORTEX_ALLOW_INSECURE_DEV_TOKEN:-1}"
export CORTEX_PORT="${CORTEX_PORT:-8766}"

if [[ -n "${CORTEX_PYTHON:-}" ]]; then
  PYTHON_BIN="$CORTEX_PYTHON"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3.12)"
else
  echo "Cortex requires Python 3.12. Run 'make setup' first." >&2
  exit 1
fi

VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$VERSION" != "3.12" ]]; then
  echo "Cortex requires Python 3.12; '$PYTHON_BIN' reports Python $VERSION. Run 'make setup'." >&2
  exit 1
fi

"$PYTHON_BIN" -m uvicorn app.main:app --reload --host 127.0.0.1 --port "$CORTEX_PORT"
