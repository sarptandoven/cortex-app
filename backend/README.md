# Cortex Backend

FastAPI backend for the Cortex MVP.

It provides:

- capture ingestion
- source import preview, duplicate-safe history, and batch undo for user-selected exports, folders, and files
- source account registry and sync cursor state for future live connectors
- content-free sync change feed for hosted-sync materialization work
- structured memory extraction
- layered memory metadata for semantic, episodic, style, decision, preference, and negative memory
- user-owned local vault persistence
- SQLite/FTS rebuildable local index
- full-text search
- optional `sqlite-vec` vector search with offline hash embeddings by default and opt-in OpenAI embeddings
- review inbox
- capture approve/archive/delete lifecycle
- daily review with recommended actions
- model-building loop for signal, review, access, and return
- cited personal adaptation profiles for ChatGPT, Claude, Cursor, and other assistants
- user settings for review behavior, pending-memory visibility, and shared-memory size
- per-source Trust policies for normal use, review-first use, or private/excluded sources
- stats and export
- graph/node mapping
- diagnostics, reliability reports, support bundles, backups, repair, and search maintenance
- MCP-style JSON-RPC tools

## Local Run

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8766
```

The macOS app defaults to `http://127.0.0.1:8766`.

## Environment

```bash
CORTEX_VAULT_PATH=./data/Cortex.vault
CORTEX_DB_PATH=./data/Cortex.vault/index.sqlite
CORTEX_API_KEY=dev-local-key
ANTHROPIC_API_KEY=optional
CORTEX_EMBEDDING_PROVIDER=hash
CORTEX_EMBEDDING_MODEL=text-embedding-3-small
CORTEX_EMBEDDING_DIMENSIONS=384
CORTEX_EMBEDDING_STRICT=0
CORTEX_REQUIRE_SCOPED_API_TOKENS=0
OPENAI_API_KEY=optional
```

If `ANTHROPIC_API_KEY` is missing, the backend uses a deterministic local extractor so capture/search still work.

If `CORTEX_EMBEDDING_PROVIDER=openai`, Cortex calls OpenAI's embeddings endpoint with the configured model and stores the resulting vectors in the rebuildable SQLite index. Leave the provider as `hash` for fully offline local search. Non-strict OpenAI mode falls back to hash embeddings when the provider is unavailable; set `CORTEX_EMBEDDING_STRICT=1` when indexing should fail instead. Keep `CORTEX_EMBEDDING_DIMENSIONS=384` for the current local sqlite-vec schema.

## Core Endpoints

```text
GET    /
GET    /health
GET    /ready
POST   /v1/captures
POST   /v1/captures/queue
GET    /v1/captures/{capture_id}/status
GET    /v1/imports/sources
POST   /v1/imports/analyze
GET    /v1/imports
POST   /v1/imports
GET    /v1/imports/{import_id}
DELETE /v1/imports/{import_id}
GET    /v1/source-accounts/catalog
GET    /v1/sources/readiness
GET    /v1/source-accounts
POST   /v1/source-accounts
DELETE /v1/source-accounts/{account_id}
GET    /v1/sync-cursors
POST   /v1/sync-cursors
GET    /v1/sync/changes
GET    /v1/jobs
GET    /v1/jobs/{job_id}
POST   /v1/maintenance/jobs/run
GET    /v1/inbox
POST   /v1/captures/{capture_id}/approve
POST   /v1/captures/{capture_id}/archive
DELETE /v1/captures/{capture_id}
DELETE /v1/memories/{memory_id}
GET    /v1/recent
GET    /v1/search
GET    /v1/ask
GET    /v1/tasks/open
GET    /v1/topics
GET    /v1/entities
GET    /v1/entities/{name}
GET    /v1/review/today
GET    /v1/loop
POST   /v1/loop/reuse
GET    /v1/context-pack
GET    /v1/personal-profile
GET    /v1/agent-adaptation
GET    /v1/graph
GET    /v1/stats
GET    /v1/memory/quality
GET    /v1/settings
PUT    /v1/settings
GET    /v1/trust/summary
GET    /v1/privacy/lifecycle
POST   /v1/integrations/api-token
POST   /v1/integrations/mcp-token
GET    /v1/integrations/tokens
DELETE /v1/integrations/tokens/{token_id}
GET    /v1/diagnostics
GET    /v1/reliability/report
GET    /v1/support/bundle
POST   /v1/backups
POST   /v1/backups/restore-latest
DELETE /v1/backups
DELETE /v1/user-data
POST   /v1/maintenance/repair-storage
POST   /v1/maintenance/rebuild-search
POST   /v1/maintenance/rebuild-index-from-vault
GET    /v1/export.json
GET    /v1/export.md
POST   /mcp
```

