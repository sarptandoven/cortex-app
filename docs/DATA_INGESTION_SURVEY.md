# Cortex data-ingestion survey — easiest path per service

## 1. TL;DR strategy — three ingestion tiers

Cortex is local-first: it builds a private, cited model of a person from *their own* content, on-device. The guiding principle is **meet users where their data already is.** Every service falls into one of three tiers, ordered by how much users will actually complete the flow:

| Tier | Path | Why it wins | Auth cost |
|------|------|-------------|-----------|
| **Tier 1 — Local file** | Read the on-disk DB/folder the app already keeps on the Mac (SQLite, plist, JSON, Markdown). | Zero auth, zero waiting, always fresh, works offline. Best UX by far. | macOS TCC only (Full Disk Access or a framework permission prompt). |
| **Tier 2 — Official export** | User downloads one export (Takeout / in-app "Export data" / ENEX / CSV); Cortex auto-detects the file and parses it. | No credentials to broker, no API to maintain. The only path for cloud apps with no read API — including *all* consumer AI chat history. | None (user is already logged in on the web/desktop app). |
| **Tier 3 — OAuth / API token** | Live API pull. Prefer **PKCE loopback** (public desktop client, no secret) where the provider supports it; fall back to Cortex's **hosted broker** for confidential clients that require a `client_secret`. Simplest of all is a **personal API token** the user pastes. | Live/incremental sync, always current. | Highest friction: consent screens, verification, token custody, refresh. |

**The core principle:** prefer the tier with the least friction that reliably gets the data. A local SQLite file (Tier 1) beats an export (Tier 2) beats OAuth (Tier 3) — but be honest when a lower tier is genuinely impossible. Notably, **consumer AI-chat history has NO read API at any tier** — export is the only path, so frictionless export detection is a first-class feature, not a fallback.

---

## 2. Master comparison table

| Service | Popularity | Best path | Auth / effort | One-line how |
|---------|-----------|-----------|---------------|--------------|
| **iMessage** | very-high | local-file | none / FDA · easy | Read `~/Library/Messages/chat.db` (+wal/shm); decode `attributedBody` for text |
| **Apple Notes** | very-high | local-file | none / FDA · moderate | `NoteStore.sqlite`; `ZICNOTEDATA.ZDATA` = gzip+protobuf |
| **Apple Contacts** | very-high | local-file | Contacts prompt · trivial | `CNContactStore` (framework); abcddb fallback |
| **Apple Calendar** | very-high | local-file | Calendar prompt · trivial | EventKit `EKEventStore`; `Calendar.sqlitedb` fallback |
| **WhatsApp (macOS)** | very-high | local-file | none / FDA · easy | `ChatStorage.sqlite` in group container (plaintext since 2026) |
| **Obsidian** | high | local-file | none / TCC · trivial | Read `.md` vaults; discover via `obsidian.json` |
| **Bear** | high | local-file | none / FDA · easy | `database.sqlite`; `ZSFNOTE.ZTEXT` = Markdown |
| **Things** | medium | local-file | none / FDA · easy | `main.sqlite`; `TMTask` table |
| **Logseq** | medium | local-file | none / TCC · easy | Read `.md` vault (OG) or SQLite `.db` (new) |
| **Signal (macOS)** | high | local-file | Keychain consent · moderate | SQLCipher `db.sqlite`; key via `Signal Safe Storage` keychain |
| **Chrome** | very-high | local-file | none · easy | `History` SQLite + `Bookmarks` JSON per profile |
| **Arc** | medium | local-file | none · easy | Chromium `History`; Spaces in `StorableSidebar.json` |
| **Safari** | very-high | local-file | none / FDA · easy | `History.db` + `Bookmarks.plist` (FDA required) |
| **ChatGPT** | very-high | export | none · easy | Data Controls → Export → email zip → `conversations.json` (tree) |
| **Claude** | very-high | export | none · easy | Settings → Privacy → Export → email zip → `conversations.json` (flat) |
| **Gemini** | very-high | export | none · moderate | Takeout → My Activity → **Gemini Apps** (HTML/JSON) |
| **Copilot (consumer)** | high | export | none · easy | Privacy Dashboard → Export activity → direct CSV download |
| **Grok** | high | export | none · moderate | `accounts.x.ai/data` zip (or X archive for X-only users) |
| **Perplexity** | high | export | none · hard | GDPR data request (email/form), ~30-day SLA — no bulk button |
| **Poe** | medium | export | none · moderate | Per-chat `@savechats`/`@export-chat` bots; no bulk export |
| **NotebookLM** | medium | export | none · moderate | Takeout (includes chat history); per-note → Google Docs |
| **Google Keep** | high | export | none · easy | Takeout → `Keep/` folder → per-note `.json` |
| **Evernote** | high | export | none · easy | Desktop app → Export → ENEX (`.enex` XML/ENML) |
| **Roam** | medium | export | none · moderate | "Export All" → JSON/EDN (keeps block tree) |
| **Craft** | medium | export | none · moderate | Desktop → Export As → TextBundle / Markdown |
| **Telegram (macOS)** | high | export | none · moderate | Telegram **Desktop** → Export data → `result.json` |
| **Instapaper** | medium | export | none · trivial | Web → Export → `instapaper-export.csv` |
| **Readwise / Reader** | medium | api-token | token · easy | Paste token → `api/v2/export/` + `api/v3/list/` |
| **ClickUp** | high | api-token | `pk_` token · easy | Paste personal token → `api.clickup.com/api/v2/` |
| **Height** | niche | api-token | `secret_` token · easy | Paste key → `api-key` header → `api.height.app` |
| **Google Contacts** | very-high | oauth (PKCE) | PKCE loopback · easy | People API `people/me/connections` (`contacts.readonly`) |
| **Google Calendar** | very-high | oauth (PKCE) | PKCE loopback · easy | Calendar API v3 events (`calendar.readonly`); reuse EventKit if local |
| **Todoist** | very-high | oauth (broker→PKCE) | broker/PKCE · easy | Sync API `sync_token=*`; scope `data:read` (comma-sep!) |
| **Linear** | high | oauth (PKCE) | PKCE loopback · easy | GraphQL `api.linear.app/graphql`; scope `read` |
| **Asana** | high | oauth (PKCE) | PKCE / broker · moderate | `oauth_authorize` PKCE; granular `*:read` scopes |
| **Trello** | very-high | oauth (token flow) | public key · easy | `/1/authorize` token flow (`scope=read`); no secret |
| **Jira Cloud** | very-high | oauth (broker) | broker (3LO) · moderate | 3LO → resolve cloudid → REST v3 search |
| **Basecamp** | medium | oauth (broker) | broker · moderate | Launchpad OAuth → `authorization.json` for account_id |
| **Google Keep API** | — | — | Workspace-admin only | ⚠️ No consumer API — use Takeout export |
| **Pocket** | medium | **none (dead)** | n/a · trivial | ⚠️ Shut down 2025; only ingest old CSV export if present |

