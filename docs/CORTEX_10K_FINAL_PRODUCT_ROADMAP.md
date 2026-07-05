# Cortex — Final Product Roadmap for 10,000 Users

Authoritative, honest roadmap from the current codebase to a product that 10,000
real users can use fully and completely. Every workstream lists its **goal**,
**current state** (grounded in the actual code, not aspiration), **gaps**,
**concrete tasks**, and **acceptance criteria (Definition of Done)**. Status
tags: ✅ done · 🟡 partial · ⬜ not started.

This supersedes and consolidates the scattered planning notes
(`HOSTED_SCALE_ROADMAP.md`, `MVP_ROADMAP.md`, `PRODUCTION_READINESS.md`,
`OPERATIONAL_READINESS.md`, `CONNECTOR_COVERAGE_READINESS.md`,
`SIMPLE_PRODUCT_LOOP.md`, `cortex_unique_value_10k_roadmap.txt`).

---

## 0. What "final product for 10,000 users" means

Cortex is the private, self-updating memory + adaptation layer between a
person's real data and every AI tool. The core loop:

> **Connect sources → sync automatically → review what was learned → ask with
> citations → expose approved memory to AI tools over MCP.**

"Final for 10k" = that loop works reliably, safely, and simply for 10,000
paying/active users, with per-tenant isolation, fair use, live data connectors,
a distributable signed app, hosted operation, and the legal/support scaffolding
to run it as a real service. It does **not** require web-scale infrastructure
(millions of users); we deliberately size to 10k.

### Product-level Definition of Done (the whole thing)
1. A new user can sign up / be provisioned, connect ≥3 real sources over OAuth,
   and get useful cited answers within 10 minutes, with no manual data pasting.
2. Sync is automatic, incremental, resumable, and never loses or duplicates data.
3. Retrieval quality is measured and regression-gated (per-layer eval ≥ target).
4. Every AI-tool answer is cited, permissioned, and revocable; nothing leaves
   without the user allowing it.
5. Multi-tenant isolation, per-user quotas, and rate limiting are enforced and
   proven; one user cannot degrade another.
6. The macOS app is Developer-ID signed **and notarized**, auto-updates, and
   reports crashes.
7. Hosted backend runs with monitoring, alerting, backups, deletion guarantees,
   and an incident runbook; `/ready` passes for the chosen runtime.
8. Privacy policy, terms, data-deletion/export flows, and a support channel exist.
9. CI gates every release on the full test + eval + packaging + security suite.

---

## 1. Current state audit (honest)

**Solid / done:**
- Local-first macOS app + local FastAPI/standalone backend; 555 backend tests green.
- Layered memory model (semantic, episodic, style, decision, preference,
  negative, procedural) with sectors, provenance, temporal validity, supersession.
- Local vault as source of truth + rebuildable SQLite/FTS + `sqlite-vec` vectors.
- Hybrid retrieval (FTS + vector + recency/importance/trusted-source rerank),
  citations, abstention, retrieval + adaptation eval harnesses.
- 13 connectors with real sync code: Obsidian, GitHub, Slack, Gmail, Google
  Drive, Notion, Outlook, Linear, Jira, Readwise, Raindrop, Zotero, Calendar.
- OAuth flows for Google + "managed" providers (Notion/Outlook/etc.); token
  connectors for the rest. Partial-failure cursor safety, backoff, dedupe.
- MCP server with scoped tools + Trust controls; scoped `cxm_`/`cxa_` tokens.
- **Hosted management plane (this iteration):** user registry + provisioning
  (`/v1/admin/users`), tenant isolation, suspension, LRU store cache, O(1) token
  auth, hosted worker auto-discovery, per-user rate limiting + memory quotas.
- Release packaging (DMG/ZIP/checksums/update manifest), codesign verification,
  distribution-site checks, ops-readiness checks.

**Partial 🟡:**
- Live connector coverage: real sync exists for ~13 sources, but the catalog
  lists many as `live_status: planned` (Google Docs/Chat, Teams, Microsoft 365,
  Contacts, Zoom, …). OAuth token refresh + secret handling exist but not every
  provider is production-hardened.
