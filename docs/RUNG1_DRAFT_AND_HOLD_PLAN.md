# Rung 1 — Draft-and-Hold ("it writes the reply in your voice; you send it")

The strategy's true product and permanent shippable ceiling. Everything built so far
(real semantic memory, the cited Profile, the knowledge graph, the persona pack) is the
fuel; this is the engine that turns it into felt value.

> Rung 1 — DRAFT, YOU SEND (reversible, one tap): the self writes a real reply/comment/
> update in your voice and leaves it UN-SENT. You edit (one key) or send (one tap). If you
> edit, it quietly learns the correction.

## 0. Non-negotiable guardrails (from the strategy — design inside these)

- **Cortex NEVER sends, publishes, spends, or deletes on the user's behalf.** No autonomous
  action, ever. Draft-and-hold is the ceiling.
- **No write/send connectors in v1.** Connectors stay read-only. "You send it" = the user
  copies the finished draft into their own tool and sends it there. This removes the entire
  liability surface (the strategy: get E&O insurance *before* the first write-capable token)
  and needs no new OAuth write scopes.
- **A draft influenced by external content (a pasted email, a web page) is ALWAYS flagged**
  in the receipt — prompt-injection is unsolved.
- **Never "this IS you" / "your twin."** Always "drafted from your memory; you decide."
- **Dead simple:** no new tab. The draft lives in the existing Ask surface (Ask can answer
  *or* draft). No wall of toggles.
- The frontier model is **not** ours to protect. Cortex's durable value is the **cited
  persona + the receipt**, not the LLM that writes the prose.

## 1. The one experience

A draft is waiting, written the way you would write it, with one quiet line under it:

> "From 3 of your memories · here's what I refused to assume · Edit · Copy"

That single moment is the product. The memory/layers/graph/MCP are invisible machinery.

## 2. Architecture — two delivery modes, one backend

The prose is written by an LLM. Two modes decide *whose* LLM, so the no-API-key path still works:

- **Mode A — MCP (the beachhead, no Cortex key):** the user is in Claude Code / Cursor /
  ChatGPT. Their AI drafts, grounded by Cortex. Cortex supplies the cited voice + facts +
  must-hold constraints, then reviews the AI's draft and returns the receipt. Their tool is
  the LLM; Cortex needs no key. This is the 60-second-install activation.
- **Mode B — in-app (richer, key-gated):** the macOS app generates the draft itself via an
  LLM when a key is configured; with no key it degrades to a **cited scaffold / talking
  points** (deterministic) rather than finished prose — honest, never a fake in-voice draft.

Both share one backend built on existing assets:

| Piece | Builds on |
|-------|-----------|
| `build_draft_context(task, context_text?)` — the cited persona + task-relevant retrieved memories + a MUST-HOLD constraint pack (style + negatives + current decisions) + poison-flag + "can't-ground" list | `agent_adaptation` persona (832e964), semantic `search()`, `_answer_*` current-truth logic |
| `review_draft(draft, task)` → structured **receipt** | upgrade of `_style_draft_review` (mcp_tools.py:951) |
| `record_draft` / `record_draft_edit` — ledger + learning | `record_agent_event` audit spine, `remember_this` (pending+attributed writes) |
| Generate (Mode B only) | LLM path gated exactly like the extractor / condense (ANTHROPIC_API_KEY), deterministic scaffold fallback |

## 3. The Receipt — the trust surface (attached to every draft)

Deterministic, cited, no LLM. This is what makes delegated writing trustworthy and is the
moat the labs can't copy:

1. **"From N of your memories"** — the exact cited `memory_ids` + sources the draft drew on.
2. **"What I refused to assume"** — aspects of the task Cortex had no grounded memory for, so
   the draft doesn't fabricate (abstention-as-a-feature). Derived from the task terms with no
   supporting current-truth memory.
3. **Poison-flag** — if `context_text` contains imperative/instruction-like content ("ignore
   previous", "send", "reply with…"), flag: "influenced by external content — verify before
   sending." Deterministic heuristic; never blocks, always discloses.
4. **Voice-check** — the `review_draft` result: does it match the user's style memory and
   avoid their negative/rejected patterns. (Replaces the hardcoded hype-list + `words>28`
   magic threshold with the user's real cited style memory; folds in deep-dive 3.8's cleanup.)

The receipt is stored via `record_agent_event` so there's an inspectable "what my AI drafted
and why" history — an integrity/undo surface, **never** marketed as legal defensibility.

## 4. The edit-learns-the-correction loop

When the user edits a draft before using it, capture the diff as a signal: a low-confidence
`style`/`preference` correction memory ("you rephrased X → Y"), written **PENDING + attributed**
into the Review inbox (never auto-active — human vouch, per the write-back guardrail). Over
time this tunes the voice. Deterministic diff capture; no LLM in the path that must be tested.

## 5. Components & phases (each shippable, tested, reversible)

**Phase 1 — backend draft-context + receipt + MCP (no key, deterministic core).**
- `storage.build_draft_context(user_id, task, context_text=None, sector=None)` → cited persona
  + retrieved cited memories + must-hold constraint pack + poison-flag + can't-ground list.
- `storage.review_draft(user_id, draft, task)` → the structured receipt (§3), upgrading
  `_style_draft_review` to use real style memory.
- MCP tools `draft_in_voice` (returns the grounded context for the AI to draft) and
  `review_draft` (returns the receipt). Read-only, scoped like existing read tools.
- `record_draft` ledger entry via `record_agent_event`.
- Tests: cited context, poison-flag fires on injection-y input, "refused to assume" lists true
  gaps, receipt cites, abstains on thin corpus.

**Phase 2 — in-app Draft (macOS) + generation.**
- `storage.generate_draft(user_id, task, context_text=None)` — LLM-gated (key present) with a
  deterministic **scaffold** fallback (cited talking points) when no key.
- macOS: the Ask surface gains a "Draft a reply" mode → shows the draft + the receipt line
  ("From N memories · what I refused to assume · Edit · Copy"). **Copy**, not send. Dark-mode,
  a11y, no new tab.
- Tests (backend deterministic; UI build-verified): scaffold fallback shape, receipt rendering
  contract.

**Phase 3 — edit-learns loop.**
- `storage.record_draft_edit(user_id, original, edited, draft_id)` → diff → pending attributed
  correction memory. Tests: edit produces a pending, cited correction; never auto-active.

**Explicitly deferred (NOT in this arc):** actual in-app send via write connectors (needs
write OAuth scopes + E&O insurance + a per-action tap gate — the strategy's later rung);
multi-step / standing-permission drafting (Rungs 2–4, RESEARCH only).

## 6. Sequencing & parallelization

Phase 1 is the foundation (everyone depends on the draft-context + receipt contract) and is
the sole heavy `storage.py` work → do it first / serially. Then Phase 2's `generate_draft`
(backend, key-gated) and the macOS Draft UI are disjoint enough to parallelize against the
Phase-1 contract; Phase 3 is a small follow-on. Same discipline as the profile build:
pin the receipt/draft-context data contract up front so parallel agents don't drift.

## 7. Success = the strategy's north-star metrics

- **wrong-in-my-voice incident rate → ~0** (a draft the user would NOT have sent). The receipt's
  refuse-to-assume + poison-flag + voice-check exist to drive this down.
- **draft-sent-without-edit rate** (the voice is right) and **edit/correction rate** (learning).
- **"an external AI visibly used your context"** — the real activation, delivered by Mode A.
