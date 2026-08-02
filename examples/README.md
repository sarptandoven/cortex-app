# Cortex Examples

These examples use the dependency-free Python SDK against a running Cortex
server. They are intentionally provider-neutral: Cortex supplies cited memory;
your application decides which model or agent framework consumes it.

If you have not run Cortex yet, start with the repository-level disposable
demo:

```bash
make demo
```

That proves capture → retrieval → cited Ask without touching personal data.
Return here when you want a persistent server and code you can modify.

## Run Locally

Terminal 1:

```bash
# Skip this line if `make demo` or `make setup` already created `.venv`.
make setup
CORTEX_AUTO_APPROVE_CAPTURES=1 make run
```

Terminal 2:

```bash
export CORTEX_API_KEY=dev-local-key

.venv/bin/python examples/seed_demo.py
.venv/bin/python examples/minimal_search.py "When does Project Atlas ship?"
```

`dev-local-key` is accepted only because `make run` starts a loopback-only
development server with the explicit insecure-development flag.
`seed_demo.py` refuses to write anywhere except a loopback origin and
refuses non-development tokens. Automated isolated test harnesses that
deliberately use a different token must opt in with
`CORTEX_ALLOW_DEMO_SEED=1`. The seeder idempotently loads three synthetic
records from
[`fixtures/demo_captures.json`](fixtures/demo_captures.json). For the
packaged app, copy a scoped `cxa_` REST token from Connections & Privacy for
the read-only examples and never commit it; do not run the demo seeder against
a packaged or personal vault. The fixture includes a release decision, a
storage decision, and an explicit
writing preference, all about or usable with the Project Atlas example task.

## Choose an Example

| Goal | Run | What you get |
|---|---|---|
| Add cited memory search | `.venv/bin/python examples/minimal_search.py` | A concise result and source; add `--json` for retrieval diagnostics |
| Build a complete memory step | `.venv/bin/python examples/memory_workflow.py` | A factual Search/Ask followed by context for a work-shaped task |
| Give roles different context | `.venv/bin/python examples/multi_agent_context.py` | Separate planner, researcher, and writer views of approved memory |
| Discover callable tools | `.venv/bin/python examples/tool_catalog.py` | An OpenAI-compatible tool catalog without calling a model |

Read the corresponding source:
[`minimal_search.py`](minimal_search.py) ·
[`memory_workflow.py`](memory_workflow.py) ·
[`multi_agent_context.py`](multi_agent_context.py) ·
[`tool_catalog.py`](tool_catalog.py).

Every script accepts `CORTEX_BASE_URL` and `CORTEX_API_KEY`. The default base
URL is `http://127.0.0.1:8766`; the query/task examples consistently default
to Project Atlas so the checked-in fixture produces deterministic, cited
output without extra arguments.

`memory_workflow.py` deliberately separates a factual `--query` (for Search and
Ask) from a work-shaped `--task` (for Context). For example:

```bash
.venv/bin/python examples/memory_workflow.py \
  --query "Which database did we choose for Project Atlas?" \
  --task "Draft the Project Atlas architecture section."
```

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

## Why Search, Ask, and Context Can Differ

| Surface | Optimizes for | Expected empty/abstain behavior |
|---|---|---|
| Search | Ranked evidence matching one query | Can return no hits when lexical/semantic evidence is weak |
| Ask | A defensible answer with citations | Abstains when retrieved evidence cannot support an answer |
| Context | A bounded, layer-balanced pack for doing a task | May include useful decisions, preferences, or procedures that do not directly answer the query |

These are separate contracts, not three aliases for the same retrieval call. A
task context can correctly contain cited background while Ask correctly
abstains from making a specific unsupported claim.

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
