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
- `StoreRegistry` also keeps a small SQLite control-plane token index under the shard root so scoped API and MCP tokens can resolve their user before a user shard is opened. This keeps cold `user` and `bucket` shard modes usable without scanning every shard.
- `CORTEX_REQUIRE_SCOPED_API_TOKENS=1` prevents the global app token from selecting arbitrary users with `X-Cortex-User`.
- FastAPI `GET /health` and the packaged standalone backend both expose a `hosted_readiness` contract documenting whether the process is in hosted-style shard mode, whether scoped API tokens are required, and whether global-token user switching is allowed only for local compatibility.
- FastAPI `GET /ready` and standalone `GET /ready` fail closed with HTTP 503 in hosted-style shard modes (`CORTEX_SHARD_MODE=user` or `bucket`) until the production gates pass.
- Hosted readiness now blocks on:
  - `CORTEX_REQUIRE_SCOPED_API_TOKENS=1`
  - hosted HTTPS origin in `CORTEX_PUBLIC_BASE_URL` with a public, non-reserved host
  - `CORTEX_SYNC_SIGNING_KEY`
  - Postgres primary store in `CORTEX_HOSTED_DATABASE_URL` or `DATABASE_URL`
  - non-hash `CORTEX_EMBEDDING_PROVIDER`
  - `CORTEX_HOSTED_VECTOR_BACKEND=pgvector`
  - `CORTEX_WORKER_MODE=external`
  - `CORTEX_OBSERVABILITY_ENABLED=1`
- FastAPI and the packaged standalone backend both authenticate scoped REST tokens before routing user-scoped memory calls.
- REST token scopes are enforced across read, write, export, maintenance, and destructive endpoint classes.
- `source_accounts` and `sync_cursors` are implemented locally as vault-backed records plus SQLite indexes. They preserve connector health, policies, high-water marks, and last errors across backups and rebuilds, but they do not yet store OAuth secrets or run live cloud sync.
- `sync_devices`, `sync_receipts`, and `GET /v1/sync/changes?device_id=...` are implemented locally as a device-aware event manifest for future hosted materialization. They return safe event metadata, counts, shard assignment, device cursors, revocation state, manifest acknowledgement state, and optional HMAC-SHA256 signatures without capture content, memory text, imports, context packs, or vault file payloads.
- This is not a full hosted identity provider. Public hosted deployments still need login, session management, token revocation UI, account membership checks, and control-plane token issuance.

Implemented management plane:

- The control-plane index now has a first-class `users` registry table (`user_id`, `display_name`, `plan`, `status`, `metadata`, `created_at`, `updated_at`) alongside the scoped-token index, so a user is a real record rather than being inferred from token existence.
- `StoreRegistry.provision_user()` creates a hosted user end to end: register the user, materialize the isolated shard, and mint an initial API + MCP token pair (returned once). `list_users`, `get_user`, `suspend_user`, `reactivate_user`, and `deprovision_user` manage the lifecycle.
- Admin HTTP surface, gated behind the operator's global `CORTEX_API_KEY` (scoped per-user tokens can never call it): `POST /v1/admin/users` (provision), `GET /v1/admin/users` (list/filter by status), `POST /v1/admin/users/{id}/suspend`, `POST /v1/admin/users/{id}/reactivate`, and `DELETE /v1/admin/users/{id}` (deprovision).
- Suspension is enforced on every authentication path (control index and the per-shard fallback), so suspending a user immediately rejects their tokens; suspended users also drop out of the ready-user/worker enumeration, pausing their background jobs. Reactivation restores both.
- Control-plane token authentication is O(1): tokens carry an indexed deterministic `lookup_hash` for a direct lookup, while the authoritative credential check remains the per-row salted hash compared in constant time. Legacy tokens without a lookup hash fall back to a scan and are backfilled on first use.
- The per-user store cache is LRU-bounded (`CORTEX_STORE_CACHE_SIZE`, default 512) so `user` mode does not leak memory/handles at 10k+ users; evicted shards re-materialize on next access.
- The background worker (`run_memory_worker.py`) auto-discovers active provisioned users from the registry each tick in sharded modes, so newly provisioned users are processed without a restart and suspended users are skipped.
- Still planned in M1: `accounts`/`memberships`/`workspaces`/`billing_plans`/`quotas`/`delete_requests` tables, self-serve signup/login/session management, and per-user storage/request quotas and rate limiting.

Current multi-user/token assumptions:

- Local mode keeps the global app token as a compatibility admin token for the default user.
- The global app token cannot select a different `X-Cortex-User` when scoped API tokens are required or when shard mode is not `local`.
- Scoped REST and MCP tokens are user-owned, hashed at rest, revocable, and must carry the scope class required by each endpoint/tool class.
- In `user` and `bucket` shard modes, hosted callers may send a resolved user identity with the scoped token, but token lookup can now resolve through the local control index even when the user's shard has not been opened in the current process.
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
- `POST /v1/sources/sync-due`
- `GET /v1/jobs/health`
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
- `GET /v1/jobs/health` reports queue status, due queued work, per-status/per-type counts, recent failures, and stale running jobs without returning job payload or memory content.
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
