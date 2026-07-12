import Foundation

// MARK: - Phase-2 background pull-sync (the signed-in cloud account → local memory)
//
// The missing half of cross-device sync: CortexPushSync.swift pushes this Mac's captures UP to the
// signed-in hosted account; this engine pulls the account's captures DOWN into the LOCAL store, so
// signing in on a fresh Mac materializes your memory. Mirrors the push engine exactly: a monotonic
// hosted-rowid cursor in UserDefaults, a 5-minute tick loop, refresh-on-401 against the hosted API,
// and idempotent apply (each capture keeps its stable id → local upsert), advancing the cursor ONLY
// after the local apply succeeds so a mid-backlog failure resumes at the last applied page.
//
// Trust passthrough: each pulled item carries the hosted store's `review_status`; the local
// /v1/sync/ingest maps "approved" → auto-approve (a decision another device already granted) unless
// a LOCAL per-source review policy demands review — policy wins on first ingest, and the local
// store preserves its own review decision for content it already holds.
//
// Echo/convergence: a pulled capture INSERTs locally, so the push worker re-sends it to hosted —
// but it round-trips with the SAME stable capture id and byte-identical content, so the hosted
// upsert is a no-op UPDATE (same rowid, raw_hash unchanged): the hosted feed doesn't grow, this
// engine never re-pulls its own echo, and both stores converge instead of ping-ponging.
//
// Everything here runs ONLY when signed in with a sync target; it is a pure no-op signed out /
// local-only, so the local-first experience is unchanged.

enum PullSyncState: Equatable {
    case idle
    case syncing
    case synced(Date)
    case error(String)
}

/// Observable pull-sync status for the settings row. Push's equivalents (`pushSyncState`,
/// `pushPendingCount`) are stored properties on AppState (CortexApp.swift); Swift extensions can't
/// add stored instance properties, so pull's live in this shared center instead and the status row
/// binds to it directly.
final class PullSyncCenter: ObservableObject {
    static let shared = PullSyncCenter()
    @Published var state: PullSyncState = .idle
    @Published var pendingCount: Int = 0
    /// The background tick loop (mirror of AppState.pushSyncTask). Only touched on the main actor.
    var task: Task<Void, Never>?
    /// Re-entrancy guard (mirror of AppState.pushSyncInFlight). Only touched on the main actor.
    var inFlight = false
    private init() {}
}

/// One capture from the HOSTED feed GET /v1/sync/captures (content + the trust-passthrough
/// review_status). Older hosted deployments omit review_status → decodes nil → normal review flow.
private struct PullCaptureItem: Codable {
    let seq: Int
    let client_capture_id: String
    let content: String
    let source: String
    let source_url: String?
    let title: String?
    let captured_at: String
    let review_status: String?
    // Zero-access (E2EE) blind-relay fields. For an encrypted capture the hosted feed returns
    // `content` == "[encrypted]" (a non-secret placeholder), `encrypted_payload` == base64(CXEC1),
    // and `enc_meta` == the decrypt hint. This device decrypts LOCALLY with its on-device key and
    // applies the recovered plaintext. Null/absent for plaintext captures (the common path).
    let encrypted_payload: String?
    let enc_meta: EncMeta?
}

/// The non-secret decrypt hint that rides an encrypted pull item. Only `aad_context` (the capture
/// id used as AES-GCM AAD) is load-bearing on decrypt; the rest is advisory. NEVER any key material.
private struct EncMeta: Codable {
    let alg: String?
    let nonce_b64: String?
    let key_id: String?
    let aad_context: String?
}

private struct PullCapturePage: Codable {
    let items: [PullCaptureItem]
    let next_seq: Int
    let has_more: Bool
}

extension AppState {

    // MARK: Client-side pull state (cursor in UserDefaults)

    static let pullCursorDefaultsKey = "cortexPullCursor.v1"

    private var pullCursor: Int {
        get { UserDefaults.standard.integer(forKey: AppState.pullCursorDefaultsKey) }
        set { UserDefaults.standard.set(newValue, forKey: AppState.pullCursorDefaultsKey) }
    }

