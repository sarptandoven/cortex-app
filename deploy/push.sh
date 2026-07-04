#!/usr/bin/env bash
# One-command deploy from your Mac to the VM.
#
# First install:   deploy/push.sh root@<VM-IP> api.<your-domain>
#                  deploy/push.sh root@<VM-IP> --sslip          (no domain: api.<ip>.sslip.io)
# Update running:  deploy/push.sh root@<VM-IP> --update
#
# Uploads the CURRENT git tree (committed state only, so a dirty checkout can't
# surprise you in production), then runs bootstrap.sh (first install) or the
# release-switch update path on the box.
set -euo pipefail

TARGET="${1:?usage: deploy/push.sh root@<VM-IP> <api-domain>|--sslip|--update}"
MODE="${2:?usage: deploy/push.sh root@<VM-IP> <api-domain>|--sslip|--update}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

STAMP="$(date -u +%Y%m%d%H%M%S)-$(git rev-parse --short HEAD)"
RELEASE_DIR="/srv/cortex/releases/$STAMP"

echo "==> Uploading tree $STAMP to $TARGET:$RELEASE_DIR"
git archive --format=tar HEAD | ssh "$TARGET" "mkdir -p '$RELEASE_DIR' && tar -x -C '$RELEASE_DIR'"

if [ "$MODE" = "--update" ]; then
  echo "==> Switching release + restarting services"
  ssh "$TARGET" "bash '$RELEASE_DIR/deploy/update.sh' '$RELEASE_DIR'"
else
  if [ "$MODE" = "--sslip" ]; then
    HOST_IP="$(ssh "$TARGET" "curl -4 -s https://ifconfig.me || hostname -I | awk '{print \$1}'")"
    API_DOMAIN="api.$(echo "$HOST_IP" | tr '.' '-').sslip.io"
    echo "==> No domain given; using $API_DOMAIN"
  else
    API_DOMAIN="$MODE"
  fi
  echo "==> Running bootstrap for https://$API_DOMAIN"
  ssh -t "$TARGET" "bash '$RELEASE_DIR/deploy/bootstrap.sh' '$API_DOMAIN' '$RELEASE_DIR'"
fi
