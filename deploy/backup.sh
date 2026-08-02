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
STAMP="$(date -u +%Y%m%d-%H%M%S)-$$"
SERVICE_USER="${CORTEX_SERVICE_USER:-cortex}"

if [ -z "${BACKUP_AGE_RECIPIENT:-}" ]; then
  echo "BACKUP_AGE_RECIPIENT is required; refusing to create a plaintext backup" >&2
  exit 1
fi
if ! command -v age >/dev/null; then
  echo "age is required to encrypt Cortex backups" >&2
  exit 1
fi
if ! command -v flock >/dev/null; then
  echo "flock is required to coordinate Cortex backups" >&2
  exit 1
fi
if ! command -v sqlite3 >/dev/null || ! command -v tar >/dev/null || ! command -v runuser >/dev/null; then
  echo "sqlite3, tar, and runuser are required to create Cortex backups" >&2
  exit 1
fi

install -d -m 0700 "$DEST_ROOT"
exec 9>"$DEST_ROOT/.backup.lock"
if ! flock -n 9; then
  echo "another Cortex backup is already running" >&2
  exit 1
fi
STAGING="$(mktemp -d "$DEST_ROOT/.staging-$STAMP.XXXXXX")"
ARCHIVE="$DEST_ROOT/cortex-$STAMP.tar.gz.age"
ARCHIVE_TMP="$(mktemp "$DEST_ROOT/.archive-$STAMP.XXXXXX")"

trap 'rm -rf -- "$STAGING"; rm -f -- "$ARCHIVE_TMP"' EXIT

# 1. Vault trees (Markdown + JSON records + encrypted credential blobs). Memory content
#    is plaintext at rest, which is why the whole archive is encrypted below.
#    Snapshot vaults BEFORE the keyring/control databases: credential creation persists
#    its wrapped key before its encrypted vault blob. This order ensures a backed-up
#    ciphertext can never be newer than the backed-up keyring needed to decrypt it.
while IFS= read -r -d '' vault; do
  rel="${vault#"$SHARD_ROOT"/}"
  target="$STAGING/vault/$rel"
  mkdir -p "$target"
  # Coordinate with CortexVault's cross-process writer lock. A shared lock
  # freezes this vault while tar snapshots it and excludes the lock inode itself.
  (
    # Never open/chown this service-owned path as root: the service account can
    # replace it with a symlink. Create and open it only with service-account
    # privileges, so even a hostile path swap cannot alter a root-owned target.
    if [ ! -e "$vault/.cortex-vault.lock" ]; then
      runuser -u "$SERVICE_USER" -- touch -- "$vault/.cortex-vault.lock"
    fi
    if [ -L "$vault/.cortex-vault.lock" ] || [ ! -f "$vault/.cortex-vault.lock" ]; then
      echo "unsafe vault lock path: $vault/.cortex-vault.lock" >&2
      exit 1
    fi
    runuser -u "$SERVICE_USER" -- \
      flock -s "$vault/.cortex-vault.lock" \
      tar -C "$vault" --exclude='./.cortex-vault.lock' -cf - . \
      | tar -C "$target" -xf -
  )
done < <(find "$SHARD_ROOT" -maxdepth 3 -type d -name '*.vault' -print0 2>/dev/null)

# 2. Consistent snapshots of every SQLite DB under the shard root (shards, control
#    registry, token index, accounts, keyring metadata) — .backup handles live WAL.
while IFS= read -r -d '' db; do
  rel="${db#"$SHARD_ROOT"/}"
  mkdir -p "$STAGING/db/$(dirname "$rel")"
  sqlite3 "$db" ".backup '$STAGING/db/$rel'"
done < <(find "$SHARD_ROOT" -name '*.sqlite' -print0 2>/dev/null)

# Never copy /etc/cortex/cortex.env: it contains operator, OAuth, signing, and SMTP
# credentials. Deployment configuration should be reconstructed from the example and
# separately escrowed secrets.
tar -cz -C "$STAGING" . | age -r "$BACKUP_AGE_RECIPIENT" -o "$ARCHIVE_TMP"
mv "$ARCHIVE_TMP" "$ARCHIVE"
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
