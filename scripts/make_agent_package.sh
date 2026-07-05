#!/bin/bash
# Build the Cortex Hermes-agent handoff package: a self-contained zip with the
# operator runbook, all launch docs, the deploy kits, and the launch scripts, so
# an agent on the Mac mini can set everything up start to finish. The zip does
# NOT bundle the app source/binaries — step 1 of the runbook clones the repo
# (the agent has GitHub access). Reproducible; safe to re-run.
#
#   bash scripts/make_agent_package.sh [output.zip]
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$REPO/cortex-agent-package.zip}"
STAGE="$(mktemp -d)/cortex-agent-package"
trap 'rm -rf "$(dirname "$STAGE")"' EXIT

mkdir -p "$STAGE/docs/runbooks" "$STAGE/deploy" "$STAGE/scripts"

# --- operator docs ---
cp "$REPO/docs/AGENT_RUNBOOK.md" "$STAGE/"          # the entrypoint
for f in START_HERE.txt COMPLETE_LAUNCH_INSTRUCTIONS.txt REMAINING_LAUNCH_WORK.txt \
         DELEGATION_TOKENS.txt DISTRIBUTION.md STATUS_PAGE.md ACCOUNTS_ENCRYPTION_DESIGN.md; do
  [ -f "$REPO/docs/$f" ] && cp "$REPO/docs/$f" "$STAGE/docs/"
done
cp "$REPO/docs/runbooks/"*.md "$STAGE/docs/runbooks/" 2>/dev/null || true

# --- deploy kits (macOS + cloud), full, so automation is in-hand offline ---
cp -R "$REPO/deploy/." "$STAGE/deploy/"

# --- the launch/ops scripts an operator runs (no product source) ---
for s in public_launch_gate.py publish_release.sh check_distribution_site.py \
         load_test_10k.py run_memory_worker.py; do
  [ -f "$REPO/scripts/$s" ] && cp "$REPO/scripts/$s" "$STAGE/scripts/"
done

# --- README_FIRST (generated) ---
cat > "$STAGE/README_FIRST.txt" <<'EOF'
CORTEX — HERMES AGENT PACKAGE
=============================
This bundle is everything an agent needs to set Cortex up on the Mac mini,
from start to finish, and take it live + usable online.

START HERE:
  1. Read AGENT_RUNBOOK.md end to end. It is your charter, ordered steps, and
     safety rails.
  2. It will have you clone the code from GitHub (doppl-tech/cortex-app) — this
     package intentionally does NOT include the app source or binaries; you have
     GitHub access, so you pull the latest.
  3. Fill in SECRETS_WORKSHEET.md with what the founder has already set up
     (domain, accounts, tokens) so you know what's ready vs. blocked.
  4. Execute Phase 1 (get it live + usable online on the mini via
     deploy/macmini/setup.sh + Cloudflare Tunnel), verify with
     scripts/public_launch_gate.py, then report.

WHAT'S IN HERE:
  AGENT_RUNBOOK.md .............. the master operator runbook (read first)
  SECRETS_WORKSHEET.md ......... what the founder provides; fill before starting
  docs/ ........................ START_HERE, full launch instructions, delegation
                                 tokens, distribution, status page, design, runbooks
  deploy/ ...................... deploy kits — deploy/macmini/ (Mac hosting) +
                                 deploy/ (cloud VM alternative)
  scripts/ ..................... launch gate, release publisher, load test, worker

GOLDEN RULES (also in AGENT_RUNBOOK §0): never commit secrets or the KEK; escrow
the KEK off-machine; STOP and ask before spending money, accepting legal terms,
publishing publicly, or flipping to PUBLIC mode; least-privilege tokens; when
blocked, report exactly what you need — don't guess.
EOF

# --- SECRETS_WORKSHEET (generated; the human fills, the agent reads) ---
cat > "$STAGE/SECRETS_WORKSHEET.md" <<'EOF'
# Founder Worksheet — fill this in, then hand the package to the agent

Mark each: [x] ready  /  [ ] not yet. Give the agent the values it needs
DIRECTLY (not committed to git). The agent reads this to know what's unblocked.

## Ready now
- [ ] Domain: trydoppl.com — DNS editable by: ______________________________
- [ ] Mac mini reachable, awake, python3 installed
- [ ] GitHub access to doppl-tech/cortex-app (agent has it)

## Accounts (for going further; not needed for a first beta)
- [ ] Cloudflare account (for the tunnel + DNS) — logged in on the mini? ______
- [ ] GitHub OAuth app for login — client id/secret handed to agent? _________
- [ ] Email provider (Postmark/SES) — API key + domain verified? _____________
- [ ] Apple Developer ID cert + notarytool profile (for a notarized DMG) ______
- [ ] Google OAuth client + consent screen submitted? ________________________
- [ ] Billing (Paddle/Stripe) account + webhook secret? ______________________
- [ ] Offsite backup bucket (R2/B2) + credentials? ___________________________
- [ ] UptimeRobot / status page tokens? ______________________________________

## Scoped tokens handed to the agent (see docs/DELEGATION_TOKENS.txt)
- [ ] Cloudflare API token (zone DNS + Pages) ........ value given: [ ]
- [ ] GitHub fine-grained PAT (this repo) ............ value given: [ ]
- [ ] Email provider API key ......................... value given: [ ]
- [ ] (cloud VM path only) Hetzner API token ......... value given: [ ]

## Decisions the agent must NOT make alone
- Go PUBLIC (open signups / autoverify off): approved? [ ]  (founder decision)
- Spend money / enter card details: approved for what? _______________________
- Legal entity / accept terms / lawyer review: founder owns these.

## Secrets the agent will GENERATE and you must ESCROW
- Admin token (cxop_...) → your password manager.
- Encryption KEK → password manager AND one offline place NOT with backups.
  (Losing the KEK = all encrypted user data is unrecoverable.)
EOF

# --- build the zip ---
rm -f "$OUT"
( cd "$(dirname "$STAGE")" && zip -qry "$OUT" "cortex-agent-package" )
echo "Built: $OUT"
echo "Contents:"
unzip -l "$OUT" | tail -n +2 | awk '{print "  "$4}' | sed '/^  $/d'
