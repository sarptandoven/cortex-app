# Connector Coverage Readiness

This map is the product-facing source coverage plan for the first-100-user beta and the later live OAuth phase.

## Phase Definitions

First-100 beta means local-first connected-source setup first. A supported service, local app integration, MCP bridge, or connector process should get explicit user consent/sign-in, register a source account, and stream records into Cortex through the local sync API. Cortex should preserve citations, skip duplicates, move new signals into Review, and show source health without asking the user to manage files.

Advanced/Fallback import remains only for unsupported services, migrations, legal exports, and support recovery. It is not the primary product loop and should not be presented as the normal setup path.

Live OAuth means direct cloud sync for services that expose appropriate APIs. Cortex now has the local source-account sync ingestion contract, but source-specific OAuth/sign-in flows still need explicit user consent, least-privilege scopes, durable source account health, reconnect states, sync cursors, rate-limit handling, deletion semantics, and product copy that distinguishes account sync from fallback import.

## Readiness Legend

| Status | Product meaning |
| --- | --- |
| Beta ready | Supported today through a connected source, local app integration, MCP bridge, or direct connector sync path. |
| Beta conditional | Supported only when the user can grant a safe local integration, use an MCP bridge, or provide an export as Advanced/Fallback. |
| Token-ready | Read-only direct sync is wired through source-account sync with a user-supplied service token or API key. This is an advanced connector path, not managed OAuth. |
| Local/feed-ready | Read-only direct sync is wired through an explicit local folder, local API, selected file, or user-provided feed URL. |
| OAuth planned | Candidate for a source-specific OAuth/API sign-in flow. The generic sync ingestion contract exists, but the branded OAuth flow is not yet a beta promise. |
| Local/import only | Should remain Advanced/Fallback import unless a safer platform API becomes available. |

## Catalog Metadata Contract

`GET /v1/source-accounts/catalog` is the source of truth for connector readiness copy in product surfaces. It does not mean OAuth is implemented. Each catalog item should expose:

- `readiness_status`: one of `export-only`, `import-ready`, `live-planned`, or `token-ready`.
- `primary_beta`: true only when the connector is a real first-100 primary path in the app, not merely a parser or fallback.
- `beta_status`: one of `ready`, `active`, `planned`, `advanced-fallback`, or `needs-connector`.
- `primary_beta_path`: one of `native-local-connector`, `native-token-connector`, `connected-source-account`, `account-sign-in-planned`, `advanced-fallback-only`, or `direct-connector-needed`.
- `show_in_primary_ui`: true only for real primary beta connectors or user-connected accounts with completed sync/data evidence.
- `scopes`: read-only service scopes or permissions for token/API connectors; for planned OAuth connectors, these are future consent scopes. Export-only and local file connectors use an empty list.
- `permissions_required`: the first-100 import permission requirement, plus future live consent scope requirements when `readiness_status` is `live-planned`.
- `first_100_note`: the beta-safe setup note. This should describe the account/direct-integration path when one exists, and only mention Advanced/Fallback import when no supported connector exists.
- `connection_setup`: the structured setup contract for the app UI. For real wired connectors this includes the HTTP sync endpoint, default cursor name, max-record limits, common account fields, credential/configuration fields, secret flags, and disconnect behavior. For planned or export-only connectors this must set `available: false` and explain why no primary connector flow should be shown.

Representative catalog expectations:

