I have verified all the load-bearing claims against the actual code. I have everything I need to produce the design.

---

# Phase-2 Local→Hosted Background Push-Sync — Implementation Design

All claims below are grounded in the actual code. Key verified facts:

- **No memory `created`/`updated` events exist.** The memory upsert (`INSERT OR REPLACE INTO memories`) at `backend/app/storage.py:17095-17127` emits **no** `_event(...)`. Only `memory/archived` (`storage.py:11814`) and `memory/deleted` (`storage.py:11826`) are emitted. So `/v1/sync/changes` **cannot enumerate created/updated memories.**
- **`/v1/sync/changes` is content-free.** `content_included` is hard-coded `False` (`storage.py:15770`); event rows carry only redacted metadata (`_sync_event_from_row`, `storage.py:15811+`).
- **No hosted ingest endpoint.** `grep` for `sync/apply|sync/ingest|sync/push` returns nothing. The only write path into the per-user hosted store is `POST /v1/captures` (`main.py:622`), which **re-extracts** raw text server-side.
- **`CaptureRequest` has no idempotency key** (`models.py:11-16`): no `capture_id_override`, no `external_id`. But `save_capture` internally supports both (`storage.py:8906-8910`) with `ON CONFLICT(id) DO UPDATE` (`storage.py:8991`) — the machinery exists, it's just not reachable over HTTP.
- **Client has creds + a cadence pattern to mirror, but no push code.** `refreshCloudAccessToken()` (`CortexCloudAuth.swift:380`), `cloudPost()` (`CortexCloudAuth.swift:522`, no 401-refresh), `startConnectedSourceAutoSync` cadence (`CortexApp.swift:4507`), `announceLearned` choke point (`CortexApp.swift:3994`), sign-out teardown `resetToLocalDefaults()` (`CortexCloudAuth.swift:471`).

---

## 1. PROTOCOL

**Decision: push RAW capture content and let the hosted backend re-extract. Do NOT push extracted memory records.** Rationale: the hosted store already extracts on ingest via `extract_context` + `save_capture` (`main.py:640-654`); pushing verbatim memory rows would require a new record-level ingest that bypasses extraction and risks local/hosted divergence in `provenance`/`topics`/`entity_ids`. Raw-content push reuses the entire mature ingest path. The unit of sync is the **capture**, not the memory — captures are the durable source; memories are derived and server-minted (`storage.py:17014`).

**Reuse:** `save_capture`'s idempotent upsert (`storage.py:8887`, `ON CONFLICT(id) DO UPDATE`).

**Add (two real gaps, justified):**

### A. Client-supplied idempotency key on captures (small, required)
`CaptureRequest` cannot carry a stable id, so re-pushing after a failed batch mints duplicates (falls to the content+source+timestamp hash `storage.py:8912`). Add fields and thread them through:

- `backend/app/models.py:11-16` — add to `CaptureRequest`:
  ```python
  capture_id_override: str | None = Field(default=None, max_length=80)   # "cap_..."
  captured_at: str | None = Field(default=None, max_length=40)           # pin timestamp for stable id
  ```
- `backend/app/main.py:645-654` — pass `capture_id_override=request.capture_id_override` into `store.save_capture(...)` (async branch `main.py:630-639` → thread into `enqueue_capture` too, or restrict async off for sync-push).

This alone makes the single-item path idempotent. But N captures = N round-trips + N extractions. So also add:

### B. Batch ingest endpoint `POST /v1/sync/ingest` (the one genuinely-new endpoint)
Symmetric to the existing pull feed. Justification: no batch upsert exists; per-item `/v1/captures` is expensive at scale (full `extract_context` per HTTP call) and gives no per-item accepted/duplicate ack for resumable retry.

