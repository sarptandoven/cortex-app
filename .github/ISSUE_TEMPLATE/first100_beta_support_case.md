---
name: First-100 beta support case
about: Track a first-100 beta issue without collecting private memory content
title: "[first-100 support] "
labels: first-100, support
assignees: ""
---

## Privacy Rule

Do not paste raw notes, chats, memory records, prompts, context packs, exports, API keys, local tokens, or screenshots containing private memory content into this issue.

Use symptoms, source type, approximate source shape, settings posture, and the sanitized support bundle status only.

## Case Intake

- Case ID:
- Tester alias:
- Batch number:
- Build version:
- Build number/hash:
- macOS version:
- Device type:
- Install path, usually `/Applications/Cortex.app`:
- First-run setup completed: yes/no/unknown
- Source type: MCP AI tool / Obsidian or local notes / advanced fallback / backup / export / delete / update / none / unknown
- Source shape: approximate file count, size, or date range only
- Connections & Privacy posture:
  - redaction: on/off/unknown
  - pending context: on/off/unknown
  - MCP reads: on/off/unknown
  - MCP writes: on/off/unknown
  - MCP exports: on/off/unknown
- Backup state: exists/none/unknown
- Support bundle: not requested/requested/received/rejected/deleted

## User-Visible Behavior

- Expected:
- Actual:
- Reproduces after restarting Cortex: yes/no/not tried
- Affected surface: onboarding/install/sync/review/ask/search/citations/MCP/backup/export/delete/support-bundle/update/docs

## Severity

Choose one:

- SEV 0: possible data loss, deletion failure, backup failure, missing memory folder, SQLite corruption, or private content in a support artifact
- SEV 1: app cannot launch, backend cannot start, installer blocked, delete/export inaccessible, or user locked out of local data
- SEV 2: sync, review, search, citation, MCP, backup, export, or update behavior is wrong but data remains accessible
- SEV 3: copy, onboarding confusion, visual polish, stale links, or docs gaps

Selected severity:

## Response Plan

- Owner:
- Response deadline:
- Current status: new / triaged / needs-user / reproducing / mitigating / fix-pending / ready-to-close / closed
- Next action:
- Dummy-data reproduction available: yes/no
- User asked to stop using Cortex: yes/no/not needed
- User asked to preserve or copy local memory folder before repair: yes/no/not needed

## Resolution

- Resolution summary:
- Linked PR/commit:
- User confirmed path forward: yes/no
- Support artifact deletion requested: yes/no
- Support artifact deletion completed at:
