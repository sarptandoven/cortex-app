# @doppl-tech/cortex-client (TypeScript)

A small, **dependency-free** client for the [Cortex](../../README.md) local memory
server. Uses the built-in `fetch` (Node 18+ or any modern browser) — no runtime
dependencies.

Cortex serves the *same* tool catalog it exposes over MCP as plain HTTP, so any
function-calling app (OpenAI, Anthropic, or a custom agent loop) can drive it from one
tool definition. This client wraps that HTTP surface.

- Default base URL: `http://127.0.0.1:8766` (local loopback — Cortex is local-first).
- Auth: `Authorization: Bearer <cxa_ REST token>`, optional `X-Cortex-User`
  header. `cxm_` tokens are only for `/mcp`, not this SDK.
- Same routes work against the hosted plane — set `baseUrl: "https://api.signindoppl.com"`
  and use a token minted there. The hosted server (`backend/app/main.py`) mirrors the local
  server's `/v1/tools/schema` and `/v1/tools/call` request/response shapes exactly, including
  scope enforcement, so this client (and the OpenAI/Anthropic examples below) work unmodified
  against either.

## Install

Requires Node 18+ (for global `fetch` / `AbortController`).

```bash
npm install @doppl-tech/cortex-client   # once published (see sdk/PUBLISHING.md)
```

### Build from source

```bash
cd sdk/typescript
npm install      # dev-only: typescript, @types/node
npm run build    # emits dist/index.js + dist/index.d.ts via tsc
npm test         # compiles test/ + src/ and runs against a stubbed fetch (node:test)
```

Or drop `src/index.ts` straight into your project.

## Quickstart

```ts
import { CortexClient, CortexError } from "@doppl-tech/cortex-client";

const cortex = new CortexClient({
  baseUrl: "http://127.0.0.1:8766",
  token: "cxa_your_token",
});

// Cited answer to a specific question (never an uncited guess).
console.log(await cortex.ask("What database do we use?"));

// Keyword/semantic search with retrieval diagnostics.
console.log(await cortex.search("release checklist", 5));

// Token-budgeted working-context pack — call this before doing a task.
console.log(await cortex.context("draft the release notes", { intent: "draft", tokenBudget: 1500 }));

// Any tool in the catalog, by name.
console.log(await cortex.callTool("get_person_map", {}));

try {
  await cortex.callTool("forget_memory", { id: "m_123" });
} catch (err) {
  if (err instanceof CortexError) {
    console.log(err.status, err.detail); // e.g. 403 "Cortex API token requires destructive scope"
  }
}
```

## Methods

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `toolsSchema(fmt = "openai")` | `GET /v1/tools/schema` | Tool catalog as `openai` / `anthropic` / `openapi` / `mcp`. |
| `openaiTools()` | `GET /v1/tools/schema?format=openai` | Convenience → `OpenAITool[]`. |
| `anthropicTools()` | `GET /v1/tools/schema?format=anthropic` | Convenience → `AnthropicTool[]`. |
| `callTool(name, args = {})` | `POST /v1/tools/call` | Invoke any tool; returns its `result`. |
| `context(task, options?)` | `POST /v1/context` | Cited working-context pack. |
| `search(query, topK = 8)` | `GET /v1/search` | Search memory. |
| `ask(query, topK = 8)` | `GET /v1/ask` | Cited answer / explicit abstention. |

Every method is `async`. Any non-2xx response rejects with `CortexError` carrying
`.status` and `.detail` (the server's `{"detail": ...}` payload — a string or an
object). Transport failures / timeouts reject with `CortexError` and `.status === 0`.
Credential-bearing redirects are handled manually: GET/HEAD follows at most
three same-origin redirects, while cross-origin redirects and redirects for
body-bearing requests are rejected before the token can be replayed.

All methods accept a generic type parameter for the expected response shape, e.g.
`await cortex.search<{ results: Memory[] }>("q")`.

## OpenAI function-calling

Wire `cortex.openaiTools()` straight into `tools`, then route the model's tool calls
back through `cortex.callTool`:

```ts
import OpenAI from "openai";
import { CortexClient } from "@doppl-tech/cortex-client";

const cortex = new CortexClient({ token: "cxa_your_token" });
const oai = new OpenAI();

const tools = await cortex.openaiTools(); // Cortex catalog as OpenAI function schemas
const messages: any[] = [
  { role: "user", content: "What did we decide about the pricing model?" },
];

let resp = await oai.chat.completions.create({ model: "gpt-4o", messages, tools });
const msg = resp.choices[0].message;
messages.push(msg);

for (const call of msg.tool_calls ?? []) {
  const args = JSON.parse(call.function.arguments || "{}");
  const result = await cortex.callTool(call.function.name, args); // route back to Cortex
  messages.push({ role: "tool", tool_call_id: call.id, content: JSON.stringify(result) });
}

resp = await oai.chat.completions.create({ model: "gpt-4o", messages, tools });
console.log(resp.choices[0].message.content);
```

## Anthropic tool use

`cortex.anthropicTools()` returns the catalog in Anthropic's `{ name, description,
input_schema }` shape. Route `tool_use` blocks back through `callTool` and reply with a
`tool_result` block:

```ts
import Anthropic from "@anthropic-ai/sdk";
import { CortexClient } from "@doppl-tech/cortex-client";

const cortex = new CortexClient({ token: "cxa_your_token" });
const client = new Anthropic();

const tools = await cortex.anthropicTools();
const messages: any[] = [
  { role: "user", content: "Summarize what you know about Project Atlas." },
];

let resp = await client.messages.create({
  model: "claude-sonnet-4-5",
  max_tokens: 1024,
  tools,
  messages,
});

const toolResults: any[] = [];
for (const block of resp.content) {
  if (block.type === "tool_use") {
    const result = await cortex.callTool(block.name, block.input as Record<string, unknown>);
    toolResults.push({ type: "tool_result", tool_use_id: block.id, content: JSON.stringify(result) });
  }
}

if (toolResults.length) {
  messages.push({ role: "assistant", content: resp.content });
  messages.push({ role: "user", content: toolResults });
  resp = await client.messages.create({
    model: "claude-sonnet-4-5",
    max_tokens: 1024,
    tools,
    messages,
  });
}
console.log(resp.content);
```
