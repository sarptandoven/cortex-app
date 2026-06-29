# Cortex Architecture

## Current Prototype

The original prototype has these pieces:

- `capture.py`: macOS menu bar capture using Python
- `ingest.py`: Claude-based extraction into records, tasks, and entities
- `github_store.py`: markdown persistence in GitHub
- `redis_store.py`: Voyage embeddings plus Redis vector search
- `mcp_server.py`: local stdio MCP server
- `ui.py`: Streamlit memory chat

## Productized MVP

The MVP adds a backend service and a native macOS client while preserving the extraction schema.

```
macOS app
  global hotkey
  clipboard capture
  quick note
  recent/search/graph
      |
      v
FastAPI backend
  /v1/captures
  /v1/captures/queue
  /v1/captures/{id}/status
  /v1/imports/sources
  /v1/imports/analyze
  /v1/imports
  /v1/imports/{id}
  DELETE /v1/imports/{id}
  /v1/jobs
  /v1/maintenance/jobs/run
  /v1/inbox
  /v1/captures/{id}/approve
  /v1/captures/{id}/archive
  DELETE /v1/captures/{id}
  DELETE /v1/memories/{id}
  /v1/search
  /v1/recent
  /v1/review/today
  /v1/loop
  /v1/loop/reuse
  /v1/context-pack
  /v1/graph
  /v1/stats
  /v1/settings
  /v1/trust/summary
  /v1/audit-log
  /v1/diagnostics
  /v1/reliability/report
  /v1/support/bundle
  /v1/backups
  /v1/backups/restore-latest
  DELETE /v1/backups
  DELETE /v1/user-data
  /v1/maintenance/repair-storage
  /v1/maintenance/rebuild-search
  /v1/maintenance/rebuild-index-from-vault
  /v1/export.md
  /v1/export.json
  /mcp
      |
      v
Local vault
  manifest.json
  settings.json
  events.jsonl
  imports/*.json
  captures/*.json
  memories/*.json
  tasks/*.json
  entities/*.json
  graph_edges/*.json
  backups/*.zip
      |
      v
Rebuildable SQLite index
  FTS5
  sqlite-vec when available
  hash or opt-in OpenAI embeddings
  normalized joins

Release pipeline
  macos/package_release.sh
  Cortex-<version>-<build>.dmg
  Cortex-<version>-<build>.app.zip
  latest.json update feed
  SHA-256 checksums
  site/
    landing page
    privacy page
    downloads/latest.json
    downloadable release artifacts
```

## Hosted Backend Migration

The local SQLite storage is intentionally swappable.

| Local Beta | Hosted Beta |
|---|---|
| User-owned local vault + SQLite index | synced vault events + SQLite WAL on persistent storage |
| FTS5 keyword search | FTS5 + sqlite-vec hybrid search |
| local review status | hosted review workflow |
| API token in env | OAuth/login + scoped API tokens |
| Local vault files | backed-up user vaults or shard DB files |
| localhost API | HTTPS API |

See `docs/SQLITE_VEC_BACKEND_PLAN.md` for the full hosted backend direction.

See `docs/MEMORY_BACKEND_BLUEPRINT.md` for the layered memory model and scale path across SQLite, sqlite-vec, libSQL/Turso, Postgres/pgvector, Qdrant, and LanceDB.

See `docs/INSTALLER_AND_UPDATES.md` for the local beta installer and update-manifest pipeline.

See `docs/DISTRIBUTION.md` for the landing page, static download directory, privacy copy, and release-site QA checklist.

See `docs/RELIABILITY_HARDENING.md` for the backend health contract, repair flow, and packaged-app reliability checks.

See `docs/SIMPLE_PRODUCT_LOOP.md` for the activation and retention loop: Capture, Review, Reuse, Return.

See `docs/OPERATIONAL_READINESS.md` for support bundles, ship gates, incident playbooks, rollback flow, and local-beta support operations.

## Memory Model

### Capture

A raw piece of context from clipboard, quick note, browser extension, AI chat, meeting transcript, or import.

Lifecycle:

- `pending`: captured and extracted, awaiting user review
- `approved`: accepted as trusted context
- `archived`: removed from active memory and search
- `deleted`: permanently removed from the current SQLite index and current vault JSON records; previous backup ZIPs still require retention pruning

