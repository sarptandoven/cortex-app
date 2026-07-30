#!/usr/bin/env bash
# Nightly Cortex backup: WAL-safe `.backup` of every SQLite database (raw copies of live
# WAL files are NOT consistent), plus vault/attachment trees. Keeps 7 nights locally.
# Archives are encrypted to an age recipient; the private identity and Cortex KEK are
# deliberately separate from the backup data.
#
# Offsite (recommended in week one): set BACKUP_RCLONE_REMOTE in /etc/cortex/cortex.env
# (e.g. "r2:cortex-backups") after `rclone config`; each archive is then copied off-box.
set -euo pipefail
umask 077

SHARD_ROOT="${CORTEX_SHARD_ROOT:-/var/lib/cortex/shards}"
DEST_ROOT="/var/lib/cortex/backups"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
STAGING="$DEST_ROOT/staging-$STAMP"
ARCHIVE="$DEST_ROOT/cortex-$STAMP.tar.gz.age"

if [ -z "${BACKUP_AGE_RECIPIENT:-}" ]; then
  echo "BACKUP_AGE_RECIPIENT is required; refusing to create a plaintext backup" >&2
  exit 1
fi
if ! command -v age >/dev/null; then
  echo "age is required to encrypt Cortex backups" >&2
  exit 1
fi

install -d -m 0700 "$DEST_ROOT" "$STAGING"
trap 'rm -rf "$STAGING"' EXIT

# 1. Consistent snapshots of every SQLite DB under the shard root (shards, control
#    registry, token index, accounts, keyring metadata) — .backup handles live WAL.
while IFS= read -r -d '' db; do
  rel="${db#"$SHARD_ROOT"/}"
  mkdir -p "$STAGING/db/$(dirname "$rel")"
  sqlite3 "$db" ".backup '$STAGING/db/$rel'"
done < <(find "$SHARD_ROOT" -name '*.sqlite' -print0 2>/dev/null)

# 2. Vault trees (Markdown + JSON records + encrypted credential blobs). Memory content
#    is plaintext at rest, which is why the whole archive is encrypted below.
while IFS= read -r -d '' vault; do
  rel="${vault#"$SHARD_ROOT"/}"
  mkdir -p "$STAGING/vault/$(dirname "$rel")"
  cp -R "$vault" "$STAGING/vault/$rel"
done < <(find "$SHARD_ROOT" -maxdepth 3 -type d -name '*.vault' -print0 2>/dev/null)

# Never copy /etc/cortex/cortex.env: it contains operator, OAuth, signing, and SMTP
# credentials. Deployment configuration should be reconstructed from the example and
# separately escrowed secrets.
tar -cz -C "$STAGING" . | age -r "$BACKUP_AGE_RECIPIENT" -o "$ARCHIVE"
chmod 0600 "$ARCHIVE"
echo "backup written: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"

# 3. Retention: keep 7 nights locally.
ls -1t "$DEST_ROOT"/cortex-*.tar.gz.age 2>/dev/null | tail -n +8 | xargs -r rm -f

# 4. Optional offsite copy. Only the encrypted archive leaves the machine.
if [ -n "${BACKUP_RCLONE_REMOTE:-}" ] && command -v rclone >/dev/null; then
  rclone copy "$ARCHIVE" "$BACKUP_RCLONE_REMOTE/" --quiet
  echo "offsite copy: $BACKUP_RCLONE_REMOTE/"
fi

# 5. Optional dead-man ping (set BACKUP_PING_URL to a healthchecks.io check).
if [ -n "${BACKUP_PING_URL:-}" ]; then
  curl -fsS -m 10 "$BACKUP_PING_URL" >/dev/null || true
fi
