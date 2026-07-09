# Session checkpoint — 2026-07-09

Resume point for the phased expansion roadmap (see docs for phases 0-6).

## Done and committed
- Phase 2: pinned context packs — `11d2bd3`
- Phase 3: provenance ledger & hygiene — `a676ad4`
- Phase 4: personal eval harness — `10477e9`
  - `get_tool_scorecard`: per-token read-model over `mcp:{tool}` events in `memory_events`
  - `grade_answer` / MCP `submit_answer_for_grading`: deterministic regex claim grading with citations
  - REST: `GET /v1/eval/scorecard`, `POST /v1/eval/grade-answer` (fastapi main.py and standalone_server.py)
  - Fixes: `_event()` same-second id collision (uuid nonce), grader qualifier-tail prefix tolerance,
    ANSWER_CLAIM_RE stops at ", and/but" and adds budget/deadline/cost fields

## State
- Full suite green: 1438 tests + 322 subtests

## Next
- Phase 5 per the roadmap docs
