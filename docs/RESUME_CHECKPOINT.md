# RESUME HERE - Cortex M8 portable memory checkpoint (2026-07-10)

## Exact state

Repo: `~/repos/cortex-app`
Branch: `mass-scale-app-redesign`

Shipped milestones: **M1 -> M3 -> M2 -> M6 -> M5 -> M7 -> M4 -> M8 stages 1-3**.

## Latest state: M8 signed portable memory protocol v2

HEAD before this work: `21f3d26 Add signed portable memory import`.

The latest M8 stages publish a language-neutral portable-memory protocol v2, an OpenClaw adapter package, and a measured cross-agent interoperability gate:

- `backend/app/storage.py` now exports outer bundle `v3` / protocol `2` with authoritative signed `payload_bytes`, strict unpadded base64url decoding, bounded JSON validation, duplicate-key rejection for payload bytes, proof SHA-256, fixed ASCII signature/proof preimages, Ed25519 signer id validation, empty-chain support, and strict outer/protocol version dispatch.
- Private signed `v2` / protocol `1` bundles remain verifiable/importable for compatibility. Legacy unsigned `v1` bundles remain payload-verifiable but are not importable.
- `import_portable_bundle` imports v2 from decoded authoritative payload bytes only, records protocol version in portable lineage, preserves idempotent replay/conflict behavior, and reports `published_protocol`.
- `docs/PORTABLE_MEMORY_PROTOCOL_V2.md` is the published protocol specification.
- `spec/portable-memory/v2/schema.json` plus `spec/portable-memory/v2/test-vectors/cortex-python.json` provide cross-language fixtures.
- `scripts/generate_portable_memory_vectors.py` regenerates the committed Python vector.
- `packages/openclaw-cortex-context/` is a TypeScript OpenClaw `contextEngine` adapter that supports live Cortex context and offline signed bundle verification with signer pinning.
- M8 stage 3 adds `cortex-python-n3.json`: three Python-signed memories cross Python import/MCP recall and OpenClaw offline recall with record and bundle provenance intact. The receiving prompt carries signer id, source-tenant SHA-256 binding, continuity head, payload SHA-256, memory ids, and source references. The raw tenant is verifier API data, not prompt text.
- The TypeScript verifier rejects a defined 11-case signed-field tamper corpus 11/11, and strict bundle mode stops assembly on tampering.

Validation on this tree:

```text
python3 -m pytest backend/tests/test_m8_portable_memory.py -q
# 14 passed, 13 subtests passed

cd packages/openclaw-cortex-context && npm test
# 8 passed

python3 scripts/generate_portable_memory_vectors.py
git diff --check
# clean
```

Recent commits:

```text
70c8d86  M6: add calibrated known-unknown metacognition
704da89  docs: RESUME_CHECKPOINT - bench v3
c6bc9f1  M1+M2: bench v3 - independent belief_proof grading
5bb9929  M2: harden proof discovery, integrity caching, and audit contracts
```

The M6 backend milestone is committed as `70c8d86`. The MemoryTruth v4, exact-cohort, retry, tests, and checkpoint changes described below are the next commit.

## What M6 now does

Cortex exposes answerability confidence separately from factual correctness.

- `ask_memory` / `GET /v1/ask` return `confidence`, `known_unknown`, `confidence_detail`, and an actionable `knowledge_gap` when evidence is insufficient.
- `would_i` returns and persists the same calibrated metacognition contract.
- Zero cited evidence is a hard invariant: confidence `0.0`, `known_unknown: true`, and abstention.
- Conflicted or low-confidence evidence cannot clear the stable `0.6` answerability threshold.
- General Ask stays on deterministic `heuristic_v1`; twin outcome history cannot leak into unrelated Ask confidence.
- Twin empirical calibration activates only after five explicit answerability labels in the same fixed confidence bin.
- `grade_twin_prediction` keeps two orthogonal labels:
  - `outcome`: `correct | incorrect | unclear`
  - `answerability`: `answerable | unknown | unclear`
- Correctness is never used as a proxy for whether memory had enough evidence.

## First-class calibration read model

`get_twin_calibration` reports:

- Expected calibration error (ECE)
- Brier score
- Five fixed confidence bins
- Abstention precision and recall
- Selection-independent coverage
- Confident-wrong count and rate
- Explicit legacy/unlabeled populations

Exact `prediction_ids` cohort filtering is supported across storage, MCP, FastAPI, and standalone REST. This prevents old user history from contaminating one benchmark/evaluation run while preserving honest account-level defaults when no cohort is supplied.

Surfaces:

- MCP: `get_twin_calibration` (read), `grade_twin_prediction` (write)
- FastAPI: `GET /v1/twin/calibration?prediction_ids=...`
- Standalone: identical route and query semantics
- `get_twin_scorecard` embeds the same calibration read model without a duplicate event-log fold

Malformed, boolean, string, and non-finite legacy confidence metadata is never treated as certainty.

## MemoryTruth v4

`backend/bench/memorytruth.py` is now version 4 with seven categories:

1. recall
2. abstention
3. temporal
4. provenance
5. canvas
6. belief_proof
7. **metacognition**

The M6 category is verifiable by construction:

