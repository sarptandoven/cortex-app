# First 100 User Demo Checkpoint

This checkpoint defines the practical demo bar for inviting the first 100 local-first Cortex users. It is not a broad launch checklist. The goal is to prove that a nontechnical macOS user can connect MCP AI tools or an Obsidian/local notes vault, review useful signals into cited personal memory, ask with citations, and understand the trust boundaries.

For the short operator checklist, start with [First 100 Beta Operator Quickstart](../BETA_OPERATOR_QUICKSTART.md).

## Demo Story

The demo should follow the real product loop:

```text
Home -> Review -> Ask -> Connections & Privacy
```

By the end of the run, the tester should have:

- chosen or confirmed a local memory folder;
- connected MCP AI tools or an Obsidian/local notes vault;
- approved at least one useful memory candidate;
- asked Cortex a question and received cited results;
- checked privacy, backup, export, and recovery controls.
- installed from the packaged DMG and understood the beta update and rollback path.

## Current Readiness Snapshot - 2026-06-30

Implemented in the repo now:

- Home, Review, Ask, and Connections & Privacy are the intended local-beta product shell.
- MCP AI-tool setup and local source-account sync are implemented through the backend/MCP contract.
- Obsidian/local notes sync is the first real native source path; other service cards must remain planned, connector-needed, or advanced/fallback until they sync actual records.
- Review-first memory, cited Ask, local backups, JSON/Markdown export, delete controls, support bundle generation, diagnostics, and packaging/update scripts exist.

Still required before inviting the first 100 users:

- run the clean macOS profile DMG install, first-run setup, MCP or Obsidian sync, Review, Ask, backup/export/support, update, and rollback walkthrough;
- run the full release and ops readiness gates against the generated release directory and checksum/update manifest artifacts;
- verify the support bundle is content-free and the user can explain where data lives and how to back it up;
- keep connector breadth honest in the UI and handoff docs.

Deferred to first-10k planning:

- hosted accounts, hosted MCP/OAuth, Postgres/pgvector, workers, cloud object storage, hosted deletion/export receipts, observability, billing/quotas, teams, and broad live OAuth/API connectors.

## Product Surfaces

### Home

Show that Cortex starts from a simple readiness view rather than an empty chat box or settings dashboard.

- connection state, memory readiness, source health, and next action are visible;
- there is one obvious next action;
- source readiness and citation coverage are product signals, not launch claims.

### Connections & Privacy

Connect real inputs and verify controls from the secondary sheet.

- connect local AI tools through MCP or connect an Obsidian/local notes vault;
- show status, permissions, sync health, and coverage before memory is used;
- confirm duplicate-safe source identity and sync history;
- keep planned Gmail, Notion, Slack, Drive, Calendar, GitHub, Mail, Messages, and browser connectors non-primary until they truly sync;
- keep Advanced/Fallback import available only for unsupported services, migrations, legal exports, and support recovery.

The first-demo inputs are MCP AI tools and Obsidian/local notes. Do not make manual export/file import or copy-memory-brief flows the success path.

### Review

Use Review as the trust boundary.

- pending source captures can be approved or archived;
- decisions, recommended actions, and follow-ups remain visible;
- noisy source captures can stay pending or be archived instead of becoming trusted memory;
- approved memory becomes available for the next Ask step.

### Ask

Ask a question that the connected source can answer.

- results include citations back to the reviewed source;
- cited memory is the primary success condition;
- connected AI tool retrieval is secondary after cited Ask results are clear;
- copy handoffs and context packs are Advanced/Fallback, not the primary Ask model;
- an empty or weak answer should lead back to connected-source coverage or Review, not to hidden automation.

### Privacy

End in Connections & Privacy so the user sees control before continued use.

- local memory folder path and service health are visible;
- review settings, pending-memory visibility, source policies, identity aliases, and connected AI tool permissions are understandable;
- connected AI tool read, save, export, maintenance, and destructive controls remain explicit;
- shared context redaction is enabled by default;
- backup, export, support bundle, and maintenance actions are discoverable without being part of the core loop.

## Onboarding Check

A fresh install should guide the tester through:

1. private memory folder confirmation;
2. first MCP or Obsidian connection;
3. first memory review;
4. first Ask with citations;
5. backup or explicit backup-later decision;
6. landing in Home, with Review, Ask, and Connections & Privacy available.

Quick memories, clipboard captures, web captures, and Advanced/Fallback import are useful secondary paths, but they should not replace the first-source connection gate for this demo.

## Backup And Export

Before ending the session, verify that the user can leave with their data.

