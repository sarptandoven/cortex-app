# Consumer Copy Review

Scope: docs-only copy review. This file proposes consumer-safe UI language for the macOS app, site, README, and user-facing docs. It does not require changing backend contracts, config keys, API names, file names, logs, or protocol documentation.

## Copy Principles

- Lead with what the user can do, not the implementation detail.
- Treat the primary beta loop as: connect MCP or Obsidian/local notes, sync automatically, review, then ask with citations.
- Lead source setup with connected services and direct local app integrations. Mention export, file, folder, or drop import only as `Advanced/Fallback`.
- Do not present copied context, memory briefs, or manual imports as the default setup or success path.
- Keep MCP visible when it is the actual connection path; keep low-level protocol config and generated setup details in advanced surfaces.
- Prefer "AI tool" or "assistant" over "agent" in consumer UI.
- Prefer "memory folder" or "Cortex data folder" over "vault" unless showing the real `Cortex.vault` path.
- Prefer "local service" or "memory engine" over "backend" in consumer UI.
- Prefer "scoped memory", "approved memory", "personal adaptation profile", or "memory view" over "context pack" in consumer UI. Avoid "memory brief" as a primary product frame.
- Prefer "follow-ups", "open tasks", or "open questions" over "open loops".
- Prefer "first release", "beta", or "current beta" over "MVP".

## Term Map

| Technical term | Consumer replacement | Keep technical term when |
| --- | --- | --- |
| MCP | MCP connection, AI tool connection, local AI tool connection | Showing protocol docs, generated config, file names, or a setup label such as "MCP config" |
| context pack | scoped memory, approved memory, memory view, personal adaptation profile | Explaining a legacy developer/API endpoint |
| agent | AI tool, assistant, connected AI tool | Developer docs or model/provider implementation details |
| backend | local service, local memory engine, Cortex service | Backend docs, API docs, logs, diagnostics for engineers |
| vault | memory folder, Cortex data folder, saved memory folder | Showing the literal `Cortex.vault` folder or storage format docs |
| manual import, file drop, selected export | Advanced/Fallback import for unsupported services | Recovery, migration, or a service that has no supported connector yet |
| MVP | first release, beta, current beta | Engineering roadmap docs only |
| open loops | follow-ups, open tasks, open questions | Internal extraction labels or technical schema docs |

## Public Site

Surface: `site/index.html`, plus the landing-page copy mirrored in `docs/DISTRIBUTION.md`.

| Current string | Proposed string |
| --- | --- |
| stale agent-first tagline copy | `Cortex keeps useful AI context on your Mac so ChatGPT, Claude, Cursor, and other tools can pick up where you left off.` |
| stale agent-first slogan | `Private memory for your AI tools` |
| stale agent/open-loop hero copy | `Cortex connects MCP tools and Obsidian/local notes, syncs useful context on your Mac, then answers questions with reviewed memory and citations.` |
| `Data` / `Local vault` | `Data` / `On your Mac` |
| `Assistants` / `ChatGPT, Claude, Cursor, MCP` | `Works with` / `ChatGPT, Claude, Cursor` |
| stale paste-first product description | `Cortex connects to the sources and AI tools you use, turns reviewed decisions, notes, project details, people, and follow-ups into memory, then lets approved tools retrieve cited context.` |
| `Import selected exports, folders, and files from chats, email, notes, messages, docs, writing, bookmarks, calendars, contacts, and work tools.` | `Connect supported services and local apps for chats, email, notes, messages, docs, writing, bookmarks, calendars, contacts, and work tools.` |
| old copied-context-first action | `Connect MCP or Obsidian/local notes, sync automatically, review useful memory, then ask with cited context.` |
| old copy/context-pack-first launch framing | `Cortex starts with connected services and direct local setup for tools that support it. Advanced/Fallback import is available only when a source cannot connect yet.` |
| old agent-permissions storage copy | `Cortex stores memory on your Mac, shows storage and backup status, and lets you choose what connected AI tools can read, change, or export.` |
| `Local vault` | `On your Mac` |
| `Readable JSON records and a rebuildable SQLite index live on disk.` | `Readable files and a rebuildable search index stay on your Mac.` |
| `Agent permissions` | `AI tool permissions` |
| `Reads, writes, exports, and maintenance actions are controlled separately.` | `Reading, saving, exporting, and repair actions are controlled separately.` |
| `Choose vault` | `Choose memory folder` |
| `Keep the default local vault or choose a folder you already back up.` | `Keep the default folder or choose one you already back up.` |
| old copied-context install fallback | `Connect a supported tool directly; use copied context only when direct connection is not available.` |
| stale agent positioning copy | `Cortex is not just AI memory. It is your personal operating model for AI tools.` |
| stale agent future copy | `The beta starts local and simple. The long-term product is the memory and action layer that helps AI tools make progress the way you would.` |