---

## 3. Grouped detail

### 3A. AI chats — export is the *only* path (no read APIs exist)

Every consumer AI assistant exposes a *completions* API (run new prompts) but **none** exposes an endpoint to list your past conversations. Manual export is the only route for all of them. Invest in frictionless export detection here.

- **ChatGPT (OpenAI)** — very-high — **export, easy.** Click-path: ChatGPT web → Settings → **Data Controls → Export data** → confirm. OpenAI **emails** a download link (minutes–hours; link **expires ~24h**). Zip contains `conversations.json` (full history) + `chat.html` + `user.json` + images. **Auto-detect:** `conversations.json` at archive root. **Structure gotcha:** `conversations.json` is an array; each conversation has `title`, `create_time`/`update_time` (Unix **float** seconds), `current_node`, and a `mapping` dict of message **nodes** (`{id, parent, children[], message}`). It's a **TREE** (stores edits/regens) — Cortex must walk from `current_node` up via `parent` (or filter the active branch), skipping empty/system nodes. Free/Plus/Pro/Edu only; **not** Business/Enterprise. Sources: help.openai.com/articles/7260999, community.openai.com/t/954762.

- **Claude (Anthropic)** — very-high — **export, easy.** Click-path: Claude web **or** Claude Desktop → initials (lower-left) → **Settings → Privacy → Export data** (not possible from mobile). Anthropic **emails** a link (**expires ~24h**). JSON files in archive: `conversations.json` + `users.json`/`projects.json`. **Auto-detect:** `conversations.json`/`users.json`. Each conversation: `uuid`, `name`, `created_at`/`updated_at` (**ISO-8601**), `chat_messages[]` with `sender` (`human`/`assistant`), `text`, timestamps — **flatter/simpler than ChatGPT's tree**. Claude "memory" is **not** in the export. Team/Enterprise: only org Primary Owner can export. Sources: support.claude.com/articles/9450526, support.claude.com/articles/13346720.

- **Google Gemini** — very-high — **export, moderate.** Via **Google Takeout**: takeout.google.com → Deselect all → **My Activity → "All activity data included" → deselect all → check "Gemini Apps"** → OK → choose delivery/format → Create export. ⚠️ The top-level **"Gemini" checkbox exports Gems/personas, NOT chats** — conversations live under **My Activity → Gemini Apps**. Delivery: email link (~7 days) or Drive/Dropbox/OneDrive/Box; processing hours–days. Format: zip/tgz; My-Activity-style **HTML + JSON** (interleaved prompt/response records keyed by timestamp). **Auto-detect:** `My Activity/Gemini Apps/` folder. Only present if Gemini Apps Activity was ON. Sources: support.google.com/gemini/answer/16920332.

