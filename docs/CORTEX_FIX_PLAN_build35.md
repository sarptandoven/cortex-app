All the load-bearing claims are confirmed. Key facts verified:

1. **Sample-notes "temp vault" gap**: `loadSampleNotes` (line 5737) writes to `<AppSupport>/Cortex/sample-notes` (persistent), only falling back to `temporaryDirectory` — the completeness claim of "uses a temp vault" is **inaccurate**.
2. **Broker orphaned**: 0 Swift calls to `/oauth/broker/*`; client reads only `configuredManagedOAuthClientID` (Info.plist).
3. **Remote-MCP token dead-end**: `connectRemoteMCP` (7492) → `loadOrCreateMCPToken` + `registerMCPTokenInBackground` (registers on local endpoint only); hosted mint `create_mcp_token` (main.py:4002) exists but is never called by the client.
4. **No Downloads watcher**: `detectAvailableExports` (5955) has no `DispatchSource`/watcher; not in `refreshLiveCounts`.
5. **Global tool surface**: main.py:4385 uses single `settings.mcp_tool_surface` for all tokens.
6. **Delete-account wall trap**: "Delete All Local Data" lives at line 11764 inside Settings (behind the sign-in wall).

Now I have everything needed to synthesize the ranked plan.

---

# Cortex Fix Plan — Single Ranked, De-Duplicated Backlog

Grouped by file to prevent implementer collision. Each item: title, exact file(s), concrete change, AppState method/state to wire. Severity and founder-gating flagged.

---

## TIER 0 — COMPLETENESS GAP (claimed done, NOT actually done) — DO FIRST

### C1. Sample-notes escape does NOT use a temp vault (contradicts shipped claim + causes account pollution)
- **Files:** `macos/Sources/CortexApp.swift` (`loadSampleNotes` ~5701–5737, `applySignedInSession` ~590), `macos/Sources/CortexCloudAuth.swift` (`unlockLocalPreview` ~145)
- **Reality:** `loadSampleNotes` copies bundled notes into the persistent `<ApplicationSupport>/Cortex/sample-notes` dir (line 5737), only using `temporaryDirectory` as a fallback. The build-34 claim "explore with sample notes escape uses a temp vault" is **false**. This is also the root of inbound/account bug **A1** below: those persistent sample captures get push-synced into a real account on sign-in.
- **Change:** Tag the sample-notes connector source as preview-only, and in `applySignedInSession` detect `localPreviewUnlocked` and **purge the sample-notes source before `startPushSync`** (reuse the existing purge-by-source path → `_delete_capture_in_conn` tombstone-safe path per memory), then set `localPreviewUnlocked = false`. This simultaneously makes the "temp/ephemeral" claim honest and prevents demo memories from polluting the user's first real "memory of you."
- **Wire:** `AppState.applySignedInSession`, `AppState.localPreviewUnlocked`, existing purge-by-source method.
- **Severity:** HIGH (correctness + honesty). **Shippable now.**

---

## TIER 1 — PIPELINE DEAD-ENDS: marquee flows that silently fail

### P1. Remote-MCP / ChatGPT / Claude-web connector key 401s (the "use Cortex in ChatGPT" hero path is non-functional)
- **File:** `macos/Sources/CortexApp.swift` (`connectRemoteMCP` ~7492–7532)
- **Reality confirmed:** The handed-out key comes from `loadOrCreateMCPToken` + `registerMCPTokenInBackground` → registers on the **local** endpoint (127.0.0.1:8766). Hosted `/mcp` authenticates against the **hosted** store (main.py:455). The token is never registered there → pasted key 401s. Hosted mint `create_mcp_token` (main.py:4002) exists but is never called.
- **Change:** In `connectRemoteMCP`, when signed in, mint the connector token against the **hosted** backend: `POST {cloudSyncBaseURL or defaultHostedURL}/v1/integrations/tokens` (audience=mcp, scopes=[read]) using the `cxs_` access token, and hand the **returned hosted token** to the user. Cache per-integration keyed by host.
- **Wire:** new `AppState.mintHostedConnectorToken(base:integration:)`; reuse `cloudSyncBaseURL`, hosted-auth Bearer path.
- **Severity:** HIGH. **Founder-gated for full verify** (hosted `/mcp` must be reachable for the signed-in user), but the client fix is shippable now and is the prerequisite for P2.

### P2. Remote-MCP "test connection" gives false green (shows success while connector 401s)
- **File:** `macos/Sources/CortexApp.swift` (`testToolConnection` remoteMCP branch ~5008–5018)
- **Reality:** remoteMCP verify returns `ok:true` after only checking `isSignedIn` + non-empty base; never probes hosted `/mcp`.
- **Change:** Make the remoteMCP verify actually `POST` an MCP `initialize` + `tools/list` to `{base}/mcp` with the hosted token and `Authorization: Bearer`. Report real tool count on 200, or exact status on failure — mirror the honest self-ping the mcpConfig/deeplink branches already do (5032/5039). Only show green on 200.
- **Also update:** `macos/Sources/ConnectionsPrivacySheet.swift` `canFinishWithoutProbe` (~3360) so remoteMCP no longer finishes with zero probe once a real probe exists; keep a "check status / notify me" affordance (folds in the connections `remoteMCPConnectBody` finding at :3719).
- **Wire:** `AppState.testToolConnection`.
- **Severity:** HIGH. Depends on P1. **Shippable now** (honest failure is better than false green even before hosted is live).

---

## TIER 2 — NEAR-ONE-CLICK PIPELINE WINS