### Import Session

A user-confirmed source batch created from selected local files, folders, or exports. The macOS app previews imports with `/v1/imports/analyze`; confirmed imports create an import session and link captures through `import_id`.

Lifecycle:

- `running`: records are being queued or saved
- `complete`: all detected records were queued or saved
- `partial`: one or more records failed
- `empty`: no records were detected
- `deleted`: the batch was undone by deleting linked captures and derived records

### Memory

An atomic extracted item:

- claim
- decision
- event
- preference
- style
- negative
- observation
- action
- question
- summary

Each memory also has a retrieval layer:

- semantic
- episodic
- style
- decision
- preference
- negative

### Entity

A stable node in the user's life/work graph:

- person
- project
- org
- topic

### Edge

A relationship between captures, memories, tasks, and entities. This creates the node map that later powers visual graph exploration and richer retrieval.

### Trust

User-controlled policy for how memory can be shared and changed:

- review new captures before trust
- hide pending captures from AI context
- allow or block MCP agent reads
- allow or block MCP agent writes
- allow or block MCP agent exports
- redact sensitive patterns in context packs, exports, and agent payloads

Import preview is read-only and writes no capture, job, or import-session records. Import delete removes the selected batch from active vault/index state and records an import tombstone so old backups cannot silently restore it.

### Event

An audit record for user-visible lifecycle actions such as capture creation, approval, memory archive/delete, source archive/delete, settings changes, backups, and MCP tool calls.

### Product Loop

The simple product loop is backend-owned so the app and MCP agents agree on the same next step. `GET /v1/loop` returns the current Capture, Review, Reuse, Return state, and `POST /v1/loop/reuse` records when a context pack is copied or generated for an AI session.

## Local Vault

The local vault is the durable user-owned storage layer. SQLite is the fast local index.

Default packaged app path:

```text
~/Library/Application Support/Cortex/Cortex.vault/
```

The vault contains human-readable JSON records for captures, memories, tasks, entities, and graph edges, plus `settings.json`, `events.jsonl`, attachments, exports, backups, and `index.sqlite`.

`POST /v1/maintenance/rebuild-index-from-vault` clears the current user's index rows and rebuilds them from the vault records. This is the recovery path if the local index is corrupted or if a future sync process materializes records before rebuilding search.

See `docs/LOCAL_VAULT_FORMAT.md`.

### Maintenance

Local production builds expose diagnostics, backups, and search-index rebuilds. These are intentionally backend-owned because ChatGPT, Claude, the macOS app, and future browser extensions should all trust the same storage health surface.

The backend health contract proves the macOS app is talking to the expected local backend build and vault. Reliability reports combine SQLite integrity, vault layout, backup recency, search-index health, relationship health, sqlite-vec availability, and embedding-provider status. The repair endpoint creates a backup first, then cleans stale derived index rows and rebuilds search from canonical records.

Operational readiness adds a sanitized support bundle that omits captured text, memory bodies, context packs, and exported user data while preserving health checks, counts, feature flags, backup state, and safe event metadata.

## MCP Tools

The backend exposes an MCP-style JSON-RPC endpoint with these tools:

- `remember_this`
- `search_memory`
- `get_recent_context`
- `get_decisions`
- `get_open_questions`
- `get_daily_review`
- `get_product_loop`
- `build_context_pack`
- `get_about_person`
- `get_memory_stats`
- `get_memory_inbox`
- `get_memory_diagnostics`
- `get_reliability_report`
- `get_support_bundle`
- `restore_latest_memory_backup`
- `delete_memory_backups`
- `delete_all_user_data`
- `repair_memory_storage`
- `forget_memory`
- `delete_memory_capture`
- `rebuild_index_from_vault`

The local endpoint is enough for beta testing and a stdio proxy. Production ChatGPT/Claude connectors should add the full remote MCP transport and OAuth flow.

## Privacy Model

- No background capture in the MVP
- User-triggered capture only
- Source retained on every memory
- Review inbox for new captures
- Archive endpoint for full captures, hard-delete endpoints for individual memories/full captures/backups/all local user data, and latest-backup restore
- Export to markdown and JSON
- Local diagnostics and backups
- Sanitized support bundle for operational triage
- Future: sensitive source blocklist, hosted account deletion, per-source retention rules
