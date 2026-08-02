#!/usr/bin/env bash
# Release switch, run on the box by deploy/push.sh --update:
#   update.sh <new-release-dir>
# Refresh deployment contracts -> encrypted pre-deploy snapshot -> deps -> flip
# symlink -> restart -> smoke -> auto-rollback.
set -Eeuo pipefail

NEW_RELEASE="${1:?release dir required}"
PREVIOUS="$(readlink -f /srv/cortex/current || true)"
ENV_FILE=/etc/cortex/cortex.env
ADMIN_TOKEN="$(grep '^CORTEX_API_KEY=' "$ENV_FILE" | cut -d= -f2)"
if [ ! -d "$NEW_RELEASE" ]; then
  echo "release directory does not exist: $NEW_RELEASE" >&2
  exit 1
fi
if [ -z "$PREVIOUS" ] || [ ! -d "$PREVIOUS" ]; then
  echo "no current release is available for rollback; use deploy/bootstrap.sh" >&2
  exit 1
fi

UNIT_BACKUP_DIR="$(mktemp -d /run/cortex-update-units.XXXXXX)"
RUNTIME_UNITS_TOUCHED=0
SWITCHED=0

cleanup() {
  if [ -d "$UNIT_BACKUP_DIR" ]; then
    rm -r -- "$UNIT_BACKUP_DIR"
  fi
}

restore_runtime_units() {
  local unit
  for unit in cortex-api.service cortex-worker.service; do
    if [ -f "$UNIT_BACKUP_DIR/$unit" ]; then
      install -m 0644 "$UNIT_BACKUP_DIR/$unit" "/etc/systemd/system/$unit"
    else
      rm -f -- "/etc/systemd/system/$unit"
    fi
  done
  systemctl daemon-reload
}

rollback_on_error() {
  local status=$?
  trap - ERR EXIT
  set +e
  echo "!! Update failed — restoring the previous runtime contract" >&2
  if [ "$RUNTIME_UNITS_TOUCHED" = "1" ]; then
    restore_runtime_units
  fi
  if [ "$SWITCHED" = "1" ]; then
    ln -sfn "$PREVIOUS" /srv/cortex/current
  fi
  if [ "$RUNTIME_UNITS_TOUCHED" = "1" ] || [ "$SWITCHED" = "1" ]; then
    systemctl restart cortex-worker cortex-api
  fi
  cleanup
  exit "$status"
}

trap cleanup EXIT
trap rollback_on_error ERR

echo "==> Refreshing deployment services and encrypted backup runtime"
if ! command -v age >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq age
fi

# Existing installs predate encrypted backups. Create a recovery identity only
# when the operator has not already configured a recipient. An externally
# managed recipient remains authoritative and is never overwritten.
BACKUP_AGE_IDENTITY=/etc/cortex/backup-age.key
BACKUP_IDENTITY_CREATED=0
BACKUP_AGE_RECIPIENT="$(grep '^BACKUP_AGE_RECIPIENT=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true)"
if [ -z "$BACKUP_AGE_RECIPIENT" ]; then
  if [ ! -f "$BACKUP_AGE_IDENTITY" ]; then
    age-keygen -o "$BACKUP_AGE_IDENTITY"
    chown root:root "$BACKUP_AGE_IDENTITY"
    chmod 0400 "$BACKUP_AGE_IDENTITY"
    BACKUP_IDENTITY_CREATED=1
  fi
  BACKUP_AGE_RECIPIENT="$(age-keygen -y "$BACKUP_AGE_IDENTITY")"
  if grep -q '^BACKUP_AGE_RECIPIENT=' "$ENV_FILE"; then
    sed -i "s|^BACKUP_AGE_RECIPIENT=.*|BACKUP_AGE_RECIPIENT=$BACKUP_AGE_RECIPIENT|" "$ENV_FILE"
  else
    # A leading newline is harmless for normal env files and prevents a legacy
    # file without a final newline from swallowing the new variable.
    printf '\nBACKUP_AGE_RECIPIENT=%s\n' "$BACKUP_AGE_RECIPIENT" >> "$ENV_FILE"
  fi
  chown root:cortex "$ENV_FILE"
  chmod 0640 "$ENV_FILE"
fi

install -m 0644 "$NEW_RELEASE/deploy/systemd/cortex-backup.service" /etc/systemd/system/cortex-backup.service
install -m 0644 "$NEW_RELEASE/deploy/systemd/cortex-backup.timer" /etc/systemd/system/cortex-backup.timer
install -m 0755 "$NEW_RELEASE/deploy/backup.sh" /usr/local/bin/cortex-backup
systemctl daemon-reload

echo "==> Encrypted pre-deploy safety snapshot"
# Start through systemd so EnvironmentFile supplies the age recipient without
# sourcing the credential-bearing environment into this shell. A failed safety
# snapshot aborts before dependencies, schema initialization, or release switch.
systemctl start cortex-backup.service
systemctl enable --now cortex-backup.timer >/dev/null

echo "==> Installing requirements"
/srv/cortex/venv/bin/pip install --quiet --require-hashes \
  -r "$NEW_RELEASE/backend/requirements.lock"
/srv/cortex/venv/bin/pip install --quiet --require-hashes \
  -r "$NEW_RELEASE/backend/runtime-requirements.lock"

echo "==> Installing runtime service contracts"
for unit in cortex-api.service cortex-worker.service; do
  if [ -f "/etc/systemd/system/$unit" ]; then
    install -m 0644 "/etc/systemd/system/$unit" "$UNIT_BACKUP_DIR/$unit"
  fi
done
RUNTIME_UNITS_TOUCHED=1
install -m 0644 "$NEW_RELEASE/deploy/systemd/cortex-api.service" /etc/systemd/system/cortex-api.service
install -m 0644 "$NEW_RELEASE/deploy/systemd/cortex-worker.service" /etc/systemd/system/cortex-worker.service
systemctl daemon-reload

echo "==> Switching current -> $NEW_RELEASE"
ln -sfn "$NEW_RELEASE" /srv/cortex/current
SWITCHED=1
# Root executes update.sh from a release during dependency-aware rollback. The
# service account must never be able to replace that script or application code.
chown -R root:root "$NEW_RELEASE"
systemctl restart cortex-worker
systemctl restart cortex-api

echo "==> Smoke"
ok=0
for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null http://127.0.0.1:8766/health -H "Authorization: Bearer $ADMIN_TOKEN"; then
    ok=1; break
  fi
  sleep 1
done
if [ "$ok" != "1" ]; then
  echo "!! Smoke failed" >&2
  false
fi
trap - ERR
echo "==> Update OK ($(basename "$NEW_RELEASE"))."
echo "    Full rollback: bash /srv/cortex/current/deploy/update.sh $PREVIOUS"
if [ "$BACKUP_IDENTITY_CREATED" = "1" ]; then
  echo "==> A backup recovery identity was created at $BACKUP_AGE_IDENTITY."
  echo "    Escrow it separately from /etc/cortex/kek using an interactive, non-logged session."
fi

# Keep the 5 newest releases.
ls -1dt /srv/cortex/releases/* 2>/dev/null | tail -n +6 | xargs -r rm -rf
