<!-- Generated 2026-07-04 by a 7-dimension + critic gap-scan workflow (wf_20677164-242)
     grounded in the actual repo state, with web research on hosting/pricing/lead times.

     STATUS CORRECTIONS (work landed AFTER the scans ran, commits 9dda21c + a72f480):
     - Item 19 "Wire the auth layer to HTTP" is DONE: /v1/auth surface, oidc_registry.py
       (Google PKCE+nonce+RS256, GitHub verified-email, disabled openai slot), cxs_ dispatch,
       app start/poll flow, invite/claim, admin encryption backfill. 1006 tests green.
     - Item 23 partially DONE: backfill endpoint + keyring injection exist; the
       CORTEX_REQUIRE_ENCRYPTED_CREDENTIALS enforce flag + readiness gauge remain.
     - Item 22 partially DONE: velocity limiter hooks + enumeration resistance are wired;
       disposable-email blocklist, argon2 semaphore, Turnstile remain.
     - The cookie/CSRF variant in item 19/21 is deliberately deferred: all auth is
       Bearer-header today (no cookie surface exists), it becomes relevant with the web UI.
-->

# Cortex — Final Launch Runbook: From "Works on the Founder's Laptop" to "Thousands of Users"

## 1. Executive Summary

Honest distance: the *logic tier* is genuinely done (972+ tests, isolation, quotas, readiness contract, accounts+envelope-encryption landing now), but **zero production surface area exists**. There is no server, domain, TLS, supervision, offsite backup, or alerting; the accounts backend has no HTTP routes, no web UI, and no email sender, so **no member of the public can create an account today**; the download link on trydoppl.com is a 404; there is no legal entity, privacy policy, ToS, or billing; and CI has never executed once (zero Actions runs, no PRs, no branch protection). The 10k load-test number is a laptop artifact that predates argon2id and AES-GCM. Realistic path: **~3–4 weeks of focused engineering, but the launch date is set by external clocks, not code** — entity → D-U-N-S → Apple org enrollment, Google OAuth publishing review, Paddle KYC, and email-domain reputation warm-up each take 1–3 weeks of pure waiting and several chain behind each other. Every one of those clocks must start **this week** (today is 2026-07-04); the launch date is max(waiting paths), not sum(engineering).

---

## 2. THE CRITICAL PATH (launch blockers, in order)

Tags: `[F]` founder-only, `[E]` engineering, ⏳ = external lead time, start immediately.

### Phase 0 — Start the clocks (this week, mostly founder)

1. ⏳ `[F, days]` **Form the legal entity** (Stripe Atlas $500 or Canadian federal corp ~CAD $200) + create support@trydoppl.com / privacy@trydoppl.com (Google Workspace $7/user/mo). Replace sdoven@uwaterloo.ca in site/privacy.html:87,95, docs/BETA_SUPPORT.md, and scripts/prepare_first100_launch_packets.py. Blocks: Paddle, Apple org enrollment, DPAs, policies, GDPR controller identity.
2. ⏳ `[F, days wait]` **Request D-U-N-S number today** (free, 1–2 wks) → enroll the *organization* in Apple Developer ($99/yr) → generate Developer ID cert under the **final company Team ID**. Hard rule (add to docs/DISTRIBUTION.md, replacing the "Example, Inc. (TEAMID)" placeholder at line 99): no public build ever ships under a personal Team ID — changing it later resets every user's TCC grants and breaks the future Sparkle feed/cask pins.
3. `[F, hours]` **Pick one brand + one site.** trydoppl.com currently serves a different Next.js "doppl" app whose root 404s; the repo's site/ is deployed nowhere. Decide Cortex-vs-doppl naming, fold one site into the other. Blocks: downloads, OAuth consent screen, email domain.
4. ⏳ `[F, hours + weeks of warm-up]` **Email stack + deliverability clock.** Sign up Postmark ($15/mo/10k) or SES; create mail.<domain>; add SPF, DKIM CNAMEs, DMARC (p=quarantine, rua=). Register in Google Postmaster Tools + Microsoft SNDS. **Start sending real low-volume mail now** (beta invites, waitlist confirmations) and ramp — a cold domain blasting thousands of verification mails at launch gets junked regardless of correct DNS. Split news.<domain> for announcements with List-Unsubscribe; wire bounce/complaint webhooks to auto-suppress.
5. ⏳ `[F, days + 1–2 wk review]` **Publish privacy policy + ToS, then submit Google OAuth consent-screen publishing.** Privacy policy: rewrite site/privacy.html (currently *false* for hosted — says "no account system…") with controller identity, connector-data categories, AI-processing vendor disclosure, the design doc's verbatim encryption claim (docs/ACCOUNTS_ENCRYPTION_DESIGN.md:249 — never "zero-knowledge"/"E2E"; crypto-shred scoped to credentials until phases 2–3), GDPR/CCPA/TDPSA rights, retention table, sub-processor link. ToS from Common Paper template: refunds + EU withdrawal waiver, user owns memory content, acceptable use, liability cap = 12 mo fees (lawyer for liability/arbitration, ~$1–2k; 2–3 lawyer-hours on encryption/deletion claims). Then: Google Cloud OAuth app + GitHub OAuth app with production callback URLs, Search Console domain verification, submit publishing (100-user testing cap until approved). Publish site/subprocessors.html + sign click-through DPAs (Postmark, Hetzner, etc.).
6. ⏳ `[F, days wait]` **Apply to Paddle** (merchant of record — VAT/sales tax is their problem; the Stripe crossover is ~$50–100k MRR, far past launch). Needs entity + live legal pages, so chains behind 1 and 5.
7. `[F, hours]` **Order the production box + staging VM** (spec in §3) and buy/confirm the production domain.
8. `[F+E, hours]` **Write docs/LAUNCH_CRITICAL_PATH.md** — dependency-ordered table (item, owner, submit date, expected wait, blocks-what) for every clock above. Review weekly.

