#!/bin/bash
# Cortex on a Mac mini — one-command host setup (macOS, no Linux VM needed).
# Runs the hosted backend + worker as launchd services and prints your secrets.
# Public HTTPS ingress is Cloudflare Tunnel (see deploy/macmini/README.md) — this
# script only stands up the local services.
#
#   bash deploy/macmini/setup.sh
# Env overrides: CORTEX_DATA_DIR (default ~/CortexServer), CORTEX_PORT (8766),
#   CORTEX_PUBLIC_HOST (default api.trydoppl.com), CORTEX_SETUP_DRY_RUN=1 (write
#   files but don't launchctl load — for inspection/testing).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="$(command -v python3)"
DATA_DIR="${CORTEX_DATA_DIR:-$HOME/CortexServer}"
PORT="${CORTEX_PORT:-8766}"
PUBLIC_HOST="${CORTEX_PUBLIC_HOST:-api.trydoppl.com}"
ENV_FILE="$DATA_DIR/cortex.env"
KEK_FILE="$DATA_DIR/kek"
LOG_DIR="$DATA_DIR/logs"
AGENTS_DIR="$HOME/Library/LaunchAgents"
API_LABEL="com.cortex.api"
WORKER_LABEL="com.cortex.worker"

echo "=============================================================="
echo " Cortex Mac mini setup"
echo "   repo:   $REPO_DIR"
echo "   python: $PYTHON"
echo "   data:   $DATA_DIR"
echo "   host:   https://$PUBLIC_HOST  (via Cloudflare Tunnel -> 127.0.0.1:$PORT)"
echo "=============================================================="

mkdir -p "$DATA_DIR/shards" "$DATA_DIR/control" "$LOG_DIR" "$AGENTS_DIR"

# --- secrets, generated ONCE ---
if [ ! -f "$ENV_FILE" ]; then
  ADMIN_TOKEN="cxop_$(openssl rand -hex 32)"
  SIGNING_KEY="$(openssl rand -hex 32)"
  "$PYTHON" - <<'PY' > "$KEK_FILE"
import base64, os
print(base64.b64encode(os.urandom(32)).decode())
PY
  chmod 600 "$KEK_FILE"
  cat > "$ENV_FILE" <<EOF
# Cortex Mac mini host config (generated $(date -u +%Y-%m-%dT%H:%M:%SZ)).
CORTEX_PUBLIC_BASE_URL=https://$PUBLIC_HOST
CORTEX_PUBLIC_APP_URL=https://$PUBLIC_HOST
CORTEX_API_KEY=$ADMIN_TOKEN
CORTEX_PORT=$PORT
CORTEX_UVICORN_WORKERS=2

# multi-tenant runtime (sanctioned sharded-SQLite tier)
CORTEX_SHARD_MODE=bucket
CORTEX_SHARD_COUNT=16
CORTEX_SHARD_ROOT=$DATA_DIR/shards
CORTEX_DB_PATH=$DATA_DIR/control/index.sqlite
CORTEX_VAULT_PATH=$DATA_DIR/control/vault
CORTEX_ACCOUNTS_DB_PATH=$DATA_DIR/control/accounts.sqlite
CORTEX_REQUIRE_SCOPED_API_TOKENS=1
CORTEX_HOSTED_RUNTIME_TIER=sharded_sqlite
CORTEX_WORKER_MODE=external
CORTEX_SYNC_SIGNING_KEY=$SIGNING_KEY

# accounts (public-safe: configure SMTP before accepting signups)
CORTEX_AUTH_ENABLED=1
CORTEX_AUTH_AUTOVERIFY=0
CORTEX_AUTH_EMAIL_MODE=smtp

# BETA ergonomics: captures are immediately retrievable (skip the Review inbox) so a new
# user's first capture -> ask returns a cited answer without a manual approval step.
# Connected-source captures still honour their per-source review policy. Set 0 to require
# review of every capture.
CORTEX_AUTO_APPROVE_CAPTURES=1

