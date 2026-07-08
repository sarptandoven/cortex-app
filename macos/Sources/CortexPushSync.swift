import Foundation

// MARK: - Phase-2 background push-sync (local memory → the signed-in cloud account)
//
// Option A architecture: the memory DATA PLANE stays LOCAL (127.0.0.1). This engine reads captures
// the local backend holds but hasn't yet pushed (tracked by a monotonic rowid cursor) and pushes
// them to the user's hosted account (`cloudSyncBaseURL`) using the cloud access token, so the account
// is a durable backup + a future cross-device anchor. Push-only, incremental, resumable,
// offline-tolerant, and idempotent (each capture carries its stable local id → server upsert).
//
// Everything here runs ONLY when signed in with a sync target; it is a pure no-op signed out /
// local-only, so the local-first experience is unchanged.

enum PushSyncState: Equatable {
    case idle
    case syncing
    case synced(Date)
    case error(String)
}

/// One capture from the LOCAL feed GET /v1/sync/captures (carries content, unlike the audit feed).
private struct PushCaptureItem: Codable {
    let seq: Int
    let client_capture_id: String
    let content: String
    let source: String
    let source_url: String?
    let title: String?
    let captured_at: String
}

private struct PushCapturePage: Codable {
    let items: [PushCaptureItem]
    let next_seq: Int
    let has_more: Bool
}

private struct PushDeviceResponse: Codable {
    let id: String
}

extension AppState {

    // MARK: Client-side sync state (cursor + device id in UserDefaults)

    private var pushCursor: Int {
        get { UserDefaults.standard.integer(forKey: AppState.pushCursorDefaultsKey) }
        set { UserDefaults.standard.set(newValue, forKey: AppState.pushCursorDefaultsKey) }
    }

    private var pushDeviceID: String {
        get { UserDefaults.standard.string(forKey: AppState.pushDeviceIDDefaultsKey) ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: AppState.pushDeviceIDDefaultsKey) }
    }

    private var canPushSync: Bool { isSignedIn && !cloudSyncBaseURL.isEmpty && !requiresSignIn }

    // MARK: Lifecycle

    /// Start (or restart) the background push-sync loop: an immediate tick, then every 5 minutes.
    /// Gated on a signed-in account with a sync target; a no-op otherwise. Mirrors the cadence of
    /// startConnectedSourceAutoSync. Idempotent — cancels any prior task first.
    func startPushSync(initial: Bool = true) {
        pushSyncTask?.cancel()
        guard canPushSync else { return }
        pushSyncTask = Task { [weak self] in
            if initial { await self?.runPushSyncTick() }
            while !Task.isCancelled {
                do { try await Task.sleep(nanoseconds: 5 * 60 * 1_000_000_000) } catch { return }
                await self?.runPushSyncTick()
            }
        }
    }

    /// Stop the loop and clear sync state. Called from resetToLocalDefaults() on sign-out; the cursor
    /// + device id are cleared there too so a different account never inherits a stale watermark.
    func stopPushSync() {
        pushSyncTask?.cancel()
        pushSyncTask = nil
        pushSyncInFlight = false
        pushSyncState = .idle
        pushPendingCount = 0
    }

    /// Clear the persisted push cursor + device id (on sign-out / account switch).
    func clearPushSyncState() {
        UserDefaults.standard.removeObject(forKey: AppState.pushCursorDefaultsKey)
        UserDefaults.standard.removeObject(forKey: AppState.pushDeviceIDDefaultsKey)
    }

    /// Debounced kick after a learn/capture event so new memory syncs promptly (not only every 5 min).
    func pushSyncNudge() async {
        guard canPushSync, !pushSyncInFlight else { return }
        await runPushSyncTick()
    }

    // MARK: The sync tick

    private func runPushSyncTick() async {
        guard !pushSyncInFlight, canPushSync else { return }
        pushSyncInFlight = true
        defer { pushSyncInFlight = false }
        pushSyncState = .syncing
        await ensurePushDevice()
        var cursor = pushCursor
        do {
            while true {
                // 1. Pull local captures newer than the cursor, WITH content (from the LOCAL backend).
                let pageData = try await request(
                    path: "/v1/sync/captures?after_seq=\(cursor)&limit=100",
                    method: "GET"
                )
                let page = try JSONDecoder().decode(PushCapturePage.self, from: pageData)
                if page.items.isEmpty { break }
                pushPendingCount = page.items.count
                // 2. Push the batch to the HOSTED account (idempotent upsert by client_capture_id).
                let body: [String: Any] = [
                    "device_id": pushDeviceID,
                    "cursor": String(page.next_seq),
                    "items": page.items.map { item -> [String: Any] in
                        var dict: [String: Any] = [
                            "client_capture_id": item.client_capture_id,
                            "content": item.content,
                            "source": item.source,
                            "captured_at": item.captured_at,
                        ]
                        if let url = item.source_url { dict["source_url"] = url }
                        if let title = item.title { dict["title"] = title }
                        return dict
                    },
                ]
                _ = try await cloudRequest(path: "/v1/sync/ingest", body: body)
                // 3. Advance + persist the cursor ONLY after the batch is acked, so a mid-backlog
                //    failure resumes exactly at the last uploaded capture (a safe re-push upsert).
                cursor = page.next_seq
                pushCursor = cursor
                if !page.has_more { break }
            }
            pushPendingCount = 0
            pushSyncState = .synced(Date())
        } catch {
            // Offline / server error / token gone: leave the cursor unadvanced; the next 5-min tick
            // (or the next learn nudge) resumes from where it stopped. No data is lost — it stays local.
            pushSyncState = .error(CortexCloudAuth.describe(error))
        }
    }

    // MARK: Device registration (best-effort — enables the server-side high-watermark receipt)

    private func ensurePushDevice() async {
        guard pushDeviceID.isEmpty else { return }
        let name = "Doppl on \(Host.current().localizedName ?? "Mac")"
        do {
            let data = try await cloudRequest(path: "/v1/sync/devices", body: [
                "device_name": name,
                "platform": "macos",
                "capabilities": ["push"],
            ])
            let resp = try JSONDecoder().decode(PushDeviceResponse.self, from: data)
            if !resp.id.isEmpty { pushDeviceID = resp.id }
        } catch {
            // Non-fatal: the ingest still works without a registered device (its receipt is simply
            // skipped). Fall back to a stable local id so each pushed batch still carries one.
            pushDeviceID = "dev_local_" + String(UUID().uuidString.prefix(12))
        }
    }
}