    private var canPullSync: Bool { isSignedIn && !cloudSyncBaseURL.isEmpty && !requiresSignIn }

    // MARK: Lifecycle

    /// Start (or restart) the background pull-sync loop: an immediate tick, then every 5 minutes.
    /// Gated on a signed-in account with a sync target; a no-op otherwise. Mirrors startPushSync.
    /// Idempotent — cancels any prior task first.
    func startPullSync(initial: Bool = true) {
        let center = PullSyncCenter.shared
        center.task?.cancel()
        guard canPullSync else { return }
        center.task = Task { [weak self] in
            if initial { await self?.runPullSyncTick() }
            while !Task.isCancelled {
                do { try await Task.sleep(nanoseconds: 5 * 60 * 1_000_000_000) } catch { return }
                await self?.runPullSyncTick()
            }
        }
    }

    /// Stop the loop and reset live state. Called from resetToLocalDefaults() on sign-out, alongside
    /// stopPushSync(), before the cloud creds/target disappear.
    func stopPullSync() {
        let center = PullSyncCenter.shared
        center.task?.cancel()
        center.task = nil
        center.inFlight = false
        center.state = .idle
        center.pendingCount = 0
    }

    /// Clear the persisted pull cursor (on sign-out / account switch) so a different account never
    /// inherits a stale watermark. Mirrors clearPushSyncState().
    func clearPullSyncState() {
        UserDefaults.standard.removeObject(forKey: AppState.pullCursorDefaultsKey)
    }

    /// Kick a pull outside the 5-minute cadence (e.g. right after sign-in or window foreground).
    func pullSyncNudge() async {
        guard canPullSync, !PullSyncCenter.shared.inFlight else { return }
        await runPullSyncTick()
    }

    // MARK: The sync tick

