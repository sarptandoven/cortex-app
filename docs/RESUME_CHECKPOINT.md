# RESUME HERE - Session Checkpoint (2026-07-09, end of M1 session)

> Paste-able context for continuing this work in a fresh chat. Read top to bottom;
> the "Start the next session with" section at the end is the actual prompt.

## Where we are

Repo: `~/repos/cortex-app`, branch `mass-scale-app-redesign`.
Working tree: **clean**. Local commits **NOT yet pushed** (see blocker).

```
75ff7c2  docs: checkpoint M1 MemoryTruth-light + whole-pipeline verification   <- local only
c429523  M1: MemoryTruth-light benchmark + the wrong-subject citation fixes    <- local only
49b8dd0  docs: MEMORY_MOONSHOTS - 8 ranked memory moonshots                    <- pushed
b381a7f  docs: checkpoint build 22 shipping                                    <- pushed
```

## The arc so far (oldest -> newest)

1. **Phases A-D shipped** (write-back, session harvest, source reputation, integrity
   chain), plus the context-pack identity fix (`CONTEXT_PACK_ENVELOPE_KEYS`).
2. **Build 22 shipped on both channels**: DMG published to the site (`a19d638`),
   App Store signed .pkg built (`f58d4d3` fixed the installer-cert lookup bug).
   Only remaining MAS gap is owner-only: provisioning profile
   (see `outputs/Doppl-AppStore-0.2.0-22/FOUNDER_CHECKLIST.txt`).
3. **docs/MEMORY_MOONSHOTS.md** (`49b8dd0`): deep-researched strategy doc. Thesis:
   the field builds probabilistic/lossy/unfalsifiable memory on two provably broken
   benchmarks (LoCoMo/LongMemEval); Cortex already has the missing spine
   (bi-temporal supersession, trust/authorship ledger, tamper-evident chain), so the
   category to own is **verifiable memory**. 8 ranked moonshots M1-M8; recommended
   order M1 (benchmark) -> M3 (receipted working canvas / OpenClaw) -> M2
   (proof-of-belief bi-temporal).
4. **M1 shipped light** (`c429523`, this session - see below).

## What M1 is (backend/bench/memorytruth.py)

Deterministic, verifiable-by-construction memory benchmark. **No LLM judge** - every
gold answer is a unique seeded slug (e.g. `qk7-xj42-mz9`), grading is exact matching,
so wrong-key/lenient-judge failure modes are structurally impossible. One integer
seed fully determines the scenario.

Four graded categories:
- **recall** - seeded fact must come back cited with the slug
- **abstention** - never-seeded question must return `no_cited_evidence` (no confabulation)
- **temporal** - v1 superseded by v2: answer cites v2, never leaks v1; belief timeline
  shows the revision with the v2 head
- **provenance** - citations resolve to real memories containing the slug;
  author_class survives as "user"; integrity chain verifies (`matches=True`)

Run it:
```
python3 -m backend.bench.memorytruth --seed 7                      # in-process
python3 -m backend.bench.memorytruth --seed 7 --url http://127.0.0.1:8766 --token <key>
```

**Score: 1.0 on all 4 categories across 15 seeds, on all 4 surfaces** (in-process,
live standalone HTTP, FastAPI /mcp JSON-RPC, and the fresh app bundle's .pyc backend
under bundled-target python3.12).

## The real bug M1 caught (and the fix that shipped with it)

`ask_memory` answered questions about UNKNOWN subjects by citing same-shaped memories
about DIFFERENT subjects ("release tag for project X?" -> confidently cited project
Y's release tag). Abstention was 0.17 before. Three-part fix in `storage.py`'s
cite-or-abstain gate (all in `c429523`):
1. `_distinctive_query_terms` - pool-relative document frequency separates subject
   terms from boilerplate; a citation must match a subject term. Terms matched by
   ZERO candidates are excluded (dead verb morphology like decide/decision must not
   veto everything - `test_retrieval_quality` caught that over-strict first cut).
2. `_query_compound_terms` - hyphenated compounds are lexical identifiers; candidates
   must match EVERY part (`delta-dynamo` != `delta-meridian`). Hard constraint even
   under exemptions.
3. Layer-intent exemption narrowed from per-query to per-layer ("release" in a
   question must not exempt non-procedural memories from term matching).

Tests added (+13, all green):
- `backend/tests/test_memorytruth_bench.py` - determinism, honesty floors (1.0 on
  reference seeds 7/42), and 4 NON-TAUTOLOGY sabotage tests (confabulating gate,
  broken supersession, tampered event log, dead retrieval each tank their category).
- `backend/tests/test_memorytruth_pipeline.py` - boots the REAL standalone server on
  loopback; every floor holds over live HTTP; HTTP==in-process agreement test.
  NOTE: HTTP integrity pin->verify uses the Phase D REST endpoints
  (`/v1/integrity/digest`, `/v1/integrity/verify`), NOT tool calls, because tool
  calls are themselves audited events that advance the chain head.

## Whole-app verification done this session

- Full suite: **1609 passed + 372 subtests** under `python3 -m pytest backend/tests
  -q -n 8 --dist loadfile` (run from repo root; xdist loadfile keeps fixtures intact).
- `macos/build.sh` green; fresh `macos/build/Cortex.app` contains the gate fixes
  (verified via `strings` on the bundle's storage.pyc) and its .pyc backend scored
  1.0 over HTTP.
- `scripts/check_distribution_site.py` green (html-links, release-manifest,
  artifact-hashes); site + outputs checksums verified OK.

## THE ONE BLOCKER (owner action needed)

**GitHub push auth died at ~21:40Z** (worked at 19:55Z). Both the stored PAT in
`~/.git-credentials` and the env `GITHUB_TOKEN` return 401 against api.github.com.
No gh oauth token, no SSH key on this machine. Fix: either run `gh auth login`, or
put a fresh PAT in `~/.git-credentials` as `https://<PAT>@github.com`, then:
```
cd ~/repos/cortex-app && git push
```
That publishes `c429523` + `75ff7c2` (+ this checkpoint commit).

## Standing invariants (unchanged)

- Full suite green per commit; contract tests on BOTH servers + MCP parity.
- Honest metrics; cited-or-silent; never weaken a guardrail test to make a change fit.
- Descriptive commits via `git commit -F /tmp/file`; push with plain `git push`
  (credential store), `env -u GITHUB_TOKEN` no longer matters since the env token is dead.
- Tests run from repo root. Bundle ships .pyc (grep with `strings`).
- User thinks input -> middle -> output; mission: "explain yourself to a computer
  once, ever." Approved moonshot order: M1 -> M3 -> M2 (M1 done light).

## Start the next session with

"Continue cortex-app on branch mass-scale-app-redesign. Read
docs/RESUME_CHECKPOINT.md and docs/MEMORY_MOONSHOTS.md first. M1 (MemoryTruth-light)
is shipped and green; the whole pipeline is verified end-to-end on all four surfaces.
First: if `git push` works now, push the local commits. Then proceed to Moonshot M3
- the receipted Symbolic Working-Memory Canvas (Tencent's Mermaid-canvas pattern
with node_ids backed by Cortex integrity receipts, wired toward the OpenClaw
contextEngine attachment per docs/OPENCLAW_ATTACHMENT_EXPLORATION.md). Keep the
MemoryTruth floors green while building: `python3 -m backend.bench.memorytruth
--seed 7` must stay 1.0 across all categories, and the full suite must stay green
(`python3 -m pytest backend/tests -q -n 8 --dist loadfile`)."
