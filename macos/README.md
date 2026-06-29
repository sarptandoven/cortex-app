# Cortex macOS App

Native desktop app for Cortex.

The app bundles the dependency-light local backend and starts it automatically on launch. Users do not need to manually run `uvicorn` for the packaged app.

## Features

- Opens as a normal macOS window from the Dock/app icon
- Menu bar item opens the same window for quick access
- Five-step first-run setup for private vault, first source import, first memory review, first Ask/use action, and backup/trust decision
- Optional global `Cmd+Shift+V` capture, disabled by default
- Model screen for readiness, memory coverage, active source health, and model signal quality
- Sources screen for imports, preview, duplicate-safe history, undo import, and source health
- Review screen for approvals, archives, recommended actions, open loops, and decisions
- Ask screen for cited memory search and focused/model context packs
- Trust screen for privacy, AI access, backup, connected tools, audit, and advanced diagnostics
- Memory behavior settings for review flow, pending-context visibility, and context-pack size
- Save clipboard text
- Quick note capture
- Review pending captures from the Review screen
- Approve/archive capture lifecycle
- Recent memories
- Search
- Advanced graph, health, stats, exports, updates, and backend settings behind Trust diagnostics
- Authenticated markdown/JSON export
- Health diagnostics
- Local vault diagnostics
- Full local vault backup
- Reliability report and backup-first storage repair
- Copy/save sanitized support bundle
- Search index rebuild
- Installer/update feed controls
- Backend endpoint and token settings
- Reopenable setup flow under Trust diagnostics
- Separate Keychain tokens for app REST access and scoped MCP integrations

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

Default app token:

```text
Generated per install, stored in Keychain, and used by the macOS app for REST calls.
```

Default MCP token:

```text
Generated per install, stored in Keychain, registered with the local backend, and copied into MCP configs instead of the app token.
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

The Trust diagnostics section includes an "Installer and updates" section that can check a local or hosted `latest.json` feed.

The Trust diagnostics section also includes Reliability controls for health checks, storage repair, and sanitized support bundle export.

Before sharing a build, run:

```bash
python3 ../scripts/ops_readiness_check.py --refresh-site
```

## Permissions

The app reads the clipboard only when the user clicks a capture button or enables and uses the optional global hotkey. It does not record the screen or listen in the background.
