#!/usr/bin/env bash
# First-install bootstrap for the Cortex hosted backend on a fresh Ubuntu 24.04 VM.
# Run by deploy/push.sh; idempotent enough to re-run after a failure.
#   bootstrap.sh <api-domain> <release-dir>
set -euo pipefail

API_DOMAIN="${1:?api domain required}"
RELEASE_DIR="${2:?release dir required}"

echo "=============================================================="
echo " Cortex bootstrap: https://$API_DOMAIN  (release $RELEASE_DIR)"
echo "=============================================================="

# --- 1. OS basics + hardening -------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ufw fail2ban unattended-upgrades \
  sqlite3 curl age debian-keyring debian-archive-keyring apt-transport-https gnupg

id -u cortex >/dev/null 2>&1 || useradd --system --home /srv/cortex --shell /usr/sbin/nologin cortex
mkdir -p /srv/cortex/releases /var/lib/cortex/shards /var/lib/cortex/backups /etc/cortex
chown -R cortex:cortex /srv/cortex /var/lib/cortex

# Firewall: SSH + HTTP(S) only.
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

# SSH hardening (keys only). Keep root-with-key so push.sh keeps working.
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl reload ssh || systemctl reload sshd || true

# Persistent, bounded logs.
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=persistent\nSystemMaxUse=2G\n' > /etc/systemd/journald.conf.d/cortex.conf
systemctl restart systemd-journald || true

# --- 2. Caddy (automatic HTTPS) ----------------------------------------------
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy
fi
sed "s/__API_DOMAIN__/$API_DOMAIN/g" "$RELEASE_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl enable --now caddy
systemctl reload caddy

# --- 3. Secrets (generated ONCE, kept out of the data dirs) -------------------
gen() { openssl rand -hex 32; }
ENV_FILE=/etc/cortex/cortex.env
if [ ! -f "$ENV_FILE" ]; then
  ADMIN_TOKEN="cxop_$(gen)"
  SIGNING_KEY="$(gen)"
  python3 - <<'PY' > /etc/cortex/kek
import base64, os
print(base64.b64encode(os.urandom(32)).decode())
PY
  chmod 0400 /etc/cortex/kek
  chown root:cortex /etc/cortex/kek && chmod 0440 /etc/cortex/kek
  sed -e "s|__API_DOMAIN__|$API_DOMAIN|g" \
      -e "s|__ADMIN_TOKEN__|$ADMIN_TOKEN|g" \
      -e "s|__SIGNING_KEY__|$SIGNING_KEY|g" \
      "$RELEASE_DIR/deploy/cortex.env.example" > "$ENV_FILE"
  chown root:cortex "$ENV_FILE" && chmod 0640 "$ENV_FILE"
  FIRST_INSTALL=1
else
  echo "==> /etc/cortex/cortex.env exists; keeping current secrets"
  FIRST_INSTALL=0
fi

# Backups use a dedicated public-key recipient. The private age identity is required
# only for disaster recovery and must be escrowed separately from backup archives.
BACKUP_AGE_IDENTITY=/etc/cortex/backup-age.key
if [ ! -f "$BACKUP_AGE_IDENTITY" ]; then
  age-keygen -o "$BACKUP_AGE_IDENTITY"
  chown root:root "$BACKUP_AGE_IDENTITY"
  chmod 0400 "$BACKUP_AGE_IDENTITY"
  BACKUP_IDENTITY_CREATED=1
else
  BACKUP_IDENTITY_CREATED=0
fi
BACKUP_AGE_RECIPIENT="$(age-keygen -y "$BACKUP_AGE_IDENTITY")"
if ! grep -q '^BACKUP_AGE_RECIPIENT=' "$ENV_FILE"; then
  printf 'BACKUP_AGE_RECIPIENT=%s\n' "$BACKUP_AGE_RECIPIENT" >> "$ENV_FILE"
fi

# --- 4. Python env + release switch -------------------------------------------
python3 -m venv /srv/cortex/venv 2>/dev/null || true
/srv/cortex/venv/bin/pip install --quiet --upgrade pip
/srv/cortex/venv/bin/pip install --quiet --require-hashes \
  -r "$RELEASE_DIR/backend/requirements.lock"
/srv/cortex/venv/bin/pip install --quiet --require-hashes \
  -r "$RELEASE_DIR/backend/runtime-requirements.lock"
ln -sfn "$RELEASE_DIR" /srv/cortex/current
chown -R cortex:cortex /srv/cortex/releases

# --- 5. systemd services -------------------------------------------------------
cp "$RELEASE_DIR"/deploy/systemd/cortex-api.service /etc/systemd/system/
cp "$RELEASE_DIR"/deploy/systemd/cortex-worker.service /etc/systemd/system/
cp "$RELEASE_DIR"/deploy/systemd/cortex-backup.service /etc/systemd/system/
cp "$RELEASE_DIR"/deploy/systemd/cortex-backup.timer /etc/systemd/system/
install -m 0755 "$RELEASE_DIR/deploy/backup.sh" /usr/local/bin/cortex-backup
systemctl daemon-reload
systemctl enable --now cortex-api cortex-worker cortex-backup.timer

# --- 6. Smoke ------------------------------------------------------------------
sleep 3
for i in $(seq 1 30); do
  if curl -fsS -o /dev/null "http://127.0.0.1:8766/health" -H "Authorization: Bearer $(grep '^CORTEX_API_KEY=' "$ENV_FILE" | cut -d= -f2)"; then
    break
  fi
  sleep 1
done
curl -fsS "http://127.0.0.1:8766/health" -H "Authorization: Bearer $(grep '^CORTEX_API_KEY=' "$ENV_FILE" | cut -d= -f2)" >/dev/null \
  && echo "==> Local health: OK" || { echo "!! API did not come up; journalctl -u cortex-api"; exit 1; }
echo "==> Public check (TLS may take ~30s on first issue): https://$API_DOMAIN/health"

echo "=============================================================="
echo " DONE."
if [ "$FIRST_INSTALL" = "1" ]; then
  echo "   New admin token: stored in $ENV_FILE (not printed)"
  echo "   New KEK        : stored in /etc/cortex/kek (not printed)"
fi
if [ "$BACKUP_IDENTITY_CREATED" = "1" ]; then
  echo "   Backup identity: stored in $BACKUP_AGE_IDENTITY (not printed)"
fi
echo "   Escrow the KEK and backup identity separately using an interactive,"
echo "   non-logged session. Do not copy them into deployment output."
echo "   Env file    : /etc/cortex/cortex.env"
echo "   API         : https://$API_DOMAIN   (health/ready)"
echo "   Services    : systemctl status cortex-api cortex-worker caddy"
echo "   Backups     : nightly via cortex-backup.timer -> /var/lib/cortex/backups"
echo "=============================================================="
