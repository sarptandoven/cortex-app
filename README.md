# Cortex

Your private personal memory model for AI tools.

Cortex is a local-first personal memory and adaptation layer. The current beta connects Obsidian/local notes and local MCP AI tools, turns reviewed source records into cited memory, and lets approved tools retrieve that memory instead of guessing or starting from zero.

## What Exists Now

- Native macOS app that opens as a normal window from the Dock
- Bundled local service on `127.0.0.1:8766`
- User-owned memory folder at `~/Library/Application Support/Cortex/Cortex.vault/`
- SQLite FTS search with optional `sqlite-vec` and opt-in OpenAI embeddings
- Connected-source setup for Obsidian/local notes and local MCP AI tools; other services stay planned or Advanced/Fallback until their direct connectors ship
- Direct local app connection helpers for supported AI tools, with Advanced/Fallback import available only when a service cannot connect directly yet
- Simple product flow: Home, Review, Ask, with Connections & Privacy kept behind one sheet
- Source readiness report covering the active beta source path, planned live connectors, connected accounts, review backlog, source errors, active memory, and citation coverage
- Lightweight first-run setup for the private memory folder, with source connection, Review, and Ask as guided next steps
- Review inbox for approve/archive, decisions, recommended actions, and follow-ups
- Ask screen with cited memory search plus secondary handoffs for ChatGPT, Claude, Cursor, direct local tools, and browser-only workflows
- Advanced local connection bridge and one-click setup helpers
- Trust controls for connected AI tool reads, saves, exports, redaction, and repair actions
- Reliability report, backup-first repair, and local memory folder backups
- Sanitized support bundle for local beta triage
- Operational readiness gate for tests, build, distribution, manifests, and support checks
- Repeatable DMG/ZIP packaging and static landing page distribution

## Build The App

```bash
./macos/build.sh
open macos/build/Cortex.app
```

The packaged app starts the local service automatically. Local service development can use:

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

Each packaged release includes `BETA_HANDOFF.md` with the tester build, install,
checksum, readiness, live-backend, and hands-on first-user verification steps.

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
python3 scripts/retrieval_eval.py
python3 scripts/backend_beta_smoke.py
python3 scripts/ops_readiness_check.py
python3 scripts/reliability_check.py --base-url http://127.0.0.1:8766 --token "$CORTEX_API_KEY"
python3 scripts/battle_test_http.py --base-url http://127.0.0.1:8766 --token "$CORTEX_API_KEY"
codesign --verify --deep --strict --verbose=2 macos/build/Cortex.app
```

Export a sanitized support bundle:

```bash
python3 scripts/export_support_bundle.py --mode live --token "$CORTEX_API_KEY"
python3 scripts/export_support_bundle.py --mode offline
```

## Key Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Memory Backend Blueprint](docs/MEMORY_BACKEND_BLUEPRINT.md)
- [Simple Product Loop](docs/SIMPLE_PRODUCT_LOOP.md)
- [Capture Surfaces](docs/CAPTURE_SURFACES.md)
- [Advanced/Fallback Source Intake](docs/SOURCE_IMPORTS.md)
- [First 100 Beta Operator Quickstart](docs/BETA_OPERATOR_QUICKSTART.md)
- [First 100 User Demo Checkpoint](docs/checkpoints/first-100-demo.md)
- [First 100 User Support and Privacy Runbook](docs/BETA_SUPPORT.md)
- [Trust Controls](docs/TRUST_CONTROLS.md)
- [Reliability Hardening](docs/RELIABILITY_HARDENING.md)
- [Operational Readiness](docs/OPERATIONAL_READINESS.md)
- [Installer and Updates](docs/INSTALLER_AND_UPDATES.md)
- [Apple Developer Release](docs/APPLE_RELEASE.md)
- [Landing Page and Distribution](docs/DISTRIBUTION.md)
- [Production Readiness](docs/PRODUCTION_READINESS.md)

## Current Boundaries

Cortex is ready for local-first beta testing, not broad public distribution yet. The current beta centers on Obsidian/local notes sync, direct local AI-tool integrations, Review, Ask, and Trust. Selected export, folder, or file import remains available only as an Advanced/Fallback path for unsupported services or recovery. Public launch still needs Developer ID signing, notarization, hosted HTTPS downloads, a formal support path, a hosted update-feed decision, and a production privacy review.
