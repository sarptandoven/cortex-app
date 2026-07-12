# YC Execution Plan — from "beautifully engineered, unfundable" to "walk in with the number"

**Date:** 2026-07-11 · **Companion to:** `YC_COMPANY_PLAN.md` (the pitch) · **This doc:** the brutal judgment + the complete implementation mapping + the build now underway.

---

## 0. THE BRUTAL VERDICT (four YC-partner lenses + synthesis, run against the actual code)

> **Not fundable today (~10%), and the reason is unusually specific: this company cannot demonstrate or even measure its own core promise.** The pitch is "one memory, actively used across every AI" — and as of 2026-07-11 there is no cross-AI demo (only a headless plumbing e2e that drives zero real clients), no Proof moment (onboarding ends on a decorative fake 8-node constellation), no share card (zero lines of code behind the entire PLG loop), and the north-star metric has never been computed for a single user despite the instrumentation being ~80% built. The founder can state the test count (~1,845) but not the one number the plan itself says flips the yes.
>
> **The saving grace, and why this is close rather than dead:** the code is stale-ahead-of-plan in the founder's own favor. On-device Model2Vec embeddings — which the plan still calls the #1 open fix — shipped, bundled, default-on. The push half of hosted sync — which the plan calls "designed but unbuilt" — is built, tested, and wired. Nothing between today and an undeniable in-room pitch is research: **this is one to two weeks of glue code, not a pivot.**
>
> **The ONE thing standing between this and a yes:** walk in with the evidence loop closed — the live three-tool cited demo, "your memory was read by N AIs this week" on the home screen, and 50–200 hand-recruited users (CLAUDE.md maintainers, Rewind refugees) generating a real N in week 4 *without opening the app*. If passive cross-AI recall retention is real, this is a fund; if those 50 users show N=0, the honest answer is the product is a beautifully engineered vitamin and no amount of protocol work fixes that.

**Fund probability:** ~10% as-is → ~20% after the build alone → **~45–50% after the build + a nonzero week-4 cross-AI recall number from 50–200 real users.** The code de-risks the demo; only the usage number de-risks the vitamin question.

**The build-trap diagnosis (accept it):** enormous verified depth (signed portable bundles, per-call client attribution, cite-or-abstain retrieval, a real MCP stack) with zero evidence surface. Everything below is ordered by *yes-per-hour*, not by engineering interest.

---

## 1. GROUND TRUTH — every plan claim vs. the code (as of 2026-07-11)

| Plan claim (§) | Ground truth | Status |
|---|---|---|
| §10 P0: "on-device embeddings needed; real semantics need a cloud key" | Model2Vec potion-base-8M shipped, bundled in DMG/MAS, default-on, verified in 0.2.0-2 (`/ready provider=model2vec`) | **STALE — claim the win** (but: potion-8M ≠ bge-class; keep the upgrade as named open work) |
| §10 P1: "hosted multi-device sync designed but unbuilt" | **Push half built + tested + wired**: `/v1/sync/ingest` (main.py), `/v1/sync/captures` (both servers), `CortexPushSync.swift` (bootstrap + learn-nudge), idempotency fields, device registration, `test_sync_push.py` | **STALE — half shipped.** Real gaps: pull leg, tombstone propagation, E2E encryption, hosted redeploy verification |
| §10 P1: "extension pull-only; need SSE push" | Delivery layer + activity long-poll exist; extension still injects on demand | Partially accurate |
| §8/§10: Constellation share card | Zero code (no ImageRenderer/NSSharingService anywhere in macos/Sources) | **Accurate gap → building now (wave 1)** |
| §3/§8: "The Proof" (a different AI already knows you) | Never shown to anyone. Onboarding ends on a **hardcoded fake 8-node constellation**; connect-wizard verify only self-tests Cortex's side — never observes an external AI reading memory. All primitives exist (per-call `token_label` attribution, activity feed) | **Accurate gap → building now (wave 2)** |
| §11: 60-second cross-AI demo | Nothing in scripts/ starts on it; closest artifact drives zero real clients | **Accurate gap → building now (wave 1)** |
| §11: north-star "weekly cross-AI recall" | Never computed anywhere. Instrumentation ~80% (per-call token attribution, scorecard); missing: served-memory-ID logging, the derivation, any headline UI, and the legacy/default/untokened token buckets collapse distinct AIs | **Accurate gap → building now (waves 1–2)** |
| §4 table: "universal reach… `/v1/tools/schema`… SDKs" | Local server only — **hosted api.signindoppl.com has no `/v1/tools` routes**; SDKs unpublished, TypeScript SDK has zero tests; the protocol doc's claimed TS bundle-verifier doesn't exist | **Overstated → wave 3** |
| §8 pricing: "$12 Pro = hosted sync" | Hosted sync stores **raw capture text in plaintext** (bundles are Ed25519-*signed*, not encrypted); local purges don't propagate to hosted/other devices | **Unnamed risk — must go in §14 + roadmap** |
| §4: Mirror/Constellation/cited Ask/vault/ingestion depth | Verified real | Accurate |

