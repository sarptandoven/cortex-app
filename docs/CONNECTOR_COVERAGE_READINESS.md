# Connector Coverage Readiness

This map is the product-facing source coverage plan for the first-100-user beta and the later live OAuth phase.

## Phase Definitions

First-100 beta means local-first import from user-selected exports, files, folders, or legally provided local database copies. Cortex should preview what it can read, import into review, preserve citations when possible, skip duplicates, and allow batch undo. It does not silently crawl apps, browser profiles, cloud accounts, or message databases.

Live OAuth means later direct cloud sync for services that expose appropriate APIs. That phase needs explicit user consent, least-privilege scopes, durable source account health, reconnect states, sync cursors, rate-limit handling, deletion semantics, and product copy that distinguishes sync from one-time import.

## Readiness Legend

| Status | Product meaning |
| --- | --- |
| Beta ready | Supported in the first-100 beta through a native or generic local import path. |
| Beta conditional | Supported only when the user provides a legal export/local copy or an optional parser dependency is available. |
| OAuth planned | Candidate for the later live OAuth/API sync phase. Not a beta promise. |
| Local/import only | Should remain user-selected local import unless a safer platform API becomes available. |

## Catalog Metadata Contract

`GET /v1/source-accounts/catalog` is the source of truth for connector readiness copy in product surfaces. It does not mean OAuth is implemented. Each catalog item should expose:

- `readiness_status`: one of `export-only`, `import-ready`, or `live-planned`.
- `scopes`: future live OAuth/API scopes only. Export-only and local import connectors use an empty list.
- `permissions_required`: the first-100 import permission requirement, plus future live consent scope requirements when `readiness_status` is `live-planned`.
- `first_100_note`: the beta-safe setup note. This should describe exports, selected files/folders, or legal local copies, not live account sync.

Representative catalog expectations:

| Connector | `readiness_status` | First-100 permission | Live scopes |
| --- | --- | --- | --- |
| ChatGPT | `export-only` | User-selected OpenAI data export. | None. |
| Apple Mail | `import-ready` | User-selected `.eml`, `.emlx`, or `.mbox` export files. | None. |
| Gmail | `live-planned` | User-selected Gmail Takeout or mail export files; no OAuth token. | `gmail.readonly`. |
| Notion | `live-planned` | User-selected Markdown, CSV, or HTML export. | `read_content`. |
| Slack | `live-planned` | User-selected workspace export folder or zip. | `channels:history`, `groups:history`, `im:history`. |
| GitHub | `live-planned` | User-selected issue, PR, project, CSV, JSON, Markdown, or text exports. | `repo:read`, `read:org`. |
| Obsidian | `import-ready` | User-selected Markdown vault folder. | None. |

## Coverage Map

