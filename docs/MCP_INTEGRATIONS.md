# Cortex AI Integrations

## Local Transport

Cortex exposes two MCP-compatible paths:

- HTTP JSON-RPC: `POST http://127.0.0.1:8766/mcp`
- Stdio proxy: `scripts/cortex_mcp_stdio.py`

The macOS app starts the local backend automatically. Once Cortex is running, local tools can connect through the stdio proxy.

## One-Click Integrations

The macOS app has a Connect tab and a first-run Connect step. It supports two integration modes:

- **One-click MCP install** for clients with stable local JSON config files.
- **Copy-ready context** for browser assistants and hosted tools that should not be edited locally by Cortex.

For direct installs, Cortex creates the parent config directory if needed, backs up an existing config next to the original file, then merges a single `mcpServers.cortex` entry without removing other servers.

Direct install targets:

- Claude Desktop: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Cursor: `~/.cursor/mcp.json`
- Windsurf: `~/.codeium/windsurf/mcp_config.json`
- Cline: `~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`
- Roo Code: `~/Library/Application Support/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json`

Copy/manual targets:

- VS Code Copilot
- Claude Code
- ChatGPT
- Claude web
- Gemini
- Perplexity
- Microsoft Copilot
- Grok
- Poe
- NotebookLM
- LM Studio
- Open WebUI
- LibreChat
- AnythingLLM

## MCP Config Shape

```json
{
  "mcpServers": {
    "cortex": {
      "command": "python3",
      "args": [
        "/absolute/path/to/second-brain/scripts/cortex_mcp_stdio.py"
      ],
      "env": {
        "CORTEX_BASE_URL": "http://127.0.0.1:8766",
        "CORTEX_API_KEY": "dev-local-key"
      }
    }
  }
}
```

## Browser Assistant Context

Browser assistants do not all expose a stable local MCP config. For those, Cortex copies a context pack with instructions:

- search/use pasted Cortex memory before asking the user to repeat context
- treat saved decisions and open loops as high-priority
- ask focused follow-ups when context is missing or stale
- preserve the user's local-first privacy constraints

## Tool Surface

- `remember_this`: save text into Cortex memory
- `search_memory`: search active memories
- `get_recent_context`: retrieve recent active memories
- `get_memory_graph`: retrieve the active graph
- `get_daily_review`: retrieve today's pending captures, open loops, decisions, topics, and recommended actions
- `build_context_pack`: build a paste-ready Markdown context pack for ChatGPT, Claude, Cursor, or another assistant
- `get_decisions`: retrieve saved decisions
- `get_open_questions`: retrieve open questions and tasks
- `list_memory_topics`: list active topics
- `list_memory_entities`: list active people, projects, organizations, and topics
- `get_about_person`: retrieve memories involving a person
- `get_about_entity`: retrieve memories involving any named entity
- `get_product_loop`: retrieve the Capture, Review, Reuse, Return loop state
- `get_memory_stats`: retrieve counts and top context
- `get_memory_inbox`: retrieve pending captures
- `approve_memory_capture`: approve a pending capture
- `archive_memory_capture`: archive a capture and remove it from active retrieval
- `get_memory_diagnostics`: inspect storage health
- `get_reliability_report`: inspect health contract, storage checks, backup state, and recommended recovery actions
- `get_support_bundle`: generate a sanitized operational support bundle without captured text or memory content
- `create_memory_backup`: create a full local vault backup
- `repair_memory_storage`: create a backup, clean stale derived index rows, and rebuild search
- `rebuild_memory_search`: rebuild full-text search
- `rebuild_index_from_vault`: rebuild the SQLite search index from user-owned vault files
- `export_memory`: export memory as Markdown or JSON
- `forget_memory`: archive one memory by ID

## Product Rule

Agents should search before asking users to repeat context, cite source memory text when making claims, and use archive/forget tools only when the user explicitly asks to remove memory.

## Reliability Notes

- Direct installers never delete an existing config file.
- Existing JSON must parse as an object before Cortex writes to it.
- Every changed config gets a timestamped `.cortex-backup-*` copy.
- Users can rerun Install as Repair to refresh the Python path, local API URL, or token.
- Browser integrations are intentionally copy-based until the target service exposes a safe local config or remote OAuth/MCP flow.
- MCP maintenance tools still respect Trust controls.
- The app and MCP clients can verify the backend through `health_contract >= 3`, the `reliability-hardening` feature flag, and the `operational-readiness` feature flag.
- `build_context_pack` records reuse in the loop because generated context means Cortex memory was used in an AI workflow.
- `get_support_bundle` is intended for support triage; users should still review the JSON before sharing it.
