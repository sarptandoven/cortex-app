# Cortex Trust Controls

Cortex trust controls define what can become memory, what connected agents can read or change, what gets shared out of the local vault, and which actions leave audit receipts.

## Product Goals

- Keep the first version simple enough for everyday users.
- Make agent access explicit instead of implied by an installed MCP config.
- Keep local storage useful while redacting risky data before it is shared.
- Make every sensitive action visible after the fact.
- Let users recover from mistakes by approving, archiving, forgetting, exporting, and backing up.

## User Controls

### Review New Saves

When enabled, new captures enter the inbox as `pending`.

- Pending captures are visible to the user.
- Pending captures can be approved or archived.
- Pending memory can be hidden from search/context by turning off `allow_pending_in_context`.

### Pending Context

`allow_pending_in_context` controls whether assistants can use memory that has not been approved.

- On: faster, more convenient, useful during active work.
- Off: stricter, only approved captures appear in search, daily review context, and context packs.

### Agent Read Access

`allow_agent_reads` controls MCP read tools:

- `search_memory`
- `get_recent_context`
- `get_daily_review`
- `get_memory_graph`
- `get_decisions`
- `get_open_questions`
- `list_memory_topics`
- `list_memory_entities`
- `get_about_person`
- `get_about_entity`
- `get_memory_stats`
- `get_memory_inbox`
- `get_memory_diagnostics`
- `get_trust_summary`
- `get_audit_log`

When disabled, connected agents cannot inspect memory through MCP.

### Agent Write Access

`allow_agent_writes` controls MCP write tools:

- `remember_this`
- `approve_memory_capture`
- `archive_memory_capture`
- `forget_memory`

When disabled, connected agents cannot mutate memory or review state.

### Agent Export Access

`allow_agent_exports` controls MCP tools that package memory for use elsewhere:

- `build_context_pack`
- `export_memory`

When disabled, connected agents cannot export or build large context bundles.

### Redact Shared Context

`redact_sensitive_context` masks common sensitive patterns before content leaves the local memory surface through:

- context packs
- Markdown exports
- JSON exports
- MCP tool responses

Current redaction labels include:

- `[REDACTED_OPENAI_KEY]`
- `[REDACTED_GITHUB_TOKEN]`
- `[REDACTED_SLACK_TOKEN]`
- `[REDACTED_SECRET]`
- `[REDACTED_EMAIL]`
- `[REDACTED_NUMBER]`

The local vault remains the source of truth. Redaction is a sharing-time control.

## Audit Trail

Cortex records audit events in SQLite and mirrors them into the local vault event log.

Important event types:

- capture created
- capture approved
- capture archived
- memory archived
- settings updated
- backup created
- search rebuilt
- index rebuilt from vault
- MCP tool called

MCP audit events include safe metadata only:

- tool name
- success or failure
- argument keys
- content character count
- query preview
- short error text

They do not store full MCP prompts or full content payloads in audit metadata.

## Trust Summary

`GET /v1/trust/summary` returns:

- trust score
- mode: `guarded`, `balanced`, or `open`
- current trust settings
- pending capture count
- active memory count
- recent agent event count
- source counts by capture source
- risk flags
- redaction labels

The score is not a security guarantee. It is a product signal that helps users understand whether the current setup is strict or permissive.

## API Surface

- `GET /v1/settings`
- `PUT /v1/settings`
- `GET /v1/trust/summary`
- `GET /v1/audit-log?limit=80`
- `POST /mcp`

## macOS Surface

The Trust tab exposes:

- trust score and warnings
- review and pending-context toggles
- agent read/write/export toggles
- shared-context redaction toggle
- context pack size
- capture source breakdown
- audit trail
- recovery actions: copy redacted context, backup, open vault

## MVP Boundaries

This is a local-first trust layer. It does not yet include:

- per-integration OAuth scopes
- per-tool remote consent screens
- signed event logs
- biometric unlock
- encrypted vault-at-rest
- remote device revocation
- organization policy management

Those become important for hosted or multi-device versions. The local MVP should still make agent access understandable and reversible.
