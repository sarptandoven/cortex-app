# Cortex First-Run Onboarding

## Goal

The first launch should make Cortex usable without docs:

1. Confirm local vault storage.
2. Let the user accept the default vault or choose a folder.
3. Set memory review behavior.
4. Save one useful memory.
5. Copy a working local MCP config or a context pack.
6. Leave the user in the daily workflow.

## Product Principles

- Local-first by default.
- No account required.
- No background capture.
- The user can open the vault folder at any time.
- The local SQLite index is not the source of truth.
- Setup can be reopened from `More -> Setup`.

## Steps

### 1. Vault

Shows the active vault path and the backend-reported vault diagnostics. Users can:

- use the default vault
- choose another folder
- open the current vault folder

Changing the vault path restarts the bundled local backend and verifies that `/health` reports the same vault path.

### 2. Rules

Controls user trust settings:

- review new saves before they become trusted
- allow or block pending saves from AI context
- context pack size

These settings are persisted in the backend and mirrored into the vault `settings.json`.

### 3. First Save

Prompts the user to save one memory through the real capture endpoint. This exercises extraction, vault writes, the local index, Today review, and diagnostics.

### 4. Connect

Provides copy actions:

- MCP config for local tools such as Claude Desktop and agent runners
- context pack for browser chats
- local API settings for advanced setup

The packaged app bundles `scripts/cortex_mcp_stdio.py`, so the copied MCP config points inside the `.app` resources.

### 5. Ready

Summarizes the chosen vault, backend state, and review behavior. Users can create a first backup or open the vault before finishing.

## Persistence

The app stores:

```text
UserDefaults.onboardingComplete.v1
UserDefaults.vaultPath
```

The backend stores user memory behavior in:

```text
Cortex.vault/settings.json
index.sqlite:user_settings
```

## Verification Checklist

- Fresh install shows onboarding automatically.
- `Skip` dismisses and persists completion.
- `More -> Setup -> Open Setup` reopens the flow.
- Choosing a vault folder restarts the backend and `/health` returns the chosen path.
- Saving the first memory writes capture and memory JSON files.
- Copy MCP config uses the bundled script path.
- Creating a backup writes `backups/cortex-vault-*.zip`.
