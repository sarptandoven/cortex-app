# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Cortex (by Doppl) is a **local-first personal memory / "operating model" for AI tools**. A native SwiftUI
macOS app bundles a Python engine on `127.0.0.1:8766`. Notes and AI-chat exports are ingested into typed,
layered, cited memory stored in a user-owned Markdown/JSON **vault** with a rebuildable **SQLite** index
(FTS5 + `sqlite-vec`). AI clients (Claude Desktop, Cursor, Zed, ChatGPT/Claude web via the browser
extension) retrieve approved memory over **MCP**.

## Commands

### Backend

```bash
# Dev server (FastAPI + uvicorn, reload, dev token)
./scripts/dev_backend.sh                     # 127.0.0.1:8766, CORTEX_ALLOW_INSECURE_DEV_TOKEN=1

# Test suite — pytest is the canonical runner. conftest.py applies import-time
# environment isolation because backend.app.main builds a process-singleton store
# on first import. `unittest discover` bypasses that isolation and can write a real
# default vault.
python -m pytest backend/tests -q
python -m pytest backend/tests/test_context_engine.py -q            # one file
python -m pytest backend/tests/test_context_engine.py::ClassName::test_name -q   # one test
python -m pytest backend/tests -q -k "retrieval and not scale"      # by expression
```

Run `make setup` to install the hash-locked contributor environment.
`backend/requirements.txt` defines the hosted-plane dependencies, while
`backend/runtime-requirements.txt` defines the packaged-app runtime
(`pysqlite3`, `sqlite-vec`, `model2vec`). `legacy/requirements.txt` belongs to
the archived prototype only.

There is no repository-wide formatter or Python type checker. CI compiles
`backend`, `examples`, `legacy`, and `scripts`, runs Bandit and dependency
audits, and type-checks the TypeScript packages. Match surrounding Python style
by hand.

`SETUP.md` uses `make test`, which invokes pytest and preserves the temporary
vault/database isolation. **Do not substitute `unittest discover`.**

### Quality gates (all of these block CI — run before claiming done)

```bash
python scripts/retrieval_eval.py             # retrieval-quality regression gate
python scripts/tool_routing_eval.py          # MCP tool-routing gate
python scripts/adapter_contract_eval.py      # universal adapter contract
python scripts/context_pack_eval.py          # context-pack quality
python scripts/token_calibration_eval.py     # per-model token calibration
python scripts/agent_task_eval.py --deterministic
python scripts/delivery_eval.py              # delivery/push safety
python scripts/adaptation_eval.py            # adaptation quality
python scripts/rerank_eval.py                # model2vec lift; SKIPS when model not provisioned
python scripts/check_connector_baseline.py   # connector baseline contract
python scripts/check_docs_current.py         # docs-currency phrase gate (see below)
python scripts/backend_beta_smoke.py
python scripts/ops_readiness_check.py --skip-tests --skip-build
python scripts/appstore_compliance_lint.py   # App Store 2.5.1/2.5.2/4.8 + privacy/entitlements
python scripts/check_distribution_site.py
python scripts/validate_update_manifest.py --allow-remote-artifacts site/downloads/latest.json
bandit -c .bandit.yaml -r backend scripts -ll # fails on MEDIUM+
pip-audit --strict -r requirements-dev.lock
pip-audit --strict -r backend/requirements.lock
pip-audit --strict -r backend/runtime-requirements.lock
pip-audit --strict -r legacy/requirements.txt
```

### macOS app

```bash
./macos/build.sh                                              # -> macos/build/Cortex.app
codesign --verify --deep --strict --verbose=2 macos/build/Cortex.app
open macos/build/Cortex.app

# CI/dev-mode build (no bundled interpreter, ad-hoc signature):
CORTEX_BUNDLE_PYTHON=0 CORTEX_CODESIGN_IDENTITY=- CORTEX_CODESIGN_TIMESTAMP=0 ./macos/build.sh
```