| Connector | `readiness_status` | First-100 permission | Scopes or permissions |
| --- | --- | --- | --- |
| ChatGPT | `export-only` | MCP/direct AI-tool bridge where available; fallback export stays Advanced/Fallback. | None. |
| Claude | `export-only` | MCP/direct AI-tool bridge where available; fallback export stays Advanced/Fallback. | None. |
| Apple Mail | `import-ready` | Planned permissioned local mail integration; fallback `.eml`, `.emlx`, or `.mbox` stays Advanced/Fallback. | None. |
| Gmail | `token-ready` | Backend read-only Gmail sync works when a trusted OAuth access token is already available; managed Google sign-in remains planned. | `gmail.readonly`. |
| Notion | `token-ready` | Read-only page sync works with an internal integration token shared into selected pages; fallback Markdown/CSV/HTML export stays Advanced/Fallback. | `read_content`. |
| Slack | `token-ready` | Read-only selected channel sync works with a bot or user token; fallback workspace export stays Advanced/Fallback. | `channels:history`, `groups:history`, `channels:read`, `groups:read`. |
| GitHub | `token-ready` | Read-only GitHub issue and pull request sync works through source-account sync with a user-supplied token; fallback issue, PR, project, CSV, JSON, Markdown, or text export stays Advanced/Fallback. | `repo:read`. |
| Linear | `token-ready` | Read-only issue sync works with a personal API key. | `read`. |
| Jira | `token-ready` | Read-only issue sync works with a Jira Cloud site URL, Atlassian account email, and API token. | `read:jira-work`. |
| Readwise | `token-ready` | Read-only highlight sync works with a user access token. | `read`. |
| Raindrop | `token-ready` | Read-only bookmark and highlight sync works with a user API token. | `read`. |
| Calendar | `import-ready` | Read-only `.ics` file or feed sync works locally; Google/Microsoft account sign-in remains later. | None. |
| Zotero | `import-ready` | Read-only item, note, and annotation sync works through the local desktop API by default; Web API tokens are optional. | Local API, optional read token. |
| Obsidian | `import-ready` | Permissioned local vault connector in the macOS app; Markdown/text notes sync through source-account sync. | None. |

First-100 primary UI rule: MCP AI tools and Obsidian/local notes are allowed in the default path today. Gmail, Notion, Slack, Drive, Calendar, GitHub, Mail, Messages, browser data, and similar services should become primary only after they can register a source account and complete a real sync without asking the user to manage files. Export/file parser coverage, or a stale account metadata row without synced records/cursors, must not make a connector look beta-ready in the primary UI.

## First-100 / 10k Wired Connector Checklist

This checklist is the narrow readiness statement for the thirteen connector modules wired into the local backend and included in the 10k baseline catalog. "Wired" means there is an in-tree connector, a local sync route, request/response models, source-account sync integration, cursor/readiness reporting, and focused connector tests. It does not mean managed OAuth, first-run UI placement, or hosted multi-tenant production readiness.

First-100 launch copy should still treat MCP AI tools and Obsidian/local notes as the normal default path. The other wired connectors are advanced, explicit, read-only sync paths for users or operators who can provide a local folder, local API, feed URL, or service token.

| Connector | Functional now | Local-only / local-first boundary | Explicitly not promised |
| --- | --- | --- | --- |
| Obsidian | Scans a user-selected Markdown/text vault, registers a source account, syncs records through Review, preserves file citations, advances cursors, skips unchanged notes, and supersedes edited notes. | Local folder access only after the user selects the vault. This is the first native first-100 connector. | No Obsidian cloud account sync, no remote vault crawl, and no write-back to notes. |
| GitHub | Syncs read-only issues and pull requests from selected repositories with stable GitHub citations and cursor-backed source-account state. | User-supplied read token at sync time; local backend stores source-account/cursor metadata, not a managed OAuth app flow. | No GitHub OAuth install, no writes/comments, no project/discussion coverage promise, and no org-wide discovery promise. |
| Gmail | Syncs read-only messages from Gmail using an explicit access token, query/label filters, pagination, parsed message bodies, and stable message citations. | The backend can consume an access token supplied by a local connector or operator path; managed Google sign-in is not shipped. | No managed Gmail OAuth, no mailbox writes, no broad label policy UI, and no authorship claims without identity aliases. |
| Google Drive | Syncs read-only Google Drive files and exported Google Docs/text/HTML content with stable file citations, pagination, and cursor state. | The backend can consume an access token supplied by a local connector or operator path; unsupported binaries are skipped. | No managed Google OAuth, no Drive writes, no full binary/PDF OCR promise, and no Docs revision UI. |
| Outlook | Syncs read-only Outlook/Microsoft Graph mail messages with parsed bodies, pagination, and stable message citations. | The backend can consume an access token supplied by a local connector or operator path; mail is the current wired slice. | No managed Microsoft OAuth, no Outlook writes, no Teams/OneDrive/contacts coverage in this connector, and no broad tenant administration. |
| Slack | Syncs read-only messages from selected channels with Slack permalinks or stable fallback citations and per-channel cursor state. | User-supplied bot/user token and explicit channel list. | No managed Slack OAuth, no broad workspace crawl, no DM/private-channel promise beyond granted scopes, and no automatic user-authorship attribution without aliases. |
| Readwise | Syncs read-only highlights with pagination, source URLs or stable fallback citations, and source-account cursors. | User-supplied Readwise access token. | No OAuth, no write/highlight management, and no guarantee that unsupported Readwise object types sync. |
| Calendar | Syncs read-only local `.ics` files or user-provided `.ics` feeds into event records with generated safe citations. | Local file/feed only; private feed URLs are not a product surface. | No Google Calendar/Microsoft OAuth, no system calendar database access, no calendar writes, and no background calendar daemon. |
| Raindrop | Syncs read-only bookmarks and optional highlights with collection/page cursors and original URL or stable Raindrop fallback citations. | User-supplied Raindrop API token and selected collection. | No OAuth, no bookmark writes, no full account management, and no background bookmark collection. |
| Zotero | Syncs read-only items, notes, and annotations through the local desktop API by default, with optional Web API token support and Zotero item citations. | Local API is the default; attachment import is off by default. | No Zotero OAuth, no library writes, and no attachment/PDF content import promise by default. |
| Linear | Syncs read-only issues with pagination, source URLs or `linear://` fallback citations, and source-account cursors. | User-supplied Linear API key. | No OAuth, no issue writes/comments, no project/team administration, and no workflow mutation. |
| Jira | Syncs read-only Jira Cloud issues with a site URL, account email, API token, optional JQL, stable browse URLs, and cursor state. | User-supplied Atlassian API token; Jira Cloud path only. | No OAuth, no issue writes/transitions, no full project/changelog coverage promise, and no on-prem Jira guarantee. |
| Notion | Syncs read-only pages shared with a Notion internal integration token, preserving page IDs, page URLs, pagination, and optional page content. | User-supplied integration token; only pages shared with the integration are in scope. | No consumer OAuth, no whole-workspace discovery, no writes, and no full block/database fidelity promise. |

