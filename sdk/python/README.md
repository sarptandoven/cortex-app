# doppl-cortex-client (Python)

A tiny, **dependency-light** client for the [Cortex](../../README.md) local memory
server. Uses only the Python standard library (`urllib`, `json`, `typing`) — no
`requests`, no third-party runtime dependencies — matching Cortex's stdlib-only ethos.

Cortex serves the *same* tool catalog it exposes over MCP as plain HTTP, so any
function-calling app (OpenAI, Anthropic, or a custom agent loop) can drive it from one
tool definition. This client wraps that HTTP surface.

- Default base URL: `http://127.0.0.1:8766` (local loopback — Cortex is local-first).
- Auth: `Authorization: Bearer <cxa_ REST token>`, optional `X-Cortex-User`
  header. `cxm_` tokens are only for `/mcp`, not this SDK.
- Same routes work against the hosted plane — pass `base_url="https://api.signindoppl.com"`
  and a token minted there. The hosted server (`backend/app/main.py`) mirrors the local
  server's `/v1/tools/schema` and `/v1/tools/call` request/response shapes exactly, including
  scope enforcement.

## Install

Requires Python 3.9+.

The SDK is currently supported from this repository; the PyPI name is reserved
but not yet the installation path:

```bash
# From the Cortex repository root:
python -m pip install -e sdk/python
```

`make setup` already performs this editable install for contributors. Once a
registry release is documented, the command will be
`pip install doppl-cortex-client`; do not assume that command works before then.
The importable module is `cortex_client` regardless of the distribution name.
You can also vendor the `cortex_client/` folder because it has no dependencies.

## Quickstart

First start Cortex (`make run` from the repository root) or open the installed
macOS app. The development server uses `dev-local-key`; an installed app exposes
a scoped `cxa_` REST token under Connections & Privacy.

```python
from cortex_client import CortexClient, CortexError

client = CortexClient(
    base_url="http://127.0.0.1:8766",
    token="dev-local-key",  # use a scoped cxa_ token with the installed app
)

# Cited answer to a specific question (never an uncited guess).
print(client.ask("What database do we use?"))

# Keyword/semantic search with retrieval diagnostics.
print(client.search("release checklist", top_k=5))

# Token-budgeted working-context pack — call this before doing a task.
print(client.context("draft the release notes", intent="draft", token_budget=1500))

# Portable SMP projection with multi-turn delta context and a project hint.
print(client.context(
    "plan the Project Atlas launch",
    format="smp",
    model="claude",
    session_id="atlas-planning",
    project="Atlas",
))

# Any tool in the catalog, by name.
print(client.call_tool("get_person_map", {}))

try:
    client.call_tool("forget_memory", {"id": "m_123"})
except CortexError as exc:
    print(exc.status, exc.detail)  # e.g. 403 "Cortex API token requires destructive scope"
```

`sector` is the hard corpus filter to use when memories must be isolated.
`project` only helps Cortex rank and enrich entity context; it is **not** an
authorization or isolation boundary. If `pin=True` is combined with
`session_id`, that ID must come from the `start_agent_session` MCP tool. Pinning
without a session ID is valid.

## Methods

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `tools_schema(fmt="openai")` | `GET /v1/tools/schema` | Tool catalog as `openai` / `anthropic` / `openapi` / `mcp`. |
| `openai_tools()` | `GET /v1/tools/schema?format=openai` | Convenience → OpenAI function-calling `tools` array. |
| `anthropic_tools()` | `GET /v1/tools/schema?format=anthropic` | Convenience → Anthropic `tools` array. |
| `call_tool(name, arguments=None)` | `POST /v1/tools/call` | Invoke any tool; returns its `result`. |
| `context(task, ..., format=None, model=None, session_id=None, pin=None, sector=None, project=None, as_of=None)` | `POST /v1/context` | Cited pack, SMP/Markdown projection, sector filter, project hint, and session deltas. |
| `search(query, top_k=8)` | `GET /v1/search` | Search memory. |
| `ask(query, top_k=8)` | `GET /v1/ask` | Cited answer / explicit abstention. |

Any non-2xx response raises `CortexError(status, detail)`, where `detail` is the
server's `{"detail": ...}` payload (a string or an object). Transport failures (server
unreachable) and invalid base URLs raise `CortexError` with `status == 0`. Base URLs
must be absolute `http://` or `https://` URLs without embedded user information.
Queries and fragments are rejected as well; put request parameters on SDK
methods instead of the base URL.

## OpenAI function-calling

Wire `client.openai_tools()` straight into `tools=`, then route the model's tool calls
back through `client.call_tool`:

```python
import json
from openai import OpenAI
from cortex_client import CortexClient

cortex = CortexClient(token="cxa_your_token")
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

cortex = CortexClient(token="cxa_your_token")
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
.venv/bin/python -m pytest sdk/python/tests/test_client.py
```
