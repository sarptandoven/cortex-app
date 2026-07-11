# Holistic functional audit — confirmed defects

26 confirmed (double-verified). Severity: {'critical': 1, 'high': 7, 'medium': 15, 'low': 3}


## #1 [CRITICAL] outbound — `macos/Sources/CortexApp.swift:7121`

**Defect:** The App Store 'Copy tool config' / guided-setup config points an MCP server at a non-existent, non-MCP endpoint: it emits {"type":"http","url":"<endpoint>/v1/tools","headers":{Authorization}}, but the backend's only MCP JSON-RPC endpoint is POST /mcp; there is no bare /v1/tools route at all (only GET /v1/tools/schema and POST /v1/tools/{name}).

**Repro:** On a Mac App Store build, open Connections > AI tools & permissions > 'Connect an AI tool manually', tap 'Copy configuration', paste into Claude Desktop's claude_desktop_config.json as instructed, restart Claude Desktop. No Cortex tools appear: an MCP client that honors type:http would POST initialize to http://127.0.0.1:8766/v1/tools and get 404/405 (that path is not routed and is not the MCP endpoint); clients that ignore type:http (Claude Desktop's stdio-only config) get a server with no command and fail to launch. Either way the promised 'Cortex memory tools appear once it restarts' never happens.

**Fix:** For the App Store loopback tool API, point the MCP url at the actual JSON-RPC endpoint ("url": "<endpoint>/mcp") rather than /v1/tools, and only emit a type:http server for clients that actually support remote/streamable-HTTP MCP. Since Claude Desktop config (referenced in the guided steps) is stdio-only, the App Store path should either document the loopback HTTP tool API (base URL + /v1/tools/schema + /v1/tools/call + bearer, as copyHTTPAPIDetails already does correctly) for function-calling clients, or ship a sandbox-safe stdio bridge — not a claude_desktop_config.json 'mcpServers' block that cannot work.


## #2 [HIGH] inbound — `macos/Sources/CortexApp.swift:6067`

**Defect:** The GitHub 'Sign in with GitHub' device flow reports success even when the first sync actually fails, leaving the user with a false 'connected' state, no saved token, and nothing imported.

**Repro:** In a direct/DMG build, click 'Sign in with GitHub' (onboarding grid or Connections library) and complete the browser device code with a GitHub account that has no repositories, OR whose repo discovery call errors (rate-limit/permission/network hiccup). completeGitHubDeviceFlow() (line 6035) discovers repos; on empty/failed discovery it builds a payload with NO 'repositories' key (lines 6063-6066) and calls syncDirectConnector (line 6067). The backend fetch_github_records raises ValueError('At least one GitHub repository is required') (backend/app/connectors/github.py:285-286) -> 422. syncDirectConnector catches the error internally and returns normally (CortexApp.swift:6239-6245), so completeGitHubDeviceFlow unconditionally sets the sheet to .done with 'Signed in. Cortex is building your memory.' (lines 6069-6073). The token is only persisted on sync SUCCESS (syncDirectConnector saves at line 6220-6221 after a successful decode), so nothing is saved and no GitHub account/source is created — the user believes GitHub is connected and syncing when it is not.

**Fix:** Make completeGitHubDeviceFlow observe the sync result. Have syncDirectConnector return a success/failure (e.g. Bool or throw), and in completeGitHubDeviceFlow set githubDeviceFlow?.phase = .done with a success message ONLY when the sync actually succeeded; on failure set a failure phase/message (e.g. 'Signed in, but no repositories were found to sync' / the surfaced error) via failGitHubDeviceFlow. Additionally, when discovery yields zero repos, either open the repository-picker setup sheet (ConnectorTokenSetupSheet with the token pre-filled) so the user can choose targets, or persist the token so a later manual repo selection can reuse it, instead of dropping it.


## #3 [HIGH] midpoint — `macos/Sources/CortexApp.swift:5445`

**Defect:** Async imports call announceLearned(count: saved + queued) immediately, firing the notch pill and bottom HUD 'Learned N new memories' celebration for items that are only QUEUED for background extraction and have produced zero memories yet.

**Repro:** User drags a ChatGPT export (imported with processing="async", CortexApp.swift:5415). The client sets added = result.saved + result.queued (line 5419) where every item is 'queued', not distilled. Line 5445 then calls announceLearned(added), which fires NotchNotifier 'Learned something new — N new memories' (CortexApp.swift:4402) and the bottom HUD celebrate() 'Learned N new memories' (LiveActivity.swift:154-155,311). Nothing has actually been learned — the conversations are only enqueued; the true memory count is unknown until the jobs drain and typically differs from the conversation count. The status text below is honest ('Building your memory in the background') but the prominent celebration lies.

