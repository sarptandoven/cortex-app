# Cortex Native Vault — design & implementation plan

Goal: make Cortex store each user's memory in a **native vault** that is as durable,
portable, and user-owned as an Obsidian vault, while still supporting Cortex's
richly-structured data (typed memories, provenance, temporal validity, relationships,
embeddings). Researched against how the best open-source local-first tools do it.

## 1. What the research says (and the one cautionary tale)

- **"File over app."** Durability comes from owning **plain-text files in open formats**,
  not from the app. Obsidian is literally a folder of Markdown files you can read with any
  editor forever; the app is disposable. (Steph Ango / Obsidian.)
- **SQLite's own guidance: "files as ground truth, DB as a rebuildable cache."** SQLite is
  recommended *as* an application file format, and the durable pattern is: source files are
  truth, the database is a **derived index you can delete and rebuild**. Cortex already does
  exactly this (`rebuild_index_from_vault`) — the plan extends it, it doesn't fight it.
- **Logseq's cautionary tale (do NOT repeat).** Logseq used Markdown *as the storage/query
  engine* and hit a wall: graphs >2,000 pages took 4–10 min to load, >300 MB failed to start.
  Their fix was a SQLite DB backend — but that **abandoned plain-text portability** and split
  the ecosystem. **Lesson: never make Markdown the query engine. Markdown = source of truth;
  SQLite (FTS5 + sqlite-vec) = the fast index over it.** Cortex is already on the right side of
  this — keep it that way.
- **Structured data in plaintext: frontmatter + sidecar, not one or the other.** YAML
  frontmatter for human-readable typed fields; **sidecar files (JSON/JSONL) or the DB for
  machine-generated / large / frequently-changing data** (embeddings, version history). Never
  put vectors in the Markdown.
