# doppl-cortex-client (Python)

A tiny, **dependency-light** client for the [Cortex](../../README.md) local memory
server. Uses only the Python standard library (`urllib`, `json`, `typing`) — no
`requests`, no third-party runtime dependencies — matching Cortex's stdlib-only ethos.

Cortex serves the *same* tool catalog it exposes over MCP as plain HTTP, so any
function-calling app (OpenAI, Anthropic, or a custom agent loop) can drive it from one
tool definition. This client wraps that HTTP surface.

- Default base URL: `http://127.0.0.1:8766` (local loopback — Cortex is local-first).
- Auth: `Authorization: Bearer <token>`, optional `X-Cortex-User` header.
- Same routes work against the hosted plane — pass `base_url="https://api.signindoppl.com"`
  and a token minted there. The hosted server (`backend/app/main.py`) mirrors the local
  server's `/v1/tools/schema` and `/v1/tools/call` request/response shapes exactly, including
  scope enforcement.

## Install

Requires Python 3.9+.

```bash
pip install doppl-cortex-client   # once published (see sdk/PUBLISHING.md)
# or, for local development against this repo:
cd sdk/python && pip install -e .
```

The importable module is `cortex_client` regardless of the PyPI package name (`doppl-cortex-client`)
— see the Quickstart below. Or just copy the `cortex_client/` folder into your project — it has no
dependencies.

## Quickstart

```python
from cortex_client import CortexClient, CortexError

client = CortexClient(base_url="http://127.0.0.1:8766", token="ctx_your_token")

# Cited answer to a specific question (never an uncited guess).
print(client.ask("What database do we use?"))

# Keyword/semantic search with retrieval diagnostics.
print(client.search("release checklist", top_k=5))

# Token-budgeted working-context pack — call this before doing a task.
print(client.context("draft the release notes", intent="draft", token_budget=1500))

# Any tool in the catalog, by name.
print(client.call_tool("get_person_map", {}))

try:
    client.call_tool("forget_memory", {"id": "m_123"})
except CortexError as exc:
    print(exc.status, exc.detail)  # e.g. 403 "Cortex API token requires destructive scope"
```

## Methods

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `tools_schema(fmt="openai")` | `GET /v1/tools/schema` | Tool catalog as `openai` / `anthropic` / `openapi` / `mcp`. |
| `openai_tools()` | `GET /v1/tools/schema?format=openai` | Convenience → OpenAI function-calling `tools` array. |
| `anthropic_tools()` | `GET /v1/tools/schema?format=anthropic` | Convenience → Anthropic `tools` array. |
| `call_tool(name, arguments=None)` | `POST /v1/tools/call` | Invoke any tool; returns its `result`. |
| `context(task, intent=None, token_budget=2000, surface="agent")` | `POST /v1/context` | Cited working-context pack. |
| `search(query, top_k=8)` | `GET /v1/search` | Search memory. |
| `ask(query, top_k=8)` | `GET /v1/ask` | Cited answer / explicit abstention. |

Any non-2xx response raises `CortexError(status, detail)`, where `detail` is the
server's `{"detail": ...}` payload (a string or an object). Transport failures (server
unreachable) raise `CortexError` with `status == 0`.

## OpenAI function-calling

Wire `client.openai_tools()` straight into `tools=`, then route the model's tool calls
back through `client.call_tool`:

```python
import json
from openai import OpenAI
from cortex_client import CortexClient

cortex = CortexClient(token="ctx_your_token")
oai = OpenAI()

tools = cortex.openai_tools()  # Cortex tool catalog as OpenAI function schemas
messages = [{"role": "user", "content": "What did we decide about the pricing model?"}]

resp = oai.chat.completions.create(model="gpt-4o", messages=messages, tools=tools)
msg = resp.choices[0].message
messages.append(msg)

for call in msg.tool_calls or []:
    args = json.loads(call.function.arguments or "{}")
    result = cortex.call_tool(call.function.name, args)  # route back to Cortex
    messages.append(
        {
            "role": "tool",
            "tool_call_id": call.id,
            "content": json.dumps(result),
        }
    )

final = oai.chat.completions.create(model="gpt-4o", messages=messages, tools=tools)
print(final.choices[0].message.content)
```

## Anthropic tool use

`client.anthropic_tools()` returns the catalog in Anthropic's `{name, description,
input_schema}` shape. Route `tool_use` blocks back through `call_tool` and reply with
a `tool_result` block:

```python
import anthropic
from cortex_client import CortexClient

cortex = CortexClient(token="ctx_your_token")
client = anthropic.Anthropic()

tools = cortex.anthropic_tools()
messages = [{"role": "user", "content": "Summarize what you know about Project Atlas."}]

resp = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=1024,
    tools=tools,
    messages=messages,
)

tool_results = []
for block in resp.content:
    if block.type == "tool_use":
        result = cortex.call_tool(block.name, dict(block.input))  # route back to Cortex
        tool_results.append(
            {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)}
        )

if tool_results:
    messages.append({"role": "assistant", "content": resp.content})
    messages.append({"role": "user", "content": tool_results})
    resp = client.messages.create(
        model="claude-sonnet-4-5", max_tokens=1024, tools=tools, messages=messages
    )
print(resp.content)
```

## Tests

The test suite stubs the SDK's one `urllib` call — it never touches the network:

```bash
python3 -m pytest sdk/python/tests/test_client.py
```
