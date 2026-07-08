# Doppl/Cortex — Product Roadmap (data-in → vault/mindmap → external retrieval)

_Generated 2026-07-07 by a 4-stage code-grounded assessment; every anchor claim verified against the source. Companion to docs/SECURITY_REVIEW.md._

All anchor claims are confirmed against the code. Every load-bearing claim from the four assessments checks out:

- **Stage 4 blocker**: `endpoint = base` (CortexCloudAuth.swift:441) + `vault_path` local path POST (CortexApp.swift:4679) + "Using remote memory engine" short-circuit (2401) → confirmed dead-end. Onboarding copy "there is no account to create" (OnboardingView.swift:291) contradicts the forced sign-in wall.
- **Stage 3**: `CORTEX_RERANK` defaults to `"off"` (storage.py:18521), app keeps it off (CortexApp.swift:2549). No `expand_context` tool exists. `answer_query` computes `missing_fields` (10108-10119) but only downgrades — never hops. MCP protocol versions diverge (main.py has no `2025-06-18`/resources/prompts; standalone has all). `delivery.py` is 118 lines, webhook-only.
- **Stage 1**: No fuzzy/coreference entity merge anywhere (grep empty). Local extraction gated on `ANTHROPIC_API_KEY` (221) falls to literal phrase lists like `"i tend to"` (942).
- **Stage 2**: `render_memory_markdown` emits no wikilinks/backlinks; no `write_entity_markdown` sibling.

I have enough grounded evidence to synthesize. Here is the unified roadmap.

---

# Doppl/Cortex — Unified High-Impact Roadmap (data-in → organized vault/mindmap → excellent external retrieval)

## The honest headline

**The product is ~80% built and ~40% reaching a real user.** Three of the four stages have deep, tested, well-engineered machinery. But the value is gated behind a small number of switches that are OFF or wires that are crossed — and the very first end-to-end step (a signed-in user connecting notes) is architecturally broken. **The highest-leverage work is almost entirely "turn on / connect / surface what already exists," not "build new."** That is a good problem to have.

What's genuinely strong (don't touch, don't rebuild):
- **Ingestion** — the strongest surface. ~2950-line `source_ingest.py` with parsers for ChatGPT/Claude/6 AI transcripts/Slack/Discord/iMessage/Twitter/LinkedIn/mbox/PDF/DOCX, plus live OAuth connectors (Gmail, Drive, Notion, Slack, GitHub Device Flow, etc.), cursors, dedup, resumable import, proactive export discovery.
- **Vault durability spine** — crash-safe atomic writes, two-way file-over-app reconcile, mass-delete floor, GDPR scrub, ZIP backup with secrets deny-list. This is production-grade.
- **Deterministic graph core** — `graph_analysis.py` centrality/communities/bridges with stable tie-breaks; `person_map`/`entity_neighborhood` return cited structure.
- **Retrieval machinery** — hybrid BM25+vector fusion, a fully-built reranker with learnable weights + MMR, query planning, cite-or-abstain gate. 75 MCP tools projected to OpenAI/Anthropic/OpenAPI, browser extension, SDKs.

The gap is not capability. **The gap is that this machinery is switched off, disconnected, or invisible to the user.**

---

## TIER 0 — The one blocker that makes everything else moot (do first, this week)

### 0. Fix the required-account data plane: keep data LOCAL, use the account for identity only
**Impact: critical · Effort: M · This is the gate on all end-to-end value.**

Today `CortexRequireAccount=true` is LIVE in Info.plist, so every fresh user is forced through the sign-in wall. On sign-in, `applySignedInSession` sets `endpoint = base` (→ `api.signindoppl.com`), and `ensureRunning` then returns "Using remote memory engine" — the local backend at 127.0.0.1:8766 is never started. But `loadSampleNotes` and `syncLocalNotesFolder` POST a **local Mac filesystem path** as `vault_path` to the remote's `/v1/connectors/obsidian/sync`, which runs against the *server's* disk → `FileNotFoundError`. **Net: a signed-in first-run user connects their notes and gets nothing. Ask, Constellation, and profile stay empty.**

Fix: in `applySignedInSession`, do **not** switch `endpoint` to the remote for the memory API — keep `endpoint = localEndpointDefault` and attach the cloud identity as a header/claim. The account becomes a gate + future-sync anchor; all connectors, sample notes, Ask, profile, and graph keep working on the already-solid local-first path.

**Unlocks:** literally every other item below. Until this is fixed, reranking, wikilinks, entity resolution, and multi-hop all improve a vault that a real (signed-in) user never populates. **Nothing else on this list matters if item 0 isn't done.**

