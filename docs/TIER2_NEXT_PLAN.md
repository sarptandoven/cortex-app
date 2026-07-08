# Tier-2 — Next Cases: In-Depth Implementation Plan

**Status of Tier-2 so far (shipped + verified, 1301 backend tests green, pushed both remotes):**
`#5` wikilinks+backlinks · `#6` entity MOC pages · `#8` expand_context+auto-hop · `#9` constellation graph analysis · plus 3 adversarial-review fixes (round-trip data-loss, MOC hash collision, expand_context cite-or-abstain).

This document plans the **next** Tier-2 cases. Theme is unchanged: **turn the vault into a real, navigable Obsidian-grade mind-map, and keep pushing retrieval quality.** Every vault-page case follows the invariants we locked in O5/O6: *lossless round-trip*, *reconcile-invisibility* (machine pages live outside `memories/`), *flag-gated* (`CORTEX_ENTITY_MOC`, desktop-only — hosted bucket vaults share one root), *deterministic* (no timestamp churn).

Ordering at a glance (detail in §7):

| # | Case | Win | Priority | Effort |
|---|------|-----|----------|--------|
| **N1** | Obsidian wikilink **resolution** (aliases/tags) | the links we already ship actually open the right note | **P0 (correctness)** | S |
| **N2** | Human-readable memory filenames | vault browsable by filename, not `mem_<uuid>.md` | P1 | M–L |
| **N3** | Vault "Start Here" Home MOC | one entry-point page when you open the vault | P1 | S–M |
| **N4** | Daily notes (`Journal/`) | timeline browsing, Obsidian daily-note parity | P1 | M |
| **N5** | Obsidian Canvas (`.canvas`) export | the constellation, native in Obsidian Canvas | P2 | M |
| **N6** | Retrieval depth (learned rerank + refinement + 2nd hop) | better answers, feedback-driven | P1 | M–L |

---

## N1 — Obsidian wikilink **resolution** (P0, correctness gap in shipped O5/O6)

### The bug we shipped
Memory notes emit `[[Entity Name]]` in their `## Links` section (`vault_markdown._render_generated_sections`, via `_wikilink(name)`). But the MOC file is named `People/<slug>--<sha1[:16]>.md` and its frontmatter `aliases:` currently holds the *entity's aliases* (e.g. `marcus@acme.com`) — **not its display name**. Obsidian resolves `[[X]]` to a file whose **basename == X** or whose **`aliases:` contains X**. Neither is true, so today `[[Marcus Feld]]` in a memory note lands on an *unresolved* link (or spawns a stray stub) instead of opening `People/Marcus-Feld--<hash>.md`. The graph we built is there in the DB but not clickable in Obsidian.

### Fix (small, high-impact)
1. **MOC frontmatter: add the display name to `aliases:`.** In `storage.build_entity_moc_pages` (storage.py:11239) the page dict already carries `name` + `aliases`. Change the `aliases` we hand to `render_entity_moc_markdown` to be a deduped, sorted union of `[name] + aliases`, so Obsidian resolves `[[Marcus Feld]]` **and** `[[marcus@acme.com]]` to the page.
   - `render_entity_moc_markdown` (vault_markdown.py:~288) already emits `aliases` in `ENTITY_MOC_FRONTMATTER_FIELDS` as `aliases: [...]` → Obsidian reads that key natively. The only change is *what's in the list*.