    func runPullSyncTick() async {
        let center = PullSyncCenter.shared
        guard !center.inFlight, canPullSync else { return }
        center.inFlight = true
        defer { center.inFlight = false }
        center.state = .syncing
        var cursor = pullCursor
        do {
            while true {
                // 1. Fetch hosted captures newer than the cursor, WITH content (from the HOSTED
                //    account feed, cloud-access-token authed with one refresh-on-401 retry).
                let pageData = try await cloudGet(
                    path: "/v1/sync/captures?after_seq=\(cursor)&limit=100"
                )
                let page = try JSONDecoder().decode(PullCapturePage.self, from: pageData)
                if page.items.isEmpty { break }
                center.pendingCount = page.items.count
                // 2. Apply the batch into the LOCAL store (idempotent upsert by the stable capture
                //    id). review_status rides along so an approval granted on another device is
                //    honored here (bounded server-side; local per-source policy still wins).
                //
                //    Zero-access (E2EE): an encrypted item is decrypted LOCALLY here with this Mac's
                //    on-device key and the recovered PLAINTEXT is applied (the local store stays
                //    plaintext — encryption is only for the wire/hosted-account). An item that fails
                //    to decrypt (wrong key / tampered / malformed) is SKIPPED — never applied as its
                //    ciphertext placeholder — and flips `decryptFailures` so we surface an error and
                //    do NOT advance the cursor past this page, leaving the item to retry once the
                //    correct recovery code is restored.
                var applyItems: [[String: Any]] = []
                var decryptFailures = 0
                for item in page.items {
                    if let payloadB64 = item.encrypted_payload, !payloadB64.isEmpty {
                        do {
                            let fields = try CortexE2EE.decryptToFields(
                                payloadB64: payloadB64,
                                captureID: item.client_capture_id
                            )
                            var dict: [String: Any] = [
                                "client_capture_id": item.client_capture_id,
                                // Recovered plaintext content; fall back to source/captured_at from
                                // the recovered fields, else the clear envelope fields.
                                "content": (fields["content"] as? String) ?? "",
                                "source": (fields["source"] as? String) ?? item.source,
                                "captured_at": (fields["captured_at"] as? String) ?? item.captured_at,
                            ]
                            if let url = fields["source_url"] as? String { dict["source_url"] = url }
                            if let title = fields["title"] as? String { dict["title"] = title }
                            if let review = fields["review_status"] as? String { dict["review_status"] = review }
                            // Never apply an item whose decrypted content is empty AND whose only
                            // marker was the "[encrypted]" placeholder — that would poison the store.
                            guard let content = dict["content"] as? String, !content.isEmpty else {
                                decryptFailures += 1
                                continue
                            }
                            applyItems.append(dict)
                        } catch {
                            // Bad tag / wrong key / malformed / id-mismatch — hard skip, never apply.
                            decryptFailures += 1
                            continue
                        }
                    } else {
                        // Plaintext item — unchanged.
                        var dict: [String: Any] = [
                            "client_capture_id": item.client_capture_id,
                            "content": item.content,
                            "source": item.source,
                            "captured_at": item.captured_at,
                        ]
                        if let url = item.source_url { dict["source_url"] = url }
                        if let title = item.title { dict["title"] = title }
                        if let review = item.review_status { dict["review_status"] = review }
                        applyItems.append(dict)
                    }
                }
                if !applyItems.isEmpty {
                    _ = try await request(path: "/v1/sync/ingest", method: "POST", body: ["items": applyItems])
                }
                // 3. If any item in this page failed to decrypt, STOP here: surface the error and
                //    leave the cursor at the previous page boundary so those items are retried on the
                //    next tick (e.g. after the user restores the correct recovery code). Applied items
                //    are idempotent upserts, so re-applying them next tick is a safe no-op.
                if decryptFailures > 0 {
                    center.state = .error(CortexE2EEError.decryptFailed.errorDescription
                        ?? "Some synced memory could not be decrypted on this Mac.")
                    return
                }
                // 4. Advance + persist the cursor ONLY after the local apply succeeded, so a
                //    mid-backlog failure resumes exactly at the last applied capture (a safe
                //    re-apply upsert).
                cursor = page.next_seq
                pullCursor = cursor
                if !page.has_more { break }
            }
            center.pendingCount = 0
            center.state = .synced(Date())
        } catch {
            // Offline / server error / token gone: leave the cursor unadvanced; the next 5-min
            // tick resumes from where it stopped. Nothing is lost — it stays in the account.
            center.state = .error(CortexCloudAuth.describe(error))
        }
    }

    // MARK: Hosted GET with refresh-on-401
    //
    // The shared `cloudRequest(path:body:)` helper (CortexCloudAuth.swift) is POST-only — the
    // hosted feed is a GET — so this mirrors it privately: same base resolution, same bearer, same
    // refresh-exactly-once-on-401, same session-expiry handling.

    private func cloudGet(path: String) async throws -> Data {
        let base = cloudSyncBaseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !base.isEmpty else { throw CortexCloudAuthError.invalidHostedURL }
        do {
            return try await cloudGetRaw(base: base, path: path, bearer: cloudAccessToken)
        } catch let error as CortexCloudAuthError {
            if case .httpStatus(let code, _) = error, code == 401 {
                if await refreshCloudAccessToken() {
                    return try await cloudGetRaw(base: base, path: path, bearer: cloudAccessToken)
                }
                handleCloudSessionExpired()
            }
            throw error
        }
    }

    private func cloudGetRaw(base: String, path: String, bearer: String) async throws -> Data {
        guard let url = URL(string: base + path) else { throw CortexCloudAuthError.invalidHostedURL }
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        req.timeoutInterval = 30
        if !bearer.isEmpty {
            req.setValue("Bearer \(bearer)", forHTTPHeaderField: "Authorization")
        }
        let (data, response) = try await URLSession.shared.data(for: req)
        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw CortexCloudAuthError.httpStatus(http.statusCode, String(data: data, encoding: .utf8))
        }
        return data
    }
}
