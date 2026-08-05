# Cortex Local Vault Format

## Purpose

Cortex V1 is local-first. User memory should live in a folder the user can inspect, copy, back up, and eventually sync without needing a hosted database.

The local vault is the durable source of truth. SQLite is the rebuildable local index used for fast search, graph queries, review state, and MCP retrieval.

## Default Location

Packaged macOS app:

```text
~/Library/Application Support/Cortex/Cortex.vault/
```

Development server:

```text
backend/data/Cortex.vault/
```

Environment overrides:

```text
CORTEX_VAULT_PATH=/path/to/Cortex.vault
CORTEX_DB_PATH=/path/to/Cortex.vault/index.sqlite
```

## Directory Layout

```text
Cortex.vault/
  manifest.json
  settings.json
  events.jsonl
  index.sqlite
  captures/
    YYYY-MM-DD/
      cap_*.json
  imports/
    YYYY-MM-DD/
      imp_*.json
  source_accounts/
    USER_ID/
      SOURCE/
        sacct_*.json
  sync_cursors/
    USER_ID/
      SOURCE/
        sync_*.json
  memories/
    decision/
      mem_*.json
    style/
      mem_*.json
    negative/
      mem_*.json
    preference/
      mem_*.json
    event/
      mem_*.json
    observation/
      mem_*.json
  tasks/
    action/
      task_*.json
  entities/
    person/
      person_*.json
    project/
      project_*.json
  graph_edges/
    contains/
      edge_*.json
  deletion_tombstones/
    USER_ID/
      capture/
        cap_*.json
      memory/
        mem_*.json
      import/
        imp_*.json
  attachments/
  backups/
    cortex-vault-*.zip
  exports/
```

## Source Of Truth

These files are canonical:

- `manifest.json`: vault format, version, index role, and directory contract
- `settings.json`: user behavior settings such as review flow and Ask memory depth
- `events.jsonl`: append-only audit log for capture, approval, archive, deletion, settings, backup, and maintenance actions
- `imports/**/*.json`: import session history, selected path summaries, source counts, bounded record previews, linked capture IDs, status, errors, and delete markers
- `source_accounts/**/*.json`: connector account metadata, health state, policy metadata, last sync time, last error, and disconnect state
- `sync_cursors/**/*.json`: incremental sync cursor values, high-water marks, per-cursor state, last completion time, and last error
- `captures/**/*.json`: raw source text and capture lifecycle state
- `memories/**/*.json`: extracted atomic memory records
- `tasks/**/*.json`: open loops and questions
- `entities/**/*.json`: people, projects, organizations, and topics
- `graph_edges/**/*.json`: relationships between sources, memories, tasks, and entities
- `deletion_tombstones/**/*.json`: hard-delete markers that prevent older backups from restoring explicitly deleted records

`index.sqlite` is important but not canonical. If the index is damaged or deleted, Cortex can rebuild it from the vault records.

## Record Guarantees

Every JSON record includes:

- `vault_record_type`
- `vault_record_version`
- `vault_updated_at`
- stable `id`
- `user_id`
- source metadata where relevant
- timestamps needed for review, search, and rebuild

Capture records include the raw saved text so a user can recover source material even if the index fails.

Import records describe a batch created from selected local paths. They keep path summaries, source counts, queued/saved/failed/skipped totals, record previews, capture IDs, errors, timestamps, status, and `deleted_at` when the batch has been undone/deleted. They do not replace capture records; captures remain the source material for extraction and retrieval. Duplicate records are marked without linking to older captures so deleting a duplicate import session cannot delete earlier trusted memory.

Memory records include both `kind` and `layer`. `kind` preserves the atomic type (`claim`, `decision`, `event`, `preference`, `style`, `negative`, `observation`, or `summary`), while `layer` controls retrieval behavior (`semantic`, `episodic`, `style`, `decision`, `preference`, or `negative`).

## Rebuild Flow

Use the maintenance endpoint:

```text
POST /v1/maintenance/rebuild-index-from-vault
```

The rebuild process:

1. Reads import sessions, source accounts, sync cursors, captures, memories, tasks, entities, graph edges, settings, and events from the vault.
2. Clears the current user's SQLite index rows.
3. Re-inserts normalized records.
4. Rebuilds FTS rows.
5. Rebuilds vector rows when `sqlite-vec` is available.
6. Appends an `index.rebuilt_from_vault` event.

This makes support and disaster recovery straightforward: preserve the vault folder, rebuild the index.

## Legacy Migration

Older local builds used:

```text
~/Library/Application Support/Cortex/cortex.db
```

On first launch, the macOS app copies that database into:

```text
~/Library/Application Support/Cortex/Cortex.vault/index.sqlite
```

The backend then backfills an empty vault from the copied index by writing captures, imports, memories, tasks, entities, graph edges, settings, and existing audit events as JSON/JSONL. This prevents local beta users from losing earlier memories when the vault layout becomes the default.

## Backups

`POST /v1/backups` now creates a zip bundle in `backups/` containing:

- manifest
- settings
- event log
- import sessions
- source accounts and sync cursors
- captures
- memories
- tasks
- entities
- graph edges
- deletion tombstones
- attachments
- a clean SQLite snapshot as `index.sqlite`

Backups intentionally exclude prior backups to avoid recursive archives.

Hard-delete operations remove the selected memory, capture, or import batch from the current SQLite index and current vault JSON records. They also write a deletion tombstone so an older backup cannot silently restore that item later. Capture tombstones also block derived memories, tasks, and graph edges from being restored. Import tombstones block the import session plus captures and derived records linked through `import_id`.

Backup archives can be removed with `DELETE /v1/backups`, and `DELETE /v1/user-data` removes current user records plus backups by default. New backups also run count-based pruning using `CORTEX_BACKUP_RETENTION_COUNT` (default `20`) and optional age-based pruning with `CORTEX_BACKUP_RETENTION_DAYS` (default `0`, disabled). If a user explicitly keeps backups during a full local reset, item-level tombstones are preserved so future restore operations still honor earlier hard deletes.

`POST /v1/backups/restore-latest` restores canonical vault records from the newest backup archive, validates member paths before extraction, skips the archived SQLite snapshot, reapplies current deletion tombstones, removes tombstoned records, and rebuilds the current SQLite index from the restored JSON/JSONL files.

## Privacy Notes

The vault is local disk storage. Cortex should not upload it unless the user explicitly enables a future sync feature.

Before public release, the app should add:

- optional vault location picker
- clear "open vault folder" action
- encrypted-at-rest option
