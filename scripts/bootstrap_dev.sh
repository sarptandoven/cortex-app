#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
VENV_PATH="${CORTEX_VENV:-$ROOT/.venv}"
CHECK_ONLY=0

if [[ "${1:-}" == "--check" ]]; then
  CHECK_ONLY=1
elif [[ $# -gt 0 ]]; then
  echo "Usage: scripts/bootstrap_dev.sh [--check]" >&2
  exit 2
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  cat >&2 <<EOF
Cortex development requires Python 3.12, but '$PYTHON_BIN' was not found.

Install Python 3.12, then rerun:
  make setup

Or point Cortex at an existing interpreter:
  make setup PYTHON=/absolute/path/to/python3.12
EOF
  exit 1
fi

VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$VERSION" != "3.12" ]]; then
  cat >&2 <<EOF
Cortex development requires Python 3.12; '$PYTHON_BIN' reports Python $VERSION.

Use:
  make setup PYTHON=/absolute/path/to/python3.12
EOF
  exit 1
fi

if [[ "$CHECK_ONLY" == "1" ]]; then
  echo "Python 3.12 check passed: $(command -v "$PYTHON_BIN")"
  exit 0
fi

"$PYTHON_BIN" -m venv "$VENV_PATH"
"$VENV_PATH/bin/python" -m pip install -r "$ROOT/requirements-dev.lock"
"$VENV_PATH/bin/python" -m pip install -e "$ROOT/sdk/python"

cat <<EOF

Cortex development environment is ready.
Virtual environment: $VENV_PATH

Next:
  make VENV="$VENV_PATH" run      # start the local FastAPI server
  make VENV="$VENV_PATH" test     # run backend tests
  make VENV="$VENV_PATH" check    # run fast pre-PR checks
EOF
