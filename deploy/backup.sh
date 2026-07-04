#!/usr/bin/env bash
# Nightly Cortex backup: WAL-safe `.backup` of every SQLite database (raw copies of live
# WAL files are NOT consistent), plus vault/attachment trees. Keeps 7 nights locally.
# The KEK is deliberately NOT included — key escrow is separate from data backups by
# design (docs/ACCOUNTS_ENCRYPTION_DESIGN.md): a stolen backup must stay unreadable.
#
# Offsite (recommended in week one): set BACKUP_RCLONE_REMOTE in /etc/cortex/cortex.env
# (e.g. "r2:cortex-backups") after `rclone config`; each archive is then copied off-box.
set -euo pipefail

SHARD_ROOT="${CORTEX_SHARD_ROOT:-/var/lib/cortex/shards}"
DEST_ROOT="/var/lib/cortex/backups"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
STAGING="$DEST_ROOT/staging-$STAMP"
ARCHIVE="$DEST_ROOT/cortex-$STAMP.tar.gz"

mkdir -p "$STAGING"
trap 'rm -rf "$STAGING"' EXIT

# 1. Consistent snapshots of every SQLite DB under the shard root (shards, control
#    registry, token index, accounts, keyring metadata) — .backup handles live WAL.
while IFS= read -r -d '' db; do
  rel="${db#"$SHARD_ROOT"/}"
  mkdir -p "$STAGING/db/$(dirname "$rel")"
  sqlite3 "$db" ".backup '$STAGING/db/$rel'"
done < <(find "$SHARD_ROOT" -name '*.sqlite' -print0 2>/dev/null)

# 2. Vault trees (Markdown + JSON records + credentials blobs — credential payloads are
#    CXE1-encrypted at rest, so this archive stays safe without the KEK).
while IFS= read -r -d '' vault; do
  rel="${vault#"$SHARD_ROOT"/}"
  mkdir -p "$STAGING/vault/$(dirname "$rel")"
  cp -R "$vault" "$STAGING/vault/$rel"
done < <(find "$SHARD_ROOT" -maxdepth 3 -type d -name '*.vault' -print0 2>/dev/null)

# 3. Config snapshot (env WITHOUT the KEK file).
mkdir -p "$STAGING/etc"
cp /etc/cortex/cortex.env "$STAGING/etc/cortex.env" 2>/dev/null || true

tar -czf "$ARCHIVE" -C "$STAGING" .
echo "backup written: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"

# 4. Retention: keep 7 nights locally.
ls -1t "$DEST_ROOT"/cortex-*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm -f

# 5. Optional offsite copy.
if [ -n "${BACKUP_RCLONE_REMOTE:-}" ] && command -v rclone >/dev/null; then
  rclone copy "$ARCHIVE" "$BACKUP_RCLONE_REMOTE/" --quiet
  echo "offsite copy: $BACKUP_RCLONE_REMOTE/"
fi

# 6. Optional dead-man ping (set BACKUP_PING_URL to a healthchecks.io check).
if [ -n "${BACKUP_PING_URL:-}" ]; then
  curl -fsS -m 10 "$BACKUP_PING_URL" >/dev/null || true
fi
