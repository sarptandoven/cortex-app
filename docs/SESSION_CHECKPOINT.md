# Session checkpoint — 2026-07-09

Resume point for the phased expansion roadmap (see docs/EXPANSION_ROADMAP_PHASES.md).

## Done and committed
- Phase 2: pinned context packs — `11d2bd3`
- Phase 3: provenance ledger & hygiene — `a676ad4`
- Phase 4: personal eval harness — `10477e9`
- Phase 5: delegate/twin — `e8e76ef`
- Phase 6: anticipatory context — `1969ebf`
  - 6.1 contradiction interrupts: post-transaction detection in save_capture (interactive
    captures only; bulk imports excluded), trust-gated (>=0.6 existing side), head-token
    agreement tolerance so restatements never alert; delivery via warnings[] injected in
    agent_payload, exactly-once (pending -> delivered)
  - 6.2 prefetch predictor: predict_next_pack (most-recent baseline, host-scoped variant
    via agent_sessions join; recency from 'pinned' events since re-pins are INSERT OR
    IGNORE), record_prefetch_outcome + get_prefetch_hit_rate metrics
  - 6.3 annoyance budget: proactive_alerts_daily_budget setting (default 3, clamp 0-20),
    suppression logged as events, dedupe by deterministic alert id, dismissal-as-label,
    get_alert_precision headline metric
  - New table proactive_alerts (Phase 0-style idempotent guard)
  - MCP: get_proactive_alerts (read), resolve_proactive_alert (write)
  - REST: GET /v1/alerts, /v1/alerts/precision, /v1/prefetch/hit-rate,
    POST /v1/alerts/{id}/resolve (both servers)
  - tests/test_phase6_anticipatory.py: 19 tests incl. replayed 12-request usage simulation
    (hit-rate >= 8/12 gate, beats no-predictor baseline)

- macOS UI for phases 4-6 — `e81cfd9`
  - New TwinAlertsModels.swift: Codable mirrors of alert/twin/metrics JSON (field names
    match backend snake_case exactly), TwinQuestionDetector (prefix-gated: "would i",
    "should i", "do i", "am i", "will i", "how would i")
  - New TwinAlertsViews.swift: Review alert cards (NEW/KNOWN claim rows, Dismiss/Noted,
    contradiction-only subtitle + arrows icon, bell for other kinds), Review twin grading
    (Wrong / Can't say / Right), Ask twin prediction card (For/Against/HARD LINES buckets,
    constraint rows dedupe against Against, muted spine + honest abstain copy on
    insufficient_evidence), Model twin scorecard (em-dash accuracy until graded),
    Connections "Activity & alerts" (per-host usage, precision + prefetch tiles with
    em-dash placeholders, budget stepper 0-20 with OFF state)
  - Wiring: Review reload refreshes alerts+grading, Ask consults twin only for
    twin-questions (best-effort), Model gates scorecard on predictions > 0, budget
    stepper persists via PATCH /v1/settings (proactive_alerts_daily_budget added to
    FastAPI SettingsResponse/UpdateRequest)
  - tests/test_macos_phase_ui_contract.py: 21 tests + 50 subtests pin Swift<->backend
    parity (endpoint paths, resolution labels, grade outcomes, Codable fields, prefix
    gating, theme guardrails: gold never text, kerned stamps, max weight semibold)
  - Validated: build.sh green; payload decode parity via swiftc against live-server JSON
    (likely_yes / likely_no / insufficient_evidence, alerts with+without detail);
    rendered previews reviewed for all four surfaces

- Release build 21 shipped — `ea62d35` + `10ddfe9`
  - CFBundleVersion 21, CORTEX_BUNDLE_PYTHON=1 build, DMG delivered to
    ~/Downloads/Cortex-0.2.0-21.dmg (82M), live-verified: alerts/precision/hit-rate/
    twin scorecard/eval scorecard endpoints, MCP catalog 81 tools (13 phase tools),
    budget round-trip, honest would-i abstain
  - Window-frame fix: corrupt autosaved frame (3400pt tall) passed intersection checks
    but never rendered; added absurdlyLarge clamp (>125% of largest screen union)

