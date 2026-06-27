# Cortex MVP Roadmap

## Product Thesis

Cortex is the shared memory layer for every AI assistant a person uses. The first product should make one promise extremely well:

> Save context once. ChatGPT, Claude, and other AI tools can retrieve it later with sources.

The current repo proves the core idea: capture text, extract structured memory, store it, search it, and expose it to AI through MCP. The MVP turns that prototype into a product with a hosted backend, a native macOS capture client, and a stable API contract.

## Target User

Start with AI power users who already use ChatGPT, Claude, Cursor, Slack, Notion, docs, and meetings every day. They feel the pain of repeating context and losing decisions across tools.

## MVP Magic Moment

1. The user copies text from any app.
2. They press `Cmd+Shift+V`.
3. Cortex saves and structures the memory.
4. In a later ChatGPT or Claude session, the user asks, "What did I decide about this?"
5. The AI retrieves the relevant memories with sources.

## Build Phases

### Phase 0: Local Product Beta

Goal: one developer can run Cortex locally and use the macOS app every day.

- FastAPI backend with capture, extraction, memory storage, search, graph, and MCP-style endpoints
- SQLite database with full-text search for zero-cost local use
- SQLite schema with review status, normalized topics/entities, source graph edges, and lifecycle events
- Native macOS menu bar app with global hotkey, clipboard capture, quick note, review inbox, search, recent memories, stats, export, and graph view
- Health diagnostics, local backups, and search-index maintenance
- Regression and HTTP battle tests before packaging
- Deterministic extraction fallback so the product works without model keys
- Optional Claude extraction when `ANTHROPIC_API_KEY` is present

### Phase 1: Hosted Free Beta

Goal: non-technical users can sign up and use Cortex without Docker, Redis, GitHub tokens, or local Python.

- Hosted API on Render, Fly.io, Railway, or a small VPS
- Supabase Auth for Google login
- SQLite WAL with `sqlite-vec` for local and hosted semantic search
- Per-user API tokens for the macOS app and MCP tools
- Web dashboard for inbox review, delete/export, and source history
- Hosted export/delete account flow with per-user retention policy

Recommended cheapest path: keep FastAPI, deploy one service, use SQLite WAL plus `sqlite-vec`, and add hosted auth around it. This keeps local and hosted storage behavior aligned, avoids a premature database rewrite, and preserves user-owned portable memory files.

### Phase 2: AI Tool Connectors

Goal: Cortex works inside the AI tools users already use.

- Remote MCP endpoint with OAuth/API-token auth
- Claude custom connector support
- ChatGPT Apps/connector support
- Stdio MCP proxy for Claude Desktop and local agent tools
- Browser extension for ChatGPT/Claude conversation capture

### Phase 3: Trust and Control

Goal: make users comfortable giving Cortex personal context.

- Memory inbox with approve/edit/delete
- Source citations on every memory
- One-click forget
- Export to markdown/JSON
- User-owned GitHub sync as an advanced option
- Sensitive source pause list

### Phase 4: Growth Loops

Goal: make Cortex valuable enough that users invite teammates and collaborators.

- Project brains
- Shared team memories
- Slack/Notion/Gmail import
- Meeting transcript ingestion
- Decision and task digests

## What Not To Build First

- Full Notion replacement
- Mobile app
- Ambient screen recorder
- Wearables integration
- Complex graph visualization as the main surface
- Heavy enterprise admin

## MVP Success Criteria

- A user can capture from any macOS app in under two seconds.
- A user can search old context from the app.
- The backend returns structured memories with source metadata.
- The graph endpoint shows people, projects, tasks, sources, and their relationships.
- The API contract is ready for ChatGPT, Claude, and other MCP-compatible tools.
