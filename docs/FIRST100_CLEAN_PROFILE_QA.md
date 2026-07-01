# First-100 Clean-Profile QA

Run this before inviting any tester batch. Use a clean macOS 13+ profile or machine that does not already have Cortex installed, then record only operational evidence. Do not paste private notes, memory content, prompts, API keys, or local tokens into the QA packet.

## Packet Format

Copy this block into a private packet file and fill every field. Boolean fields must be `yes`, `passed`, `ok`, `done`, or `verified` for the strict launch gate to pass.

```text
QA owner:
QA date:
macOS version:
Device type:
Artifact source:
DMG checksum matched:
Installed on clean macOS 13+ profile:
Gatekeeper path accepted:
First-run onboarding completed without developer docs:
Obsidian/local notes or MCP connected:
Sync created Review items:
Review approval worked:
Ask returned cited answer:
Backup worked:
Content-free support bundle export worked:
Manual update preserved memory folder:
Manual rollback preserved memory folder:
Invite copy reviewed for local-beta limitations:
```

Run the strict gate with both the support packet and this QA packet:

```bash
python3 scripts/first100_launch_gate.py \
  --support-packet path/to/support-packet.txt \
  --clean-profile-qa path/to/clean-profile-qa.txt \
  --require-human-packet \
  --require-clean-profile-qa
```

To generate private support and QA packet templates with the current release hashes filled in, run:

```bash
python3 scripts/prepare_first100_launch_packets.py
```

The generated files stay under `.context/` by default and must not be committed.

## Required Pass

1. Download the exact DMG from the planned tester URL or hosted artifact path.
2. Verify its SHA-256 hash against `site/downloads/Cortex-0.1.0-1.checksums.txt`.
3. Install on a clean macOS 13+ profile.
4. If the build is unnotarized, verify the documented Control-click > Open path works and the invite copy discloses it.
5. Complete first-run onboarding without developer docs.
6. Connect Obsidian/local notes or an MCP AI tool.
7. Sync enough data to create Review items.
8. Approve at least one Review item.
9. Ask a question and confirm the answer includes citations.
10. Create a backup from Connections & Privacy.
11. Generate a support bundle and confirm it is content-free.
12. Install the current build over a previous beta and confirm the memory folder is preserved.
13. Roll back to the previous retained beta and confirm the memory folder is preserved.

## No-Go

Do not invite testers from the build if any of these fail:

- checksum differs from the release manifest;
- the app cannot launch from Applications;
- Gatekeeper blocks the unnotarized build beyond the documented Control-click > Open path;
- onboarding requires developer docs to complete;
- sync does not create Review items;
- Ask cannot return cited answers after Review approval;
- backup, support bundle, update, or rollback risks user data;
- invite copy implies notarization, automatic cloud backup, hosted sync, or production support.