- **New model** `SyncIngestRequest` (`models.py`): `{ device_id: str, cursor: str, items: [SyncIngestItem] }` where `SyncIngestItem = { client_capture_id: str ("cap_..."), content, source, source_url, title, captured_at }`.
- **New endpoint** in `main.py` (near the existing sync routes ~`main.py:1962`):
  ```python
  @app.post("/v1/sync/ingest", response_model=SyncIngestResponse)
  def sync_ingest(request: SyncIngestRequest, user_id: str = Depends(auth)):
      _enforce_memory_quota(user_id)
      results = []
      for item in request.items:
          extracted = extract_context(item.content, item.source, ...)
          saved = store.save_capture(user_id=user_id, content=item.content,
                                     source=item.source, source_url=item.source_url,
                                     title=item.title, extracted=extracted,
                                     capture_id_override=item.client_capture_id,
                                     cite_capture_provenance=True,
                                     auto_approve=settings.auto_approve_captures)
          results.append({"client_capture_id": item.client_capture_id,
                          "capture_id": saved["capture_id"], "status": "accepted"})
      # Persist the device high-watermark via the existing receipt store:
      store.record_sync_receipt(user_id, request.device_id, cursor=request.cursor,
                                status="applied", stats={"count": len(results)})
      return {"applied": len(results), "cursor": request.cursor, "results": results}
  ```
  Idempotent by construction: `save_capture(capture_id_override=...)` → `ON CONFLICT(id) DO UPDATE` (`storage.py:8991`); re-pushing the same batch is a no-op upsert. Reuses `record_sync_receipt` (`main.py:1379`, `storage.py`) as the server-side high-watermark.

**Push payload (client → hosted):**
```json
POST /v1/sync/ingest   Authorization: Bearer cxs_...
{ "device_id":"dev_...", "cursor":"<local monotonic seq>",
  "items":[ {"client_capture_id":"cap_<localid>","content":"...","source":"macos",
             "source_url":null,"title":"...","captured_at":"2026-07-08T..Z"} ] }
```

---

## 2. LOCAL CHANGE FEED (the second real gap)

`/v1/sync/changes` is unusable for content push (no created/updated events, no content). The first shippable slice needs a **local, incremental, content-bearing capture feed**. Two sub-options; pick the monotonic-seq one to avoid the ISO-timestamp tiebreaker fragility (`storage.py:15722-15743`) and the `cursor_not_found` reset trap (`storage.py:15716`):

**Add `GET /v1/sync/captures?after_seq=<int>&limit=`** on the **local** backend (`main.py` / `standalone_server.py`), backed by a new `Storage.capture_change_page(user_id, after_seq, limit)`:
- The `captures` table has monotonic `rowid`. Query `SELECT rowid AS seq, id, source, source_url, title, raw_text, captured_at FROM captures WHERE user_id=? AND rowid > ? ORDER BY rowid ASC LIMIT ?`.
- Returns `{ items:[{seq, client_capture_id:id, content:raw_text, source, source_url, title, captured_at}], next_seq, has_more }`.
- `rowid` is a true monotonic integer sequence — no hash-cursor ties, no purge-reset problem.

This is the minimal new local surface. (Deletes/tombstones are **out of the first slice** — see §5.)

---

## 3. CLIENT SyncEngine

New file `macos/Sources/CortexPushSync.swift` as an `AppState` extension, mirroring `CortexCloudAuth.swift`'s extension style.

**Cadence + hooks** (mirror `startConnectedSourceAutoSync` at `CortexApp.swift:4507`):
```swift
private var pushSyncTask: Task<Void, Never>?
private var pushSyncInFlight = false   // mirror jobDrainInFlight (CortexApp.swift:4590)

func startPushSync(initial: Bool = true) {
    pushSyncTask?.cancel()
    guard isSignedIn, !cloudSyncBaseURL.isEmpty, !requiresSignIn else { return }  // gates: CortexCloudAuth.swift:108/114/133
    pushSyncTask = Task { [weak self] in
        if initial { await self?.runPushSyncTick() }
        while !Task.isCancelled {
            do { try await Task.sleep(nanoseconds: 5 * 60 * 1_000_000_000) } catch { return }
            await self?.runPushSyncTick()
        }
    }
}
```
- **Hook into bootstrap:** add `startPushSync()` right after `startConnectedSourceAutoSync()` at `CortexApp.swift:3625`. Re-invoke (initial:false) at the same source/pause change sites (`5523/5533/5558/5846/6083`) is unnecessary; only re-run after sign-in. The post-sign-in bootstrap re-run (`CortexCloudAuth.swift:468`) already re-enters bootstrap, so `startPushSync()` restarts there automatically.
- **Trigger-on-learn:** inside `announceLearned(count:)` (`CortexApp.swift:3994`) add `Task { await pushSyncNudge() }` — a debounced kick that calls `runPushSyncTick()` unless `pushSyncInFlight`.

