# Source Ingestion

Cortex source ingestion turns connected services, local app connectors, MCP bridges, and support fallback imports into normalized memory candidates. The first-100-user product should lead with connected paths that can register a source account, stream records through `/v1/source-accounts/{account_id}/sync`, preserve citations, and route new records through Review.

Local export/file import still exists as backend infrastructure for tests, migrations, unsupported services, and support recovery. It should not be the normal first-run product path.

## Native Fallback Importers

These formats have dedicated parsers in `backend/app/source_ingest.py`. They are useful behind Advanced/Fallback, connector workers, migrations, and support tooling:

| Source | Supported input | Notes |
| --- | --- | --- |
| ChatGPT | OpenAI export zip or folder with `conversations.json` | Preserves conversation title, timestamps, roles, and message order. |
| Claude | Claude export `conversations.json` / `chats.json` | Preserves chat title, sender, message text, and created time when present. |
| Notion | Markdown, CSV, and HTML export folders | Imported as page/file records with Notion source detection. |
| Email / Gmail / Apple Mail / Outlook | `.mbox`, `.eml`, `.emlx` | Extracts subject, from/to/date, plain text, and HTML bodies. Gmail Takeout mbox, Apple Mail exports, and Outlook mail files are supported. |
| Slack | Workspace export folders or zips | Parses channel date JSON files and maps user IDs through `users.json` when present. |
| Discord | Discord data package `messages.csv` | Parses timestamps, message contents, and attachments. |
| Telegram | Telegram Desktop `result.json` | Parses chats and ordered messages. |
| Google Keep | Google Takeout Keep JSON/HTML | Parses note titles, text, and checklist content. |
| Google Chat / Hangouts | Google Takeout Chat/Hangouts `messages.json` | Parses conversation messages, creators, and timestamps. |
| Microsoft Teams | Teams JSON or CSV message exports | Parses sender, created time, and HTML/plain message bodies. |
| Zoom | `.vtt` and `.srt` transcripts | Preserves speaker lines and transcript text from meeting exports. |
| Messages | Advanced/Fallback iMessage `chat.db` copy | Read-only import of recent message text by chat. This is only read from an explicit local copy or legally provided export. |
| WhatsApp | Text chat export | Parses common timestamped text exports as episodic message history. |
| Browser bookmarks and history exports | Chrome, Edge, Safari, and Firefox Netscape bookmark HTML exports; Chrome/Edge Bookmarks JSON; Chrome/Firefox history SQLite through Advanced/Fallback | Preserves bookmark titles, URLs, and bounded browser history as research/source signals. |
| Calendar | Local `.ics` files/feed sync plus Google Calendar, Apple Calendar, and Outlook `.ics` exports | Parses event summaries, dates, locations, organizers, attendees, and descriptions with event-level citations. |
| Contacts | Apple Contacts, Google Contacts, and Outlook `.vcf` or CSV exports | Parses names, organizations, titles, emails, phones, URLs, and notes. |
| Twitter/X | Archive `tweets.js` and `direct-messages.js` files | Parses public tweets and direct-message text from local archive files. |
| LinkedIn | Data export `Messages.csv` and `Connections.csv` | Parses professional relationship and conversation context. |
| Docs, Markdown, and PDFs | `.txt`, `.md`, `.html`, `.csv`, `.json`, `.jsonl`, `.xml`, `.yaml`, `.rtf`, `.docx`, `.pdf` when `pypdf` is installed | Used for writing samples, notes, code, meeting notes, saved docs, PDFs, and exported research. |

## Generic Fallback Coverage

These services work through generic Markdown, HTML, CSV, JSON, DOCX, or text exports:

- Apple Notes exported as text, HTML, RTF, PDF, or Markdown.
- Google Docs / Drive Takeout exports as DOCX, HTML, plain text, or PDF.
- Microsoft 365, OneDrive, Outlook, and Dropbox Paper exports as DOCX, HTML, PDF, CSV, JSON, email files, calendar ICS, contact CSV, or text.
- Obsidian, Logseq, Roam, Bear, Craft, Ulysses, iA Writer, and other Markdown vaults.
- Readwise, Pocket, Instapaper, Raindrop, Zotero, browser history/bookmark exports, and research archives.
- Linear, Jira, Asana, Trello, GitHub Issues, GitLab, Zendesk, Intercom, Help Scout, and customer support exports when exported as CSV/JSON/Markdown.
- Code folders and project notes when selected through Advanced/Fallback support tooling.

## Connected Source Contract

Primary connected-source sync uses durable source accounts:

```text
GET    /v1/source-accounts/catalog
GET    /v1/source-accounts?include_disconnected=false
POST   /v1/source-accounts
POST   /v1/source-accounts/{account_id}/sync
DELETE /v1/source-accounts/{account_id}
GET    /v1/sync-cursors?source_account_id={account_id}
POST   /v1/sync-cursors
GET    /v1/sources/readiness
```

A connector sends records with `content`, optional `title`, original `source_url`, `external_id`, `captured_at`, and metadata. If `source_url` is absent, Cortex generates a stable `source-account://{source}/{account_id}/{external_id}` locator. `processing: "sync"` extracts immediately; `processing: "async"` stores raw captures and queues extraction.

The response reports saved, queued, skipped, failed, generated capture IDs, per-record statuses, and the updated cursor. This is the contract that should power local app integrations, MCP bridges, and future Gmail, Notion, Slack, Drive, Calendar, GitHub, Mail, Messages, browser, and AI-tool connectors.

For account-backed sync, `source_account_id + external_id` is the durable record identity. Re-syncing the same external record with unchanged content is skipped; re-syncing it with changed content replaces the capture's derived memories, tasks, graph edges, and queued extraction work under the same capture ID. Two different external records are allowed to produce separate captures even if their current text is identical, because service records often share boilerplate, signatures, or short repeated status text.

## Fallback Import API Contract

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

Fallback import selected paths:

```text
POST /v1/imports
{
  "paths": ["/path/to/export.zip", "/path/to/Notion Export"],
  "processing": "async",
  "max_records": 1000
}
```

`processing: "async"` creates queued captures and extraction jobs. `processing: "sync"` is mainly for tests and small local imports.

Fallback import history:

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

Deleting an import is the internal undo for a bad fallback batch. It hard-deletes captures created by that import plus derived memories, tasks, and graph edges, marks the session deleted, and writes an import tombstone. It is not a reversible restore operation.

Repeated fallback imports are idempotent by content hash and source. If a fallback record already exists for the user, Cortex marks the new import record as `duplicate`, increments `skipped`, and does not link that duplicate session to the existing capture. Undoing the duplicate session therefore cannot remove memory created by an earlier import. Connected sources use the account/external-record identity described above instead of collapsing every same-text service record together.

## Source Account Registry

Cortex keeps durable local connector state for connected-source sync. The macOS app now includes a first native local connector for Obsidian vaults: the user grants a vault folder once, Cortex scans Markdown/text notes, registers a source account, streams cited records through `/v1/source-accounts/{account_id}/sync`, advances a cursor, and lets Review decide what becomes trusted memory. Other source-specific OAuth/sign-in UI is still implemented connector by connector, but the shared backend contract is in place for local app integrations, MCP bridges, and connector processes.

Connector capability catalog:

```text
GET /v1/source-accounts/catalog
GET /v1/sources/readiness
```

The catalog lists common services such as ChatGPT, Claude, Gmail, Apple Mail, Outlook, email files, docs, PDFs, cloud-doc exports, Notion, Google Drive, Google Docs, Google Keep, Microsoft 365, Slack, Google Chat, Teams, Discord, Telegram, Messages, iMessage exports, WhatsApp, Calendar, Contacts, GitHub, Linear, Jira, Zoom, Browser Bookmarks, browser history exports, Readwise, Raindrop, Zotero, LinkedIn, Twitter/X, Apple Notes, and Obsidian. Each entry includes current connector readiness, future live-sync status, auth type, scopes, supported formats, `export_status`, and the canonical `source_ids` that captures and memories will use.

Branded connectors can map to canonical memory sources. For example, Gmail, Apple Mail, and Outlook mail records can map to `email`, Google Drive and Google Docs records can map to `cloud-docs` or `docs`, PDFs map to `docs`, iMessage records map to `messages`, GitHub records map to `github` or `work-tools`, and Readwise/Raindrop/Zotero records map to their branded sources or `knowledge-base`. Connections & Privacy should show working account/local-note connections first and keep export/file import under Advanced/Fallback when a source cannot connect directly yet.

See `docs/CONNECTOR_COVERAGE_READINESS.md` for the first-100-user beta coverage map and the later live OAuth readiness gates.

Citation URLs are service-aware when the importer can infer useful structure. Native chat/email imports include conversation, channel, subject, or message locators. Service-like file imports add fragments such as `service=notion&page=...`, `service=cloud-docs&provider=...&document=...`, `service=github&repository=...&file=...`, and `service=calendar&first_event=...`. Plain local `docs` imports keep the raw file path as the citation.

