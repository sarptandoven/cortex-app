# Cortex Reliability Hardening

This pass makes the local macOS product safer to launch, diagnose, and recover without a hosted backend.

## Goals

- Prove the app is connected to the expected backend build, not just any process on port `8766`.
- Keep the local vault as the user-owned source of truth.
- Repair only derived local index drift after creating a backup.
- Give users and support a copyable reliability report.
- Make reliability checks runnable in CI, local development, and packaged app QA.

## Backend Contract

`GET /health` now returns a versioned contract:

```json
{
  "status": "ok",
  "backend_version": "0.1.0",
  "health_contract": 3,
  "features": [
    "local-vault",
    "capture-surfaces",
    "trust-controls",
    "installer-updates",
    "reliability-hardening",
    "simple-product-loop",
    "operational-readiness"
  ],
  "mode": "standalone",
  "db_path": ".../index.sqlite",
  "vault_path": ".../Cortex.vault",
  "auth": true
}
```

The macOS launcher and operational readiness checks require `health_contract >= 3`, the `reliability-hardening`, `simple-product-loop`, and `operational-readiness` feature flags, and the configured vault path. If an incompatible local Cortex backend is already listening on the packaged port, the app stops that stale listener and launches the bundled backend.

## Reliability Report

`GET /v1/reliability/report` returns:

- backend version and health contract
- latest backup path and age
- SQLite `quick_check`
- FTS stale row count
- relationship orphan count
- vault folder layout health
- sqlite-vec availability
- recommended user-facing recovery actions

The More tab surfaces the same report and can copy it as Markdown for support or bug reports.

## Support Bundle

`GET /v1/support/bundle` returns an operational triage payload with:

- backend version and feature flags
- Python, platform, and SQLite version
- diagnostics and reliability report
- trust state and risk flags
- record counts
- backup age
- safe recent event metadata

It intentionally omits captured text, memory content, task content, context packs, exported user files, and raw MCP query values.

## Repair Storage

`POST /v1/maintenance/repair-storage` is intentionally narrow:

1. Create a zip backup of the vault and SQLite index.
2. Remove FTS rows for missing or inactive memories.
3. Remove orphaned memory/task relationship rows.
4. Remove graph edges with missing evidence records.
5. Run SQLite `PRAGMA optimize`.
6. Rebuild the search index for active memories.
7. Return before/after diagnostics and row counts.

It does not delete canonical capture, memory, task, entity, or vault JSON records.

## MCP Tools

Agents can inspect and repair reliability through:

- `get_reliability_report`
- `get_support_bundle`
- `repair_memory_storage`
- `get_memory_diagnostics`
- `create_memory_backup`
- `rebuild_memory_search`
- `rebuild_index_from_vault`

Maintenance tools still pass through Trust controls.

## QA Commands

```bash
python3 -m unittest discover backend/tests
python3 scripts/reliability_check.py --base-url http://127.0.0.1:8766 --token "$CORTEX_API_KEY"
python3 scripts/first100_live_smoke.py --base-url http://127.0.0.1:8766 --token "$CORTEX_API_KEY"
python3 scripts/battle_test_http.py --base-url http://127.0.0.1:8766 --token "$CORTEX_API_KEY"
```

`scripts/reliability_check.py` fails if the running backend is missing the health contract, required feature flags, vault diagnostics, core reliability checks, or has a critical reliability report.
`scripts/first100_live_smoke.py` verifies the packaged first-100 loop with an isolated smoke user and cleans it up by default. `scripts/battle_test_http.py` is broader and writes lifecycle test data into the target vault, so use it for deeper backend QA rather than the default first-user demo proof.

Operational ship checks are centralized in:

```bash
python3 scripts/ops_readiness_check.py --refresh-site
```
