#!/bin/bash
# launchd wrapper for the Cortex API on macOS. Sources the env file (secrets stay
# out of the plist) then execs uvicorn bound to loopback — Cloudflare Tunnel (or a
# reverse proxy) provides the public HTTPS ingress, so the port is never exposed.
set -euo pipefail
ENV_FILE="${CORTEX_ENV_FILE:?CORTEX_ENV_FILE not set}"
set -a; . "$ENV_FILE"; set +a
cd "${CORTEX_REPO_DIR:?CORTEX_REPO_DIR not set}"
exec "${CORTEX_PYTHON:-python3}" -m uvicorn backend.app.main:app \
  --host 127.0.0.1 \
  --port "${CORTEX_PORT:-8766}" \
  --workers "${CORTEX_UVICORN_WORKERS:-2}" \
  --proxy-headers --forwarded-allow-ips=127.0.0.1
