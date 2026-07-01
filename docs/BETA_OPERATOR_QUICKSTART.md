# First 100 Beta Operator Quickstart

Use this one-page checklist before each invite batch. The full runbooks remain the source of truth, but operators should be able to run this without reading every doc first.

## 1. Ship Gate

Run from the repo root:

```bash
python3 -W error::ResourceWarning -m unittest discover backend/tests
python3 scripts/retrieval_eval.py
python3 scripts/adaptation_eval.py
python3 scripts/run_memory_worker.py --user-id local --limit 25
./macos/build.sh
codesign --verify --deep --strict --verbose=2 macos/build/Cortex.app
python3 scripts/ops_readiness_check.py --skip-tests --refresh-site --include-package --require-package-artifacts
git diff --check
```

The package command must produce and verify:

- `outputs/Cortex-0.1.0-1/Cortex-0.1.0-1.dmg`
- `outputs/Cortex-0.1.0-1/Cortex-0.1.0-1.app.zip`
- `outputs/Cortex-0.1.0-1/latest.json`
- `outputs/Cortex-0.1.0-1/Cortex-0.1.0-1.checksums.txt`
- `outputs/Cortex-0.1.0-1/BETA_HANDOFF.md`

Do not invite users from a build that fails the ship gate.

The worker command should return a JSON object with `failed: 0`. If it reports failed jobs, inspect the `failed_jobs` array before inviting users from that build.

After launching the packaged app, run the live first-100 smoke once:

```bash
python3 scripts/first100_live_smoke.py
```

It uses the local app token from `~/Library/Application Support/Cortex/credentials.json`, syncs a temporary Obsidian vault under an isolated smoke user, verifies Review/Ask/MCP, and deletes the smoke user rows. It does not create a backup unless `--include-backup` is passed.

## 2. Invite The Right Testers

Good first-100 users:

- run macOS 13 or newer;
- can install a direct beta DMG;
- can connect MCP AI tools or an Obsidian/local notes vault;
- can spend 30 to 45 minutes on setup, sync, review, Ask, Connections & Privacy, backup, export, and support bundle checks;
- understand this is local-first beta software with no hosted account or cloud recovery.

Defer users who need SSO, compliance review, live OAuth sync, mobile support, enterprise retention, regulated workflows, or hosted backup.

## 3. Run The Product Loop

Ask each tester to complete this exact loop:

```text
Home -> Review -> Ask -> Connections & Privacy
```

Acceptance for a tester session:

- local vault is visible and understandable;
- MCP AI tools or Obsidian/local notes connect and sync;
- at least one memory is approved in Review;
- Ask returns a useful cited answer;
- Connections & Privacy shows redaction, agent permissions, backup, export, delete, and support bundle controls;
- the tester can explain where data lives and how to back it up.

Manual imports, context packs, and copy handoffs are secondary. Do not treat a copied memory brief or imported data dump as the core success metric.

## 4. Privacy Rules

Operators should not ask for private memory content.

Allowed support artifacts:

- sanitized support bundle reviewed by the user;
- source type, file count, size, and date range;
- redacted screenshots or short redacted examples created by the user;
- terminal/app error text with private names, paths, and tokens redacted.

Do not request raw imports, full chat exports, memory exports, context packs, prompts with private content, API keys, or local tokens.

## 5. Support Intake

Before sending invites, fill in the batch support packet:

```text
Support channel:
Primary support owner:
Backup support owner:
Incident engineer:
Case log location:
Support artifact storage:
Business hours and timezone:
Deletion request contact:
Build version, build number, hash:
Known limitations sent to testers:
Stop/go decision:
```

For every case, open a case ID first and record only operational metadata:

- tester alias, batch, build, macOS, device, install path;
- source type and approximate source shape, such as file count, size, or date range;
- Connections & Privacy posture: redaction, pending context, MCP reads/writes/exports;
- symptom, expected behavior, actual behavior, restart result, backup state;
- support bundle status after user review;
- severity, owner, response deadline, next action, and resolution.

Use statuses from `BETA_SUPPORT.md`: `new`, `triaged`, `needs-user`, `reproducing`, `mitigating`, `fix-pending`, `ready-to-close`, `closed`.

## 6. Triage Targets

Set severity before repair steps:

- SEV 0: possible data loss, deletion failure, backup failure, memory folder missing, SQLite corruption, or private content in a support artifact. Acknowledge within 2 business hours, ask the user to stop using Cortex until backup state is known, and escalate to the primary support owner and incident engineer.
- SEV 1: app cannot launch, backend cannot start, installer blocked, delete/export inaccessible, or user locked out of local data. Acknowledge within 1 business day and generate an offline support bundle.
- SEV 2: sync, import, review, search, citation, MCP, backup, or export behavior is wrong but data remains accessible. Acknowledge within 2 business days and reproduce with dummy data or local test data.
- SEV 3: copy, onboarding confusion, visual polish, stale links, update-feed mismatch, or docs gaps. Batch unless it blocks onboarding, privacy understanding, backup, export, or deletion.

## 7. Stop Conditions

Pause new invites immediately if any tester reports:

- possible data loss, missing vault data, or SQLite corruption;
- backup, export, or delete-all failure;
- private content in a support bundle;
- app launch failure affecting more than one tester on the same build;
- installer/update/rollback behavior that risks the local vault.

Before repair, ask the user to stop using Cortex and make a manual copy of the vault folder if possible.

## 8. Batch Discipline

Suggested rollout:

- users 1 to 10: technical testers only;
- users 11 to 25: mixed technical users after no SEV 0/1 issues remain;
- users 26 to 100: broader macOS beta users only after onboarding, backup, export, delete-all, and support bundle checks pass for the current build.

Each batch needs the filled support packet, no open SEV 0, no repeated unresolved SEV 1 on the same build, and a recorded stop/go decision from the primary support owner.

## References

- [First 100 User Demo Checkpoint](checkpoints/first-100-demo.md)
- [First 100 User Support And Privacy Runbook](BETA_SUPPORT.md)
- [Installer And Updates](INSTALLER_AND_UPDATES.md)
- [Trust Controls](TRUST_CONTROLS.md)
- [Operational Readiness](OPERATIONAL_READINESS.md)