`GET /v1/sources/readiness` returns the product-level source summary used by the macOS Connections & Privacy surface: local-note readiness, connected account count, sync cursor health, review backlog, active memory count, citation coverage, warnings, and next actions. It is the main endpoint for deciding whether a user's life-data sources are ready for model adaptation.

Source account lifecycle:

```text
GET    /v1/source-accounts?include_disconnected=false
POST   /v1/source-accounts
POST   /v1/source-accounts/{account_id}/sync
DELETE /v1/source-accounts/{account_id}
```

`POST /v1/source-accounts` records source, account label, account identifier, connection type, status, auth state, policy, and metadata. It stores metadata only; OAuth secrets are not stored in this layer. `DELETE /v1/source-accounts/{account_id}` marks the account disconnected/revoked rather than removing the historical record.

`POST /v1/source-accounts/{account_id}/sync` accepts up to 500 records per request. It is the same contract described above and should be preferred over raw fallback imports for any connector-backed source. Connectors should send a stable `external_id` from the source service whenever possible so updates replace the prior Cortex capture instead of creating duplicates.

Sync cursor lifecycle:

```text
GET  /v1/sync-cursors?source_account_id={account_id}
POST /v1/sync-cursors
```

`POST /v1/sync-cursors` records an incremental cursor name/value, optional high-water mark, state metadata, and success/failure state. A successful linked cursor updates the source account `last_sync_at` and clears `last_error`; a failed cursor records `last_error` and leaves `last_completed_at` empty. The source-account sync endpoint uses the same cursor machinery so direct connector sync and standalone cursor updates report health consistently.

Source accounts and sync cursors are written to the local vault under `source_accounts/` and `sync_cursors/`, included in backups, restored by latest-backup restore, and rebuilt by `POST /v1/maintenance/rebuild-index-from-vault`. `POST /v1/sources/sync-due` is the source-only scheduler trigger used by the local app to run due connected-source sync jobs without draining unrelated capture or embedding work.

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

Connections & Privacy exposes these as Normal, Review first, and Keep private.

## User Authorship In Imports

Slack and email exports can contain many named speakers. By default, Cortex does not treat named-speaker preferences, writing style, or rejected approaches as the user's own memory. This prevents an external sender saying "I prefer..." from becoming a durable user preference.

Users can add identity aliases under Connections & Privacy permissions:

```json
{
  "identity_aliases": ["sarpt", "@sarpt", "sarpt@example.com"]
}
```

When aliases match a Slack handle/name or email sender, that text can seed user-authored preference, style, and negative memory. Other speakers remain external.

## Product Flow

Connections & Privacy should present only useful connection paths in the default flow. For the first-100-user checkpoint, those are:

- MCP AI tools for connected assistant access and memory writes.
- Obsidian vault sync as the first native local notes connector.

Future source rows should follow the same pattern: connect or authorize the source, register a source account, sync records, then let Review decide what becomes trusted memory. Manual sync controls, export/file import, and copy-oriented fallback flows should stay inside Advanced/Fallback, not Home, Review, or Ask.

## Privacy Boundaries

- Cortex does not crawl apps or cloud services automatically.
- Connected sources require explicit account consent, local-app permission, MCP bridge setup, or another user-approved integration.
- Advanced/Fallback imports only read files, folders, or exports selected by the user.
- The iMessage importer only reads a user-selected `chat.db` copy or legally provided local export and does not request system database access.
- Browser handoff and connected AI tools remain controlled by trust settings.
- Large fallback imports enter the review pipeline before becoming trusted memory when review is enabled.
- Re-importing the same source skips duplicate content instead of creating duplicate captures.
- Parser guards avoid treating arbitrary linked HTML as bookmarks, skip Slack workspace metadata files, preserve rich Slack attachment/block text, prefer plain email bodies over duplicated HTML alternatives, skip email attachments, preserve escaped calendar/contact line breaks, and keep native importer citation paths in `source_url`.

## Current Limits

- The generic source-account sync ingestion endpoint is implemented; branded OAuth/API sign-in flows still need to be built source by source for Gmail, Notion, Slack, Google Chat, Google Drive, Microsoft 365, Teams, Linear, Jira, GitHub, LinkedIn, Twitter/X, Zoom, and browser history.
- Advanced/Fallback import remains available for services that cannot connect directly yet.
- PDF extraction depends on optional `pypdf`; otherwise the macOS fallback can extract PDFs selected through the app.
- Very large exports are capped by record count and per-record character limits, then chunking/reranking should be improved in the next ingestion pass.
- The importer normalizes data into candidate captures; extraction quality still depends on the local heuristic extractor or the configured LLM extractor.
