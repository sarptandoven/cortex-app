# Cortex Examples

These examples use the dependency-free Python SDK against a running Cortex
server. They are intentionally provider-neutral: Cortex supplies cited memory;
your application decides which model or agent framework consumes it.

## Run Locally

Terminal 1:

```bash
make setup
make run
```

Terminal 2:

```bash
export CORTEX_API_KEY=dev-local-key
.venv/bin/python examples/minimal_search.py "release checklist"
```

`dev-local-key` is accepted only because `make run` starts a loopback-only
development server with the explicit insecure-development flag. For the
packaged app, copy a scoped token from Connections & Privacy and never commit
it.

## Examples

| Example | What it demonstrates |
|---|---|
| [`minimal_search.py`](minimal_search.py) | Smallest search request with cited results |
| [`memory_workflow.py`](memory_workflow.py) | Search, cite-or-abstain Ask, and a task-specific context pack |
| [`multi_agent_context.py`](multi_agent_context.py) | Give planner/researcher/writer roles separate bounded context |
| [`tool_catalog.py`](tool_catalog.py) | Fetch the OpenAI-compatible tool catalog without calling a model |

Every script accepts `CORTEX_BASE_URL` and `CORTEX_API_KEY`. The default base
URL is `http://127.0.0.1:8766`.

## Integration Pattern

```text
user task
   │
   ▼
request a cited Cortex context pack
   │
   ├── no evidence → abstain or ask the user
   │
   └── cited evidence → pass the bounded pack to your model/agent
                              │
                              ▼
                    keep memory IDs/citations
```

For model-specific function-calling examples, see the
[Python SDK guide](../sdk/python/README.md) and
[TypeScript SDK guide](../sdk/typescript/README.md).

## Best Practices

- Mint the narrowest token scopes the integration needs.
- Request bounded context for the current task instead of dumping the vault.
- Preserve memory IDs and source URLs when a result affects an answer.
- Treat an explicit abstention as a successful safety outcome.
- Keep the user's newest instruction above stored memory.
- Use synthetic data in demos and tests.

## Anti-Patterns

- Do not read the SQLite file or vault behind the user's back.
- Do not turn search results into factual claims after citations were dropped.
- Do not use the development token outside the local development server.
- Do not automatically approve high-risk connector content without explaining
  the trust policy.
- Do not retry an abstention with broader and broader private context.
