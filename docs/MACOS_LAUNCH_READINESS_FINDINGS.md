# macOS app launch-readiness review (2026-07-02)

A read-only SwiftUI review of `macos/Sources/` for a 10k-user launch. No crashers were
found (no unguarded force-unwraps, stable `Identifiable` ids, `@MainActor AppState`, weak
captures in long-lived tasks, guarded keychain + security-scoped bookmarks). The real risks
are **networking robustness / in-flight feedback** and **data-safety UX** around destructive and
disconnect actions. Core file is `macos/Sources/CortexApp.swift` (~8.3k lines: `AppState`,
`BackendSupervisor`, `AppDelegate`).

## Launch-critical (fix before launch)

- **#1 HIGH robustness — no request timeout.** `performRequest` sets no `timeoutInterval` (only
  the 1.5s health probes do), so every UI call uses URLSession's 60s default; each mutation then
  fires ~11 sequential reloads → a slow/hung backend freezes the flow for minutes. Fix: explicit
  8–15s timeout; parallelize post-mutation reloads.
- **#2 HIGH bug — `request()` retry restarts the backend on ANY error and replays POST/DELETE.**
  On any thrown error (incl. 4xx/5xx `CortexHTTPError`) it calls `ensureBackend()` (can
  terminate+restart, 45–90s) then re-runs the same request, so a non-idempotent call that already
  applied server-side is replayed → duplicate approve/create/delete. `ensureBackend()` has no
  in-flight guard (auto-sync tick can race a manual action). Fix: retry+ensureBackend only on
  connection-level `URLError`; never replay non-GET; coalesce `ensureBackend()`.
- **#3 HIGH bug/race — approve/archive/forget/delete never disable in-flight.** Fire-and-forget
  `Task {}` with no per-item in-flight state; double-tap → duplicate mutation; on failure the row
  silently reappears. Fix: per-capture-id in-flight set on `AppState`, disable buttons + per-row
  spinner, surface failures inline.
- **#4 HIGH robustness — Ask button never disables during search;** rapid submit spawns overlapping
  `/v1/ask` calls, last-write race flips results to a stale query. Fix: `.disabled(isBusy || empty)`
  on all Ask/suggestion buttons; guard `search()`.
- **#5 HIGH robustness — no loading state on Ask; backend errors render as "No cited answer found"**
  (a failure looks like "your notes don't contain that"). Fix: `ProgressView` while busy; distinct
  error card with Retry vs the legitimate empty-answer case.
- **#6 MED robustness — ReviewTab shows a false "connect notes" empty-state when the backend is
  down** (4 sequential loads, no failure/loading branch). Fix: gate on `isLocalServiceReady`.
- **#9 MED robustness — createBackup/deleteBackups/deleteAllUserData/resetMCPIntegrationToken set no
  `isBusy` and don't disable;** token reset sets no status on failure. Fix: gate in `isBusy`,
  disable in-flight, always set a status incl. failure.
- **#10 HIGH data-safety — "Delete All Local Data" is one-tap, no typed confirm, no "irreversible"
  wording, and also deletes all backups** (the archives needed to recover). Fix: typed `DELETE`
  confirm; state permanence; consider not wiping backups in the same action.
- **#11 HIGH data-safety — "Pause" a connector silently deletes the stored token** while its own
  message claims credentials are retained; Resume can then fail. Fix: make Pause truly reversible
  (keep credential) or rename to Disconnect + warn; fix the contradictory message.
- **#12 HIGH data-safety — "Reset Token"/"Revoke" rotate/kill live tool credentials with no
  confirmation** (immediately breaks every connected AI tool). Fix: confirmation dialogs naming the
  affected tool/token.
- **#13 MED data-safety/onboarding — no way to disconnect the primary Notes source;** `deleteImport`
  has no UI caller; disconnect/tombstone/restore semantics are never explained. Fix: explicit
  disconnect / per-connection remove; wire up `deleteImport`; explain what happens to synced memory.
- **#14 MED bug — delete-all doesn't re-present onboarding** (`resetOnboardingProgressAfterDataDeletion`
  clears flags but nothing calls `presentOnboardingIfNeeded()`). Fix: re-present onboarding after a wipe.
- **#7/#8 MED — tab views load once and never refresh (stale counts); Review queue hard-caps at 10
  with no pagination.** Fix: reload on tab activation / `.refreshable`; paginate the review queue.

## Deferred polish / a11y (post-launch-critical)

- **#15 no dark mode** (`.preferredColorScheme(.light)` forced; hardcoded light colors).
- **#16 fixed window/sheet sizes + hardcoded `.font(.system(size:))` fight Dynamic Type.**
- **#17 icon-only buttons lack VoiceOver labels; metric/badge composites read fragmented.**
- **#18 backend-down Connections sheet shows indefinite "Preparing…" with no Retry.**
- **#19 no confirmation/undo on Archive in Review.**
- **#20 global `isBusy` single shared flag used inconsistently** (move to per-item state).
- **#21 pervasive hardcoded spacing instead of `CortexDesign.Space` tokens.**
- **#22 unconfigured OAuth connectors render a button titled "Not configured" (status as action).**
- **#23 ISO date handling is string-based/brittle (string-sort timestamps).**
- **#24 nested vertical ScrollView in Ask; unused older ReviewCaptureCard/ReviewPreviewList duplicates.**

## Verification note

macOS behavior is only partially verifiable headlessly: `macos/build.sh` compiles + code-signs
the app (the gate we can run), but runtime UX cannot be exercised in this environment. Fixes are
compile-verified; behavioral QA needs a human on-device pass before launch.

## Resolution status (2026-07-02)

**Fixed (all compile/build-verified):**
- Wave 1 — #1 request timeout, #2 retry-replays-mutations correctness, #3-part `ensureBackend`
  in-flight guard, #10 delete-all typed confirmation, #11 Pause keeps credential, #12 token
  reset/revoke confirmations + reset failure status, #14 re-present onboarding after wipe.
- Wave 2 — #3 per-item in-flight disable+spinner on approve/archive/forget/delete, #4 Ask
  button disable + concurrent-search guard, #5 Ask loading + distinct error card, #6 ReviewTab
  backend-down state, #7 tab reload on activation.
- Wave 3 — #13 disconnect-a-source (wired deleteImport) + per-connection remove, #8 review-queue
  pagination, #18 Connections-sheet Retry, #19 archive confirmation (no un-archive API exists),
  #22 "Setup required" status badge, #17 VoiceOver labels + combined composites.
- Wave 4 — #15 dark mode: `CortexDesign` palette is now appearance-adaptive and the forced
  `.preferredColorScheme(.light)` is removed (follows system); light mode is visually unchanged.
- Window — the app now opens at a roomy screen-relative default (was 820x760).

**Deferred (lower-priority polish, tracked):**
- #16 fully resizable sheets / Dynamic Type on hardcoded `.system(size:)` fonts.
- #20 replace the single global `isBusy` with per-item state everywhere (partially done via the
  new in-flight sets).
- #21 replace remaining hardcoded spacing with `CortexDesign.Space` tokens.
- #23 replace string-based ISO date sorting with real date parsing.
- #24 remove the unused older `ReviewCaptureCard`/`ReviewPreviewList` duplicates; fix the nested
  Ask ScrollView.
- Dark-mode second pass: audit `.white.opacity`/`.black.opacity` overlays and semantic status
  colors for contrast on dark backgrounds (the palette adapts; overlays are acceptable but not
  yet individually tuned).
