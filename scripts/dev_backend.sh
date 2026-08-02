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

if FREE_PORT="$("$PYTHON_BIN" - "$CORTEX_PORT" <<'PY'
import socket
import sys

try:
    requested = int(sys.argv[1])
    if not 1 <= requested <= 65535:
        raise ValueError
except ValueError:
    print(f"CORTEX_PORT must be an integer from 1 to 65535; received {sys.argv[1]!r}.", file=sys.stderr)
    raise SystemExit(2)

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    try:
        probe.bind(("127.0.0.1", requested))
    except OSError:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as fallback:
            fallback.bind(("127.0.0.1", 0))
            print(fallback.getsockname()[1])
        raise SystemExit(1)
PY
)"; then
  :
else
  STATUS=$?
  if [[ "$STATUS" -eq 1 ]]; then
    cat >&2 <<EOF
Port $CORTEX_PORT is already in use on 127.0.0.1.

The installed Cortex app may already own this port. For an isolated development server, run:
  CORTEX_PORT=$FREE_PORT make run

Point examples and SDKs at the same server:
  export CORTEX_BASE_URL=http://127.0.0.1:$FREE_PORT
EOF
  fi
  exit "$STATUS"
fi

"$PYTHON_BIN" -m uvicorn app.main:app --reload --host 127.0.0.1 --port "$CORTEX_PORT"
