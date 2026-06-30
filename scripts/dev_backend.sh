#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../backend"
export CORTEX_VAULT_PATH="${CORTEX_VAULT_PATH:-./data/Cortex.vault}"
export CORTEX_DB_PATH="${CORTEX_DB_PATH:-$CORTEX_VAULT_PATH/index.sqlite}"
export CORTEX_API_KEY="${CORTEX_API_KEY:-dev-local-key}"
export CORTEX_ALLOW_INSECURE_DEV_TOKEN="${CORTEX_ALLOW_INSECURE_DEV_TOKEN:-1}"
export CORTEX_PORT="${CORTEX_PORT:-8766}"
python3 -m uvicorn app.main:app --reload --host 127.0.0.1 --port "$CORTEX_PORT"