- **Microsoft Copilot (consumer)** — high — **export, easy.** account.microsoft.com/privacy → **Privacy → Copilot** → choose "Copilot apps" → **Export all activity history**. Delivery: a **CSV downloads directly** to the device (no email) — one row per activity (prompt, response, timestamp). **Auto-detect:** the CSV in the download folder. ⚠️ Consumer only — **Enterprise M365 Copilot history is Purview/eDiscovery, admin-only**. Images not fully captured. Sources: support.microsoft.com privacy dashboard article.

- **Grok (xAI)** — high — **export, moderate.** Two paths by login: (A) standalone xAI account → **accounts.x.ai/data** → "Download account data" → zip of full history as **JSON**; (B) Grok-via-X users → X archive at **x.com/settings/download_your_data** (~24h) where Grok data "may be included depending on account configuration" (not guaranteed). **Auto-detect:** conversations/grok JSON in the zip. Sources: x.ai/privacy-portal, chatexport.guide/guides/grok.

- **Perplexity** — high — **export, HARD.** No bulk "Export all" button and no history API — the weakest of the group. Path is a **GDPR/CCPA data request**: Settings → Account → "Request data export" where available, OR perplexity.typeform.com/datarequest, OR email privacy@perplexity.ai. Fulfilled by email, **up to ~30 days**. Includes queries with timestamps, thread titles, full text. Instant option is per-thread **Share → Export to PDF / Copy link** only. Be honest with users: no fast bulk path. Sources: llmnesia.com/blog, takeoutday.org/services/perplexity.

- **Poe (Quora)** — medium — **export, moderate.** No API, no account-wide export. In-app bots (one chat at a time): **`@export-chat`** (`.md`), **`@savechats`** (`.json`), `@SaveThisChat` (`.md`); poe.com/export-chat + export.tools render transcripts. Bulk = third-party browser extensions (PoeLog, Poe Exporter) scraping the logged-in session — unofficial. Parser must handle both JSON and MD. Low priority. Sources: poe.com/export-chat, github.com/KoriIku/poe-exporter.

- **NotebookLM (Google)** — medium — **export, moderate.** No API for chat history. (1) Per-note: Studio panel → three-dot → **"Export to Google Docs"** (tables → Sheets) — saved notes/reports only, **not raw chat**. (2) **Google Takeout / Workspace Data Export** DOES include "Chat history (excluding deleted), audio overviews, uploaded content, generated notes." **Auto-detect:** NotebookLM folder in Takeout archive; format underdocumented — validate against a real export. Sources: knowledge.workspace.google.com/admin/migrate/export-notebooklm-data.

### 3B. PM / dev tools — OAuth-heavy; personal API tokens where possible

- **Todoist** — very-high — **OAuth (broker now, PKCE emerging), easy.** Authorize `https://app.todoist.com/oauth/authorize?client_id=…&scope=data:read&state=…`, token at `POST https://api.todoist.com/oauth/access_token`. Full snapshot: **Sync API** `POST https://api.todoist.com/api/v1/sync` with `sync_token=*` and `resource_types=["items","projects","labels","sections","notes"]` — everything in one call, incremental re-sync after. ⚠️ **Scopes are COMMA-separated** (not spec-standard spaces) — common `invalid_scope` bug. Newer apps get 1h tokens + rotating refresh (persist it); legacy = non-expiring. `localhost` allowed for testing → loopback works. As of 2026 a **public-client PKCE path** exists (Client ID Metadata Doc, `token_endpoint_auth_method=none`) but is newer/less battle-tested than the broker. Sources: developer.todoist.com/guides, doist.github.io/todoist-api-typescript.

- **Jira Cloud** — very-high — **OAuth 3LO via broker, moderate.** Authorize `https://auth.atlassian.com/authorize?audience=api.atlassian.com&…&scope=read:jira-work read:jira-user offline_access&…&prompt=consent`; token `POST https://auth.atlassian.com/oauth/token`; then `GET https://api.atlassian.com/oauth/token/accessible-resources` for the **cloudid**, then read via `https://api.atlassian.com/ex/jira/{cloudid}/rest/api/3/search` (JQL). **No PKCE for 3LO → client_secret mandatory → broker.** Include `offline_access` for a refresh token (access ~1h; refresh expires after 90d inactivity). Granular-vs-classic scope mismatches cause misleading 401s. Cloud only. Sources: developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps.

