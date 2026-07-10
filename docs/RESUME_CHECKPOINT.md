# RESUME HERE - Session Checkpoint (2026-07-10, bench v3 session)

> Paste-able context for continuing this work in a fresh chat. Read top to bottom;
> the "Start the next session with" section at the end is the actual prompt.

## Where we are

Repo: `~/repos/cortex-app`, branch `mass-scale-app-redesign`.
Working tree: **clean** after the bench v3 commit. All commits **pushed**.
Note: other agent sessions work in this repo too (5bb9929 landed from a parallel
session); always `git fetch` + rebase-check before pushing.

```
c6bc9f1  M1+M2: bench v3 - belief_proof category graded by independent crypto
5bb9929  M2: harden proof discovery, integrity caching, and audit contracts
5fdf099  M2: sealed bi-temporal Proof-of-Belief across storage, MCP, and both HTTP servers
5f1139f  M3: canvas token-budget cap (max_chars) with oldest-first elision
0f6ef7e  M1xM3: canvas category in MemoryTruth + the byte-mutation bug it caught
ab94757  M3: receipted symbolic working-memory canvas (Tencent's trick, verifiable)
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
5. **M3 shipped** (`ab94757` + `0f6ef7e` + `5f1139f`) - see below.
6. **M2 shipped this session** - see below.

## What M2 is (sealed bi-temporal Proof-of-Belief)

Every new or revised memory snapshot now gets a `belief_recorded` / `belief_revised`
receipt in the append-only integrity log. Supersession writes a corrected valid-time
snapshot plus a `belief_superseded` receipt, so Cortex can reconstruct **valid-time X
as known at transaction-time Y** without reading mutable current text.

Surfaces (full parity, all tested):
- **MCP**: `get_belief_proof` and `verify_belief_proof` (read scope), plus timeline
  `as_of` shorthand. Verification accepts `expected_head` separately from the
  untrusted proof and reports `anchored_verified` only for that external anchor.
- **FastAPI**: `GET /v1/beliefs/proof`, `POST /v1/beliefs/proof/verify`, and
  `GET /v1/beliefs/timeline?as_of=...`.
- **Standalone shipping server**: identical routes, validation, and read-scope rules.

Proof mechanics and honest limits:
- Full snapshots are sealed in receipts; returned content is re-hashed against its
  receipt, and receipts are bound to the known-at chain head by a hash-chain suffix.
- Normal-sized histories also get Merkle inclusion paths. Large histories omit that
  redundant tree and keep the authoritative chain segment.
- A derived FTS projection searches **historical sealed snapshots**, fixing the case
  where a same-id revision removes every old query term. It is rebuilt from receipts
  and erased with tenant data.
- Cached event fingerprints and chain links are invalidated by SQLite triggers on
  insert/update/delete, including direct tampering. Existing 10k-memory vaults are
  prewarmed at startup: measured first proof **23.59 ms** after a **63.99 ms** startup
  prewarm (synthetic 10,002-event vault), below the <50 ms query objective.
- The pure verifier proves **receipt inclusion**, not arbitrary search completeness.
  It explicitly returns `completeness_verified: false`; a selection manifest catches
  omission/mutation in transit, but M2 does not pretend this is an authenticated search
  index. That would be a future proof-format upgrade.
- Legacy rows remain queryable but are marked unsealed. Migration never invents
  supersession time from `updated_at`; it uses a real historical conflict receipt or
  leaves the bound unknown.

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

## MemoryTruth is now v3 (6 categories)

`backend/bench/memorytruth.py`: recall, abstention, temporal, provenance, canvas,
and **belief_proof** (15 probes/seed). belief_proof grades M2 end to end and
NEVER trusts store verdicts: `_independent_verify_belief_proof` reimplements the
crypto contract (canonical event fingerprint, chain fold from genesis/prefix,
canonical snapshot re-hash) so a lying store is caught. Probes: P1 current shows
v2 never v1; P2 retro known_at = v2.recorded_at - 1us shows v1 never v2; P3
bench-doctored content rejected by both independent + store verifiers (parity);
P4 unknown topics don't fabricate the never-seeded compound; P5 a prefix head
pinned before all probe traffic reproduces byte-identically at end of run (the
pin anchor waits out the current server second - second-precision audit rows
truncate down and would race a same-second prefix). Report carries
`belief_proof_latency` {count, p50_ms, max_ms} from real probe timings.

Sabotage tests (test_memorytruth_bench.py NonTautologyTests) prove each failure
mode tanks the category: retconning store (ignores known_at), doctored response
content with intact receipts, rubber-stamp verify_belief_proof, and a **coherent
DB rewrite after the pin** (UPDATE trigger clears cached fingerprint, store
rebuilds its chain green, only the external pin catches it - asserted that the
pin probe specifically is what fails).

**Floors: 1.0 on all 6 categories, seeds 7/21/42/99/1234, in-process AND live
HTTP** (fresh standalone server per seed; also test_memorytruth_pipeline.py).
Proof latency p50 ~28ms in-process, ~38ms over HTTP.

## Verification state

- Full suite: **1637 passed + 372 subtests** (isolated worktree; the working repo
  had 145 unrelated failures from ANOTHER agent's in-flight M6 edits to
  storage.py - not ours, verify in a worktree when the tree is shared).
- MemoryTruth: `python3 -m backend.bench.memorytruth --seed 7` -> 1.0 overall.
- NOT yet done since M3/M2: a fresh `macos/build.sh` bundle build + bundle-level
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
  (**all three done**).

## Start the next session with

"Continue cortex-app on branch mass-scale-app-redesign. Read
docs/RESUME_CHECKPOINT.md and docs/MEMORY_MOONSHOTS.md first. M1, M3, and M2 are
shipped and green: Proof-of-Belief is bi-temporal and chain-sealed on MCP,
FastAPI, and standalone; MemoryTruth v3 scores 1.0 on all 6 categories
(belief_proof graded by independent crypto, sabotage-tested); full suite is
1637+372 in a clean worktree. Next operational gate: run a fresh macos/build.sh
plus the bundle-level MemoryTruth bench before build 23. Then prove OpenClaw
attachment Option A (zero-code MCP against the live server) before considering
the TypeScript contextEngine plugin. Keep the floors green."
