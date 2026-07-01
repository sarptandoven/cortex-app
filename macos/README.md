# Cortex macOS App

Native desktop app for Cortex.

The app bundles the dependency-light local backend and starts it automatically on launch. Users do not need to run `uvicorn` themselves for the packaged app.

## Features

- Opens as a normal macOS window from the Dock/app icon
- Menu bar item opens the same window for quick access
- Simple first-run setup for private memory folder, connection, review, and first Ask
- Optional global `Cmd+Shift+V` save-from-clipboard action, disabled by default
- Home screen for connection state, memory readiness, and the next action
- Connections & Privacy sheet for MCP tools, Obsidian/local notes, sync health, privacy, backup, support, and diagnostics
- Review screen for approving or archiving candidate memory
- Ask screen for natural-language memory search with source citations
- AI access settings for read, save, export, maintenance, and destructive permissions
- Advanced fallback context-copy and import tools for migration, support, and unsupported sources
- Approve/archive memory candidate lifecycle
- Search
- Advanced graph, health, stats, exports, updates, and backend settings behind Connections & Privacy diagnostics
- Authenticated markdown/JSON export
- Health diagnostics
- Local memory folder diagnostics
- Full local memory folder backup
- Reliability report and backup-first storage repair
- Copy/save sanitized support bundle
- Search index rebuild
- Installer/update feed controls
- Backend endpoint and token settings behind Advanced diagnostics
- Reopenable setup flow
- Separate local tokens for app REST access and scoped MCP integrations

## Build

This project is intentionally buildable with Apple Command Line Tools; full Xcode is not required.

```bash
cd macos
./build.sh
open build/Cortex.app
```

The packaged app starts its own local backend. `../scripts/dev_backend.sh` is only needed for backend development.

On launch, the app verifies the local backend health contract, required feature flags, and active memory folder path before reusing an existing backend process.

Default backend:

```text
http://127.0.0.1:8766
```

Default app token:

```text
Generated per install, stored in local app defaults, and used by the macOS app for REST calls.
```

Default MCP token:

```text
Generated per install, stored in local app defaults, registered with the local backend, and copied into MCP configs instead of the app token.
```

Default local memory folder:

```text
~/Library/Application Support/Cortex/Cortex.vault/
```

The memory folder contains user-readable JSON records plus `index.sqlite`, which is a rebuildable local search index.

The packaged app also bundles `scripts/cortex_mcp_stdio.py` so setup can copy a ready local MCP config.

## Package a Release

```bash
./macos/package_release.sh
```

This creates a DMG, ZIP, checksums, and `latest.json` update manifest under `../outputs/Cortex-<version>-<build>/`.
It also writes `BETA_HANDOFF.md` beside the artifacts with tester-facing build,
install, checksum, readiness, live-backend, and first-user verification
steps.

The Connections & Privacy diagnostics section includes an "Installer and updates" section that can check a local or hosted `latest.json` feed.

The Connections & Privacy diagnostics section also includes Reliability controls for health checks, storage repair, and sanitized support bundle export.

Before sharing a build, run:

```bash
python3 ../scripts/ops_readiness_check.py --refresh-site
```

## Permissions

The app reads the clipboard only when the user clicks a save-from-clipboard action or enables and uses the optional global hotkey. It does not record the screen or listen in the background.
