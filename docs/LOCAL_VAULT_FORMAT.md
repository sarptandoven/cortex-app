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
  memories/
    decision/
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
  attachments/
  backups/
    cortex-vault-*.zip
  exports/
```

## Source Of Truth

These files are canonical:

- `manifest.json`: vault format, version, index role, and directory contract
- `settings.json`: user behavior settings such as review flow and context-pack size
- `events.jsonl`: append-only audit log for capture, approval, archive, settings, backup, and maintenance actions
- `captures/**/*.json`: raw source text and capture lifecycle state
- `memories/**/*.json`: extracted atomic memory records
- `tasks/**/*.json`: open loops and questions
- `entities/**/*.json`: people, projects, organizations, and topics
- `graph_edges/**/*.json`: relationships between sources, memories, tasks, and entities

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

## Rebuild Flow

Use the maintenance endpoint:

```text
POST /v1/maintenance/rebuild-index-from-vault
```

The rebuild process:

1. Reads captures, memories, tasks, entities, graph edges, settings, and events from the vault.
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

The backend then backfills an empty vault from the copied index by writing captures, memories, tasks, entities, graph edges, settings, and existing audit events as JSON/JSONL. This prevents local beta users from losing earlier memories when the vault layout becomes the default.

## Backups

`POST /v1/backups` now creates a zip bundle in `backups/` containing:

- manifest
- settings
- event log
- captures
- memories
- tasks
- entities
- graph edges
- attachments
- a clean SQLite snapshot as `index.sqlite`

Backups intentionally exclude prior backups to avoid recursive archives.

## Privacy Notes

The vault is local disk storage. Cortex should not upload it unless the user explicitly enables a future sync feature.

Before public release, the app should add:

- optional vault location picker
- clear "open vault folder" action
- delete-all-data flow
- restore-from-backup flow
- encrypted-at-rest option
