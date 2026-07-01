# Cortex Product Flow

Cortex should make one guided flow obvious:

```text
Home -> Review -> Ask
```

Connections and privacy controls live in a secondary **Connections & Privacy** sheet. The product habit is simple: connect MCP AI tools or Obsidian once, let Cortex sync memory automatically, review useful candidates, then ask with citations.

## Surface Acceptance

| Surface | User goal | Ship check |
| --- | --- | --- |
| Home | Understand whether Cortex is ready and what to do next. | Shows connection state, memory readiness, and one primary action. |
| Review | Decide what becomes approved memory. | Synced memory candidates can be approved or archived with source/citation visible. |
| Ask | Retrieve cited personal memory directly in the app. | Natural-language search returns cited results; context-copy handoffs are secondary fallback actions. |
| Connections & Privacy | Manage MCP tools, Obsidian/local notes, privacy, backup, and advanced diagnostics. | MCP and Obsidian are primary; import, export, and context-copy flows stay advanced or fallback. |

## Loop State

`GET /v1/loop` supports a single opinionated next action for the visible product flow:

- connect/sync: connect MCP tools or Obsidian/local notes and let Cortex update memory
- review: approve or archive pending memory candidates
- ask: ask Cortex or use approved memory in a connected AI tool
- `done`: the loop is complete for today

The response also includes fixed steps, daily counts, completion percentage, use count, and streak days. Product copy should describe the loop as connect/sync, review, ask even when older API fields use compatibility names.

## Use Tracking

`POST /v1/loop/reuse` records when memory is actually used:

```json
{
  "surface": "today",
  "query": "launch plan",
  "target": "ChatGPT"
}
```

The event is stored in the same append-only audit stream as source syncs, approvals, backups, and agent activity. This gives Cortex a retention signal without adding a hosted analytics service.

## App Behavior

The app uses the loop as supporting state:

- Home shows readiness, connection state, and next action.
- Review shows pending memory and approval state.
- Ask is the primary place to use memory with citations.
- Connections & Privacy keeps AI access, source sync, backup, and diagnostics out of the core flow.

Context-copy packs and browser handoffs remain available as secondary fallback actions when a tool cannot connect to Cortex directly.

## MCP Behavior

Agents can call `get_product_loop` to understand where the user is in the loop.

`build_context_pack` records a use event because an agent-requested context-copy pack means Cortex memory was used in work.

## Why This Matters

This keeps Cortex simple for normal users:

- Connect MCP tools or Obsidian.
- Let Cortex sync automatically.
- Approve what is useful.
- Ask Cortex before an AI tool acts.
- Keep privacy and backup choices visible.

That is enough for the MVP to create daily value before hosted sync, teams, billing, or deeper automation.