**Fix:** Announce learned only from saved (sync) memories, or defer/replace the celebration for async imports with an honest 'Importing N conversations…' progress state, then celebrate the ACTUAL learned count after drainQueuedMemoryJobs completes. Do not include queued in a 'Learned' count.


## #4 [HIGH] midpoint — `macos/Sources/CortexApp.swift:6703`

**Defect:** Obsidian/connector sync calls announceLearned(count: saved + queued) and the backend emits per-memory 'learned' ticker events, but connector captures are forced to review_status='pending' (untrusted source) and excluded from retrieval until the user approves — so the app celebrates 'Learned N new memories' for memories that are not yet usable.

**Repro:** User connects an Obsidian vault (untrusted by default). save_memory_capture forces review_status='pending' (storage.py:9913-9915) so the memories are gated out of context by _memory_filters (storage.py:29466-29488). Yet _persist_memory emits kind='memory'/action='learned' for each one (storage.py:26564) and the client fires announceLearned('Learned N new memories', CortexApp.swift:6703). The live ticker and HUD claim the memories were learned while they actually sit in Review, invisible to every AI tool until approved. This also creates the import-auto-approve (default auto_approve=True, standalone_server.py:525) vs connector-review asymmetry with no honest signal distinguishing the two in the ticker.

**Fix:** Gate the 'learned' activity event and the announceLearned count on the capture actually being approved/usable (review_status='approved' or trusted source). For pending connector captures emit a distinct 'queued for review' event/state instead of 'learned', and only celebrate 'Learned N' once approved memories exist.


## #5 [HIGH] outbound — `macos/Sources/ConnectionsPrivacySheet.swift:2374`

