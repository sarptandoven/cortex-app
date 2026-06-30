# Cortex Production Readiness

## Current Goal

This repo is now a local-first beta candidate: useful enough for daily dogfooding on macOS, hardened enough to protect local user memory during a controlled beta, and shaped so a hosted backend can replace local SQLite later without changing the client contract.

It is not yet a broad public launch or millions-of-users hosted system. That requires hosted auth, a managed multi-tenant database, remote MCP/OAuth, billing/quotas, observability, incident response, and notarized distribution.

## Productization Status

Cortex is currently a local-first macOS app with three primary surfaces:

```text
Home -> Review -> Ask
```

Connections and privacy controls live in a secondary sheet. The product loop is connect, review, ask, then verify privacy:

- Home shows connection state, memory readiness, and the next action.
- Review is the quality gate. New memory candidates can be approved or archived before they become trusted memory.
- Ask is the primary use surface. It returns cited memory search results in-app, while fallback handoffs remain secondary Advanced tools.
- Connections & Privacy manages MCP AI tools, Obsidian/local notes, sync health, privacy posture, redaction, backups, support bundles, maintenance controls, and Advanced/Fallback import only when a source cannot connect directly yet.

Memory quality is intentionally gated before expansion:

- connected-source records become candidate captures first;
- noisy or duplicate batches can be previewed, skipped, or undone;
- author identity for Slack/email style and preferences is conservative unless aliases are configured;
- archived or excluded sources stay out of active search and AI context;
- citation coverage, source readiness, review backlog, decisions, and open loops are product signals, not launch claims.

The remaining roadmap should stay non-overengineered:

- prove daily value with real connected sources, approvals, and cited Ask results;
- keep improving connector quality, chunking, reranking, citation paths, and review ergonomics before adding automation;
- package and support a controlled macOS beta with repeatable checks, backups, and sanitized support bundles;
- add notarization, hosted downloads, update-feed policy, and formal support before external distribution;
- defer hosted accounts, remote MCP/OAuth, teams, billing, and enterprise controls until the local MCP/Obsidian loop is consistently useful.

## Local Production Guarantees

- User-approved MCP and Obsidian sync only; no background app crawling
- API-token protected local endpoints
- User-owned local vault folder with JSON records and append-only events
- Content-free local sync change feed for event ordering, counts, safe metadata, and future hosted materialization planning
- SQLite WAL mode with busy timeout and foreign keys as a rebuildable index
- Structured captures, memories, tasks, entities, topics, graph edges, and events
- Three-surface product flow: Home, Review, Ask
- Connections & Privacy sheet for MCP tools, Obsidian/local notes, privacy, backup, and advanced diagnostics
- Home readiness surface with memory quality, source readiness, decisions, and next action
- Connected-source state with direct sync health, duplicate-safe history, source readiness, and connector health
- Review inbox for approve/archive lifecycle, decisions, recommended actions, and open loops
- Ask surface with natural-language cited search; Advanced handoff artifacts remain secondary fallbacks
- Simple product loop state for connection, review, memory use, and return
- Reuse tracking when Advanced handoffs are copied or generated through MCP
- First-run onboarding for private vault health, first MCP or Obsidian connection, Review, and Ask; backup/privacy controls stay in Connections & Privacy instead of blocking setup
- User-controllable memory behavior for review flow, pending-memory visibility, and Ask memory depth
- Active search and graph exclude archived captures and memories
- JSON and Markdown export
- Full local vault zip backup endpoint
- Storage diagnostics with SQLite quick check, FTS health, relation health, vault file counts, event counts, and index status
- Versioned backend health contract with required feature flags and vault-path handshake
- User-facing reliability report with backup recency, storage checks, recommended actions, and copyable support context
- Sanitized support bundle for operational triage without raw memory content
- Local operational readiness gate covering docs, tests, retrieval eval, build, distribution, manifests, support bundle, and optional live backend contracts
- Backup-first storage repair endpoint for stale search rows and relationship drift
- Search-index rebuild maintenance endpoint
- Vault-to-index rebuild maintenance endpoint
- Legacy `cortex.db` migration into the new vault layout
- Repeatable macOS release packaging with DMG, ZIP, checksums, and update manifest
- In-app update feed checker for local or hosted `latest.json`
- Static landing page with DMG/ZIP download links, install steps, privacy page, and release metadata hydration
- Distribution site preparation script that copies packaged artifacts into `site/downloads/`
- Standard-library unit tests for storage lifecycle
- HTTP battle-test script for the running backend