- Hosted runtime: management plane works on sharded SQLite, but the readiness
  gate requires Postgres/pgvector which is **not built** (`runtime_storage_status`
  returns `sqlite`). So "hosted ready" is unachievable as currently gated.
- UI: simplified to Model/Review/Ask + Connections & Privacy, but not validated
  with non-technical users; onboarding not connect-first end-to-end.
- Quotas: memory cap enforced at interactive capture only (not sync/import).

**Not started ⬜:**
- Accounts/auth for end users (signup, login, sessions) — only operator-token
  provisioning exists.
- Developer-ID **notarization**, auto-update channel, crash reporting.
- Hosted deployment (infra, CI/CD, monitoring, backups, on-call).
- Billing, legal (privacy policy/ToS), support operations.
- Encryption at rest for hosted vaults; deletion-receipt / tombstone fan-out.

---

## 2. Workstreams

### A. Core product loop reliability 🟡
**Goal:** the connect→sync→review→ask→MCP loop is bulletproof for every user.
- ✅ Automatic Obsidian + connector sync, review gating, cited Ask, MCP retrieval.
- ✅ Scheduled-sync data-loss safety (partial-failure cursors; no-archive invariant).
- ⬜ **Import/sync at scale:** streaming import for huge exports; dedupe by source
  id + content hash across all paths; chunking long docs before extraction;
  resumable/retryable imports with progress.
- ⬜ **Extraction quality pipeline:** move extraction fully async at volume;
  confidence scoring; duplicate-memory merging; source-reliability weighting;
  editable extracted memories before approval.
- **DoD:** import a 10k-record export without blocking, with 0 dupes and a
  visible progress/failure UI; every capture path is idempotent.

### B. Memory & retrieval quality (the actual value) 🟡
**Goal:** Cortex is measurably better than search; agents get the *right* context.
- ✅ Layered model, temporal validity, trusted-source rerank, citations, abstention.
- 🟡 Eval harnesses exist (retrieval_eval, adaptation_eval) but the corpus is small.
- ⬜ **Grow the eval corpus** to a realistic multi-source, multi-project fixture
  (100s of cases) with per-layer metrics (precision/recall@k, citation accuracy,
  abstention correctness, cross-project no-leak) and a **CI regression gate**.
- ⬜ **Reranking**: deterministic first; add an LLM/cross-encoder reranker only
  after evals prove lift; keep it optional and measured.
- ⬜ **Embeddings**: production default (OpenAI/Voyage or a strong local model),
  dimension management, re-embed/backfill jobs, freshness.
- ⬜ **Contradiction detection + stale handling**: surface conflicting memories,
  auto-supersede, "this is well-known / weak / unknown" confidence in answers.
- ⬜ **Adaptation profile v2**: style, preferences, decisions, constraints,
  relationships, procedures as a real "model of the person" with coverage/
  freshness metrics and per-surface profiles (ChatGPT/Claude/Cursor/browser).
- **DoD:** a documented eval suite runs in CI and blocks regressions; answers
  cite the right facts across layers with no cross-project leakage.

### C. Connectors / live data ingestion 🟡
**Goal:** users connect their real accounts; data flows in automatically.
- ✅ 13 connectors with real incremental sync, backoff, dedupe, citations.
- 🟡 OAuth exists for Google/managed providers; pending-state now persisted.
- ⬜ **Harden each live connector to production:** token refresh + revocation UI,
  scope minimization, per-connector partial-failure/full-snapshot tests,
  rate-limit compliance with each provider, pagination correctness at volume.
- ⬜ **Ship the "planned" high-value connectors** the catalog advertises: finish
  Microsoft 365/Teams, Google Docs/Chat, Contacts; add ChatGPT/Claude export
  ingestion as first-class; browser history/bookmarks; iMessage/WhatsApp where
  OS-permissible.
- ⬜ **Connector platform**: a uniform connector contract (OAuth, cursor, stable
  external id, citation URL, health, pause/resume-without-delete), a connector
  health dashboard, and per-connector error budgets.
