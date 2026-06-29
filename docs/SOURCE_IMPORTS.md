# Source Imports

Cortex Stage 1 source ingestion turns user-selected exports, folders, and files into normalized memory candidates. Imports are local-first: the macOS app passes selected local paths to the local backend, the backend detects known export formats, creates capture records, and queues extraction jobs.

## Native Importers

These formats have dedicated parsers in `backend/app/source_ingest.py`:

| Source | Supported input | Notes |
| --- | --- | --- |
| ChatGPT | OpenAI export zip or folder with `conversations.json` | Preserves conversation title, timestamps, roles, and message order. |
| Claude | Claude export `conversations.json` / `chats.json` | Preserves chat title, sender, message text, and created time when present. |
| Notion | Markdown, CSV, and HTML export folders | Imported as page/file records with Notion source detection. |
| Email / Gmail / Apple Mail | `.mbox`, `.eml`, `.emlx` | Extracts subject, from/to/date, plain text, and HTML bodies. Gmail Takeout mbox is supported. |
| Slack | Workspace export folders or zips | Parses channel date JSON files and maps user IDs through `users.json` when present. |
| Discord | Discord data package `messages.csv` | Parses timestamps, message contents, and attachments. |
| Telegram | Telegram Desktop `result.json` | Parses chats and ordered messages. |
| Google Keep | Google Takeout Keep JSON/HTML | Parses note titles, text, and checklist content. |
| Google Chat / Hangouts | Google Takeout Chat/Hangouts `messages.json` | Parses conversation messages, creators, and timestamps. |
| Microsoft Teams | Teams JSON or CSV message exports | Parses sender, created time, and HTML/plain message bodies. |
| Zoom | `.vtt` and `.srt` transcripts | Preserves speaker lines and transcript text from meeting exports. |
| Messages | User-selected copy of iMessage `chat.db` | Read-only import of recent message text by chat. This is only read when the user explicitly selects the database copy. |
| WhatsApp | Text chat export | Parses common timestamped text exports as episodic message history. |
| Browser bookmarks and research | Chrome, Edge, Safari, and Firefox Netscape bookmark HTML exports; Chrome/Edge Bookmarks JSON; Chrome/Firefox history SQLite | Preserves bookmark titles, URLs, and bounded browser history as research/source signals. |
| Calendar | Google Calendar, Apple Calendar, and Outlook `.ics` exports | Parses event summaries, dates, locations, organizers, attendees, and descriptions. |
| Contacts | Apple Contacts, Google Contacts, and Outlook `.vcf` or CSV exports | Parses names, organizations, titles, emails, phones, URLs, and notes. |
| Twitter/X | Archive `tweets.js` and `direct-messages.js` files | Parses public tweets and direct-message text from local archive files. |
| LinkedIn | Data export `Messages.csv` and `Connections.csv` | Parses professional relationship and conversation context. |
| Docs and writing | `.txt`, `.md`, `.html`, `.csv`, `.json`, `.jsonl`, `.xml`, `.yaml`, `.rtf`, `.docx`, `.pdf` when `pypdf` is installed | Used for writing samples, notes, code, meeting notes, saved docs, and exported research. |

## Generic Import Coverage

These services work through generic Markdown, HTML, CSV, JSON, DOCX, or text exports:

- Apple Notes exported as text, HTML, RTF, PDF, or Markdown.
- Google Docs / Drive Takeout exports as DOCX, HTML, plain text, or PDF.
- Microsoft 365, OneDrive, Outlook, and Dropbox Paper exports as DOCX, HTML, PDF, CSV, JSON, or text.
- Obsidian, Logseq, Roam, Bear, Craft, Ulysses, iA Writer, and other Markdown vaults.
- Readwise, Pocket, Instapaper, Raindrop, Zotero, browser history/bookmark exports, and research archives.
- Linear, Jira, Asana, Trello, GitHub Issues, GitLab, Zendesk, Intercom, Help Scout, and customer support exports when exported as CSV/JSON/Markdown.
- Code folders and project notes when selected as folders or dropped into the Capture Inbox.

## API Contract

Supported-source catalog:

```text
GET /v1/imports/sources
```

Analyze selected paths without creating captures or import history:

```text
POST /v1/imports/analyze
{
  "paths": ["/path/to/export.zip", "/path/to/Notion Export"],
  "source_hint": "",
  "max_records": 500
}
```

The analyze response is a read-only preview: `records_found`, per-source counts, a bounded sample, and the supported-source catalog. It does not write captures, memories, jobs, or import sessions.

Import selected paths:

```text
POST /v1/imports
{
  "paths": ["/path/to/export.zip", "/path/to/Notion Export"],
  "processing": "async",
  "max_records": 1000
}
```

`processing: "async"` creates queued captures and extraction jobs. `processing: "sync"` is mainly for tests and small local imports.

Import history:

```text
GET /v1/imports?limit=50&include_deleted=false
GET /v1/imports/{import_id}
DELETE /v1/imports/{import_id}
```

`GET /v1/imports` returns import sessions with source counts, records found, queued/saved/failed/skipped totals, remaining capture/memory/task counts, timestamps, and `can_delete`. `GET /v1/imports/{import_id}` adds record-level previews and linked captures.

Import session statuses:

- `running`: records are being queued or saved.
- `complete`: all detected records were queued or saved.
- `partial`: at least one record failed.
- `empty`: no records were detected.
- `deleted`: the import batch was removed.

Deleting an import is the app-level undo for a bad batch. It hard-deletes captures created by that import plus derived memories, tasks, and graph edges, marks the session deleted, and writes an import tombstone. It is not a reversible restore operation.

