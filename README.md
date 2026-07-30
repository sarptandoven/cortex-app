<div align="center">

<img src="macos/Assets/AppIcon.iconset/icon_256x256.png" alt="Cortex" width="128" height="128" />

# Cortex

### Your personal operating model for AI: a local, cited model of how you work.

Every AI tool starts from a foundation model of the world and knows nothing about you. Cortex builds a
**model of you** on your own Mac — your voice, preferences, decisions, and the people and projects
around them — and serves it to Claude, ChatGPT, Cursor, and other MCP clients. Your memory is plain
Markdown plus a rebuildable index that you own, and every answer it gives is **cited or withheld**.

<br/>

<!-- Affiliation, sponsor & license — individual buttons: each bordered, rounded, spaced -->
[<img src=".github/badges/waterloo.png" alt="University of Waterloo" height="30" />](https://uwaterloo.ca)
[<img src=".github/badges/engineering.png" alt="Faculty of Engineering" height="30" />](https://uwaterloo.ca/engineering)
[<img src=".github/badges/research.png" alt="Academic Research" height="30" />](https://uwaterloo.ca/research)
[<img src=".github/badges/composio.png" alt="Sponsored by Composio" height="30" />](https://composio.dev)
[<img src=".github/badges/mit.png" alt="MIT License" height="30" />](LICENSE)

<br/>

[![Download for macOS](https://img.shields.io/github/v/release/doppl-tech/releases?sort=semver&display_name=tag&label=Download%20for%20macOS&color=E4B429&labelColor=000000&logo=apple&logoColor=FDD54F&style=for-the-badge)](https://github.com/doppl-tech/releases/releases/latest)

<br/>

![macOS 13+](https://img.shields.io/badge/macOS-13%2B%20(Apple%20Silicon)-000000?logo=apple&logoColor=white)
![Notarized](https://img.shields.io/badge/Apple-Notarized-555555?logo=apple&logoColor=white)
![SwiftUI](https://img.shields.io/badge/App-SwiftUI-F05138?logo=swift&logoColor=white)
![Python engine](https://img.shields.io/badge/Engine-Python%203.12-3776AB?logo=python&logoColor=white)
![SQLite + sqlite-vec](https://img.shields.io/badge/Storage-SQLite%20%2B%20sqlite--vec-003B57?logo=sqlite&logoColor=white)
![MCP](https://img.shields.io/badge/Protocol-MCP-6E56CF?logo=modelcontextprotocol&logoColor=white)
![Works with Claude](https://img.shields.io/badge/Works%20with-Claude-D97757?logo=claude&logoColor=white)
![On-device retrieval](https://img.shields.io/badge/Retrieval-on--device-2E7D32?logo=ghostery&logoColor=white)

</div>

---

## What Cortex does today

Cortex is a **macOS beta**. It runs a local engine on your Mac that ingests your notes and AI-chat
exports, turns them into typed and cited memory you review, and serves that memory to AI tools over
MCP.

Concretely, today you can:

- **Bring in your history.** Drop in a ChatGPT, Claude, Gemini, Notion, Slack, Discord, or Telegram
  export — or mbox/eml, `.ics`, `.vcf`, DOCX, Zoom transcripts, browser bookmarks, an X or LinkedIn
  archive, and more. See [`docs/SOURCE_IMPORTS.md`](docs/SOURCE_IMPORTS.md).
- **Sync live sources with a key you paste.** GitHub (secretless device-flow sign-in), Slack, Linear,
  Jira, Readwise, Raindrop, Zotero, Calendar, Notion, and Obsidian.
- **Review before you remember.** Captures from connected sources land in a Review inbox, grouped into at
  most fifteen decisions, where you approve, edit, or archive. Approving records the memory and its audit
  event in SQLite, then mirrors it to your Markdown vault. Files you import yourself are treated as trusted
  and are usable right away; pass `auto_approve=false` to route them through Review too.
- **Ask and get citations or nothing.** Retrieval fuses BM25, `sqlite-vec` vector KNN, and temporal
  signals, reranks on-device, and passes through a cite-or-abstain gate. With no cited evidence it tells
  you so rather than guessing.
- **Connect your tools in one click.** Claude Desktop, Cursor, Windsurf, Zed, Cline, Roo Code, VS Code
  Copilot, and Claude Code get a Cortex MCP server written into their config.
- **Keep control.** Per-tool tokens are scoped and revocable, secret/email redaction is on by default,
  and export, maintenance, and destructive capabilities are **off** until you enable them.

Ask composes its answers deterministically from cited excerpts — no generative language model is
bundled or called to write prose. Retrieval embeddings run entirely on-device.

### Current limitations, plainly

- **Apple Silicon, macOS 13+.** The Swift target is `arm64-apple-macosx13.0`; there is no Intel or
  universal build.
- **This beta requires a one-time account sign-in.** The shipped build sets `CortexRequireAccount`, so
  first launch shows a sign-in wall. Your memory still lives and is queried locally — the account is
  identity plus an optional sync target. There is an "Explore with sample notes" path, but it is not
  remembered between launches.
- **Cloud sync is not end-to-end encrypted by default.** Client-side E2EE exists but is opt-in and off,
  so anything you sync is readable server-side. See [`docs/E2EE_SYNC_DESIGN.md`](docs/E2EE_SYNC_DESIGN.md).
- **Managed Google / Microsoft / Notion OAuth is implemented but not configured** in this build; those
  client IDs ship empty. Use file import or a pasted token instead.
- **PDF text extraction is inactive** in the shipped app — `pypdf` is not bundled.

## The loop

<div align="center">

| ① Connect | ② Review | ③ Ask | ④ Control |
|:--:|:--:|:--:|:--:|
| Bring in notes, an AI-chat export, or connect a live source | Approve what's useful, archive the noise — memory stays trustworthy | Ask with cited answers, or let a connected AI tool retrieve what you approved | Keep reads, saves, exports, and every connection visible and revocable |

</div>

## How it works

```mermaid
flowchart LR
    subgraph SOURCES["Your sources"]
      A1["Local notes / Obsidian"]
      A2["ChatGPT · Claude · Notion · Slack"]
      A3["Files & exports"]
    end
    subgraph CORTEX["Cortex: your operating model, on your Mac"]
      B1["Review inbox<br/>(you approve)"]
      B2["Layered, cited memory<br/>SQLite + sqlite-vec"]
      B4["Profile + knowledge graph<br/>(how you work)"]
      B3["Context Assembly Engine<br/>(CMP)"]
    end
    subgraph TOOLS["Your AI tools"]
      C1["Claude Desktop · Cursor<br/>Windsurf · Zed · Cline · more"]
    end
    SOURCES --> B1 --> B2 --> B4 --> B3 --> C1
    C1 -. "cited retrieval" .-> B3
```

A native **SwiftUI** app bundles a Python 3.12 interpreter and launches a local engine
(`backend/app/standalone_server.py`, stdlib `http.server`) on `127.0.0.1:8766`, loopback only. Ingested
sources become typed memory in a **SQLite** store — FTS5 full-text plus `sqlite-vec` vectors — that
mirrors to a human-readable, Obsidian-style vault at
`~/Library/Application Support/Cortex/Cortex.vault/`. When a tool asks, the **Contextual Memory
Protocol** packs the smallest cited, model-calibrated context that answers the task.

The same storage core also runs behind a **FastAPI** app (`backend/app/main.py`) for the hosted
accounts and sync plane. That server is not part of the desktop app's local engine.

## Inside the operating model

Approved memory goes in; a calibrated model of how you operate comes out.

- **It packs context as a protocol.** The [Contextual Memory Protocol](docs/CMP_PROTOCOL.md) fits an
  **SMP envelope** calibrated to the consuming model: name your model and the pack is token-budgeted and
  filled by a marginal-utility knapsack with MMR diversity rather than a flat greedy fill. A
  **per-session delta channel** then tells each agent what changed since its last turn — new,
  superseded, evicted — and never re-announces a memory it already holds, an invariant pinned in CI by
  [`scripts/context_pack_eval.py`](scripts/context_pack_eval.py).
- **It retrieves and cites, or abstains.** BM25 and `sqlite-vec` vector KNN are fused with temporal
  signals by weighted reciprocal rank, diversified by provenance, and reranked on-device with MMR; an
  intent retriever backs them up when the lexical and temporal arms come back empty. Embeddings come
  from a bundled 256-dimension `model2vec` model — no API key, no network. When nothing carries a
  relevant citation, Ask returns that fact instead of an answer.
- **It models judgment.** Deterministic extractors build seven typed layers — `semantic`, `episodic`,
  `style`, `decision`, `preference`, `negative`, `procedural` — plus entity and topic indexes, into a
  whole-person map. Agents call `GET /v1/agent-adaptation` to load your calibration brief before they
  work.
- **It recalls relationally.** Ask expands along direct stored relations (shared entity, same capture).
  Agents can opt into **bounded multi-hop recall** over a trust-aware memory graph — depth ≤ 3,
  trust-floored, path-strength gated — including a true **personalized-PageRank** mode, via
  `search_memory` with `associative=true`. Every hop returns its full path with per-edge kind and weight.
  Neither is the default Ask path.
- **It consolidates on request.** A bounded consolidation pass resolves only contradictions that clear
  deterministic safety rules, keeps your source memory authoritative, logs every decision, and pre-warms
  verified hot-context packs. It runs when invoked (`POST /v1/memory/consolidate` or the
  `consolidate_memory` tool) and requires the maintenance permission, which is off by default.
- **It compounds as an asset you own.** Pin a pack and it becomes **sha256-addressed and replayable**,
  byte-verified on every read, so an agent can prove exactly what it acted on. Your model lives as plain
  Markdown memories plus durable JSON/JSONL records, with an index rebuildable from that vault — vendor-portable, inspectable, exportable as
  a signed bundle, and erasable in one act. Switch assistants and it comes with you.

## Also inside

- **One-click connections.** Config-write-and-relaunch for desktop MCP clients, deeplink install for
  Cursor and VS Code, and copy-paste HTTP endpoints for LM Studio, Open WebUI, LibreChat, and
  AnythingLLM. Web tools (ChatGPT, Claude web, Perplexity, Gemini) connect through a hosted connector
  key and need an account. See [`docs/EXTERNAL_INTEGRATIONS.md`](docs/EXTERNAL_INTEGRATIONS.md) and
  [`docs/MCP_INTEGRATIONS.md`](docs/MCP_INTEGRATIONS.md).
- **A curated MCP surface.** Tokens minted for your tools carry `read` and `write` scopes and see ten
  core tools, including `use_cortex`, `get_context`, `ask_memory`, `search_memory`, and `remember_this`.
  Permission checks run on every call, not just in the UI.
- **Trust controls.** Scoped revocable per-tool permissions, redaction on by default, an append-only
  audit log, and a tamper-evident hash chain over your history. See
  [`docs/TRUST_CONTROLS.md`](docs/TRUST_CONTROLS.md).
- **Portable memory.** A published, test-vectored bundle spec under
  [`spec/portable-memory/v2/`](spec/portable-memory/v2). See
  [`docs/PORTABLE_MEMORY_PROTOCOL_V2.md`](docs/PORTABLE_MEMORY_PROTOCOL_V2.md).

## Beyond the Mac app

| Surface | What it is | Path |
|---|---|---|
| Obsidian plugin | Sync a vault and write cited memory back into it | [`packages/obsidian-cortex-plugin`](packages/obsidian-cortex-plugin) |
| Browser extension | MV3 extension that injects cited context into ChatGPT, Claude, and Notion | [`extension/`](extension) |
| OpenClaw context plugin | Context-engine adapter for OpenClaw | [`packages/openclaw-cortex-context`](packages/openclaw-cortex-context) |
| Python SDK | Dependency-free client for the local API | [`sdk/python`](sdk/python) |
| TypeScript SDK | Dependency-free client for the local API | [`sdk/typescript`](sdk/typescript) |

## Install

1. **[Download the latest DMG →](https://github.com/doppl-tech/releases/releases/latest)** (macOS 13 or
   later, Apple Silicon).
2. Open the DMG and drag **Cortex** into Applications.
3. Launch it. Release builds are Developer ID signed and **notarized by Apple**, so it opens with no
   warning.
4. Sign in, then point Cortex at a notes folder or import your AI chats, review your first memories,
   and connect a tool.

Cortex checks the HTTPS release feed for new builds and links you to the current DMG. Installing an
update still uses the normal macOS app-replacement flow.

## Five-minute developer quickstart

Requires Python 3.12. This starts a loopback-only development server and uses
synthetic data, so it does not touch your packaged Cortex vault.

```bash
make setup
CORTEX_AUTO_APPROVE_CAPTURES=1 make run
```

In a second terminal:

```bash
curl -sS http://127.0.0.1:8766/v1/captures \
  -H 'Authorization: Bearer dev-local-key' \
  -H 'Content-Type: application/json' \
  -d '{"content":"Project Atlas ships Thursday after the rollback drill.","source":"quickstart"}'

curl -sS -G http://127.0.0.1:8766/v1/ask \
  -H 'Authorization: Bearer dev-local-key' \
  --data-urlencode 'query=When does Project Atlas ship?'
```

The second response either includes the cited quickstart memory or explicitly
abstains. Next, try the runnable [Python examples](examples/README.md), the
[Python SDK](sdk/python/README.md), or the
[TypeScript SDK](sdk/typescript/README.md). `dev-local-key` is accepted only by
the explicit local development configuration.

## Build from source

Requires **Python 3.12** and Xcode command line tools. CI and the packaged runtime both use Python 3.12.

```bash
# Reproducible contributor environment
make setup
make check
make test

# macOS app (SwiftUI) — writes macos/build/Cortex.app
./macos/build.sh

# Run the hosted-plane API (FastAPI + uvicorn) at 127.0.0.1:8766
make run

# Or run the stdlib engine the packaged app actually ships
cd backend && python3 -m app.standalone_server --host 127.0.0.1 --port 8766

# Deterministic, offline quality gates (no dependencies needed)
python3 scripts/retrieval_eval.py
python3 scripts/adaptation_eval.py
```

By default `./macos/build.sh` produces an ad-hoc-signed app with **no** bundled interpreter or
embedding model, and prints an embeddings-fallback warning — good enough to launch and inspect. A full
build needs `CORTEX_BUNDLE_PYTHON=1` and a python.org framework install at
`/Library/Frameworks/Python.framework/Versions/3.12`; release packaging lives in
`macos/package_release.sh`.

CI runs the full gate set for pull requests and pushes to `main` — see [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
for the authoritative command list. [`SETUP.md`](SETUP.md) covers the app-side development loop.

## Repository layout

| Path | What lives there |
|---|---|
| `macos/` | The SwiftUI/AppKit app and its build and packaging scripts |
| `backend/app/` | Storage core, retrieval, connectors, MCP tools, and both HTTP servers |
| `backend/tests/` | The test suite (2,000+ pytest cases, plus parameterized subtests) |
| `scripts/` | Dev helpers plus the deterministic CI quality gates |
| `packages/`, `sdk/`, `extension/` | Plugins, client SDKs, and the browser extension |
| `docs/` | Architecture, protocol, and release documentation |
| `deploy/`, `site/` | Hosted-plane ops and the static distribution site |
| `capture.py`, `ingest.py`, `mcp_server.py`, `redis_store.py`, `github_store.py`, `ui.py` | **Legacy prototype** at the repo root, kept for reference. Not used by the app; the root `requirements.txt` (Redis, Voyage AI, rumps) belongs to it, not to `backend/`. |

## Documentation

| Area | Doc |
|---|---|
| Documentation index and status | [`docs/README.md`](docs/README.md) |
| System architecture | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Contextual Memory Protocol | [`docs/CMP_PROTOCOL.md`](docs/CMP_PROTOCOL.md) · [`docs/PORTABLE_MEMORY_PROTOCOL_V2.md`](docs/PORTABLE_MEMORY_PROTOCOL_V2.md) |
| The vault format you own | [`docs/LOCAL_VAULT_FORMAT.md`](docs/LOCAL_VAULT_FORMAT.md) |
| Connecting AI tools | [`docs/EXTERNAL_INTEGRATIONS.md`](docs/EXTERNAL_INTEGRATIONS.md) · [`docs/MCP_INTEGRATIONS.md`](docs/MCP_INTEGRATIONS.md) |
| Importing your sources | [`docs/SOURCE_IMPORTS.md`](docs/SOURCE_IMPORTS.md) |
| Trust & privacy controls | [`docs/TRUST_CONTROLS.md`](docs/TRUST_CONTROLS.md) |
| Reproducible benchmark results | [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) |
| Open-source readiness review | [`docs/OPEN_SOURCE_READINESS.md`](docs/OPEN_SOURCE_READINESS.md) |
| Examples | [`examples/README.md`](examples/README.md) |
| FAQ and troubleshooting | [`docs/FAQ.md`](docs/FAQ.md) · [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) |
| Sync & encryption | [`docs/E2EE_SYNC_DESIGN.md`](docs/E2EE_SYNC_DESIGN.md) · [`docs/CXE1_WIRE_FORMAT.md`](docs/CXE1_WIRE_FORMAT.md) |
| Install & update checks | [`docs/INSTALLER_AND_UPDATES.md`](docs/INSTALLER_AND_UPDATES.md) |
| Release & distribution | [`docs/DISTRIBUTION.md`](docs/DISTRIBUTION.md) · [`docs/APPLE_RELEASE.md`](docs/APPLE_RELEASE.md) |
| Experimental pairwise twin evaluation | [Overview](https://github.com/trace-cortex/cortex-app/blob/feat/pairwise-twin-eval/docs/PAIRWISE_TWIN_EVALUATION.md) · [Integration guide](https://github.com/trace-cortex/cortex-app/blob/feat/pairwise-twin-eval/docs/PAIRWISE_TWIN_INTEGRATION_GUIDE.md) |

## Contributing and support

Start with [`CONTRIBUTING.md`](CONTRIBUTING.md), then use the issue forms for a
reproducible bug or a scoped feature proposal. Security reports belong in the
private channel described by [`SECURITY.md`](SECURITY.md), not a public issue.
Project direction and release history live in [`ROADMAP.md`](ROADMAP.md) and
[`CHANGELOG.md`](CHANGELOG.md); support boundaries are in
[`SUPPORT.md`](SUPPORT.md).

## Privacy

Your memory is stored as plain files on your Mac at
`~/Library/Application Support/Cortex/Cortex.vault/`, and ingestion, retrieval, Ask, and the profile all
run locally on the bundled engine — embeddings included. Cortex reads a source only after you connect
it and records nothing ambient: there is no microphone access, and screen capture happens only when you
trigger it. Context goes to an AI tool only within the scoped permission you grant, with redaction on by
default.

Two things worth being precise about. The current beta build requires a one-time account sign-in for
identity and sync, so it is not account-free — though your memory stays local either way. And synced
content is readable server-side unless you turn on client-side encryption, which is off by default.
Questions: **sdoven@uwaterloo.ca** or **vamika_singhal@berkeley.edu**.

## Affiliations & sponsor

<table>
  <tr>
    <td align="center" width="33%">
      <a href="https://uwaterloo.ca"><img src=".github/badges/waterloo.png" alt="University of Waterloo" height="30" /></a><br/>
      <sub>Built at the <b>University of Waterloo</b><br/>Faculty of Engineering</sub>
    </td>
    <td align="center" width="33%">
      <a href="https://uwaterloo.ca/research"><img src="https://img.shields.io/badge/Academic-Research-B5179E?style=for-the-badge&labelColor=000000" alt="Research" /></a><br/>
      <sub>An applied <b>research</b> project on<br/>local-first personal operating models</sub>
    </td>
    <td align="center" width="33%">
      <a href="https://composio.dev"><img src="https://github.com/ComposioHQ.png" width="44" height="44" alt="Composio" /></a><br/>
      <sub>Proudly sponsored by<br/><a href="https://composio.dev"><b>Composio</b></a></sub>
    </td>
  </tr>
</table>

## License

Cortex is released under the **[MIT License](LICENSE)**, free to use, modify, and build on.
© 2026 Doppl.

<div align="center">
<br/>

**Cortex is your personal operating model for AI:** the cited, local, portable model of how you work.

<sub>The more you bring in, the more your tools act the way you would.</sub>
</div>
