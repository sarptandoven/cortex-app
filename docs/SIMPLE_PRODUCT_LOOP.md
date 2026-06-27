# Cortex Simple Product Loop

The first release should make one loop obvious:

```text
Capture -> Review -> Reuse -> Return
```

This is the product habit. The user saves useful context once, quickly decides whether it is trusted, copies it into an AI session, and comes back because the next assistant already knows more.

## Loop State

`GET /v1/loop` returns a single opinionated next action:

- `capture`: save one useful thing
- `review`: approve or archive pending saves
- `reuse`: copy a context pack into an AI tool
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

The Today tab now starts with the loop:

- primary next action
- four compact steps
- loop completion
- reuse count
- streak count

Copying a daily context pack, focused context pack, or integration context records reuse silently. The user still just sees that context was copied.

## MCP Behavior

Agents can call `get_product_loop` to understand where the user is in the loop.

`build_context_pack` records a reuse event because an agent-requested context pack means Cortex memory was used in work.

## Why This Matters

This keeps Cortex simple for normal users:

- Save what matters.
- Approve what is trusted.
- Paste memory into the next AI session.
- See the loop complete.

That is enough for the MVP to create daily value before hosted sync, teams, billing, or deeper automation.
