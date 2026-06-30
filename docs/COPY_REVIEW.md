# Consumer Copy Review

Scope: docs-only copy review. This file proposes consumer-safe UI language for the macOS app, site, README, and user-facing docs. It does not require changing backend contracts, config keys, API names, file names, logs, or protocol documentation.

## Copy Principles

- Lead with what the user can do, not the implementation detail.
- Lead source setup with connected services and direct local app integrations. Mention export, file, folder, or drop import only as `Advanced/Fallback`.
- Keep protocol names such as MCP visible only in advanced setup, generated config, and developer docs.
- Prefer "AI tool" or "assistant" over "agent" in consumer UI.
- Prefer "memory folder" or "Cortex data folder" over "vault" unless showing the real `Cortex.vault` path.
- Prefer "local service" or "memory engine" over "backend" in consumer UI.
- Prefer "scoped memory", "approved memory", "personal adaptation profile", or "memory view" over "context pack" in consumer UI. Avoid "memory brief" as a primary product frame.
- Prefer "follow-ups", "open tasks", or "open questions" over "open loops".
- Prefer "first release", "beta", or "current beta" over "MVP".

## Term Map

| Technical term | Consumer replacement | Keep technical term when |
| --- | --- | --- |
| MCP | direct AI tool connection, advanced local connection, advanced setup | Showing protocol docs, generated config, file names, or an advanced label such as "MCP config" |
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
| `Cortex is the local memory and action layer for AI agents, helping ChatGPT, Claude, Cursor, and MCP tools work from your context.` | `Cortex keeps useful AI context on your Mac so ChatGPT, Claude, Cursor, and other tools can pick up where you left off.` |
| `Local-first memory for AI agents` | `Private memory for your AI tools` |
| `Cortex gives ChatGPT, Claude, Cursor, and MCP agents your memory, preferences, decisions, and open loops so they can work with context instead of starting from zero.` | `Cortex saves your preferences, decisions, projects, people, and follow-ups on your Mac, then lets you reuse them in ChatGPT, Claude, Cursor, and other AI tools.` |
| `Data` / `Local vault` | `Data` / `On your Mac` |
| `Assistants` / `ChatGPT, Claude, Cursor, MCP` | `Works with` / `ChatGPT, Claude, Cursor` |
| `Cortex turns scattered decisions, notes, project context, people, and open loops into a reusable memory layer. Save once, review what matters, then paste or expose the right context to the AI tool you already use.` | `Cortex connects to the services and AI tools you use, turns reviewed decisions, notes, project details, people, and follow-ups into memory, then lets approved tools retrieve cited context.` |
| `Import selected exports, folders, and files from chats, email, notes, messages, docs, writing, bookmarks, calendars, contacts, and work tools.` | `Connect supported services and local apps for chats, email, notes, messages, docs, writing, bookmarks, calendars, contacts, and work tools.` |
| `Copy a context pack or connect an MCP tool before starting work in an assistant.` | `Connect a supported service or direct AI tool before starting work.` |
| `Cortex starts with copy-ready context packs and local MCP setup for tools that can connect directly. That keeps the first version useful before a hosted backend exists.` | `Cortex starts with connected services and direct local setup for tools that support it. Advanced/Fallback import is available only when a source cannot connect yet.` |
| `Cortex stores memory on the user's Mac, exposes local diagnostics, and lets users choose what agents can read, write, export, or repair.` | `Cortex stores memory on your Mac, shows storage and backup status, and lets you choose what connected AI tools can read, change, or export.` |
| `Local vault` | `On your Mac` |
| `Readable JSON records and a rebuildable SQLite index live on disk.` | `Readable files and a rebuildable search index stay on your Mac.` |
| `Agent permissions` | `AI tool permissions` |
| `Reads, writes, exports, and maintenance actions are controlled separately.` | `Reading, saving, exporting, and repair actions are controlled separately.` |
| `Choose vault` | `Choose memory folder` |
| `Keep the default local vault or choose a folder you already back up.` | `Keep the default folder or choose one you already back up.` |
| `Copy a context pack or install the local MCP config for an assistant.` | `Connect a supported tool directly; use copied context only when direct connection is not available.` |
| `Cortex is not just AI memory. It is your personal operating model for agents.` | `Cortex is not just AI memory. It is your personal operating model for AI tools.` |
| `The beta starts local and simple. The long-term product is the memory and action layer that lets agents make progress the way you would.` | `The beta starts local and simple. The long-term product is the memory and action layer that helps AI tools make progress the way you would.` |

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
| `Context is shared when the user copies a context pack or enables a local MCP integration.` | `Context is shared when the user copies chat context or enables a direct local connection.` |
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
| `Connect the tools you use most. You can finish setup with a paste-ready context pack and add direct MCP tools later from Settings.` | `Connect the services and tools you use most. Use copied context or Advanced/Fallback import only when direct connection is not available.` |
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
| `Install direct MCP tools where safe, and use copy-ready context packs everywhere else.` | `Set up supported services and local tools directly; use copy handoff only when direct connection is not available.` |
| `Local API` | `Local service` |
| `Copy MCP` | `Copy Advanced Setup` |
| `Copy MCP Config` | `Copy Advanced Setup` |
| `Copy Chat Context` | `Copy Chat Context` |