**Tick body:**
```swift
private func runPushSyncTick() async {
    guard !pushSyncInFlight, isSignedIn, !requiresSignIn else { return }
    pushSyncInFlight = true; defer { pushSyncInFlight = false }
    pushSyncState = .syncing
    var cursor = loadPushCursor()          // Int seq, UserDefaults
    do {
        while true {
            // 1. pull local changed captures WITH content
            let page = try await request(path: "/v1/sync/captures?after_seq=\(cursor)&limit=100", method: "GET")
            let decoded = try JSONDecoder().decode(CaptureChangePage.self, from: page)
            if decoded.items.isEmpty { break }
            pendingCount = decoded.items.count
            // 2. push batch to hosted with refresh-on-401
            let body = ["device_id": pushDeviceID, "cursor": String(decoded.next_seq),
                        "items": decoded.items.map(\.ingestDict)]
            _ = try await cloudRequest(path: "/v1/sync/ingest", body: body)  // NEW wrapper, §below
            // 3. advance + persist cursor ONLY after the batch is acked
            cursor = decoded.next_seq
            savePushCursor(cursor)
            if !decoded.has_more { break }
        }
        pushSyncState = .synced(Date()); pendingCount = 0
    } catch {
        pushSyncState = .error(CortexCloudAuth.describe(error))   // offline/500 → keep cursor, retry next tick
    }
}
```

**Refresh-on-401 for the cloud leg** (`cloudPost` has none — `CortexCloudAuth.swift:518-537`). Add a thin `cloudRequest` mirroring `request()`'s branch at `CortexApp.swift:7300-7308`:
```swift
private func cloudRequest(path: String, body: [String: Any]) async throws -> Data {
    let base = cloudSyncBaseURL.trimmingCharacters(in: CharacterSet(charactersIn:"/"))
    do { return try await cloudPost(base: base, path: path, body: body, bearer: cloudAccessToken) }
    catch {
        if isUnauthorizedCloudError(error) {           // 401 from CortexCloudAuthError.httpStatus
            if await refreshCloudAccessToken() {        // CortexCloudAuth.swift:380
                return try await cloudPost(base: base, path: path, body: body, bearer: cloudAccessToken)
            }
            handleCloudSessionExpired(); throw error    // CortexCloudAuth.swift:443
        }
        throw error
    }
}
```

- **Batching:** fixed `limit=100` per page; cursor advances per acked batch, so a mid-backlog failure resumes at the last persisted seq.
- **Retries/backoff:** no in-loop retry needed for the first slice — a failed tick just leaves the cursor unadvanced and the next 5-min tick (or next learn) resumes. Optionally add exponential backoff on consecutive failures by lengthening the sleep. Offline is handled the same way: `URLError` → `.error` state, cursor untouched, natural resume.
- **Sign-out stops it:** add `pushSyncTask?.cancel(); pushSyncState = .idle; pushDeviceID = ""` inside `resetToLocalDefaults()` (`CortexCloudAuth.swift:471`). Because the gate checks `isSignedIn && !cloudSyncBaseURL.isEmpty`, and reset clears both, any in-flight tick also self-terminates.

**Device registration (once):** on first successful `startPushSync`, if `pushDeviceID` empty, `POST /v1/sync/devices` (`main.py:1351`, `SyncDeviceRequest`) against `cloudSyncBaseURL` via `cloudRequest`, persist returned id in `cortexPushDeviceID.v1`. Enables the hosted receipt/high-watermark ack.

---

## 4. STATE

- **Client push cursor:** new UserDefaults key `cortexPushCursor.v1` (Int seq), alongside the keys at `CortexApp.swift:2979-2982`. Clear it in `resetToLocalDefaults` on sign-out (fresh account → resync from 0).
- **Client device id:** `cortexPushDeviceID.v1`.
- **Client status:** new `@Published var pushSyncState: PushSyncState` (`.idle/.syncing/.synced(Date)/.error(String)`) and `@Published var pendingCount: Int`.
- **Server receipt:** reuse `sync_receipts` via `record_sync_receipt` (`main.py:1379`) written inside `/v1/sync/ingest` — stores `{cursor, status, stats}` per device as the durable server-side high-watermark. Do **not** overload the inbound `sync_cursors` table (`storage.py:7691`, external-ingestion only).

---

## 5. STATUS UI

Minimal surface in `CortexCloudSection.signedInView` (`CortexCloudAuth.swift:625`). Replace the static line at `:633` ("Your memory stays on this Mac and syncs to your account.") with a state-driven row:
```swift
switch state.pushSyncState {
case .idle:          Label("Your memory stays on this Mac and syncs to your account.", systemImage:"icloud")
case .syncing:       Label("Syncing… \(state.pendingCount) pending", systemImage:"arrow.triangle.2.circlepath")
case .synced(let t): Label("Synced ✓ · \(t.relativeShort)", systemImage:"checkmark.icloud")
case .error(let m):  Label(m, systemImage:"exclamationmark.icloud").foregroundColor(.orange)
}
```
Reuse `beginMenuBarWork/endMenuBarWork` (`CortexApp.swift:3971/3982`) around the tick to drive the menu-bar sync icon.