`build.sh` invokes **`swiftc` directly** on `macos/Sources/*.swift` and hand-assembles the `.app` bundle —
there is no Xcode project and no SwiftPM manifest. Target is `arm64-apple-macosx13.0`.

### Obsidian plugin

```bash
scripts/check_obsidian_plugin.sh             # typecheck + build (Node 22)
```

## Architecture

### Two HTTP server implementations — keep them in sync

This is the single most important structural fact in the repo.

- **`backend/app/standalone_server.py`** — a pure-stdlib `http.server`/`ThreadingHTTPServer` implementation.
  **This is what ships.** The macOS app launches `python3 -S -s -m app.standalone_server --host 127.0.0.1
  --port 8766` from `macos/Sources/CortexApp.swift` against a bundled interpreter that has **no
  FastAPI/uvicorn/pydantic**. The smoke/verify/bench scripts boot this one too (`verify_full_pipeline.py`,
  `verify_pipeline_live.py`, `live_product_smoke.py`, `e2e_external_connections.py`, `cmp_demo.py`,
  `demo_seed.py`, `bundle_bench.py`, `cmp_scale_bench.py`). The `*_eval.py` gates do **not** — they exercise
  `CortexStore` in-process, so they will not catch a route wired into only one server.
- **`backend/app/main.py`** — the FastAPI app. This is the dev server
  (`scripts/dev_backend.sh`) and the hosted/multi-tenant plane (`scripts/load_test_10k.py` runs it under
  uvicorn in sharded mode).

Both dispatch into the same `CortexStore` and the same `mcp_tools` registry, but they are **not route-identical
in either direction** — verified:

- Only in `main.py`: the whole accounts / OAuth-login / billing / admin plane (`/v1/auth/*`, `/v1/admin/*`,
  `/v1/billing/webhook`). The shipping Mac app never serves `/v1/auth/signup`.
- Only in `standalone_server.py`: `/v1/context-file/preview`, `/v1/context-file/sync`, `/v1/delivery/preview`,
  `/v1/delivery/send`, `/v1/imports/detect`, `/v1/pair`.

So "edit both files" is the default for shared product endpoints, but decide deliberately which plane a new
route belongs to. ⚠️ `test_fastapi_contract.py` and `test_standalone_server.py` each test their own surface;
**nothing in the suite diffs the two route sets**, and the `*_eval.py` gates run in-process. A route wired into
only one server ships green. Check by hand.

### Storage: vault is truth, SQLite is a cache

- **`backend/app/storage.py`** (~33k lines) is the monolith holding `CortexStore` — nearly all business
  logic lives here. Expect to `grep` rather than read it.
- The **local vault** (`~/Library/Application Support/Cortex/Cortex.vault/`, or `CORTEX_VAULT_PATH`) holds
  human-readable JSON/Markdown records: `captures/`, `memories/`, `tasks/`, `entities/`, `graph_edges/`,
  `imports/`, `source_accounts/`, `sync_cursors/`, plus `manifest.json`, `settings.json`, `events.jsonl`,
  `backups/`.
- `index.sqlite` (FTS5 + `sqlite-vec`) is **derived and disposable**. `POST /v1/maintenance/rebuild-index-from-vault`
  reconstructs it. Never let index-only state become authoritative — if you write a record, write the vault
  record too (`vault.py`, `vault_markdown.py`, `mirror.py`).
- `sharding.py` (`StoreRegistry`) maps users to shard DB/vault paths; `shard_mode="local"` is the single-user
  packaged case, other modes are the hosted path.

### Memory model

Captures have a lifecycle `pending → approved | archived` (plus hard delete). There is **no `rejected`
state** — archive is the reject. **Nothing reaches AI tools until the user approves it in the Review inbox**;
this gate is a product invariant, and `CORTEX_AUTO_APPROVE_CAPTURES` is deliberately **overridden by a
connected source's own review policy**, so connector trust can't be bypassed by a global flag.
`backend/app/models.py` is the authoritative taxonomy: `MemoryKind` = claim, decision, event, preference, observation, action, question,
summary, style, negative, procedure; `MemoryLayer` = semantic, episodic, style, decision, preference, negative,
procedural. Entities and topics are separate join tables, not memory layers. Entities
(person/project/org/topic) plus edges form the knowledge graph used by `graph_analysis.py` — bounded
strongest-path walk, plus a `personalized_pagerank` mode.

