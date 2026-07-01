# Cortex First-Run Onboarding

## Goal

The first launch should make Cortex understandable without docs:

1. Confirm the private local vault.
2. Connect one real memory path: MCP AI tools or Obsidian.
3. Show that new source memory goes through Review.
4. Let the user Ask Cortex with citations once approved memory exists.
5. Confirm trust defaults and make a backup decision.
6. Leave the user in Home, with Review, Ask, and Connections & Privacy available.

## Product Principles

- Local-first by default.
- No account required for the local beta.
- No manual data dump as the primary setup path.
- No background capture.
- Review-first memory.
- Setup can be dismissed without marking onboarding complete.
- Setup can be reopened from Connections & Privacy.

## Steps

### 1. Private Vault

Shows the active vault path, local service state, and backend-reported vault diagnostics. Users can:

- use the default vault
- choose another folder
- reveal the current vault folder

Changing the vault path restarts the bundled local backend and verifies that `/health` reports the same vault path.

### 2. Connect Cortex

Prompts the user to connect one working path:

- local AI tools through MCP
- an Obsidian vault through the native local connector

The Obsidian connector chooses a vault folder once, scans Markdown/text notes locally, registers a source account, syncs records through `/v1/source-accounts/{account_id}/sync`, and preserves file citations.

Future account sign-ins for Gmail, Notion, Slack, Drive, Calendar, GitHub, Mail, Messages, and browser history should use the same source-account sync contract. Unsupported service exports remain Advanced/Fallback, not onboarding.

### 3. Review Path

Explains that Review is the trust boundary. Connected source records and MCP-written memory candidates wait here before becoming approved memory.

The step can continue once a real connection path exists, because MCP/source sync may create reviewable items asynchronously. If pending items already exist, onboarding lets the user approve or archive them directly.

### 4. Ask Path

Shows the Ask box and cited answer surface. Ask becomes useful once approved memory exists.

The product goal is cited retrieval from approved memory. Copyable handoff formats are secondary advanced tools and do not define onboarding success.

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
- Choosing a vault folder restarts the backend and `/health` returns the chosen path.
- Connecting MCP tools or Obsidian satisfies the first source gate.
- Obsidian sync creates a source account, sync cursor, cited captures, and reviewable memory.
- Review remains the approval boundary before memory is trusted.
- Ask with cited results marks Cortex as used.
- Creating a backup writes `backups/cortex-vault-*.zip`; skipping backup records an explicit decision.
- Removed manual import labels stay absent from the app binary.