# per-user encryption
CORTEX_KEK_FILE=$KEK_FILE

# fair use (non-zero on purpose)
CORTEX_RATE_LIMIT_PER_MINUTE=120
CORTEX_DEFAULT_MEMORY_QUOTA=20000

# on-device embeddings (free, private)
CORTEX_EMBEDDING_PROVIDER=model2vec
CORTEX_OBSERVABILITY_ENABLED=1
PYTHONDONTWRITEBYTECODE=1
EOF
  chmod 600 "$ENV_FILE"
  FIRST=1
else
  echo "==> $ENV_FILE exists; keeping current secrets."
  FIRST=0
fi

# Reconcile flags added AFTER the initial install so `git pull && setup.sh` actually applies
# them to an already-provisioned cortex.env. Idempotent; never overwrites an existing value.
ensure_env() {
  if ! grep -q "^$1=" "$ENV_FILE"; then
    printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"
    echo "   added $1=$2 to $ENV_FILE"
  fi
}
ensure_env CORTEX_AUTO_APPROVE_CAPTURES 1

chmod +x "$REPO_DIR/deploy/macmini/run-api.sh" "$REPO_DIR/deploy/macmini/run-worker.sh"

# --- Python deps ---
echo "==> Installing backend dependencies into $PYTHON"
"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet -r "$REPO_DIR/backend/requirements.txt" -r "$REPO_DIR/backend/runtime-requirements.txt" uvicorn || {
  echo "!! pip install failed; check $PYTHON"; exit 1; }

# --- launchd plists ---
PATH_ENV="$(dirname "$PYTHON"):/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
write_plist() {
  local label="$1" wrapper="$2" out="$AGENTS_DIR/$1.plist"
  cat > "$out" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$REPO_DIR/deploy/macmini/$wrapper</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CORTEX_ENV_FILE</key><string>$ENV_FILE</string>
    <key>CORTEX_REPO_DIR</key><string>$REPO_DIR</string>
    <key>CORTEX_PYTHON</key><string>$PYTHON</string>
    <key>PATH</key><string>$PATH_ENV</string>
  </dict>
  <key>WorkingDirectory</key><string>$REPO_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/$label.out.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/$label.err.log</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
EOF
  echo "   wrote $out"
  plutil -lint "$out" >/dev/null && echo "   plist valid"
}
write_plist "$API_LABEL" "run-api.sh"
write_plist "$WORKER_LABEL" "run-worker.sh"

if [ "${CORTEX_SETUP_DRY_RUN:-0}" = "1" ]; then
  echo "==> DRY RUN: plists written, not loaded."
  exit 0
fi

# --- load services ---
for label in "$API_LABEL" "$WORKER_LABEL"; do
  launchctl unload "$AGENTS_DIR/$label.plist" 2>/dev/null || true
  launchctl load -w "$AGENTS_DIR/$label.plist"
done

# --- smoke ---
sleep 3
ok=0
for _ in $(seq 1 20); do
  if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/health" \
      -H "Authorization: Bearer $(grep '^CORTEX_API_KEY=' "$ENV_FILE" | cut -d= -f2)"; then ok=1; break; fi
  sleep 1
done
[ "$ok" = "1" ] && echo "==> Local API healthy on 127.0.0.1:$PORT" || {
  echo "!! API did not come up — check $LOG_DIR/$API_LABEL.err.log"; exit 1; }

echo "=============================================================="
if [ "${FIRST:-0}" = "1" ]; then
  echo " New admin token: stored in $ENV_FILE (not printed)"
  echo " New KEK        : stored in $KEK_FILE (not printed)"
  echo " Escrow the KEK offline from an interactive, non-logged session."
fi
echo " Services : launchctl list | grep com.cortex"
echo " Logs     : $LOG_DIR/"
echo " NEXT: expose it publicly with Cloudflare Tunnel — see deploy/macmini/README.md"
echo "   Prevent sleep (dedicated mini):  sudo pmset -c sleep 0 disksleep 0 autorestart 1"
echo "=============================================================="
