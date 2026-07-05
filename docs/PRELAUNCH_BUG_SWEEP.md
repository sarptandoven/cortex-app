# Pre-launch adversarial bug sweep (2026-07-02)

A 14-surface adversarial find→verify sweep (~43 agents) hunted correctness/security/reliability
bugs across the whole backend before a 10K launch. 29 findings reported → 9 confirmed after
per-finding verification. Each confirmed finding was re-verified by hand against the real code.

## Fixed (with regression tests; full backend suite green)

- **Stuck jobs / no lock reclaim (HIGH).** `_claim_next_job` set `locked_until` to *now* and
  never checked it; a worker killed mid-job (OOM/SIGKILL/deploy restart/hang) left the job in
  `running` forever. Added a future lease at claim + `_reap_expired_jobs` (requeue if attempts
  remain, else fail terminally). Fixes both the extract/embed path and the source-sync
  re-enqueue path (a stuck sync job is reclaimed on the next tick).
- **No retry backoff (MED).** `_fail_job` requeued without moving `run_at`, so a transient
  failure was re-claimed in the same tick. Now backs off exponentially (30s→300s).
- **Parser recursion crashes (HIGH).** Deeply-nested JSON exports / bookmark trees stack-
  overflowed the importer. Added `MAX_PARSE_NESTING_DEPTH` to both traversals + broadened
  `except json.JSONDecodeError` to also catch `RecursionError` (the stdlib decoder overflows
  before the parser runs).
- **Capture query-token auth fail-open (MED, defense-in-depth).** `_auth_query_token` /
  `_auth_token` granted the default user on any token when no global key was set, even in
  scoped-token mode. Now fails closed unless genuinely local single-user dev.
- **Vault attachment deletion not user-scoped (LOW, latent).** `delete_user_records` wiped the
  whole `attachments/` tree; now scoped to `attachments/<user_id>/` with a traversal guard.

## Deferred — real, but need a design decision (not a rushed data-path change)

- **#4 Connector cursor advances past failed records (HIGH) — FIXED (2026-07-02).** See below.

### #4 (resolved)
 In
  `sync_source_account_records`, if some records in a page fail to save (e.g. an oversized
  email raising in `save_capture`), the forward pagination token / high-water-mark still
  advances, so those records are never retried — permanent loss (the failure IS surfaced in
  `errors`/needs_attention, so not fully silent). The correct fix is a **bounded retry /
  dead-letter**: on `failed>0`, re-fetch the same window (hold the prior cursor, which
  `upsert_sync_cursor` currently overwrites unconditionally, so this needs reading the existing
  cursor) with a `consecutive_failing_pages` counter; after N attempts, advance past the poison
  window (accept the loss) with the account already flagged `needs_attention`. A naive "don't
  advance" risks a poison record stalling the account forever, which is worse — hence deferred
  to a designed change with cross-connector tests, not a one-liner. **Top follow-up.**
- **#7 No job retention (LOW).** Succeeded/failed rows accumulate in `memory_jobs` forever.
  Add a periodic prune (keep recent N / age-based) — cheap, do next.

## Rejected / not bugs

The other 20 reported findings did not survive verification (intended local-dev behavior,
per-user vault isolation in hosted mode, already-handled paths, or field-shaping limits).

## Separate finding — test-suite hygiene (pre-existing)

`pytest-randomly` is active; under some random orders, Ask/citation tests
(`test_ask_starter_queries…`, `test_relationship_context`) fail because a prior test leaks
global state (likely an env var / embeddings config / `main_module.settings` mutation) that
changes retrieval. The full suite is **green under deterministic order** (`-p no:randomly`), and
each test passes in isolation, so this is test-isolation fragility, not a product bug — but it
can flake CI. A concurrent commit (`5376c63`, "Fix Ask citing unrelated memory instead of
abstaining") hardened the abstention side. Follow-up: find and null the leaking global in the
offending test's teardown so CI is order-independent.
