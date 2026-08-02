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

The npm scope is planned but not yet the supported installation path. Build and
test the source package from this repository:

```bash
cd sdk/typescript
npm ci           # dev-only: typescript, @types/node
npm run build    # emits dist/index.js + dist/index.d.ts via tsc
npm test         # compiles test/ + src/ and runs against a stubbed fetch (node:test)
```

Until a registry release is documented, import `dist/index.js` from the checkout
or vendor `src/index.ts`. Do not assume
`npm install @doppl-tech/cortex-client` works before then.

## Quickstart

First start Cortex (`make run` from the repository root) or open the installed
macOS app. The development server uses `dev-local-key`; an installed app exposes
a scoped `cxa_` REST token under Connections & Privacy.

```ts
// Registry release: import from "@doppl-tech/cortex-client".
// Source checkout: import from "./sdk/typescript/dist/index.js".
import { CortexClient, CortexError } from "./sdk/typescript/dist/index.js";

const cortex = new CortexClient({
  baseUrl: "http://127.0.0.1:8766",
  token: "dev-local-key", // use a scoped cxa_ token with the installed app
});

// Cited answer to a specific question (never an uncited guess).
console.log(await cortex.ask("What database do we use?"));

// Keyword/semantic search with retrieval diagnostics.
console.log(await cortex.search("release checklist", 5));

// Token-budgeted working-context pack — call this before doing a task.
console.log(await cortex.context("draft the release notes", { intent: "draft", tokenBudget: 1500 }));

// Portable SMP projection with multi-turn delta context and a project hint.
console.log(await cortex.context("plan the Project Atlas launch", {
  format: "smp",
  model: "claude",
  sessionId: "atlas-planning",
  project: "Atlas",
}));

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

`sector` is the hard corpus filter to use when memories must be isolated.
`project` only helps Cortex rank and enrich entity context; it is **not** an
authorization or isolation boundary. If `pin: true` is combined with
`sessionId`, that ID must come from the `start_agent_session` MCP tool. Pinning
without a session ID is valid.

## Methods

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `toolsSchema(fmt = "openai")` | `GET /v1/tools/schema` | Tool catalog as `openai` / `anthropic` / `openapi` / `mcp`. |
| `openaiTools()` | `GET /v1/tools/schema?format=openai` | Convenience → `OpenAITool[]`. |
| `anthropicTools()` | `GET /v1/tools/schema?format=anthropic` | Convenience → `AnthropicTool[]`. |
| `callTool(name, args = {})` | `POST /v1/tools/call` | Invoke any tool; returns its `result`. |
| `context(task, options?)` | `POST /v1/context` | Cited pack, SMP/Markdown projection, sector filter, project hint, and session deltas. |
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
