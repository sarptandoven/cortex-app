# Cortex MVP Roadmap

## Product Thesis

Cortex is the shared memory layer for every AI assistant a person uses. The first product should make one promise extremely well:

> Connect your trusted life and work context once. ChatGPT, Claude, and other AI tools can retrieve it later with sources.

The current repo proves the core idea: sync source data, extract structured memory, store it, search it, and expose it to AI through MCP. The MVP turns that prototype into a restrained macOS product with Home, Review, Ask, a Connections & Privacy sheet, a local-first backend, and a stable sync/API contract.

## Target User

Start with AI power users who already use ChatGPT, Claude, Cursor, Slack, Notion, docs, and meetings every day. They feel the pain of repeating context and losing decisions across tools.

## MVP Magic Moment

1. The user connects MCP AI tools or an Obsidian vault.
2. Cortex syncs source records locally, preserves citations, and queues extraction automatically.
3. The user approves useful memory candidates in Review and archives noise.
4. The user asks Cortex a question and sees cited memory.
5. Connected AI tools can retrieve approved memory through MCP with sources.

## Repository Status - 2026-06-30

### Complete Now

- The macOS app has the intended local-beta shell: Home, Review, Ask, and a secondary Connections & Privacy sheet for source setup, MCP tools, privacy, backup, export, support, and diagnostics.
- The local backend has capture, extraction, review, memory storage, search, ask, graph, diagnostics, backup/restore, export/delete, support bundle, and MCP endpoints.
- Source-account sync is implemented locally with catalog/readiness APIs, sync cursors, duplicate-safe `source_account_id + external_id` identity, citations, and review-first capture processing.
- MCP includes the connected-source tools `list_source_connectors`, `connect_source_account`, `sync_source_records`, and `sync_connected_sources`, plus focused retrieval tools `get_style_profile`, `get_project_context`, and `get_procedure`.
- Obsidian/local notes are the first real native source path: Swift chooses and remembers the vault folder, while backend connector code scans Markdown/text notes, cleans Obsidian markup, preserves `file://` citations, and resyncs saved vaults.
- The local memory model includes procedural memory, sectors, source type/provenance, validity windows, supersession, and lightweight memory relations; retrieval filters expired, future-valid, and superseded memory.
- Local beta trust and operations primitives exist: scoped local tokens, review-first defaults, redaction/export controls, backups, support bundles, reliability/ops checks, package scripts, update manifests, and distribution-site scripts.

### Remaining For First 100

- Complete the clean-profile hands-on QA loop from the packaged DMG: install, first-run setup, MCP or Obsidian sync, Review approve/archive, cited Ask, backup/export, support bundle, update, and rollback.
- Run the release gates against the actual generated release directory: unit tests, retrieval eval, backend smoke, reliability check, battle test, package validation, checksum validation, update manifest validation, distribution-site preparation/checks, and ops readiness.
- Keep Gmail, Notion, Slack, Drive, Calendar, GitHub, Mail, Messages, browser, and similar connectors out of the primary setup path until they complete real source-account sync with data/cursor evidence.
- Continue dogfooding extraction quality, citation quality, review ergonomics, and noisy-source behavior with real notes and AI-tool records before widening invites.

### Deferred For First 10k

- Hosted accounts, login/session management, token issuance UI, organization membership, billing, quotas, public telemetry, and incident operations.
- Hosted FastAPI deployment backed by Postgres plus `pgvector`, background workers, hosted MCP, object storage for vault/export artifacts, hosted deletion/export receipts, and observability.
- The repo now has an honest hosted readiness contract: non-local shard modes block until scoped API tokens, HTTPS public URL, sync signing, a Postgres database URL, non-hash embeddings, pgvector, external workers, and observability are configured.
- Live OAuth/API connectors for Gmail, Notion, Slack, Drive/Docs, GitHub, calendar, mail, browser, and work-tool sources.
- Additional graph/entity/correction MCP tools, team memories, project sharing, automatic updates, hosted cloud backup, and enterprise policy.

## Build Phases

### Phase 0: Local Product Beta

Goal: one developer can run Cortex locally and use the macOS app every day.

- FastAPI backend with capture, extraction, memory storage, search, graph, and MCP-style endpoints
- SQLite database with full-text search for zero-cost local use
- SQLite schema with review status, normalized topics/entities, source graph edges, and lifecycle events
- Native macOS app with Home, Review, Ask, Connections & Privacy, local backups, and advanced diagnostics
- Stage 1 source ingestion through MCP AI tools, Obsidian vault sync, and source-account sync APIs
- Advanced/Fallback import retained for unsupported services, migration, tests, and support recovery, not as the primary setup path
- Health diagnostics, local backups, and search-index maintenance
- Regression and HTTP battle tests before packaging
- Deterministic extraction fallback so the product works without model keys
- Optional Claude extraction when `ANTHROPIC_API_KEY` is present

### Phase 1: Hosted 10k-User Platform

Goal: non-technical users can sign up and use Cortex without a local backend while preserving the same review, citation, and MCP contracts.

- Hosted FastAPI service
- Account auth and scoped user/API tokens
- Postgres with `pgvector` as the default hosted memory store
- Background workers for source sync, extraction, embedding, and deletion jobs
- Hosted MCP endpoint for approved-memory retrieval
- Web dashboard for inbox review, delete/export, and source history
- Hosted export/delete account flow with per-user retention policy and audit logs

Local beta remains SQLite/vault-first. Hosted scale should use FastAPI plus Postgres/pgvector unless retrieval evals or latency prove another vector service is needed.

### Phase 2: AI Tool Connectors

Goal: Cortex works inside the AI tools users already use.

- Remote MCP endpoint with OAuth/API-token auth
- Claude custom connector support
- ChatGPT Apps/connector support
- Stdio MCP proxy for Claude Desktop and local agent tools
- Browser extension for ChatGPT/Claude conversation capture

### Phase 3: Trust and Control

Goal: make users comfortable giving Cortex personal context.

- Memory inbox with approve/edit/delete
- Source citations on every memory
- One-click forget
- Export to markdown/JSON
- User-owned GitHub sync as an advanced option
- Sensitive source pause list

### Phase 4: Growth Loops

Goal: make Cortex valuable enough that users invite teammates and collaborators.

- Project brains
- Shared team memories
- Live Slack/Notion/Gmail sync through source-account connectors
- Meeting transcript ingestion
- Decision and task digests

## What Not To Build First

- Full Notion replacement
- Mobile app
- Ambient screen recorder
- Wearables integration
- Complex graph visualization as the main surface
- Heavy enterprise admin

## MVP Success Criteria

- A user can connect MCP or Obsidian in under five minutes.
- Synced memory appears in Review automatically.
- A user can ask approved memory from the app and see citations.
- The backend returns structured memories with source metadata.
- The API contract is ready for ChatGPT, Claude, and other MCP-compatible tools.
