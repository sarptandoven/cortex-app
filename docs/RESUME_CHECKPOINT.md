# RESUME HERE - Session Checkpoint (2026-07-09, end of M3 session)

> Paste-able context for continuing this work in a fresh chat. Read top to bottom;
> the "Start the next session with" section at the end is the actual prompt.

## Where we are

Repo: `~/repos/cortex-app`, branch `mass-scale-app-redesign`.
Working tree: **clean**. All commits **pushed** (auth fixed, see below).

```
5f1139f  M3: canvas token-budget cap (max_chars) with oldest-first elision
0f6ef7e  M1xM3: canvas category in MemoryTruth + the byte-mutation bug it caught
ab94757  M3: receipted symbolic working-memory canvas (Tencent's trick, verifiable)
218cc08  docs: RESUME_CHECKPOINT (previous session)
75ff7c2  docs: checkpoint M1 MemoryTruth-light + whole-pipeline verification
c429523  M1: MemoryTruth-light benchmark + the wrong-subject citation fixes
```

## The arc so far (oldest -> newest)

1. **Phases A-D shipped** (write-back, session harvest, source reputation, integrity
   chain), plus the context-pack identity fix.
2. **Build 22 shipped on both channels** (DMG on the site, App Store signed .pkg).
   Only remaining MAS gap is owner-only: provisioning profile
   (`outputs/Doppl-AppStore-0.2.0-22/FOUNDER_CHECKLIST.txt`).
3. **docs/MEMORY_MOONSHOTS.md**: strategy doc; category to own is **verifiable
   memory**. Approved order M1 -> M3 -> M2.
4. **M1 shipped** (`c429523`): MemoryTruth-light, deterministic slug-graded bench,
   1.0 floors, plus the wrong-subject citation gate fix it caught.
5. **M3 shipped this session** (`ab94757` + `0f6ef7e` + `5f1139f`) - see below.

## What M3 is (the receipted symbolic working-memory canvas)

Tencent's good idea (compact symbolic graph in context, raw tool output offloaded
to disk) with the integrity-free file pointers replaced by **receipts in Cortex's
chain**. Every node = content-addressed vault blob (`working_canvas/evidence/
<sha[:2]>/<sha>.txt`) + one append-only `working_canvas_node` event + a row in
`working_canvas_nodes` (schema ships in database.py SCHEMA, IF NOT EXISTS).

Surfaces (full parity, all tested):
- **MCP tools**: `record_working_canvas_node` (write scope), `get_working_canvas`,
  `get_working_canvas_node` (read scope).
- **FastAPI**: `POST /v1/working-canvas/nodes`, `GET /v1/working-canvas`,
  `GET /v1/working-canvas/{session}/nodes/{node}`.
- **Standalone (shipping) server**: same three routes.
- Status contract: unknown node -> 404, tampered/missing evidence -> 409 (exists
  but proof broken), bad input -> 422. Read-only tokens read but cannot record.
- Drill-down re-hashes the blob (`hmac.compare_digest`) before returning raw bytes.
- **max_chars budget** (Tencent mmdMaxTokenRatio analog): oldest nodes elide from
  the RENDERING only, `_elided` marker + `elided_node_ids` keep them discoverable,
  newest node always renders, storage untouched.
- GDPR: `delete_user_data` deletes canvas rows + unlinks evidence blobs unless
  another tenant references the same content hash.

## The real bug M1xM3 caught (why the scoreboard matters)

First canvas bench run scored 0.55: MCP dispatch passed `raw_text` through
`_text_arg`, which `.strip()`s - evidence with leading/trailing whitespace was
stored MUTATED, so byte-exact recovery was impossible over the tool surface.
Fixed: raw_text exempt from trimming in mcp_tools (length check kept); standalone
server now rejects oversized raw_text (422) instead of silently truncating.
Silent truncation/mutation is the exact "silent evidence loss" M3 exists to kill.

## MemoryTruth is now v2 (5 categories)

`backend/bench/memorytruth.py`: recall, abstention, temporal, provenance, and
**canvas** (record returns receipt + never echoes raw; board holds count, leaks no
slug, renders edges, is <=60% of raw bytes; drill-down byte-identical with stable
receipt). Grading recomputes equality against the scenario - never trusts the
store's verified flag. Sabotage tests prove each category can fail (lossy
drill-down + leaky board tank canvas).

**Floors: 1.0 on all 5 categories, seeds 7/21/42/99/1234, in-process AND live
HTTP** (test_memorytruth_pipeline.py boots the real standalone server).

## Verification state

- Full suite: **1622 passed + 372 subtests** (`python3 -m pytest backend/tests -q
  -n 8 --dist loadfile`, from repo root).
- MemoryTruth: `python3 -m backend.bench.memorytruth --seed 7` -> 1.0 overall.
- NOT yet done since M3: a fresh `macos/build.sh` bundle build + bundle-level
  bench run (do this before shipping build 23).

## Push auth (fixed this session)

`~/.git-credentials` needs the `x-access-token:` username form:
`https://x-access-token:<PAT>@github.com`. The bare `https://<PAT>@github.com`
form stopped working. Also bypass the stale osxkeychain helper and dead env token:
```
env -u GITHUB_TOKEN git -c credential.helper= -c credential.helper=store push
```

## Standing invariants (unchanged)

- Full suite green per commit; contract tests on BOTH servers + MCP parity.
- Honest metrics; cited-or-silent; never weaken a guardrail test to make a change fit.
- Descriptive commits via `git commit -F /tmp/file`.
- Tests run from repo root. Bundle ships .pyc (grep with `strings`).
- Canvas raw_text is NEVER trimmed/truncated on any surface - byte-exact recovery
  is the contract. Oversized input is rejected, not cut.
- Mission: "explain yourself to a computer once, ever." Order M1 -> M3 -> M2
  (M1 done, M3 done; M2 next).

## Start the next session with

"Continue cortex-app on branch mass-scale-app-redesign. Read
docs/RESUME_CHECKPOINT.md and docs/MEMORY_MOONSHOTS.md first. M1 and M3 are
shipped and green (MemoryTruth v2 scores 1.0 on all 5 categories on all
surfaces; full suite 1622+372). Proceed to Moonshot M2 - proof-of-belief
bi-temporal memory (elevate supersession + the integrity chain into sealed,
queryable belief history: 'what did I believe on date X and why'). Also
consider: (a) fresh macos/build.sh + bundle-level bench before any build 23,
(b) the OpenClaw contextEngine attachment - prove Option A (zero-code MCP
against the live server) before writing the TypeScript plugin
(docs/OPENCLAW_ATTACHMENT_EXPLORATION.md). Keep the floors green:
python3 -m backend.bench.memorytruth --seed 7 must stay 1.0 across all 5
categories, and the full suite must stay green."
