# Cortex Hosted Scale Roadmap

## Current Stage

Cortex is a local-first beta foundation. The local product now has a user-owned vault, rebuildable SQLite index, scoped MCP tokens, optional scoped REST user tokens, restore-safe tombstones, backup retention, and a durable local job queue.

It is not yet a millions-user hosted product. Hosted scale needs account identity, shard routing, object storage, background workers, observability, billing, deletion guarantees, support operations, and public distribution hardening.

## Milestone 1: Hosted Control Plane

Build identity and routing outside memory shards.

Control-plane tables:

- `accounts`
- `users`
- `memberships`
- `workspaces`
- `devices`
- `user_shards`
- `api_tokens`
- `sync_cursors`
- `billing_plans`
- `quotas`
- `delete_requests`

The control plane owns auth, organization membership, token revocation, billing state, quotas, shard assignment, and deletion request orchestration. Shard databases own memory records and retrieval indexes.

Implemented local primitive:

- `POST /v1/integrations/api-token` stores hashed `cxa_` REST tokens with user ownership, audience, scopes, and last-used metadata.
- `GET /v1/integrations/tokens` and `DELETE /v1/integrations/tokens/{token_id}` provide token metadata and revocation without exposing token secrets.
- `CORTEX_REQUIRE_SCOPED_API_TOKENS=1` prevents the global app token from selecting arbitrary users with `X-Cortex-User`.
- FastAPI and the packaged standalone backend both authenticate scoped REST tokens before routing user-scoped memory calls.
- REST token scopes are enforced across read, write, export, maintenance, and destructive endpoint classes.
- This is not a full hosted identity provider. Public hosted deployments still need login, session management, token revocation UI, account membership checks, and control-plane token issuance.

## Milestone 2: Shard Runtime

Turn `CortexStore` into a shard-backed service.

Initial hosted shape:

- one SQLite/libSQL database per user or small shard;
- shard migrations based on `backend/app/database.py`;
- `ShardRouter` for user/workspace to shard lookup;
- `StoreRegistry` for request-time `CortexStore` construction and lazy shard initialization;
- shard health checks, backups, and repair reports;
- no global vector database until per-user shard search is proven insufficient.

Implemented local primitive:

- `backend/app/sharding.py` supports `local`, `user`, and `bucket` shard modes.
- `CORTEX_SHARD_MODE=local` preserves the packaged macOS behavior with one database and vault.
- `CORTEX_SHARD_MODE=user` stores each user in a dedicated SQLite/vault directory under `CORTEX_SHARD_ROOT`.
- `CORTEX_SHARD_MODE=bucket` hashes users into `CORTEX_SHARD_COUNT` bucket directories for small hosted shards.
- `/health` exposes shard mode and default shard metadata so operators can verify runtime routing.
- Scoped REST token auth composes with these shard modes: the token resolves the user, then `StoreRegistry` routes the request to that user's local, per-user, or bucketed store.

## Milestone 3: Async Ingestion Workers

The local app now has the first durable queue primitives:

- `memory_jobs`
- `capture_processing_state`
- `POST /v1/captures/queue`
- `GET /v1/captures/{capture_id}/status`
- `GET /v1/jobs/{job_id}`
- `POST /v1/maintenance/jobs/run`

Hosted ingestion should extend this model:

1. Accept raw capture fast.
2. Enqueue `extract_capture`.
3. Materialize records/tasks/entities/FTS.
4. Enqueue `embed_memory` jobs.
5. Enqueue dedupe, summarization, and quality-eval jobs.
6. Expose queue lag, failure counts, and index freshness in diagnostics.

Workers should claim jobs with a short SQLite/libSQL lease, release the database while doing model work, and complete/fail idempotently.

## Milestone 4: Vault Sync And Object Storage

Treat the local vault as syncable canonical user data.

Object storage layout:

```text
users/{user_id}/vault/captures/{capture_id}.json
users/{user_id}/vault/memories/{memory_id}.json
users/{user_id}/vault/tasks/{task_id}.json
users/{user_id}/vault/graph_edges/{edge_id}.json
users/{user_id}/vault/deletion_tombstones/{type}/{id}.json
users/{user_id}/imports/{import_id}/...
users/{user_id}/attachments/{attachment_id}/...
users/{user_id}/backups/{backup_id}/...
```

Sync should use signed append-only events and materialize shard rows from vault records. Imports, attachments, support-approved recovery bundles, and large source files should use object storage instead of shard blobs.

## Milestone 5: Deletion, Observability, Migration

Deletion must be operational before hosted public beta:

- tombstone propagation to shard DBs and object storage;
- purge jobs for vectors, derived records, imports, and attachments;
- backup expiry tracking;
- user-visible deletion receipts;
- deletion SLA monitoring.

Observability must include:

- request latency and error rates;
- extraction and embedding latency;
- queue lag by job type;
- failed and dead-lettered jobs;
- shard health and backup age;
- search quality and zero-result rates;
- sync conflict counts;
- delete SLA state.

Migration from local beta:

1. Import a local `Cortex.vault/`.
2. Upload canonical vault files to object storage.
3. Materialize the hosted shard.
4. Verify counts and sample retrieval.
5. Enable sync cursors.
6. Keep local export/delete available at every step.
