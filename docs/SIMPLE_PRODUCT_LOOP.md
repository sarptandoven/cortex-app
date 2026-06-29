# Cortex Product Flow

Cortex should make one guided flow obvious:

```text
Model -> Sources -> Review -> Ask -> Trust
```

This is the product habit. The user sees whether their personal model is ready, imports real source data, approves useful memory, asks Cortex with citations, and controls what AI tools can access.

## Tab Acceptance

| Tab | User goal | Ship check |
| --- | --- | --- |
| Model | Understand readiness, coverage, source health, decisions, and open loops at a glance. | Shows model quality, memory coverage, source readiness, and one clear next action. |
| Sources | Add real life-data sources without worrying about duplicates or bad batches. | Supports preview, import, history, source readiness, and undo import. |
| Review | Decide what becomes trusted memory. | Pending captures can be approved or archived, and decisions/open loops stay visible. |
| Ask | Retrieve cited personal memory directly in the app. | Natural-language search returns cited results; context handoffs are secondary fallback actions. |
| Trust | Control privacy, AI access, vault health, backups, and advanced diagnostics. | Defaults are understandable, backup is available, and advanced tools are hidden until needed. |

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

The five-tab app uses the loop as supporting state:

- Model shows readiness and next action.
- Sources shows import and source-readiness state.
- Review shows pending memory and approval state.
- Ask is the primary place to use memory with citations.
- Trust keeps AI access, backup, and diagnostics out of the core flow.

Context packs and browser handoffs remain available as secondary fallback actions when a tool cannot connect to Cortex directly.

## MCP Behavior

Agents can call `get_product_loop` to understand where the user is in the loop.

`build_context_pack` records a reuse event because an agent-requested context pack means Cortex memory was used in work.

## Why This Matters

This keeps Cortex simple for normal users:

- Import what matters.
- Approve what is trusted.
- Ask Cortex before an AI tool acts.
- Keep privacy and backup choices visible.

That is enough for the MVP to create daily value before hosted sync, teams, billing, or deeper automation.