| Source | First-100 beta path | Beta readiness | Later live OAuth/API posture | Catalog mapping |
| --- | --- | --- | --- | --- |
| Gmail | Google Takeout `.mbox`, `.eml`, or `.emlx` files selected by the user. | Beta ready | OAuth planned with read-only mail scopes, incremental cursors, reconnect, and label/thread preservation. | `gmail` maps to canonical `email`. |
| Apple Mail | User-exported `.eml`, `.emlx`, or `.mbox` files. | Beta ready | Local/import only. Do not read Mail.app storage directly without explicit user selection and permissions. | `apple-mail` maps to canonical `email`. |
| Outlook | Outlook/Microsoft export mail files, `.eml`, `.mbox`, `.ics`, contacts CSV/VCF, or Microsoft 365 exported docs. | Beta ready | OAuth planned through Microsoft Graph for mail, calendar, contacts, and files. | `outlook` maps to `email`, `calendar`, `contacts`, and `cloud-docs`. |
| Notion | Markdown, CSV, or HTML export folders. | Beta ready | OAuth planned after export quality is proven. Start read-only and preserve page/database IDs. | `notion`. |
| Google Drive/Docs | Google Takeout Drive/Docs exports as DOCX, HTML, Markdown, text, CSV, or PDF. | Beta ready through cloud-doc and document import. | OAuth planned through Drive/Docs read-only scopes with file cursors and revision safety. | `google-drive` and `google-docs` map to `cloud-docs` and `docs`. |
| Slack | Workspace export folder or zip, with `users.json` when available. | Beta ready | OAuth planned for channels, private channels, and DMs where granted. Requires workspace policy clarity. | `slack`. |
| Discord | Discord data package `messages.csv`. | Beta ready | Local/import only until an official user-consented history export or API path is product-safe. | `discord`. |
| Calendar | Google, Apple, or Outlook `.ics` exports. | Beta ready | OAuth planned for Google Calendar and Microsoft Graph calendars; Apple Calendar should stay local/import only unless permissioned local APIs are added. | `calendar`. |
| GitHub | Issue, pull request, project, CSV, JSON, Markdown, or text exports. | Beta ready through generic work-tool import. | OAuth planned for issues, PRs, discussions, and projects with repo/org read scopes. | `github` maps to `github` and `work-tools`. |
| Linear | CSV or JSON exports. | Beta ready through generic work-tool import. | OAuth/API planned for issues, comments, projects, and teams with read-only scopes. | `linear` maps to `linear` and `work-tools`. |
| Jira | CSV, JSON, or project exports. | Beta ready through generic work-tool import. | OAuth/API planned for issues, comments, projects, and changelogs with read-only scopes. | `jira` maps to `jira` and `work-tools`. |
| Obsidian/Markdown | User-selected Markdown vaults, folders, or files. | Beta ready through local folder and docs import. | Local/import only at first. A later local folder watcher is separate from OAuth. | `obsidian`, `knowledge-base`, and `docs`. |
| Apple Notes exports | User-exported HTML, RTF, PDF, Markdown, or text files. | Beta ready through generic notes/docs import. | Local/import only. Do not parse private Notes databases. | `apple-notes` maps to `apple-notes` and `docs`. |
| PDFs | User-selected PDFs or exported PDFs from another service. | Beta conditional on `pypdf` in backend or macOS fallback extraction. | Local/import only unless PDFs arrive through a live source such as Drive, Notion, or Outlook. | `pdfs` maps to canonical `docs`. |
| Browser bookmarks/history exports | Safari/Chrome/Edge/Firefox bookmark HTML or JSON exports; Chrome/Firefox history SQLite selected by the user. | Beta ready for explicit exports/copies. | Local/import only. No background browser history collection. | `browser-bookmarks` and `browser-history`. |
| iMessage exports | User-selected copy of `chat.db` or legally provided local export. | Beta conditional on explicit local-user provision. | Local/import only. No iCloud scraping, no system database access without user selection. | `imessage` maps to canonical `messages`. |

## First-100 Beta Readiness Bar

A source is ready for the first-100 beta when:

- The user can select an export, folder, file, or legal local copy without account credentials.
- `POST /v1/imports/analyze` can show a bounded preview before import.
- `POST /v1/imports` creates candidate captures with source labels and citation paths where possible.
- Review-first mode, source exclusion, duplicate skipping, and batch undo work for that import.
- Product copy states that Cortex is importing a user-provided export, not connecting live sync.
- Email, Slack, and message imports stay conservative about user authorship unless identity aliases are configured.

## Live OAuth Phase Gates

A connector should not move from planned to live until it has:

- Least-privilege OAuth scopes and user-facing consent copy.
- A source account record with healthy, expired, revoked, disconnected, and error states.
- Idempotent backfill and incremental sync cursors.
- A reconnect flow and a disconnect flow that stops future sync without deleting historical memories unexpectedly.
- Rate-limit, retry, and partial-failure behavior that is visible in source readiness.
- Citation parity with export import, so live memories remain traceable to their original source.
- A deletion/export story that matches the rest of Cortex trust controls.

## Product Guidance

For the first 100 users, the recommended setup prompt should ask for two or three high-signal sources first: email, work chat, notes/docs, or project tools. The product should describe every beta connector as "Import an export" or "Choose local files" unless a live account sync is actually implemented.

Live OAuth should be introduced source by source only after the local import loop proves useful, noisy-source review is understandable, and support can diagnose source health without seeing raw private content.
