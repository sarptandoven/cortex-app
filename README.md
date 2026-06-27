# Cortex

You, but AI.

Cortex is a local-first memory layer for AI agents. It helps ChatGPT, Claude, Cursor, and MCP-compatible tools reuse your preferences, decisions, project context, people, and open loops instead of starting from zero every time.

## What Exists Now

- Native macOS app that opens as a normal window from the Dock
- Bundled local backend on `127.0.0.1:8766`
- User-owned local vault at `~/Library/Application Support/Cortex/Cortex.vault/`
- SQLite FTS search with optional `sqlite-vec`
- Capture surfaces for clipboard, quick notes, files, browser capture, and drop-folder import
- Today loop: Capture, Review, Reuse, Return
- Review inbox for approve/archive
- Copy-ready context packs for ChatGPT, Claude, Cursor, and other assistants
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
python3 scripts/reliability_check.py --base-url http://127.0.0.1:8766 --token dev-local-key
python3 scripts/battle_test_http.py --base-url http://127.0.0.1:8766 --token dev-local-key
codesign --verify --deep --strict --verbose=2 macos/build/Cortex.app
```

Export a sanitized support bundle:

```bash
python3 scripts/export_support_bundle.py --mode live
python3 scripts/export_support_bundle.py --mode offline
```

## Key Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Simple Product Loop](docs/SIMPLE_PRODUCT_LOOP.md)
- [Capture Surfaces](docs/CAPTURE_SURFACES.md)
- [Trust Controls](docs/TRUST_CONTROLS.md)
- [Reliability Hardening](docs/RELIABILITY_HARDENING.md)
- [Operational Readiness](docs/OPERATIONAL_READINESS.md)
- [Installer and Updates](docs/INSTALLER_AND_UPDATES.md)
- [Apple Developer Release](docs/APPLE_RELEASE.md)
- [Landing Page and Distribution](docs/DISTRIBUTION.md)
- [Production Readiness](docs/PRODUCTION_READINESS.md)

## Current Boundaries

Cortex is ready for local-first beta testing, not broad public distribution yet. Public launch still needs Developer ID signing, notarization, hosted HTTPS downloads, a formal support path, a hosted update-feed decision, and a production privacy review.