Recommended integration-card rewrites:

| Current string | Proposed string |
| --- | --- |
| `Adds Cortex memory tools directly inside Claude Desktop through MCP.` | `Lets Claude Desktop search and use Cortex memory directly.` |
| `Use Claude Desktop for direct MCP access to Cortex search, daily review, context packs, and memory capture.` | `Use Claude Desktop to search Cortex memory, review today, copy context, and save new memory.` |
| `Gives Cursor agent sessions access to Cortex project memory and context packs.` | `Gives Cursor access to saved project memory and chat context.` |
| `Restart Cursor, then enable the cortex MCP server in Cursor settings if prompted.` | `Restart Cursor, then enable Cortex in Cursor settings if prompted.` |
| `Use Cortex before implementation tasks: search memory for project decisions, people, and open loops.` | `Use Cortex before implementation tasks: search memory for project decisions, people, and follow-ups.` |
| `Connects Windsurf/Cascade to Cortex through the local MCP stdio bridge.` | `Lets Windsurf use Cortex memory through a local connection.` |
| `Restart Windsurf after installing the MCP server.` | `Restart Windsurf after installing the Cortex connection.` |
| `Installs Cortex as a Cline MCP server for VS Code agent workflows.` | `Adds Cortex memory to Cline coding sessions in VS Code.` |
| `Adds Cortex MCP tools for Roo Code coding sessions.` | `Adds Cortex memory tools for Roo Code coding sessions.` |
| `Copy a Cortex MCP server definition for VS Code user or workspace MCP setup.` | `Copy Cortex advanced setup for VS Code user or workspace settings.` |
| `Copy a ready command/config snippet for Claude Code MCP setup.` | `Copy a ready setup command for Claude Code.` |
| `Copy a paste-ready Cortex context pack and usage instruction for ChatGPT.` | `Copy Cortex chat context and instructions for ChatGPT.` |
| `Paste the Cortex context pack into ChatGPT when you want the assistant to reuse your local memory.` | `Paste Cortex context into ChatGPT when you want it to reuse your saved memory.` |
| `Copy a Cortex context pack for Grok conversations.` | `Copy Cortex chat context for Grok conversations.` |
| `Paste the context pack into Poe bots that need personal/project memory.` | `Paste Cortex context into Poe bots that need personal or project memory.` |
| `Copy Cortex MCP/API settings for local model workflows.` | `Copy Cortex local connection settings for local model workflows.` |
| `Use Cortex's local API or MCP bridge with local model agents that support tools.` | `Use Cortex's local service with local model tools that support direct connections.` |
| `Copy Cortex MCP/API settings for team chat deployments.` | `Copy Cortex local connection settings for team chat deployments.` |
| `Copy Cortex exports/context packs into AnythingLLM workspaces.` | `Use copied Cortex chat context in AnythingLLM workspaces when direct connection is not available.` |

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
| `Copy a context pack before your next ChatGPT, Claude, Cursor, or MCP session.` | `Copy context before your next ChatGPT, Claude, Cursor, or AI session.` |
| `Clear or update N open loop(s).` | `Close or update N follow-up(s).` |
| `Copy a context pack for #topic before your next AI session.` | `Copy context for #topic before your next AI session.` |

