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

After packaging, run the isolated-home packaged DMG QA once:

```bash
python3 scripts/run_first100_dmg_qa.py
```

Quit any existing Cortex app or backend first; the script fails if `http://127.0.0.1:8766/health` is already responding so it cannot accidentally test the wrong app. It verifies the current `site/downloads` DMG checksum, runs `hdiutil verify`, mounts the DMG, copies the app into a temporary `Applications`-style folder, launches it with a temporary `CFFIXED_USER_HOME`, runs `scripts/first100_live_smoke.py` against `http://127.0.0.1:8766`, and writes `.context/first100_clean_profile_qa_run.txt`. The live smoke syncs a temporary Obsidian vault under an isolated smoke user, verifies Review/Ask/MCP/support-bundle privacy, deletes the smoke user rows, and includes the backup check when supported.

To fill only the clean-profile packet fields that this automation actually verified, run:

```bash
python3 scripts/run_first100_dmg_qa.py --update-clean-profile-packet
```

This is isolated-home QA, not a real human clean-profile or Gatekeeper pass. The clean macOS profile, Control-click > Open, first-run human onboarding, manual update, manual rollback, and invite-copy review gates still need a human.

Before inviting a tester batch, run the launch gate summary:

```bash
python3 scripts/first100_launch_gate.py
```

This command verifies the release artifacts are tracked, the update manifest matches local hashes, docs are current, distribution site files validate, issue templates exist, and the tracked worktree is clean. It returns `needs_human` with exit code 0 when automated checks pass but the required support packet fields still need to be filled.

Use the strict command as the invite-blocking gate:

```bash
python3 scripts/first100_launch_gate.py \
  --support-packet path/to/support-packet.txt \
  --clean-profile-qa path/to/clean-profile-qa.txt \
  --require-human-packet \
  --require-clean-profile-qa
```

Use `docs/FIRST100_CLEAN_PROFILE_QA.md` for the clean-profile packet format. The default summary command exits 0 with `needs_human`; only the strict command above should be treated as invite-blocking.

To generate private packet templates with the current release hashes already filled, run:

```bash
python3 scripts/prepare_first100_launch_packets.py
```

The generated files stay under `.context/` by default and must not be committed.

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
Support channel: sdoven@uwaterloo.ca
Primary support owner: Sarp Doven <sdoven@uwaterloo.ca>
Backup support owner: Sarp Doven <sdoven@uwaterloo.ca>
Incident engineer: Sarp Doven <sdoven@uwaterloo.ca>
Case log location: GitHub Issues in doppl-tech/cortex-app
Support artifact storage: Private GitHub issue attachments/comments; sanitized artifacts only
Business hours and timezone: Founder-monitored weekdays, America/Los_Angeles
Deletion request contact: sdoven@uwaterloo.ca
Build version, build number, hash:
Tester cohort source: Founder-selected first-100 local beta cohort
First batch size: 10 initial testers, then 25, then 100 after no open SEV 0/1 issues
Known limitations sent to testers: local-first macOS beta; unnotarized; macOS may require Control-click > Open; manual updates and rollback; no hosted accounts, cloud backup, automatic updates, production support SLA, or broad live OAuth sync.
Stop/go decision owner: Sarp Doven <sdoven@uwaterloo.ca>
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
- [First-100 Clean-Profile QA](FIRST100_CLEAN_PROFILE_QA.md)
- [First 100 User Support And Privacy Runbook](BETA_SUPPORT.md)
- [Installer And Updates](INSTALLER_AND_UPDATES.md)
- [Trust Controls](TRUST_CONTROLS.md)
- [Operational Readiness](OPERATIONAL_READINESS.md)
