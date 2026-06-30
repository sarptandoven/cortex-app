# Cortex Hosted Scale Roadmap

## Current Stage

Cortex is a local-first beta foundation. The local product now has a user-owned vault, rebuildable SQLite index, scoped MCP tokens, optional scoped REST user tokens, restore-safe tombstones, backup retention, durable source-account/sync-cursor records, a content-free local sync change feed, and a durable local job queue.

It is not yet a hosted 10k-user platform. The next hosted direction is FastAPI plus Postgres/pgvector, account identity, background workers, object storage for vault/export artifacts, observability, deletion guarantees, support operations, and public distribution hardening.

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
- `source_accounts`
- `sync_cursors`
- `billing_plans`
- `quotas`
- `delete_requests`

The control plane owns auth, organization membership, token revocation, billing state, quotas, shard assignment, and deletion request orchestration. Shard databases own memory records and retrieval indexes.

Implemented local primitive:

- `POST /v1/integrations/api-token` stores hashed `cxa_` REST tokens with user ownership, audience, scopes, and last-used metadata.
- `GET /v1/integrations/tokens` and `DELETE /v1/integrations/tokens/{token_id}` provide token metadata and revocation without exposing token secrets.
- `CORTEX_REQUIRE_SCOPED_API_TOKENS=1` prevents the global app token from selecting arbitrary users with `X-Cortex-User`.
- FastAPI `GET /health` and the packaged standalone backend both expose a `hosted_readiness` contract documenting whether the process is in hosted-style shard mode, whether scoped API tokens are required, and whether global-token user switching is allowed only for local compatibility.
- FastAPI `GET /ready` and standalone `GET /ready` fail closed with HTTP 503 in hosted-style shard modes (`CORTEX_SHARD_MODE=user` or `bucket`) until the production gates pass.
- Hosted readiness now blocks on:
  - `CORTEX_REQUIRE_SCOPED_API_TOKENS=1`
  - hosted HTTPS `CORTEX_PUBLIC_BASE_URL`
  - `CORTEX_SYNC_SIGNING_KEY`
  - non-hash `CORTEX_EMBEDDING_PROVIDER`
  - `CORTEX_HOSTED_VECTOR_BACKEND=pgvector`
  - `CORTEX_WORKER_MODE=external`
  - `CORTEX_OBSERVABILITY_ENABLED=1`
- FastAPI and the packaged standalone backend both authenticate scoped REST tokens before routing user-scoped memory calls.
- REST token scopes are enforced across read, write, export, maintenance, and destructive endpoint classes.
- `source_accounts` and `sync_cursors` are implemented locally as vault-backed records plus SQLite indexes. They preserve connector health, policies, high-water marks, and last errors across backups and rebuilds, but they do not yet store OAuth secrets or run live cloud sync.
- `sync_devices`, `sync_receipts`, and `GET /v1/sync/changes?device_id=...` are implemented locally as a device-aware event manifest for future hosted materialization. They return safe event metadata, counts, shard assignment, device cursors, revocation state, manifest acknowledgement state, and optional HMAC-SHA256 signatures without capture content, memory text, imports, context packs, or vault file payloads.
- This is not a full hosted identity provider. Public hosted deployments still need login, session management, token revocation UI, account membership checks, and control-plane token issuance.

Current multi-user/token assumptions:

- Local mode keeps the global app token as a compatibility admin token for the default user.
- The global app token cannot select a different `X-Cortex-User` when scoped API tokens are required or when shard mode is not `local`.
- Scoped REST tokens are user-owned, hashed at rest, revocable, and must carry the scope class required by each endpoint.
- In `user` and `bucket` shard modes, hosted callers should send a resolved user identity with the scoped token. Without a control-plane token index, token lookup can only search the default store and stores already opened by the process.
- Standalone server readiness uses the same hosted readiness contract as FastAPI.

## Milestone 2: Postgres/pgvector Runtime

Turn the local store contract into a hosted relational service while keeping local SQLite/vault behavior intact.

Initial hosted shape:

- Postgres as the hosted source of truth for accounts, source records, captures, memories, review state, audit rows, and jobs;
- `pgvector` for hosted semantic retrieval, paired with Postgres keyword search and deterministic reranking;
- `ShardRouter` for user/workspace to shard lookup;
- `StoreRegistry` for request-time `CortexStore` construction and lazy shard initialization;
- shard or tenant health checks, backups, and repair reports;
- no separate vector database until Postgres/pgvector fails measured latency or recall targets.

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
- `backend/app/worker.py`
- `scripts/run_memory_worker.py`

Hosted ingestion should extend this model:

1. Accept raw capture fast.
2. Enqueue `extract_capture`.
3. Materialize records/tasks/entities/FTS.
4. Enqueue `embed_memory` jobs.
5. Enqueue dedupe, summarization, and quality-eval jobs.
6. Expose queue lag, failure counts, and index freshness in diagnostics.

Workers should claim jobs with a short SQLite/libSQL lease, release the database while doing model work, and complete/fail idempotently.

Implemented local worker runner:

- `run_worker_tick(...)` wraps the existing queue primitives without introducing a second queue system.
- `scripts/run_memory_worker.py` runs one or more worker ticks for selected users and prints JSON summaries with processed, pending, failed, per-user, and recent failed-job data.
- Example local run:

```bash
python3 scripts/run_memory_worker.py --user-id local --limit 25 --fail-on-failed
```

Hosted deployments should run this as an external worker process with `CORTEX_WORKER_MODE=external`, shard-aware user assignment, process supervision, and alerting on nonzero failed-job counts. The macOS first-100 app does not expose this as a primary user control.

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

Implemented local primitive:

- `POST /v1/sync/devices` registers a local sync device, `DELETE /v1/sync/devices/{device_id}` revokes it, `GET /v1/sync/changes?device_id=...` provides a content-free, cursorable event feed, and `POST /v1/sync/devices/{device_id}/receipts` records accepted/uploaded/failed manifest cursors.
- The feed and receipts are useful for hosted materialization, support diagnostics, and device upload planning because they prove ordering, scope, acknowledgement, and local integrity without exposing raw memory bodies.
- Remaining hosted work: authenticated cloud device identity, asymmetric signatures or KMS-backed signing, conflict resolution, encrypted payload upload, object-storage manifests, remote materializers, backpressure, replay idempotency, and cross-device merge/delete receipts.

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