*(Merge note: this is the single most important fix in the entire synthesis — Stage 4's top idea. All Stage 1/2/3 improvements are dark for shipping users until it lands.)*

---

## TIER 1 — Quick wins: flip switches / tell the truth (days each, outsized payoff)

These are the highest impact-per-effort items. The code exists; the work is turning it on or correcting a string.

### 1. Flip `CORTEX_RERANK` on (behind a real graded-relevance gate)
**Impact: very-high · Effort: M (mostly the eval, code is done) · Quick-win-adjacent.**

The reranker (`_rerank_rows`) is fully implemented — model2vec cosine + entity overlap + rank-prior blend with learnable weights + MMR dedup — and ships **`"off"` by default** for 100% of users. It's the single strongest relevance signal, dark. The only blocker is an unproven-lift concern. Extend `rerank_eval.py` with nDCG@k/MRR + graded relevance to assert ON ≥ OFF, then set `CORTEX_RERANK=linear+mmr` in the app alongside the already-on QUERY_PLAN/TEMPORAL_DECAY flags.
**Unlocks:** the plan's headline retrieval improvement actually reaching users. Best quality-per-effort in the retrieval stage.

### 2. Fix the onboarding copy that lies to the user
**Impact: high · Effort: S (a string + a flag) · Pure quick win.**

OnboardingView.swift:291 tells a user who was *just forced through a sign-in wall*: "nothing is uploaded, and there is no account to create." Line 306: "No account needed." This is a trust-destroying contradiction and a plausible App Store privacy-misrepresentation rejection. Gate the copy on `accountRequired` so the local-only DMG keeps its original honest wording and the account build gets an honest account-aware story.
**Unlocks:** trust at the exact moment it's being established; removes a review risk. Trivial to do, do it now.

### 3. Import "what happened" report — surface the silent filters
**Impact: high · Effort: S · Quick win.**

Fair-cap, boilerplate filter, and dedup all run **silently**. When organization looks sparse, the user can't tell why or fix it. After each import/sync show: records found vs memories kept, per-source counts, items dropped (with a peek), truncated files — with one-click "keep dropped" / "merge these." Builds on existing `analyze_sources`/import-session tracking + the review queue.
**Unlocks:** the trust/feedback loop that makes "get my data in" *feel* like it worked. Directly closes Stage 1's visibility gap.

### 4. Human-readable memory note filenames
**Impact: high · Effort: S · Quick win.**

The vault is sold on "open it in Obsidian," but a browsing user sees `memories/semantic/mem_a1b2c3.md` — a directory of hashes. Name files `<YYYY-MM-DD>-<title-slug>-<shortid>.md`, keep the id in frontmatter as the stable key, and reuse the existing layer-change de-orphan logic for retitles. Low risk (id stays the durable key).
**Unlocks:** the human-browsing experience the vault's core pitch depends on.

---

## TIER 2 — Bigger bets that complete a broken promise (weeks each)

### 5. Write real `[[wikilinks]]` + Backlinks into every memory note
**Impact: very-high · Effort: M · Bigger bet, huge payoff.**

Cortex **parses** Obsidian wikilinks/canvas on ingest but **never produces them**. `render_memory_markdown` emits frontmatter+body only. Open the Cortex vault in Obsidian today and you get a flat pile of `id.md` files with an **empty graph view and no backlinks** — the exact opposite of what's promised. Append a `## Links` section ([[Entity]]/[[Topic]] from `entity_ids`) + a `## Backlinks` block (co-mention neighbors), stripping/re-deriving on parse so two-way editing stays lossless.
**Unlocks:** the vault's #1 selling point ("open it in Obsidian"). Obsidian's graph view, backlinks pane, and unlinked-mentions all light up for free. Pairs directly with #6.

### 6. Generate per-entity / per-topic Markdown pages (People/, Projects/, Topics/ MOCs)
**Impact: very-high · Effort: M · Bigger bet; pairs with #5 and depends on #7.**

The graph's richest structure — hub people, communities, the web around a person — is **JSON-only and invisible** in the folder the user browses (`write_entity` writes `.json`; there's no `write_entity_markdown`). Emit auto-maintained MOC pages with frontmatter (kind, centrality, community, supporting-count) and a body of [[wikilinks]], mirroring `person_map`/`entity_neighborhood`. Mark `cortex_generated:true` so reconcile ignores regenerated pages.
**Unlocks:** a durable, portable, offline "who/what is in my world" index; Obsidian clusters by these hubs. Together, #5 + #6 turn "a folder of frontmatter notes" into an actual knowledge base. **Note the dependency:** these pages are only as good as the entity graph underneath — which fragments without #7.

