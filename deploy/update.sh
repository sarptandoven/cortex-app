#!/usr/bin/env bash
# Release switch, run on the box by deploy/push.sh --update:
#   update.sh <new-release-dir>
# Pre-deploy DB snapshot -> deps -> flip symlink -> restart -> smoke -> auto-rollback.
set -euo pipefail

NEW_RELEASE="${1:?release dir required}"
PREVIOUS="$(readlink -f /srv/cortex/current || true)"
ENV_FILE=/etc/cortex/cortex.env
ADMIN_TOKEN="$(grep '^CORTEX_API_KEY=' "$ENV_FILE" | cut -d= -f2)"

echo "==> Pre-deploy safety snapshot"
/usr/local/bin/cortex-backup || echo "!! backup failed (continuing; investigate after)"

echo "==> Installing requirements"
/srv/cortex/venv/bin/pip install --quiet -r "$NEW_RELEASE/backend/requirements.txt" -r "$NEW_RELEASE/backend/runtime-requirements.txt" uvicorn

echo "==> Switching current -> $NEW_RELEASE"
ln -sfn "$NEW_RELEASE" /srv/cortex/current
chown -R cortex:cortex "$NEW_RELEASE"
systemctl restart cortex-worker
systemctl restart cortex-api

echo "==> Smoke"
ok=0
for i in $(seq 1 30); do
  if curl -fsS -o /dev/null http://127.0.0.1:8766/health -H "Authorization: Bearer $ADMIN_TOKEN"; then
    ok=1; break
  fi
  sleep 1
done
if [ "$ok" != "1" ]; then
  echo "!! Smoke failed — rolling back to $PREVIOUS"
  if [ -n "$PREVIOUS" ] && [ -d "$PREVIOUS" ]; then
    ln -sfn "$PREVIOUS" /srv/cortex/current
    systemctl restart cortex-worker cortex-api
  fi
  exit 1
fi
echo "==> Update OK ($(basename "$NEW_RELEASE")). Rollback: ln -sfn $PREVIOUS /srv/cortex/current && systemctl restart cortex-api cortex-worker"

# Keep the 5 newest releases.
ls -1dt /srv/cortex/releases/* 2>/dev/null | tail -n +6 | xargs -r rm -rf
