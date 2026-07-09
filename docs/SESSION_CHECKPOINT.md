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

## State
- Full suite green: 1579 tests + 372 subtests
- Branch pushed through `21b2d9d` (Phase B); Phase C is the next commit
- Shipped DMG (build 21) predates Phase 2b + A + B + C — build 22 ships at the end of
  the approved slate (write-back → session harvest → reputation → integrity → OpenClaw
  exploration doc)

## Next
- Phase D: integrity/portability (hash-linked events, manifest, lossless export)
- OpenClaw exploration doc, then build 22 + DMG last
- Prefetch: only add learning if the most-recent baseline plateaus (measure first)