---

## 2. THE RANKED BUILD (synthesis of all four judges, ordered by yes-flipping power)

> **STATUS 2026-07-11: all three waves SHIPPED, verified, and pushed** (`49f5aad` wave 1, `ace356a` wave 2, `585505a` wave 3). Full backend suite 1944 passing; macOS `build.sh` green; TS SDK 20/20, Py SDK 19/19; an adversarial review of the wave-3 diff caught 3 real defects in the CLAUDE.md compiler (a HIGH symlink-escape arbitrary-write, a HIGH indented-marker data-loss, a MEDIUM CRLF over-rewrite) — all fixed with regression tests before commit. What remains is **not code**: the 50–200-user experiment in §3, plus the founder-gated items (E2EE sync, SDK publishing, OAuth registration, hosted redeploy).

Execution is 3 waves of parallel, file-disjoint workstreams. **Waves 1–2 are the YC-critical path.**

### Wave 1 — evidence primitives (in flight now)
1. **[A] North-star backend** — served-memory-ID logging (IDs only, content-free) on the tool-call audit path; `cross_ai_recall_headline(user_id, days=7)` → `{distinct_ais, total_recalls, clients[], top_memories[], unattributed_calls}`; `GET /v1/usage/headline` on both servers. *Honesty rule:* legacy shared / default / untokened buckets are **never** counted as distinct AIs — the number must survive 30 seconds of partner questioning.
2. **[C] Constellation share card** — `ImageRenderer` snapshot of the REAL graph (real layout/communities/god-nodes) as a 2:1 social card; Share…/Copy/Save PNG; visible Share button in the map. The tell-a-friend object of the whole PLG loop.
3. **[E] Demo kit** — `scripts/demo_seed.py`: curated persona vault via the real capture path + three distinct per-app tokens ("Claude Desktop", "Cursor", "ChatGPT (extension)") + ready-to-paste configs; `--verify` asserts each label actually performed reads; `--self-test` = headless full rehearsal on a spare port. Plus `docs/DEMO_RUNBOOK.md` (60-second choreography + failure recovery).
4. **[F] Embeddings integrity guard** — release packaging hard-fails if the model2vec bundle is missing; `check_vector_runtime.py --require-model2vec`; `rerank_eval.py --forbid-skip`. The demo's only aha can no longer silently degrade to keyword-hash.

### Wave 2 — the felt product moments
5. **[B] North-star headline card + the Proof moment (Swift)** — new `CortexNorthStar.swift` AppState extension: fetch `/v1/usage/headline`, render "**Your memory was read by N AIs this week — M recalls**" as the top card of Home; watch `/v1/activity` for the first tool-call whose `token_label` is an external AI and surface "**Claude Desktop just read your memory**" live in the onboarding finish step *and* the connect-wizard Done step. Replace the fake 8-node onboarding constellation with the user's real (or seeded) graph.
6. **[D] Pull sync (second device)** — `CortexPullSync.swift` mirroring the push worker (hosted `/v1/sync/captures?after_seq=` → local ingest; separate cursor; sign-out teardown) + `trusted_sync` passthrough so Mac B doesn't re-triage Mac A's approved captures. Makes the $12 Pro tier and the "sign in on a fresh Mac, your memory is there" demo real.