---

## 6. SCOPE — ordered task list (first shippable slice: push-only, captures)

**Slice 1 (build now):**
1. `models.py:11` — add `capture_id_override`, `captured_at` to `CaptureRequest`; thread through `main.py:645`.
2. `models.py` — add `SyncIngestRequest/Item/Response`; `main.py` (~1962) — add `POST /v1/sync/ingest` looping `save_capture` + `record_sync_receipt`.
3. `storage.py` — add `capture_change_page(user_id, after_seq, limit)` over `captures.rowid`; `main.py`/`standalone_server.py` — add local `GET /v1/sync/captures`.
4. `macos/Sources/CortexPushSync.swift` — new `AppState` extension: `startPushSync`, `runPushSyncTick`, `cloudRequest`, cursor persistence, device registration.
5. `CortexApp.swift:3625` — call `startPushSync()` in bootstrap; `CortexApp.swift:3994` — nudge from `announceLearned`.
6. `CortexCloudAuth.swift:471` — cancel `pushSyncTask` + clear cursor/device/state in `resetToLocalDefaults`.
7. `CortexCloudAuth.swift:633` — status UI in `signedInView`; `@Published pushSyncState/pendingCount` in `AppState`.
8. Backend tests: idempotent re-push of a batch (no dup captures), cursor resume after partial failure, quota enforcement.

**Later (out of first slice):**
- **Tombstones/deletes:** surface `memory/deleted` + `capture` deletes through the local feed and an ingest `delete` op (hosted `DELETE /v1/memories/{id}` exists at `main.py:1759` but is per-memory; needs a capture-level tombstone apply).
- **Pull / cross-device:** hosted→local direction, second device convergence.
- **Conflict resolution:** currently last-writer-wins via `raw_hash` compare in `save_capture` (`storage.py:8958`); a real version/vector-clock is later.
- **Push extracted records verbatim** (bypass `extract_context`) only if bit-identical local/hosted memory becomes a requirement.

---

## 7. RISKS

- **Idempotency:** solved only if the client supplies a stable `client_capture_id` (= local capture id) AND the server honors it via `capture_id_override` → `ON CONFLICT(id) DO UPDATE` (`storage.py:8991`). Without task 1/2 the fallback hash (`storage.py:8912`) dupes on any timestamp drift — pin `captured_at` per item.
- **Partial-batch failure:** cursor advances **only after** the whole batch is acked (§3 step 3). A 500 mid-batch leaves the cursor at the last good seq; re-push is a safe upsert. Keep batches ≤100 to bound replay.
- **Token expiry mid-sync:** `cloudRequest` refreshes once on 401 (`refreshCloudAccessToken` `CortexCloudAuth.swift:380`) and retries; a failed refresh → `handleCloudSessionExpired` (`CortexCloudAuth.swift:443`) wipes the session, the gate stops the loop.
- **Large backlog / first sync:** paginate from seq 0; `announceLearned` nudge is debounced by `pushSyncInFlight`; menu-bar icon shows progress. Server-side `_enforce_memory_quota` (`main.py:629`) can reject — surface as `.error`, cursor holds, resumes when space frees.
- **Local backend not running:** the local read leg goes through `request()` (`CortexApp.swift:7293`), which already restarts the backend on retriable connection errors for idempotent GETs (`CortexApp.swift:7312-7316`). If still down, tick fails cleanly, cursor untouched, retries next cadence.
- **Cursor integrity:** using `captures.rowid` (monotonic int) instead of the event-hash cursor avoids the ISO-timestamp tiebreaker ties and the `cursor_not_found` reset (`storage.py:15716`) that plague `/v1/sync/changes`. Cursor is cleared on sign-out so a new account never inherits a stale watermark.

**Files to touch:** `backend/app/models.py:11`, `backend/app/main.py:645` & ~`:1962`, `backend/app/storage.py` (new `capture_change_page`; reuse `save_capture:8887`, `record_sync_receipt`), `macos/Sources/CortexPushSync.swift` (new), `macos/Sources/CortexApp.swift:3625,3994,2981`, `macos/Sources/CortexCloudAuth.swift:471,522,625`.