- **Trello** — very-high — **OAuth token flow, easy.** Simplest is the **token (Authorize) flow, not full OAuth 1.0a**: redirect to `https://trello.com/1/authorize?expiration=never&scope=read&response_type=token&name=Cortex&key={API_KEY}&return_url={loopback}&callback_method=fragment`. Only your **public API key** is needed at authorize time; token returns in the URL **fragment** (loopback reads `location.hash`). Read via `GET https://api.trello.com/1/members/me/boards`, `/cards`, etc. Effectively a public-client flow — ideal for desktop. ⚠️ Generate the API key by creating a Power-Up at trello.com/power-ups/admin (old /app-key deprecated). Full OAuth 1.0a exists but needs the secret — unnecessary for read-only. Sources: developer.atlassian.com/cloud/trello/guides/rest-api/authorization.

- **Asana** — high — **OAuth PKCE, moderate.** Authorize `GET https://app.asana.com/-/oauth_authorize?…&response_type=code&code_challenge=…&code_challenge_method=S256`; token `POST https://app.asana.com/-/oauth_token`. Read `/workspaces`, `/tasks?assignee=me&workspace=…`, `/projects`, `/users/me`. **PKCE (S256) recommended**; confidential client exists but PKCE keeps the secret broker-side. ⚠️ New granular scopes (`tasks:read`, `projects:read`, `users:read`) **must be pre-registered** in the dev console AND passed. `http://localhost` not clearly supported → prefer `urn:ietf:wg:oauth:2.0:oob` or a broker HTTPS callback. Access token 1h + refresh. Sources: developers.asana.com/docs/oauth.

- **Linear** — high — **OAuth PKCE, easy.** Authorize `https://linear.app/oauth/authorize?…&response_type=code&scope=read&code_challenge=…&code_challenge_method=S256`; token `POST https://api.linear.app/oauth/token`. Everything is **GraphQL** at `https://api.linear.app/graphql` — one query fetches issues+projects+comments. Scope `read` (the default) is sufficient. **PKCE → secret-less loopback works** (`http://localhost` supported). ⚠️ Access tokens expire in **24h** (refresh; 30-min replay grace). Sources: linear.app/developers/oauth-2-0-authentication, /graphql.

- **ClickUp** — high — **personal API token, easy.** For an individual, best is a **Personal API Token** (prefix `pk_`) from Settings → Apps, pasted into Cortex, used in the `Authorization` header — no OAuth round-trip, **never expires.** Read via `https://api.clickup.com/api/v2/`: `/team`, `/team/{id}/space`, `/list/{id}/task`, `/task/{id}`. OAuth alternative is confidential-only (needs secret, Workspace admin to create) with **no granular scopes** → far clunkier than the `pk_` token for individuals. Sources: developer.clickup.com/docs/authentication.

- **Basecamp** — medium — **OAuth via broker, moderate.** 37signals Launchpad: authorize `https://launchpad.37signals.com/authorization/new?type=web_server&…`; token `POST …/authorization/token?type=web_server`; then `GET https://launchpad.37signals.com/authorization.json` to discover **account_id + API host** (`https://3.basecampapi.com/{account_id}`), then `/projects.json`, `/todos.json`, etc. **Confidential → broker** (no PKCE). ⚠️ A descriptive **User-Agent with contact email is REQUIRED** or requests are rejected. **No scopes** — token inherits full user permissions (Cortex reads only). Access token ~2 weeks + refresh. Sources: github.com/basecamp/api authentication.md.

- **Height** — niche — **personal secret API key, easy.** Paste key (prefix `secret_`) from Settings → API; auth header is unusual: **`Authorization: api-key secret_…`** (not `Bearer`). Read via `https://api.height.app/`: `GET /tasks`, `/lists`, `/workspace`, `/users`. ⚠️ ~120 req/min; OpenAPI spec is community-maintained (field drift). Smallest user base — low priority. Sources: height-api.xyz/openapi, apitracker.io/a/height-app.

### 3C. Notes / knowledge — mostly local-file wins

- **Apple Notes** — very-high — **local-file, moderate.** `~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite`. Bodies in `ZICNOTEDATA.ZDATA` = **gzip-compressed Apple protobuf** (UTF-8 text + attribute runs) → gunzip then parse. Metadata (title/folder/dates) in plain columns on `ZICCLOUDSYNCINGOBJECT`. Attachments under `…/Media/<UUID>/`. **WAL DB — copy `.sqlite`+`-wal`+`-shm` first, open read-only.** ⚠️ Needs **Full Disk Access**; sandboxed access to this group container is unreliable even with FDA → **ship unsandboxed + FDA**. Password-locked notes are encrypted before gzip — skip/flag. Reference: threeplanetssoftware/apple_cloud_notes_parser.

