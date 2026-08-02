# Connecting external apps to Cortex

Cortex is local-first: it runs a small server on your Mac at `http://127.0.0.1:8766` and every
integration talks to *that* — your memory never leaves your machine unless you explicitly send it.
This doc covers every way an external app can use your Cortex memory as context.

Cortex has two scoped token audiences, both sent as
`Authorization: Bearer <token>`:

- `cxa_` tokens authenticate the REST API, including `/v1/context`,
  `/v1/search`, `/v1/ask`, and `/v1/tools/*`. Use these with the Python and
  TypeScript SDKs.
- `cxm_` tokens authenticate only the MCP JSON-RPC endpoint at `/mcp` and its
  stdio bridge. Do not put an MCP token in an SDK.

Tokens are minted in the Cortex app (or the matching integration-token
endpoint) and carry scopes (`read`, `write`, `export`, `maintenance`,
`destructive`). **Advertisement is never authorization** — every call is
re-checked against the token's scopes and your Connections & Privacy trust
toggles.

## 1. MCP (Claude Desktop, Claude Code, Cursor, VS Code)

Cortex speaks the Model Context Protocol over JSON-RPC at `POST /mcp` (protocol revisions
`2025-06-18` / `2025-03-26` / `2024-11-05`). A stdio bridge (`scripts/cortex_mcp_stdio.py`) proxies
local clients that expect a subprocess.

Config (generated for you by the Cortex app):
```json
{
  "mcpServers": {
    "cortex": {
      "command": "python3",
      "args": ["/Applications/Cortex.app/Contents/Resources/scripts/cortex_mcp_stdio.py"],
      "env": { "CORTEX_BASE_URL": "http://127.0.0.1:8766", "CORTEX_API_KEY": "<scoped cxm_ token>" }
    }
  }
}
```

**Tools.** A curated core surface is advertised by default (task-awareness happens *inside*
`use_cortex`, so the tool list stays cache-stable):
- `use_cortex` — one entry point; describe the task and Cortex routes to the right retrieval.
- `get_context` — token-budgeted, cited working-context pack for a task.
- `ask_memory` — cite-or-abstain answer to a question.
- `search_memory`, `get_entity_context`, `get_person_map`, `remember_this`, `list_capabilities`.

Every tool carries MCP `annotations` (`readOnlyHint`/`destructiveHint`/`idempotentHint`/
`openWorldHint`) and output schemas so clients render and select them well. Surface presets
(`core` / `coding` / `chat`) keep each client under its tool cap (Cursor 40, ChatGPT 128).

**Resources** (`cortex://` — app-driven, cacheable): `profile/person-map`, `profile/personal`,
`profile/adaptation`, `schema/capabilities`, `review/daily`, and the template `entity/{name}`.

**Prompts** (user-driven templates that embed cited context): `summarize_recent_decisions`,
`extract_action_items`, `brief_me_on`.

## 2. Any function-calling app (universal HTTP + OpenAI/Anthropic/OpenAPI)

The same tool catalog is reachable over plain HTTP, so any function-calling LLM/app works without
MCP:

- `GET  /v1/tools/schema?format=openai|anthropic|openapi|mcp` — the tool catalog projected to that
  format (scoped to your token). Wire `openai` straight into an OpenAI `tools=[...]` call.
- `POST /v1/tools/call` — `{"name": "<tool>", "arguments": {...}}` → `{"tool", "result"}`.
- `POST /v1/tools/{name}` — arguments as the body; matches the OpenAPI `operationId`s (for custom
  GPT actions / Zapier-style connectors, once a reachable endpoint is enabled).
- `GET  /v1/context`, `/v1/search`, `/v1/ask` — the Context Assembly Engine and cited retrieval
  directly.

Example (OpenAI Python):
```python
import openai, requests
BASE, TOK = "http://127.0.0.1:8766", "<cxa_ REST token>"
tools = requests.get(f"{BASE}/v1/tools/schema?format=openai", headers={"Authorization": f"Bearer {TOK}"}).json()["schema"]
resp = openai.chat.completions.create(model="...", messages=[...], tools=tools)
# For each tool call, POST /v1/tools/call {name, arguments} and feed the result back.
```

## 3. Client SDK (Python / TypeScript)

`sdk/python` (`pip install doppl-cortex-client`, stdlib-only) and `sdk/typescript`
(`@doppl-tech/cortex-client`, fetch-based) wrap the above:
```python
from cortex_client import CortexClient
cx = CortexClient(token="<cxa_ REST token>")       # base_url defaults to loopback
pack = cx.context("prep for the Acme sync")          # cited context pack
answer = cx.ask("what did we decide about pricing?")  # cite-or-abstain
tools = cx.openai_tools()                             # ready for tools=[...]
```

## 4. Web apps (ChatGPT, Claude.ai, Notion) via the browser extension

Because web apps can't reach a loopback server, the **Cortex browser extension** (`extension/`)
bridges them client-side: it pairs with your local Cortex (`POST /v1/pair` → a read-only token),
and injects cited context into the page's input box. Load it unpacked (see `extension/README.md`),
paste the token from the Cortex app, and click "◆ Cortex" on a supported site.

## 5. Delivery / push (send context OUT to a service)

Cortex can proactively deliver a cited brief to a webhook (Slack incoming webhook, a local
automation, etc.):
- `POST /v1/delivery/preview` — `{task, sector?}` → exactly what would be sent (nothing leaves).
- `POST /v1/delivery/send` — `{url, task, sector?}` → delivers the cited, sector-scoped,
  identity-omitted brief. Requires the `export` scope + the `allow_agent_exports` trust toggle
  (default off), is SSRF-guarded (no internal targets; public targets must be https), and audited.

## Privacy & safety invariants (hold across every surface)

- Everything returned is **cited** (each item keeps its `memory_id` + source) or Cortex abstains.
- Retrieved excerpts are stamped `treat_as_data: true` — agents are told to treat them as data,
  never instructions (prompt-injection guard).
- Sector-scoped requests never leak another sector; the identity/persona layer is omitted from
  pushed briefs.
- Reads honor the review gate; writes/exports/deletes need their scope + trust toggle.