## Pre-Hosted Backend Checklist

- Dogfood with at least 1,000 captures across clipboard, notes, ChatGPT, Claude, docs, and meetings
- Run `python3 -m unittest discover backend/tests` before every app package
- Run `python3 scripts/retrieval_eval.py` before every app package
- Run `python3 scripts/backend_beta_smoke.py` before inviting beta users; it must pass the Obsidian/MCP -> Review -> Ask loop with sockets blocked
- Run `python3 scripts/first100_live_smoke.py` against the launched packaged app before inviting beta users
- Run `python3 scripts/reliability_check.py` against the packaged app backend before every app package
- Keep `python3 scripts/battle_test_http.py` as a deeper backend lifecycle stress test, not the primary first-100 user loop
- Verify `GET /v1/loop` moves from review to memory use after approving pending captures
- Verify `POST /v1/loop/reuse` marks approved-memory use and updates product-loop state
- Verify export and backup files can be opened after a week of use
- Verify `POST /v1/maintenance/rebuild-index-from-vault` restores search after deleting the local index rows
- Verify `POST /v1/maintenance/repair-storage` creates a backup before cleaning stale derived rows
- Verify the vault folder can be copied to another machine and opened there
- Verify search quality on real noisy notes, not only clean synthetic examples
- Verify first-run onboarding on a clean user profile before every beta package
- Verify Home shows memory/source readiness, Connections & Privacy shows MCP or Obsidian setup and privacy controls, Review approves/archives, and Ask returns cited memory
- Verify copied MCP config points at the bundled proxy script inside the app
- Verify `macos/package_release.sh` produces a valid DMG, ZIP, checksums, and `latest.json`
- Verify `scripts/validate_update_manifest.py` passes against the generated `latest.json`
- Verify `scripts/prepare_distribution_site.py` prepares `site/downloads/` from the release artifacts
- Verify the landing page download links, privacy page, and mobile layout
- Verify the in-app update checker can read the generated feed
- Verify `python3 scripts/export_support_bundle.py --mode live` creates a content-free support bundle
- Verify `python3 scripts/ops_readiness_check.py --refresh-site` passes before inviting testers
- Verify the inbox is still understandable with 100+ pending captures
- Verify the graph remains useful and responsive with thousands of nodes
- Add app notarization and signed installer before external distribution
- Add a user-facing "Open Vault Folder", "Change Vault Location", and "Restore Backup" flow

## Hosted Backend Requirements

- OAuth-backed account system
- Per-user API tokens scoped to read/write/export/maintenance/destructive actions
- Hosted FastAPI service backed by Postgres plus `pgvector`
- Hosted MCP endpoint with OAuth/API-token auth
- Hosted readiness gate that blocks non-local shard modes until scoped tokens, HTTPS base URL, sync signing, non-hash embeddings, pgvector, external workers, and observability are configured
- External worker process for queued memory extraction/vector jobs, currently represented by `scripts/run_memory_worker.py`
- Rate limits and abuse protection
- Per-user export/delete account flow
- Encrypted backups and retention policy
- Observability: request logs, error traces, database metrics, extraction latency, support bundle ingestion, release-health dashboard
- Queue observability: processed count, queued lag, failed-job count, and recent failure reasons from worker output or diagnostics
- Incident runbook for data-loss, auth, extraction, and search failures

See `docs/OPERATIONAL_READINESS.md` for the local-beta operations model, severity levels, support flow, rollback, update-feed safety, and public-scale blockers.

## Launch Bar

A free public beta is credible when a nontechnical user can:

- Install the macOS app in under two minutes
- Connect MCP or Obsidian, approve one useful memory, and ask Cortex without reading setup docs
- Understand Home, Review, Ask, and Connections & Privacy without opening advanced settings
- Search and recover an old decision without setup help
- Export or back up their memory without contacting support
- See whether local storage is healthy and run a backup-first repair without contacting support
- Connect at least one AI assistant through MCP or a documented proxy
- Understand exactly when Cortex reads clipboard data
