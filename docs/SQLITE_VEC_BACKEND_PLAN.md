# Cortex SQLite-Vec Backend Plan

## Decision

Cortex should use SQLite as the product storage layer and `sqlite-vec` as the first vector retrieval layer. This keeps the product local-first, cheap to operate, easy to back up, and credible for privacy-sensitive personal memory.

Current direction: SQLite and `sqlite-vec` are the local-first beta path. The verified 10k-user hosted direction is FastAPI with Postgres plus `pgvector`; keep the SQLite-hosting notes below as historical/low-cost fallback context, not the primary hosted plan.

The earlier hosted backend option was a full service around SQLite:

- FastAPI service
- SQLite WAL database
- FTS5 keyword search
- `sqlite-vec` vector search
- hybrid retrieval
- background extraction and embedding jobs
- remote MCP endpoint
- macOS sync
- browser extension ingestion
- web dashboard
- encrypted backups

## Why This Direction

`sqlite-vec` is a SQLite extension for vector search that can run on laptops, servers, mobile devices, browsers, Raspberry Pis, and other SQLite environments. It uses normal SQL for creating vector tables, inserting embeddings, and KNN-style queries.

That fits Cortex better than a managed vector database at this stage because user memory should be portable, inspectable, exportable, and inexpensive.

## Runtime Requirement

The current packaged app launches with `/usr/bin/python3`, but Apple's default Python/SQLite build may not support loading SQLite extensions. The full product needs a bundled backend runtime that supports extension loading and includes the `sqlite-vec` package.

Preferred packaging path:

- Bundle a managed Python runtime or standalone backend binary.
- Include `sqlite-vec`.
- Initialize vector schema on first launch.
- Fall back to FTS5 if vector loading fails.
- Surface vector status in diagnostics.

## Storage Shape

Keep current tables:

- `captures`
- `memories`
- `tasks`
- `entities`
- `memory_topics`
- `memory_entities`
- `graph_edges`
- `memory_events`
- `memory_fts`

Add vector tables:

- `memory_vec_map`
  - maps Cortex memory IDs to sqlite-vec integer row IDs
  - stores user ID, embedding model, text hash, timestamps
- `memory_vec`
  - `vec0` virtual table
  - stores 384-dimensional memory embeddings

## Retrieval Pipeline

Search should be hybrid:

1. Normalize the query.
2. Run FTS5 keyword search.
3. Embed the query.
4. Run `sqlite-vec` KNN search when available.
5. Fuse results with reciprocal rank fusion.
6. Filter by user, status, kind, and source policy.
7. Return cited memory records.

FTS5 remains the reliable floor. Vector search improves recall when wording differs.

## Embedding Strategy

Phase 1 uses `cortex-hash-v1`, a deterministic local embedding fallback. It proves indexing, sync, backups, rebuilds, and hybrid retrieval without paid APIs.

The current backend also has an opt-in OpenAI embeddings provider:

- `CORTEX_EMBEDDING_PROVIDER=hash` keeps local/offline deterministic embeddings.
- `CORTEX_EMBEDDING_PROVIDER=openai` calls the OpenAI embeddings endpoint with `CORTEX_EMBEDDING_MODEL`, `CORTEX_EMBEDDING_DIMENSIONS`, and `OPENAI_API_KEY`.
- non-strict OpenAI mode falls back to `cortex-hash-v1` on provider failures; `CORTEX_EMBEDDING_STRICT=1` makes those failures visible.
- `CORTEX_EMBEDDING_DIMENSIONS` must stay at `384` until the local sqlite-vec schema becomes dynamically migratable.
- vector rows store `embedding_model`, text hash, and timestamps so indexes can be rebuilt and re-embedded.

Phase 2 should add more embedding choices:

- Local model first for privacy-focused users.
- Anthropic-compatible or other embedding providers for users who want quality over local-only operation.
- Store `embedding_model`, dimensions, text hash, and timestamps so we can re-embed safely.

## Full Backend Architecture

### API

- `POST /v1/captures`
- `GET /v1/search`
- `GET /v1/review/today`
- `GET /v1/context-pack`
- `GET /v1/graph`
- `POST /v1/maintenance/rebuild-search`
- `POST /v1/maintenance/rebuild-vectors`
- `GET /v1/diagnostics`
- `POST /mcp`

### Jobs

- extraction
- embedding generation
- import processing
- vector rebuild
- digest generation
- backup verification

### Sync

Use append-only sync events:

- `capture_created`
- `capture_approved`
- `capture_archived`
- `memory_archived`
- `memory_edited`
- `embedding_rebuilt`

The macOS app can remain useful offline, then push events to hosted Cortex when the user enables sync.

### Hosting

Cheapest credible path:

- one FastAPI service on Fly.io, Render, Railway, or a small VPS
- persistent volume for SQLite
- WAL mode
- daily encrypted backups
- Litestream-style replication later
- per-user sharding once write contention appears

Scale path:

- user-sharded SQLite files
- one metadata/control database
- queued writes per shard
- read replicas or cache for hot users
- historical fallback: Turso/libSQL if a SQLite-hosted path is revived; current 10k plan uses Postgres/pgvector first

## Integration Plan

### MCP

Remote MCP should sit on top of the same backend retrieval layer:

- `remember_this`
- `search_memory`
- `get_daily_review`
- `build_context_pack`
- `get_decisions`
- `get_open_questions`
- `forget_memory`

Read tools can run without approval. Destructive tools need explicit confirmation and audit events.

### ChatGPT and Claude

The backend should expose the remote MCP transport and OAuth/token auth. ChatGPT/Claude should retrieve memories through MCP tools, not through separate vendor-specific memory stores.

### Browser Extension

The browser extension should capture explicitly selected context from:

- ChatGPT
- Claude
- Gemini
- Perplexity
- docs and web pages

No ambient scraping in the default product.

## Production Bar

Before public beta:

- vector diagnostics in the app
- vector rebuild test
- hybrid retrieval battle test
- backup restore test
- 1,000-capture dogfood database
- extension-loading packaging solved on macOS
- clear export/delete account flow
- MCP auth hardened

## Current Implementation Status

The repo now has the first safe layer:

- `sqlite-vec` declared as a backend dependency
- optional extension loading
- vector schema initialization when available
- deterministic local embeddings
- opt-in OpenAI embeddings with offline fallback
- memory vector indexing hooks
- vector cleanup on archive/delete
- hybrid search path that falls back to FTS5
- diagnostics and rebuild endpoint return vector and embedding provider status

The next engineering step is to ship a backend runtime that actually includes and loads `sqlite-vec` on macOS.