### P3. Wire the desktop client to the hosted OAuth broker (Notion/Google Drive/Gmail/Outlook go from dead → one-click)
- **File:** `macos/Sources/CortexApp.swift` (`managedOAuthIsConfigured` ~6105, `startManagedOAuthConnectorNow` ~6130, `configuredManagedOAuthClientID` ~6213)
- **Reality confirmed:** Client reads `client_id` only from Info.plist (ships empty). `oauth_broker.py` (`/oauth/broker/start|exchange|providers`) is **never called** by Swift (0 hits). So "Available soon" is NOT just a missing secret — it's **missing client code**.
- **Change:** (1) On launch fetch `GET https://api.signindoppl.com/oauth/broker/providers`, cache the configured list. (2) `managedOAuthIsConfigured` returns true when provider ∈ broker list **OR** Info.plist client_id present. (3) In `startManagedOAuthConnectorNow`, when no local client_id, `POST /oauth/broker/start` for the authorization_url (PKCE for google/github) and on the loopback callback `POST /oauth/broker/exchange` to mint tokens.
- **Wire:** new `AppState.brokerConfiguredProviders`, `AppState.managedOAuthIsConfigured`, `AppState.startManagedOAuthConnectorNow`.
- **Honest ceiling:** First Google/Notion sign-in is ~3 real actions (tap → pick account/consent → grant read-only scope) — the OAuth-consent floor, not literally one click.
- **Severity:** HIGH. **Founder-gated to go live** (needs `CORTEX_BROKER_*_CLIENT_ID/SECRET` on server) but **the client wiring is shippable now** — and once shipped, "Available soon" flips with zero app rebuild the moment the founder sets secrets.

### P4. Route Notion/Microsoft exclusively through the broker; delete embedded client_secret from the app bundle
- **Files:** `macos/Sources/CortexApp.swift` (`managedOAuthIsConfigured` ~6113, `startManagedOAuthConnectorNow` ~6130–6148), `macos/Info.plist` (`CortexNotionOAuthClientSecret`/`CortexMicrosoftOAuthClientSecret` ~35–40), `backend/app/oauth_broker.py` (`BrokerProviderSpec` for Notion, `supports_pkce=False` ~75)
- **Reality:** Notion is a confidential client (needs secret at exchange). Current local path requires `client_id`+`client_secret` **embedded in the distributed binary** — the exact footgun the broker was built to avoid.
- **Change:** Remove the client-secret Info.plist keys and the client-secret branch in the two AppState methods; force Notion/Microsoft through the broker (server holds the secret). Keep Google/GitHub on PKCE. Notion consent stays 2 actions (approve + choose pages).
- **Wire:** `AppState.managedOAuthIsConfigured`, `AppState.startManagedOAuthConnectorNow`.
- **Severity:** MEDIUM (security). Do **with/after P3**. **Founder-gated** (broker secrets).