- **Encryption vs. longevity is a real tension.** Anytype/Standard Notes are fully E2E
  encrypted (Anytype: two-layer keys, backup node can't read content) — great privacy, but the
  files are *not* "plaintext you own forever." Resolution: **plaintext local vault by default;
  encrypt at the sync/backup layer**, not the local files.
- **Sync: files-first, CRDT later.** Local-first (Ink & Switch) favors CRDTs (Automerge 3.0,
  Yjs) for conflict-free multi-device merge, but they add real complexity. A file/folder vault
  already syncs today via iCloud/Syncthing/git; adopt CRDTs only if file-sync conflicts prove
  painful.
- **Crash-safe writes are non-negotiable.** Write temp file → `fsync` file → atomic
  `os.replace()` → `fsync` the parent directory. Anything less risks partial/corrupt files.

## 2. Where Cortex is today

- **Truth-ish:** SQLite `cortex.db` (FTS5 + `sqlite-vec`) is the working store; `CortexVault`
  (`backend/app/vault.py`) mirrors every record as **JSON** under a folder and can rebuild the
  DB (`rebuild_index_from_vault`).
- **Gap vs. the goal:** the durable mirror is **JSON, not human-readable Markdown**, so it is
  *portable* but not *Obsidian-openable / editable by the user*. That's the upgrade.

## 3. Target architecture

**Markdown-native vault as the source of truth; SQLite as the rebuildable index.**

```
CortexVault/                      # the user's native, Obsidian-openable vault
  .cortex/                        # app-owned index + config (rebuildable; like .obsidian)
    index.db                      # SQLite: FTS5 + sqlite-vec (DERIVED — safe to delete)
    embeddings/                   # vector sidecars (binary; never in markdown)
    manifest.json                 # vault schema version, checksums
    tombstones/                   # deletion records (already exists)
  memories/
    semantic/2026-07-02-sharded-sqlite-decision.md
    decision/...
    preference/...
  captures/                       # raw source material (as md when text-native)
    2026/07/<id>.md
  entities/
    people/Dana.md                # one file per entity -> wikilink target
    projects/Cortex.md
  attachments/<user_id>/...       # binaries (already user-scoped)
  settings.md                     # or settings.json (app config)
```

Openable directly in Obsidian: `.md` + YAML frontmatter, `[[wikilinks]]` between memories and
entities, an attachments folder, and an app-owned `.cortex/` (ignored like `.obsidian/`).

### Per-memory file format (frontmatter = typed fields, body = content)

`memories/decision/2026-07-02-sharded-sqlite-decision.md`
```markdown
---
id: mem_8e74cf70b6e9
kind: decision
layer: decision
confidence: confirmed
importance: 4
sector: engineering
source: obsidian
source_url: local-file://cortex-project.md#line=3
captured_at: 2026-07-02T15:04:00Z
occurred_at: 2026-06-15
valid_from: 2026-06-15
valid_to: null
superseded_by: null
entities: ["[[Cortex]]", "[[Dana]]"]
topics: [database, launch, scaling]
provenance:
  capture_id: cap_336e27347d70
  source_account_id: sacct_...
  external_id: cortex-project.md#heading=decision
---
We use sharded SQLite for the Cortex 10k launch instead of Postgres.
```
- **Body** = the atomic memory content (human-readable, editable, greppable).
- **Frontmatter** = every typed field Cortex needs (kind/layer/validity/provenance/…), all
  human-readable and Obsidian-queryable via Dataview-style tools.
- **Embeddings + FTS live in `.cortex/index.db` / `embeddings/`** — regenerated from the body,
  never stored in the Markdown (this is the Logseq lesson + the sidecar convention).
- **Relationships** (incl. Obsidian `obsidian_link` relations and extracted entity edges) are
  expressed as `[[wikilinks]]` in `entities` / inline, so the graph *is* the file links — round-
  trips to Obsidian for free.

## 4. The read/write pipeline

- **Write path (DB → vault):** on every memory create/update, render the Markdown+frontmatter
  and write it **atomically** (temp → fsync → `os.replace` → fsync dir). The `.cortex/index.db`
  is updated in the same logical op; if they diverge, the files win.
- **Rebuild (vault → DB):** extend `rebuild_index_from_vault` to parse Markdown+frontmatter
  (not JSON), re-run embeddings, rebuild FTS + vec. Losing `.cortex/` is a non-event.
- **Two-way editing (the real "native vault" promise):** a file-watcher on the vault detects
  external edits (user edits a memory in Obsidian/any editor) → debounce → re-parse changed
  files → update the index + re-embed only changed bodies. Frontmatter `id` is the stable key,
  so edits update in place; new files get ingested; deleted files → tombstone + archive (reuse
  the existing complete-scan archival + tombstone machinery so a half-synced state can't wipe
  memory).
- **Conflict/merge:** filesystem-level first (last-writer-wins per file with a `.conflict` copy,
  like Obsidian Sync). Revisit CRDT (Automerge) only if multi-device editing conflicts hurt.

## 5. Encryption & sync (decoupled from the local files)

- **Local vault: plaintext by default** — that's the entire ownership/longevity story.
- **Backup/hosted/sync: encrypted** — envelope-encrypt on the way out (age/AES; per-user key),
  matching the E2E posture of Anytype/Standard Notes, without sacrificing local plaintext.
  (This is also where the blocked-on-user KMS work lands.)
- **Sync:** works today via any file-sync (iCloud/Syncthing/git). CRDT is a later option.

## 6. Phased rollout

- **Phase 0 — decide + spec (no user-visible change).** Lock the frontmatter schema + folder
  layout; add a `vault_format_version`. Keep JSON mirror in parallel behind a flag.
- **Phase 1 — Markdown as an additional export/mirror.** Write memories as Markdown+frontmatter
  alongside the current JSON, atomically. Ship "Open my Cortex vault in Obsidian." Read path
  still uses the DB. Low risk, immediately delivers the ownership story.
- **Phase 2 — Markdown becomes source of truth.** `rebuild_index_from_vault` reads Markdown;
  drop the JSON mirror; `.cortex/index.db` is officially disposable. Add a round-trip test
  (write → wipe DB → rebuild → identical retrieval).
- **Phase 3 — two-way editing.** File-watcher + incremental reindex + re-embed; edit/rename/
  delete reconciliation via frontmatter `id` + tombstones.
- **Phase 4 — encrypted sync/backup + (optional) CRDT** for multi-device.

## 7. Risks & mitigations

- **Scale (the Logseq wall):** never query Markdown directly; the DB stays the index. Benchmark
  a 10k-note vault for write/rebuild/watch cost before Phase 2.
- **Lossy round-trip:** frontmatter must carry *every* structured field, or edits lose data.
  Round-trip test is the gate.
- **Edit conflicts (app write vs user edit):** debounce + `id`-keyed upsert + `.conflict` files;
  never blind-overwrite a newer mtime.
- **Vector staleness on edit:** re-embed only changed bodies (content hash) on reindex.
- **Migration safety:** dual-write (JSON + MD) through Phase 1; a real old→new migration test
  (mirrors the existing DB upgrade-path test discipline).

## 8. Sources

- Steph Ango — "File over app": https://stephango.com/file-over-app
- SQLite — "SQLite As An Application File Format": https://sqlite.org/appfileformat.html ; benefits: https://sqlite.org/aff_short.html
- Logseq DB vs Markdown (perf wall + portability tradeoff): https://discuss.logseq.com/t/logseq-og-markdown-vs-logseq-db-sqlite/34608 ; https://discuss.logseq.com/t/why-the-database-version-and-how-its-going/26744
- Ink & Switch — "Local-first software": https://www.inkandswitch.com/local-first/ ; Automerge: https://automerge.org/ ; Yjs: https://github.com/yjs/yjs
- Frontmatter vs sidecar conventions: https://github.com/jlevy/sidematter-format ; https://github.com/jlevy/frontmatter-format
- Anytype data security (two-layer E2E): https://doc.anytype.io/anytype-docs/advanced/data-and-security/how-we-keep-your-data-safe
- Atomic writes / crash consistency (fsync + rename + dir fsync): https://0xkiire.com/crash-consistency-fsync-rename/
