# Cortex Operational Readiness

This document defines how Cortex should be operated for the local-first macOS beta before a hosted backend exists.

## Operating Model

Cortex beta operations are local-first:

- user data lives in the local vault
- the SQLite index is rebuildable
- releases are packaged as DMG and ZIP artifacts
- the update feed is a static `latest.json`
- support triage starts from a sanitized support bundle
- public incident response is manual until hosted accounts and telemetry exist

The goal is not to pretend we have production cloud operations. The goal is to make the local beta repeatable, supportable, and recoverable.

## Operational Surfaces

Backend endpoints:

- `GET /health`: backend version, health contract, features, vault path
- `GET /ready`: readiness gate for SQLite and vault health
- `GET /v1/diagnostics`: raw local storage diagnostics
- `GET /v1/reliability/report`: user-readable health checks and recovery actions
- `GET /v1/support/bundle`: sanitized support payload for support triage
- `POST /v1/backups`: backup-first recovery action
- `POST /v1/backups/restore-latest`: restore readable vault records from the latest backup archive and rebuild the local index
- `DELETE /v1/backups`: remove local backup archives after hard deletion or retention changes
- `DELETE /v1/user-data`: remove the current user's local vault/index data; includes backups by default
- `POST /v1/maintenance/repair-storage`: backup, clean derived rows, rebuild search
- `POST /v1/maintenance/rebuild-search`: rebuild full-text and vector index rows
- `POST /v1/maintenance/rebuild-index-from-vault`: restore SQLite index from vault files

MCP tools:

- `get_memory_diagnostics`
- `get_reliability_report`
- `get_support_bundle`
- `create_memory_backup`
- `repair_memory_storage`
- `rebuild_memory_search`
- `rebuild_index_from_vault`
- `get_audit_log`

Operator scripts:

- `python3 scripts/reliability_check.py`
- `python3 scripts/backend_beta_smoke.py`
- `python3 scripts/battle_test_http.py`
- `python3 scripts/export_support_bundle.py`
- `python3 scripts/ops_readiness_check.py`
- `python3 scripts/check_distribution_site.py`
- `python3 scripts/validate_update_manifest.py`
- `python3 scripts/check_docs_current.py`

## Ship Gate

Run before sharing a beta build:

```bash
python3 scripts/backend_beta_smoke.py
python3 scripts/ops_readiness_check.py --refresh-site
```

For a full package refresh:

```bash
python3 scripts/ops_readiness_check.py --refresh-site --include-package
```

For a live installed app:

```bash
python3 scripts/ops_readiness_check.py --require-live --base-url http://127.0.0.1:8766 --token "$CORTEX_API_KEY"
```

The local readiness gate checks:

- Python syntax without creating bytecode as the syntax step
- required operator docs
- beta docs and committed direct-release manifest currency
- backend beta smoke on temp data with network sockets blocked
- backend unit tests
- macOS local build
- DMG/ZIP packaging when `--include-package` is set
- static distribution links and artifact hashes
- site update feed
- latest packaged release update feed when package output exists, or when package/site refresh is requested
- offline support-bundle generation
- optional live backend health, reliability, and support-bundle contracts

## Support Bundle

Generate from a running app:

```bash
python3 scripts/export_support_bundle.py --mode live
```

Generate without the app running:

```bash
python3 scripts/export_support_bundle.py --mode offline
```

The support bundle intentionally omits:

- raw capture text
- memory content
- task content
- entity bodies
- context packs
- exported user files
- raw MCP query values

It includes:

- backend version and health contract
- feature flags
- Python, platform, and SQLite version
- storage health
- reliability checks
- trust settings and risk flags
- record counts
- backup age
- safe recent event metadata
- recommended recovery actions

Users should still review the JSON before sending it to support.

## Severity Levels

### SEV 0: Possible Data Loss

Examples:

- vault files missing
- backups cannot be created
- SQLite quick check fails
- rebuild from vault fails

Immediate response:

1. Tell the user to stop using the app.
2. Ask them not to delete the vault folder.
3. Generate a support bundle if possible.
4. Copy the entire vault folder manually before repair.
5. Try `POST /v1/backups`.
6. If backup succeeds, try `POST /v1/maintenance/rebuild-index-from-vault`.
7. If SQLite is corrupted but vault JSON exists, rebuild the index from vault.

Do not ask the user to reinstall until the vault is backed up.

### SEV 1: App Cannot Launch

Examples:

- app opens but backend does not start
- port collision on `127.0.0.1:8766`
- macOS blocks local beta opening

Immediate response:

1. Confirm macOS version is 13 or newer.
2. Ask whether the app was dragged into Applications.
3. Ask the user to Control-click and Open for the local beta.
4. Check whether another process is using port `8766`.
5. Generate an offline support bundle.
6. If a new release exists, ask the user to replace the app, not the vault.

### SEV 2: Search, MCP, or Context Packs Are Wrong

Examples:

- search misses known approved memory
- context pack contains stale content
- MCP tool returns unexpected empty results

Immediate response:

1. Check `GET /v1/reliability/report`.
2. Create a backup.
3. Run `POST /v1/maintenance/rebuild-search`.
4. If the index still looks wrong, run `POST /v1/maintenance/rebuild-index-from-vault`.
5. Ask the user whether pending captures are hidden by trust settings.

### SEV 3: Beta UX or Distribution Issue

Examples:

- download link is stale
- update feed does not match DMG
- landing page mobile layout breaks
- install copy confuses users

Immediate response:

1. Run `python3 scripts/check_distribution_site.py`.
2. Run `python3 scripts/validate_update_manifest.py site/downloads/latest.json`.
3. Re-run `python3 scripts/prepare_distribution_site.py`.
4. Re-test the static site locally.
5. Replace hosted files atomically: manifest after artifacts.

## Rollback

Local beta rollback is manual:

1. Keep the user vault folder unchanged.
2. Download the previous DMG.
3. Quit Cortex.
4. Replace `Cortex.app` in Applications.
5. Reopen Cortex.
6. Run the reliability report.
7. Create a fresh backup.

The release site must retain at least one previous stable DMG and ZIP during beta testing.

## Update Feed Safety

When replacing a hosted release:

1. Upload DMG and ZIP first.
2. Upload checksums.
3. Validate public artifact URLs.
4. Upload `latest.json` last.
5. Download from the landing page and verify SHA-256.

Never publish `latest.json` before artifacts are available.

## Privacy And Support Boundaries

Support must not ask users for:

- full vault zips
- Markdown exports
- raw captures
- context packs
- screenshots containing memory content

Ask for the support bundle first. Escalate to full vault sharing only if the user explicitly consents and the issue is a true data-recovery case.

## No-Analytics Beta Metrics

Until analytics exists, track launch readiness manually:

- number of users invited
- number of users who installed successfully
- number of users who saved one memory
- number of users who copied one context pack
- number of support bundles received
- number of backup/rebuild incidents
- number of users who returned the next day

Do not add silent analytics to the local beta without a user-visible privacy decision.

## Public-Scale Blockers

Before claiming readiness for millions of users:

- Developer ID signing and notarization
- automatic update mechanism or Sparkle appcast
- hosted crash/error reporting decision
- support email and ticket workflow
- public privacy policy and terms
- release rollback archive
- clean first-run logs and user-facing error states
- reproducible CI packaging on a clean macOS runner
- hosted backend auth and observability for non-local accounts
- incident owner and escalation policy
