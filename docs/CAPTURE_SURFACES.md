# Cortex Source Surfaces

Cortex source intake is a connected-source layer, not a paste/import product. The consumer app should guide users toward connections that can keep memory current, preserve citations, and send new signals through Review.

## Consumer Surfaces

### MCP AI Tools

- Claude Desktop, Cursor, Windsurf, and compatible MCP clients connect to Cortex through the local MCP bridge.
- Connected tools can search approved memory and, when writes are allowed, save source records into Review.
- The app installs or repairs supported local MCP configs from `Connections & Privacy > AI tools`.
- Context-copy handoff is an advanced fallback, not the primary product model.

### Obsidian/Local Notes Sync

- The macOS app exposes the first native local source connector for Obsidian.
- The user chooses an Obsidian or local notes folder once.
- Cortex scans Markdown/text notes locally, cleans common Markdown and Obsidian markup, registers a source account, and streams cited records through `/v1/source-accounts/{account_id}/sync`.
- New source memory waits in Review before it becomes approved memory.

### Review And Ask

- Review is the approval boundary for connected data.
- Ask only uses approved memory by default and returns cited answers.
- Source health and sync outcomes are visible in Home and Connections & Privacy.

## Backend Ingestion Infrastructure

The backend still includes local ingestion APIs for tests, migrations, support recovery, and future connector workers:

- `POST /v1/captures`
- `POST /v1/captures/queue`
- `GET /v1/captures/{capture_id}/status`
- `POST /v1/imports/analyze`
- `POST /v1/imports`
- `GET /v1/imports`
- `DELETE /v1/imports/{import_id}`
- `POST /v1/source-accounts/{account_id}/sync`

These APIs are not the first-100-user front door in the macOS app. New product surfaces should prefer connected accounts, local app connectors, MCP bridges, or connector processes that register source accounts and sync records.

## Privacy Model

- No ambient screen recording.
- No background browser history collection.
- No private app database scraping without explicit user action and a source-account record.
- New connected records are review-first.
- Source citations must be preserved or generated as stable `source-account://...` locators.
- Unsupported service exports belong under advanced/fallback or support tooling, not onboarding.

## Reliability Rules

- Every source path flows through extraction, Review, the memory folder, search, and graph indexing.
- Duplicate records are skipped by content hash and source.
- Sync cursors and batch outcomes should be visible in source health.
- Partial failures should be inspectable without exposing private raw content in support bundles.
