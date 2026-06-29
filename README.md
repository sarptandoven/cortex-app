# Cortex

Your private personal memory model for AI tools.

Cortex is a local-first personal memory and adaptation layer. It turns approved chats, notes, files, writing samples, decisions, people, projects, and open loops into cited memory that AI tools can retrieve instead of guessing or starting from zero.

## What Exists Now

- Native macOS app that opens as a normal window from the Dock
- Bundled local backend on `127.0.0.1:8766`
- User-owned local vault at `~/Library/Application Support/Cortex/Cortex.vault/`
- SQLite FTS search with optional `sqlite-vec` and opt-in OpenAI embeddings
- Capture surfaces for clipboard, quick notes, files, browser capture, and drop-folder import
- Stage 1 source import with preview, duplicate-safe history, and batch undo for user-selected exports, folders, and files from ChatGPT, Claude, Notion, Gmail/email, Slack, Discord, Telegram, Google Keep, Messages, WhatsApp, bookmarks, calendars, contacts, LinkedIn, Twitter/X, docs, notes, and work-tool exports
- Five-screen product flow: Model, Sources, Review, Ask, Trust
- Gated first-run setup for private vault, first source import, first memory review, first Ask/use action, and backup/trust decision
- Review inbox for approve/archive, decisions, recommended actions, and open loops
- Ask screen with cited memory search plus focused context packs for ChatGPT, Claude, Cursor, MCP tools, and browser handoffs
- Local MCP bridge and one-click config helpers
- Trust controls for agent reads, writes, exports, redaction, and maintenance
- Reliability report, backup-first repair, and local vault backups
- Sanitized support bundle for local beta triage
- Operational readiness gate for tests, build, distribution, manifests, and support checks
- Repeatable DMG/ZIP packaging and static landing page distribution

## Build The App

```bash
./macos/build.sh
open macos/build/Cortex.app
```

The packaged app starts the local backend automatically. Backend development can use:

```bash
./scripts/dev_backend.sh
```

## Package A Release

```bash
./macos/package_release.sh
python3 scripts/prepare_distribution_site.py
```

Release artifacts are written to:

```text
../outputs/Cortex-<version>-<build>/
```

The static landing page lives in:

```text
site/
```

Run it locally:

```bash
cd site
python3 -m http.server 8780
```

## Verify

```bash
python3 -m unittest discover backend/tests
python3 scripts/ops_readiness_check.py --refresh-site
python3 scripts/reliability_check.py --base-url http://127.0.0.1:8766 --token "$CORTEX_API_KEY"
python3 scripts/battle_test_http.py --base-url http://127.0.0.1:8766 --token "$CORTEX_API_KEY"
codesign --verify --deep --strict --verbose=2 macos/build/Cortex.app
```

Export a sanitized support bundle:

```bash
python3 scripts/export_support_bundle.py --mode live
python3 scripts/export_support_bundle.py --mode offline
```

## Key Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Memory Backend Blueprint](docs/MEMORY_BACKEND_BLUEPRINT.md)
- [Simple Product Loop](docs/SIMPLE_PRODUCT_LOOP.md)
- [Capture Surfaces](docs/CAPTURE_SURFACES.md)
- [Source Imports](docs/SOURCE_IMPORTS.md)
- [Trust Controls](docs/TRUST_CONTROLS.md)
- [Reliability Hardening](docs/RELIABILITY_HARDENING.md)
- [Operational Readiness](docs/OPERATIONAL_READINESS.md)
- [Installer and Updates](docs/INSTALLER_AND_UPDATES.md)
- [Apple Developer Release](docs/APPLE_RELEASE.md)
- [Landing Page and Distribution](docs/DISTRIBUTION.md)
- [Production Readiness](docs/PRODUCTION_READINESS.md)

## Current Boundaries

Cortex is ready for local-first beta testing, not broad public distribution yet. Public launch still needs Developer ID signing, notarization, hosted HTTPS downloads, a formal support path, a hosted update-feed decision, and a production privacy review.