- create a local memory folder backup;
- confirm backups are written under the memory folder backup area;
- verify JSON or Markdown export is available;
- confirm redaction applies to shared context and exports when enabled;
- confirm support bundle generation does not include raw memory content.

## Verification Commands

Run the app build:

```bash
./macos/build.sh
open macos/build/Cortex.app
```

Run backend tests and retrieval checks:

```bash
python3 -m unittest discover backend/tests
python3 scripts/retrieval_eval.py
python3 scripts/backend_beta_smoke.py
```

The beta smoke uses a temporary Obsidian vault and validates MCP tools, Review approval, cited Ask, backup, support bundle safety, queue health, and Trust gates without network sockets.

With the packaged app backend running on `127.0.0.1:8766`, run:

```bash
python3 scripts/first100_live_smoke.py
python3 scripts/reliability_check.py --base-url http://127.0.0.1:8766 --token "$CORTEX_API_KEY"
```

`first100_live_smoke.py` reads the app token from macOS defaults when `--token` is omitted, writes only under an isolated smoke user, and deletes that smoke user by default.

Run the operational ship gate before inviting testers:

```bash
python3 scripts/ops_readiness_check.py --refresh-site
```

For a packaged beta candidate, include packaging:

```bash
python3 scripts/ops_readiness_check.py --refresh-site --include-package
```

The release directory generated by `./macos/package_release.sh` also contains
`BETA_HANDOFF.md`. Use that file as the tester-facing handoff for build,
install, checksum, readiness, live-backend, support-bundle, and hands-on loop
verification.

Verify the generated release artifacts explicitly:

```bash
RELEASE_DIR="outputs/Cortex-0.1.0-1"
python3 scripts/validate_update_manifest.py "$RELEASE_DIR/latest.json"
python3 scripts/ops_readiness_check.py \
  --skip-tests \
  --skip-build \
  --require-package-artifacts \
  --release-dir "$RELEASE_DIR"
```

From the release directory, verify the checksum file:

```bash
shasum -a 256 -c "Cortex-0.1.0-1.checksums.txt"
```

Generate support bundles:

```bash
python3 scripts/export_support_bundle.py --mode live --token "$CORTEX_API_KEY"
python3 scripts/export_support_bundle.py --mode offline
```

## Installer And Rollback QA

The first-100 demo is not ready unless the packaged install path works without
source-code instructions:

- install from the DMG on a clean macOS 13 or newer user profile;
- launch from Applications and complete first-run setup;
- confirm the local memory folder is outside the app bundle;
- update over a previous beta by replacing `Cortex.app`;
- confirm the memory folder remains intact after update;
- roll back to the previous beta by replacing `Cortex.app`;
- confirm the memory folder remains intact after rollback;
- run the reliability report and create a backup after update and rollback;
- keep the previous DMG, ZIP, checksum file, and manifest available until the new package passes hands-on QA.

## Hands-On Acceptance

The demo is ready for the first 100 users when:

- generated DMG, ZIP, checksum file, `latest.json`, and `BETA_HANDOFF.md` pass package-artifact verification;
- onboarding works on a clean user profile;
- Connections & Privacy can connect MCP tools or Obsidian/local notes and show status/history;
- Advanced/Fallback can preview and import a selected source when no supported connector exists, but is not the primary path;
- Review can approve and archive connected-source candidates;
- Ask returns at least one useful cited result from approved memory;
- Connections & Privacy clearly shows local storage, permissions, redaction, backup, export, and support controls;
- the reliability report has no critical issue;
- the support bundle is content-free;
- the user can explain where their data lives and how to back it up.
- update and rollback both preserve the local memory folder.

## Known Non-Goals

Do not present these as first-100-user capabilities:

- hosted accounts or multi-user organizations;
- broad public launch readiness;
- hosted 10k-user platform readiness;
- broad live OAuth/API sync for every Gmail, Notion, Slack, Google Drive, Microsoft 365, Teams, Linear, Jira, GitHub, LinkedIn, Twitter/X, Zoom, or browser-history source;
- remote MCP/OAuth;
- billing, quotas, teams, enterprise policy, or hosted analytics;
- automatic app crawling or background cloud capture;
- notarized external distribution unless the current release has completed that work;
- automatic updates or in-app rollback;
- hosted cloud backup;
- perfect extraction quality for every unsupported-source fallback import;
- production incident response, telemetry, or cloud backup.

## Demo Notes

Use real but non-sensitive test data where possible. Avoid promising that Cortex has learned the user's whole life after one connection. The honest claim is narrower: connected and reviewed sources can become cited memory that stays under the user's control.