- Four seeded preference decisions must return cited `likely_yes` predictions above threshold.
- Four never-seeded, unique decisions must return zero-confidence `insufficient_evidence` abstentions.
- The bench grades correctness and answerability through the public feedback loop.
- ECE, Brier, bins, abstention precision/recall, coverage, and confident-wrong rate are independently reimplemented from benchmark gold labels.
- Cortex's first-class calibration result is cross-checked field-by-field for window, exact cohort, sample counts, bins, scalar/nested metrics, and empty ungraded/unlabeled lists.
- Quality floors: ECE <= `0.05`, Brier <= `0.01`, abstention precision/recall `1.0`, confident-wrong rate `0.0`, and expected `0.5` coverage for the balanced scenario.
- HTTP mode honors bounded `Retry-After` responses for 429/503, capped at six attempts and five seconds per delay.

Non-tautology tests prove the category falls for:

- Overconfident answers to never-seeded decisions
- A lying calibration scorecard
- Cohort/population contamination
- Existing M1/M2/M3 sabotage cases remain intact

Hand-calculated tests pin ECE, Brier, bin boundaries at `0.0/0.2/0.6/1.0`, abstention confusion counts, coverage, confident-wrong math, and invalid confidence rejection.

## Validation

- Focused cohort/surface/metric tests: **23 passed + 11 subtests**
- Full affected M6 suite: **212 passed + 12 subtests**
- MemoryTruth v4 module: **24 passed + 11 subtests**
- Live standalone pipeline: **3 passed + 2 consecutive-run subtests**
- Reference seeds `7/21/42/99/1234`: all seven category scores `1.0`, overall `1.0`
- Fresh-store M6 metrics on all five seeds: ECE `0.0`, Brier `0.0`, abstention precision/recall `1.0`, confident-wrong rate `0.0`, coverage `0.5`
- Independent final review: **Ship**, no blocking/high findings
- Full backend suite: **1665 passed + 385 subtests**

## Standing invariants

- Cite or abstain. Never invent evidence.
- Answerability and correctness remain separate labels.
- Zero evidence can never be calibrated into an answer.
- Metrics are independently checkable and honest about unlabeled/legacy populations.
- MCP, FastAPI, and standalone behavior stay in parity.
- Tests run from the repo root.
- Do not weaken a benchmark floor merely to make a regression pass.

## Next milestone

Per `docs/MEMORY_MOONSHOTS.md`, the next recommended higher-ceiling bet after M6 is **M5: poison-proof shared memory**, unless the user redirects. Preserve MemoryTruth v4 as the scoreboard and build attacks first, defenses second.

## Release gates cleared (2026-07-10, cow session)

- **Bundle gate for build 23: PASSED.** `python3 scripts/bundle_bench.py` boots the
  standalone server FROM the built .app (.pyc backend, embedded-3.12 framework
  interpreter, same env the Swift supervisor sets) and runs MemoryTruth over live
  HTTP, one fresh server+vault per seed. Seeds 7/21/42/99/1234 all hold every
  category at 1.0 against the shipped artifact. Encoded gotcha: bundle .pyc is
  3.12-compiled, so the harness must use the framework python, not PATH python3.
- **OpenClaw attachment Option A: PROVEN** (zero Cortex code). An MCP client over
  `scripts/cortex_mcp_stdio.py` -> bundle server `/mcp` completed the full agent
  loop: initialize -> tools/list -> remember_this -> ask_memory (cited, slug
  found) -> get_context (slug found) -> honest abstention on an unseeded topic.
  Option B (TypeScript contextEngine plugin) now has a working baseline to beat.
- **UI quality shipped** (`b0615c1` + `956bfe3`): window sizing fixed
  (NSHostingController sizingOptions), machine-artifact extraction gate, live
  vault swept (216 junk captures archived with backup), named source status,
  stale-footer clearing. Suite on that tree: 1689 passed + 389 subtests.

## Start the next session with

"Continue cortex-app on branch `mass-scale-app-redesign`. Read `docs/RESUME_CHECKPOINT.md` and `docs/MEMORY_MOONSHOTS.md`. M1, M3, M2, and M6 are shipped. M6 separates answerability from correctness, exposes exact-cohort ECE/Brier/abstention metrics across MCP and both HTTP servers, and MemoryTruth v4 independently grades seven categories with all reference seeds at 1.0. Start M5 poison-proof shared memory next, using adversarial attacks as the spec and keeping all v4 floors green."

Updated M8 resume prompt:

"Continue cortex-app on branch `mass-scale-app-redesign`. Read `docs/RESUME_CHECKPOINT.md`, `docs/PORTABLE_MEMORY_PROTOCOL_V2.md`, and `docs/MEMORY_MOONSHOTS.md`. Current active work is M8 stage 1: published portable-memory protocol v2, authoritative signed payload bytes, Python verifier/import compatibility, cross-language schema/vector generation, and the `@cortex/openclaw-context` TypeScript verifier/contextEngine adapter. Re-run `python3 -m pytest backend/tests/test_m8_portable_memory.py -q`, `cd packages/openclaw-cortex-context && npm test`, and `python3 scripts/generate_portable_memory_vectors.py && git diff --check` before shipping further changes."