`docs/ARCHITECTURE.md` maps the endpoint surface and vault layout; `docs/CMP_PROTOCOL.md` is the CMP spec
and names its own source-of-truth files. The `TOOLS` registry in `backend/app/mcp_tools.py` remains the
authoritative MCP surface; update the architecture inventory whenever that registry changes.

### Ingestion and connectors

`source_ingest.py` + `backend/app/connectors/` (obsidian, notion, gmail, google_drive, calendar, slack, jira,
linear, github, outlook, raindrop, readwise, zotero, agent_sessions). Every connector must satisfy the
baseline contract enforced by `scripts/check_connector_baseline.py` and the `test_connector_*` tests
(cursor/pagination data-loss, redaction via `connectors/_redaction.py`, SSRF guards).

Extraction: `extractor.py` runs deterministic extractors; `condense.py` adds an LLM pass when
`ANTHROPIC_API_KEY` is set. **Without the key the backend must still work** via the deterministic extractor —
don't introduce hard LLM dependencies in the ingest path.

### Retrieval and CMP

`query_plan.py` builds the plan (intent classification, entity extraction, decomposition) behind flags
`query_plan_enabled()`, `context_hop_enabled()`, `entity_boost_enabled()`, `second_hop_enabled()` with
millisecond deadlines. `CortexStore.search` / `_vector_search` fuse BM25 + `sqlite-vec` KNN + temporal + intent.

The context engine is `CortexStore.assemble_context` → `_pack_context_knapsack` → `_compute_working_memory_delta`,
projected onto the wire by `smp.py` (`build_smp_envelope`, `SMP_LEGEND`). Key invariants, stated in the
`smp.py` docstring and enforced upstream: **cited-only, superseded-never, global-dedup, provenance**. SMP is a
*pure projection* — it must invent, re-rank, and drop nothing. Answers **cite or abstain**; returning uncited
text is a regression the eval gates catch.

Consolidation ("dreaming") is real: tables `memory_consolidation_runs`, `memory_consolidation_decisions`,
`hot_context_packs`. The pass detects conflicts, auto-resolves only those clearing deterministic rules
(everything else → `conflicts_review_required`), commits SQLite mutations atomically, then patches the vault
*outside* the transaction and reports mirror failures as warnings rather than faking a rollback. It then
pre-warms hot context packs and verifies each one round-trips before counting it.

### MCP

`backend/app/mcp_tools.py` is a **hand-written JSON-RPC handler, not the `mcp` SDK**. `TOOLS` holds **110**
tools, exposed at `POST /mcp` (streamable-HTTP compatible: `Accept: text/event-stream` gets SSE +
`Mcp-Session-Id`; JSON clients get byte-identical `application/json`). Desktop clients connect through the
bundled stdio proxy `scripts/cortex_mcp_stdio.py`. `legacy/mcp_server.py` is the **archived prototype**.

Clients cap tool counts (Cursor 40, ChatGPT 128), so `MCP_TOOL_SURFACES` in `mcp_tools.py` advertises
subsets — `core` (10), `coding`, `chatgpt`, `full` — selected by the token's label. **Surface is advertisement
only, never authorization**; scopes do the enforcing. The oddly-named `search` / `fetch` tools exist solely
because the ChatGPT deep-research connector requires those exact names.

Legacy prototype files are kept under `legacy/` for reference only, including
`capture.py`, `ingest.py`, `github_store.py`, `redis_store.py`, `mcp_server.py`,
and `ui.py`. They still must compile because CI runs `compileall` over the
directory.

### The universal adapter surface