Across all thirteen, the current promise is read-only local source-account sync into Review and cited Ask. The checklist explicitly does not promise managed OAuth, secret custody, hosted background workers, hosted deletion/export receipts, team administration, two-way service writes, billing/quotas, or primary first-run UI placement for every connector.

## Coverage Map

| Source | First-100 beta path | Beta readiness | Later live OAuth/API posture | Catalog mapping |
| --- | --- | --- | --- | --- |
| ChatGPT / Claude / MCP tools | Connect local AI tools through MCP so assistants can read approved memory and write source records into Review. | Beta ready through MCP bridge. | Direct account import can stay planned; MCP output is the first useful integration surface. | `chatgpt`, `claude`, `mcp`, and canonical AI-tool sources. |
| Obsidian/Markdown | In-app Obsidian vault connector for Markdown/text notes, with review-first source-account sync. | Beta ready for explicit local vault connection. | Local folder connector first; a background watcher can build on the same cursor contract later. | `obsidian`, `knowledge-base`, and `docs`. |
| Gmail | Advanced read-only Gmail connector backed by source-account sync with an explicit access token. | Backend wired; managed OAuth planned; export remains Advanced/Fallback for unsupported setup. | OAuth planned with read-only mail scopes, incremental cursors, reconnect, and label/thread preservation. | `gmail` maps to canonical `email`. |
| Apple Mail | Planned permissioned local mail connector; fallback export stays Advanced/Fallback. | Beta conditional. | Local/import only unless a safe permissioned local integration is added. | `apple-mail` maps to canonical `email`. |
| Outlook | Advanced read-only Outlook mail connector backed by source-account sync with an explicit Microsoft Graph access token. | Backend wired for mail; managed OAuth planned; export remains Advanced/Fallback for unsupported setup. | OAuth planned through Microsoft Graph for broader mail, calendar, contacts, and files. | `outlook` maps to `email`, `calendar`, `contacts`, and `cloud-docs`. |
| Notion | Advanced Notion integration-token connector backed by source-account sync for pages shared with the integration. | Token-ready; primary OAuth is still planned. | OAuth later for consumer-grade sign-in. Preserve page/database IDs and cite page URLs. | `notion`. |
| Google Drive/Docs | Advanced read-only Drive connector backed by source-account sync with an explicit access token; Google Docs export is supported through Drive. | Backend wired for Drive files and exported Docs/text/HTML; managed OAuth planned; standalone Docs OAuth remains planned. | OAuth planned through Drive/Docs read-only scopes with file cursors and revision safety. | `google-drive` and `google-docs` map to `cloud-docs` and `docs`. |
| Slack | Advanced read-only channel connector backed by source-account sync with a bot/user token and selected channels. | Token-ready; primary OAuth is still planned. | OAuth later for channels, private channels, and DMs where granted. Requires workspace policy clarity. | `slack`. |
| Discord | Planned only if a user-consented history API or connector path becomes product-safe. | Local/import only. | Local/import only until an official user-consented history export or API path is product-safe. | `discord`. |
| Calendar | Read-only local `.ics` file/feed connector backed by source-account sync; Google/Microsoft account sign-in remains later. | Local/feed-ready; OAuth planned. | Preserve event UIDs and use generated source-account citations so local paths and private feed URLs do not leak. | `calendar`. |
| GitHub | Advanced read-only issue and pull request connector backed by source-account sync with a fine-grained personal token. | Token-ready; primary OAuth is still planned. | OAuth later for issues, PRs, discussions, and projects with repo/org read scopes. | `github` maps to `github` and `work-tools`. |
| Linear | Advanced read-only issue connector backed by source-account sync with a personal API key. | Token-ready; primary OAuth is still planned. | OAuth/API later for issues, comments, projects, and teams with read-only scopes. | `linear` maps to `linear` and `work-tools`. |
| Jira | Advanced read-only Jira Cloud issue connector backed by source-account sync with a site URL, email, and API token. | Token-ready; managed OAuth remains planned. | OAuth/API later for issues, comments, projects, and changelogs with read-only scopes. | `jira` maps to `jira` and `work-tools`. |
| Apple Notes | Planned local connector or supported export only under Advanced/Fallback. | Local/import only today. | Local/import only. Do not parse private Notes databases. | `apple-notes` maps to `apple-notes` and `docs`. |
| PDFs | PDFs arriving through a connected notes/docs source; standalone PDF import remains Advanced/Fallback. | Beta conditional. | Local/import only unless PDFs arrive through a live source such as Drive, Notion, or Outlook. | `pdfs` maps to canonical `docs`. |
| Readwise | Advanced read-only highlight connector backed by source-account sync with a Readwise access token. | Token-ready; primary OAuth is still planned. | OAuth/API later if needed; preserve book/highlight IDs and cite source URLs or stable Readwise fallback URLs. | `readwise` maps to `readwise` and `knowledge-base`. |
| Raindrop | Advanced read-only bookmark/highlight connector backed by source-account sync with a Raindrop API token. | Token-ready; primary OAuth is still planned. | OAuth/API later if needed; preserve bookmark IDs and cite original URLs or stable Raindrop fallback URLs. | `raindrop` maps to `raindrop` and `knowledge-base`. |
| Zotero | Advanced read-only local API connector for items, notes, and annotations from the Zotero desktop app; optional Web API token for synced libraries. | Local/API prototype; primary OAuth is still planned. | Keep the default local and read-only. Preserve Zotero item keys with `zotero://select/...` citations or API alternate URLs. Do not import attachment/PDF contents by default. | `zotero` maps to `zotero` and `knowledge-base`. |
| Browser bookmarks/history | Planned browser connector or MCP/browser bridge only. | Local/import only today; no background browser history collection. | Local/import only unless a user-consented browser extension or local browser connector is built. | `browser-bookmarks` and `browser-history`. |
| iMessage | Legal local export or explicit database copy only under Advanced/Fallback. | Beta conditional. | Local/import only. No iCloud scraping, no system database access without explicit local permission and a product-safe connector. | `imessage` maps to canonical `messages`. |

## First-100 Beta Readiness Bar

A source is ready for the first-100 beta when its primary path can:

- Register or confirm a source account after explicit user consent/sign-in.
- Stream records through `POST /v1/source-accounts/{account_id}/sync`.
- Preserve original service citations or generate stable `source-account://...` citations.
- Advance incremental sync cursors and surface partial failures in source readiness.
- Put new source records into Review before they become trusted memory when review is enabled.
- Skip duplicates by content hash and source.
- Stay conservative about user authorship for email, Slack, and message data unless identity aliases are configured.

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

For the first 100 users, the recommended setup prompt should start with MCP AI tools or Obsidian/local notes. Broader email, work chat, docs, calendar, and project-tool connectors should appear as planned or advanced until they can register an account and sync records into Cortex without asking the user to manage files.

Live OAuth should be introduced source by source only after the direct sync loop proves useful, noisy-source review is understandable, and support can diagnose source health without seeing raw private content.