- Phase 2b: recompute-verify diagnostic — this commit
  - storage.verify_context_pack: integrity check first (tampered = error, never a
    verdict), engine_mismatch when CONTEXT_ENGINE_VERSION differs (diff would only
    measure the version bump), else recompute with stored inputs + as_of pinned to
    the recorded effective moment; statuses match/drift with per-field, per-layer
    memory-id diff (_pack_recompute_diff, envelope excluded); honest caveats;
    recompute_verified audit event
  - assemble_context gains record_reuse flag: diagnostic recomputes never feed the
    prefetch/reuse signal; pack resolution records as_of_effective for exact replay
  - filters.as_of null-echo normalized before diffing (else phantom drift on
    as_of-less packs)
  - MCP: verify_context_pack (maintenance-scoped diagnostic, catalog-only surface)
  - REST: POST /v1/context/packs/{sha}/verify on both servers, maintenance scope in
    both _required_api_scope maps (parity with the MCP tool), PermissionError -> 403,
    ValueError -> 404
  - tests/test_phase2b_recompute_verify.py: 13 tests (match/drift/engine_mismatch/
    tampered, as_of pinning, reuse-signal isolation, event emission, no new pins,
    scope + trust-gate discipline, user scoping, advertised-surface gating)
  - Contract tests: FastAPI + standalone verify routes (scope refusal, trust gate,
    404 mapping); token registration upserts by label — scoped test tokens need
    distinct labels