Surface: `site/app.js` canvas labels.

| Current string | Proposed string |
| --- | --- |
| `Open loop` | `Follow-up` |
| `MCP` | `AI tool` |

Surface: `site/privacy.html`.

| Current string | Proposed string |
| --- | --- |
| `A readable local vault plus a rebuildable SQLite index for search and graph views.` | `A readable local memory folder plus a rebuildable search index for search and map views.` |
| `The user can choose a different vault folder during setup.` | `The user can choose a different memory folder during setup.` |
| `The app does not require a hosted backend for the local beta.` | `The app does not require a cloud service for the local beta.` |
| old context-pack privacy copy | `Context is shared when the user copies chat context or enables a direct local connection.` |
| `Trust controls can block agent reads, writes, exports, and maintenance actions.` | `Trust controls can block connected AI tools from reading, saving, exporting, or running maintenance actions.` |

## macOS Onboarding

Surface: first-run setup in `macos/Sources/CortexApp.swift`.

| Current string | Proposed string |
| --- | --- |
| `Vault` | `Memory Folder` |
| `Choose where your local memory lives and confirm the vault is healthy.` | `Choose where your local memory lives and confirm the folder is ready.` |
| `Your memory stays in a normal folder on this Mac. Cortex uses a local index for speed, but the vault files remain readable, portable, and backup-friendly.` | `Your memory stays in a normal folder on this Mac. Cortex keeps a fast search index beside readable files, so your data stays portable and backup-friendly.` |
| `Vault folder` | `Memory folder` |
| `Vault ready` | `Folder ready` |
| `Local index` | `Search index` |
| `Starting local backend` | `Starting local memory engine` |
| `Recoverable` / `Rebuild index` | `Recoverable` / `Rebuild search` |
| `Context pack size: N` | `Context to include: N items` |
| `Start with one useful preference, decision, project detail, or open loop. A good first memory makes Cortex useful immediately.` | `Start with one useful preference, decision, project detail, or follow-up. A good first memory makes Cortex useful immediately.` |
| old paste-ready first-run completion copy | `Connect MCP or Obsidian/local notes now. Use copied context or Advanced/Fallback import only when direct connection is not available.` |
| `No local MCP apps detected` | `No supported local apps detected` |
| `Copy MCP Config` | `Copy Advanced Setup` |
| `Cortex is ready to save memory locally and reuse it in AI sessions.` | `Cortex is ready to save memory locally and reuse it in your AI tools.` |
| `Backend` | `Local service` |
| `Open Vault` | `Open Folder` |

## macOS Integrations

Surface: integration center and setup cards.

| Current string | Proposed string |
| --- | --- |
| `One-click MCP` | `Direct setup` |
| `Developer tools` | `Coding tools` |
| `Local and team stacks` | `Local and team tools` |
| `Connect Cortex everywhere` | `Connect your AI tools` |
| old copy-handoff-first integration framing | `Set up supported services and local tools directly; use copied context only when direct connection is not available.` |
| `Local API` | `Local service` |
| `Copy MCP` | `Copy Advanced Setup` |
| `Copy MCP Config` | `Copy Advanced Setup` |
| `Copy Chat Context` | `Copy Chat Context` |

Recommended integration-card rewrites:

| Current string | Proposed string |
| --- | --- |
| `Adds Cortex memory tools directly inside Claude Desktop through MCP.` | `Lets Claude Desktop search and use Cortex memory directly.` |
| old Claude context-pack tool description | `Use Claude Desktop to search Cortex memory directly and save useful updates for Review.` |
| old Cursor context-pack tool description | `Gives Cursor access to saved project memory through a direct local connection.` |
| `Restart Cursor, then enable the cortex MCP server in Cursor settings if prompted.` | `Restart Cursor, then enable Cortex in Cursor settings if prompted.` |
| `Use Cortex before implementation tasks: search memory for project decisions, people, and open loops.` | `Use Cortex before implementation tasks: search memory for project decisions, people, and follow-ups.` |
| `Connects Windsurf/Cascade to Cortex through the local MCP stdio bridge.` | `Lets Windsurf use Cortex memory through a local connection.` |
| `Restart Windsurf after installing the MCP server.` | `Restart Windsurf after installing the Cortex connection.` |
| `Installs Cortex as a Cline MCP server for VS Code agent workflows.` | `Adds Cortex memory to Cline coding sessions in VS Code.` |
| `Adds Cortex MCP tools for Roo Code coding sessions.` | `Adds Cortex memory tools for Roo Code coding sessions.` |
| `Copy a Cortex MCP server definition for VS Code user or workspace MCP setup.` | `Copy Cortex advanced setup for VS Code user or workspace settings.` |
| `Copy a ready command/config snippet for Claude Code MCP setup.` | `Copy a ready setup command for Claude Code.` |
| old ChatGPT context-pack copy | `Use copied Cortex context for ChatGPT only when a direct connection is not available.` |
| old ChatGPT paste-first instruction | `Paste Cortex context into ChatGPT as an Advanced/Fallback path after direct tool connections are unavailable.` |
| old Grok context-pack copy | `Use copied Cortex context for Grok only when a direct connection is not available.` |
| old Poe context-pack copy | `Paste Cortex context into Poe only as an Advanced/Fallback path when direct connection is unavailable.` |
| `Copy Cortex MCP/API settings for local model workflows.` | `Copy Cortex local connection settings for local model workflows.` |
| `Use Cortex's local API or MCP bridge with local model agents that support tools.` | `Use Cortex's local service with local model tools that support direct connections.` |
| `Copy Cortex MCP/API settings for team chat deployments.` | `Copy Cortex local connection settings for team chat deployments.` |
| old AnythingLLM export/context-pack copy | `Use copied Cortex chat context in AnythingLLM workspaces when direct connection is not available.` |

## macOS Today And Capture

Surface: Today tab, product loop, capture tab, and any backend-provided copy rendered in the UI.

| Current string | Proposed string |
| --- | --- |
| `Cortex is starting the local memory engine.` | Keep. This is already consumer-safe. |
| `N memories [separator] N open loops` | `N memories [separator] N follow-ups` |
| `Open` | `Follow-ups` |
| `Open loops` | `Follow-ups` |
| `Use in an AI chat` | Keep. This is clear. |
| `Copy Context` | Keep. This is clear. |
| `Copy Focus` | `Copy Focused Context` |
| `N recent [separator] N decisions [separator] N open` | `N recent [separator] N decisions [separator] N follow-ups` |
| `Active context` | `Active memory` |
| `Save once from any surface. Cortex structures it, reviews it, and makes it reusable in your AI apps.` | `Save once from connected services, app integrations, clipboard, notes, or links. Cortex organizes it and makes it reusable in your AI tools.` |
| `Use the bookmarklet to capture selected text or page text from ChatGPT, Claude, docs, email, and web research into local Cortex memory.` | `Use the bookmarklet to save selected text or page text from ChatGPT, Claude, docs, email, and web research into Cortex.` |
| `Capture Inbox: path` | `Advanced/Fallback intake path` |

Backend-provided strings that surface in Today should be cleaned up in a later source pass, without changing endpoint names:

| Current string | Proposed string |
| --- | --- |
| `Start with a decision, preference, project detail, or open loop.` | `Start with a decision, preference, project detail, or follow-up.` |
| old context-pack recommended action | `Copy context before your next ChatGPT, Claude, Cursor, or AI session.` |
| `Clear or update N open loop(s).` | `Close or update N follow-up(s).` |
| old topic context-pack recommended action | `Copy context for #topic before your next AI session.` |

## macOS Connections & Privacy

Surface: Connections & Privacy sheet.

