# Cortex Trust Controls

Cortex trust controls define what can become memory, what connected agents can read or change, what gets shared out of the local vault, and which actions leave audit receipts.

## Product Goals

- Keep the first version simple enough for everyday users.
- Make agent access explicit instead of implied by an installed MCP config.
- Keep local storage useful while redacting risky data before it is shared.
- Make every sensitive action visible after the fact.
- Let users recover from mistakes by approving, archiving, forgetting, exporting, and backing up.

## User Controls

## Default Posture

New local vaults start in a guarded mode:

- new saves enter review by default,
- pending saves can appear in app search/context until the user switches to strict mode,
- MCP agent read tools are enabled for retrieval,
- MCP agent write, export, maintenance, and destructive tools are disabled until the user opts in,
- shared context redaction is enabled.

This keeps the core retrieval loop useful while avoiding silent agent mutation or bulk export on first launch.

## Token Boundary

The packaged app now keeps two local secrets:

- the admin app token, stored in Keychain and used by the macOS app for REST API calls;
- a scoped `cxm_` MCP token, also stored in Keychain and copied into local AI-tool MCP configs.

REST endpoints require the admin app token. `/mcp` accepts the admin token for backward compatibility, but new copied or installed MCP configs use the scoped MCP token. MCP tool calls must pass both checks: the token must include the needed scope and the user's Trust toggle for that capability must be enabled.

Default MCP token scopes are `read`, `write`, `export`, and `maintenance`. The destructive scope is not included in newly generated local integration tokens.

Hosted and multi-user development can also enable scoped REST user tokens:

- `POST /v1/integrations/api-token` registers a hashed `cxa_` token for the authenticated user.
- `CORTEX_REQUIRE_SCOPED_API_TOKENS=1` blocks the global app token from selecting another user through `X-Cortex-User`.
- A scoped REST token can only be used as the user it was issued for; mismatched `X-Cortex-User` headers are rejected.
- REST token scopes are enforced by endpoint capability: `read`, `write`, `export`, `maintenance`, and `destructive`.
- Token metadata can be listed and revoked through `GET /v1/integrations/tokens` and `DELETE /v1/integrations/tokens/{token_id}` without exposing raw token values, salts, or hashes.
- This is a backend boundary, not a full consumer account system. Hosted production still needs login, token revocation, device/session management, organization policies, and recovery flows.

### Review New Saves

When enabled, new captures enter the inbox as `pending`.

- Pending captures are visible to the user.
- Pending captures can be approved or archived.
- Pending memory can be hidden from search/context by turning off `allow_pending_in_context`.

### Pending Context

`allow_pending_in_context` controls whether assistants can use memory that has not been approved.

- On: faster, more convenient, useful during active work.
- Off: stricter, only approved captures appear in search, daily review context, and context packs.

### Identity Aliases

`identity_aliases` is an optional list of names, handles, and email addresses that tell the importer which Slack or email speaker lines were written by the user.

- Empty by default, so named speakers stay conservative and do not become user preference/style/negative memory.
- Examples: `sarpt`, `@sarpt`, `sarpt@example.com`.
- Used only during extraction to classify author role; it is not a login system, account proof, or hosted identity layer.
- Stored in the local settings vault and included in local backups.

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
- `get_trust_summary`
- `get_audit_log`

When disabled, connected agents cannot inspect memory through MCP.

### Agent Write Access

`allow_agent_writes` controls MCP write tools:

- `remember_this`
- `approve_memory_capture`
- `archive_memory_capture`

When disabled, connected agents cannot mutate memory or review state.

This is disabled by default for new local vaults.

### Agent Maintenance Access

`allow_agent_maintenance` controls MCP maintenance tools:

- `get_memory_diagnostics`
- `get_reliability_report`
- `create_memory_backup`
- `repair_memory_storage`
- `rebuild_memory_search`
- `rebuild_index_from_vault`

When disabled, connected agents cannot run storage maintenance or rebuild jobs.

This is disabled by default for new local vaults.

### Agent Destructive Access

`allow_agent_destructive_actions` controls MCP destructive tools:

- `forget_memory`
- `delete_memory_capture`
- `delete_memory_backups`
- `restore_latest_memory_backup`
- `delete_all_user_data`

When disabled, connected agents cannot delete records, delete backup archives, restore backups over current state, or delete all local user data.

This is disabled by default for new local vaults.

### Agent Export Access

`allow_agent_exports` controls MCP tools that package memory for use elsewhere:

- `build_context_pack`
- `export_memory`

When disabled, connected agents cannot export or build large context bundles.

This is disabled by default for new local vaults.

### Redact Shared Context

`redact_sensitive_context` masks common sensitive patterns before content leaves the local memory surface through:

- context packs
- Markdown exports
- JSON exports
- MCP tool responses
- Ask citations and cited answer excerpts

Current redaction labels include:

- `[REDACTED_OPENAI_KEY]`
- `[REDACTED_GITHUB_TOKEN]`
- `[REDACTED_SLACK_TOKEN]`
- `[REDACTED_SECRET]`
- `[REDACTED_EMAIL]`
- `[REDACTED_NUMBER]`

Local absolute file paths are converted into safe citation labels such as `local-file://Notes.md#line=12` before they appear in shared context, exports, Ask answers, or agent payloads, even when sensitive-text masking is disabled. The local vault remains the source of truth. Redaction is a sharing-time control.

## Audit Trail

Cortex records audit events in SQLite and mirrors them into the local vault event log.

Important event types:

- capture created
- capture approved
- capture archived
- import created
- import deleted
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
- token id, label, audience, admin flag, and scopes

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

## Data Lifecycle

`GET /v1/privacy/lifecycle` returns a user-facing lifecycle receipt:

- local database and vault paths
- record counts for captures, active memories, imports, source accounts, sync cursors, audit events, and deletion tombstones
- latest backup and retention policy
- export endpoints and redaction state
- delete-all coverage for SQLite, vault records, backups, tokens, jobs, settings, and audit events
- deletion tombstone policy used to block deleted records from being restored from backups
- connected AI access posture and recent audit activity

The report contains counts, paths, settings, and policy status; it does not include memory bodies or raw capture text.

## API Surface

- `GET /v1/settings`
- `PUT /v1/settings`
- `GET /v1/trust/summary`
- `GET /v1/privacy/lifecycle`
- `POST /v1/integrations/api-token`
- `POST /v1/integrations/mcp-token`
- `GET /v1/integrations/tokens`
- `DELETE /v1/integrations/tokens/{token_id}`
- `GET /v1/audit-log?limit=80`
- `POST /mcp`

## macOS Surface

The Trust tab exposes:

- trust score and warnings
- review and pending-context toggles
- agent read/write/export toggles
- agent maintenance/destructive toggles
- shared-context redaction toggle
- context pack size
- integration token list, revoke action, and local MCP token reset
- capture source breakdown
- audit trail
- recovery actions: copy redacted context, backup, open vault

## MVP Boundaries

This is a local-first trust layer. It does not yet include:

- remote per-integration OAuth scopes
- UI for issuing custom per-integration token scopes
- per-tool remote consent screens
- signed event logs
- biometric unlock
- encrypted vault-at-rest
- remote device revocation
- organization policy management
- consumer login and hosted account recovery

Those become important for hosted or multi-device versions. The local MVP should still make agent access understandable and reversible.
