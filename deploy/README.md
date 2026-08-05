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
   KEK, signing key — printed ONCE at the end; store them in a password manager),
   systemd services for the API + background worker, nightly WAL-safe backups, done.

4. **Verify:** `curl https://api.signindoppl.com/health` → `{"status":"ok",...}` and open
   `https://api.signindoppl.com/ready`.

5. **Escrow the KEK** (printed by bootstrap): copy `/etc/cortex/kek` into your password
   manager AND one offline place. If the box dies and the KEK is lost, every user's
   encrypted credentials are unrecoverable — that is the point of the design.

## What this beta configuration deliberately does

- `CORTEX_AUTH_AUTOVERIFY=1`: accounts activate at signup with **no email server**.
  Flip it off (and set up Postmark + `CORTEX_AUTH_EMAIL_MODE=smtp`) before public launch —
  unverified emails mean no password-recovery channel.
- GitHub login works the moment you create a (2-minute, no-review) GitHub OAuth app and
  set the two env vars in `/etc/cortex/cortex.env`; Google login needs the consent-screen
  publishing review (1–2 weeks) so leave it for later.
- Free tier only; no billing.
- Backups are nightly, WAL-safe, kept 7 days **on the box** plus whatever Hetzner's
  VM backup snapshots. Add true offsite (rclone target in `backup.sh`) in week one.

## Updating the running backend

```bash
deploy/push.sh root@<VM-IP> --update
```

(uploads the tree, reinstalls requirements, restarts worker, reloads API; the previous
release dir is kept for instant rollback: `ln -sfn /srv/cortex/releases/<prev> /srv/cortex/current && systemctl restart cortex-api cortex-worker`)

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