## macOS Trust Controls

Surface: Trust tab and Trust section under Settings.

| Current string | Proposed string |
| --- | --- |
| `N active memories [separator] N pending [separator] N agent events this week` | `N active memories [separator] N pending [separator] N AI tool actions this week` |
| `Let AI use pending saves` | Keep. This is clear. |
| `Turn this off when only approved captures should appear in search and context packs.` | `Turn this off when only approved saves should appear in search and copied context.` |
| `Agent read access` | `AI tool read access` |
| `Connected MCP agents can search memory, read review context, and inspect stats.` | `Connected AI tools can search memory, read today's context, and view memory stats.` |
| `Agent write access` | `AI tool save access` |
| `Connected MCP agents can save, approve, archive, or forget memory.` | `Connected AI tools can save, approve, archive, or delete memory when allowed.` |
| `Agent export access` | `AI tool export access` |
| `Connected MCP agents can build context packs or export memory.` | `Connected AI tools can copy context or export memory when allowed.` |
| `Context pack size: N` | `Context to include: N items` |
| `Audit Trail` | `Activity History` |
| `Captures, approvals, backups, settings, and agent tool calls will appear here.` | `Saves, approvals, backups, settings changes, and connected AI tool actions will appear here.` |
| `Receipts and recovery` | `History and recovery` |
| `Copy Redacted Context` | `Copy Safe Context` |
| `Open Vault` | `Open Folder` |
| `Trust controls apply to the local backend, MCP agents, context packs, and exports. The vault remains on this Mac.` | `Trust controls apply to the local service, connected AI tools, copied context, and exports. Your memory folder stays on this Mac.` |

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
| `Stage 1 source import with preview, duplicate-safe history, and batch undo for user-selected exports, folders, and files` | `Connected-source setup for supported services and direct app integrations, with Advanced/Fallback import only when a source cannot connect yet` |
| `first source import` | `first connected source` |
| `MCP-compatible tools` | `tools that support direct local connections` |
| `open loops` | `follow-ups` |
| `Bundled local backend on 127.0.0.1:8766` | `Bundled local service on 127.0.0.1:8766` |
| `User-owned local vault at path` | `User-owned memory folder at path` |
| `Copy-ready context packs` | `Copy-ready chat context` |
| `Local MCP bridge and one-click config helpers` | `Advanced local connection and one-click setup helpers` |
| `Trust controls for agent reads, writes, exports, redaction, and maintenance` | `Trust controls for connected AI tools, exports, redaction, and repair actions` |
| `The packaged app starts the local backend automatically.` | `The packaged app starts the local service automatically.` |

Technical docs can keep implementation terms when they are the subject of the doc:

- Keep `MCP` in `docs/MCP_INTEGRATIONS.md`, API endpoint docs, generated config examples, and integration implementation notes.
- Keep `vault` in `docs/LOCAL_VAULT_FORMAT.md` and storage-recovery instructions, but introduce it as "Cortex's memory folder (`Cortex.vault`)" when the audience is non-engineering support.
- Keep `backend` in architecture, reliability, and hosted-system docs.
- Keep `MVP` in roadmap docs, but avoid it in install, privacy, first-run, release, or landing-page copy.

## Priority Cleanup Order

1. Public site and distribution copy: remove `MCP`, `agents`, `context packs`, `vault`, `backend`, `open loops`, and manual import/drop as the first-source path from first-viewport and install language.
2. First-run onboarding: replace `Vault`, `Backend`, `MCP Config`, `context pack size`, and import-first setup before beta users see setup.
3. Trust controls: replace `Agent read/write/export` with explicit connected-AI-tool permissions.
4. Today: replace `open loops` and backend-provided `context pack` strings that appear in recommended actions.
5. Settings diagnostics: keep technical detail behind "Advanced diagnostics", but rename headings and pills to service/folder/search language.
