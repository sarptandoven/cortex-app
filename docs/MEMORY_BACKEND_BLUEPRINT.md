# Cortex Memory Backend Blueprint

## Recommendation

Cortex should stay local-first and vault-backed, then scale through user-sharded memory databases instead of moving immediately to one global vector database.

The production path is:

1. Local app: JSON vault plus SQLite WAL, FTS5, and optional `sqlite-vec`.
2. Hosted beta: one SQLite database per user or per small shard, replicated/backed up with Litestream-style streaming backups or libSQL/Turso when managed sync is needed.
3. Team or enterprise scale: Postgres plus `pgvector` for account metadata, admin workflows, billing, teams, and shared memory spaces.
4. Dedicated vector tier only when needed: Qdrant for high-volume filtered vector search, or LanceDB for large multimodal/document lake workloads.

This keeps the core promise intact: a user's memory is portable, inspectable, exportable, and recoverable.

## Why This Stack

- SQLite FTS5 is built for efficient full-text search over large document collections: https://www.sqlite.org/fts5.html
- SQLite WAL supports the current single-user local write pattern and gives a clear backup/repair model: https://sqlite.org/wal.html
- `sqlite-vec` runs vector search inside SQLite and is explicitly designed to run on laptops, servers, mobile, and WASM: https://github.com/asg017/sqlite-vec
- Litestream is an open-source SQLite replication/backup tool for safely running SQLite on a single node: https://litestream.io/
- libSQL/Turso adds SQLite-compatible native vector search and embedded replicas when managed sync becomes more important: https://docs.turso.tech/libsql and https://docs.turso.tech/features/ai-and-embeddings
- `pgvector` keeps vectors with relational account data when Cortex needs hosted auth, teams, quotas, billing, and operational reporting in Postgres: https://github.com/pgvector/pgvector
- Qdrant is a Rust vector database with payload filtering and hybrid query support, useful once vector search becomes a separate service: https://qdrant.tech/documentation/search/hybrid-queries/
- LanceDB supports hybrid vector and full-text search for search-heavy and multimodal datasets: https://docs.lancedb.com/search/hybrid-search

## Canonical Memory Layers

Raw data stays immutable enough to audit:

- Claude chats
- ChatGPT chats
- emails
- notes
- writing samples
- decisions
- messages
- files and links

Processed memory is stored as atomic, cited records:

- `semantic`: durable facts about the user, projects, people, goals, constraints, and domain context
- `episodic`: specific past events, conversations, meetings, messages, and dated work sessions
- `style`: how the user writes, including tone, sentence length, phrasing, structure, and formatting patterns
- `decision`: choices, reasons, tradeoffs, constraints, and the date/source of the decision
- `preference`: durable preferences that should guide future answers and actions
- `negative`: rejected ideas, disliked outputs, blocked approaches, and "do not do this again" signals

The current implementation now persists this as `memories.layer` while preserving `memories.kind` for finer-grained display and filtering.

## Retrieval Pipeline

Every retrieval path should return cited memories and explain why they were selected.

1. Normalize the query and infer intent: person, project, preference, decision, style, open loop, or general context.
2. Apply hard filters first: user, workspace, source policy, review status, archive status, retention policy, and trust controls.
3. Run keyword retrieval with FTS5/BM25.
4. Run vector retrieval with `sqlite-vec` locally or the hosted vector provider for that shard.
5. Pull graph neighbors for matched entities, projects, people, and captures.
6. Boost by layer:
   - decisions for planning and implementation tasks
   - preferences and negative memory for response shaping
   - style for writing tasks
   - episodic memory for "what happened" queries
   - semantic facts for project/person/entity questions
7. Fuse rankings with reciprocal rank fusion.
8. Apply recency, importance, source reliability, and explicit rejection boosts.
9. Optionally rerank the top 30-80 candidates with a local or hosted cross-encoder.
10. Return a compact context pack with memory IDs, sources, dates, and layers.

## Hosted Data Shape

Add these production concepts before public hosted sync:

- `users`: account identity, encryption state, export/delete state
- `api_tokens`: scoped local, MCP, browser, and automation tokens
- `sources`: source app, source account, trust policy, retention policy
- `capture_events`: append-only raw ingestion/event log
- `captures`: normalized source documents and review state
- `memories`: atomic extracted memory with `kind`, `layer`, status, visibility, source, and citation
- `memory_embeddings`: model, dimensions, text hash, embedding version, freshness
- `memory_edges`: person/project/topic/source/decision relationships
- `memory_jobs`: extraction, embedding, summarization, dedup, deletion, and backup jobs
- `deletion_tombstones`: hard-delete and backup-retention workflow; the local vault now writes item-level tombstones and reapplies them during restore before rebuilding the SQLite index
- `audit_events`: user-visible reads, writes, exports, agent calls, and policy changes

## Operating Model

- Local writes append to the vault and update the local index.
- The backend now has a `StoreRegistry` facade and `ShardRouter` that can preserve local single-store behavior or route user-scoped calls to per-user/per-bucket SQLite shards.
- Scoped `cxa_` REST tokens can now resolve a user before shard routing, enforce read/write/export/maintenance/destructive capability classes, and `CORTEX_REQUIRE_SCOPED_API_TOKENS=1` disables global-token user switching for hosted-style runs.
- Local async capture is now available through `POST /v1/captures/queue`, `capture_processing_state`, and `memory_jobs`; the existing `POST /v1/captures` path remains synchronous for the macOS app and compatibility.
- Hosted sync sends signed append-only events, not opaque app state.
- The server materializes events into a per-user index and can rebuild it from events.
- Extraction can run in background jobs with retries and idempotent keys; embeddings still run inside materialization today and should be split into `embed_memory` jobs next.
- Backups are encrypted per user or per shard.
- "Forget" is implemented locally as hard delete plus restore-blocking tombstones. Hosted data still needs backup-expiry enforcement and tombstone propagation across shards.
- MCP reads can be low-friction; MCP writes, exports, and destructive actions need scopes, audit events, and optional confirmation.

## Scale Decision Points

Stay with SQLite/sqlite-vec while:

- the product is local-first,
- user memory is private and mostly queried by one user,
- portability/export/recovery are core differentiators,
- and one user's index fits comfortably in a local or per-user database.

Move hosted shards to libSQL/Turso when:

- sync and embedded read replicas matter,
- the team wants managed SQLite-compatible operations,
- and native vector search without SQLite extension loading is valuable.

Use Postgres plus `pgvector` when:

- account/team/billing/admin workflows dominate,
- queries need richer relational joins,
- and hosted operational tooling matters more than single-file portability.

Use Qdrant or LanceDB when:

- vector search is a separately scaled service,
- filtering/hybrid search or multimodal indexing is the main bottleneck,
- and memory is no longer primarily per-user local state.
