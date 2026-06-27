# Cortex macOS App

Native desktop app for Cortex.

The app bundles the dependency-light local backend and starts it automatically on launch. Users do not need to manually run `uvicorn` for the packaged app.

## Features

- Opens as a normal macOS window from the Dock/app icon
- Menu bar item opens the same window for quick access
- First-run setup for vault location, memory rules, first save, and AI-tool connection
- Global `Cmd+Shift+V` capture
- Today cockpit with recommended actions, open loops, and one-click context packs
- Simple Today loop for capture, review, reuse, and return
- Memory behavior settings for review flow, pending-context visibility, and context-pack size
- Save clipboard text
- Quick note capture
- Review pending captures from Today
- Approve/archive capture lifecycle
- Recent memories
- Search
- Advanced graph, health, stats, exports, and backend settings
- Authenticated markdown/JSON export
- Health diagnostics
- Local vault diagnostics
- Full local vault backup
- Reliability report and backup-first storage repair
- Copy/save sanitized support bundle
- Search index rebuild
- Installer/update feed controls
- Backend endpoint and token settings
- Reopenable setup flow under More

## Build

This project is intentionally buildable with Apple Command Line Tools; full Xcode is not required.

```bash
cd macos
./build.sh
open build/Cortex.app
```

The packaged app starts its own local backend. `../scripts/dev_backend.sh` is only needed for backend development.

On launch, the app verifies the local backend health contract, required feature flags, and active vault path before reusing an existing backend process.

Default backend:

```text
http://127.0.0.1:8766
```

Default token:

```text
dev-local-key
```

Default local vault:

```text
~/Library/Application Support/Cortex/Cortex.vault/
```

The vault contains user-readable JSON records plus `index.sqlite`, which is a rebuildable local search index.

The packaged app also bundles `scripts/cortex_mcp_stdio.py` so setup can copy a ready local MCP config.

## Package a Release

```bash
./macos/package_release.sh
```

This creates a DMG, ZIP, checksums, and `latest.json` update manifest under `../outputs/Cortex-<version>-<build>/`.

The More tab includes an "Installer and updates" section that can check a local or hosted `latest.json` feed.

The More tab also includes a Reliability section for health checks, storage repair, and sanitized support bundle export.

Before sharing a build, run:

```bash
python3 ../scripts/ops_readiness_check.py --refresh-site
```

## Permissions

The app reads the clipboard only when the user presses the hotkey or clicks a capture button. It does not record the screen or listen in the background.
