# Cortex hosted backend — simplest, cheapest production setup

One VM, one paste. Total cost: **€3.79–6.80/month** (Hetzner Cloud CX22/CX32) + a domain
you already own. This is the sanctioned sharded-SQLite tier from
`docs/CORTEX_10K_FINAL_PRODUCT_ROADMAP.md` on the smallest hardware that honestly serves a
beta; the 10k load test needs the bigger AX-line box from `docs/LAUNCH_RUNBOOK.md`, but
hundreds of beta users fit comfortably here and the upgrade is a snapshot-restore away.

## What you do (about 15 minutes)

1. **Create the VM** — Hetzner Cloud → new project → Add Server:
   - Image: Ubuntu 24.04, Type: **CX32** (4 vCPU / 8 GB, €6.80/mo; CX22 €3.79 works for a
     small beta), Location: nearest your users.
   - Add your SSH key. Enable "Backups" (+20% ≈ €1.40/mo — one click, worth it).
   - Note the server IP.

2. **Point DNS** — add one A record: `api.signindoppl.com` → the VM IP.
   (You own trydoppl.com — one A record does not disturb the site already living there.
   No domain handy? Pass `--sslip` below and you get `api.<ip>.sslip.io` with real TLS —
   fine for a private beta, not for public links.)

3. **From this repo on your Mac, run one command:**

   ```bash
   deploy/push.sh root@<VM-IP> api.signindoppl.com
   ```

   That uploads the current tree and runs `bootstrap.sh` on the box: OS hardening +
   firewall, Caddy with automatic HTTPS, Python venv, secret generation (admin token,
   KEK, signing key — stored in root-readable files and deliberately not printed),
   systemd services for the API + background worker, nightly WAL-safe encrypted backups,
   done.

4. **Verify:** `curl https://api.signindoppl.com/health` → `{"status":"ok",...}` and open
   `https://api.signindoppl.com/ready`.

5. **Escrow both recovery secrets** from an interactive, non-logged root session:
   - `/etc/cortex/kek` decrypts stored connector credentials.
   - `/etc/cortex/backup-age.key` decrypts backup archives.

   Keep both in a password manager and offline recovery location, separately from the
   backup bucket. Do not paste them into deployment logs or shell history. Losing either
   can make a full restore impossible.

## What this beta configuration deliberately does

- `CORTEX_AUTH_AUTOVERIFY=0`: accounts stay pending until email verification. Configure
  Postmark/SES (or another SMTP provider) before accepting public signups.
- `CORTEX_LEGAL_TERMS_APPROVED=0`: public account creation and hosted readiness stay
  blocked. Change this only after approved Terms and Privacy text is deployed; both
  password and OAuth signup require explicit terms and age consent.
- GitHub login works the moment you create a (2-minute, no-review) GitHub OAuth app and
  set the two env vars in `/etc/cortex/cortex.env`; Google login needs the consent-screen
  publishing review (1–2 weeks) so leave it for later.
- Free tier only; no billing.
- Backups are nightly, WAL-safe, age-encrypted, and kept 7 days **on the box** plus
  Hetzner VM snapshots. Set `BACKUP_RCLONE_REMOTE` for an offsite encrypted copy.

To inspect a recovery archive on a clean machine:

```bash
mkdir restore
age --decrypt -i backup-age.key cortex-YYYYMMDD-HHMMSS.tar.gz.age \
  | tar -xz -C restore
```

## Updating the running backend

```bash
deploy/push.sh root@<VM-IP> --update
```

The updater refreshes the systemd units and hardened backup script, installs `age`
when needed, takes a mandatory encrypted pre-deploy snapshot, reinstalls locked
requirements, and then restarts the worker and API. Runtime service definitions are
switched transactionally and restored automatically if the smoke check fails. The
previous release directory is kept for a dependency-and-service-aware rollback:

```bash
bash /srv/cortex/current/deploy/update.sh /srv/cortex/releases/<prev>
```

Do not replace only the `current` symlink unless you have separately verified that the
target release is compatible with the currently installed systemd units and Python
environment. Release trees remain owned by `root`; the `cortex` service account writes
only under `/var/lib/cortex`.

On the first update from an older deployment, the updater may create
`/etc/cortex/backup-age.key`. Escrow that identity separately from `/etc/cortex/kek`
before relying on the new backups. Existing environment files are deliberately not
rewritten with authentication or credential-encryption policy: reconcile
`CORTEX_AUTH_AUTOVERIFY`, `CORTEX_LEGAL_TERMS_APPROVED`, and
`CORTEX_REQUIRE_ENCRYPTED_CREDENTIALS` using
[`docs/SECURITY_REVIEW.md`](../docs/SECURITY_REVIEW.md) before public traffic. Enable
credential-encryption enforcement only after the documented credential backfill is
complete.

## Day-2 knobs (in `/etc/cortex/cortex.env`)

- `CORTEX_RATE_LIMIT_PER_MINUTE=120`, `CORTEX_DEFAULT_MEMORY_QUOTA=20000` — non-zero on
  purpose; raise deliberately.
- `CORTEX_OIDC_GITHUB_CLIENT_ID/SECRET` — enables "Continue with GitHub".
- Add a free UptimeRobot monitor on `https://api.signindoppl.com/ready` (5 minutes, your phone
  finds out before your users do).

## Scale-up path (unchanged code)

CX32 → resize to bigger cloud VM (minutes, in-place) → Hetzner AX42/52 dedicated
(€46–64/mo, the 10k-tested tier; restore from backup) → Postgres control store + KMS
per `docs/ACCOUNTS_ENCRYPTION_DESIGN.md` §5 swap table.