### P5. No Downloads auto-import — remove every click AFTER the export downloads
- **Files:** `macos/Sources/CortexApp.swift` (`detectAvailableExports` ~5955, `importDetectedExports` ~5981, `refreshLiveCounts` ~4087; new watcher), `macos/Sources/ConnectionsPrivacySheet.swift` (`AIChatsImportCard.onAppear` ~1312, pill ~1198–1218)
- **Reality confirmed:** No `DispatchSource`/watcher exists; `detectAvailableExports` runs only on card `onAppear`, and `refreshLiveCounts` never re-detects — so even the pill is stale if the export lands while the card is open.
- **Change:** Add a `DispatchSource.makeFileSystemObjectSource` watcher on `~/Downloads` + `~/CortexImports` at launch (direct builds only). On write/rename → debounce ~1.5s → `detectAvailableExports()`; when a NEW candidate with `records_found>0` matching a chat-export shape and no prior import for that file hash appears, call `importFromPath(path, automatic: true)` directly (not just the pill). Gate behind a default-on "Auto-import chat exports I download" toggle; post the Learning HUD. Also call `detectAvailableExports()` from `refreshLiveCounts` while the Connections sheet is visible so the pill self-updates.
- **Wire:** new `AppState.startExportWatcher()`, `AppState.autoImportExportsEnabled`, existing `importFromPath`, `detectAvailableExports`.
- **Honest ceiling:** ChatGPT/Claude/Gemini have no import API and email the archive out-of-band; the user must still (a) click the per-vendor export button and (b) download the emailed file. The watcher removes all clicks **after** the download — the real ceiling.
- **Severity:** HIGH (biggest miss vs the "near-zero-click after one export click" ideal). **Shippable now** (direct-build only; App Store sandbox can't read Downloads).

### P6. Export de-dup + tracked "waiting for export" funnel state
- **Files:** `macos/Sources/CortexApp.swift` (`detectAvailableExports` ~5955, `importDetectedExports` ~5989), `macos/Sources/ConnectionsPrivacySheet.swift` (`exportVendorButton` ~1343, pill copy ~1198)
- **Change:** Track imported export identities (path+size+mtime or cheap hash) in UserDefaults; skip already-imported candidates in both detect and import. Show per-vendor detail in the pill ("Found ChatGPT (142) + Claude (30)"). After a per-vendor export button is tapped, persist "requested export for <vendor> at <time>" and flip the card to a calm "Waiting for your ChatGPT export… we'll import it automatically when it lands" state (ties into P5's watcher). This also makes P5's watcher safe from re-importing on every filesystem event.
- **Wire:** `AppState.importedExportIdentities`, `AppState.exportRequestState[vendor]`.
- **Severity:** LOW → but prerequisite-safety for P5. **Shippable now.**

### P7. Hosted `/mcp` per-token tool surface (stop serving the 6-tool chatgpt surface to everyone)
- **Files:** `backend/app/main.py` (`tools/list` ~4385, `create_mcp_token` ~4002), `backend/app/mcp_tools.py` (`tools_for_scopes`, surfaces ~1388)
- **Reality confirmed:** `tools/list` uses a single **global** `settings.mcp_tool_surface`; prod runs `CORTEX_MCP_TOOL_SURFACE=chatgpt`, so generic remote-MCP / Claude-web / Cursor-over-remote all see only the 6-tool chatgpt surface.
- **Change:** Store a `surface` on each minted MCP token (`create_mcp_token` already takes label/scopes); resolve in `tools/list` as `tools_for_scopes(token_scopes, surface=token.get('surface') or settings.mcp_tool_surface)`. Then `connectRemoteMCP` (P1) mints `chatgpt` for ChatGPT and `core`/`full` for generic/Claude-web/Cursor. Optionally honor `initialize` `clientInfo.name` as fallback hint.
- **Wire:** backend token record + `create_mcp_token` param; client-side surface choice in `connectRemoteMCP`.
- **Severity:** MEDIUM. **Shippable now** (backend-only + client mint param).

### P8. ChatGPT connector: separate URL/key copy fields + concrete guided steps
- **Files:** `macos/Sources/ConnectionsPrivacySheet.swift` (`remoteMCPConnectBody` ~3696–3722, reuse `GuidedStepWalkthrough` ~1283), `macos/Sources/CortexApp.swift` (`connectRemoteMCP` clipboard blob ~7509–7516)
- **Reality:** URL+key are copied as ONE blob; ChatGPT/Claude forms have separate fields, so a second copy wipes the key. And instructions don't say where developer mode is or which field the key goes in.
- **Change:** Render Connector URL and Connector key as **two separate labeled rows, each with its own copy button** (keep combined copy as secondary). Expand `pasteInstructions` into the concrete ChatGPT path (Settings → Connectors → Advanced → Developer mode → Create → paste URL → Authentication: API key/Bearer → paste key → Create) using per-host step arrays via the existing `GuidedStepWalkthrough`.
- **Wire:** view-local; no new AppState.
- **Honest note:** custom remote connectors have no one-click deeplink anywhere — precise guided steps are the ceiling.
- **Severity:** MEDIUM. **Shippable now.**

### P9. Deeplink token race + real first-call confirmation (Cursor/VS Code)
- **Files:** `macos/Sources/CortexApp.swift` (`mcpInstallDeeplink` ~7392, `connectViaDeeplink` ~7428, `registerMCPToken` ~4182), `macos/Sources/ConnectionsPrivacySheet.swift` (verify step, `RecallProofWatcher` ~4000)
- **Change:** **Await** `registerMCPToken(for:)` before opening the deeplink (make connect async) so the local backend has the token before the client can call → eliminates the fast-client 401. Additionally, surface the existing `RecallProofWatcher` (already wired for non-remote tools on the done step) on the **verify** step for deeplink hosts, so the user gets a true first-call confirmation instead of only a Cortex-side self-ping.
- **Wire:** `AppState.registerMCPToken`.
- **Honest note:** Cortex can't read Cursor/VS Code's own MCP install state; a real-call watcher is the honest best confirmation.
- **Severity:** LOW. **Shippable now.**

---

## TIER 3 — ACCOUNT HOLISM (seams between lifecycle stages)

*(A1 is folded into C1 above — same root cause, do it once.)*

### A2. Delete-account traps the user behind the sign-in wall with no way to erase local memory
- **Files:** `macos/Sources/CortexCloudAuth.swift` (delete confirmation sheet ~902–933, `performDeleteCloudAccount`), `macos/Sources/CortexApp.swift` ("Delete All Local Data" ~11764, `resetToLocalDefaults`)
- **Reality confirmed:** "Delete All Local Data" lives in Settings (line 11764), which is behind the wall in an `accountRequired` build. After delete → `resetToLocalDefaults` signs out → `requiresSignIn` true → wall slams shut. The success copy references an action the user can no longer reach.
- **Change:** Add an **"Also delete local memory on this Mac" checkbox** to the delete-account confirmation sheet that runs the local purge in the **same action** (before sign-out drops the wall). Now the success-copy promise is actually reachable.
- **Wire:** delete-account confirm flow + existing local-purge path.
- **Severity:** HIGH. **Shippable now.**

### A3. New-machine restore is only reachable from onboarding (no "Restore now" in Settings)
- **File:** `macos/Sources/CortexCloudAuth.swift` (`signedInView` in `CortexCloudSection` ~876, reuse `restoreProgress`)
- **Change:** Add a "Rebuild this Mac from your account" button to the signed-in section that calls `restoreFromAccount()` and shows the same `restoreProgress` states. Pure reuse of existing machinery.
- **Wire:** `AppState.restoreFromAccount`, `AppState.restoreProgress`.
- **Severity:** MEDIUM. **Shippable now.**

### A4. Zero-access + restore are uncoordinated (encrypted pull applies 0 items, reports bare `.empty`)
- **Files:** `macos/Sources/CortexApp.swift` (`restoreFromAccount` ~8130), `macos/Sources/OnboardingView.swift` (restore stage), `macos/Sources/CortexCloudAuth.swift` (`restoreZeroAccessFromRecoveryCode` ~1082)
- **Change:** When zero-access is enabled server-side and no local key is present, `restoreFromAccount()` short-circuits to a `.needsRecoveryCode` state and routes the onboarding restore stage into recovery-code entry (reuse `restoreZeroAccessFromRecoveryCode`). Minimum: detect "pulled N encrypted, applied 0" → surface "Enter your recovery code to unlock your synced memory" instead of `.empty`.
- **Wire:** `AppState.restoreFromAccount`, new `.needsRecoveryCode` restore state.
- **Severity:** MEDIUM. **Shippable now.**

### A5. Sign-out honesty + freshly-signed-in false-alarm (two small copy/state splits)
- **File:** `macos/Sources/CortexCloudAuth.swift` (`performCloudSignOut` ~542–550; signed-in status else branch ~818–820)
- **Change (a):** Track the logout POST result instead of discarding with `try?`; when it fails show "Signed out on this Mac. Could not reach Cortex Cloud to end the session remotely — it will expire automatically." **Change (b):** Split the `canActuallyPushSync`-false branch: only show the orange "Sign in again" when genuinely broken (`isSignedIn` but `cloudSyncBaseURL` empty); for just-signed-in/nothing-synced-yet show neutral "Ready to sync — your memory will back up as it's captured."
- **Wire:** `AppState.performCloudSignOut`, signed-in status view.
- **Severity:** LOW. **Shippable now.**

### A6. Preview return-visit wall reads as cold first-run
- **File:** `macos/Sources/CortexApp.swift` (`unlockLocalPreview` persistence ~3476–3478; wall copy)
- **Honest note:** Persisting the unlock indefinitely would defeat the required-account gate. Do NOT persist the unlock — instead persist a "previewed before" flag so the return-visit wall copy acknowledges it ("Pick up where you left off, or sign in to sync").
- **Severity:** LOW. **Shippable now.**

---

## TIER 4 — DEFAULT-ACTION / INTERACTIVITY GAPS THAT UNLOCK REAL VALUE

Grouped by file. These convert dead read-outs and dead-end cards into the action they name.

### === `macos/Sources/TwinAlertsViews.swift` (thread `@ObservedObject var state: AppState` into `TwinScorecardCard` once — call site `ModelTab.swift:78` already has `state`) ===

- **U-TWIN1 (HIGH): No "Ask would I…" on the Twin scorecard.** (`TwinScorecardCard` ~413–449) The card invites would-I questions but has no way to ask one. Add a primary `CortexButton "Ask would I…"` → set `state.selectedTab = .ask`, seed `state.searchQuery = "Would I …"`, call `state.runSearch()`. **Wire:** `AppState.selectedTab`, `.searchQuery`, `.runSearch`.
- **U-TWIN2 (HIGH): "Grade in Review" dead-end.** (~439–441, graded==0 / `scorecard.ungraded` non-empty) Replace the passive footnote with `CortexButton "Grade N predictions"` (count from `scorecard.ungraded.count`) → `state.selectedTab = .review`, `state.status = "Grade twin predictions"`. **Wire:** `AppState.selectedTab`, `.status`.
- **U-TWIN3 (LOW): twinStat cells non-interactive.** (~451–463) Wrap Predictions/Graded (and Accuracy) in the `LedgerColumn`-style Button → route to Review grading queue. Reuse `CortexStatView` + hover/press.
- **U-TWIN4 (LOW): `verdict_mix` + `caveats` decoded but never rendered.** (payload at ~79–88) Render `scorecard.caveats.first` as faint italic footnote (same as `ConnectionsToolUsageSection` ~555) and a tiny verdict-mix line ("Likely yes 8 · Likely no 3 · Mixed 2").

### === `macos/Sources/ModelTab.swift` ===

- **U-MODEL1 (HIGH): Model tab shows stale profile + Mirror.** (`.task`/`.onChange` ~122–132) The tab renders `state.profile` (~81) and `state.mirrorInsight` (~48) but never calls their loaders here. Add `await state.loadProfile()` and `await state.loadMirrorInsight()` alongside `loadRecallHeadline`/`loadTwinScorecard` in both `.task` and the `.onChange(of: state.selectedTab)`. **Wire:** `AppState.loadProfile`, `.loadMirrorInsight`.
- **U-MODEL2 (HIGH): Hero Constellation portrait is dead.** (`HomeHeroSection.portrait` ~1103) Wrap `portrait` in a Button/`.onTapGesture` posting `NotificationCenter.default.post(name: .cortexPresentConstellation, ...)` (the exact call "Open full view" uses at :66); add `CortexMotion.press` + accessibilityHint. When `graphNodes` empty, route tap to `state.presentConnectToolsWizard()`.
- **U-MODEL3 (MEDIUM): detectedNudge is static text.** (`ConnectAIToolsHeroCard` ~270) Turn "N ready to connect on this Mac" into a `CortexButton "Connect the N we found"` → `state.presentConnectToolsWizard()` (pre-scoped to detected / auto-connect-all).
- **U-MODEL4 (MEDIUM): profile readiness stamp has no improve path.** (~87–91) When `profile.readiness < 100`, add ghost `CortexButton "Improve readiness"` → `state.presentConnectToolsWizard()` or `openConnectionsPrivacy`, driven off the weakest source/connect state.
- **U-MODEL5 (MEDIUM): confidence pill inert.** (`ProfileCard.confidencePill` ~627–646) Make tappable to reveal a one-line "why" + ghost "Confirm this" that posts a training label (reuse `confirmMirrorInsight` mechanism); at minimum add `.help()` + VoiceOver.
- **U-MODEL6 (MEDIUM): Mirror micro-action dead-stops.** (`MirrorMomentCard` ~594) After `confirmMirrorInsight()`/`dismissMirrorInsight()`, have AppState immediately call `loadMirrorInsight()` to chain the next insight; if none, surface a "See what else Cortex learned" link scrolling to the profile stack. **Wire:** `AppState.confirmMirrorInsight`/`dismissMirrorInsight` → auto `loadMirrorInsight`.
- **U-MODEL7 (LOW): ledger readers not tappable.** (`RecallHeadlineCard.ledgerColumn`, `CortexNorthStar.swift:162`) Make each per-client column a Button → open Connections tool-usage for that label OR seed `state.searchQuery` filtered by reader; skip the aggregate "Total recalls" column.
- **U-MODEL8 (LOW): `.help("")` empty tooltip.** (`heroTextZone` ~1073) Attach `.help(...)` only when `detailHelp != nil`.
- **U-MODEL9 (LOW): limitations footnote silently drops >2.** (~97–105) When `limitations.count > 2`, add a disclosure/"See all N notes" (reuse `ProfileCard.showSources` toggle).

### === `macos/Sources/ReviewTab.swift` ===

- **U-REV1 (HIGH): "needs attention" strip has no fix button.** (`ReviewSourceHealthStrip` ~142–220) When `needsAttentionCount > 0`, add trailing `CortexButton` titled from `first.next_action` (fallback "Fix source") → `state.openConnectionsPrivacy(statusMessage: "Fix \(first.name)")` (exists at CortexApp.swift:5260). For healthy+pending sources add a "Sync now" secondary re-running `loadInbox()`/source sync.
- **U-REV2 (HIGH): no "Open source" from a review card.** (`ReviewQueueSourceBox` ~1465–1478) Add "Open source" `CortexIconButton` (`arrow.up.right.square`) next to `AccessionStamp` when `capture.source_url` is a valid URL or `MemoryText.isPathLike(capture.source)` → `NSWorkspace.shared.open(url)`. Also add to the card `.contextMenu` (~1268).
- **U-REV3 (HIGH): no per-item detail — long content truncated with only a hover tooltip.** (`ReviewQueueCaptureCard` ~1100–1345) Add a "Details" ghost button / header `.onTapGesture` toggling `@State showDetail` presenting full `capture.summary` + all `preview_memories`/`preview_tasks` untruncated (`MemoryText.normalizedProse`), keeping Approve/Archive in the detail.
- **U-REV4 (MEDIUM): no edit-before-approve.** (action row ~1207–1240) Add "Edit" ghost button opening an inline `TextEditor` bound to summary/first preview memory + "Save & approve" → new `state.approveCapture(capture, editedContent:)` variant. **Wire:** new AppState approve-with-edit method.
- **U-REV5 (MEDIUM): no batch/per-source ARCHIVE.** (batch row ~683–716, contextMenu ~1268) Add "Archive N shown" (guarded by `ReviewWaxSealConfirm`) → new `state.archiveCaptures(batch)` mirroring `approveCaptures` (CortexApp.swift:8515); add "Archive all from <source>" → new `state.archiveAllCaptures(source:)` mirroring `approveAllCaptures` (4904). **Wire:** two new AppState methods.
- **U-REV6 (MEDIUM): Proactive alerts + grading go stale on visible Review tab.** (`refreshLiveCounts` `case .review:` CortexApp.swift:4094–4106) Add `await loadProactiveAlerts()` and (under the existing `heavy` tick) `await loadTwinScorecard()`. **Wire:** `AppState.loadProactiveAlerts`, `.loadTwinScorecard`.
- **U-REV7 (MEDIUM): source chips inert.** (`ReviewSourceHealthChip` ~226–260) Wrap in `Button(.plain)`; needs-attention → `openConnectionsPrivacy(...)`; healthy+pending → filter inbox/approve path. Min viable: tap → `openConnectionsPrivacy`.
- **U-REV8 (LOW): confidence seal non-interactive / no triage sort.** (~1150–1166) Add a "Low confidence first" sort in the batch row reordering `visibleCaptures` by `cortexCaptureSeal` priority (or make the seal a filter chip).
- **U-REV9 (LOW): all-clear empty state can't see approved memories.** (~916–953) Add secondary "View recent memories" → `state.selectedTab = .model`.
- **U-REV10 (LOW): no "Show fewer".** (~747–751) When `visibleLimit > pageSize` render "Show fewer" → `visibleLimit = Self.pageSize` + scroll top.

### === `macos/Sources/AskTab.swift` ===

- **U-ASK1 (HIGH): no-answer state is a dead-end.** (`AskResponseSection`/`AskEmptyGuidance` ~607–664) Render `AskSuggestedQuestions(state:)` inside the no-answer branches (after `AskEmptyGuidance` ~628 and `AskQuietState` ~637) — chips already wire `state.searchQuery = suggestion; state.runSearch()`.
- **U-ASK2 (HIGH): no recent-query/search history.** (`AskQuerySection` ~108–195) Add `@Published var recentQueries: [String]` to AppState, append trimmed query in `search()` (CortexApp.swift:4685; cap ~8, dedupe, persist to UserDefaults). Show as tappable chips beneath the field when empty/focused. **Wire:** new `AppState.recentQueries` + append in `search()`.
- **U-ASK3 (MEDIUM): source cards lack Copy / Ask-about-this / expand.** (`AskSourceDetailRow` ~876–971) Add ghost "Copy" (`MemoryText.unwrap(item.content)` → `NSPasteboard`), "Ask about this" (seed scoped prompt → `runSearch()`), and an expand toggle flipping `lineLimit` 6→nil.
- **U-ASK4 (MEDIUM): no per-citation copy.** (`AskMarginCitationCard` ~1347–1476) Add "Copy excerpt" ghost action copying `excerpt` (always visible for VoiceOver). Consider unifying margin card with `AskSourceDetailRow`.
- **U-ASK5 (MEDIUM): Details source-health telemetry all static.** (`sourceHealthLabel` ~387–401) Make rows tappable: "N in Review" → `.review`; "N sync due" → `syncSource`/`openConnectionsPrivacy`; "N needs attention" → Connections. Wrap `AskContextMetric`/`AskSourceConfidenceChip` in Buttons.
- **U-ASK6 (MEDIUM): error card only Retry.** (`AskErrorCard` ~564–605) Add contextual secondary: connectivity/auth error → "Check connection" → `openConnectionsPrivacy(...)`/`loadSourceConnectivity()`; add a one-line hint distinguishing "no memory reviewed yet" vs "engine unreachable."
- **U-ASK7 (MEDIUM): answer is a read-only terminus.** (`AskAnswerPanel` header ~1159) Add "Save as note" (POST answer+sources via the `/v1/captures` path used by `saveQuickCapture`) and/or "Share" via `NSSharingServicePicker` (reuse `copyAnswerWithSources`).
- **U-ASK8 (LOW): context strip stale on visible tab.** (~11–68) Add `.onChange` on `state.review?.stats.memories` / `state.stats?.memories` → `reload()`, or a refresh `CortexIconButton`.
- **U-ASK9 (LOW): fragile 0.05s focus timer.** (~187–193) Drive focus off `.onChange(of: state.selectedTab)` when `== .ask` instead of a `DispatchQueue` delay.

### === `macos/Sources/ConnectionsPrivacySheet.swift` (giant file — coordinate; items are in disjoint sections) ===

- **U-CONN1 (HIGH): four permission tiles are a dead read-out.** (`ConnectionsMCPAccessSection` grid ~4254–4279, `ConnectionsTrustTile` ~4536) Add `action`/`isOn` binding to `ConnectionsTrustTile`; each tile flips the matching `state.appSettings.allow_agent_reads/writes/exports/maintenance` and persists via the existing `/settings` update path (CortexApp.swift:5435). Reuse `applyTrustPreset`. Min: tile expands+scrolls to `TrustPolicySection`. **Wire:** `AppState.appSettings`, settings-save path.
- **U-CONN2 (HIGH): MCP token/audit summary goes stale while sheet open.** (~4192–4353) Add a `.task`/timer periodically calling `await state.loadIntegrationTokens()` + audit reload (`loadTrust()`), OR add both into `refreshLiveCounts` (CortexApp.swift:4092, next to `refreshIntegrationStates()`). **Wire:** `AppState.loadIntegrationTokens`, `.loadTrust`.
- **U-CONN3 (MEDIUM): connected tools capped at `.prefix(3)`, test results never persist.** (~2760–2798, `connectedIntegrationRow` ~3119) Remove `.prefix(3)` or add "Show all N"; seed `testResults` from a new `state.lastToolTestResults[id]` cache updated in `testToolConnection`; optionally auto-run a light test on appear. **Wire:** new `AppState.lastToolTestResults`.
- **U-CONN4 (MEDIUM): silent copy — no confirmation on 3 button clusters.** (`desktopAppsStatusRow` "Copy tool config" ~2842–2851; `universalReachRow` "Connect extension"/"Copy API details" ~2926–2970) Add per-button `@State` copied flags with 2s reset (like `GitHubDeviceCodeView.copyCode` ~4764), swap label to "Copied" + checkmark. For `pairBrowserExtension`, show the copied pairing token + "Paste this into the Cortex browser extension" next-step.
- **U-CONN5 (MEDIUM): "Recent tool activity" no manual refresh.** (~4306–4342) Add ghost "Refresh" `CortexIconButton` → `Task { await state.loadTrust() }` (mirrors `TrustAuditSection` refresh at ~451); also refresh on expand.
- **U-CONN6 (MEDIUM): web-chat/connector rows don't pre-target.** (`browserAssistantRow` ~3214, `connectWizardEntry` ~2864) Give `presentConnectToolsWizard` an optional preselected `AIIntegration`/`connectionKind == .remoteMCP` filter so "Add a connector" lands on the remote-connector tools at the `.connect` step. **Wire:** `AppState.presentConnectToolsWizard` (new optional arg).
- **U-CONN7 (MEDIUM): no-detected/no-connected desktop row hands out a config blob with no destination.** (~2846–2851 else branch) Replace/supplement "Copy tool config" with a primary `state.presentConnectToolsWizard()` (guided pick→connect→verify), or surface `ConnectionsGuidedMCPSetup` on direct builds; min add a "Which apps work?" help link.
- **U-CONN8 (LOW): backup button no result.** (`ConnectionsPrivacyDefaultsSection` ~4177–4185) Show "Last backup <relative>" from `state.dataLifecycleReport?.backups` + spinner in-flight; reload `dataLifecycleReport` after `createBackup()`. Reuse `relativeCapturedLabel()`.
- **U-CONN9 (LOW): redundant duplicate export deep-links in walkthrough.** (`AIChatsImportCard` step 1 ~1286–1288 vs grid ~1235–1239) Drop the per-step duplicate links (or replace with "Already opened above ↑") so there's one canonical export affordance.

### === `macos/Sources/MemoryMapView.swift` ===

- **U-MAP1 (HIGH): empty state is a pure dead-end.** (~232–237) Pass `actionTitle`/`action` to `CortexEmptyState` (supported at CortexDesign.swift:871–892): primary "Bring your memory in" → `state.selectedTab = .review` (or inbound import); secondary "Ask Cortex something" → `.ask`.
- **U-MAP2 (HIGH): search has no not-found feedback / no jump-to-result.** (`searchField` ~290–316, `matchedNodeIDs` ~188) (1) When `matchedNodeIDs` is empty, show inline "No person, project, or topic matches "X"" instead of silently dimming all. (2) On exactly one match / Enter, `selectNode(id)` + pan/zoom via new `frameNode(id:)`. (3) Optional tappable results list.
- **U-MAP3 (MEDIUM): overlay not live while summoned.** (`ConstellationOverlay.swift` ~28–42/122–195) In `ConstellationOverlay.present`, start a Task calling `await state.loadGraph()` every ~6–8s until `dismiss()`, cancel in dismiss. **Wire:** `AppState.loadGraph`.
- **U-MAP4 (MEDIUM): composition-line counts inert.** (~906–941) Make each count segment a Button setting `spotlight = .type("People"/"Projects"/"Topics")` (reuse existing `MapSpotlight` lens the legend drives).
- **U-MAP5 (MEDIUM): no loading/error state — masks connection failure as "no memories yet".** (`onAppear loadGraph` ~260) Add `graphLoadState: idle/loading/loaded/failed` set in `loadGraph` (CortexApp.swift:4736); show ProgressView on loading, and a distinct `CortexEmptyState` + "Retry" on failed. **Wire:** new `AppState.graphLoadState`.
- **U-MAP6 (MEDIUM): connection `example` evidence hidden behind a decorative quote icon.** (`connectionsSection` ~1294–1317) Render `connection.example` as a truncated italic second line, or make the icon a disclosure/`exploreAction` seed.
- **U-MAP7 (LOW): node detail only "Explore in Ask".** (~359–366, `NodeDetailPanel` ~1262) Add evidence-first secondary "See memories" (scoped Ask retrieval or entity MOC page).
- **U-MAP8 (LOW): no "Fit to view".** (`cameraControls` ~619–651) Always show "Fit to view" that calls `resetCamera()` AND reseeds `workingPositions` from canonical layout.
- **U-MAP9 (LOW): legend hint under-teaches drag/zoom.** (~994–996) Rotate hint to teach "Drag a point to rearrange · scroll to zoom" (one-time via UserDefaults flag).

### === `macos/Sources/OnboardingView.swift` ===

- **U-ONB1 (HIGH): "Back Up Now" silent on failure/in-flight.** (`OnboardingFinishStep.backupRow` ~1666, `createBackup` CortexApp.swift:8369) Gate a ProgressView on new `state.backupInFlight`; on failure render `OnboardingNoticeBanner(severity:.warning)` + "Try again" (mirror `OnboardingRestoringStep.failed` ~730–746). **Wire:** new `AppState.backupInFlight`/backup error flag.
- **U-ONB2 (MEDIUM): illustrative tool chips should connect.** (`OnboardingToolChip` ~1489–1524) Add action closure → `state.presentConnectToolsWizard(statusMessage: "Connect \(name)")`; reflect connected state via `connectedAIIntegrationCount`/detection.
- **U-ONB3 (MEDIUM): Constellation preview stale on first sync.** (~1556–1566/832–842) Add `.onChange(of: state.connectorSyncingIDs)` / `state.onboardingHasSyncedMemory` re-calling `loadStats()`/`loadGraph()`, or drive off a live published value.
- **U-ONB4 (LOW): payoff footer primary is a self-defeating "Do this later".** (~249–260) When `connectedAIIntegrationCount == 0`, relabel to neutral "Continue" (or make footer primary invoke `presentConnectToolsWizard`); demote "Do this later" to a ghost link.
- **U-ONB5 (LOW): notch demo buried behind toggle.** (`OnboardingQuickCaptureRow` ~1753) Move "Show me the notch" out of the `if state.quickCaptureEnabled` block, or auto-trigger one `NotchNotifier` preview on first enable.
- **U-ONB6 (LOW): notes-folder primary falls back OUT of onboarding.** (`OnboardingAddMemoryStep.runConnectAction` ~1347) When `obsidianConnector` is nil, disable/spinner the card until `loadSourceConnectivity()` resolves rather than routing to `openConnectionsPrivacy()`; if possible call `connectLocalNotesFolder` with a synthesized local connector so it always connects in-flow.
- **U-ONB7 (LOW): RecallProofWatcher "Waiting…" forever for skip-tool users.** (~1571) When `connectedAIIntegrationCount == 0`, replace passive "Waiting…" with "Connect a tool to see this" → `presentConnectToolsWizard`.

### === `macos/Sources/MenuBarQuickPanel.swift` + `LiveActivity*.swift` + `QuickCapture.swift` (menu-bar / live surfaces) ===

- **U-LIVE1 (HIGH): screenshot-OCR capture path has ZERO callers (dead high-value feature).** (`QuickCapture.swift:350` `triggerScreenshotCapture()`) Wire a second recordable `KeyCombo` (e.g. `⌥⌘S`) registered in `QuickCapture.setEnabled` (second `RegisterEventHotKey`, `EventHotKeyID id:2`, routed in `quickCaptureHotKeyEventHandler` by `hotKeyID.id`), expose a `KeybindRecorderView` in settings. Alt: add "Capture screen region" to the menu-bar panel footer → `QuickCapture.shared.triggerScreenshotCapture()`. **Wire:** `AppState` quick-capture prefs (new `defaultScreenshot` combo).
- **U-LIVE2 (MEDIUM): menu-bar empty-answer "connect more sources" is dead text.** (`MenuBarQuickPanel.swift:257–259`) Add `CortexButton "Connect sources"` → set `state.selectedTab` to import/sources + `onOpenApp()`; and/or "Ask the full app" → `continueInCortex()`.
- **U-LIVE3 (MEDIUM): menu-bar Ask error has no Retry.** (~260–261) Add "Retry" → clear `askError` + re-run `askTask`/`runAsk()`; optional "Open Cortex" → `onOpenApp()`.
- **U-LIVE4 (MEDIUM): LiveActivityTicker card non-interactive.** (`LiveActivity.swift:780–853`, CortexApp.swift:9173) Drop `allowsHitTesting(false)` on the card (keep on trailing fade); add `onTapGesture` → set `state.selectedTab` + target based on `event.kind`, bring app forward.
- **U-LIVE5 (MEDIUM): permission-guidance toasts can't deep-link (notch is non-interactive).** (`QuickCapture.swift:262–268/355–361`) When `ensureAccessibilityPermission`/`ensureScreenRecordingPermission` returns false, directly `NSWorkspace.shared.open(URL("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"))` (and `Privacy_ScreenCapture`) alongside the toast.
- **U-LIVE6 (LOW): idle Ask surfaces only one inbox item.** (`MenuBarQuickPanel.swift:271–295`) When `state.inbox.count > 1`, add "Review N more" → `onOpenReview()`.
- **U-LIVE7 (LOW): menu-bar Ask field has no visible submit.** (~186–220) Add trailing `arrow.right.circle.fill` button → `runAsk()` when `!query.isEmpty`.
- **U-LIVE8 (LOW): collapsed pill body inert.** (`LiveActivityPill.swift:189–193`) Wrap collapsed HStack in a Button / `onTapGesture` → `onReview()`; hover still reveals explicit choices.

### === `macos/Sources/ImportDiffView.swift` + `MemoryWrapped.swift` (wrapped-diff — the viral/ingest surfaces) ===

- **U-DIFF1 (HIGH): missing-facts have no batch "Add all".** (`ImportDiffView.swift` `factGroup` .missing ~547–599) Add "Add all N to Cortex" in the .missing header → new async `addAllMissing([ImportDiffFact])` looping `state.addImportDiffFact`, inserting ids into `addedFactIDs`, progress in title ("Adding 3/25…"), disabled when all added. **Wire:** `AppState.addImportDiffFact`.
- **U-DIFF2 (HIGH): conflicting/stale facts are a dead-end.** (`ImportDiffFactRow` ~835–869, `factGroup` ~547–571) When `.conflicting`/`.stale`, render an action row: "Update Cortex with this" (→ `state.addImportDiffFact`), "Keep Cortex's" (dismiss); for stale, "Open in Cortex" via `match.memory_id`. **Wire:** `AppState.addImportDiffFact` + memory-open path.
- **U-DIFF3 (HIGH): the "what AIs think of you" verdict is un-shareable.** (`resultsSection`/`summaryHeader` ~439–476) Build `ImportDiffShareCard` (mirror `MemoryWrappedCard`'s `ImageRenderer`→`NSBitmapImageRep`→`NSSharingServicePicker`, MemoryWrapped.swift:525–582), 1200×630 night-sky ledger; add "Share result…" `CortexButton`. Min: "Copy summary" → verdict counts to `NSPasteboard`.
- **U-DIFF4 (HIGH): Memory-Wrapped first-week card is a dead-end at activation.** (`MemoryWrapped.swift` `MemoryWrappedEntry` ~627) Add primary `CortexButton "Connect an AI"` → `state.showConnectionsPrivacy = true` (or `showConnectToolsWizard`). **Wire:** `AppState.showConnectionsPrivacy`.
- **U-DIFF5 (MEDIUM): cited internal memory not tappable.** (`ImportDiffView.swift` `matchBlock` ~858–895) When `match.memory_id` non-empty, make "Cortex remembers: …" a Button → memory-detail path.
- **U-DIFF6 (MEDIUM): Wrapped card body/number/thumbnail not tappable.** (`MemoryWrapped.swift` ~633–637) Add `.onTapGesture { showSheet = true }` on the outer card VStack; keep the button as the visible affordance.
- **U-DIFF7 (MEDIUM): Wrapped sheet snapshots a stale headline.** (~412–583, ~653) Re-read `state.recallHeadline` reactively in `MemoryWrappedShareSheet` + `.task { await state.loadRecallHeadline() }` on open. **Wire:** `AppState.loadRecallHeadline`.
- **U-DIFF8 (MEDIUM): intro state has no sample/quick-win.** (`introState` ~416–424) Add "How to export from ChatGPT/Claude/Gemini" (per-vendor `NSWorkspace.open`) and/or "Try a sample" loading a bundled export into `pasted` + `compare()`.
- **U-DIFF9 (LOW): compare result not persisted (lost on reopen/second vendor).** (~631–647) Persist last `ImportDiffResult` + `addedFactIDs` on `state.lastImportDiff`; restore chip in `introState`. **Wire:** new `AppState.lastImportDiff`.
- **U-DIFF10 (LOW): first-week Wrapped card states are unreachable dead code.** (`MemoryWrapped.swift` ~412–583) Either surface a "Preview my card" in the isFirstWeek branch, or remove the unreachable firstWeek card states.
- **U-DIFF11 (LOW): weekly-notify opt-in with no permission feedback.** (~482–506) On toggle-on, `getNotificationSettings`; if `.denied` show caption + button to `x-apple.systempreferences:com.apple.preference.notifications`; else request auth immediately.

### === Home-tab-only items (`ModelTab.swift` Home hero, `MemoryWrapped.swift`) ===
*(U-MODEL2/3/7/8 above already cover the Home hero; the remaining Home-only stragglers:)*
- **U-HOME1 (HIGH): Twin scorecard "No grades yet" dead-end on Home** — same fix as **U-TWIN2** (shared component).
- **U-HOME2 (MEDIUM): Memory-Wrapped first-week card static on Home** — same fix as **U-DIFF4** (shared component).
- **U-HOME3 (LOW): Twin stat blocks non-interactive on Home** — same fix as **U-TWIN3**.

---

## FOUNDER-GATED SUMMARY (need OAuth broker secrets / signing / hosted flip)

- **P1/P2** — need hosted `/mcp` reachable + hosted token mint live for full end-to-end verify (client code shippable now; honest failure state ships now).
- **P3/P4** — client wiring shippable now; **go-live needs `CORTEX_BROKER_*_CLIENT_ID/SECRET` on the server**. Once shipped, "Available soon" flips with zero app rebuild.
- **App-Store-build carve-outs (not founder-gated, but distribution-gated):** P5 (Downloads watcher), U-CONN7, and detect/import are **direct-build only** — sandbox can't read Downloads. Guide behind `DistributionMode.isAppStore`.
- **Everything else (C1, A2–A6, all U-* interactivity/live-refresh items)** — **shippable now**, no secrets required. Still unsigned per current DMG state (xattr workaround), unchanged by these edits.

## HONEST-CEILING CALLOUTS (ideal impossible; best alternative stated)
- **ChatGPT/Claude/Gemini import (P5/P6, U-DIFF8):** no import API — export is emailed out-of-band. Ceiling = remove every click *after* download (watcher + auto-import) + a tracked "waiting for export" state. Cannot remove the export click or the provider's email delay.
- **Custom remote connectors (P8):** no host offers a one-click deeplink for a custom remote connector. Ceiling = precise per-host guided steps + separate URL/key copy fields.
- **Cursor/VS Code verify (P9):** Cortex can't read the tool's own MCP install state. Ceiling = await-token (kill the race) + surface the real first-call `RecallProofWatcher` on verify.
- **Preview persistence (A6):** persisting the unlock defeats the required-account gate — do NOT persist unlock; persist only a "previewed before" flag for softer return-visit copy.

## SUGGESTED EXECUTION ORDER
1. **C1** (completeness + account-pollution root; unblocks A1).
2. **P1 → P2 → P7** (fix + honestly verify the ChatGPT/remote hero path; do together — P2 depends on P1, P7 pairs with P1's mint).
3. **P3 → P4** (broker wiring; ship dark, founder flips secrets).
4. **P5 → P6** (Downloads auto-import + de-dup safety).
5. **A2, A3, A4** (account seams), then **P8, P9, A5, A6**.
6. **Tier-4 HIGH interactivity** (U-MAP1/2, U-REV1/2/3, U-ASK1/2, U-MODEL1/2, U-TWIN1/2, U-ONB1, U-CONN1/2, U-LIVE1, U-DIFF1/2/3/4) — grouped by file per the sections above so implementers don't collide.
7. Remaining MEDIUM then LOW polish per file section.

**Load-bearing files touched by multiple items (coordinate a single owner per file):** `CortexApp.swift` (C1, P1–P3, P5, P6, P9, A2–A6, U-REV6, and every `AppState` wire), `ConnectionsPrivacySheet.swift` (P5, P8, U-CONN1–9), `TwinAlertsViews.swift` (U-TWIN1–4 + Home mirrors), `ModelTab.swift` (U-MODEL1–9), `ImportDiffView.swift`/`MemoryWrapped.swift` (U-DIFF1–11).