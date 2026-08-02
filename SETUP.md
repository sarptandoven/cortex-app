# Cortex Setup Guide

Cortex is a local-first macOS beta. You do not need Redis, Docker, or an
external vector database. The current direct-download build requires account
sign-in; retrieval and the user-owned vault remain local.

## Choose a setup path

### Evaluate the engine

This is the safest first source run: it uses synthetic data and deletes its
temporary vault.

```bash
make doctor
make demo
```

### Use the desktop app

Download the current notarized DMG from the
[release page](https://github.com/doppl-tech/releases/releases/latest). Building
from source is a contributor workflow, not the shortest installation path.

### Develop the backend or SDKs

```bash
make setup
make run
```

Open `http://127.0.0.1:8766/docs` for the interactive API.

## Requirements

- Backend/demo: Python 3.12 on macOS or Linux
- TypeScript SDK: Node.js 18+; Obsidian/OpenClaw plugins: Node.js 22
- Desktop app: Apple Silicon Mac running macOS 13+ and Xcode command line tools

## Build the macOS UI from source

```bash
./macos/build.sh
open macos/build/Cortex.app
```

The default source build is an ad-hoc-signed UI development bundle without the
release Python runtime or embedding model. It is useful for SwiftUI work but is
not a full replacement for the downloadable app. See
[docs/APPLE_RELEASE.md](docs/APPLE_RELEASE.md) for a release-like bundle.

The release package starts its bundled local service on `127.0.0.1:8766` and
stores user-owned memory files in:

```text
~/Library/Application Support/Cortex/Cortex.vault/
```

## First Run

Use the setup flow:

1. Memory folder: confirm the local memory folder path and service health.
2. Connect/sync: connect Obsidian or a local notes folder as the first memory source, then connect AI tools through MCP when ready.
3. Review: approve useful memory after a source syncs.
4. Ask: ask a question and inspect cited memory after approval.

`Finish Later` opens the app without marking setup complete. Setup can be reopened from Connections & Privacy.

## Connect Sources And AI Tools

Open Connections & Privacy and connect Obsidian/local notes or another supported source plus supported local AI tools through MCP.

Cloud OAuth/API sync is staged by service readiness in this beta and does not appear as a primary setup path until direct sync is ready.

## Review And Ask

- `Review` is where pending memories are approved or archived.
- `Ask` searches approved memory and returns cited answers.
- Connected AI tools can retrieve approved memory through MCP. Ask remains the in-app way to test cited answers.

## Connections & Privacy And Backup

Open Connections & Privacy to manage:

- privacy posture and AI access permissions
- local memory folder backup
- connected AI tools
- source/audit trail
- advanced diagnostics and setup reset

`make setup` refuses to continue with the system Python when it is not 3.12,
creates `.venv`, and installs the exact backend/test versions in
`requirements-dev.lock`, verifying every downloaded artifact hash. To use an
explicit interpreter:

```bash
make setup PYTHON=/opt/homebrew/bin/python3.12
```

Run the fast local pre-PR set:

```bash
make check
```

Run the broader verification set:

```bash
make test
make connector-check
./macos/build.sh
codesign --verify --deep --strict --verbose=2 macos/build/Cortex.app
.venv/bin/python scripts/ops_readiness_check.py
```