- ⬜ **Optional browser extension** for capture + web research trails.
- **DoD:** a user connects Gmail + Drive + Notion + Slack + Calendar over OAuth,
  sees them sync automatically with visible health, and can pause/revoke each.

### D. Hosted backend & scale ✅🟡
**Goal:** run 10k isolated tenants reliably.
- ✅ Sharding (local/user/bucket), control-plane token index, provisioning,
  isolation, suspension, LRU cache, O(1) auth, worker auto-discovery, rate
  limiting, quotas.
- ⬜ **Decision: runtime for 10k.** Sharded SQLite is sufficient for 10k on a
  single/large instance. Either (a) **re-calibrate the readiness gate** to accept
  a sharded-SQLite hosted tier (recommended for 10k), or (b) build the
  **Postgres/pgvector runtime** (needed only for multi-instance / multi-device
  sync / larger scale). Pick (a) for 10k; treat (b) as post-10k.
- ⬜ **End-user accounts**: signup/login/session (or SSO), map account → provision
  → shard; self-serve onboarding instead of operator-token-only.
- ⬜ **Multi-instance readiness** (only if scaling out): shared rate-limit/quota
  store, external worker queue, sticky-free routing.
- ⬜ **Deletion guarantees**: account delete/export flows, tombstone propagation,
  deletion receipts, backup expiry so restores can't resurrect deleted data
  (local tombstones exist; hosted fan-out does not).
- **DoD:** provision→use→suspend→delete works end-to-end for a tenant; `/ready`
  passes for the chosen runtime; a load test sustains 10k users' steady traffic.

### E. Security, privacy & trust 🟡
**Goal:** users trust Cortex with their most sensitive data.
- ✅ Token-bound identity (not header-spoofable), scoped tokens, Trust controls,
  metadata secret sanitization, content-free support bundles, browser-capture
  token-leak fixes.
- ⬜ **Encryption at rest** for hosted vaults/artifacts (per-user keys / KMS).
- ⬜ **Secrets management**: OAuth client secrets, refresh tokens in a real secret
  store, not env/plaintext; rotation.
- ⬜ **Audit + receipts**: signed audit log; per-answer "what memory was used,
  from where, why allowed, what was withheld" receipts; token-revocation UI.
- ⬜ **Abuse/security review**: dependency scanning, SAST, pen-test of the hosted
  surface, fail-closed hosted config assertions, per-connector data-scope review.
- ⬜ **Redaction**: stronger PII redaction before AI handoff; per-source
  sensitivity policies and warnings.
- **DoD:** an external security review passes; users can see, export, and delete
  everything; nothing sensitive is stored in plaintext server-side.

### F. UI/UX & onboarding ⬜🟡
**Goal:** a non-technical user understands and succeeds in <5 minutes.
- 🟡 App simplified to Model/Review/Ask + Connections & Privacy.
- ⬜ **Connect-first onboarding**: first run = pick sources → OAuth → first sync →
  first cited answer; hide MCP/vault/backend/shard vocabulary.
- ⬜ **Import wizard + preview**, model-coverage dashboard, source-health view,
  review-queue ergonomics, clean empty/loading/error states everywhere.
- ⬜ **Ask UX**: cited answers with expandable sources, "why selected", abstain
  messaging, follow-ups.
- ⬜ **Trust presets** finalized (Private / Read-only / Can-save / Advanced) with
  plain language; per-connector permission screens.
- ⬜ **Non-technical usability testing** (5–8 users) with a success metric
  (install → useful retrieval) and iteration until it passes.
- **DoD:** ≥80% of test users reach a useful cited answer unaided in <10 min.

### G. Distribution & release ⬜🟡
**Goal:** anyone can install and stay updated safely.
- 🟡 DMG/ZIP/checksums/update manifest + local codesign verification exist.
- ⬜ **Developer-ID signing + notarization** (`notarytool`) so Gatekeeper opens
  the app without Control-click workarounds. *(No notarization tooling exists yet.)*
- ⬜ **Auto-update channel** (Sparkle or equivalent) wired to the update manifest;
  signed updates; rollback that preserves the memory folder.