`GET /v1/tools/schema?format=openai|anthropic`, `POST /v1/tools/call`, and `POST /v1/context` are the
non-MCP integration surface. Both SDKs (`sdk/python` → `doppl-cortex-client`, `sdk/typescript` →
`@doppl-tech/cortex-client`, each zero-dependency) and the browser extension talk to exactly these.
`scripts/adapter_contract_eval.py` gates them.

The browser extension (`extension/`, MV3, vanilla JS, no build step) is **inject-only, not capture**: it reads
what you typed in ChatGPT/Claude/Notion, asks the local server for cited context, and prepends it to the
prompt. Its auth token is minted by the macOS app via `POST /v1/pair` (`cxm_` prefix).

### Out-of-tree consumers

`packages/obsidian-cortex-plugin` is CI-gated and asserts the committed `main.js` matches a fresh build.
`packages/openclaw-cortex-context` implements the portable-memory spec in TypeScript — meaning
`spec/portable-memory/v2/schema.json` (Ed25519-signed, hash-chained export bundles) has **two independent
implementations** and conformance vectors in `spec/portable-memory/v2/test-vectors/`. Changing the format
means changing both sides plus regenerating vectors via `scripts/generate_portable_memory_vectors.py`.
Neither the extension, the SDKs, nor the OpenClaw package run in CI.

### Hosted control plane (accounts, auth, encryption, billing)

This layer is fully built and **dormant in the local product** — none of it runs in the packaged app.

- `accounts.py` — `ControlStore` / `SQLiteControlStore` in a separate control-plane DB
  (`<shard_root>/control/accounts.sqlite`). Load-bearing invariant: `user_id` is the immutable non-PII
  shard-routing key and account merge is **never** an `UPDATE` of `user_id`.
- `authn.py` — argon2id (scrypt fallback), opaque `cxs_`/`cxr_` tokens, **no JWTs**, refresh rotation with
  family revocation on reuse.
- `keyring.py` — envelope encryption: root KEK → per-user master DEK → HKDF purpose subkeys. The **CXE1**
  ciphertext format (`b"CXE1" || kek_id || dek_version || nonce || AES-256-GCM`) is AAD-bound to
  `user_id || purpose || dek_version`, so a blob copied to another user or purpose fails authentication.
  `scripts/cortex_decrypt.py` is the offline decryptor.
- `billing.py` — Paddle + Stripe webhooks, **config-gated**: with no provider/secret the route 404s and a
  no-billing deployment is byte-identical. The site advertises free beta only.
- `oauth_broker.py` — hosted-only secret holder for providers without a PKCE public-client path (Notion,
  GitHub). Posture: *the auth handshake transits the server, the data does not* — the broker never persists
  tokens, and redirect URIs are allowlisted to loopback callbacks.

`accounts.py`, `authn.py`, and `keyring.py` are deliberately **framework-free stdlib** so the same code can
run inside the bundled interpreter. Don't import fastapi/pydantic into them.

Tables with no product surface yet: `working_canvas_nodes`, `shared_memory_principals`,
`shared_memory_nonces`, `shared_memory_writes` — currently only counted in diagnostics/isolation checks.

### Hosted deployment

`deploy/` is a working kit, not scaffolding: `deploy/push.sh` → `bootstrap.sh` provisions a single Ubuntu VM
(ufw, fail2ban, Caddy TLS reverse-proxying `127.0.0.1:8766`) with systemd units `cortex-api`, `cortex-worker`,
`cortex-backup.timer`; `deploy/macmini/` is the launchd + Cloudflare Tunnel alternative. Hosted mode is the
same FastAPI app with `CORTEX_SHARD_MODE=bucket`, scoped API tokens, a KEK file, and rate limits
(`deploy/cortex.env.example`). `status/` is explicitly a scaffold — Upptime runs in its own repo.

### macOS client