### 7. Entity resolution / alias-merge layer (the single biggest *organization* defect)
**Impact: very-high · Effort: M · Bigger bet; a prerequisite for 6, and it upgrades the whole graph.**

`extractor.py` derives entity id from a lowercased slug; `_save_entity` upserts by exact id; grep confirms **no fuzzy/coreference/alias merge exists anywhere.** "Marcus," "Marcus Feld," "marcus@acme.com," "Marcus F." become **four nodes.** This fragments the constellation, person-map, profile ranking, and `about_entity` — the exact "well-organized memory" the product promises. Resolve before `_save_entity` via exact+normalized name, email/local-part, subset/superset name, and optional embedding cosine; keep an `aliases` list (the table already has `aliases_json`). Add a user-facing merge/split action + surface high-similarity merge *suggestions*.
**Unlocks:** cleaner data for *everything downstream for free* — graph, MOC pages (#6), retrieval entity-overlap boosts, MCP entity tools. This is the load-bearing organization fix; **do it before #6** so the pages aren't built on fragmented nodes.

*(Merge note: Stage 1 and Stage 2 both independently named entity resolution their top/near-top idea. It's one item, and it's the organization counterpart to item 0's activation fix.)*

### 8. `expand_context` tool + one bounded auto-hop on low-confidence
**Impact: very-high · Effort: M · Bigger bet.**

`answer_query` already computes exactly *which* fields are missing (`missing_fields`, storage.py:10108) but does nothing except downgrade to `low_confidence` — the internal `entity_neighborhood`/`_related_memory_rows` helpers exist but are never used to recover. Add an `expand_context` MCP tool (cited neighborhood) and, on `low_confidence`, fire ONE bounded hop (search the missing entities, union candidates, re-run the citation gate) before abstaining.
**Unlocks:** turns many silent abstentions into cited answers — precisely the relational-question failure mode a user hits in Claude/Cursor. This is where "excellent retrieval" is won or lost. **Synergy: entity resolution (#7) makes the hop land on canonical nodes instead of fragments.**

### 9. Feed the constellation UI the graph analysis it already computes
**Impact: high · Effort: M · Bigger bet, mostly front-end.**

The backend produces god-nodes, communities, and bridges deterministically — and `/v1/graph` → `MemoryMapView` **throws it away**, laying out by importance+edge-weight only. The flagship "Your Constellation" is a decorative starfield. Extend `/v1/graph` (or point the map at `/v1/person-map`) to include centrality/community/bridges; color by community, size by centrality, cluster, highlight bridges, add tap-to-drill (`entity_neighborhood`) + search/filter.
**Unlocks:** the mindmap becomes an actual insight tool instead of decoration. High-leverage front-end win on already-computed signal. **Depends on #7** (communities are only meaningful once entities are canonical).

---

## TIER 3 — Complete the loop & harden (important, sequence after Tier 1-2)

### 10. Pre-sign-in value story + first-source "aha" moment
**Impact: very-high (activation) · Effort: M · Depends on item 0.**

The first thing a brand-new user sees is a bare sign-in wall with one sentence; the narrative onboarding is *suppressed until after sign-in* — asking for account creation before showing any value (classic top-of-funnel drop-off). Show a 2-3 panel "what Cortex is" intro + let "Explore with sample notes" run in a **local read-only preview before account creation**, then present sign-in as "save your archive." After the first sync, drive a visible count-up ("Distilled 42 memories from 9 notes") into the user's *actual* Constellation + a pre-filled cited Ask.
**Unlocks:** conversion of curious installers into activated users. **Strictly blocked by item 0** — the sample path must actually ingest first.

### 11. Guided "connect your first AI tool" finish with live confirmation
**Impact: high · Effort: L · The product's actual payoff loop.**

The whole promise is "your memory, in your AI tools," but the final onboarding beat is passive homework ("Open Connections… nothing is required to finish"). Detect an installed client (Claude Desktop/Cursor), one-click write its MCP config, and show a success check when the tool's first authorized read hits the audit log.
**Unlocks:** the difference between "installed" and "activated + retained" — the promised end state of the entire product.

### 12. Make high-quality extraction the default without a cloud key
**Impact: high · Effort: M.**

The LLM extractor only runs when `ANTHROPIC_API_KEY` is set (extractor.py:221); a shipped local-first app with no key falls to literal phrase lists (`"i tend to"`, line 942). Most installs won't have a key, so the **default** experience is brittle keyword bucketing. Use the already-bundled model2vec embedder to drive nearest-prototype memory-type classification + near-duplicate fact collapse at ingest. Keep deterministic as floor, Claude as ceiling — raise the no-key baseline.
**Unlocks:** materially better memory typing + less cross-source redundancy for the majority (no-key) install, with no new network dependency.

### 13. Extract the shared MCP dispatcher; bring the hosted server to parity
**Impact: high · Effort: M.**

The two servers have **already diverged**: `main.py` advertises only `2025-03-26`/`2024-11-05` with **no resources/prompts**; `standalone_server.py` has `2025-06-18` + full resources/prompts. A user on the hosted path gets a strictly weaker MCP surface, and every future MCP change must be double-maintained. Route both through one `handle_jsonrpc` dispatcher + add a parity test.
**Unlocks:** the coherence invariant the plan called load-bearing; stops silent drift.

### 14. Complete the delivery layer into a usable daily-brief + open-loop push
**Impact: high · Effort: L · Largest genuinely-unbuilt surface.**

`delivery.py` is 118 lines, webhook-only — no scheduler, no event trigger, no consent UI, no extension push. The "Cortex proactively delivers into your tools" promise is one-directional. Add a scheduler (daily cited brief via `assemble_context` redaction path), an event trigger (new decision/open-loop), a persisted push-target model with consent/preview/revoke, audit via `record_agent_event`, and the extension SSE channel.
**Unlocks:** the "feels alive" bidirectional promise. Biggest new build here — sequence after the switch-flips deliver value.

### 15. Model2vec-tier end-to-end quality gate + query-plan constant tuning
**Impact: high · Effort: M+S.**

CI green today certifies the **hash-embedder** path where every semantic feature no-ops — so there's **zero automated evidence** the retrieval a real user gets under model2vec is good. Run `context_pack_eval`/`agent_task_eval` under bundled model2vec with an nDCG/MRR floor. Also replace guessed constants (`_SEMANTIC_OVERRIDE_FLOOR=0.50`, `mmr_lambda=0.3`) with swept values, and semantically dedup the merged sub-query set (reuse `_rerank_rows` cosine) so compound questions don't flood top-k with near-duplicates.
**Unlocks:** confidence to flip #1/#8 and a regression net for the *real* path. **This is the safety rail that makes items 1 and 8 shippable rather than a leap of faith — schedule it alongside them.**

---

## Sequencing / dependency summary

1. **Item 0 first, alone** — nothing ships value until signed-in users can ingest. Hard gate.
2. **Tier 1 (items 1-4) in parallel** — pure switch-flips and string fixes; ship within the week after 0. (Item 1 pairs with the eval work in 15.)
3. **Entity resolution (#7) before MOC pages (#6)**; #5+#6 ship together as the "real Obsidian vault" bet; #7 also silently upgrades #8 and #9.
4. **#8 + #15 together** — never flip retrieval behavior (1, 8) without the model2vec gate underneath it.
5. **Activation loop (#10, #11) after item 0** — #10 is blocked by 0; #11 is the retention payoff.
6. **#13, #14 last** — coherence hardening and the largest new build; valuable but not gating.

## What is honestly *already excellent* and needs nothing
Ingestion breadth/robustness, vault durability + two-way reconcile + GDPR/backup safety, the deterministic graph-analysis math, the reranker/query-plan/cite-or-abstain *implementations*, and the universal tool-schema projection. The work ahead is overwhelmingly **connect, switch on, and surface** — not build.

---

Relevant files (all absolute):
- `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/macos/Sources/CortexCloudAuth.swift` (item 0: `applySignedInSession` endpoint switch, line 441)
- `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/macos/Sources/CortexApp.swift` (item 0: `ensureRunning`:2401, `loadSampleNotes`:4679, `syncLocalNotesFolder`:6041; item 1 flag:2549)
- `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/macos/Sources/OnboardingView.swift` (item 2: copy at 291/306; item 10)
- `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/backend/app/storage.py` (item 1: `_rerank_rows`:18517 / default off:18521; item 8: `answer_query`:9884, `missing_fields`:10108; item 7: `_save_entity`)
- `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/backend/app/extractor.py` (item 7: entity id/slug; item 12: `_extract_locally`:346, key gate:221, phrase lists:935)
- `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/backend/app/vault_markdown.py` (item 5: `render_memory_markdown`:89)
- `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/backend/app/vault.py` (item 6: `write_entity`:501, needs markdown sibling)
- `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/backend/app/main.py` vs `standalone_server.py` (item 13: MCP protocol divergence, 3309 vs 369)
- `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/backend/app/delivery.py` (item 14: 118-line webhook-only stub)
- `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/backend/app/graph_analysis.py` + `MemoryMapView.swift` (item 9)