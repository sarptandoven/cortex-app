# First 100 User Support And Privacy Runbook

This runbook is for operators inviting and supporting the first 100 Cortex beta users. It turns the local-first privacy and review model into concrete invite, consent, support, privacy, deletion, export, and escalation practices.

The operating rule is simple: do not ask users to send private memory content. Start from symptoms, counts, source types, settings posture, and the sanitized support bundle. Ask for raw content only if the user volunteers a redacted example that they have reviewed.

## Invite Criteria

Invite users who can safely exercise the beta without turning it into broad production support.

Good first-100 candidates:

- use macOS 13 or newer;
- are comfortable installing a local beta app from a direct build;
- can spend 30 to 45 minutes on first-run setup, sync, review, Ask, Connections & Privacy, backup, and support-bundle checks;
- can connect an MCP AI tool or Obsidian/local notes source and let it sync;
- can explain expected behavior when filing a bug;
- agree to keep sensitive, regulated, or third-party-confidential material out of early testing unless they intentionally choose to manage that risk locally;
- understand that Cortex is local-first beta software, not a hosted account service or managed enterprise product.

Do not invite, or defer until later, users who need:

- Windows, Linux, iOS, Android, or browser-extension-only support;
- team administration, SSO, compliance review, DPA, SOC 2 evidence, or enterprise retention controls;
- broad live OAuth/API sync beyond MCP and Obsidian as the primary workflow;
- cloud backup, hosted recovery, or remote support access;
- legal, medical, financial, HR, child-safety, or regulated-record workflows;
- guaranteed extraction quality for every large, noisy, or proprietary export format.

For the first 25 users, prefer people who can report clearly and tolerate beta friction. For users 26 through 100, include more nontechnical macOS users, but only after onboarding, backup, export, delete-all, and support-bundle checks pass for the current build.

## Pre-Invite Operator Checklist

Before sending a build to any new batch:

1. Run the beta ship gate from `docs/OPERATIONAL_READINESS.md`.
2. Confirm the packaged release includes `BETA_HANDOFF.md`.
3. Confirm the app shows Home, Review, Ask, and Connections & Privacy.
4. Confirm support-bundle export works in live and offline modes, and fails closed if the bundle is not content-free.
5. Confirm delete-all and export flows are available from the current build.
6. Confirm delete-all with the default backup-including path removes current backup archives and leaves restore unavailable.
7. Confirm the operator has the current release version, build number, hash, and download location.
8. Confirm the known-limitations list below matches the build being sent.

Do not expand the batch if any current tester has an unresolved SEV 0 possible-data-loss issue.

## Support Desk Setup

Before inviting the first tester, create a private first-100 support workspace that operators can use without storing private memory content.

Fill these batch routing fields before invites go out:

```text
Support channel: REQUIRED private email, chat, or helpdesk queue
Primary support owner: REQUIRED named person
Backup support owner: REQUIRED named person
Incident engineer: REQUIRED named person or rotation
Case log location: REQUIRED private tracker or spreadsheet
Support artifact storage: REQUIRED private folder limited to support owner and incident engineer
Business hours and timezone: REQUIRED response window
Deletion request contact: REQUIRED channel or named owner
Build version, build number, hash: REQUIRED exact build and artifact hash sent to testers
Tester cohort source: REQUIRED invite list, waitlist segment, or partner group
First batch size: REQUIRED number of users in the next invite batch
Known limitations sent to testers: REQUIRED link, template, or exact limitations text
Stop/go decision owner: REQUIRED named person
```

Required setup:

1. Create a private case log with one row per support case.
2. Restrict the case log and support artifact storage to operators who need access.
3. Pin the consent script, deletion/export guarantee, severity table, and known-limitations list in the support channel.
4. Prepare one reply template for standard intake, one for SEV 0/1 stop-using guidance, and one for support artifact deletion confirmation.
5. Confirm the support owner can generate or explain live and offline support bundles.
6. Confirm every operator knows that raw memory content, raw source files, memory exports, prompts with private content, API keys, and local tokens are not acceptable support artifacts.
7. Run one dummy support case before the first invite: file the case, attach a sanitized test support bundle, assign severity, close it, and delete the test artifact.

Use this case-log schema. It is intentionally content-free:

```text
Case ID:
Opened at:
Tester alias:
Batch number:
Build version:
Build number/hash:
macOS version:
Device type:
Severity:
Status:
Owner:
Response deadline:
Subsystem: onboarding/install/sync/review/ask/search/citations/MCP/backup/export/delete/support-bundle/update/docs
Source type: MCP AI tool/Obsidian/local notes/advanced fallback/none/unknown
Source shape: approximate file count, size, or date range only
Connections & Privacy posture: redaction on/off, pending context on/off, MCP reads/writes/exports on/off
Backup state: exists/none/unknown
Support bundle: not requested/requested/received/rejected/deleted
User-visible symptom:
Next action:
Resolution:
Support artifact deletion requested: yes/no
Support artifact deletion completed at:
```

