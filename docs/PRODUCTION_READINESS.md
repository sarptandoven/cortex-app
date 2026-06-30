# Cortex Production Readiness

## Current Goal

This repo is now a local-first beta candidate: useful enough for daily dogfooding on macOS, hardened enough to protect local user memory during a controlled beta, and shaped so a hosted backend can replace local SQLite later without changing the client contract.

It is not yet a broad public launch or millions-of-users hosted system. That requires hosted auth, a managed multi-tenant database, remote MCP/OAuth, billing/quotas, observability, incident response, and notarized distribution.

## Productization Status

Cortex is currently a local-first five-tab macOS app:

```text
Model -> Sources -> Review -> Ask -> Trust
```

The product loop is import, review, ask, then verify trust:

- Sources imports user-selected exports, folders, and files. Live OAuth/API sync is planned, but not enabled.
- Review is the quality gate. New memory candidates can be approved or archived before they become trusted memory.
- Ask is the primary use surface. It returns cited memory search results in-app, while context packs and AI-tool handoffs remain secondary paths.
- Trust keeps privacy posture, agent access, redaction, backups, support bundles, and maintenance controls visible but out of the main workflow.

Memory quality is intentionally gated before expansion:

- imported records become candidate captures first;
- noisy or duplicate batches can be previewed, skipped, or undone;
- author identity for Slack/email style and preferences is conservative unless aliases are configured;
- archived or excluded sources stay out of active search and AI context;
- citation coverage, source readiness, review backlog, decisions, and open loops are product signals, not launch claims.

The remaining roadmap should stay non-overengineered:

- prove daily value with real local imports, approvals, and cited Ask results;
- keep improving importer quality, chunking, reranking, citation paths, and review ergonomics before adding automation;
- package and support a controlled macOS beta with repeatable checks, backups, and sanitized support bundles;
- add notarization, hosted downloads, update-feed policy, and formal support before external distribution;
- defer hosted accounts, live sync, remote MCP/OAuth, teams, billing, and enterprise controls until the local loop is consistently useful.

## Local Production Guarantees

- User-triggered capture only
- API-token protected local endpoints
- User-owned local vault folder with JSON records and append-only events
- Content-free local sync change feed for event ordering, counts, safe metadata, and future hosted materialization planning
- SQLite WAL mode with busy timeout and foreign keys as a rebuildable index
- Structured captures, memories, tasks, entities, topics, graph edges, and events
- Five-tab product flow: Model, Sources, Review, Ask, Trust
- Model readiness surface with memory quality, layer coverage, source readiness, decisions, and open loops
- Sources surface with import preview, duplicate-safe history, undo import, source readiness, and connector health
- Review inbox for approve/archive lifecycle, decisions, recommended actions, and open loops
- Ask surface with natural-language cited search; context packs and adaptation artifacts are secondary handoff fallbacks
- Trust surface for privacy posture, AI access, vault health, backups, backend health, and advanced diagnostics
- Simple product loop state for source import, review, memory use, and return
- Reuse tracking when context packs are copied or generated through MCP
- First-run onboarding for private vault health, first real source import, first memory approval, first Ask/use action, and backup/trust decision
- User-controllable memory behavior for review flow, pending-memory visibility, and context-pack size
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
- Run `python3 scripts/reliability_check.py` against the packaged app backend before every app package
- Run `python3 scripts/battle_test_http.py` against a fresh local backend before every app package
- Verify `GET /v1/loop` moves from review to memory use after approving pending captures
- Verify `POST /v1/loop/reuse` marks approved-memory use and updates product-loop state
- Verify export and backup files can be opened after a week of use
- Verify `POST /v1/maintenance/rebuild-index-from-vault` restores search after deleting the local index rows
- Verify `POST /v1/maintenance/repair-storage` creates a backup before cleaning stale derived rows
- Verify the vault folder can be copied to another machine and opened there
- Verify search quality on real noisy notes, not only clean synthetic examples
- Verify first-run onboarding on a clean user profile before every beta package
- Verify Model shows memory quality/source coverage, Sources previews/imports/undoes, Review approves/archives, Ask returns cited memory, and Trust shows lifecycle/redaction/support-bundle proof
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

- Supabase Auth or equivalent OAuth-backed account system
- Per-user API tokens scoped to capture/search/export
- SQLite WAL plus `sqlite-vec`, hosted on persistent storage with encrypted backups and migration scripts
- Remote MCP endpoint with OAuth/API-token auth
- Rate limits and abuse protection
- Per-user export/delete account flow
- Encrypted backups and retention policy
- Observability: request logs, error traces, database metrics, extraction latency, support bundle ingestion, release-health dashboard
- Incident runbook for data-loss, auth, extraction, and search failures

See `docs/OPERATIONAL_READINESS.md` for the local-beta operations model, severity levels, support flow, rollback, update-feed safety, and public-scale blockers.

## Launch Bar

A free public beta is credible when a nontechnical user can:

- Install the macOS app in under two minutes
- Import a real source, approve one useful memory, and ask Cortex without reading setup docs
- Understand the five tabs without opening advanced settings
- Search and recover an old decision without setup help
- Export or back up their memory without contacting support
- See whether local storage is healthy and run a backup-first repair without contacting support
- Connect at least one AI assistant through MCP or a documented proxy
- Understand exactly when Cortex reads clipboard data