## Standalone Backend

The macOS app bundles `app.standalone_server`, a dependency-light local HTTP server built on Python's standard library plus the shared Cortex storage/extraction modules. The FastAPI server remains useful for hosted development, while the packaged app can run locally without `uvicorn`.

## Storage

The local beta uses a user-owned vault folder plus a rebuildable SQLite index. Import sessions, source accounts, sync cursors, captures, memories, tasks, entities, graph edges, settings, and audit events are written as JSON/JSONL under `Cortex.vault/`. `index.sqlite` is used for fast FTS5/sqlite-vec search, embedding metadata, source health queries, and graph queries, but it can be rebuilt from the vault records.

See `docs/LOCAL_VAULT_FORMAT.md` for the full disk layout and recovery contract.

## Battle Tests

```bash
python3 -m unittest discover backend/tests
```

With the backend running:

```bash
python3 scripts/reliability_check.py
python3 scripts/battle_test_http.py
python3 scripts/export_support_bundle.py --mode live
python3 scripts/ops_readiness_check.py --require-live
```

## Hosted Beta

Deploy the same FastAPI service to Render, Fly.io, Railway, or a small VPS. For the hosted beta, keep SQLite WAL plus `sqlite-vec` as the primary memory store, add login/API-token auth, encrypted backups, and eventually per-user or per-shard database files.

Shard routing is opt-in and defaults to the local single-store mode used by the macOS app:

```bash
export CORTEX_SHARD_MODE=local   # local, user, or bucket
export CORTEX_SHARD_ROOT=/var/lib/cortex/shards
export CORTEX_SHARD_COUNT=64     # only used by bucket mode
```

`local` preserves `CORTEX_DB_PATH` and `CORTEX_VAULT_PATH`. `user` creates a dedicated SQLite database and vault per user. `bucket` hashes users into a fixed number of shard directories. `/health` includes the active shard mode and default shard metadata.

For hosted or multi-user development, enable scoped REST API tokens:

```bash
export CORTEX_REQUIRE_SCOPED_API_TOKENS=1
```

With this enabled, the global `CORTEX_API_KEY` can no longer use `X-Cortex-User` to select another user. Register a user-owned REST token with `POST /v1/integrations/api-token`, then call REST endpoints with that `cxa_` token and the matching `X-Cortex-User` header. This keeps local single-user behavior unchanged while giving hosted deployments a safer boundary between account identity and memory shards.

`GET /v1/sync/changes` returns a cursorable, content-free local event feed for a user. It includes event IDs, safe object IDs or redacted hashes, whitelisted metadata, counts, the active shard assignment, and a high watermark, but no raw capture text, memory bodies, imported source content, context packs, or vault files. This is a hosted-sync foundation for materializing shard state from local events; it is not yet a full sync protocol, conflict resolver, OAuth connector runtime, or remote backup service.

Scoped REST tokens enforce the same capability names used by Trust controls:

- `read`: search, inbox, stats, graph, source history, source accounts, sync cursors, settings reads, and review reads
- `write`: captures, queued captures, imports, source account registration, sync cursor updates, approvals, archives, loop reuse, and settings changes
- `export`: context packs, personal profiles, support bundles, and JSON/Markdown export
- `maintenance`: diagnostics, reliability reports, jobs, repair/rebuild operations, backups, source account disconnect, and token registration
- `destructive`: capture/memory/import deletion, backup deletion, full user-data deletion, and backup restore

Token lifecycle endpoints return metadata only. They never expose token hashes, salts, or raw token values. Revocation sets `revoked_at`; revoked tokens no longer authenticate and are omitted from token lists unless `include_revoked=true` is passed.