- **Obsidian** — high — **local-file, TRIVIAL.** Every note is a plain `.md` in a user "vault" (+ `.obsidian/` config). **Discover vaults** via `~/Library/Application Support/obsidian/obsidian.json` (paths + IDs), then walk each folder for `*.md` (skip `.obsidian/`, `.trash/`). Parse YAML front-matter + `[[wikilinks]]`/`#tags`. Cleanest source in the whole survey. ⚠️ Vaults often in iCloud/Dropbox — expect dataless placeholders + a TCC prompt. Sources: help.obsidian.md.

- **Bear** — high — **local-file, easy.** `~/Library/Group Containers/9K33E3U3T4.net.shinyfrog.bear/Application Data/database.sqlite`. Text is **Markdown** in `ZSFNOTE.ZTEXT` (+ `ZTITLE`, dates, `ZTRASHED`/`ZARCHIVED`, `ZUNIQUEIDENTIFIER`). Filter `ZTRASHED=0`. Copy WAL trio, read-only. Needs FDA (group container). Far easier than the `bear://` x-callback API (token, one note per call). Sources: bear.app/faq.

- **Logseq** — medium — **local-file, easy.** Two products: **OG** = folder of plain **Markdown** (`journals/` + `pages/` + `logseq/`) — read `.md` directly; **new DB version** = canonical **SQLite `.db`** under the graph dir (~/logseq/graphs), schema internal/unstable → ask DB users to **export to Markdown**. Detect which via presence of `logseq/`+`journals/`+`pages/`. Parse outliner blocks, `((refs))`, `[[links]]`, front-matter. Sources: github.com/logseq/docs db-version.md.

- **Things** — medium — **local-file, easy.** 3.13.1+: `~/Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/ThingsData-XXXXX/Things Database.thingsdatabase/main.sqlite` (the `.thingsdatabase` is a **package** — real file is `main.sqlite` inside). Legacy (<3.13): sandboxed Containers path `Things.sqlite3`. Tables: `TMTask`, `TMArea`, `TMTag`. ⚠️ Things uses **its own numeric date encoding** (not plain Unix) for some fields — verify. Copy WAL trio; needs FDA. References: bboc/things3-export, evelion-apps/things-api.

- **Google Keep** — high — **export, easy.** **Google Takeout → Keep → download zip.** Each note = `.html` + parallel `.json` (`title`, `textContent`, `listContent` checkboxes, `color`, `labels`, `isArchived`/`isTrashed`, `createdTimestampUsec`/`userEditedTimestampUsec` in **microseconds**, attachment refs). **Auto-detect:** `Takeout/Keep/` folder; **prefer the `.json`.** ⚠️ **Keep REST API is Workspace-enterprise-only — does NOT work for @gmail** → do not build OAuth for individuals. Sidecar naming shifted (`.supplemental-metadata.json`) — match by folder, not fixed filename. Sources: developers.google.com/workspace/keep/api, issuetracker.google.com/issues/263769283.

- **Evernote** — high — **export, easy.** **Desktop app** (Mac) → select notebook/≤100 notes → right-click → Export → **ENEX** (`.enex`). XML: each `<note>` has `<title>`, `<content>` (**ENML**, XHTML in `<en-note>`), `<created>`/`<updated>`, `<tag>`, `<resource>` (base64 attachments). Auto-detect `.enex`; strip ENML to text/markdown. ⚠️ **Cannot export ENEX from the web app** — desktop only; 100-note cap for selections (export whole notebooks); **tasks/reminders NOT included**. Legacy Thrift API is frozen — avoid. Sources: help.evernote.com/hc/articles/209005557.

- **Roam Research** — medium — **export, moderate.** No local file, no read API. In-app **"…" → Export All → JSON (or EDN)** — preserves block hierarchy + `[[page]]`/`((block))` refs (Markdown export loses block-ref fidelity). JSON = tree of pages → blocks (`:string`, `:children`, `:uid`, `:create/:edit-time`). Cortex must resolve `((uid))` refs across the tree. Prefer JSON over EDN (Clojure format). Sources: davidbieber.com/snippets/2020-04-25-roam-json-export.

- **Craft** — medium — **export, moderate.** On-disk store is a private Core Data/SwiftData cloud container with an unstable schema — **do not parse directly.** Instead: open doc → Share → **Export As → TextBundle** (`.textbundle`, packages Markdown + images) or Markdown. Auto-detect `.md`/`.textbundle`. ⚠️ Batch export is Mac/iPad only; nested blocks may flatten oddly. Sources: support.craft.do.