Repeated imports are idempotent by content hash and source. If a record already exists for the user, Cortex marks the new import record as `duplicate`, increments `skipped`, and does not link that duplicate session to the existing capture. Undoing the duplicate session therefore cannot remove memory created by an earlier import.

## Source Account Registry

Cortex keeps durable local connector state for live-sync work without turning on cloud OAuth yet.

Connector capability catalog:

```text
GET /v1/source-accounts/catalog
GET /v1/sources/readiness
```

The catalog lists common services such as ChatGPT, Claude, Gmail, email files, docs, cloud-doc exports, Notion, Google Drive, Google Keep, Microsoft 365, Slack, Google Chat, Teams, Discord, Telegram, Messages, WhatsApp, Calendar, Contacts, GitHub, Linear, Jira, Zoom, Browser Bookmarks, Readwise, LinkedIn, Twitter/X, Apple Notes, and Obsidian. Each entry includes current import readiness, future live-sync status, auth type, scopes, supported export formats, `export_status`, and the canonical `source_ids` that imported captures and memories will use.

Branded live connectors can map to canonical import sources. For example, Gmail Takeout imports as `email`, Google Drive exports import as `cloud-docs` or `docs`, and GitHub CSV/JSON/project files import as `github` or `work-tools`. The Sources UI shows that mapping so planned OAuth connectors are not mistaken for already-connected live sync.

`GET /v1/sources/readiness` returns the product-level source summary used by the macOS Sources view: import readiness, live connector state, connected account count, sync cursor health, review backlog, active memory count, citation coverage, warnings, and next actions. It is the main endpoint for deciding whether a user's life-data sources are ready for model adaptation.

Source account lifecycle:

```text
GET    /v1/source-accounts?include_disconnected=false
POST   /v1/source-accounts
DELETE /v1/source-accounts/{account_id}
```

`POST /v1/source-accounts` records source, account label, account identifier, connection type, status, auth state, policy, and metadata. It stores metadata only; OAuth secrets are not implemented or stored in this layer. `DELETE /v1/source-accounts/{account_id}` marks the account disconnected/revoked rather than removing the historical record.

Sync cursor lifecycle:

```text
GET  /v1/sync-cursors?source_account_id={account_id}
POST /v1/sync-cursors
```

`POST /v1/sync-cursors` records an incremental cursor name/value, optional high-water mark, state metadata, and success/failure state. A successful linked cursor updates the source account `last_sync_at` and clears `last_error`; a failed cursor records `last_error` and leaves `last_completed_at` empty.

Source accounts and sync cursors are written to the local vault under `source_accounts/` and `sync_cursors/`, included in backups, restored by latest-backup restore, and rebuilt by `POST /v1/maintenance/rebuild-index-from-vault`.

## Source Trust Policies

Per-source policies are stored in `settings.source_policies` and updated through `PUT /v1/settings`.

```json
{
  "source_policies": {
    "gmail": { "mode": "review", "allow_ai_context": true, "review_required": true },
    "browser-capture": { "mode": "excluded", "allow_ai_context": false, "review_required": true }
  }
}
```

Modes:

- `default`: remove the explicit source policy and use global Trust settings.
- `review`: keep the source available only after captures from that source are approved.
- `excluded`: keep the source out of Ask, context packs, personal profiles, topic/entity summaries, and open-loop context.

The macOS Trust tab exposes these as Normal, Review first, and Keep private.

## User Authorship In Imports

Slack and email exports can contain many named speakers. By default, Cortex does not treat named-speaker preferences, writing style, or rejected approaches as the user's own memory. This prevents an external sender saying "I prefer..." from becoming a durable user preference.

Users can add identity aliases under Trust > Advanced permissions:

```json
{
  "identity_aliases": ["sarpt", "@sarpt", "sarpt@example.com"]
}
```

When aliases match a Slack handle/name or email sender, that text can seed user-authored preference, style, and negative memory. Other speakers remain external.

## App Flow

The macOS Sources tab accepts files, folders, and export bundles. It first calls `/v1/imports/analyze` to show a preview of detected records. After confirmation, it calls `/v1/imports`, queues normalized records, starts a local job run for the first batch, refreshes Model, Sources, Review, Ask, and Trust state, and shows the batch in Import History. If a selected file is not readable by the backend importer, the app falls back to its existing local text/PDF extraction path.

## Privacy Boundaries

- Cortex does not crawl apps or cloud services automatically.
- Imports only read files, folders, or exports selected by the user.
- The iMessage importer only reads a user-selected `chat.db` copy and does not request system database access.
- Browser handoff and connected AI tools remain controlled by trust settings.
- Large source imports enter the review pipeline before becoming trusted memory when review is enabled.
- Re-importing the same source skips duplicate content instead of creating duplicate captures.
- Parser guards avoid treating arbitrary linked HTML as bookmarks, skip Slack workspace metadata files, preserve rich Slack attachment/block text, prefer plain email bodies over duplicated HTML alternatives, skip email attachments, preserve escaped calendar/contact line breaks, and keep native importer citation paths in `source_url`.

## Current Limits

- Live OAuth/API connectors are not implemented yet for Gmail, Notion, Slack, Google Chat, Google Drive, Microsoft 365, Teams, Linear, Jira, GitHub, LinkedIn, Twitter/X, Zoom, or browser history.
- Notion, Google Drive, Microsoft 365, Teams, Apple Notes, Zoom, and work-tool support currently depends on user-selected exports rather than direct cloud sync.
- PDF extraction depends on optional `pypdf`; otherwise the macOS fallback can extract PDFs selected through the app.
- Very large exports are capped by record count and per-record character limits, then chunking/reranking should be improved in the next ingestion pass.
- The importer normalizes data into candidate captures; extraction quality still depends on the local heuristic extractor or the configured LLM extractor.