- ⬜ **Crash reporting** (opt-in) + symbolication.
- ⬜ **Hosted download site** (e.g., trydoppl.com/downloads) with the artifacts,
  or App Store track (`package_app_store.sh` exists — decide MAS vs. Developer ID).
- **DoD:** a fresh macOS profile installs from the DMG, opens without a Gatekeeper
  block, auto-updates to a new build, and rolls back with memory intact.

### H. Observability & operations ⬜
**Goal:** we know it's healthy and can fix it fast.
- 🟡 Health/readiness endpoints, hosted job health, ops-readiness script.
- ⬜ **Metrics/tracing/logging** (structured logs, request/latency/error metrics,
  per-connector sync metrics, queue depth, per-tenant usage) behind
  `CORTEX_OBSERVABILITY_ENABLED`.
- ⬜ **Alerting + dashboards** (queue backlog, failed jobs, auth errors, connector
  error budgets, disk, p95 latency).
- ⬜ **Backups + restore drills** for hosted shards + control DB; retention;
  tested restore runbook.
- ⬜ **Incident runbook + on-call**; SLOs/error budgets.
- ⬜ **CI/CD**: deploy pipeline, migrations, blue/green or staged rollout.
- **DoD:** a simulated connector outage + a failed-job spike + a shard restore are
  all detected, alerted, and resolved via the runbook.

### I. Compliance, legal & support ⬜
**Goal:** we can legally and operationally run a data product.
- ⬜ Privacy policy, Terms of Service, DPA, data-processing inventory.
- ⬜ GDPR/CCPA data export + deletion flows (self-serve), sub-processor list,
  data-retention policy, consent for any telemetry.
- ⬜ Support channel (email/Discord/issue tracker), SLA, first-100/first-1k
  playbooks, status page.
- **DoD:** published policies + working export/delete + a monitored support inbox.

### J. Testing, QA & quality gates 🟡
**Goal:** every release is provably safe.
- ✅ 555 backend tests; retrieval/adaptation evals; codesign/ops/distribution checks.
- ⬜ **HTTP-layer multi-tenant integration coverage** expanded (isolation, quotas,
  rate limits, provisioning, suspension) — started this iteration.
- ⬜ **Load/soak test** at 10k-user profile; connector contract tests per provider;
  clean-profile install QA automation; migration tests.
- ⬜ **CI gate** bundling: full backend suite + evals (regression-gated) + macOS
  build + notarization dry-run + distribution/manifest + security scan.
- **DoD:** the merge gate runs all of the above and blocks on any regression.

### K. AI / adaptation & agent layer (differentiator) 🟡⬜
**Goal:** AI tools behave like a high-fidelity extension of the user.
- 🟡 MCP tools: search, ask, style profile, project context, procedure, decision
  history exist; adaptation profile v1 exists.
- ⬜ **Context Assembly Engine**: given a task + surface, build the right token-
  budgeted, permissioned, cited context pack (intent → project → recency →
  trust → constraints).
- ⬜ **Permissioned MCP runtime**: per-tool scopes, audit per query, citations in
  MCP responses, resources (style/project/decisions/open-loops/trusted-facts).
- ⬜ **Computer-use worker layer (post-core)**: safe task execution with approval
  checkpoints, audit trail, "act like me" constraints — only after memory quality
  + trust are proven.
- **DoD:** connected Claude/ChatGPT/Cursor get scoped, cited, permissioned context
  automatically and measurably act more like the user.

### L. Billing & accounts (if commercial) ⬜
**Goal:** sustainable operation.
- ⬜ Plans/tiers mapped to the registry `plan` + quotas already scaffolded;
  billing provider (Stripe), usage metering, upgrade/downgrade, dunning.
- **DoD:** a user can subscribe, hit plan limits, and upgrade.

---

## 3. Phased sequencing (with exit criteria)

**Phase 1 — Trustworthy single-tenant beta (closest to done).**
Finish B (eval gate + embeddings), F (connect-first onboarding), G
(notarization + auto-update), and A (import scale/dedupe).
*Exit:* a non-technical user installs a notarized app, connects 3 sources via
OAuth, and gets cited answers; evals gate CI.