Allowed case statuses:

- `new`: case received, intake not complete.
- `triaged`: severity assigned and response deadline set.
- `needs-user`: waiting for non-private details, support bundle review, or confirmation.
- `reproducing`: operator is trying to reproduce with dummy data or local test data.
- `mitigating`: user has stop-use, backup, export, delete, reinstall, or wait-for-fix guidance.
- `fix-pending`: linked product/docs fix exists but is not yet in a verified build.
- `ready-to-close`: user has a path forward and artifact deletion preference is recorded.
- `closed`: resolution and artifact retention/deletion are recorded.

## Consent And Privacy Script

Read or send this before the user installs the beta. Keep the wording intact unless the product behavior changes.

```text
Cortex is a local-first beta for personal memory. Your synced sources and approved memory are stored on your Mac in your Cortex memory folder. Cortex does not create a hosted account for this beta, and operators cannot remotely inspect that folder.

You choose what to connect or sync. For early testing, avoid sensitive, regulated, employer-confidential, or third-party private data unless you have decided that storing it locally in Cortex is appropriate.

Support may ask for a sanitized support bundle. The support bundle is designed to omit raw source text, memory content, task content, exported user files, context-copy payloads, and raw MCP query values. You should review the bundle before sending it.

Support will not ask you to paste private memory content. If a bug depends on exact content, send a short redacted example that you are comfortable sharing, or describe the source type and visible behavior without the private text.

You can export your memory and delete local Cortex data from this beta. Deletion is local to your Mac and covers the current Cortex memory folder and index data and, by default, local Cortex backup archives. If you previously sent a support bundle or redacted example to operators, ask support to delete their copy too.

This is beta software. Source sync, advanced/fallback import quality, citations, search, MCP handoffs, packaging, and recovery flows may have bugs. Do not rely on Cortex as the only copy of important data.
```

Record consent before sending the build:

- user confirmed they understand local-first storage;
- user confirmed they choose what to connect or sync;
- user confirmed they know support bundles should be reviewed before sending;
- user confirmed support will not request private memory content;
- user confirmed they can export and delete local beta data;
- user confirmed they understand beta limitations.

## Deletion And Export Guarantee

Use this guarantee in beta support conversations:

```text
You own the local Cortex memory folder on your Mac. Before you leave the beta, we will help you export your Cortex data and delete local Cortex beta data from the app. We cannot remotely delete data from your Mac because we do not operate a hosted account for this beta. If you sent us a support bundle or redacted example, we will delete our support copy when you ask.
```

Operator actions for export:

1. Ask the user to create a local backup before export.
2. Ask the user to use the app export flow from Connections & Privacy, or the documented export endpoint if they are running a developer build.
3. Confirm whether redaction is enabled before any shared export. Redaction must cover Markdown export, JSON export, and MCP export responses before operators treat the build as first-100 ready.
4. Tell the user to store the exported file somewhere they control.
5. Do not ask the user to send the export to support.

Operator actions for deletion:

1. Ask whether the user wants to export first.
2. Ask the user to quit connected AI tools that may use the Cortex MCP bridge.
3. Ask the user to run the app delete-all flow from Connections & Privacy, or the documented delete endpoint if they are running a developer build.
4. Confirm whether backups should also be deleted.
5. For beta offboarding, use the backup-including delete path unless the user explicitly asks to preserve local backups.
6. After backup-including delete-all, confirm there are no Cortex backup archives available to restore and Cortex starts as a fresh setup.
7. If deletion fails, treat it as SEV 0 until a backup and support bundle are collected.

Support-bundle handling:

- Use `scripts/export_support_bundle.py` in live or offline mode when possible; it validates the privacy flags and rejects content-bearing fields before writing the JSON file.
- A support bundle is acceptable only when it omits raw source text, memory content, task content, exported user files, context-copy payloads, and raw MCP query values.
- If the exporter exits with a content-free validation error, do not ask the user to send the file. Escalate as a privacy issue for that build.

Support copy retention:

- Delete user-provided support bundles and redacted examples within 7 days of a deletion request.
- Delete support bundles after the issue is closed unless they are still needed for a linked fix.
- Do not copy support bundles into public issue trackers, pull requests, release notes, or shared demos.
- If a support artifact accidentally contains private memory content, restrict access immediately and escalate as a privacy incident.