`macos/Sources/CortexApp.swift` (~14k lines) holds app state, the backend supervisor, and the custom tab bar.
Three primary tabs — **Model** (`ModelTab.swift`), **Review** (`ReviewTab.swift`), **Ask** (`AskTab.swift`) —
plus the `ConnectionsPrivacySheet.swift` surface for sources, trust, MCP tool setup, backups, and diagnostics.
`OnboardingView.swift` drives first run. Ancillary surfaces: `MenuBarQuickPanel`, `QuickCapture`,
`NotchNotifier`, `LiveActivityPill`, `ConstellationOverlay`, `MemoryWrapped`, `MemoryMapView`, `ImportDiffView`,
`TwinAlertsViews`. `CortexDesign.swift` / `CortexNorthStar.swift` hold the design tokens and product principles.

### Cross-language contract tests

Several Python tests assert on the **text of Swift/HTML/CSS source files** — e.g.
`backend/tests/test_macos_ui_quality_contract.py` asserts literal strings like `hosting.sizingOptions = []`
exist in `CortexApp.swift`. Editing a Swift view, the extension, or `site/styles.css` can break `pytest`.
Same family: `test_macos_connector_ui_contract.py`, `test_macos_constellation_contract.py`,
`test_macos_phase_ui_contract.py`, `test_macos_external_connections_contract.py`,
`test_onboarding_sources_contract.py`.

### Docs-currency gate

`scripts/check_docs_current.py` fails CI when a doc contains a phrase from its per-file `FORBIDDEN_PHRASES`
list (stale product vocabulary, removed flows, retired tabs). When you change product behaviour, update the
docs *and* that script's expectations together.

## Conventions that matter

- **Embeddings — read this before trusting a green CI run.** Default provider is offline `hash`
  (deterministic, no network). Semantic near-dup detection, reranking, MMR, and entity boost are all gated on
  `provider != "hash"` and **no-op under it** — and `hash` is exactly what CI runs, since
  `backend/requirements.txt` doesn't install `model2vec`. **CI proves the deterministic skeleton, not the
  semantic system.** `scripts/rerank_eval.py` is the gate for semantic lift and it exits 0 with
  `status: skipped` when the model is absent; only release packaging enforces it, via
  `macos/package_release.sh` setting `CORTEX_REQUIRE_MODEL=1` plus
  `check_vector_runtime.py --require-model2vec`. Test semantic changes with the model provisioned.
  Keep `CORTEX_EMBEDDING_DIMENSIONS=384` (the `sqlite-vec` schema depends on it).
- **Source protection.** `build.sh` compiles the bundled backend to `.pyc` and deletes the `.py` files
  (`CORTEX_SKIP_PYC=1` to opt out for local debugging).
- **Auth.** REST requires `CORTEX_API_KEY` (Bearer). `/mcp` also accepts a scoped `CORTEX_MCP_API_KEY` with
  `CORTEX_MCP_API_KEY_SCOPES`. `CORTEX_ALLOW_INSECURE_DEV_TOKEN=1` is dev-only.
- **Privacy invariants.** No ambient capture (no screen, no mic). `GET /v1/sync/changes` is deliberately
  **content-free** — event metadata only, never capture/memory text. Redaction is on by default in context
  packs, exports, and agent payloads. Treat these as load-bearing; tests enforce them.
- **Bandit.** `.bandit.yaml` skips exactly three IDs, each with a written justification (B608 — SQL f-strings
  interpolate only hardcoded predicates, values are `?`-bound; B310 — urlopen against validated endpoints;
  B108 — tmp dirs in fixtures only) and an explicit *"Do NOT add blanket skips"* instruction. Fix the finding,
  don't widen the skip list.
- **Release.** `CFBundleShortVersionString` + `CFBundleVersion` in `macos/Info.plist` (currently `0.2.0` / `52`);
  each release bumps the build and regenerates `site/downloads/latest.json` (SHA-256 pinned artifacts) via
  `macos/package_release.sh` / `scripts/prepare_distribution_site.py`. Commits follow
  `release: bump build N -> N+1 (...)`.
- Local-only development needs no Redis, no Docker, no GitHub token, and no hosted account.
