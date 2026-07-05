# Cortex hosted — incident runbook

For the **operator** (not end users). A checklist you can follow at 3am. Assumes the
standard single-VM deploy from `deploy/` (Hetzner + Caddy + systemd). If you run a
different layout, adjust the paths.

## The facts you need (memorize or pin these)

| Thing | Value |
|---|---|
| API service | `cortex-api` (uvicorn, loopback `127.0.0.1:8766`, behind Caddy) |
| Worker service | `cortex-worker` (embeddings, syncs, background jobs) |
| TLS / reverse proxy | `caddy` (auto Let's Encrypt) |
| Backup timer | `cortex-backup.timer` → `cortex-backup.service` (nightly) |
| Env file | `/etc/cortex/cortex.env` (edit → restart services) |
| Data root | `/var/lib/cortex` (shards in `shards/`, backups in `backups/`) |
| Releases | `/srv/cortex/releases/<timestamp>`; live symlink `/srv/cortex/current` |
| Python venv | `/srv/cortex/venv` |
| KEK | `/etc/cortex/kek` (never in a backup — see `KEK_ESCROW.md`) |
| Admin token | `CORTEX_API_KEY=` in the env file (needed for `/health`) |
| Health check | `GET /health` (needs `Authorization: Bearer <admin token>`) |
| Readiness | `GET /ready` (public; 200 = ok, 503 = needs config / degraded) |

Get the admin token when a command needs it:

```bash
ADMIN_TOKEN="$(grep '^CORTEX_API_KEY=' /etc/cortex/cortex.env | cut -d= -f2)"
```

First move for almost any incident: SSH in and take the pulse.

```bash
ssh root@<SERVER-IP>
systemctl status cortex-api cortex-worker caddy --no-pager
df -h /var/lib/cortex          # disk
free -m                        # memory
curl -fsS http://127.0.0.1:8766/ready | head           # readiness (or 503 body)
curl -fsS http://127.0.0.1:8766/health -H "Authorization: Bearer $ADMIN_TOKEN" | head
```

---

## 1. API is down (users get errors / site unreachable)

```bash
systemctl status cortex-api --no-pager
journalctl -u cortex-api -n 200 --no-pager        # read the last errors first
curl -fsS http://127.0.0.1:8766/ready             # is it the app or the proxy?
```

- If `/ready` on loopback works but the public URL fails → it's TLS/proxy, jump to **§6**.
- If the app is down or crash-looping:

```bash
systemctl restart cortex-api
sleep 3 && systemctl status cortex-api --no-pager
curl -fsS http://127.0.0.1:8766/health -H "Authorization: Bearer $ADMIN_TOKEN"
```

- Still failing? Check for the common causes:
  - **Disk full** → **§3**.
  - **Bad deploy** (just shipped something) → **§4**.
  - **DB integrity** (errors mention SQLite / malformed) → **§7**.
  - **Config broke** (recent env edit) → review `/etc/cortex/cortex.env`, restart.
- `/ready` returning **503 `needs_configuration`** means a required setting is missing
  (rate limit / quota zero, etc.) — read the JSON body, fix the env value, restart.

---

## 2. Worker stopped (embeddings and syncs stall)

Symptom: captures land but never get embedded/answerable, connector syncs don't
progress, job queue grows.

```bash
systemctl status cortex-worker --no-pager
journalctl -u cortex-worker -n 200 --no-pager
systemctl restart cortex-worker
```

- Confirm it's chewing through the queue again after restart (watch the journal).
- If it dies immediately, look for a bad job or a disk/DB problem (**§3**, **§7**).

---

## 3. Disk full

```bash
df -h /var/lib/cortex
du -sh /var/lib/cortex/* | sort -h        # what's big?
du -sh /var/lib/cortex/backups/*
```

Fast wins, safest first:

```bash
# Prune old local backups (retention is 7 nights; delete oldest first).
ls -1t /var/lib/cortex/backups/cortex-*.tar.gz | tail -n +8 | xargs -r rm -f

# Trim journald if logs ballooned.
journalctl --vacuum-size=200M
```

- If still tight, **resize the VM disk** (Hetzner console → resize; the filesystem
  usually grows on reboot, or `resize2fs` the partition).
- Do NOT delete anything under `/var/lib/cortex/shards` — that is live user data.
- After freeing space, restart services if they wedged: `systemctl restart cortex-api cortex-worker`.

---

## 4. Bad deploy

The normal update path snapshots the DB, flips the release symlink, smoke-tests
`/health`, and **auto-rolls-back** if the smoke test fails:

```bash
git pull ; deploy/push.sh root@<SERVER-IP> --update
```

If a deploy got through the smoke test but is misbehaving, roll back manually to the
previous release (kept on the box — see `COMPLETE_LAUNCH_INSTRUCTIONS.txt` PART 19.2):

```bash
ssh root@<SERVER-IP>
ls -1dt /srv/cortex/releases            # find the previous timestamp
ln -sfn /srv/cortex/releases/<PREV> /srv/cortex/current
systemctl restart cortex-worker cortex-api
curl -fsS http://127.0.0.1:8766/health -H "Authorization: Bearer $ADMIN_TOKEN"
```

- The 5 newest releases are retained, so the previous one is almost always present.
- Once stable, investigate the bad release before re-attempting the deploy.

---

## 5. Mass 5xx right after a deploy — checklist

Work top to bottom; stop when you find it.

1. **Is it the app or the proxy?**
   `curl -fsS http://127.0.0.1:8766/ready` — if loopback is fine, it's TLS/proxy (**§6**).
2. **Read the fresh errors:** `journalctl -u cortex-api -n 100 --no-pager` (look for
   tracebacks, missing-module, missing-env, migration errors).
3. **Did deps/migrations fail?** A new release can add requirements or DB columns; the
   journal will say. If migrations are the issue, roll back (**§4**) and fix offline.
4. **Missing/changed env var?** Compare `/etc/cortex/cortex.env` against
   `deploy/cortex.env.example` for anything the new release now requires.
5. **Roll back** (**§4**) if you can't fix it in a minute or two. Recovery beats debugging
   live. The previous release is one symlink away.
6. **Confirm recovery:** `/health` (auth) 200, `/ready` 200, 5xx rate drops on the monitor.
7. Then debug the bad release on a scratch box, not production.

---

## 6. TLS / certificate issue (HTTPS broken, cert errors)

Caddy owns TLS and auto-renews Let's Encrypt certs.

```bash
systemctl status caddy --no-pager
journalctl -u caddy -n 200 --no-pager       # renewal / ACME / rate-limit errors
systemctl reload caddy                       # picks up Caddyfile changes without dropping conns
# curl through the proxy from the box:
curl -fsS https://<API-DOMAIN>/ready
```

- Common causes: DNS not pointing at the box, port 80/443 blocked (ACME needs 80),
  or Let's Encrypt rate-limit after repeated failures. Fix DNS/firewall, then
  `systemctl restart caddy` and watch the journal for a successful issuance.
- The app itself listens on loopback only; if `/ready` on `127.0.0.1:8766` is healthy,
  the problem is Caddy/DNS/firewall, not Cortex.

---

## 7. Database integrity

If the journal shows SQLite errors ("database disk image is malformed", "database is
locked" persistently):

```bash
# Quick check each DB under the shard root (read-only).
for db in $(find /var/lib/cortex/shards -name '*.sqlite'); do
  echo "== $db"
  sqlite3 "$db" 'PRAGMA quick_check;'
done
```

- `ok` on every DB → integrity is fine; the error is elsewhere (locking, disk).
- Anything other than `ok` → stop writes: `systemctl stop cortex-api cortex-worker`,
  then **restore from the most recent good backup** (the nightly `.backup` archives in
  `/var/lib/cortex/backups` are WAL-consistent). Restore procedure and the KEK it needs
  are in `KEK_ESCROW.md`. Do not run repair pragmas on production before you have a copy.

---

## 8. Suspected KEK compromise

The KEK (`/etc/cortex/kek`) is the master encryption key. If you suspect it leaked
(exposed in logs, a stolen box image, an over-broad backup, a shared secret):

- **First, contain:** rotate the box's access (SSH keys, admin token
  `CORTEX_API_KEY`), and confirm the KEK is not in any backup (`backup.sh` excludes it
  by design — verify no one added it).
- **Assess exposure:** a stolen KEK is only dangerous *together with* a data backup —
  see the golden rule in `KEK_ESCROW.md`.
- **Rotation is advanced.** KEK rotation is an incremental re-wrap of the per-user key
  wraps under a new KEK version; it does not touch user data bytes. It is designed but
  operationally advanced — follow `KEK_ESCROW.md` and the design doc
  (`docs/ACCOUNTS_ENCRYPTION_DESIGN.md` §4 "Rotation", §5 LocalKek → KMS path) before
  attempting it, and do it on a maintenance window with a fresh backup and the escrowed
  KEK on hand.

---

## After any incident

- Confirm green: `/health` (auth) and `/ready` both 200; worker draining its queue.
- Verify the nightly backup still runs: `systemctl status cortex-backup.timer` and check
  a fresh archive appears in `/var/lib/cortex/backups`.
- Write down what happened and what fixed it (a two-line note now saves the next 3am).
- Update the status page if you have one.
