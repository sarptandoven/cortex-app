# Session checkpoint — 2026-07-09

Resume point for the phased expansion roadmap (see docs/EXPANSION_ROADMAP_PHASES.md).

## Done and committed
- Phase 2: pinned context packs — `11d2bd3`
- Phase 3: provenance ledger & hygiene — `a676ad4`
- Phase 4: personal eval harness — `10477e9`
- Phase 5: delegate/twin — `e8e76ef`
- Phase 6: anticipatory context — this commit
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

- macOS UI for phases 4-6 — this commit
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

## State
- Full suite green: 1496 tests + 372 subtests

## Next
- Prefetch: only add learning if the most-recent baseline plateaus (measure first)
- Push branch (now 10 commits ahead of origin)
