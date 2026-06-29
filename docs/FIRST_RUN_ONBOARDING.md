# Cortex First-Run Onboarding

## Goal

The first launch should make Cortex usable without docs:

1. Confirm local vault storage.
2. Import a real first source.
3. Approve one useful memory.
4. Ask Cortex and get cited memory back.
5. Make a backup decision.
6. Leave the user in the five-tab product flow.

## Product Principles

- Local-first by default.
- No account required.
- No background capture.
- The user can open the vault folder at any time.
- The local SQLite index is not the source of truth.
- Setup can be reopened from Trust.

## Steps

### 1. Private Vault

Shows the active vault path, local service state, and backend-reported vault diagnostics. Users can:

- use the default vault
- choose another folder
- open the current vault folder

Changing the vault path restarts the bundled local backend and verifies that `/health` reports the same vault path.

### 2. Add First Source

Prompts the user to import real source material through preview/import:

- ChatGPT, Claude, Slack, email, notes, docs, or other supported exports
- selected files and folders only
- no live OAuth/API sync in the local beta

This exercises import detection, extraction, duplicate checks, import history, and undo state.

### 3. Review Memory

Prompts the user to approve at least one useful pending memory. This is the trust boundary before Cortex treats memory as ready.

### 4. Ask / Use Cortex

Prompts the user to ask Cortex a real question about the approved source. The step completes when Ask returns cited memory.

Copyable chat context and direct MCP setup remain available after setup, but they are secondary handoff options rather than the main onboarding goal.

### 5. Trust & Backup

Summarizes the chosen vault, AI access posture, redaction state, backend health, and backup decision. Users can create a first backup or explicitly finish later.

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
