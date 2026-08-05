# Cortex Client SDKs

Small, dependency-light clients for the **Cortex local memory server** — in both Python
and TypeScript. They wrap Cortex's local HTTP surface so any app or agent can read the
user's cited memory, ask questions, assemble working context, and call the full tool
catalog.

| SDK | Package | Transport | Runtime deps |
| --- | --- | --- | --- |
| [Python](./python/) | `doppl-cortex-client` | stdlib `urllib` | none (Python 3.9+) |
| [TypeScript](./typescript/) | `@doppl-tech/cortex-client` | `fetch` | none (Node 18+ / browser) |

Both mirror the same method surface:

| Method (py / ts) | Endpoint | Purpose |
| --- | --- | --- |
| `tools_schema` / `toolsSchema` | `GET /v1/tools/schema` | Tool catalog as `openai` / `anthropic` / `openapi` / `mcp`. |
| `openai_tools` / `openaiTools` | `GET /v1/tools/schema?format=openai` | OpenAI function-calling `tools` array. |
| `anthropic_tools` / `anthropicTools` | `GET /v1/tools/schema?format=anthropic` | Anthropic `tools` array. |
| `call_tool` / `callTool` | `POST /v1/tools/call` | Invoke any tool by name; returns its `result`. |
| `context` | `POST /v1/context` | Token-budgeted, cited working-context pack. |
| `search` | `GET /v1/search` | Search memory (`top_k` → server `limit`). |
| `ask` | `GET /v1/ask` | Cited answer or explicit abstention. |

## Local-first by default

Cortex runs **on the user's machine**. Both clients default to `http://127.0.0.1:8766`
(loopback) — the address the macOS app serves on. Nothing leaves the device unless you
point `base_url` / `baseUrl` elsewhere.

Authentication is a bearer token (`Authorization: Bearer <token>`) with an optional
`X-Cortex-User` header to select a user in multi-user deployments. The token's scopes
determine which tools are allowed; the server enforces scopes on every call regardless
of what the schema advertises, so a read-only token gets a read-only surface.

## Relationship to MCP

Cortex exposes its tools two ways over the **same catalog and the same scope
enforcement**:

- **MCP** (`POST /mcp`) — JSON-RPC (`initialize` / `tools/list` / `tools/call`), for MCP
  clients like Claude Desktop and Cursor.
- **Universal HTTP** (`/v1/tools/*`) — plain HTTP for any function-calling app. This is
  what these SDKs use.

So the two transports are interchangeable: a tool called over MCP and the same tool
called via `call_tool` / `callTool` run identical server-side logic and honor the same
token scopes. The only difference is the wire protocol.

`tools_schema(fmt)` / `toolsSchema(fmt)` projects that one catalog into whichever shape
your framework wants:

- `openai` → OpenAI function-calling `tools`
- `anthropic` → Anthropic Messages `tools`
- `openapi` → OpenAPI 3.1 document (one `POST /v1/tools/{name}` per tool) for custom-GPT
  actions / connectors
- `mcp` → the raw MCP tool descriptors

Wire the schema into your model's `tools=` argument, then route the model's tool calls
back through `call_tool` / `callTool`. See each SDK's README for a full OpenAI and
Anthropic example.

## Getting started

- Python: [`python/README.md`](./python/README.md)
- TypeScript: [`typescript/README.md`](./typescript/README.md)