### Phase 1 — Production platform (engineering, ~1 week; details in §3)

9. `[E, hours]` OS hardening + firewall + SSH policy (bootstrap.sh).
10. `[E, hours]` Caddy TLS + security headers + proxy-headers/real-client-IP; `/ready` flips green on the public HTTPS origin.
11. `[E, days]` systemd supervision for API + external worker with sandboxing; worker-count decision (rate-limit ÷ N, store-cache ÷ N).
12. `[E, days]` Production env/secrets: deploy/cortex.env.example enumerating every var with generation commands; **rate limits and quotas explicitly non-zero** (both default to 0 = OFF in config.py).
13. `[E+F, days]` **KEK escrow + secrets hygiene**: KEK in /etc/cortex/kek (0400) via CORTEX_KEK_FILE; permission/owner check in LocalKekProvider._load_kek; os.umask(0o077) + chmod 0600 on accounts.sqlite/token_index/keyring DBs; CORTEX_KEYRING_DB_PATH **outside** shard_root and excluded from data backups; founder escrows KEK in ≥2 offline places that are NOT the data backup; docs/runbooks/KEK_ESCROW.md + a scripted restore-and-decrypt drill. Also: a test that restore-after-crypto_shred does NOT resurrect keys.
14. `[E, days]` Offsite backups (nightly, WAL-safe, encrypted, off-box) + one timed restore drill → docs/runbooks/RESTORE_RUNBOOK.md with measured RPO/RTO.
15. `[E, days]` Alerting that pages a phone: /metrics endpoint (iter_prometheus_lines already exists), Grafana Cloud free tier, external uptime probe on /ready, healthchecks.io dead-man pings from worker tick + backup job.
16. `[E, days]` Minimal deploy: deploy.sh with git-sha release dirs + current symlink, **pre-deploy sqlite3 .backup snapshot of accounts.sqlite + shards (kept 7 days)**, post-restart smoke, symlink-flip rollback to N-1; additive-only migration rule. (GitHub Actions auto-deploy = first week.)
17. `[E, days]` **Disk-full protection**: shutil.disk_usage gauges in observability + hosted_readiness; refuse imports/syncs >90% with clear 429; page at 80%; schedule the never-invoked prune_backups; size disk from load-test du × 10k × 2.5.
18. `[E, days]` **Shard-topology guard**: persist {shard_mode, shard_count} in a control-plane meta table at first boot and hard-fail startup on env mismatch (a typo'd CORTEX_SHARD_COUNT presents as total data loss); assigned_shard column consulted before modulo; scripts/reshard_user.py; per-shard size/latency gauges + numeric Postgres-escape trigger in docs.

### Phase 2 — Make signup real (engineering, ~1–2 weeks)

19. `[E, week+]` **Wire the auth layer to HTTP** (nothing is wired today: no /v1/auth router, AccountsService/UserKeyring never instantiated, oidc_registry.py absent): construct LocalKekProvider + UserKeyring + SQLiteControlStore + AccountsService at startup; /v1/auth routes (signup, verify, login, refresh, logout, reset, sessions list/revoke) with __Host- cookies + X-Cortex-Client CSRF; cxs_ prefix dispatch in auth() (kept off admin/MCP surfaces); oidc_registry.py with PKCE S256 + nonce + full id_token verification + GitHub verified-primary-email; design section-7 release-gate tests incl. never-auto-link matrix.
20. `[E, days]` **SMTP EmailSender** behind flow_delivery (auth_email_mode='smtp' is unimplemented — signup dead-ends at pending_verification); 3 plaintext templates; staging smoke that sends a real mail; mail-tester ≥9.
21. `[E, days]` **Web auth UI** (none exists anywhere): server-rendered /account/{signup,login,verify,reset,claim} + session/token/delete page, same-origin for the cookie/CSRF design; correct autocomplete attributes for password managers.
22. `[E, days]` **Abuse controls** (design mandates, zero in code): per-IP + per-identifier velocity limits wired into AccountsService (limiter currently None, and keys on attacker-chosen email); disposable-email blocklist; BoundedSemaphore(4) around argon2 (64 MiB/hash — a signup flood OOMs the box); TTL eviction in ratelimit.py buckets (unbounded today); Cloudflare Turnstile on signup; HIBP k-anonymity check. Load-test: 1k signups/min from one IP → 429s, flat memory.
23. `[E, days]` **Encryption enforce-flip machinery** (currently a hosted operator with CORTEX_KEK set still writes plaintext credentials): pass keyring into StoreRegistry.from_settings; remaining_plaintext_credentials gauge in readiness; admin backfill endpoint; CORTEX_REQUIRE_ENCRYPTED_CREDENTIALS flag; runbook: KEK → backfill → gauge 0 → enforce → gate /ready.
24. `[E, hours]` **Age gate + ToS acceptance**: "16 or older" attestation + tos_version/tos_accepted_at columns in the accounts migration; required on signup/claim; checkboxes in both UIs. Retrofitting after thousands of signups means auditing an unknown minor population.
25. `[E+F, days]` **Accounts cutover runbook** (claim_account_for_user exists; flip day doesn't): enumerate existing tenants, mint+email invite codes with deadline and reminders; **decide legacy cxa_ token policy in code** (dated grace window + deprecation metric, then hard-require account-bound — otherwise a parallel auth path bypasses sessions/encryption forever); support playbook + FAQ for the guaranteed never-auto-link duplicate-account confusion; rehearse on staging with 5 seeded legacy tenants.

### Phase 3 — Clients, distribution, money

26. `[E, week+]` **macOS app account UI + hosted endpoint**: the app has zero account views and CortexApp.swift:2264 hard-rejects any non-localhost endpoint. Email+password form; Google/GitHub via ASWebAuthenticationSession + app start/poll flow; cxr_ in Keychain with rotation/family-revocation handling; allow exactly one pinned hosted HTTPS domain; .textContentType autofill hints.
27. ⏳ `[F+E, days]` **Notarization** (known blocker — now unblocked by items 1–2): sign under company Team ID, plan hardened-runtime entitlements for the bundled Python so the first submission passes.
28. `[E, days]` **Fix the download path**: deploy the chosen site; move the 22 MB DMG/16 MB zip **out of git** (currently tracked!) to Cloudflare R2 (zero egress) behind downloads.<domain>; keep only latest.json + checksums in git; CI deploy step gated on check_distribution_site.py; `curl -fI` the manifest.
29. `[E, hours]` **Fix Gatekeeper copy for macOS 15+/26**: every doc/README/install_steps says "Control-click → Open," which Apple removed in Sequoia. Update to Settings > Privacy & Security > Open Anyway; add a 15-sec GIF; verify on a clean macOS 26 VM. (Notarization makes this mostly moot but interim installs and docs must be right.)
30. `[E, week+]` **Billing**: PLAN_REGISTRY in config.py; _enforce_memory_quota reads the user's plan (not the single global env var); plan threaded into rate limits; billing.py Paddle webhook receiver (signature-verified, activated/canceled/past_due + grace) → plan transitions; tests; manage-subscription link in app.

### Phase 4 — Prove it (gates)

31. `[E, days]` **Make CI real**: open a PR (pull_request trigger has literally never fired; `gh api .../actions/runs` → 0), fix clean-runner breakage, merge, add branch protection requiring python/security-scan/distribution/macos-app. Watch macos-latest 10× billing. Also switch CI from `unittest discover` (bypasses conftest.py temp-dir isolation — will flake/write into checkout) to pytest, and fix the two docs prescribing unittest.
32. `[E, days]` **Load-test rerun against the real box**: `scripts/load_test_10k.py --users 10000 --duration 600 --concurrency 64 --base-url https://staging.<domain>` from a second machine, TLS in path, *after* the accounts/encryption branch deploys (argon2 + AES-GCM change the latency profile); include worker embedding throughput; record pass/fail JSON in docs/HOSTED_DEPLOYMENT.md.
33. `[F+E, week+]` **Usability test** (known blocker, but no protocol exists): write docs/USABILITY_TEST_PROTOCOL.md — 5–8 non-technical macOS users (UserInterviews.com ~$40–80/head), download-URL-only, tasks = install → onboard → connect → approve → cited answer; pass = roadmap DoD (≥80% unaided <10 min); rerun until pass.
34. `[E, days]` **scripts/public_launch_gate.py + go/no-go issue template** (the only existing gates are first-100/DMG-scoped) — encode §6 below as machine checks.

---

## 3. Backend Hosting Setup — Execute This

**Box**: Hetzner Robot dedicated **AX42** (Ryzen 7 PRO 8700GE, 8c, 64 GB, 2×512 GB NVMe **software RAID1** — mandatory for SQLite fsync) €46.52/mo; take **AX52** (2×1 TB) €64/mo if attachment/import headroom is wanted (10k × shard+vault+attachments will outgrow 512 GB). FSN1/HEL1 vs Ashburn by first-users' geography. Alternatives are 3–5× dearer (CCX33 €138.49, OVH $134+setup, m7g.2xlarge ~$238). **Staging**: one Hetzner Cloud CX/CPX VM (€10–25/mo), same artifacts, separate KEK + OIDC app, auto-deployed on main.

**Step-by-step** (commit everything under deploy/; record decisions in docs/HOSTED_DEPLOYMENT.md):

1. **OS**: Ubuntu 24.04 LTS minimal. `deploy/bootstrap.sh`: create non-sudo `cortex` user owning only CORTEX_SHARD_ROOT; /srv/cortex, /etc/cortex; nftables/ufw allow 22/80/443 only + Hetzner Robot firewall; SSH `PasswordAuthentication no`, `PermitRootLogin no`, keys for founder + deploy user; fail2ban; unattended-upgrades + weekly reboot window; journald persistence `SystemMaxUse=2G`. Verify with external nmap: only 22/80/443 answer.
2. **DNS**: A/AAAA for api.<domain> and staging.<domain>.
3. **TLS**: `apt install caddy`; deploy/Caddyfile:
   `api.<domain> { reverse_proxy 127.0.0.1:8766; request_body { max_size 50MB }; header Strict-Transport-Security "max-age=31536000; includeSubDomains"; header X-Content-Type-Options nosniff; header X-Frame-Options DENY; header Content-Security-Policy "default-src 'none'; form-action 'self'" }`
   uvicorn stays loopback-only, run with `--proxy-headers --forwarded-allow-ips=127.0.0.1` (otherwise every per-IP abuse limit keys on the proxy's IP). Set CORTEX_PUBLIC_BASE_URL=https://api.<domain>; confirm GET /ready → 200. CORS: explicit prod origins (default includes localhost).
4. **Services**: `deploy/systemd/cortex-api.service` — ExecStart uvicorn app.main:app --host 127.0.0.1 --port 8766 **--workers 4** (8-core box; leaves room for worker + sqlite-vec), EnvironmentFile=/etc/cortex/cortex.env, User=cortex, Restart=always, NoNewPrivileges=yes, ProtectSystem=strict, ReadWritePaths=<shard_root>, PrivateTmp=yes, MemoryMax set. `cortex-worker.service` — run_memory_worker.py --iterations 0 --interval-seconds 30, Restart=always, RestartSec=5, CORTEX_WORKER_MODE=external. Both get `OnFailure=cortex-alert@%n.service` (curl → email/webhook). Per-process math: CORTEX_RATE_LIMIT_PER_MINUTE ÷ 4 (limiter is per-process), CORTEX_STORE_CACHE_SIZE=128/worker (LRU RAM × N). Socket activation (cortex-api.socket) for near-zero-downtime restarts.
5. **Env/secrets**: deploy/cortex.env.example with every readiness var + generation command (`openssl rand -hex 32`); real values at /etc/cortex/cortex.env (root:cortex 0640); **non-zero** CORTEX_RATE_LIMIT_PER_MINUTE (e.g. 120) and CORTEX_DEFAULT_MEMORY_QUOTA; KEK per Phase-1 item 13; prefer CORTEX_KEK_FILE over env (env leaks via /proc). Give observability an explicit CORTEX_EVENTS_PATH under /var/lib/cortex (the server currently drops events.jsonl/manifest.json into CWD — visible in git status).
6. **Backups (offsite ≠ RAID1)**: Hetzner Storage Box BX21 (€10.90/mo, 5 TB, native borg). cortex-backup.{service,timer} nightly: `sqlite3 <each shard + accounts.sqlite> ".backup <staging>/"` (raw copies of live WAL files are not consistent) → tar with vault/attachment dirs + /etc/cortex **minus KEK and keyring DB** → `borg create` (repokey in escrow) → keep 7d/4w/6mo → healthchecks.io ping. Alert on missed run. One timed restore drill to staging before launch. First-month upgrade: Litestream streaming shard WALs to R2/B2 → RPO seconds.
7. **Monitoring**: GET /metrics from the existing iter_prometheus_lines; Grafana Cloud free + Alloy (scrape /metrics + node_exporter, ship journald via loki.source.journal); alerts: instance down 2m, 5xx >2%/5m, disk >80%, worker failed>0 or oldest_queued_age>15m (push gauges from run_worker_tick); UptimeRobot on /ready (1-min) + cert-expiry check; healthchecks.io dead-man on worker tick + backup; all routed to founder phone+email.
8. **Deploy**: deploy.sh per Phase-1 item 16 now; then .github/workflows/deploy.yml (rsync → /srv/cortex/releases/<sha> → venv from requirements.lock → compileall + smoke → flip symlink → restart worker, reload api → auto-rollback on failed smoke).

---

## 4. Follow-ups

### First week (post-blockers)
- **GitHub Actions deploy.yml + staging auto-deploy**, gated on backend_beta_smoke + short load-test run.
- **Readiness hard-fails when limits are off** (sharded_sqlite + rate_limit==0 or quota==0) + test.
- **Quota on every write path**: sync_*/import routes bypass _enforce_memory_quota entirely today; move enforcement into ingestion, add per-user **byte** quota, "quota reached" in sync results + test.
- **Dependency discipline**: pip-compile --generate-hashes → requirements.lock; deploy with --require-hashes; point pip-audit at the lock; dependabot.yml (pip + actions weekly); dedupe the doubled sqlite-vec line; 72h security-bump policy.
- **Incident runbook + status page**: docs/runbooks/HOSTED_INCIDENTS.md (API down, shard quick_check failure, accounts.sqlite corruption, KEK compromise, mass-5xx post-deploy) + Instatus/upptime at status.<domain>, linked from app error states.
- **Vault upload (local→hosted), idempotent**: one-shot "Upload my vault" through the batch-import path with content-hash dedupe (Migration Assistant clones + re-uploads must be safe); explicit second-device copy; "more than one Mac" FAQ; bidirectional sync documented as post-launch.
- **GET /v1/account/usage** {plan, memory_count, quota, rate tier} + render in web account page and app; make quota errors actionable.
- **Update channel made safe + on**: stamp CortexUpdateFeedURL into Info.plist at build; https-only feeds; sha256-verify downloads; honor `mandatory`; daily background check. Then **Sparkle 2** (EdDSA keys, SPM/xcodebuild conversion, signed appcast, vault-schema downgrade fails closed, rollback runbook retaining N-1).
- **Release discipline**: scripts/bump_release.py (plutil bump + CHANGELOG + tag + dirty-tree fail); app.js hydrates checksum/feed hrefs from latest.json; extend check_distribution_site.py to match manifest version.
- **Site**: Cloudflare Web Analytics (cookie-free), UptimeRobot on latest.json, stable /download 301.
- **EU/UK Art. 27 decision**: rep service (~€500–1,500/yr) or geo-scope launch to US/Canada in ToS.
- **Crash/telemetry consent**: default-OFF toggle in onboarding + ConnectionsPrivacySheet gating the future crash SDK; beforeSend scrubbing; disclose in policy.
- **Retention enforcement**: CORTEX_AUTH_AUDIT_RETENTION_DAYS / CORTEX_EVENTS_RETENTION_DAYS + worker prune pass; the policy's retention table must match reality.
- **Support desk**: shared inbox live in all docs/scripts; case log moved off the forbidden doppl-tech repo; "Accounts and sessions" triage section; site/faq.html (Gatekeeper, updates, backup/export/delete, sign-in/reset, connector re-auth); add stale "no hosted accounts" phrases to check_docs_current.py FORBIDDEN_PHRASES.
- **Batch-gate instrumentation**: in-app "Send feedback" (pre-filled mailto/issue); per-batch Tally/Google form asking exactly the gate questions; declared as official evidence in the go/no-go template.
- **Hosted MCP**: app config writer gets a "hosted" mode (mint scoped cxm_ + write https base URL into Claude/Cline/Roo configs); minted MCP tokens default to expiry + one-click rotation; Cursor/Claude Code copy-paste docs. (Remote Streamable-HTTP MCP + OAuth = post-launch.)
- **Full-fidelity takeout**: GET /v1/account/takeout (step-up auth) — current export caps imports at 100, drops attachments, and silently applies the AI-context filter, making DSAR responses false; add the sqlite_master coverage test.
- **Rate-limit survivability**: separate bigger bucket for bulk endpoints; machine-readable rate_limited body; Retry-After backoff in the Obsidian plugin and MCP stdio proxy (both currently hard-fail on 429).
- **Unit economics + spend cap**: default hosted embeddings to model2vec on-box (zero COGS, privacy-consistent — validate CPU cost in the staging load test); any metered provider gets a hard monthly cap + per-user daily budget in the worker; $/user/month table before Paddle catalog goes live.

### First month
- Failed-job gauges → Grafana alert (failed>0 for 30m) + weekly readiness-queue cron.
- docs/THREAT_MODEL.md (single-box blast radius, KMS-at-100k milestone), /.well-known/security.txt, budget external pen test (Cure53/7ASecurity/ROS, $10–25k; scope: auth flows, tenant isolation, token boundaries, SSRF via connector api_base_url, /capture surface) or start with a VDP.
- Auth-abuse monitoring: auth_events_total metrics + alerts on refresh_reuse>0 / login_fail spikes / signup velocity; 90-day IP/UA nulling; sessions UI with revoke.
- Post-auth structured errors {"code":"account_suspended"|"account_deleted"} (post-auth only, preserving enumeration posture) + defined app behavior + "local vault untouched" copy + test.
- Release channels: /downloads/{stable,beta}/latest.json, channel stamped per build, hidden beta toggle; promote by copying artifacts; Sparkle phasedRolloutInterval canary (beta 48h zero SEV0/1 → stable).
- Homebrew cask (needs notarized, stable bucket URLs): sarptandoven/homebrew-cortex + livecheck; submit to homebrew-cask when notable.
- RoPA (ICO/CNIL template) + vendor-DPA folder; customer-facing DPA only when a team tier ships.
- Trademark knockout on "Cortex" (USPTO/CIPO, class 9/42; likely crowded — consider leaning on "Doppl"); ITU filing ~$1–2k with attorney; match Paddle descriptor.
- Waitlist capture (Buttondown/Formspark) + "Beyond 100" expansion gates (SEV rate per 100 active, activation %, support SLA held).
- Abandoned-account lifecycle: purge pending_verification >7 days (frees squatted emails), 18-month dormancy warn-then-shred (never a paying user), both in the retention table.
- Passkeys (WebAuthn as a third never-auto-link method) + apple-app-site-association webcredentials for iCloud Keychain autofill (chains on final Team ID).
- MAS decision record in docs/DISTRIBUTION.md: Developer ID only — cross-app config writes, bundled Python, broad file access are sandbox-incompatible.

---

## 5. Monthly Run-Rate

| Item | 1k users | 10k users |
|---|---|---|
| Prod box (Hetzner AX42 → AX52) | €46.52 (~$50) | €64 (~$69) |
| Staging VM (Hetzner Cloud) | ~$16 | ~$16 |
| Offsite backup (Storage Box BX21) | ~$12 | ~$12 |
| Downloads CDN (Cloudflare R2, zero egress) | ~$1 | ~$5 |
| Domain (amortized) | ~$2 | ~$2 |
| Email (Postmark 10k → 50k) | $15 | $55 |
| Google Workspace (support/privacy inboxes) | $7 | $14 (2 seats) |
| Monitoring/alerting/status (Grafana Cloud + UptimeRobot + healthchecks.io + upptime, free tiers) | $0 | $0 |
| Analytics (Cloudflare free; Plausible optional) | $0–9 | $0–9 |
| Apple Developer ($99/yr) | ~$8 | ~$8 |
| Policy tooling (Termageddon $99/yr) | ~$8 | ~$8 |
| EU/UK rep (if not geo-scoped) | $45–125 | $45–125 |
| Embeddings (model2vec on-box) | $0 | $0 |
| **Fixed total** | **~$120–175** | **~$190–250** |
| Paddle (COGS, on revenue) | 5% + $0.50/txn | 5% + $0.50/txn |

One-time: entity ~$500; ToS/liability lawyer $1–2k; privacy-claims review ~$500; trademark ~$1–2k; usability incentives ~$300–600; pen test $10–25k (first-month budget line). At $20/mo Pro, ~10 subscribers cover the entire fixed run-rate at 10k users.

---

## 6. Go/No-Go Checklist (encode in scripts/public_launch_gate.py + issue template)

**Infrastructure & data safety**
- [ ] CI green on main via GitHub API; branch protection requires python/security-scan/distribution/macos-app
- [ ] Prod `https://api.<domain>/health` + `/ready` green (readiness contract passes; rate limit & quota non-zero; remaining_plaintext_credentials == 0 with enforce flag on; worker external)
- [ ] Shard-topology guard active (env-mismatch startup failure demonstrated on staging)
- [ ] Load-test JSON artifact: 10k users vs https:// prod/staging, post-accounts-branch, pass=true, ≤14 days old
- [ ] Restore-drill receipt ≤30 days: borg restore to scratch box + escrowed-KEK credential decrypt + reliability checks
- [ ] KEK escrowed in 2 offline locations (neither in the data backup); backup timer green ≥7 consecutive nights
- [ ] External monitor + phone alert verified by a forced failure; status page live
- [ ] External nmap: only 22/80/443 answer

**Signup-to-value path (QA packet, staging + prod)**
- [ ] signup → verify email (real Gmail + Outlook inboxes, not spam; mail-tester ≥9) → login → refresh; refresh-reuse revokes family
- [ ] Google + GitHub OIDC round-trip; never-auto-link matrix; Google consent screen **published** (not testing)
- [ ] Password reset, session list/revoke, account delete → crypto-shred verified; legacy invite/claim rehearsed with 5 seeded tenants; legacy-token grace policy deployed
- [ ] Abuse drill: 1k signups/min single IP → 429s, flat memory, argon2 semaphore holds
- [ ] macOS app on a clean machine: hosted login, Keychain-stored session, vault upload idempotent (run twice → no dupes), cited Ask against hosted

**Distribution**
- [ ] `spctl -a -t exec -vv /Applications/Cortex.app` accepts; Team ID = company entity
- [ ] `curl -fI https://<site>/downloads/latest.json` + DMG on CDN; checksums match manifest; binaries not in git
- [ ] Gatekeeper first-run verified on clean macOS 26 VM with the shipped instructions
- [ ] Update feed stamped in shipped Info.plist; sha256-verified update path exercised once

**Legal & money**
- [ ] privacy.html / terms.html / subprocessors.html / faq.html return 200 under the entity's name; no "zero-knowledge"/"E2E" claims anywhere
- [ ] Signup records tos_version + age attestation (DB row spot-check)
- [ ] Paddle live-mode checkout completes; webhook flips plan; cancel/past_due path tested; refund policy published
- [ ] EU decision executed (rep listed in policy, or geo-scope enforced)

**Humans**
- [ ] Usability run passed: ≥80% of 5–8 non-technical users reach a cited answer unaided in <10 min
- [ ] Support inbox round-trip case closed within SLA; incident runbook + escalation line published internally
- [ ] docs/LAUNCH_CRITICAL_PATH.md shows every external clock complete — launch = max(path), and every box above is checked