2. **Memory notes: keep `[[Name]]`** (now resolvable via the alias). No change to `_render_generated_sections`. (Rejected alternative: `[[stem|Name]]` — uglier, and couples memory notes to MOC filenames; the alias approach is the Obsidian-native way and also works when MOC generation is OFF-by-flag → the link simply stays unresolved, which is correct.)
3. **Memory notes: surface topics as Obsidian `tags:` — as a RENDER-ONLY projection.** Emit `tags:` computed from `topics` at render time (slugified: Obsidian tags can't contain spaces/punctuation — `_obsidian_tag(topic)` maps disallowed runs to `-`, drops empty/pure-numeric). **Do NOT persist a `tags` record key.** ⚠️ **Correctness (the subtle one):** `parse_memory_markdown` would read `tags:` back into the record, and `_write_record` (vault.py:1201) only strips `_`-prefixed keys — so `tags` would leak into the JSON source-of-truth and re-render, a divergence. Fix: **strip `tags` on parse** (in the frontmatter loop, `if key and key != "tags"`), making `tags` a pure output projection of `topics`. This keeps the durable record byte-identical and the render→parse→render idempotent — the same round-trip discipline that the CRITICAL review finding taught us. `topics` stays the sole source of truth.

### Files
`backend/app/vault_markdown.py` (MEMORY_FRONTMATTER_FIELDS +`tags`; render emits tags), `backend/app/storage.py` (`build_entity_moc_pages` aliases union; memory `tags` from topics in the save_capture attach block ~9196).

### Tests (`test_vault_markdown.py` / `test_vault_entity_moc.py`)
- MOC page frontmatter `aliases` contains the display name + every alias, sorted/deduped.
- Memory note frontmatter emits `tags:` slugified from topics; round-trip still lossless (content unchanged, `topics` preserved).
- Determinism: same entity → byte-identical aliases order.

### N1 exact edits (from the design pass, verified against code)
- `storage.build_entity_moc_pages` (storage.py:11317-11329): set the page `aliases` to `[display_name] + sorted(dedup(entity aliases + entity_id))`, **name first, tail sorted case-insensitively** (sorted/deduped is mandatory for MOC byte-determinism → no git churn). Adding `entity_id` as an alias also makes the raw-id fallback link `[[ent_cortex]]` resolve.
- `render_entity_moc_markdown` (vault_markdown.py:298): optionally drop the display name from the human "Also known as:" body line (keep it in frontmatter only) so the body reads naturally.

### Risks
- **Round-trip (highest):** the computed `tags:` MUST be stripped on parse (above), or a rebuild persists `tags` into JSON and re-emits → divergence.
- **Determinism:** aliases MUST be sorted+deduped (name-first, tail sorted) — an unsorted/set-derived order churns the MOC file on every render.
- Two entities sharing a display name (two "Alex") both claim `[[Alex]]` → Obsidian picks/prompts. Pre-existing name-ambiguity, not introduced here; the `entity_id` alias gives an unambiguous fallback link.
- Hosted/bucket safety unchanged — aliases/tags ride existing memory + MOC pages; MOC stays flag-gated OFF on hosted.

**Effort: S (½ day). Do this first — it makes O5+O6 actually deliver their promised value.**

---

## N2 — Human-readable memory-note filenames (P1, the deferred Tier-2 #4)

### Goal
`memories/<layer>/mem_8e74cf70b6e9.md` → `memories/<layer>/<summary-slug>--<shortid>.md` (e.g. `chose-sharded-sqlite-over-postgres--8e74cf70.md`). The vault becomes browsable by filename.

### Why it's intricate — every consumer of the filename
`grep` shows the filename `mem_<id>.md` is assumed in **five** places, all in `vault.py`:
- `memory_markdown_path` (483) — the write path.
- `_write_memory_markdown` orphan sweep (495) — `base.rglob(f"{memory_id}.md")` to delete stale notes on layer change.
- deletion/patch helpers at **653, 715, 1275** — `rglob(f"{memory_id}.md")` to find/patch/detect-deletion by id.
- **Rebuild is SAFE**: `iter_memory_markdown_records` (687) globs `memories/**/*.md` and reads the id from **frontmatter** (695), so the filename is free to change.
- **O5 backlinks break**: `_backlinks_for_memory` emits `- [[mem_id]] — label`, which resolves by *basename* today. If the basename becomes `<slug>--<short>`, `[[mem_id]]` no longer resolves.

### Design
1. **Stem:** `vault.memory_note_stem(record)` = `safe_segment(summary or content-first-N) + "--" + memory_id[-8:]` (short suffix keeps it stable + unique; slug is human-readable). Centralize like `entity_moc_short_id`.
2. **Find-by-id, not by-filename:** replace the four `rglob(f"{memory_id}.md")` sites with a `_find_memory_note(memory_id)` helper that globs `memories/**/*.md` and matches on **frontmatter id** (parse-and-check). O(notes) but these paths are already rare (patch/delete/layer-change). Cache is optional.
3. **Backlinks resolve:** two options — (a) emit backlinks as `[[<memory-note-stem>]]` (requires the stem to be computable from just the neighbour's id+summary in `_backlinks_for_memory` — it has both), or **(b, chosen)** add `memory_id` to the memory note's Obsidian `aliases:` frontmatter so `[[mem_id]]` keeps resolving regardless of filename. (b) is far less coupling and also fixes any *external* `[[mem_id]]` reference. Pairs naturally with N1 (we're already touching memory frontmatter for tags/aliases).
4. **Migration:** a one-time `_migrate_memory_note_filenames(user_id)` in `ensure_vault_backfilled` (behind the same care as `_backfill_memory_markdown`): for each `<id>.md`, if the filename isn't already the new stem, write the new file (atomic) + unlink the old. Idempotent; best-effort; a failure leaves the old name (still works — rebuild reads frontmatter).

### Files
`backend/app/vault.py` (stem helper, `memory_markdown_path`, `_write_memory_markdown` sweep, patch/delete/exists helpers, migration), `backend/app/storage.py` (backlink emit if option (a); memory `aliases` add if (b); call migration in `ensure_vault_backfilled`), `backend/app/vault_markdown.py` (`aliases` in `MEMORY_FRONTMATTER_FIELDS` — shared with N1).

### Tests
- Round-trip: write memory → filename is the human stem → rebuild recovers the same id/content from frontmatter.
- Layer change moves the note (no orphan) — the sweep finds the old note by **frontmatter id** even though its filename slug changed.
- Deletion detection: removing the note file (any name) is still seen as a user deletion.
- `[[mem_id]]` still resolves (alias present) — assert `mem_id` in the note's `aliases`.
- Migration is idempotent and renames legacy `<id>.md`.

### Risks (highest first)
- **Deletion mis-detection → data loss:** the mass-delete floor in `reconcile_vault_edits` keys on id-from-frontmatter, which is unchanged — but the *orphan sweep* and *patch* now scan-by-id; a bug that fails to find the note could double-write or fail to clean up. Verify all four sites with tests before shipping.
- Slug collisions (two memories, same summary) — the `--<id[-8:]>` suffix disambiguates; assert.
- Windows path length / illegal chars — `safe_segment` already caps at 80 + sanitizes.

**Effort: M–L (1–1.5 days). Sequence AFTER N1 (shares the memory-frontmatter aliases edit) and verify the delete/patch paths exhaustively.**

---

## N3 — Vault "Start Here" Home MOC (P1)

### Goal
One machine-owned root page, e.g. `Cortex — Start Here.md`, that's the natural entry point when the vault is opened in Obsidian: top hub people/projects (by centrality), the life-areas (community labels), most-recent captures, open loops/tasks, and links into `People/ Projects/ Topics/`.

### Design (mirrors O6 exactly)
- `storage.build_home_page(user_id)` composes: `entity_graph_analysis` (hubs = `ranked[:8]`, `community_labels`), `recent(...)` / `inbox(...)` (recent captures — sources at storage.py:9253/9273), `open_tasks(...)` (10952). Returns a page dict with sections of `[[wikilink]]`s (entity links use the **same `entity_moc_stem`** so they resolve; memory links use the N2 stem / `[[mem_id]]` alias).
- `vault_markdown.render_home_markdown(page)` — deterministic, `cortex_generated: true`, no timestamp.
- `vault.write_home_markdown(page)` — writes to vault root (or a `_Cortex/` folder), **outside `memories/`** so reconcile ignores it.
- Wire into `regenerate_entity_moc` (storage.py:11332) — same flag gate, same hooks (interactive save_capture / rebuild / backfill). Rename that method's intent to "regenerate machine pages" or add a sibling `regenerate_vault_pages` that does MOC + Home (+ N4/N5).

### Tests
Render has the expected sections + wikilinks that match real MOC/memory basenames; deterministic; flag-off → not written; not seen by `iter_memory_markdown_records`.

### Risks
- Determinism vs "recent" churn: "most-recent captures" changes constantly → the file diffs on every capture (git/iCloud churn). Mitigation: cap "recent" to a stable window (e.g. last 7 days) **and** only regenerate on the existing hooks (already debounced to interactive captures), or omit volatile sections from the synced file. Decide per §7.

**Effort: S–M (½–1 day). Rides the O6 hooks; low risk once churn is handled.**

---

## N4 — Daily notes (`Journal/YYYY-MM-DD.md`) (P1)

### Goal
Obsidian daily-note parity: one page per day that had activity, listing that day's captures + memories learned + entities touched, cross-linked. Enables timeline browsing ("what did I learn on the 4th?").

### Design
- `storage.build_daily_pages(user_id, *, days=30)` — group memories/captures by `captured_at[:10]`, **bounded to the recent N days** (avoid unbounded regen over years of history). Each day → a page dict with `[[memory-stem]]` / `[[Entity Name]]` links.
- `vault.write_daily_markdown(page)` → `Journal/<date>.md`, machine-owned, outside `memories/`, deterministic.
- **Regen cost control:** on an interactive `save_capture`, only regenerate the **day(s) touched by this capture** (its `captured_at` day), not all days. Full rebuild/backfill regenerates the bounded window. This keeps the ingest path cheap.
- Flag-gated `CORTEX_ENTITY_MOC` (or a dedicated `CORTEX_DAILY_NOTES` sub-flag if you want it independently toggleable).

### Tests
A capture on date D produces `Journal/D.md` linking that day's memory; a second capture same day updates one file; determinism; bounded window respected; flag-off no-op; reconcile-invisible.

### Risks
- Timezone: `captured_at` is UTC ISO — day boundaries are UTC. Acceptable; document it (or derive local day from a user setting later).
- Unbounded history → bounded-window is mandatory (a 3-year vault must not regen 1000 files per capture).

**Effort: M (1 day). Pairs with N2 (day pages link to memory notes by stem).**

---

## N5 — Obsidian Canvas (`.canvas`) export of the constellation (P2)

### Goal
Write `Constellation.canvas` so the entity graph is viewable **natively in Obsidian Canvas** (spatial, zoomable) — a "wow" artifact that reuses the O9 analysis.

### Design
- `.canvas` is documented JSON: `{"nodes":[{id,type:"file"|"text",x,y,width,height,color,file?,text?}], "edges":[{id,fromNode,toNode,...}]}`.
- `storage.build_constellation_canvas(user_id)` maps `build_entity_graph` + `entity_graph_analysis` → canvas: node per entity, **deterministic position** by community cluster (angular offset per community) + centrality (radius), `width/height` sized by centrality, `color` (1–6) by community, `type:"file"` linking to `People/…md` when that MOC exists else `type:"text"` with the label; edges from graph edges (bridge edges get a distinct color).
- `vault.write_canvas(name, obj)` → `Constellation.canvas` at root, machine-owned, outside `memories/`. `.canvas` isn't Markdown so no parse/round-trip — but must NOT be picked up by reconcile (it isn't: reconcile globs `memories/**/*.md`).
- Flag-gated with the vault-pages flag; regenerated on the same hooks; deterministic (no timestamp).

### Tests
Valid JSON with the documented keys; node count == graphed entities; positions deterministic across two builds; `file` links point at real MOC basenames; flag-off no-op.

### Risks
- Positioning/overlap on large graphs — reuse the deterministic radial+community-centroid scheme from `MemoryMapView`, clamp to a canvas bound. Cosmetic, not correctness.
- Canvas format drift across Obsidian versions — stick to the stable documented subset.

**Effort: M (1 day). Highest "delight per line", lowest correctness risk. Good to bundle with N3/N4.**

---

## N6 — Retrieval depth: learned rerank + signal refinement + optional 2nd hop (P1)

The **highest-leverage non-vault** case: the reranker + query-plan + one-hop are live; this is the next quality increment. Each sub-item is flag-gated, no-ops under the `hash` embedder, and must not regress `scripts/retrieval_eval.py` (flag off = byte-identical; flag on ≥ baseline).

### N6a — Offline learned rerank weights (the seam already exists)
`storage.py:204-227` already loads `RERANK_WEIGHTS` (`{"sem","rank","ent"}`, default `{0.6,0.25,0.15}`) from `CORTEX_RERANK_WEIGHTS_PATH`, and a comment references `scripts/learn_rerank_weights.py`. So the *consumption* seam is done — build the **learner**:
- `scripts/learn_rerank_weights.py`: read logged `retrieval_feedback` events (`_log_retrieval_feedback`, storage.py:10022 — already logs cited vs retrieved-but-skipped), form pairwise (cited > skipped) training pairs, fit `{sem,rank,ent}` via **pure-python pairwise logistic/hinge gradient descent** (no numpy). Emit the JSON only if it **beats baseline on `retrieval_eval.py`** (gate in the script). Ship the JSON with the app; `CORTEX_RERANK_WEIGHTS_PATH` points at it.
- Deterministic seed; the eval gate is the acceptance test.

### N6b — Signal refinement (per-layer decay + entity-overlap boost)
- In `_rerank_rows` / `_fuse_search_rows`: add per-layer **exponential temporal decay** params (replace coarse recency bins), amplitudes **capped at today's max** so decay only *refines* tie-breaking, never overrides relevance. Add an `_entity_overlap_boost` (query entities ∩ candidate entity_ids). Flag `CORTEX_SIGNAL_REFINE`.
- Prove with new graded-relevance cases in `retrieval_eval.py` (add nDCG@k/MRR if not already present).

### N6c — Optional bounded SECOND hop (deep relational chains)
- Reuse the O8 hop machinery (`_context_hop_queries`, the re-gate) for a **second** hop on still-`low_confidence` after hop 1, with a **stricter** budget (tighter deadline, 1 query). Flag `CORTEX_CONTEXT_HOP_DEPTH=2`. Same cite-or-abstain guarantees. Only worth it if eval shows lift on multi-hop cases.

### Files
`scripts/learn_rerank_weights.py` (new), `backend/app/storage.py` (`_rerank_rows`/`_fuse_search_rows`/`answer_query` hop-depth), `scripts/retrieval_eval.py` (graded cases + gate), `backend/app/config.py` (flags/params).

### Risks
- **Never regress the gate** — every sub-flag must be proven off=identical, on≥baseline before its default flips. N6a must not ship weights that lose to `{0.6,0.25,0.15}`.
- Feedback data volume: early on there's little logged feedback → the learner must fall back to defaults gracefully (it does: the loader defaults when the path is unset/empty).

**Effort: M–L (1.5 days for a6+b; +½ day for c). Independent of the vault cases — can run in parallel with them.**

---

## §7 — Sequencing, coordination, ROI

### Recommended order
1. **N1 (P0, S)** — first: it fixes a correctness gap in *already-shipped* work (the wikilinks don't navigate). Cheap, high impact.
2. **N2 (P1, M–L)** — right after N1 because it shares the memory-frontmatter `aliases` edit and its backlink-resolution fix *depends on* N1's alias mechanism. Verify the delete/patch/orphan paths exhaustively (highest data-loss risk of the batch).
3. **N3 + N4 + N5 (P1/P2, S–M each)** — the "vault pages" trio. They all wire into the **same** `regenerate_entity_moc` hook + flag + reconcile-invisibility discipline, so build them together behind a shared `regenerate_vault_pages` method to avoid touching the hook three times. N5 (Canvas) is the delight artifact; N3 (Home) the daily-use entry point; N4 (Journal) the timeline.
4. **N6 (P1, M–L)** — independent of the vault; can run **in parallel** with 1–3 (touches `storage.py` search/rerank region, disjoint from the vault render/regen region).

### The one cross-train dependency (from the design pass)
N1 and N2 both edit `_render_generated_sections`, but **different blocks**: N1 owns the **Links** block (keep `[[Name]]`, now alias-resolvable), N2 owns the **Backlinks** block (rewrite `- [[mem_id]]` → `- [[stem|label]]` once memory filenames change). So N1 then N2, each editing its own block — never ship N1's `[[mem_id]]` and then have N2 immediately rewrite it. And **N3/N4/N5 inherit N2's filename contract**: their `[[memory]]` links must consume N2's `memory_note_stem` helper, not a bare id — so N2 precedes the batch. N6 has no such coupling (parallel from day 1).

### Shared-file coordination
- `vault_markdown.py`: N1 (+N2) edit `MEMORY_FRONTMATTER_FIELDS`/`_render_generated_sections` and add render helpers; N3/N5 add `render_home_markdown`/canvas (pure appends). Land N1's frontmatter edit first, then N2's backlink-target rewrite.
- `vault.py`: N2 edits the memory path + four glob sites; N3/N4/N5 add `write_home/write_daily/write_canvas` (pure appends, no collision with N2).
- `storage.py`: N1/N2 edit the `save_capture` attach block + `build_entity_moc_pages`; N3/N4/N5 add `build_home_page`/`build_daily_pages`/`build_constellation_canvas` + extend the regen hook; N6 edits the `search`/`_rerank_rows` region (disjoint). Serialize the regen-hook edit (N3–N5) behind one `regenerate_vault_pages`.
- Re-anchor every insertion on a `def <name>` landmark, not a line number (they drift), and `py_compile` + targeted tests between cases — same discipline that caught the O9 `unquote` regression.

### ROI ranking
1. **N1** — turns the whole O5/O6 investment from "data in the DB" into "a clickable graph in Obsidian." Do it regardless.
2. **N6a** — feedback-driven ranking is the compounding retrieval win; the seam already exists.
3. **N3 + N5** — the vault's first-open experience (Home + Canvas) is what makes users *feel* the mind-map.
4. **N2 + N4** — real polish (browsable filenames, timeline), slightly more work/risk.

### Highest-risk-first per case
- N1: alias dedupe/casing (test).  · N2: delete/patch/orphan find-by-id (the data-loss vector — test all four sites).  · N3: recent-section git churn (bound the window).  · N4: unbounded-history regen (bounded window mandatory).  · N5: none load-bearing (cosmetic positioning).  · N6: the eval gate (off=identical, on≥baseline) before any default flips.

### Executive summary (one paragraph)
We've built the graph; **N1 makes it clickable** (a P0 correctness fix so the `[[links]]` we already write actually open the right note in Obsidian). Then **N2/N3/N4/N5** turn the folder into a real Obsidian vault a person *wants* to open — human-readable filenames, a "Start Here" home page, a daily journal, and a native Canvas view of the constellation — all reusing the flag-gated, reconcile-invisible, deterministic machinery from O6, so they're low-risk and desktop-only. In parallel, **N6** compounds retrieval quality with feedback-learned rerank weights (the loading seam already exists), signal refinement, and an optional deeper hop, each gated behind the retrieval-eval floor. Total ≈ 5–6 focused days; N1 first (½ day, ship immediately), N6a next-best ROI, the vault-pages trio bundled behind one regen hook.
