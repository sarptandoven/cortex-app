# Cortex First-Run Onboarding

## Goal

The first launch should make Cortex understandable without docs:

1. Confirm the private local memory folder.
2. Connect one real memory path: MCP AI tools or Obsidian.
3. Show that new source memory goes through Review.
4. Let the user Ask Cortex with citations once approved memory exists.
5. Confirm privacy defaults and make a backup decision.
6. Leave the user in Home, with Review, Ask, and Connections & Privacy available.

## Product Principles

- Local-first by default.
- No account required for the local beta.
- No file-dump or context-copy workflow as the primary setup path.
- No background collection.
- Review-first memory.
- Setup can be dismissed without marking onboarding complete.
- Setup can be reopened from Connections & Privacy.

## Steps

### 1. Private Memory Folder

Shows the active memory folder path, local service state, and backend-reported storage diagnostics. Users can:

- use the default memory folder
- choose another folder
- reveal the current memory folder

Changing the memory folder path restarts the bundled local backend and verifies that `/health` reports the same path.

### 2. Connect Cortex

Prompts the user to connect one working path:

- local AI tools through MCP
- an Obsidian or local notes folder through the native local connector

The Obsidian connector chooses a notes folder once, scans Markdown/text notes locally, registers a source account, syncs records through `/v1/source-accounts/{account_id}/sync`, and preserves file citations.

Future account sign-ins for Gmail, Notion, Slack, Drive, Calendar, GitHub, Mail, Messages, and browser history should use the same source-account sync contract. Unsupported service exports remain advanced/fallback, not onboarding.

### 3. Review Path

Explains that Review is the approval boundary. Connected source records and MCP-written memory candidates wait here before becoming approved memory.

The step can continue once a real connection path exists, because MCP/source sync may create reviewable items asynchronously. If pending items already exist, onboarding lets the user approve or archive them directly.

### 4. Ask Path

Shows the Ask box and cited answer surface. Ask becomes useful once approved memory exists.

The product goal is cited retrieval from approved memory. Context-copy handoffs are secondary advanced/fallback tools and do not define onboarding success.

### 5. Privacy & Backup

Confirms privacy defaults:

- review new memories first
- keep pending memory private from AI tools
- redact exported/handoff memory where possible
- create a local backup or explicitly skip the first backup

## Persistence

The app stores:

```text
UserDefaults.onboardingComplete.v1
UserDefaults.onboardingStep.v2
UserDefaults.onboardingFirstSourceImported.v1
UserDefaults.onboardingFirstMemoryReviewed.v1
UserDefaults.onboardingCortexUsed.v1
UserDefaults.onboardingBackupDecision.v1
UserDefaults.connectedObsidianVaultPath.v1
UserDefaults.connectedObsidianVaultBookmark.v1
UserDefaults.vaultPath
```

The backend stores memory behavior in:

```text
Cortex.vault/settings.json
index.sqlite:user_settings
```

## Verification Checklist

- Fresh install shows onboarding automatically.
- Finish later dismisses setup without marking onboarding complete.
- Connections & Privacy can reopen setup.
- Choosing a memory folder restarts the backend and `/health` returns the chosen path.
- Connecting MCP tools or Obsidian satisfies the first source gate.
- Obsidian sync creates a source account, sync cursor, cited source records, and reviewable memory.
- Review remains the approval boundary before memory is approved.
- Ask with cited results marks Cortex as used.
- Creating a backup writes a local backup archive; skipping backup records an explicit decision.
- Legacy import labels stay absent from the app binary.