- Phase A: bidirectional Obsidian (cited write-back pages) — this commit
  - Pattern borrowed from TencentDB-Agent-Memory (pattern, not code): the top of the
    memory pyramid should be human-readable Markdown living in the user's own vault
  - New obsidian_writeback.py: pure renderers (render_readme / render_profile_page /
    render_person_page), no timestamps in bytes so idempotency = byte-equality;
    cited-or-silent (every bullet carries its `mem_` id); cortex_generated: true
    frontmatter marker = machine ownership; parse_generated_marker fails safe to
    "not generated" (never touch a user's file on a parse doubt)
  - storage.write_obsidian_pages: writes Cortex/README.md, Cortex/Profile.md,
    Cortex/People/<slug>--<sha>.md into the connected vault (or explicit vault_path);
    only marker-verified files are written or pruned (user file with the same name →
    skipped_unowned); byte-equal renders skip (no mtime churn for git/iCloud);
    stale generated People pages pruned; obsidian_writeback audit event
  - Connector: _records_for_note returns [] for cortex_generated notes — Cortex never
    re-ingests its own distillate (marker-based, so a user note dropped inside
    Cortex/ still ingests)
  - MCP: write_obsidian_pages in EXPORT_TOOLS (memory egress into user-owned files);
    _tool_annotations gains writes_external_files so it is NOT advertised readOnlyHint
    despite living in export scope
  - REST: POST /v1/connectors/obsidian/write-back on both servers, export scope in
    both _required_api_scope maps, allow_agent_exports trust gate, ValueError -> 422
  - tests/test_obsidian_writeback.py: 15 tests (pages + citations, idempotent rerun,
    corpus-change rewrite, unowned-file safety, marker-only pruning, connected-vault
    default, ingestion exclusion + full sync/write-back round trip, scope + trust
    gate + readOnlyHint discipline, audit event); contract tests on both servers

- Phase B: session harvesting — the user's own agent prompts become memory — this commit
  - The user already explains themselves to Claude Code / Codex / Cursor every day;
    those transcripts sit on local disk and evaporate. Harvest the USER'S OWN messages
    (never agent replies) into the review pipeline.
  - New connectors/agent_sessions.py: scan_agent_sessions over three local formats —
    Claude ~/.claude/projects/*.jsonl (type=user, skip isMeta/isSidechain), Codex
    ~/.codex/sessions/**/rollout-*.jsonl (event_msg/user_message, subagent rollups
    excluded), Cursor workspaceStorage/*/state.vscdb (sqlite ItemTable
    aiService.prompts, opened read-only). User words only; agent output would launder
    model prose into first-party evidence (same principle as the write-back exclusion)
  - Noise gates: <-prefixed command/env wrappers dropped, sub-40-char steering
    ("continue", "yes") dropped, 1MB line cap (embedded tool dumps), 8k message cap.
    Incremental by file-mtime high-water mark; truncation never advances the HWM past
    a partially-emitted file; stable external ids (agent:session:anchor) make re-scan
    a dedupe no-op
  - storage.sync_agent_sessions: harvested prompts are review-gated by default (raw
    self-explanation, not curated notes); NEVER archive-missing (memory must OUTLIVE a
    rotated log); records a source account + cursor like any connector
  - MCP: sync_agent_sessions in WRITE_TOOLS + DIRECT_CONNECTOR_SYNC_TOOLS, agent-list
    arg only (no directory-override — an MCP client can't point the scanner at
    arbitrary paths)
  - REST: POST /v1/connectors/agent-sessions/sync on both servers (write scope,
    AgentSessionsSyncRequest with no path fields, invalid agent / out-of-range bound
    -> 422)
  - tests: test_agent_sessions_connector.py (12: per-format harvest, agent/meta/
    sidechain/subagent exclusion, content-block join, read-only Cursor open, stable
    ids, incremental cursor, truncation HWM hold, corrupt-file-is-error, unknown
    agent reject), test_agent_sessions_store.py (7: review-gated, never-archive,
    rescan no-op, source account + cursor, MCP route + write-scope), FastAPI +
    standalone contract tests (harvest + inbox review-gating, idempotent, scope
    refusal, 422s); metadata suite auto-covers the new tool

- Phase C: source reputation — the review ledger learns which sources earn trust — this commit
  - A pure READ-MODEL over the capture review ledger: per source (and per source
    account) how often the user APPROVES vs REJECTS what it proposes. It RECOMMENDS
    promote/demote; it never flips a trust toggle on its own (cited-or-silent applied
    to autonomy — we only suggest what the user's own review history already supports)
  - Signal hygiene was the whole game (a dirty signal is worse than none):
    approvals come from capture.approved, rejections ONLY from capture.archived with
    reason=user_review. Added a `reason` param to archive_capture (default user_review)
    and tagged the reconciliation caller reason=reconcile, so an automated archive
    (a source file disappeared) is NOT counted as a rejection — and does not even
    overwrite an earlier genuine approval. approve/archive events now carry source +
    source_account_id so the model can aggregate per connector and per account
  - Fold-to-last-decision per capture (approve→reject churn can't inflate totals);
    ORDER BY created_at, rowid so the later same-second event wins the fold
  - Evidence floor: REPUTATION_MIN_DECISIONS=8 decided captures before any verdict
    counts (one lucky streak must not auto-trust a connector). Verdicts insufficient_
    evidence / reliable (>=0.9) / noisy (<=0.5) / mixed → promote only for an UNtrusted
    source, demote only for a currently-TRUSTED one
  - MCP: get_source_reputation in READ_TOOLS (read-model, not a mutation — a write-only
    token is refused); readOnlyHint true, openWorldHint false (purely local)
  - REST: GET /v1/sources/reputation on both servers (read scope via GET fall-through,
    bounded days window)
  - tests: test_source_reputation.py (12: approval rate + reliable verdict, reconcile-
    archive-is-not-a-rejection, fold-to-last-decision, min-decisions gate, promote-only-
    untrusted, already-trusted-holds, demote-only-trusted-noisy, untrusted-noisy-holds,
    mixed-holds, window excludes old decisions, report caveats, MCP read-scope parity);
    FastAPI + standalone contract tests (report + promote rec, read-scope refusal of a
    write-only token); metadata suite auto-covers the new tool

- Phase D: memory integrity + portability — prove nothing was silently edited, hand the whole
  memory over intact — this commit
  - A tamper-evident hash CHAIN over the append-only event log: link_0 = a fixed genesis
    constant, link_n = sha256(link_{n-1} + ':' + sha256(canonical(event_n))), folded oldest
    first (created_at, rowid tiebreak). The final link is the chain_head. Any edit, reorder,
    insertion, or deletion anywhere in history changes the head — tamper-EVIDENCE, cheap to
    recompute, impossible to forge without rewriting the whole log. Pure read-model, no writes
  - integrity_digest (head + attested counts) + verify_integrity(expected_head): pin the head
    today, recompute tomorrow; a mismatch means the past moved (new events advance it
    legitimately — verify makes no judgement about WHY, only whether it matches)
  - Portable, self-verifying export: export_manifest (head + payload_sha256 + record counts,
    attests the export WITHOUT containing it) and export_portable_bundle (full export_json
    payload wrapped in that manifest). verify_portable_bundle recomputes the payload hash from
    the embedded payload and checks it against the manifest — the RECEIVING half of portability,
    a pure function of its argument so it can check a bundle from anywhere before a restore
  - Canonicalization is the same discipline as the context pack: json sorted keys, compact
    separators, ensure_ascii — byte-stable across Python/json versions. Comparisons use
    hmac.compare_digest
  - MCP: get_memory_integrity / verify_memory_integrity / verify_memory_bundle in READ_TOOLS
    (hashes/counts/pure-check, no content egress); export_memory_bundle in EXPORT_TOOLS (carries
    the whole corpus out of custody, export scope + allow_agent_exports trust gate). Added
    "verify_" to SCORECARD_READ_TOOL_PREFIXES so the storage-side classifier parity guard stays
    in sync (verify_* tools are read-side recompute+compare)
  - REST: GET /v1/integrity/digest, POST /v1/integrity/verify, GET /v1/export/manifest (all
    read); GET /v1/export/bundle (export); POST /v1/export/verify (read) — on both servers, with
    the export-scope entry registered in both _required_api_scope maps
  - tests: test_memory_integrity.py (13: deterministic head, empty→genesis, new-event-advances,
    self-verify, stale-head-rejected, SILENT-TAMPER-detected, empty-expected-head, bundle
    round-trip, modified-payload-fails, manifest-attests-same-bytes, malformed-bundle-raises,
    counts-match-export, MCP scope+dispatch); FastAPI + standalone contract tests (digest/verify/
    manifest read, bundle export-scoped, round-trip + tamper-fail, 422s, read-only refused the
    bundle); metadata + scorecard-parity suites auto-cover the new tools

## OpenClaw exploration doc (shipped)
- `docs/OPENCLAW_ATTACHMENT_EXPLORATION.md` — the user's asked-for bridge between the Phase A-D
  memory foundation and OpenClaw's local execution power. Exploration only, no code this pass.
- Grounded in current facts (re-fetched both sources): OpenClaw = self-hosted local agent with
  hands (files/browser/shell/APIs), ~180k stars, plugin system with a typed `contextEngine` slot;
  TencentDB-Agent-Memory = an MIT/TS plugin that fills that slot via a local Gateway on :8420
  (capture/search/recall, optional Bearer auth). Cortex already ships the same shape: /mcp + /v1
  on :8766 with get_context / build_context_pack / remember_this + agent-session tools
- Thesis: "Cortex is the memory, OpenClaw is the hands." Cortex is a strict upgrade over the
  default TencentDB memory slot (curation/review gate, citations, honest metrics, Phase C
  reputation, Phase D integrity+portability, real non-chat inputs); TencentDB's one edge is
  symbolic short-term Mermaid offload, which the attachment reframes as newly-relevant but
  parked (position 1: let OpenClaw/TencentDB own it; position 2: take the pattern only if real
  workhorse sessions measurably demand it — same measure-before-you-add-learning bar)
- Three integration options, cheapest first: (A) OpenClaw → existing /mcp, zero Cortex code,
  prove the loop first; (B) thin `contextEngine` TS plugin proxying to :8766 for native
  auto-recall/auto-capture UX, likely zero new Cortex endpoints; (C) deep merge, not recommended.
  Recommendation: A to validate, then B if the loop feels right, keep short-term offload out
- Respects standing invariants: cited-or-silent extended to action, review gate for capture,
  allow_agent_exports + export scope gate egress, both-servers+MCP parity if any endpoint added

## Context-pack identity fix (shipped, de7a730)
- Found while parallelizing the suite for the build 22 gate: the prefetch predictor's replay
  test passed serially but failed under pytest-xdist (-n 8). Root cause was a real PRODUCTION
  bug, not a flake: `assemble_context` baked `"generated_at": now_iso()` into
  `_canonical_pack_bytes`, so the same task pinned a second later got a different `pack_sha`.
- Blast radius: (1) the "most recent pack" prefetch predictor scored 0.0 hit-rate under an
  advancing clock (every real session) — the phase6 test only saw 0.75 because fast serial runs
  pinned all 12 events inside one clock-second; (2) `pin_context_pack`'s documented
  "re-pinning is a no-op" was false, so `context_packs` grew unbounded.
- Fix: one shared `CONTEXT_PACK_ENVELOPE_KEYS = {generated_at, receipt, pin}` constant used by
  BOTH `_canonical_pack_bytes` (identity hash) and `_pack_recompute_diff` (recompute envelope),
  so they can never drift apart. `generated_at` stays in the live/stored payload (as_of recompute
  reads it); it is just no longer part of the sha. The recompute-diff already excluded that exact
  set, and `test_context_engine.test_deterministic_output` already pops `generated_at` before
  comparing — the identity hash simply never agreed with that existing intent.
- Backward-compatible, no migration: `get_context_pack` verifies `sha256(stored vault bytes) ==
  pack_sha` per-file, so every existing build-21 pack still self-verifies against its own bytes;
  only newly-assembled packs get the corrected time-independent identity. Locked invariant
  `sha256(file) == pack_sha` still holds.
- Regression guards (both verified to FAIL against reverted code):
  test_hit_rate_holds_when_the_clock_advances_between_pins,
  test_repin_is_idempotent_across_clock_seconds.
- Suite now 1596 passed (+2) + 372 subtests, green (run parallel: `-n 8 --dist loadfile`).

## State
- Full suite green: 1596 tests + 372 subtests
- Branch pushed through `de7a730` (context-pack identity fix); Info.plist bumped to build 22
  (uncommitted, ships with the release commit)

## Next
- Build 22 + DMG last, with verify_context_pack shipped in it (everything A-D aboard)
- Prefetch: only add learning if the most-recent baseline plateaus (measure first)
