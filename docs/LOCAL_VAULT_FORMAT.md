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
  sync_devices/
    USER_ID/
      device_*.json
  sync_receipts/
    USER_ID/
      DEVICE_ID/
        receipt_*.json
  memories/
    decision/
      mem_*.json
      decision-summary--a1b2c3d4e5f6.md
    style/
      mem_*.json
      writing-style--b2c3d4e5f6a1.md
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
  context_packs/
    HASH_PREFIX/
      SHA256.json
  backups/
    cortex-vault-*.zip
  exports/
  People/                     # generated, rebuildable Markdown maps
  Projects/
  Orgs/
  Topics/
  Journal/
  Cortex — Start Here.md
  Constellation.canvas
  credentials.json            # private; excluded from vault sync and backups
```

## Persistence and Authority

The vault has three persistence layers with different jobs:

1. Human-readable Markdown is authoritative for accepted, user-editable memory
   fields.
2. JSON is authoritative for every non-memory record and is the machine
   companion/fallback for memories.
3. SQLite is a derived query index. It is never the only durable copy of a
   record.

The following JSON and JSONL files are canonical:

- `manifest.json`: vault format, version, index role, and directory contract
- `settings.json`: user behavior settings such as review flow and Ask memory depth
- `events.jsonl`: append-only audit log for capture, approval, archive, deletion, settings, backup, and maintenance actions
- `imports/**/*.json`: import session history, selected path summaries, source counts, bounded record previews, linked capture IDs, status, errors, and delete markers
- `source_accounts/**/*.json`: connector account metadata, health state, policy metadata, last sync time, last error, and disconnect state
- `sync_cursors/**/*.json`: incremental sync cursor values, high-water marks, per-cursor state, last completion time, and last error
- `sync_devices/**/*.json` and `sync_receipts/**/*.json`: durable sync-device state and applied-operation receipts
- `captures/**/*.json`: raw source text and capture lifecycle state
- `tasks/**/*.json`: open loops and questions
- `entities/**/*.json`: people, projects, organizations, and topics
- `graph_edges/**/*.json`: relationships between sources, memories, tasks, and entities
- `deletion_tombstones/**/*.json`: hard-delete markers that prevent older backups from restoring explicitly deleted records
- `attachments/**/*`: user-owned attachment bytes linked from durable records

`credentials.json` is a private working file containing connector credentials.
It is excluded from the generated sync ignore rules and from backup archives.
`context_packs/` contains immutable, content-addressed audit artifacts; those
packs may sync with the vault but are not restored as canonical user records.
The top-level map-of-content pages, `Journal/`, and `Constellation.canvas` are
generated views and can be regenerated from durable records.

Memory records intentionally have a dual representation:

- `memories/**/*.md` is the user-facing memory. Its body is `content`; accepted
  YAML frontmatter fields include summary, kind/layer, confidence, importance,
  status, source references, topics, entity IDs, and temporal fields.
- `memories/**/*.json` preserves the complete machine record and provides
  system fields or values not accepted from Markdown. It is also the recovery
  fallback if a note is missing or cannot be parsed.

On rebuild, records are joined by memory `id`. JSON is loaded first and the
parsed Markdown fields are overlaid on it, so Markdown wins when the same
accepted field exists in both. Fields intentionally projected or stripped by
the Markdown parser—such as generated `tags`, derived `trust_score`,
`recorded_at`, and `superseded_at`—continue to come from JSON. Generated Links
and Backlinks sections are stripped while parsing and regenerated; editing
those generated blocks does not change indexed memory content.

The `id`, `user_id`, and `capture_id` frontmatter values are identity metadata,
not ordinary content. Do not hand-edit them. A note whose `user_id` no longer
matches the account being rebuilt is outside that account's rebuild scope.

`index.sqlite` is important but derived. If the index is damaged or deleted,
Cortex can rebuild it from the durable vault files using the precedence above.

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

Normal Cortex writes persist the JSON companion first and then render the
Markdown note. A Markdown-rendering failure does not invalidate an already
durable JSON write; the next backfill can recreate the note. Conversely, a
valid Markdown-only memory can be added to SQLite during reconciliation.

## Rebuild Flow

Use the maintenance endpoint:

```text
POST /v1/maintenance/rebuild-index-from-vault
```

The rebuild process:

1. Applies deletion tombstones before loading records.
2. Reads import sessions, source accounts, sync cursors, captures, tasks,
   entities, graph edges, settings, and events from JSON/JSONL.
3. Reads memory Markdown and memory JSON, merges matching IDs with Markdown
   precedence, and retains JSON-only memories as a recovery fallback.
4. Clears the current user's derived SQLite rows.
5. Re-inserts normalized records and rebuilds FTS rows.
6. Rebuilds vector rows when `sqlite-vec` is available.
7. Appends an `index.rebuilt_from_vault` event.

This makes support and disaster recovery straightforward: preserve the vault folder, rebuild the index.

### Hand edits and deletion

Use the vault reconciliation action after editing Markdown. Once the one-time
Markdown backfill has completed, reconciliation can treat a genuinely missing
note as a requested deletion. A corrupt-but-present note is preserved for
recovery, and a safety threshold blocks a suspicious mass disappearance
instead of tombstoning most of a vault.

Deleting only a `.md` file and immediately calling the raw rebuild endpoint is
not a hard delete: the rebuild deliberately retains a JSON-only memory. Use
Cortex's delete flow (or successful post-backfill reconciliation), which
removes both representations and writes a deletion tombstone. Tombstones take
precedence during restore and rebuild so an older backup cannot resurrect a
hard-deleted record.

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
- memory Markdown notes and JSON companions
- tasks
- entities
- graph edges
- deletion tombstones
- attachments
- a clean SQLite snapshot as `index.sqlite`

Backups intentionally exclude prior backups to avoid recursive archives.

Hard-delete operations remove the selected memory, capture, or import batch from the current SQLite index and current vault files, including both representations of a memory. They also write a deletion tombstone so an older backup cannot silently restore that item later. Capture tombstones also block derived memories, tasks, and graph edges from being restored. Import tombstones block the import session plus captures and derived records linked through `import_id`.

Backup archives can be removed with `DELETE /v1/backups`, and `DELETE /v1/user-data` removes current user records plus backups by default. New backups also run count-based pruning using `CORTEX_BACKUP_RETENTION_COUNT` (default `20`) and optional age-based pruning with `CORTEX_BACKUP_RETENTION_DAYS` (default `0`, disabled). If a user explicitly keeps backups during a full local reset, item-level tombstones are preserved so future restore operations still honor earlier hard deletes.

`POST /v1/backups/restore-latest` restores durable vault records from the newest
backup archive, validates member paths before extraction, skips the archived
SQLite snapshot, reapplies current deletion tombstones, removes tombstoned
records, and rebuilds the index from restored Markdown memories plus the
remaining JSON/JSONL records.

## Privacy Notes

The vault is local disk storage. Cortex should not upload it unless the user explicitly enables a future sync feature.

Before public release, the app should add:

- optional vault location picker
- clear "open vault folder" action
- encrypted-at-rest option