### Wave 3 — diligence-proofing + the wedge repositioning
7. **[G] Hosted developer API** — mirror `/v1/tools/schema` + `/v1/tools/call` into main.py (exporters are server-agnostic); TypeScript SDK tests; CI publish workflow **prepared but not run** (publishing is founder-gated).
8. **[H] CLAUDE.md compiler (new-direction wedge #1)** — "Sync to CLAUDE.md": render the cited profile + active-project memories into a fenced, Cortex-managed block of the user's existing `CLAUDE.md`/`.cursorrules`/`AGENTS.md`, refreshed on memory change. **Meet the ICP inside their revealed workaround** (visible, git-diffable) instead of asking them to trust an invisible retrieval layer; MCP live recall becomes the upsell.
9. **Plan-doc corrections** — fold §1's table into `YC_COMPANY_PLAN.md` (claim the shipped wins, name the plaintext-sync risk in §14, fix §11/§15 "next" lists).

### SHIPPED since (were deferred; now built + reviewed + pushed)
- ✅ **Zero-access E2EE sync** — opt-in; the server stores ciphertext it structurally cannot read (blind relay), the key never leaves the user's devices, recovery-code escrow + offline `cortex-decrypt`. Crypto-reviewed (`1cfe5c6`).
- ✅ **Purge/tombstone propagation** — a local forget reaches the hosted copy + every device; data-loss-reviewed (`0d666ab`).
- ✅ **Memory Wrapped** weekly card (`2b508ae`); ✅ **Limitless/Rewind importer + landing page** (`98b52d4`); ✅ **provable key ownership** (CXE1 spec + offline tool, `f27a589`).

### Still deferred (post-YC or founder-gated)
- New-machine restore (in build), mobile (iOS), bge-class embedding upgrade, teams UX, OpenMemory alias layer, import-diff ("what the AIs think of you"), storage.py refactor.
- Actual SDK publishing, OAuth app registration, MAS provisioning, hosted redeploy — founder actions.

---

## 3. THE EXPERIMENT THAT DECIDES THE COMPANY (not code — do not skip)

The build above produces the demo and the meter. The **meter reading** requires humans:

1. **Recruit 50–200 users by hand** from the two named pools: (a) people who hand-maintain `CLAUDE.md`/`.cursorrules` (search GitHub, X, HN — revealed demand), (b) Rewind/Limitless refugees (privacy-primed, burned, hunting). The Limitless importer + landing page is the honest hook for (b).
2. **Activation bar:** import + wire ≥2 AI tools in the first session (the connect wizard + demo-seed personas make this a 10-minute path).
3. **The number:** week-4 cross-AI recall (N distinct AIs with reads, per user, from `/v1/usage/headline`) — *without the user opening the app*. Users report it themselves (screenshot of the headline card) or via opt-in share; there is no telemetry, and that's a feature, not a gap: "even our traction numbers are user-reported, because we can't see your data."
4. **Decision rule (pre-committed):** median N ≥ 1 with retention → raise mode, apply with the curve. N ≈ 0 → the wedge is wrong; fall back to the CLAUDE.md-compiler wedge ([H]) where usage is visible in git history, and re-run.

---

## 4. WHAT "DONE" LOOKS LIKE FOR THE APPLICATION

- **Demo:** `demo_seed.py` + runbook → live 60-second three-client cited demo, rehearsed, with self-test rehearsal passing in CI.
- **Product:** Home opens on "read by N AIs this week"; onboarding ends on *your real constellation* + a live external-read Proof; wizard Done step shows the first real read; share card exports.
- **Sync:** sign in on a second Mac → memory arrives (pull), honestly labeled as signed-not-yet-encrypted.
- **Diligence:** hosted `/v1/tools` live, SDKs tested + publish-ready, plan doc contains zero claims a partner can falsify against the repo.
- **Traction:** the §3 experiment running with real users before the interview.