| Current string | Proposed string |
| --- | --- |
| `N active memories [separator] N pending [separator] N agent events this week` | `N active memories [separator] N pending [separator] N AI tool actions this week` |
| `Let AI use pending saves` | Keep. This is clear. |
| old context-pack trust copy | `Turn this off when only approved saves should appear in search and copied context.` |
| `Agent read access` | `AI tool read access` |
| old MCP-agent read copy | `Connected AI tools can search memory, read today's context, and view memory stats.` |
| `Agent write access` | `AI tool save access` |
| old MCP-agent write copy | `Connected AI tools can save, approve, archive, or delete memory when allowed.` |
| `Agent export access` | `AI tool export access` |
| old MCP-agent export copy | `Connected AI tools can use approved memory directly; export remains an explicit advanced permission.` |
| `Context pack size: N` | `Context to include: N items` |
| `Audit Trail` | `Activity History` |
| `Captures, approvals, backups, settings, and agent tool calls will appear here.` | `Saves, approvals, backups, settings changes, and connected AI tool actions will appear here.` |
| `Receipts and recovery` | `History and recovery` |
| `Copy Redacted Context` | `Copy Safe Context` |
| `Open Vault` | `Open Folder` |
| old backend/agent/context-pack trust copy | `Trust controls apply to the local service, connected AI tools, copied context, and exports. Your memory folder stays on this Mac.` |

## macOS Settings, Health, And Reliability

Surface: Settings tab.

| Current string | Proposed string |
| --- | --- |
| `Backend diagnostics` | `Advanced diagnostics` |
| `Backend` | `Local service` |
| `Endpoint` | `Service URL` |
| `API token` | `Access token` |
| `Backend: status` | `Service: status` |
| `Log: path` | `Log file: path` |
| `Backend` health pill | `Service` |
| `Contract` health pill | `Compatibility` |
| `Repair Storage` | `Repair Search and Storage` |
| `Support Bundle` | `Export Support Bundle` |
| `FTS` | `Search` |
| `Relations` | `Map links` |
| `Vault` health pill | `Folder` |
| `Vault: path` | `Folder: path` |
| `Open Vault` | `Open Folder` |
| `For the local beta, updates are manual: download the new DMG, quit Cortex, replace the app, and reopen. Your vault stays on disk.` | `For the local beta, updates are manual: download the new DMG, quit Cortex, replace the app, and reopen. Your memory folder stays on disk.` |

## README And User-Facing Docs

The README and public distribution docs are closer to customer-facing copy than engineering reference. They should use the same consumer-safe terms as the site, while preserving exact commands and paths.

Recommended README rewrites:

| Current string | Proposed string |
| --- | --- |
| `Cortex is a local-first memory layer for AI agents.` | `Cortex is private memory for the AI tools you already use.` |
| old Stage 1 import-first feature wording | `Connected-source setup for supported services and direct app integrations, with Advanced/Fallback import only when a source cannot connect yet` |
| old first-import setup wording | `first connected source` |
| `MCP-compatible tools` | `tools that support direct local connections` |
| `open loops` | `follow-ups` |
| `Bundled local backend on 127.0.0.1:8766` | `Bundled local service on 127.0.0.1:8766` |
| `User-owned local vault at path` | `User-owned memory folder at path` |
| old copy-pack feature wording | `Copy-ready chat context` |
| `Local MCP bridge and one-click config helpers` | `Advanced local connection and one-click setup helpers` |
| `Trust controls for agent reads, writes, exports, redaction, and maintenance` | `Trust controls for connected AI tools, exports, redaction, and repair actions` |
| `The packaged app starts the local backend automatically.` | `The packaged app starts the local service automatically.` |

Technical docs can keep implementation terms when they are the subject of the doc:

- Keep `MCP` in `docs/MCP_INTEGRATIONS.md`, API endpoint docs, generated config examples, and integration implementation notes.
- Keep `vault` in `docs/LOCAL_VAULT_FORMAT.md` and storage-recovery instructions, but introduce it as "Cortex's memory folder (`Cortex.vault`)" when the audience is non-engineering support.
- Keep `backend` in architecture, reliability, and hosted-system docs.
- Keep `MVP` in roadmap docs, but avoid it in install, privacy, first-run, release, or landing-page copy.

## Priority Cleanup Order

1. Public site and distribution copy: describe the connect/sync/review/ask loop first; keep `MCP`, copied context, `vault`, `backend`, and manual import/drop out of first-viewport and install language unless they are advanced or literal technical references.
2. First-run onboarding: replace `Vault`, `Backend`, `MCP Config`, `context pack size`, and import-first setup before beta users see setup.
3. Trust controls: replace `Agent read/write/export` with explicit connected-AI-tool permissions.
4. Today: replace `open loops` and backend-provided copied-context strings that appear in recommended actions.
5. Settings diagnostics: keep technical detail behind "Advanced diagnostics", but rename headings and pills to service/folder/search language.