## Support Triage

Handle every case through the same intake and triage path.

### Intake Path

1. Open a case ID before asking for details.
2. Send the standard intake request below.
3. Record only symptoms, counts, source types, settings posture, build data, and artifact status.
4. Reject or delete accidental raw content instead of copying it into the case log.
5. Assign severity before suggesting repair steps.
6. For SEV 0 or SEV 1, give stop-use or backup-first guidance before troubleshooting.
7. Set the response deadline from the severity table.
8. Close the case only after the user has a path forward and support artifact retention/deletion is recorded.

Standard intake request:

```text
Please do not send raw notes, chats, memory records, prompts, exports, API keys, or local tokens.

Case ID:
Cortex version and build number:
macOS version:
Device type:
Install path, usually /Applications/Cortex.app:
Did first-run setup complete: yes/no
Source type involved: MCP AI tool, Obsidian/local notes, advanced/fallback import, backup, export, delete, update, or none
Approximate source shape: file count, size, or date range only
Connections & Privacy settings relevant to the issue:
What you expected:
What happened instead:
Does it reproduce after restarting Cortex: yes/no/not tried
Does a local backup exist: yes/no/unknown
Are you comfortable reviewing and sending a sanitized support bundle: yes/no
Optional: redacted screenshot or dummy-data reproduction, with private text hidden
```

Record these triage fields:

- Cortex version, build number, build hash if known, and batch number;
- macOS version and device type;
- install path, usually `/Applications/Cortex.app`;
- whether first-run setup completed;
- source type involved, such as MCP AI tool sync, Obsidian/local notes sync, notes, bookmarks, calendar, or an advanced/fallback import source;
- rough source size, file count, or date range;
- Connections & Privacy settings: pending memory candidates allowed in AI context, redaction enabled, MCP reads/writes/exports enabled or disabled;
- what the user expected;
- what happened instead;
- whether the issue reproduces after restarting Cortex;
- whether a backup exists;
- sanitized support bundle status if the user is comfortable reviewing and sending it;
- severity, owner, response deadline, status, next action, and resolution.

Do not request:

- raw synced or imported files;
- full chat exports;
- full memory records;
- screenshots that reveal private memory;
- context-copy payloads;
- MCP prompts containing private content;
- API keys, local tokens, or secrets.

Acceptable optional artifacts:

- sanitized support bundle reviewed by the user;
- redacted screenshot with memory text hidden;
- redacted three-line example created by the user;
- source schema or file extension without content;
- terminal error text with paths, tokens, and private names redacted;
- a reproduction using dummy data.

### Triage Levels

SEV 0: possible data loss, deletion failure, backup failure, memory folder missing, SQLite corruption, private content leaked into a support artifact.

Response:

1. Acknowledge within 2 business hours.
2. Tell the user to stop using Cortex until backup state is known.
3. Ask them not to delete or move the memory folder.
4. Collect a sanitized support bundle if possible.
5. Ask them to make a local copy of the memory folder before repair.
6. Escalate immediately to the primary support owner and incident engineer named in Support Desk Setup.
7. Pause new invites for the affected build until the incident engineer gives a stop/go decision.

SEV 1: app cannot launch, backend cannot start, installer is blocked, delete/export flow is inaccessible, or the user is locked out of their local data.

Response:

1. Acknowledge within 1 business day.
2. Confirm macOS version, install location, and whether the app was moved to Applications.
3. Generate an offline support bundle.
4. Prefer replacing the app over touching the memory folder.
5. Escalate to the primary support owner if the issue affects more than one tester on the same build.
6. Pause the next invite batch when two SEV 1 cases are open on the same build.

SEV 2: sync, advanced/fallback import, review, search, citation, MCP, context-copy fallback, backup, or export behavior is wrong but data remains accessible.

Response:

1. Acknowledge within 2 business days.
2. Collect the standard intake and support bundle.
3. Ask for source type and shape, not source content.
4. Try backup-first repair or search rebuild only after the user has a backup.
5. Link the case to the current build and suspected subsystem.
6. Escalate to SEV 1 if the user cannot access local data or if two more testers reproduce the same failure on the same build.

SEV 3: copy, onboarding confusion, visual polish, stale links, update-feed mismatch, or documentation gaps.

Response:

1. Batch with other beta feedback unless it blocks onboarding.
2. Ask for the screen name and visible label, not private content.
3. Fix before the next invite batch if it affects first-run privacy, review, or backup clarity.
4. Keep the case open only when a docs or product follow-up is assigned.

## Incident Escalation

Escalate immediately when any of these happen:

- a user reports missing or corrupted memory folder data;
- backup, export, or delete-all fails;
- the support bundle includes raw memory content;
- an operator accidentally requests or receives private memory content;
- a build is sent with a known delete/export regression;
- two or more users hit the same launch, source sync, advanced/fallback import, or local service startup failure;
- a user cannot access local data after an app replacement.

Escalation steps:

1. Pause new invites for the affected build.
2. Assign one operator as user contact and one engineer as incident lead.
3. Preserve the build number, hash, release notes, and current docs.
4. Ask affected users to stop using Cortex until the incident lead gives a recovery step.
5. Collect support bundles only after reminding users to review them.
6. Avoid repair commands until a backup or memory-folder copy exists.
7. Write a private incident note with timeline, affected build, user count, impact, root cause, fix, and follow-up.
8. Resume invites only after the fix is verified by the ship gate and one first-100 demo pass.

Privacy incident handling:

- If private memory content reaches support, stop copying it.
- Move the artifact to the smallest-access private location available, or delete it if it is not needed for user recovery.
- Record who accessed it.
- Ask the user whether they want the artifact deleted immediately.
- Do not quote private content in tickets, commits, pull requests, release notes, or docs.

## Known Limitations To Tell Testers

Use this list in handoff notes and support replies:

- Cortex is local-first beta software for macOS, not a hosted account service.
- The first-100 workflow is connect MCP AI tools or Obsidian/local notes, let Cortex sync, review useful candidates, then ask with citations. Advanced/fallback imports are only for unsupported sources, migration, or support recovery.
- The app does not provide cloud backup or remote memory-folder recovery.
- Source sync and fallback importers may miss, duplicate, or misclassify content from large or unusual sources.
- Review is the approval boundary; unreviewed or archived content may not appear in Ask results depending on settings.
- Citations should be checked by the user before relying on an answer.
- Optional embeddings and connected AI tools may introduce their own privacy boundaries.
- MCP access is controlled by Connections & Privacy settings and scoped local tokens, but connected tools are still user-managed.
- Support bundles are designed to be content-free, but users should review them before sending.
- The beta is not intended for regulated, legal, medical, financial, HR, child-safety, or enterprise compliance workflows.
- Reinstalling the app should not be used as a recovery step until the memory folder is backed up.
- Public launch items such as hosted accounts, broad telemetry, enterprise policy, notarized public distribution, and formal production incident response may still be incomplete for a given build.

## Useful Bug Reports Without Private Content

Ask users for this structure:

```text
Build:
macOS:
Install path:
First-run setup completed: yes/no
Source type:
Approximate source size:
Connections & Privacy settings relevant to the issue:
Steps to reproduce:
Expected behavior:
Actual behavior:
Does restart change it:
Backup created before repair: yes/no
Support bundle attached after review: yes/no
Redacted example attached: optional
```

Good report examples:

- "Claude Desktop MCP sync completed, Review showed 0 candidates, support bundle attached."
- "Obsidian/local notes sync found 42 Markdown files, Ask result cited an archived note, pending context disabled, screenshot redacted."
- "MCP search returned empty results after approving 12 memory candidates, MCP reads enabled, support bundle attached."
- "Advanced/fallback folder import with 42 Markdown files duplicated two batches after one import, screenshot redacted."

Poor report requests to avoid:

- "Send the chat export that caused this."
- "Paste the memory record that looks wrong."
- "Send a screenshot of the full Ask answer."
- "Send your context-copy payload."
- "Send your MCP prompt and response."

If exact text seems necessary, ask the user to reproduce with dummy data or to provide a redacted minimal example that preserves only the shape of the bug.

## Batch Rollout Practice

Use small batches so support quality stays ahead of growth:

- Batch 1: 5 technical testers, no unresolved SEV 0 or SEV 1 before expanding.
- Batch 2: 20 mixed technical and nontechnical testers, at least 80 percent complete first-run setup and backup.
- Batch 3: 50 broader testers, support bundle and delete/export paths verified on the current build.
- Batch 4: 100 total testers, no repeated unresolved onboarding blocker and no open privacy incident.

Hold the rollout when:

- any SEV 0 is open;
- two SEV 1s occur on the same build;
- support cannot explain delete/export behavior clearly;
- support has requested private memory content in the last batch;
- the known-limitations list is stale for the build being distributed.

## Operator Closeout

Before closing a support case:

- confirm the user can continue, export, delete, or wait for a fixed build;
- confirm whether any support artifact should be deleted;
- record the build, source type, subsystem, severity, and resolution;
- add a docs or product follow-up if the issue came from unclear privacy, review, backup, export, deletion, or support-bundle language.