**Defect:** The App Store guided-setup shows a preview config with an env block (CORTEX_API_KEY / CORTEX_BASE_URL, i.e. a stdio-style server) while the Copy button copies a totally different shape (type:http / url / headers from mcpServerDefinition's App Store branch). The 'what you see' does not match 'what you paste'.

**Repro:** On a Mac App Store build, expand 'Connect an AI tool manually'. The visible JSON shows cortex.env.CORTEX_API_KEY and CORTEX_BASE_URL. Tap 'Copy configuration' and paste elsewhere: the clipboard instead contains {"mcpServers":{"cortex":{"type":"http","url":".../v1/tools","headers":{"Authorization":"Bearer ..."}}}}. A user reconciling the pasted config against the on-screen preview sees two incompatible shapes and cannot tell which is correct.

**Fix:** Generate the preview from the same mcpServerDefinition(...) used by the copy path (with redactToken:true) so the redacted preview is byte-for-byte the real shape, instead of hardcoding a stale env-based literal. This should be fixed together with the /mcp-vs-/v1/tools defect so the preview reflects a config that actually works.


## #6 [HIGH] outbound — `macos/Sources/CortexApp.swift:6981`

**Defect:** In App Store builds the IntegrationCompactHero shows a prominent 'Connect' button with the copy 'Cortex can connect detected local AI tools automatically.', but tapping it calls installDetectedIntegrations(), which immediately returns the status 'App Store builds require advanced AI tool setup' — the exact opposite of what the button promised.

**Repro:** On a Mac App Store build with Claude Desktop (or Cursor) installed, open Connections > Developer & diagnostics > Integration center (or the compact hero). Because App Store integrationState is hardcoded configured:false while appInstalled can be true, detectedCount>connectedCount so needsConnection is true: the hero reads '<n> tools ready', 'Cortex can connect detected local AI tools automatically.' with a borderedProminent 'Connect'. Tapping Connect does nothing except set status 'App Store builds require advanced AI tool setup'. Nothing is connected; the user is dead-ended and misled.

**Fix:** In App Store mode, don't render the automatic 'Connect' hero/detail (installDetectedIntegrations is a no-op there); instead route the primary action to the copy-config / guided-manual-setup path with honest copy ('Copy setup config to paste into your AI app'), matching how ConnectionsAIToolsSection already gates its Enable-in-apps button behind !DistributionMode.isAppStore.


## #7 [HIGH] account-sync-privacy — `backend/app/storage.py:8809`

**Defect:** The local push-sync feed (capture_change_page) selects ALL captures by rowid with no review_status filter, so a capture the user REJECTED (archived) in Review is still pushed to the hosted cloud account with its full raw content — directly violating the app's promise 'Cortex won't remember archived items.'

**Repro:** Sign into a Cortex Cloud account (push-sync active). A capture arrives in Review (rowid N > pushCursor). Reject it in Review -> archiveCapture() calls /v1/captures/{id}/archive, which sets review_status='archived' but KEEPS the row and raw_text (storage.py archive_capture, line 15103-15115; it does not DELETE like delete_capture at 15071). On the next 5-min tick or pushSyncNudge, runPushSyncTick (CortexPushSync.swift:107-138) pulls that capture via GET /v1/sync/captures (served by capture_change_page, storage.py:8799, which has no review_status filter) and POSTs it to the hosted /v1/sync/ingest (main.py:2587), which re-extracts and store.save_capture()s the full raw content server-side. The ReviewTab UI explicitly told the user 'Cortex won't remember archived items' (ReviewTab.swift:305 and :751), but the rejected content is now permanently stored in the cloud account (and never retracted — push-sync is push-only and never propagates an archive/delete tombstone). Even content archived BEFORE it was pushed leaks the same way if its rowid is still above the cursor.

**Fix:** Exclude non-retained captures from the outbound push feed: add `AND review_status != 'archived'` (and ideally only push approved, or pending-not-yet-decided, captures) to the SELECT in capture_change_page (storage.py:8807-8811). Additionally, when a capture is archived/deleted after it may already have been pushed, propagate a retraction to the hosted side (a delete/tombstone in the sync protocol) so already-synced rejected content is removed from the cloud, honoring the 'won't remember archived items' guarantee.


## #8 [HIGH] backend-robustness — `backend/app/standalone_server.py:575`

**Defect:** CortexRequestHandler sets no socket timeout, so a request that declares a Content-Length within the 16 MiB cap but sends fewer bytes blocks the handler thread forever in self.rfile.read(length) while holding one of the (default 8) concurrency-gate slots; 8 such connections exhaust the gate and every /mcp and /v1/* request then returns 503 with the app still showing healthy.

**Repro:** Verified live on an isolated instance (port 8791). Opened 8 sockets that each sent `POST /v1/captures` with `Content-Length: 2000` but only 1 byte of body, then a normal `GET /v1/search` returned HTTP 503 'Cortex is busy handling other requests; retry shortly.' immediately, while `GET /health` and `/ready` still returned 200 (they bypass the gate). A single stalled connection stays blocked in rfile.read past an 8s wait with no server-side timeout. Any local process (or a buggy client/extension/agent loop) that can reach 127.0.0.1:8766 wedges every real API action; the app's health probe stays green so the user sees 'connected' but every capture/search/import/MCP call fails with 503, and it never self-recovers while the sockets are held.

**Fix:** Set a read timeout on the request-serving path so a stalled body read aborts and releases the slot. Simplest: add `timeout = 30` to CortexRequestHandler (BaseHTTPRequestHandler honors it as the socket timeout; it defaults to None). 30s is safely above the /v1/activity long-poll max of 8s, and that long-poll uses a threading.Condition (activity.py wait_since), not socket I/O, so a socket timeout does not affect it. Optionally also enforce a max wall-clock on _read_body. This releases the concurrency slot on a stalled/partial upload instead of holding it indefinitely.


## #9 [MEDIUM] onboarding-auth — `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/macos/Sources/CortexCloudAuth.swift:367`

**Defect:** A started browser/GitHub/Google sign-in locks the entire sign-in surface for up to 5 minutes with no way to cancel, so a user who abandons or gets stuck in the browser flow is stranded on the required-account wall.

**Repro:** Shipping build is direct + CortexRequireAccount=true, so a new user hits the sign-in wall. They click 'Sign in with GitHub' (or any browser provider, or the generic 'Continue in browser'). performCloudBrowserSignIn sets cloudAuthBusy=true and polls for up to 300s (line 399). Every other control — Apple button, all provider buttons, email/password 'Sign in', 'Create account' — is .disabled(state.cloudAuthBusy) (CortexCloudSection lines 850/865/878/900/906). The poll Task is fire-and-forget (line 225, not stored), and there is no Cancel button. If the user closes the browser tab, picks the wrong account, or the network hiccups, they cannot try email/password or another provider until the 5-minute timeout elapses; the only escape is dropping the account requirement via 'Explore with sample notes'.

**Fix:** Store the sign-in Task in an AppState property and add a visible 'Cancel' button (shown while cloudAuthBusy) that cancels it and resets cloudAuthBusy; and/or don't blanket-disable the email/password + other-provider controls during a browser poll so the user can switch methods. The Task.sleep loop already handles CancellationError (line 405-408: 'Browser sign-in cancelled.'), so wiring a cancel is low-risk.


## #10 [MEDIUM] onboarding-auth — `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/macos/Sources/OnboardingView.swift:733`

**Defect:** Enabling the quick-capture toggle without recording a shortcut silently registers no hotkey — the feature reports as 'on' but does nothing, with no warning.

**Repro:** In the shipping direct build, onboarding step 5 shows the live enableCard. User flips 'Enable quick capture' on (line 733) but does not click 'Record shortcut'/press keys. quickCaptureKeybind stays nil (default is nil, not KeyCombo.defaultCapture — CortexApp.swift line 3400). refreshQuickCaptureWiring calls QuickCapture.setEnabled(true, keybind: nil), which logs 'setEnabled(true) with no keybind — nothing registered' (QuickCapture.swift line 192-194) and registers no hotkey. The user believes quick capture is enabled but no shortcut ever fires; there is no in-UI indication that a shortcut is required.

**Fix:** When quickCaptureEnabled flips true and quickCaptureKeybind is nil, seed KeyCombo.defaultCapture (⌥⌘C) so the toggle is immediately functional, OR block/mark the toggle 'on' state until a keybind is recorded (disable Continue/show an inline 'Record a shortcut to finish enabling' hint) so 'enabled but inert' is impossible.


## #11 [MEDIUM] onboarding-auth — `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/macos/Sources/OnboardingView.swift:185`

**Defect:** The 'Your Archive is ready' celebration plays unconditionally on Finish even when no source was connected and onboarding cannot actually complete, telling the user setup succeeded when it did not.

**Repro:** The 6 narrative WalkSteps have no hard gate — Continue always advances (advance(), line 164), and Add-Memory can be passed without connecting a source or loading samples. On the last step the user clicks Finish; finishTapped() unconditionally sets celebrating=true and shows 'Your Archive is ready' (lines 185, 218), then after 1.15s calls state.finishOnboarding(). finishOnboarding() finds canCompleteOnboarding == false (no source) and silently falls back to dismissOnboardingForSession() (CortexApp.swift lines 7338-7345), so setup is NOT complete. The user just saw a full success celebration but lands on an empty app. (Recoverable — Home's HomeHeroSection still shows a 'Connect your notes' CTA — but the celebration is dishonest.)

**Fix:** Gate the celebration on canCompleteOnboarding: if setup can't complete, skip the 'Your Archive is ready' overlay and instead surface a short 'One more step — connect a source' message (and/or route the user back to the Add-Memory step) rather than claiming success.


## #12 [MEDIUM] inbound — `macos/Sources/ConnectionsPrivacySheet.swift:674`

**Defect:** Notion's fully-working integration-token connect path is completely unreachable in the shipping build because Notion advertises managed OAuth (whose client ID ships empty), which hides the tile and short-circuits the token fallback.

**Repro:** Backend fully supports Notion via a pasted internal-integration token: CONNECTOR_SETUP_BLUEPRINTS['notion'] has credential_fields=[{name:'token', required:True}] (backend/app/storage.py:1047) and 'notion' is in directConnectorSyncIDs (CortexApp.swift:3478). But the shipping Info.plist sets CortexNotionOAuthClientID to '' (macos/Info.plist:37-38), so managedOAuthIsConfigured(notion)=false and isUnconfiguredOAuth(notion)=true. browsableConnectors filters out unconfigured-OAuth connectors (ConnectionsPrivacySheet.swift:512-513), so Notion never gets a tile. Even if it did, libraryAction() returns early for any supportsManagedOAuth connector without ever falling through to the token setup sheet (selectedTokenConnector) (lines 674-679). The onboarding grid has the same block (OnboardingView.swift:444-457). Net: a user holding a valid Notion integration token has no way to connect Notion, despite the backend supporting it and it being listed in the '10k baseline' connector set.

**Fix:** When a managed-OAuth connector is NOT configured but has a usable credential-field fallback (Notion's durable integration token), don't hide it — render it as a token-setup tile. In libraryAction, for supportsManagedOAuth connectors where !managedOAuthIsConfigured but credential_fields includes a required non-ephemeral secret, fall through to selectedTokenConnector = connector (open ConnectorTokenSetupSheet) instead of returning. Mirror this in OnboardingView.connectOnboardingSource and adjust isUnconfiguredOAuth/browsableConnectors so Notion stays visible with the token path.


## #13 [MEDIUM] midpoint — `backend/app/extractor.py:339`

**Defect:** For conversational captures (has_known_turns=True) the local extractor caps prioritized candidates at BASE_EXTRACTION_CANDIDATE_LIMIT=40 and silently drops the rest; a long single-unit conversation pasted into the capture box (not chunked upstream) loses everything past the top 40 candidates when running deterministic extraction.

**Repro:** A user pastes a long multi-turn conversation into the capture box, or an un-chunked conversational note is imported. _extraction_candidate_limit (extractor.py:338-339) returns 40 for has_known_turns, and _prioritized_extraction_candidates (extractor.py:653-661) keeps only the top 40 by priority, discarding the remainder with no warning. The non-conversational path scales up to MAX_EXTRACTION_CANDIDATE_LIMIT=2000 (line 340-343) specifically to avoid this, but conversational flat captures don't get that scaling. If ANTHROPIC_API_KEY is set the capture-box path windows correctly, so the drop is limited to the deterministic/no-key case — which is also the default for imports (finding #1).

**Fix:** Scale the conversational cap with content too (or window like the Claude path) when the capture is a single un-chunked unit, or surface an honest 'kept top 40 of N candidates' note rather than dropping silently. Given imports are always deterministic (finding #1), the practical exposure is real.


## #14 [MEDIUM] outbound — `macos/Sources/CortexApp.swift:4745`

**Defect:** 'Test' for mcpConfig/cliCommand/httpAPI tools only probes Cortex's own /v1/tools/schema and reports 'Connected — N Cortex tools available'. It never inspects the tool's config file, token, or reachability, so it reports success even when the integration's config is missing, points at the wrong token/URL, or the host app was never actually wired up.

**Repro:** Connect Claude Desktop, then corrupt or delete Claude's claude_desktop_config.json (or reset the MCP token so the pasted config is stale). In Connections > AI tools, tap 'Test' on the Claude Desktop row. As long as the local Cortex server answers /v1/tools/schema, the row shows a green check 'Connected — N Cortex tools available', falsely implying Claude Desktop can reach memory when its config is broken.

**Fix:** For config-based tools, base the pass/fail on the integration's own state (configHasVerifiedCortexServer for the tool's configTargets, matching endpoint + a registered token) rather than a generic self-ping; word the success message about that specific tool ('Claude Desktop config verified') and fail with an actionable message when the config is absent/stale. Keep the self-ping only as the memoryPack-style reachability check.


## #15 [MEDIUM] outbound — `macos/Sources/CortexApp.swift:11371`

**Defect:** The only entry points to pair the browser extension (mint the /v1/pair read-only token) and to copy universal-API/SDK connection info live in the menu-bar right-click status menu ('Connect browser extension…', 'Copy API connection info…'). Neither appears in the Connections & Privacy sheet or the Integration Center, where all other 'use your memory outside Cortex' UI lives.

**Repro:** Open Connections > 'AI tools & permissions' (and 'Developer & diagnostics' > Integration center). There is no control to pair the shipped browser extension or to get SDK/REST connection details; the extension's options page requires a token the user has no in-app path to obtain from this surface. A user who installed the Cortex Memory extension (or wants the Python/TS SDK) has no way to get a token unless they discover the hidden right-click menu.

**Fix:** Surface 'Connect browser extension…' (pairBrowserExtension) and 'Copy API connection info…' (copyUniversalAPIConnectionInfo) as rows in ConnectionsAIToolsSection / IntegrationCenterView, alongside the MCP and memory-pack actions, so the extension/SDK path is reachable from the same place users go to connect external tools.


## #16 [MEDIUM] outbound — `macos/Sources/CortexApp.swift:7036`

**Defect:** integrationState(for:) hardcodes configured:false in App Store builds, so even after a MAS user successfully pastes a working config, no tool ever shows as 'connected': the ConnectionsAIToolsSection status hero never reads 'Enabled in N apps', the connected-integration rows (with the inline 'Test' button) never render, and IntegrationCompactRow never shows the green 'Connected' pill.

**Repro:** On a Mac App Store build, wire up a tool via the copy-config path and confirm it works in the AI app. Return to Connections > AI tools: the hero still says 'Connect Claude Desktop or another tool', no connected rows or 'Test' controls appear, and the tool row still offers 'Connect'/'Copy'. The UI permanently understates what the user has achieved and gives MAS users no way to verify or manage an established connection.

**Fix:** Track connected state for App Store builds via a lightweight signal that does not require reading other apps' sandboxed files — e.g. persist per-integration 'config copied/marked connected' locally, and/or key off registered MCP tokens + last_used_at from /v1/integrations/tokens — so a MAS user who connected a tool sees it as connected and can Test/manage it, instead of the state being frozen at 'not connected'.


## #17 [MEDIUM] core-surfaces — `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/macos/Sources/ReviewTab.swift:416`

**Defect:** The "Approve N shown" button labels itself with the full visible count but the underlying approveCaptures() silently caps the batch at 10, so it approves fewer items than it promises with no indication.

**Repro:** In Review with >10 pending items, click "Show more" once so 20 cards are visible. The header button now reads "Approve 20 shown". Click it. approveCaptures(visibleCaptures) is called with 20 items but at CortexApp.swift:7658 it does `Array(captures.prefix(10))`, so only 10 are approved. Status reads "Approved 10 review items", 10 cards remain, and the button now says "Approve 10 shown" — the user was told 20 would be approved and half silently weren't.

**Fix:** Either cap the displayed count to match (label "Approve 10 shown" and only ever show up to 10 in the batch affordance), or remove the 10-item cap in approveCaptures() and loop over all of visibleCaptures. Cleanest: pass `Array(visibleCaptures.prefix(10))` from the call site so the label uses the same 10 (`min(visibleCount,10)`), keeping label and action identical.


## #18 [MEDIUM] core-surfaces — `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/macos/Sources/ModelTab.swift:526`

**Defect:** The Home hero headline and its primary button are computed with different priority orders for pending-review vs due-sync, so when both are true the headline says one thing and the button does another.

**Repro:** Have a source that is both (a) holding pending items in Review and (b) past its sync interval (sourceReadinessReport with pending>0 AND a source whose sync_plan.due_now == true). `title` checks pendingCount before dueSyncSources (lines 526→531) and shows "Review new memory". But `actionTitle`/`actionIcon`/`runNextAction` check dueSyncSources before pendingCount (lines 601-603 / 613-615 / 728-738), so the primary button reads "Sync now" with a sync icon and, when clicked, calls openConnectionsPrivacy("Sync connected sources") — it opens the Connections sheet to sync instead of taking the user to Review as the headline instructed.

**Fix:** Make the three computed properties share one ordered state enum (compute the hero state once, then derive title/actionTitle/icon/action from it) so headline, button label, icon and tap action can never disagree. If the intended priority is 'review first', move the pendingCount branch above dueSyncSources in actionTitle/actionIcon/runNextAction to match `title` (or vice-versa).


## #19 [MEDIUM] core-surfaces — `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/macos/Sources/CortexApp.swift:4698`

**Defect:** purgeSource() only refreshes source-stats, stats and recent after a destructive delete; it does not refresh the graph, the source-readiness report, or the review inbox, so the Constellation and several counts stay stale until an unrelated reload — contradicting its own "the whole app reflects the removal" comment.

**Repro:** Open Connections → Manage stored data → "Delete all" for a source that contributed graph nodes and pending items (e.g. ChatGPT with 40 memories). The purge succeeds and the row + Home memory count update. But purgeSource (lines 4698-4702) calls only loadSourceStats/loadStats/loadRecent — NOT loadGraph, loadSourceConnectivity (which sets sourceReadinessReport), or loadInbox/loadReview. Result: Home's "Your Constellation" still renders nodes derived from the purged source, and the Home source-status row, Ask memory-context strip, and Review health strip still count the purged source's memories, until the user switches tabs (Ask/Review reload connectivity on tab change) or restarts. Unlike approveCapture/archiveCapture (CortexApp.swift:7603-7610, 7634-7639) which refresh loadGraph/loadReview/etc., purge is inconsistent.

**Fix:** After a successful purge also await loadGraph(), loadSourceConnectivity(), loadInbox(), loadReview() (mirror the refresh set the approve/archive paths already run), so every surface that showed the purged source's data updates immediately.


## #20 [MEDIUM] account-sync-privacy — `macos/Sources/CortexCloudAuth.swift:481`

**Defect:** 'Delete account' deletes only the HOSTED account/cloud data and clears the session; it never touches the local-first memory vault, yet the confirmation and success copy claim it deletes 'all of its memory' / 'all its data were permanently deleted.'

**Repro:** In Connections & Privacy > Developer & diagnostics > Cortex Cloud, tap 'Delete account…'. The confirmation reads 'This permanently deletes your account and all of its memory. This cannot be undone.' (CortexCloudSection, CortexCloudAuth.swift:787). performDeleteCloudAccount (CortexCloudAuth.swift:481-502) DELETEs /v1/auth/account on the hosted backend (which crypto-shreds + deprovisions cloud data), then calls resetToLocalDefaults() and shows 'Your account and all its data were permanently deleted.' (line 498). But in the local-first (Option A) model the primary memory lives on 127.0.0.1 and resetToLocalDefaults (line 534-547) explicitly KEEPS the local data plane ('The data plane was already local (Option A). Keep it local'). Result: every capture/memory/graph/note the user has is still fully present on the Mac, contradicting the 'all of its memory' / 'all its data ... permanently deleted' claims. A user who deletes their account to erase their data is left with all of it intact locally.

**Fix:** Either (a) make the copy accurate — say the cloud account and its synced copy are deleted while local memory stays on this Mac (and point users to 'Delete All Local Data' to erase the local vault), or (b) after a successful account DELETE, also run the local wipe (deleteAllUserData / DELETE /v1/user-data) so the claim 'all of its memory' actually holds. Do not assert 'all data permanently deleted' while the local vault is untouched.


## #21 [MEDIUM] account-sync-privacy — `macos/Sources/CortexPushSync.swift:135`

**Defect:** The push-sync cursor is a persisted SQLite rowid, but restoring a backup reinserts all captures with fresh rowids while the cursor persists, so the feed can return empty and show 'Synced ✓' even though restored captures were never re-reconciled against the cloud.

**Repro:** Signed in with push-sync, pushCursor advances to e.g. 500 (CortexPushSync.swift:135, persisted in UserDefaults key cortexPushCursor.v1). The user then restores a backup: restoreLatestBackup() (CortexApp.swift:7780) -> /v1/backups/restore-latest -> storage.restore_latest_backup (storage.py:18923) -> rebuild_index_from_vault, which DELETEs and re-INSERTs all captures (storage.py:19309 + re-insert). Because captures is `id TEXT PRIMARY KEY` (not WITHOUT ROWID; database.py:20-37), every capture gets a brand-new implicit rowid starting from 1. restoreLatestBackup does NOT clear the push cursor. If the restored vault has fewer captures than the pre-restore max rowid (post-restore max rowid < 500), the feed query `rowid > 500` (capture_change_page, storage.py:8809) returns nothing, runPushSyncTick immediately sets pushSyncState = .synced (CortexPushSync.swift:140), and the UI shows 'Synced ✓' though the restored captures were never pushed/re-reconciled in this session.

**Fix:** Clear the push cursor (clearPushSyncState) after any restore/rebuild that reassigns rowids, forcing a full re-scan from 0 (safe: ingest is idempotent by client_capture_id). Better, make the outbound cursor independent of a reassignable SQLite rowid — e.g. track pushed state per client_capture_id or use a monotonic sync-sequence column that survives rebuilds — so restore/repair never strands captures behind a stale watermark.


## #22 [MEDIUM] backend-robustness — `backend/app/standalone_server.py:39`

**Defect:** The module-level startup runs store.ensure_vault_backfilled(...) before the server binds the port, and inside it _backfill_memory_markdown (storage.py:3302) is the one step NOT wrapped in try/except; any exception it raises (e.g. a connect()/DB error on a damaged vault) propagates out of import and kills the backend process before it can bind, so the app's health probe never succeeds and the user is stuck on 'Local memory engine did not become ready' with no in-app recovery.

**Repro:** If index.sqlite is corrupted or a legacy-memory row is unreadable at first boot, ensure_vault_backfilled -> _backfill_memory_markdown throws; the process exits at import (standalone_server.py:39). The Swift ensureRunning loop (CortexApp.swift:2592) sees the process not running and surfaces a dead-end 'did not become ready' / port-in-use message; the readiness self-heal (prune_orphans) that was added specifically to avoid startup stalls never runs because backfill precedes it and is unguarded.

**Fix:** Wrap the startup store.ensure_vault_backfilled(...) call at standalone_server.py:39 (and the ensure_mcp_token call) in try/except that logs and continues so a non-fatal migration/backfill error can't prevent the server from binding, mirroring how migrate_memory_note_filenames/regenerate_vault_pages/prune_orphans are already guarded inside ensure_vault_backfilled (storage.py:3305-3321). Let the readiness gate report the degraded state instead of crashing before bind.


## #23 [MEDIUM] backend-robustness — `backend/app/vault.py:1482`

**Defect:** restore_from_zip_backup deletes each live vault directory with shutil.rmtree BEFORE copytree-ing the restored copy in, with no rollback; if the copy fails partway (disk full, permission error, corrupt archive member) the user's live vault directory is already gone, so a failed restore leaves the vault half-wiped and unrecoverable.

**Repro:** User picks Restore latest backup (confirmed via the destructive alert in CortexApp.swift:10726). restore_latest_backup (storage.py:18944) does not snapshot the current vault first; restore_from_zip_backup then does rmtree(memories/) followed by copytree from the temp extraction. If the machine runs out of disk (or the archive is truncated) after the rmtree but during/after the copytree of a large directory, the original memories are already deleted with nothing to fall back to. rebuild_index_from_vault then rebuilds an index over whatever partial data remains.

**Fix:** Make the swap atomic/rollback-safe: extract to the temp dir (already done), then for each RESTORE_DIRECTORIES move the current live dir to a sibling `.restore-old` path (os.replace) BEFORE moving the restored dir into place, and only unlink the `.old` copies after all directories are swapped successfully; on any failure, restore the `.old` copies. Alternatively, take an automatic pre-restore create_backup() at the top of restore_latest_backup so a failed restore is recoverable.


## #24 [LOW] inbound — `macos/Sources/ConnectionsPrivacySheet.swift:632`

**Defect:** The only explanation shown when Gmail/Drive/Outlook/Notion are hidden (unconfigured OAuth) names just 'Email and Drive', silently omitting Notion and Outlook so users searching for those get no guidance at all.

**Repro:** In the shipping build all managed-OAuth client IDs are empty (Info.plist:29-40), so Gmail, Google Drive, Outlook, and Notion are all filtered out of the Connections library (browsableConnectors, lines 512-513). hasHiddenOAuthConnectors becomes true and the sheet renders a single footnote: 'Email and Drive connect via export import for now.' (line 632). A user who opens Connections looking for Notion or Outlook sees no tile and no mention of them — they can't tell whether the connector is missing, broken, or coming, and aren't pointed at the export-import path that does exist (Notion Markdown/CSV export and Outlook mbox/ICS/vCard all parse via source_ingest.py).

**Fix:** Make the footnote name the actually-hidden set dynamically (build it from the connectors that hasHiddenOAuthConnectors matched, e.g. 'Gmail, Google Drive, Outlook, and Notion aren't available for direct sign-in in this build yet — import them via file/export import'), and link to or open the export-import card so the alternate working path is discoverable.


## #25 [LOW] core-surfaces — `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/macos/Sources/CortexApp.swift:4821`

**Defect:** The Mirror Moment's positive "That's right" button is functionally identical to "Not quite": both just hide the card and suppress re-showing it locally; the confirmation sends no signal to the backend, so an explicit user confirmation delivers nothing to the product.

**Repro:** On Home, a Mirror Moment card appears ("CORTEX NOTICED …") with "That's right" and "Not quite". Tapping "That's right" (confirmMirrorInsight, CortexApp.swift:4821) calls rememberMirrorDismissal(insight.dismissKey) + sets status "Thanks — noted." — the exact same suppression path as dismissMirrorInsight (4831). No confirmation is round-tripped (no trust bump, no reinforcement) so the affirmative and negative answers are indistinguishable to the system; the card cannot re-surface to reinforce the memory either.

**Fix:** Have confirmMirrorInsight POST a positive signal for the underlying memory (e.g. a trust/confirmation endpoint or a captures-style reinforcement) so a user confirming 'it knows me' actually strengthens that memory, distinct from a dismissal. If v1 truly can't act on it, drop the two-button choice to a single "Got it" so the UI doesn't imply the answer matters when it doesn't.


## #26 [LOW] account-sync-privacy — `macos/Sources/CortexCloudAuth.swift:694`

**Defect:** When signed in but canPushSync is false (e.g. cloudSyncBaseURL missing from UserDefaults while the refresh token persists in Keychain), startPushSync no-ops and the status stays .idle, so the UI shows 'Your memory stays on this Mac and syncs to your account' even though nothing will ever sync.

**Repro:** isSignedIn is derived from the Keychain refresh token (CortexCloudAuth.swift:124), while cloudSyncBaseURL is restored from UserDefaults (CortexApp.swift:3257). Keychain items can outlive UserDefaults (e.g. certain reinstall/migration scenarios, or a user who clears app defaults), leaving isSignedIn=true but cloudSyncBaseURL="". canPushSync (CortexPushSync.swift:56) then evaluates false, so startPushSync() returns early (line 65) and pushSyncState stays .idle. signedInView still renders and pushSyncStatusView's .idle branch shows 'Your memory stays on this Mac and syncs to your account.' (CortexCloudAuth.swift:692-694) — a promise of syncing that will never occur, with no error surfaced.

**Fix:** In the signed-in status view, detect the not-actually-syncable state (isSignedIn && (cloudSyncBaseURL.isEmpty || requiresSignIn)) and show an actionable message ('Sync is unavailable — sign in again to reconnect your account') instead of the reassuring idle copy; or re-derive/repair cloudSyncBaseURL from defaultHostedURL when a refresh token exists but the base was lost.
