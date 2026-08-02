# Cortex Contributor Code Map

Use this page to find the first file to open. Cortex has two HTTP runtimes around
one storage/retrieval core, so runtime-facing changes usually need parity tests.

| I want to change… | Start here | Verify with |
|---|---|---|
| FastAPI development/hosted route | [`backend/app/main.py`](../backend/app/main.py), [`backend/app/models.py`](../backend/app/models.py) | [`backend/tests/test_fastapi_contract.py`](../backend/tests/test_fastapi_contract.py) |
| Packaged local HTTP behavior | [`backend/app/standalone_server.py`](../backend/app/standalone_server.py) | [`backend/tests/test_standalone_server.py`](../backend/tests/test_standalone_server.py) |
| Capture extraction | [`backend/app/extractor.py`](../backend/app/extractor.py) and capture methods in [`storage.py`](../backend/app/storage.py) | [`test_extractor_quality.py`](../backend/tests/test_extractor_quality.py), [`test_storage_lifecycle.py`](../backend/tests/test_storage_lifecycle.py) |
| Search/ranking | Retrieval section near `_search_rows` in [`backend/app/storage.py`](../backend/app/storage.py) | [`test_retrieval_quality.py`](../backend/tests/test_retrieval_quality.py), [`scripts/retrieval_eval.py`](../scripts/retrieval_eval.py) |
| Context packs / CMP | Context-pack methods in [`backend/app/storage.py`](../backend/app/storage.py), [`backend/app/models.py`](../backend/app/models.py) | [`scripts/context_pack_eval.py`](../scripts/context_pack_eval.py) |
| Embeddings/vector index | [`backend/app/embeddings.py`](../backend/app/embeddings.py), vector-index section in [`storage.py`](../backend/app/storage.py) | [`test_embeddings.py`](../backend/tests/test_embeddings.py), [`test_vector_dim_migration.py`](../backend/tests/test_vector_dim_migration.py) |
| A source connector | [`backend/app/connectors/`](../backend/app/connectors), connector setup/scheduler methods in [`storage.py`](../backend/app/storage.py) | [`ADDING_A_CONNECTOR.md`](ADDING_A_CONNECTOR.md) |
| MCP tool behavior | [`backend/app/mcp_tools.py`](../backend/app/mcp_tools.py) | [`backend/tests/`](../backend/tests) (`test_mcp_*.py`) |
| Vault, backup, and repair | [`backend/app/vault.py`](../backend/app/vault.py), lifecycle/maintenance methods in [`storage.py`](../backend/app/storage.py) | [`backend/tests/`](../backend/tests) (`test_vault*.py`), [`test_native_vault_source_of_truth.py`](../backend/tests/test_native_vault_source_of_truth.py), [`test_job_reliability.py`](../backend/tests/test_job_reliability.py) |
| Authentication and capabilities | [`backend/app/authn.py`](../backend/app/authn.py), [`backend/app/accounts.py`](../backend/app/accounts.py), route/tool policies in [`main.py`](../backend/app/main.py) and [`mcp_tools.py`](../backend/app/mcp_tools.py) | [`test_authn_sessions.py`](../backend/tests/test_authn_sessions.py), [`test_auth_http.py`](../backend/tests/test_auth_http.py), capability contract tests |
| Python SDK | [`sdk/python/cortex_client/client.py`](../sdk/python/cortex_client/client.py) | [`sdk/python/tests/`](../sdk/python/tests) |
| TypeScript SDK | [`sdk/typescript/src/index.ts`](../sdk/typescript/src/index.ts) | `npm test` in [`sdk/typescript/`](../sdk/typescript) |
| macOS UI / lifecycle | [`macos/Sources/`](../macos/Sources) | `swift test` in [`macos/`](../macos) |
| Public examples | [`examples/`](../examples) | `make examples-check` |
| Release metadata/site | [`site/`](../site), [`macos/package_release.sh`](../macos/package_release.sh) | `make docs-check` |

## Runtime Parity

`backend/app/main.py` is the FastAPI development and optional hosted surface.
`backend/app/standalone_server.py` is the dependency-light server bundled in
the desktop app. Both call the same `CortexStore`, but they have separate HTTP
adapters.

When adding or changing an endpoint:

1. update the shared model/store behavior;
2. update both HTTP adapters when the endpoint belongs in local mode;
3. add one FastAPI contract test and one standalone-server contract test;
4. run `make runtime-check`.

Hosted-only filesystem operations must stay hosted-safe: a path sent to FastAPI
refers to the server filesystem, never the user's Mac.

## About `storage.py`

`backend/app/storage.py` is a large compatibility boundary that currently owns
schema migrations and several legacy domain services. Avoid adding an unrelated
new subsystem to it.

- Put pure parsing, vendor HTTP, scoring, or transformation logic in a focused
  module.
- Keep `CortexStore` as the transaction/orchestration boundary.
- Reuse public store methods instead of duplicating persistence rules in routes.
- Add a narrow private helper when behavior must remain transaction-local.
- Preserve vault-first durability: SQLite is a rebuildable index, not the sole
  source of truth.

This is an architectural direction, not a claim that the existing module has
already been split.

## Fast Validation Loop

```bash
make doctor
make check
make runtime-check
```

Run `make test` before a broad backend pull request. SDK-only changes can use
their focused test suites first, but `make check` remains the required local
pre-PR gate.
