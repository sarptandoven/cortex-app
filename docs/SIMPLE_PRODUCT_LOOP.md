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
| Review | Decide what becomes trusted memory. | Synced memory candidates can be approved or archived with source/citation visible. |
| Ask | Retrieve cited personal memory directly in the app. | Natural-language search returns cited results; context handoffs are secondary fallback actions. |
| Connections & Privacy | Manage MCP tools, Obsidian/local notes, privacy, backup, and advanced diagnostics. | MCP and Obsidian are primary; manual import/export/copy flows stay advanced or fallback. |

## Loop State

`GET /v1/loop` returns a single opinionated next action:

- `capture`: save one useful thing
- `review`: approve or archive pending saves
- `reuse`: ask Cortex or use approved memory in an AI tool
- `done`: the loop is complete for today

The response also includes four fixed steps, daily counts, completion percentage, reuse count, and streak days.

## Reuse Tracking

`POST /v1/loop/reuse` records when memory is actually used:

```json
{
  "surface": "today",
  "query": "launch plan",
  "target": "ChatGPT"
}
```

The event is stored in the same append-only audit stream as captures, approvals, backups, and agent activity. This gives Cortex a retention signal without adding a hosted analytics service.

## App Behavior

The app uses the loop as supporting state:

- Home shows readiness, connection state, and next action.
- Review shows pending memory and approval state.
- Ask is the primary place to use memory with citations.
- Connections & Privacy keeps AI access, source sync, backup, and diagnostics out of the core flow.

Context packs and browser handoffs remain available as secondary fallback actions when a tool cannot connect to Cortex directly.

## MCP Behavior

Agents can call `get_product_loop` to understand where the user is in the loop.

`build_context_pack` records a reuse event because an agent-requested context pack means Cortex memory was used in work.

## Why This Matters

This keeps Cortex simple for normal users:

- Connect MCP tools or Obsidian.
- Let Cortex sync automatically.
- Approve what is trusted.
- Ask Cortex before an AI tool acts.
- Keep privacy and backup choices visible.

That is enough for the MVP to create daily value before hosted sync, teams, billing, or deeper automation.
