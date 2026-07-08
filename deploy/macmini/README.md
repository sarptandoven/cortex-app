# Hosting Cortex on a Mac mini

Yes — you can run the whole hosted backend on a Mac mini instead of a cloud VM.
The app is Python + SQLite, so it runs natively on macOS; this kit makes it a
one-command, always-on service and exposes it to the internet **without opening
any router ports** via Cloudflare Tunnel.

## Honest tradeoffs (read first)
- **Compute: excellent.** An M-series mini with 16 GB is far stronger than the
  €7 cloud VM this project load-tested at 10k users. Capacity is not your limit.
- **Availability: your responsibility.** A home/office mini depends on your
  power, your internet, and the machine staying awake. A cloud VM has better
  uptime and a static address. For an invite beta / early users the mini is
  genuinely fine; for a big public launch, weigh the uptime risk.
- **Backups MUST go off the machine.** The mini is the whole system now — if it
  dies or is stolen, local backups die with it. Ship backups to R2/B2 (below).
- **Escrow the KEK OFF the mini.** Same reason. (deploy/macmini/setup.sh prints it.)
- **ISP terms.** Cloudflare Tunnel is outbound-only (no inbound ports), which
  sidesteps port-blocking, but some residential ISP ToS still discourage
  "servers." Low-traffic betas are rarely an issue; know your ISP's rules.

## Step 1 — Run the services (one command)
From the repo on the mini:
```
bash deploy/macmini/setup.sh
```
This installs deps, generates your admin token + encryption KEK (printed once —
save them), writes `~/CortexServer/cortex.env`, and loads two launchd services
(`com.cortex.api`, `com.cortex.worker`) that start at login and restart on crash.
The API listens only on `127.0.0.1:8766` (never exposed directly).

Verify: `curl http://127.0.0.1:8766/health` → `{"status":"ok",...}`.

## Step 2 — Make it always-on
- Keep the mini awake (dedicated server):
  `sudo pmset -c sleep 0 disksleep 0 autorestart 1`
- launchd `LaunchAgents` run when your user is logged in. For a headless mini,
  enable **Automatic Login** (System Settings → Users & Groups) so the services
  come back after a reboot. (For a fully hardened setup, promote the two plists
  to `LaunchDaemons` in `/Library/LaunchDaemons` — runs at boot without login,
  needs sudo; the plists are identical.)

## Step 3 — Expose it publicly with Cloudflare Tunnel (free, no port-forwarding)
```
brew install cloudflared
cloudflared tunnel login                       # opens browser; pick trydoppl.com
cloudflared tunnel create cortex               # note the tunnel ID/credentials file
cloudflared tunnel route dns cortex api.signindoppl.com
```
Create `~/.cloudflared/config.yml`:
```
tunnel: <TUNNEL-ID>
credentials-file: /Users/<you>/.cloudflared/<TUNNEL-ID>.json
ingress:
  - hostname: api.signindoppl.com
    service: http://127.0.0.1:8766
  - service: http_status:404
```
Run it as a service so it's always up:
```
sudo cloudflared service install     # installs a launchd daemon for the tunnel
```
Now `https://api.signindoppl.com` reaches your mini with real TLS — Cloudflare
terminates HTTPS and the tunnel carries traffic over an outbound connection, so
no inbound firewall/port changes are needed.

Verify from anywhere: `curl https://api.signindoppl.com/health`, then open
`https://api.signindoppl.com/account/signup`.

Quick test without a domain/account: `cloudflared tunnel --url http://127.0.0.1:8766`
prints a temporary `https://<random>.trycloudflare.com` URL (ephemeral).

## Step 4 — Off-machine backups (do not skip)
The bundled backup job keeps local copies; add an offsite target so a dead mini
doesn't take the data with it. Easiest: a Backblaze B2 / Cloudflare R2 bucket +
`rclone`, then a nightly launchd job running the same WAL-safe snapshot logic as
`deploy/backup.sh` (adapt paths to `~/CortexServer`). Escrow the KEK separately.

## Step 5 — Point the app + web at it
Same as any hosted deployment: users open `https://api.signindoppl.com/account/signup`
in a browser, or in the macOS app enter that URL under Settings → Cortex Cloud
and sign in. Nothing else changes.

## Managing the services
```
launchctl list | grep com.cortex                 # status
launchctl unload ~/Library/LaunchAgents/com.cortex.api.plist   # stop
launchctl load  -w ~/Library/LaunchAgents/com.cortex.api.plist # start
tail -f ~/CortexServer/logs/com.cortex.api.err.log             # logs
```
Update after `git pull`: re-run `bash deploy/macmini/setup.sh` (idempotent; keeps
your secrets) — it reinstalls deps and reloads the services.

## When to move off the mini
If uptime becomes the bottleneck (home internet/power), restore onto a cloud box
with the Ubuntu kit in `deploy/` (Hetzner AX-line for the 10k tier) — the data,
KEK, and config port over unchanged.