- **Readwise / Reader** — medium — **api-token, easy.** Personal token from readwise.io/access_token; header **`Authorization: Token <TOKEN>`**; validate `GET https://readwise.io/api/v2/auth/` (204). Two endpoints: (a) **highlights** `GET https://readwise.io/api/v2/export/` (supports `?updatedAfter=<ISO8601>`, `nextPageCursor`); (b) **Reader documents** `GET https://readwise.io/api/v3/list/` (`location`, `category`, `updatedAfter`, `pageCursor`, **`withHtmlContent=true`** for full text). ⚠️ Don't conflate v2 (int `book_id`) vs v3 (UUID). Rate limits: 240/min default, but **LIST endpoints throttled to 20/min** — page slowly, honor 429. Store token in Keychain. Sources: readwise.io/reader_api, /api_deets.

### 3D. Comms / personal — local-file dominates; export for Telegram; OAuth for Google-only accounts

- **iMessage** — very-high — **local-file, easy.** `~/Library/Messages/chat.db` (+`-wal`/`-shm`, WAL mode). Tables: `message` (text; `date` = **nanoseconds since 2001-01-01**), `handle`, `chat`, `chat_message_join`, `attachment`. ⚠️ Modern macOS stores body in **`message.attributedBody`** (serialized NSAttributedString / typedstream) when `message.text` is NULL — **decoding this is the main work.** Date → Unix: `/1e9 + 978307200`. **Requires Full Disk Access** (fails with operation-not-permitted, not file-not-found); restart app after grant. Highly privacy-sensitive — keep on-device. Sources: spin.atomicobject.com, github.com/johnlarkin1/imessage-schema.

- **WhatsApp (macOS)** — very-high — **local-file, easy.** `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite` (Core Data). `ZWAMESSAGE` (`ZTEXT`, `ZMESSAGEDATE` = **Mac-absolute seconds since 2001**), `ZWACHATSESSION`, `ZWAMEDIAITEM`; contacts in `ContactsV2.sqlite`. **Plaintext at rest** (no SQLCipher) per Mysk disclosure **2026-05-23**. ⚠️ Only the **native Mac App Store app** (not old Electron); **add a fallback** in case WhatsApp hardens this. Needs FDA. History limited to what the linked device synced. Sources: pasqualepillitteri.it/en/news/3462, group-ib.com/blog whatsapp-forensic-artifacts.

