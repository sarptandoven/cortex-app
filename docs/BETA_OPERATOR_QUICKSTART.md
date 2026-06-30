# First 100 Beta Operator Quickstart

Use this one-page checklist before each invite batch. The full runbooks remain the source of truth, but operators should be able to run this without reading every doc first.

## 1. Ship Gate

Run from the repo root:

```bash
python3 -W error::ResourceWarning -m unittest discover backend/tests
python3 scripts/retrieval_eval.py
python3 scripts/adaptation_eval.py
./macos/build.sh
codesign --verify --deep --strict --verbose=2 macos/build/Cortex.app
python3 scripts/ops_readiness_check.py --skip-tests --include-package --require-package-artifacts
git diff --check
```

The package command must produce and verify:

- `outputs/Cortex-0.1.0-1/Cortex-0.1.0-1.dmg`
- `outputs/Cortex-0.1.0-1/Cortex-0.1.0-1.app.zip`
- `outputs/Cortex-0.1.0-1/latest.json`
- `outputs/Cortex-0.1.0-1/Cortex-0.1.0-1.checksums.txt`
- `outputs/Cortex-0.1.0-1/BETA_HANDOFF.md`

Do not invite users from a build that fails the ship gate.

## 2. Invite The Right Testers

Good first-100 users:

- run macOS 13 or newer;
- can install a direct beta DMG;
- have one user-controlled source export, folder, or file to import;
- can spend 30 to 45 minutes on setup, import, review, Ask, Trust, backup, export, and support bundle checks;
- understand this is local-first beta software with no hosted account or cloud recovery.

Defer users who need SSO, compliance review, live OAuth sync, mobile support, enterprise retention, regulated workflows, or hosted backup.

## 3. Run The Product Loop

Ask each tester to complete this exact loop:

```text
Model -> Sources -> Review -> Ask -> Trust
```

Acceptance for a tester session:

- local vault is visible and understandable;
- one real source imports through Sources;
- at least one memory is approved in Review;
- Ask returns a useful cited answer;
- Trust shows redaction, agent permissions, backup, export, delete, and support bundle controls;
- the tester can explain where data lives and how to back it up.

Context packs and MCP handoffs are secondary. Do not treat a copied memory brief as the core success metric.

## 4. Privacy Rules

Operators should not ask for private memory content.

Allowed support artifacts:

- sanitized support bundle reviewed by the user;
- source type, file count, size, and date range;
- redacted screenshots or short redacted examples created by the user;
- terminal/app error text with private names, paths, and tokens redacted.

Do not request raw imports, full chat exports, memory exports, context packs, prompts with private content, API keys, or local tokens.

## 5. Stop Conditions

Pause new invites immediately if any tester reports:

- possible data loss, missing vault data, or SQLite corruption;
- backup, export, or delete-all failure;
- private content in a support bundle;
- app launch failure affecting more than one tester on the same build;
- installer/update/rollback behavior that risks the local vault.

Before repair, ask the user to stop using Cortex and make a manual copy of the vault folder if possible.

## 6. Batch Discipline

Suggested rollout:

- users 1 to 10: technical testers only;
- users 11 to 25: mixed technical users after no SEV 0/1 issues remain;
- users 26 to 100: broader macOS beta users only after onboarding, backup, export, delete-all, and support bundle checks pass for the current build.

Each batch needs a build number, artifact hashes, known limitations, support owner, and stop/go decision.

## References

- [First 100 User Demo Checkpoint](checkpoints/first-100-demo.md)
- [First 100 User Support And Privacy Runbook](BETA_SUPPORT.md)
- [Installer And Updates](INSTALLER_AND_UPDATES.md)
- [Trust Controls](TRUST_CONTROLS.md)
- [Operational Readiness](OPERATIONAL_READINESS.md)
