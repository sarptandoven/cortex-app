<div align="center">

<img src="macos/Assets/AppIcon.iconset/icon_256x256.png" alt="Cortex" width="128" height="128" />

# Cortex

### Your private, personal memory for every AI tool — on your Mac, owned by you.

Cortex turns your notes and AI chat history into one **reviewed, cited memory** that lives on your
Mac. Then it feeds that memory to Claude, ChatGPT, Cursor, and any MCP-capable tool — so they stop
starting from zero and start picking up where you left off.

<br/>

<!-- Affiliation, sponsor & license — individual buttons: each bordered, rounded, spaced -->
[<img src=".github/badges/waterloo.png" alt="University of Waterloo" height="53" />](https://uwaterloo.ca)
[<img src=".github/badges/engineering.png" alt="Faculty of Engineering" height="53" />](https://uwaterloo.ca/engineering)
[<img src=".github/badges/research.png" alt="Academic Research" height="53" />](https://uwaterloo.ca/research)
[<img src=".github/badges/composio.png" alt="Sponsored by Composio" height="53" />](https://composio.dev)
[<img src=".github/badges/mit.png" alt="MIT License" height="53" />](LICENSE)

<br/>

[![Download for macOS](https://img.shields.io/github/v/release/doppl-tech/releases?sort=semver&display_name=tag&label=Download%20for%20macOS&color=E4B429&labelColor=000000&logo=apple&logoColor=FDD54F&style=for-the-badge)](https://github.com/doppl-tech/releases/releases/latest)

<br/>

![macOS 13+](https://img.shields.io/badge/macOS-13%2B-000000?logo=apple&logoColor=white)
![Notarized](https://img.shields.io/badge/Apple-Notarized-555555?logo=apple&logoColor=white)
![SwiftUI](https://img.shields.io/badge/App-SwiftUI-F05138?logo=swift&logoColor=white)
![FastAPI](https://img.shields.io/badge/Engine-FastAPI-009688?logo=fastapi&logoColor=white)
![SQLite + sqlite-vec](https://img.shields.io/badge/Storage-SQLite%20%2B%20sqlite--vec-003B57?logo=sqlite&logoColor=white)
![MCP](https://img.shields.io/badge/Protocol-MCP-6E56CF?logo=modelcontextprotocol&logoColor=white)
![Works with Claude](https://img.shields.io/badge/Works%20with-Claude-D97757?logo=claude&logoColor=white)
![Local-first](https://img.shields.io/badge/Local--first-100%25%20on%20your%20Mac-2E7D32?logo=ghostery&logoColor=white)

</div>

---

## Why Cortex

Every assistant forgets the work you already did. You re-explain your project, your preferences, your
decisions — every session, to every tool. Cortex is the layer that remembers **once** and serves that
memory **everywhere**, without shipping your life to someone else's server.

- 🔒 **Local-first, by default.** Your memory is plain files on your Mac at
  `~/Library/Application Support/Cortex/Cortex.vault/`. It works with the network off. No account needed.
- 🧠 **Reviewed, cited memory.** Nothing is "remembered" until you approve it. Every answer cites the
  source it came from — no hallucinated recall.
- 🔌 **Works where you already work.** One click wires Cortex into Claude Desktop, Cursor, Windsurf,
  Zed, and any MCP client. Bring your history in from ChatGPT, Claude, Perplexity, and Notion.
- 🎛️ **You hold the controls.** Per-tool permissions for read, save, export, and repair. Redaction on
  by default. Revoke anything, anytime.

## The loop

<div align="center">

| ① Connect | ② Review | ③ Ask | ④ Control |
|:--:|:--:|:--:|:--:|
| Bring in notes, an AI-chat export, or sign in and import your history | Approve what's useful, archive the noise — memory stays trustworthy | Ask with cited answers, or let a connected AI tool retrieve what you approved | Keep reads, saves, exports, and every connection visible and revocable |

</div>

## How it works

```mermaid
flowchart LR
    subgraph SOURCES["Your sources"]
      A1["Local notes / Obsidian"]
      A2["ChatGPT · Claude · Perplexity · Notion"]
      A3["Files & exports"]
    end
    subgraph CORTEX["Cortex — on your Mac"]
      B1["Review inbox<br/>(you approve)"]
      B2["Layered, cited memory<br/>SQLite + sqlite-vec"]
      B3["Context Assembly Engine<br/>(CMP)"]
    end
    subgraph TOOLS["Your AI tools"]
      C1["Claude Desktop · Cursor<br/>Windsurf · Zed · any MCP client"]
    end
    SOURCES --> B1 --> B2 --> B3 --> C1
    C1 -. "cited retrieval" .-> B3
```

A native **SwiftUI** app bundles a local **FastAPI** engine on `127.0.0.1:8766`. Ingested sources become
typed, layered memory in a **SQLite** store (full-text + `sqlite-vec` vectors) that mirrors to a
human-readable, Obsidian-style vault you own. When a tool asks, the **Contextual Memory Protocol** packs
the smallest cited, model-calibrated context that answers the task.

## What's inside

- **Contextual Memory Protocol (CMP)** — model-aware context packing (SMP envelope + per-session working
  set) that gives an agent exactly what it needs, cited, and nothing it doesn't. See [`docs/CMP_PROTOCOL.md`](docs/CMP_PROTOCOL.md).
- **A vault you own** — plain Markdown + a rebuildable index, portable like an Obsidian vault. See
  [`docs/LOCAL_VAULT_FORMAT.md`](docs/LOCAL_VAULT_FORMAT.md).
- **Retrieval that cites or abstains** — hybrid BM25 + vector + temporal ranking, reranking, and a
  cite-or-abstain gate so answers are grounded, never guessed.
- **One-click connections** — write-and-relaunch MCP config for desktop tools, session-import for the
  web chat apps, drag-and-drop for exports. See [`docs/MCP_INTEGRATIONS.md`](docs/MCP_INTEGRATIONS.md).
- **Trust controls** — scoped, revocable per-tool permissions with redaction. See [`docs/TRUST_CONTROLS.md`](docs/TRUST_CONTROLS.md).
- **Optional cloud sync** — an end-to-end-encryption design for multi-device sync, opt-in and
  account-based. Data stays local unless you turn it on. See [`docs/ACCOUNTS_ENCRYPTION_DESIGN.md`](docs/ACCOUNTS_ENCRYPTION_DESIGN.md).

## Install

1. **[Download the latest DMG →](https://github.com/doppl-tech/releases/releases/latest)** (macOS 13 or later).
2. Open the DMG and drag **Cortex** into Applications.
3. Launch it. It's Developer ID signed and **notarized by Apple**, so it opens with no warning.
4. Point Cortex at a notes folder or import your AI chats, review your first memories, then connect a tool.

Cortex checks for updates on its own, so once you're on a recent build, new releases arrive automatically.

## Build from source

```bash
# macOS app (SwiftUI)
./macos/build.sh

# Backend engine + test suite (Python 3.11+)
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/runtime-requirements.txt
python3 -m pytest backend/tests

# Retrieval-quality gate (deterministic, offline)
python3 scripts/retrieval_eval.py
```

See [`SETUP.md`](SETUP.md) for the full development setup and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
for how the pieces fit together.

## Documentation

| Area | Doc |
|---|---|
| System architecture | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Contextual Memory Protocol | [`docs/CMP_PROTOCOL.md`](docs/CMP_PROTOCOL.md) · [`docs/PORTABLE_MEMORY_PROTOCOL_V2.md`](docs/PORTABLE_MEMORY_PROTOCOL_V2.md) |
| The vault format you own | [`docs/LOCAL_VAULT_FORMAT.md`](docs/LOCAL_VAULT_FORMAT.md) |
| Connecting AI tools (MCP) | [`docs/MCP_INTEGRATIONS.md`](docs/MCP_INTEGRATIONS.md) |
| Importing your sources | [`docs/SOURCE_IMPORTS.md`](docs/SOURCE_IMPORTS.md) |
| Trust & privacy controls | [`docs/TRUST_CONTROLS.md`](docs/TRUST_CONTROLS.md) |
| Encryption & sync design | [`docs/ACCOUNTS_ENCRYPTION_DESIGN.md`](docs/ACCOUNTS_ENCRYPTION_DESIGN.md) · [`docs/CXE1_WIRE_FORMAT.md`](docs/CXE1_WIRE_FORMAT.md) |
| Install & auto-updates | [`docs/INSTALLER_AND_UPDATES.md`](docs/INSTALLER_AND_UPDATES.md) |

## Privacy

Cortex is local-first: the default experience needs no account and no cloud. Cortex reads a source only
after you connect it, records nothing ambient (no screen, no microphone), and shares context with an AI
tool only within the scoped permission you grant. The optional Cortex Cloud tier (for multi-device sync)
is described honestly in the app and on the site. Questions: **support@trydoppl.com**.

## Affiliations & sponsor

<table>
  <tr>
    <td align="center" width="33%">
      <a href="https://uwaterloo.ca"><img src=".github/badges/waterloo.png" alt="University of Waterloo" height="53" /></a><br/>
      <sub>Built at the <b>University of Waterloo</b><br/>Faculty of Engineering</sub>
    </td>
    <td align="center" width="33%">
      <a href="https://uwaterloo.ca/research"><img src="https://img.shields.io/badge/Academic-Research-B5179E?style=for-the-badge&labelColor=000000" alt="Research" /></a><br/>
      <sub>An applied <b>research</b> project on<br/>local-first AI memory</sub>
    </td>
    <td align="center" width="33%">
      <a href="https://composio.dev"><img src="https://github.com/ComposioHQ.png" width="44" height="44" alt="Composio" /></a><br/>
      <sub>Proudly sponsored by<br/><a href="https://composio.dev"><b>Composio</b></a></sub>
    </td>
  </tr>
</table>

## License

Cortex is released under the **[MIT License](LICENSE)** — free to use, modify, and build on.
© 2026 Doppl.

<div align="center">
<br/>
<sub>Built for people who want their AI to remember — without giving up their data.</sub>
</div>
