# Cortex Backend

FastAPI backend for the Cortex MVP.

It provides:

- capture ingestion
- structured memory extraction
- user-owned local vault persistence
- SQLite/FTS rebuildable local index
- full-text search
- optional `sqlite-vec` vector search
- review inbox
- capture approve/archive lifecycle
- daily review with recommended actions
- simple product loop for capture, review, reuse, and return
- copy-ready context packs for ChatGPT, Claude, Cursor, and other assistants
- user settings for review behavior, pending-memory visibility, and context-pack size
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
```

If `ANTHROPIC_API_KEY` is missing, the backend uses a deterministic local extractor so capture/search still work.

## Core Endpoints

```text
GET    /
GET    /health
GET    /ready
POST   /v1/captures
GET    /v1/inbox
POST   /v1/captures/{capture_id}/approve
POST   /v1/captures/{capture_id}/archive
GET    /v1/recent
GET    /v1/search
GET    /v1/tasks/open
GET    /v1/topics
GET    /v1/entities
GET    /v1/entities/{name}
GET    /v1/review/today
GET    /v1/loop
POST   /v1/loop/reuse
GET    /v1/context-pack
GET    /v1/graph
GET    /v1/stats
GET    /v1/settings
PUT    /v1/settings
GET    /v1/diagnostics
GET    /v1/reliability/report
GET    /v1/support/bundle
POST   /v1/backups
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

The local beta uses a user-owned vault folder plus a rebuildable SQLite index. Captures, memories, tasks, entities, graph edges, settings, and audit events are written as JSON/JSONL under `Cortex.vault/`. `index.sqlite` is used for fast FTS5/sqlite-vec search and graph queries, but it can be rebuilt from the vault records.

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