**Phase 2 — Hosted multi-tenant for 10k.**
Decide runtime (re-calibrate readiness for sharded-SQLite tier), add end-user
accounts/auth, deletion/export guarantees, observability + alerting + backups,
CI/CD, and per-connector production hardening.
*Exit:* provision→use→suspend→delete works hosted; monitoring + backups + runbook
exist; a 10k-profile load test passes.

**Phase 3 — Security, compliance, and scale-out readiness.**
Encryption at rest, secrets store, audit receipts, external security review,
legal/privacy/support, billing. Optional Postgres/pgvector + multi-instance only
if 10k-on-one-instance is exceeded.
*Exit:* security review passed; policies published; export/delete verified;
service is operable by a small team.

**Phase 4 — Adaptation & agent differentiation.**
Context Assembly Engine, permissioned MCP runtime v2, adaptation profile v2,
then (guarded) computer-use.
*Exit:* connected AI tools act like the user with cited, permissioned context.

---

## 4. Top risks
1. **Retrieval quality is unmeasured at scale** → build the eval gate before
   scaling users (Phase 1).
2. **Notarization/distribution missing** → users can't install cleanly; blocks any
   real launch (Phase 1).
3. **Hosted readiness gate assumes Postgres** → a 10k-on-SQLite launch reads as
   "not ready"; resolve the runtime decision explicitly (Phase 2).
4. **Server-side plaintext secrets/vaults** → a breach is catastrophic; encrypt +
   secret-store before hosted GA (Phase 3).
5. **Connector provider limits/ToS** → per-provider rate/scope compliance and
   review before enabling live sync broadly (Phase 2).

## 5. Explicitly out of scope for 10k (avoid overengineering)
- Postgres/pgvector rewrite (sharded SQLite is sufficient for 10k on one box).
- Web-scale/multi-region infra, sharded global queues, CDN edge compute.
- Computer-use automation before memory quality + trust are proven.
- Mobile/Windows/Linux clients (macOS-first for 10k).

## 6. Progress log — 2026-07-02

Shipped this session (all with regression tests; full backend suite green):
- **A/core-loop:** fixed silent data loss in extraction — large flat captures were capped
  at the top 40 candidates (~80% loss); LLM path truncated at 40k chars; JSONL imports
  abandoned a whole file on one corrupt line. All fixed + `docs/PIPELINE_SILENT_LOSS_AUDIT.md`.
- **B/retrieval:** confidence now affects ranking (was inert); per-layer precision/recall
  metrics + CI floors; adversarial safety corpus (abstention, temporal-supersession,
  cross-project no-leak).
- **C/connectors:** Notion OAuth token refresh (accounts no longer lock out after ~30d) +
  fixed sync wiping refresh material.
- **E/security + J/QA:** security-scan CI gate (bandit + pip-audit), teeth-verified.
- **H/observability:** metrics + structured-event foundation behind `CORTEX_OBSERVABILITY_ENABLED`
  with an admin `/v1/metrics` surface; job-health p50/p95/p99 latency + queue-age percentiles.
- **F/onboarding:** first-run recovery (retry/show-log) when the memory engine stalls.
- **I/compliance:** self-maintaining DB upgrade-path test; hosted deletion isolation +
  token-revocation HTTP test (GDPR/CCPA delete-and-stay-gone).

Confirmed already-solid (verified, not rebuilt): delete-across-restore tombstone preservation,
retrieval isolation guards, hosted multi-tenant plane, resumable imports, per-connector dedup.

Blocked on external inputs before GA: production embeddings (API key), Developer-ID
notarization (Apple creds), crash reporting (DSN), at-rest encryption/KMS, legal docs.
Open product decisions (see `PIPELINE_SILENT_LOSS_AUDIT.md` and gap-scan): editable-memories
UX, source-reliability weighting, LLM reranker, per-source sensitivity tiers, audit-receipt
schema, per-parser import scale-bound handling.
