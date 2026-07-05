#!/bin/bash
# launchd wrapper for the Cortex background worker on macOS (embeddings, connector
# syncs, queued jobs). Sources the same env file as the API.
set -euo pipefail
ENV_FILE="${CORTEX_ENV_FILE:?CORTEX_ENV_FILE not set}"
set -a; . "$ENV_FILE"; set +a
cd "${CORTEX_REPO_DIR:?CORTEX_REPO_DIR not set}"
exec "${CORTEX_PYTHON:-python3}" scripts/run_memory_worker.py \
  --iterations 0 --interval-seconds "${CORTEX_WORKER_INTERVAL:-30}"