- **Signal (macOS)** — high — **local-file, moderate.** `~/Library/Application Support/Signal/sql/db.sqlite` — **SQLCipher v4**. Key is in `…/Signal/config.json` `encryptedKey`, encrypted with Electron **safeStorage** → read **"Signal Safe Storage"** from login Keychain, PBKDF2 (Chromium scheme) to AES-decrypt → 32-byte key → `PRAGMA key="x'<hex>'"`. Tables: `messages` (json + body/type/sent_at), `conversations`. ⚠️ Needs a **SQLCipher-capable driver** (stock sqlite3 won't open it); Keychain access **prompts Allow/Deny** unless Cortex is the same signed identity. FDA not needed (Application Support). Sources: vmois.dev/query-signal-desktop-messages-sqlite, github.com/fjh658/signal-decryption-tool.

- **Telegram (macOS)** — high — **export, moderate.** Local cache is SQLCipher-encrypted with a Telegram-derived key (fragile) → **prefer export.** **Telegram Desktop** (tdesktop, not the App Store macOS client) → Settings → Advanced → **Export Telegram data → JSON** → `result.json` + media. Auto-detect the export folder. ⚠️ **Secret Chats are intentionally excluded** from export and cloud cache — unobtainable. TDLib/MTProto path needs app credentials + full login — overkill. Sources: core.telegram.org/import-export.

- **Apple Contacts** — very-high — **local-file, TRIVIAL.** Prefer **`CNContactStore`** (`requestAccess(for:.contacts)`, `CNContactFetchRequest`) — first-party, normalized, schema-stable; triggers the standard Contacts TCC prompt (`NSContactsUsageDescription`). Raw DB fallback: `~/Library/Application Support/AddressBook/AddressBook-v22.abcddb` (`ZABCDRECORD` + multivalue tables; needs FDA). **iCloud contacts are already in the local store** — no CardDAV auth. Sources: michaelwornow.net/2024/12/24/mac-address-book-schema.

- **Apple Calendar** — very-high — **local-file, TRIVIAL.** Prefer **EventKit** (`EKEventStore`, `requestFullAccessToEvents`, `predicateForEvents`) — normalized `EKEvent`, **includes iCloud/CalDAV/Google calendars already added on the Mac.** macOS 14+ split permission into full-access vs write-only — request full (`NSCalendarsFullAccessUsageDescription`). Raw fallback: `Calendar.sqlitedb` (moved into `group.com.apple.calendar` on recent macOS; Mac-absolute time). Sources: github.com/ajrosen/icalPal.

- **Google Contacts** — very-high — **OAuth PKCE, easy** (or Takeout fallback). **People API** `GET https://people.googleapis.com/v1/people/me/connections?personFields=names,emailAddresses,phoneNumbers,organizations,birthdays,addresses` (paginate `pageToken`, incremental `syncToken`); also `people.otherContacts.list`. Scope **`contacts.readonly`** (`otherContacts.readonly` for auto-saved). **Auth Code + PKCE, loopback (127.0.0.1), no secret**; broker only if you want confidential refresh-token custody. ⚠️ `contacts.readonly` is a **sensitive scope → Google verification** (warning + ~100 test-user cap until verified). **Takeout → Contacts → vCard/CSV** is the friction-free no-OAuth fallback (snapshot only). `personFields` is required. Sources: developers.google.com/people/api.

- **Google Calendar** — very-high — **OAuth PKCE, easy** (or reuse EventKit). If already on the Mac, get it free via EventKit. Otherwise **Calendar API v3** `GET https://www.googleapis.com/calendar/v3/calendars/primary/events` (+ `syncToken`, `calendarList.list`). Scope **`calendar.readonly`** (or `calendar.events.readonly`). **PKCE loopback, no secret.** Zero-auth fallback: **Takeout → Calendar → `.ics`**. ⚠️ Sensitive/restricted scope → verification for public release. Sources: developers.google.com/workspace/calendar.

- **Chrome** — very-high — **local-file, easy.** History: `~/Library/Application Support/Google/Chrome/<Profile>/History` (`urls`, `visits`; timestamps = **microseconds since 1601-01-01** — `/1e6 - 11644473600`). Bookmarks: plain JSON `Bookmarks` in the same dir. **No FDA needed** (Application Support unprotected). ⚠️ Chrome holds an **exclusive lock while running** — copy first or open `?immutable=1`/nolock; grab `-wal`/`-shm`. Enumerate profiles via `Local State` → `profile.info_cache`. Sources: foxtonforensics.com chrome-history-location, sqlite.org/wal.html.

- **Arc** — medium — **local-file, easy.** Chromium history: `~/Library/Application Support/Arc/User Data/<Profile>/History` — **reuse the Chrome ingester** (same WebKit-epoch microseconds, same lock/WAL). Arc Spaces/pinned tabs are **NOT** in Chrome's `Bookmarks` — they're in **`StorableSidebar.json`** under `~/Library/Application Support/Arc/` (bespoke, version-fragile → best-effort). No FDA. ⚠️ Arc in maintenance since 2025 (focus shifted to Dia) — lower priority. Sources: foxtonforensics.com, en.wikipedia.org/wiki/Arc_(web_browser).

- **Safari** — very-high — **local-file, easy.** History: `~/Library/Safari/History.db` (`history_items` + `history_visits`; `visit_time` = **Mac-absolute seconds since 2001** — do NOT reuse the Chrome converter). Bookmarks + Reading List: `~/Library/Safari/Bookmarks.plist` (**binary plist** — plist parser, not JSON). Open tabs: `LastSession.plist`. ⚠️ **`~/Library/Safari` IS TCC-protected → Full Disk Access REQUIRED** (unlike Chrome/Arc), then restart. WAL/locked while Safari runs — copy or read-only. Sources: foxtonforensics.com safari-history-location.

- **Instapaper** — medium — **export, trivial.** instapaper.com/user (desktop) → Export section → **"Download .CSV file"** → `instapaper-export.csv` (URL, Title, folder, Timestamp), or HTML. Auto-detect `instapaper-export.csv` in `~/Downloads`. ⚠️ CSV has URLs/titles/folders but **not full article body** (re-fetch URLs if needed). OAuth 1.0a "Full" API is access-gated by Instapaper — avoid. Sources: instapaper.zendesk.com/hc/articles/30080578815245.

- **Pocket** — medium — **NONE (dead service).** Mozilla shut Pocket down: app/site/API offline **2025-07-08**, export-only until **2025-10-08**, then **all data permanently deleted**; OAuth/API retired. Only artifact: a legacy CSV/ZIP a user saved before Oct 2025 (`title, url, time_added, tags, status`). Best: **auto-detect an old Pocket export CSV/ZIP** (`part_000000.csv`) in `~/Downloads`, plus Instapaper/Readwise imports that absorbed Pocket data. **Do not build a live integration.** Sources: techradar.com pocket-shuts-down.

---

## 4. Recommended build order for Cortex

Ordered by **user-data unlocked per unit of effort.** The overarching rule: **ship the local-file ingesters first — they need zero auth and give the best UX — then wire up the frictionless exports, and only build OAuth where a service has no local file and enough users to justify it.**

### Phase 1 — Pure local-file wins (NO auth beyond macOS TCC; best UX, build first)
These are on-disk on the user's Mac right now. One Full Disk Access grant (plus first-party permission prompts for Contacts/Calendar) unlocks a huge fraction of a person's data with no waiting, no credentials, and free refresh.

1. **iMessage** (`chat.db`) — very-high, huge personal signal. Invest in the `attributedBody` decoder.
2. **Apple Contacts + Apple Calendar** (EventKit / CNContactStore) — trivial, first-party, and Calendar transitively pulls in any Google/Exchange calendar already on the Mac (saves you an OAuth).
3. **Obsidian** — trivial; discover vaults via `obsidian.json`, then just read `.md`. Cleanest source in the survey.
4. **Safari + Chrome + Arc** browser history/bookmarks — reuse one Chromium ingester for Chrome+Arc; Safari is a separate (Mac-epoch, binary-plist, FDA-required) ingester.
5. **WhatsApp** (`ChatStorage.sqlite`, plaintext) — very-high; add a fallback in case it's hardened.
6. **Bear / Things / Logseq-OG** — easy SQLite/Markdown reads once the FDA + copy-WAL plumbing exists.
7. **Apple Notes** — very-high but moderate (gzip+protobuf `ZDATA` decode; unsandboxed + FDA).
8. **Signal** — moderate (bundle SQLCipher + Keychain consent), but high-value comms.

**Shared infrastructure to build once here:** an FDA-onboarding flow; a "copy the WAL trio + open read-only/immutable" SQLite helper; and the several epoch converters (Mac-absolute seconds-since-2001 for Apple apps/WhatsApp/Safari; microseconds-since-1601 for Chromium; float/Unix seconds for exports).

### Phase 2 — One export away (no credentials; build the auto-detect + parse pipeline)
Build a generic **"drop-a-file / watch ~/Downloads → auto-detect → parse"** ingest pipeline, then add per-format parsers. **This is where consumer AI chat lives, and it has NO read API at any tier — export is the only path**, so treat frictionless export *guidance* (the exact click-path per service, "download promptly, links expire ~24h") and *detection* as a headline feature, not an afterthought.

- **AI chats (all export-only, no API — be blunt about this):** ChatGPT (`conversations.json` **tree** — walk `current_node`) and Claude (`conversations.json` **flat**) first (very-high, easy, 24h link). Then Gemini (Takeout — steer users to **My Activity → Gemini Apps**, not the Gems checkbox), Copilot consumer (direct CSV), Grok (accounts.x.ai/data). **Perplexity** (~30-day GDPR request), **Poe** (per-chat bots), **NotebookLM** (Takeout) are low-priority/high-friction — support detection but set expectations.
- **Notes/knowledge exports:** Google Keep (Takeout `.json` — no consumer API exists), Evernote (ENEX, desktop-only), Roam (JSON/EDN), Craft (TextBundle).
- **Comms/read-later exports:** Telegram (Desktop `result.json`; Secret Chats excluded), Instapaper (`instapaper-export.csv`). **Pocket** is dead — only detect a pre-Oct-2025 CSV; do not build live.

### Phase 3 — API token (paste-a-key; near-zero flow complexity, do before OAuth)
No redirect dance, no broker, no verification — just a Keychain-stored user token. Cheap wins for engaged power users.

- **Readwise / Reader** (`Authorization: Token`) — v2 highlights + v3 documents.
- **ClickUp** (`pk_` token) and **Height** (`api-key secret_…`) — personal tokens beat their clunky confidential-OAuth alternatives.

### Phase 4 — OAuth (highest friction; PKCE loopback where possible, broker where required)
Build last, and prefer **PKCE loopback (no secret shipped)** over the **hosted broker**. Only these genuinely need OAuth (no local file, meaningful user base):

- **PKCE loopback (public client, no secret):** Google Contacts + Google Calendar (`contacts.readonly` / `calendar.readonly` — *for accounts not already on the Mac*; both are sensitive scopes needing Google verification), **Linear** (`read`, GraphQL), **Asana** (register granular `*:read` scopes; prefer `oob` over localhost), **Trello** (lightweight token flow — only the public API key at authorize time), and **Todoist** (emerging PKCE; scopes comma-separated).
- **Hosted broker (confidential, `client_secret` required — no public path):** **Jira Cloud** 3LO (resolve cloudid first), **Basecamp** (discover account host via `authorization.json`; mandatory User-Agent), and Todoist's battle-tested route today.
- Lower individual-adoption team tools (Jira/Asana/Basecamp skew org, not personal) → defer within this phase.

**One honest caveat to surface in the product:** the biggest, most valuable AI-chat corpora (ChatGPT/Claude/Gemini/etc.) will *never* be a live sync — there is no read API and won't be one. The competitive edge is making their **export → auto-detect → parse** flow so smooth (precise click-paths, download-now nudges before the 24h/7-day links expire, tree-vs-flat parsers ready) that it feels almost as good as an integration.
