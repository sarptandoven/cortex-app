import AppKit
import AuthenticationServices
import Foundation
import ObjectiveC
import Security
import SwiftUI

// MARK: - Cortex Cloud (hosted account) sign-in
//
// This entire file is additive and only ever runs when the user has explicitly
// signed into a hosted Cortex Cloud account. The shipping local-first app is
// byte-identical when signed OUT / on localhost: `isCloudMode` is the single gate
// and it requires BOTH a non-localhost endpoint AND a stored cxr_ refresh token.

struct CortexCloudAccount: Codable, Equatable {
    let id: String?
    let email: String?
    let name: String?
    let plan: String?
}

struct CortexCloudTokenResponse: Codable {
    let access_token: String
    let refresh_token: String
    let account: CortexCloudAccount?
}

struct CortexCloudAppStartResponse: Codable {
    let flow_id: String
    let browser_url: String
    let poll_secret: String
}

/// The browser-poll endpoint returns either `{"status":"pending"}` or a full token
/// payload. Decode leniently so a pending tick never throws.
struct CortexCloudAppPollResponse: Codable {
    let status: String?
    let access_token: String?
    let refresh_token: String?
    let account: CortexCloudAccount?
}

/// One social sign-in provider the HOSTED backend actually has configured (from GET
/// /v1/auth/providers). We render a real "Continue with X" button only for providers the server
/// returns, so the sign-in surface never shows a dead button for a provider that isn't wired.
struct CloudAuthProvider: Codable, Identifiable, Hashable {
    let provider: String        // "google", "github", "apple"
    let kind: String
    let display_name: String
    var button_order: Int?
    var id: String { provider }
}

struct CloudAuthProvidersResponse: Codable {
    let results: [CloudAuthProvider]
}

enum CortexCloudAuthError: LocalizedError {
    case invalidHostedURL
    case badResponse
    case httpStatus(Int, String?)
    case pollTimedOut
    case missingRefreshToken

    var errorDescription: String? {
        switch self {
        case .invalidHostedURL:
            return "That Cortex Cloud URL is not valid. Use something like https://api.signindoppl.com."
        case .badResponse:
            return "Cortex Cloud returned an unexpected response."
        case .httpStatus(let code, let body):
            switch code {
            case 401, 403:
                return "Incorrect email or password."
            case 429:
                return "Too many sign-in attempts. Wait a minute, then try again."
            case 500...599:
                return "Cortex Cloud is temporarily unavailable (HTTP \(code)). Try again shortly."
            default:
                let detail = (body?.isEmpty == false) ? " (\(body!))" : ""
                return "Cortex Cloud sign-in failed (HTTP \(code))\(detail)."
            }
        case .pollTimedOut:
            return "Browser sign-in did not complete in time. Try again."
        case .missingRefreshToken:
            return "No Cortex Cloud session is stored. Sign in again."
        }
    }
}

/// Reference box so a `Task` value can be held by an `objc` associated object (which requires a
/// class instance). Used only to back `AppState.cloudBrowserSignInTask` from this extension file.
private final class CortexTaskBox {
    let task: Task<Void, Never>
    init(task: Task<Void, Never>) { self.task = task }
}

extension AppState {

    /// Whether the hosted Cortex Cloud sign-in / sign-up surface is available at all.
    ///
    /// In the Mac App Store build (`DistributionMode.isAppStore`) the app is LOCAL-FIRST
    /// ONLY: there is no in-app account creation or cloud sign-in, so this is false and
    /// every cloud-auth entry point below becomes a no-op that reports a clear
    /// "not available in this version" state instead of reaching /v1/auth/*.
    ///
    /// The direct / notarized-DMG build (`DistributionMode.isAppStore == false`) keeps
    /// full cloud auth and behaves exactly as before.
    ///
    /// The rest of the app (e.g. `CortexCloudSection` / `ConnectionsPrivacySheet`) should
    /// read this flag to decide whether to show or hide the cloud sign-in controls.
    /// Cortex accounts are available in EVERY build (Option B: required accounts). Sign-in runs over
    /// Swift's native URLSession/HTTPS to the hosted API, independent of the Python backend's TLS, so
    /// it works in the sandboxed App Store build too. (Whether an account is *required* to use the app
    /// is a separate gate — see `accountRequired`.)
    var isCloudAuthAvailable: Bool {
        true
    }

    /// Whether the app REQUIRES a signed-in account before it can be used. Driven by the Info.plist
    /// `CortexRequireAccount` flag so the enforcement can be turned on once the hosted backend is live
    /// and the OAuth providers are registered (until then the sign-in surface is available but the app
    /// still works locally, so a build can never brick itself before the backend exists).
    var accountRequired: Bool {
        (Bundle.main.object(forInfoDictionaryKey: "CortexRequireAccount") as? String)?.lowercased() == "true"
    }

    /// True when the user holds a stored Cortex account session (a cxr_ refresh token), INDEPENDENT
    /// of which memory endpoint is active. In the local-first + cloud-sync model (Option A) the data
    /// plane stays local even when signed in, so "signed in" is NOT the same as `isCloudMode`: this
    /// flag reflects identity + sync; `isCloudMode` reflects the (now rarely used) remote data plane.
    var isSignedIn: Bool { AppState.storedCloudRefreshToken() != nil }

    /// True when the build requires an account AND the user is not signed in. Single source of truth
    /// for EVERY sign-in gate — the main-window wall (CortexView), the menu-bar "Cortex Spotlight"
    /// quick panel, and onboarding presentation — so no surface can drift out of sync and expose
    /// Ask/Capture/Review before the user has signed in.
    var requiresSignIn: Bool { accountRequired && !isSignedIn && !localPreviewUnlocked }

    /// Reviewer / try-before-you-sign-up escape hatch: drop the required-account wall for THIS
    /// session and make the app fully usable locally. Mirrors the post-sign-in re-bootstrap (the wall
    /// dropping re-enters bootstrap so onboarding + local memory present), then loads the bundled
    /// sample notes so Home/Review/Ask/Constellation are immediately exercisable with no account and
    /// no network. Not persisted, so the real account requirement returns on next launch.
    func unlockLocalPreview() {
        guard !localPreviewUnlocked else { return }
        localPreviewUnlocked = true
        Task {
            await bootstrap()
            await loadSampleNotes()
        }
    }

    /// Message shown when a cloud-auth action is attempted in a build where it is disabled.
    static let cloudAuthUnavailableMessage = "Cortex Cloud is not available in this version."

    /// The default hosted API the sign-in surface targets when the user hasn't entered another.
    /// Read from Info.plist `CortexHostedAPIURL` so the backend domain can be changed with a
    /// one-line plist edit (then a rebuild) — no source change — falling back to the shipped default.
    static var defaultHostedURL: String {
        let configured = (Bundle.main.object(forInfoDictionaryKey: "CortexHostedAPIURL") as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return configured.isEmpty ? "https://api.signindoppl.com" : configured
    }

    /// The one and only gate for every new cloud code path.
    ///
    /// True only when the endpoint host is NOT localhost AND a cxr_ refresh token
    /// is stored in the Keychain. In local mode (default), both conditions fail,
    /// so no new logic is ever reached.
    var isCloudMode: Bool {
        guard AppState.hostIsRemote(endpoint) else { return false }
        return AppState.storedCloudRefreshToken() != nil
    }

    // MARK: Cancellable browser sign-in task

    /// Handle to the in-flight browser sign-in poll so it can be cancelled (see
    /// `cancelCloudBrowserSignIn()`). Stored via an associated object because AppState's sign-in/cloud
    /// surface lives in this extension file and Swift extensions cannot add stored properties; the
    /// Task is boxed in a class so it can ride `objc_setAssociatedObject`. Only ever touched on the
    /// main actor (AppState is @MainActor), so the raw pointer access is safe here.
    var cloudBrowserSignInTask: Task<Void, Never>? {
        get { (objc_getAssociatedObject(self, &AppState.cloudBrowserSignInTaskKey) as? CortexTaskBox)?.task }
        set {
            objc_setAssociatedObject(
                self,
                &AppState.cloudBrowserSignInTaskKey,
                newValue.map { CortexTaskBox(task: $0) },
                .OBJC_ASSOCIATION_RETAIN_NONATOMIC
            )
        }
    }

    private static var cloudBrowserSignInTaskKey: UInt8 = 0

    // MARK: Host classification

    static func hostIsRemote(_ endpoint: String) -> Bool {
        let trimmed = endpoint.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: trimmed), let host = url.host?.lowercased() else {
            return false
        }
        return !["127.0.0.1", "localhost", "::1"].contains(host)
    }

    // MARK: Refresh-token storage (Keychain, reusing the existing credential store)

    static func storedCloudRefreshToken() -> String? {
        guard let token = CortexCredentialStore.loadSecret(forKey: cloudRefreshTokenKey),
              token.hasPrefix("cxr_") else {
            return nil
        }
        return token
    }

    private func storeCloudRefreshToken(_ token: String) {
        CortexCredentialStore.saveSecret(token, forKey: AppState.cloudRefreshTokenKey)
    }

    private func clearCloudRefreshToken() {
        CortexCredentialStore.removeSecret(forKey: AppState.cloudRefreshTokenKey)
    }

    // MARK: Public entry points

    func signInToCloud(hostedURL: String, email: String, password: String) {
        guard isCloudAuthAvailable else {
            cloudAuthMessage = AppState.cloudAuthUnavailableMessage
            return
        }
        Task { await performCloudSignIn(hostedURL: hostedURL, email: email, password: password) }
    }

    /// Create a Cortex Cloud account in-app (no browser round-trip), then sign in. In beta
    /// autoverify the account is active immediately; if the deployment requires email
    /// verification, the follow-up login surfaces a clear "verify your email" message.
    func signUpToCloud(hostedURL: String, email: String, password: String, displayName: String = "") {
        guard isCloudAuthAvailable else {
            cloudAuthMessage = AppState.cloudAuthUnavailableMessage
            return
        }
        Task { await performCloudSignUp(hostedURL: hostedURL, email: email, password: password, displayName: displayName) }
    }

    /// Start a browser-handoff sign-in. When `provider` is set (e.g. "github", "google"), it is
    /// threaded onto the hosted login URL as a hint so the web page can jump straight to that
    /// provider; a nil/blank provider opens the generic account login page (all providers + email).
    ///
    /// The started poll can run for up to 5 minutes and locks the whole sign-in surface, so the Task
    /// is stored (`cloudBrowserSignInTask`) and can be cancelled via `cancelCloudBrowserSignIn()`.
    func signInToCloudWithBrowser(hostedURL: String, provider: String = "") {
        guard isCloudAuthAvailable else {
            cloudAuthMessage = AppState.cloudAuthUnavailableMessage
            return
        }
        cloudBrowserSignInTask?.cancel()
        cloudBrowserSignInTask = Task { [weak self] in
            await self?.performCloudBrowserSignIn(hostedURL: hostedURL, provider: provider)
            self?.cloudBrowserSignInTask = nil
        }
    }

    /// Cancel an in-flight browser/GitHub/Google sign-in so a user who abandons the browser flow is
    /// not stranded behind the (up-to-5-minute) poll with the whole sign-in surface disabled. The poll
    /// loop's `Task.sleep` is cancellation-aware, so cancelling breaks it promptly on its next tick.
    func cancelCloudBrowserSignIn() {
        cloudBrowserSignInTask?.cancel()
        cloudBrowserSignInTask = nil
        cloudAuthBusy = false
        cloudAuthMessage = "Sign-in cancelled."
    }

    /// Fetch the social sign-in providers the hosted backend actually has configured. Best-effort:
    /// on any failure `cloudAuthProviders` is left as-is (the sign-in surface falls back to the
    /// generic browser + email/password paths). Never shows a dead per-provider button.
    func loadCloudAuthProviders(hostedURL: String) {
        guard isCloudAuthAvailable else { return }
        guard let base = AppState.normalizedHostedBase(hostedURL)
            ?? AppState.normalizedHostedBase(AppState.defaultHostedURL),
              let url = URL(string: base + "/v1/auth/providers") else { return }
        Task { @MainActor in
            do {
                var req = URLRequest(url: url)
                req.timeoutInterval = 15
                let (data, response) = try await URLSession.shared.data(for: req)
                guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else { return }
                let decoded = try JSONDecoder().decode(CloudAuthProvidersResponse.self, from: data)
                self.cloudAuthProviders = decoded.results.sorted { ($0.button_order ?? 999) < ($1.button_order ?? 999) }
            } catch {
                // best-effort; leave providers unchanged
            }
        }
    }

    /// Native Sign in with Apple. The ASAuthorization credential carries an id_token that we POST to
    /// /v1/auth/oauth/apple/native; Apple returns the full name ONLY on the first authorization, so we
    /// forward it for the display name. No token ever rides a browser redirect — the OS does the flow.
    func signInWithApple(hostedURL: String, idToken: Data?, fullName: PersonNameComponents?, email: String?) {
        guard isCloudAuthAvailable else {
            cloudAuthMessage = AppState.cloudAuthUnavailableMessage
            return
        }
        guard let idToken, let token = String(data: idToken, encoding: .utf8), !token.isEmpty else {
            cloudAuthMessage = "Apple did not return a sign-in token. Please try again."
            return
        }
        let name = [fullName?.givenName, fullName?.familyName].compactMap { $0 }.joined(separator: " ")
        Task { await performAppleNativeSignIn(hostedURL: hostedURL, idToken: token, displayName: name, email: email ?? "") }
    }

    private func performAppleNativeSignIn(hostedURL: String, idToken: String, displayName: String, email: String) async {
        guard let base = AppState.normalizedHostedBase(hostedURL) ?? AppState.normalizedHostedBase(AppState.defaultHostedURL) else {
            cloudAuthMessage = CortexCloudAuthError.invalidHostedURL.localizedDescription
            return
        }
        cloudAuthBusy = true
        cloudAuthMessage = "Signing in with Apple…"
        defer { cloudAuthBusy = false }
        do {
            var body: [String: Any] = ["id_token": idToken, "client": "macos"]
            if !displayName.isEmpty { body["display_name"] = displayName }
            let data = try await cloudPost(base: base, path: "/v1/auth/oauth/apple/native", body: body)
            let tokens = try JSONDecoder().decode(CortexCloudTokenResponse.self, from: data)
            applySignedInSession(base: base, tokens: tokens, fallbackEmail: tokens.account?.email ?? email)
            cloudAuthMessage = "Signed in with Apple."
        } catch {
            cloudAuthMessage = CortexCloudAuth.describe(error)
        }
    }

    func signOutOfCloud() {
        Task { await performCloudSignOut() }
    }

    /// Permanently delete the signed-in Cortex account and all its cloud data. Required by App Store
    /// Guideline 5.1.1(v): an app that offers account creation must also offer in-app account
    /// deletion. `password` is required by the server ONLY for email/password accounts (blank is
    /// fine for Apple/Google/GitHub accounts).
    func deleteCloudAccount(password: String) {
        guard isCloudAuthAvailable else {
            cloudAuthMessage = AppState.cloudAuthUnavailableMessage
            return
        }
        Task { await performDeleteCloudAccount(password: password) }
    }

    func openCloudSignup(hostedURL: String) {
        guard isCloudAuthAvailable else {
            cloudAuthMessage = AppState.cloudAuthUnavailableMessage
            return
        }
        guard let base = AppState.normalizedHostedBase(hostedURL),
              let url = URL(string: base + "/account/signup") else {
            cloudAuthMessage = CortexCloudAuthError.invalidHostedURL.localizedDescription
            return
        }
        NSWorkspace.shared.open(url)
    }

    // MARK: Password sign-in

    private func performCloudSignIn(hostedURL: String, email: String, password: String) async {
        guard let base = AppState.normalizedHostedBase(hostedURL) else {
            cloudAuthMessage = CortexCloudAuthError.invalidHostedURL.localizedDescription
            return
        }
        cloudAuthBusy = true
        cloudAuthMessage = "Signing in to Cortex Cloud..."
        defer { cloudAuthBusy = false }
        do {
            let data = try await cloudPost(base: base, path: "/v1/auth/login", body: [
                "email": email.trimmingCharacters(in: .whitespacesAndNewlines),
                "password": password,
                "client": "macos-app"
            ])
            let tokens = try JSONDecoder().decode(CortexCloudTokenResponse.self, from: data)
            applySignedInSession(base: base, tokens: tokens, fallbackEmail: email)
            cloudAuthMessage = "Signed in to Cortex Cloud."
        } catch {
            cloudAuthMessage = CortexCloudAuth.describe(error)
        }
    }

    // MARK: Create account (in-app signup, then auto sign-in)

    private func performCloudSignUp(hostedURL: String, email: String, password: String, displayName: String) async {
        guard let base = AppState.normalizedHostedBase(hostedURL) else {
            cloudAuthMessage = CortexCloudAuthError.invalidHostedURL.localizedDescription
            return
        }
        cloudAuthBusy = true
        cloudAuthMessage = "Creating your Cortex account..."
        do {
            _ = try await cloudPost(base: base, path: "/v1/auth/signup", body: [
                "email": email.trimmingCharacters(in: .whitespacesAndNewlines),
                "password": password,
                "display_name": displayName.trimmingCharacters(in: .whitespacesAndNewlines)
            ])
        } catch {
            cloudAuthBusy = false
            cloudAuthMessage = CortexCloudAuth.describe(error)
            return
        }
        // Signup returns an enumeration-resistant generic shape. In beta autoverify the account
        // is active immediately, so sign in right away to obtain tokens. If the deployment
        // requires email verification, the login below surfaces a clear "verify" message.
        await performCloudSignIn(hostedURL: hostedURL, email: email, password: password)
    }

    // MARK: Browser (Google/GitHub) sign-in — same poll pattern as waitForManagedOAuthCompletion

    private func performCloudBrowserSignIn(hostedURL: String, provider: String = "") async {
        guard let base = AppState.normalizedHostedBase(hostedURL) else {
            cloudAuthMessage = CortexCloudAuthError.invalidHostedURL.localizedDescription
            return
        }
        cloudAuthBusy = true
        cloudAuthMessage = "Opening Cortex Cloud sign-in in your browser..."
        defer { cloudAuthBusy = false }
        do {
            let startData = try await cloudPost(base: base, path: "/v1/auth/app/start", body: [:])
            let started = try JSONDecoder().decode(CortexCloudAppStartResponse.self, from: startData)
            // Thread the chosen provider onto the login URL (same host as the server-provided
            // browser_url) so the web page can jump straight to that provider's OAuth.
            var openURLString = started.browser_url
            let providerHint = provider.trimmingCharacters(in: .whitespacesAndNewlines)
            if !providerHint.isEmpty,
               let encoded = providerHint.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) {
                openURLString += (openURLString.contains("?") ? "&" : "?") + "provider=" + encoded
            }
            // The server fully controls browser_url; only ever hand an http(s) URL to the OS opener
            // so a malicious/MITM response can't launch a file://, custom-scheme, or app URL.
            guard let browserURL = URL(string: openURLString),
                  let scheme = browserURL.scheme?.lowercased(), scheme == "https" || scheme == "http" else {
                throw CortexCloudAuthError.badResponse
            }
            NSWorkspace.shared.open(browserURL)
            cloudAuthMessage = "Finish sign-in in your browser..."

            // Browser sign-in (account picker / SSO / 2FA / first-time account creation)
            // routinely takes minutes. Poll for up to 5 minutes with visible progress and a
            // short backoff so a first-time user setting up 2FA does not get timed out.
            let start = Date()
            let deadline = start.addingTimeInterval(300)
            var delay: UInt64 = 2 * 1_000_000_000
            var tick = 0
            while Date() < deadline {
                do {
                    try await Task.sleep(nanoseconds: delay)
                } catch {
                    // Cancelled (e.g. via cancelCloudBrowserSignIn()). That canceller owns the
                    // user-facing message, so just unwind — don't overwrite it here.
                    return
                }
                delay = min(delay + 1_000_000_000, 5 * 1_000_000_000)
                let pollData = try await cloudPost(base: base, path: "/v1/auth/app/poll", body: [
                    "flow_id": started.flow_id,
                    "poll_secret": started.poll_secret
                ])
                let poll = try JSONDecoder().decode(CortexCloudAppPollResponse.self, from: pollData)
                if let access = poll.access_token, let refresh = poll.refresh_token {
                    let tokens = CortexCloudTokenResponse(
                        access_token: access,
                        refresh_token: refresh,
                        account: poll.account
                    )
                    applySignedInSession(base: base, tokens: tokens, fallbackEmail: poll.account?.email ?? "")
                    cloudAuthMessage = "Signed in to Cortex Cloud."
                    return
                }
                tick += 1
                if tick % 5 == 0 {
                    let elapsed = Int(Date().timeIntervalSince(start))
                    cloudAuthMessage = "Waiting for browser sign-in (\(elapsed)s)..."
                }
            }
            throw CortexCloudAuthError.pollTimedOut
        } catch {
            cloudAuthMessage = CortexCloudAuth.describe(error)
        }
    }

    // MARK: Refresh — rotates the token; only the newest is ever kept

    /// Calls /v1/auth/refresh against `cloudSyncBaseURL` with the stored cxr_ token, stores the NEW
    /// refresh token, and updates `cloudAccessToken` (used for cloud sync / logout / delete only —
    /// never the local `apiKey`). Returns true on success. No-op when signed out / no sync base.
    @discardableResult
    func refreshCloudAccessToken() async -> Bool {
        guard let refreshToken = AppState.storedCloudRefreshToken() else { return false }
        let base = cloudSyncBaseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !base.isEmpty else { return false }
        do {
            let data = try await cloudPost(base: base, path: "/v1/auth/refresh", body: [
                "refresh_token": refreshToken
            ])
            let tokens = try JSONDecoder().decode(CortexCloudTokenResponse.self, from: data)
            // Store the NEW refresh token first: the old one is now revoked server-side,
            // so we must never hold onto it (reusing it kills the whole token family).
            storeCloudRefreshToken(tokens.refresh_token)
            // The access token lives ONLY in memory and is used for CLOUD SYNC only — never the
            // local apiKey, so the local engine's machine key stays intact.
            cloudAccessToken = tokens.access_token
            return true
        } catch {
            return false
        }
    }

    // MARK: Sign out

    private func performCloudSignOut() async {
        cloudAuthBusy = true
        defer { cloudAuthBusy = false }
        // Best-effort server-side revocation using the cloud sync creds (the data plane is local).
        // Refresh the in-memory access token first: after an app restart it is empty (in-memory
        // only), so without this the server-side session would never actually be revoked.
        let base = cloudSyncBaseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if !base.isEmpty {
            _ = await refreshCloudAccessToken()
            _ = try? await cloudPost(base: base, path: "/v1/auth/logout", body: [:], bearer: cloudAccessToken)
        }
        resetToLocalDefaults()
        cloudAuthMessage = "Signed out of Cortex Cloud."
    }

    private func performDeleteCloudAccount(password: String) async {
        guard isSignedIn, !cloudSyncBaseURL.isEmpty else {
            cloudAuthMessage = "You are not signed into a Cortex account."
            return
        }
        cloudAuthBusy = true
        cloudAuthMessage = "Deleting your account…"
        defer { cloudAuthBusy = false }
        // The DELETE is session-authed; refresh the in-memory cloud access token first (best-effort —
        // if refresh fails the DELETE surfaces the auth error).
        _ = await refreshCloudAccessToken()
        let base = cloudSyncBaseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        do {
            var body: [String: Any] = [:]
            if !password.isEmpty { body["password"] = password }  // not trimmed: passwords may hold spaces
            _ = try await cloudPost(base: base, path: "/v1/auth/account", body: body, bearer: cloudAccessToken, method: "DELETE")
            resetToLocalDefaults()
            cloudAuthMessage = "Your Cortex Cloud account and its synced copy were permanently deleted. Your memory on this Mac stays local — use \"Delete All Local Data\" to erase it from this device."
        } catch {
            cloudAuthMessage = CortexCloudAuth.describe(error)
        }
    }

    /// Called on refresh failure inside a cloud request: wipe the session and surface
    /// the standard expiry message.
    func handleCloudSessionExpired() {
        resetToLocalDefaults()
        cloudAuthMessage = "Your Cortex Cloud session expired — sign in again."
        status = "Your Cortex Cloud session expired — sign in again."
    }

    // MARK: Session application / teardown

    private func applySignedInSession(base: String, tokens: CortexCloudTokenResponse, fallbackEmail: String) {
        storeCloudRefreshToken(tokens.refresh_token)
        // Local-first + cloud-sync (Option A): DO NOT point the memory data plane at the remote.
        // `endpoint` stays local so all ingestion / Ask / graph / profile run on the mature local
        // engine (first-run works instantly, offline-capable). The account is identity + the sync
        // TARGET; a background sync pushes local memory to `cloudSyncBaseURL`.
        cloudSyncBaseURL = base
        UserDefaults.standard.set(base, forKey: AppState.cloudSyncBaseDefaultsKey)
        // The cxs_ access token is for CLOUD SYNC only and lives in memory — it must NOT overwrite
        // the local machine `apiKey` the local engine authenticates with.
        cloudAccessToken = tokens.access_token
        let email = (tokens.account?.email ?? fallbackEmail).trimmingCharacters(in: .whitespacesAndNewlines)
        cloudAccountEmail = email
        UserDefaults.standard.set(email, forKey: AppState.cloudAccountEmailDefaultsKey)
        // The sign-in wall just dropped (requiresSignIn is false once the refresh token is stored).
        // Re-run bootstrap (same re-entry the vault-folder change uses) so local memory/profile
        // reload and onboarding (suppressed behind the wall) can present for a first-run user.
        Task { await bootstrap() }
    }

    private func resetToLocalDefaults() {
        stopPushSync()          // cancel the background push loop before its creds/target disappear
        clearPushSyncState()    // drop the push cursor + device id so a different account resyncs from 0
        clearCloudRefreshToken()
        cloudAccountEmail = ""
        cloudAccessToken = ""
        cloudSyncBaseURL = ""
        UserDefaults.standard.removeObject(forKey: AppState.cloudAccountEmailDefaultsKey)
        UserDefaults.standard.removeObject(forKey: AppState.cloudSyncBaseDefaultsKey)
        // The data plane was already local (Option A). Keep it local + the local machine key.
        endpoint = AppState.localEndpointDefault
        UserDefaults.standard.set(AppState.localEndpointDefault, forKey: "endpoint")
        apiKey = AppState.restoreLocalAPIKey()
    }

    /// One-time migration to the local-first + cloud-sync model (Option A). A prior build pointed the
    /// memory data plane at the remote (`endpoint` = the hosted URL) once signed in; that path is gone
    /// — the data plane is always local now — so reset any persisted remote `endpoint` back to local,
    /// preserving it as the cloud sync target if one isn't already recorded. Idempotent + a no-op for
    /// users who were never signed in (endpoint already local). Call early in bootstrap().
    func migrateToLocalFirstDataPlane() {
        guard AppState.hostIsRemote(endpoint) else { return }
        if cloudSyncBaseURL.isEmpty {
            cloudSyncBaseURL = endpoint
            UserDefaults.standard.set(endpoint, forKey: AppState.cloudSyncBaseDefaultsKey)
        }
        endpoint = AppState.localEndpointDefault
        UserDefaults.standard.set(AppState.localEndpointDefault, forKey: "endpoint")
    }

    // MARK: Hosted URL normalization

    static func normalizedHostedBase(_ raw: String) -> String? {
        var value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return nil }
        if !value.lowercased().hasPrefix("http://") && !value.lowercased().hasPrefix("https://") {
            value = "https://" + value
        }
        value = value.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: value), let host = url.host, !host.isEmpty else {
            return nil
        }
        // A hosted account must be remote; refuse to point cloud sign-in at localhost.
        guard hostIsRemote(value) else { return nil }
        return value
    }

    // MARK: Low-level unauthenticated/explicit-bearer POST
    //
    // Deliberately separate from `request`/`performRequest` so that:
    //   1. the auth endpoints are reachable before any valid access token exists, and
    //   2. refreshCloudAccessToken() can NEVER recurse into the 401-refresh path.

    private func cloudPost(base: String, path: String, body: [String: Any], bearer: String? = nil, method: String = "POST") async throws -> Data {
        guard let url = URL(string: base + path) else { throw CortexCloudAuthError.invalidHostedURL }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.timeoutInterval = 30
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let bearer, !bearer.isEmpty {
            req.setValue("Bearer \(bearer)", forHTTPHeaderField: "Authorization")
        }
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await URLSession.shared.data(for: req)
        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw CortexCloudAuthError.httpStatus(http.statusCode, String(data: data, encoding: .utf8))
        }
        return data
    }

    /// POST to the hosted CLOUD sync backend (`cloudSyncBaseURL`) with the cloud access token,
    /// refreshing the token once on a 401 and retrying. Used by the push-sync engine
    /// (CortexPushSync.swift) — the memory data plane stays local, so ONLY sync calls take this path.
    func cloudRequest(path: String, body: [String: Any]) async throws -> Data {
        let base = cloudSyncBaseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !base.isEmpty else { throw CortexCloudAuthError.invalidHostedURL }
        do {
            return try await cloudPost(base: base, path: path, body: body, bearer: cloudAccessToken)
        } catch let error as CortexCloudAuthError {
            if case .httpStatus(let code, _) = error, code == 401 {
                if await refreshCloudAccessToken() {
                    return try await cloudPost(base: base, path: path, body: body, bearer: cloudAccessToken)
                }
                handleCloudSessionExpired()
            }
            throw error
        }
    }
}

enum CortexCloudAuth {
    static func describe(_ error: Error) -> String {
        if let cloudError = error as? CortexCloudAuthError {
            return cloudError.localizedDescription
        }
        if error is DecodingError {
            return CortexCloudAuthError.badResponse.localizedDescription
        }
        if let urlError = error as? URLError {
            switch urlError.code {
            case .notConnectedToInternet, .networkConnectionLost, .dataNotAllowed:
                return "You appear to be offline. Check your internet connection and try again."
            case .timedOut:
                return "Cortex Cloud took too long to respond. Check the URL and that the server is reachable."
            case .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed:
                return "Could not reach Cortex Cloud at that address. Check the URL and that the server is running."
            case .secureConnectionFailed, .serverCertificateUntrusted, .serverCertificateHasBadDate,
                 .serverCertificateHasUnknownRoot, .serverCertificateNotYetValid, .clientCertificateRejected:
                return "Could not establish a secure (HTTPS) connection to Cortex Cloud. On a corporate or VPN network, check with your IT admin."
            default:
                break
            }
        }
        return "Cortex Cloud sign-in failed. \(error.localizedDescription)"
    }
}

// MARK: - Settings UI section

struct CortexCloudSection: View {
    @ObservedObject var state: AppState
    @State private var hostedURL: String = ""
    @State private var email: String = ""
    @State private var password: String = ""
    @State private var showDeleteConfirm = false
    @State private var deletePassword = ""

    private static var defaultHostedURL: String { AppState.defaultHostedURL }

    private var isSignedIn: Bool { state.isSignedIn }

    private var canUseNativeAppleSignIn: Bool { Self.hasAppleSignInEntitlement() }

    /// Native Sign in with Apple is only usable when the running app is signed with the Apple
    /// Sign In entitlement. The local beta can be ad-hoc signed, which means the button would open
    /// the OS sheet and then fail without an actionable explanation. Gate the control at runtime so
    /// properly signed builds keep the native flow, while unsigned/ad-hoc betas point users at the
    /// browser or email/password fallback instead.
    private static func hasAppleSignInEntitlement() -> Bool {
        guard let task = SecTaskCreateFromSelf(nil),
              let value = SecTaskCopyValueForEntitlement(
                  task,
                  "com.apple.developer.applesignin" as CFString,
                  nil
              ) else {
            return false
        }
        if let values = value as? [String] {
            return !values.isEmpty
        }
        if let allowed = value as? Bool {
            return allowed
        }
        return true
    }

    /// Mirrors CortexPushSync's (private) `canPushSync`: signed in, a sync target is recorded, and no
    /// sign-in wall is up. When false while signed in, push-sync can never run (e.g. the refresh token
    /// persisted but `cloudSyncBaseURL` is empty), so the idle copy would falsely reassure the user.
    private var canActuallyPushSync: Bool {
        state.isSignedIn && !state.cloudSyncBaseURL.isEmpty && !state.requiresSignIn
    }

    /// Live background-sync status (Phase 2): the memory is on this Mac and syncs to the account.
    @ViewBuilder private var pushSyncStatusView: some View {
        switch state.pushSyncState {
        case .idle:
            if canActuallyPushSync {
                Label("Your memory stays on this Mac and syncs to your account.", systemImage: "icloud")
                    .font(.caption).foregroundColor(.secondary)
            } else {
                Label("Sync is unavailable — sign in again to reconnect your account.", systemImage: "exclamationmark.icloud")
                    .font(.caption).foregroundColor(.orange)
            }
        case .syncing:
            Label(state.pushPendingCount > 0 ? "Syncing… \(state.pushPendingCount) pending" : "Syncing…",
                  systemImage: "arrow.triangle.2.circlepath")
                .font(.caption).foregroundColor(.secondary)
        case .synced(let date):
            Label("Synced ✓ · \(CortexCloudSection.relativeShort(date))", systemImage: "checkmark.icloud")
                .font(.caption).foregroundColor(.secondary)
        case .error(let message):
            Label(message, systemImage: "exclamationmark.icloud")
                .font(.caption).foregroundColor(.orange)
        }
    }

    private static func relativeShort(_ date: Date) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Cortex Cloud")
                .font(.headline)

            // In the Mac App Store build the app is local-first only: there is no in-app
            // cloud sign-in or account creation, so the whole sign-in surface is hidden and
            // replaced with a short explanation. The direct / notarized-DMG build keeps the
            // full sign-in form below unchanged.
            if !state.isCloudAuthAvailable {
                Text("This version of Cortex is local-first only. Your memory stays on this Mac; there is no cloud account to sign in to.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("Sign in, then setup will help you connect memory sources and wire Cortex into Claude Desktop, ChatGPT, or other AI tools.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                if isSignedIn {
                    signedInView
                } else {
                    signInForm
                }

                if !state.cloudAuthMessage.isEmpty {
                    Text(state.cloudAuthMessage)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .onAppear {
            if hostedURL.isEmpty {
                hostedURL = AppState.hostIsRemote(state.endpoint) ? state.endpoint : Self.defaultHostedURL
            }
            if email.isEmpty {
                email = state.cloudAccountEmail
            }
            // Discover which social providers the backend actually offers, so the sign-in surface
            // renders a real labeled button per provider (and never a dead one).
            if state.isCloudAuthAvailable && !isSignedIn {
                state.loadCloudAuthProviders(hostedURL: resolvedHostedURL)
            }
        }
    }

    private var signedInView: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: "checkmark.seal.fill")
                    .foregroundColor(.green)
                Text("Signed in as \(state.cloudAccountEmail.isEmpty ? "your Cortex Cloud account" : state.cloudAccountEmail)")
                    .font(.subheadline)
            }
            pushSyncStatusView
                .fixedSize(horizontal: false, vertical: true)
            Button {
                state.signOutOfCloud()
            } label: {
                Label("Sign out", systemImage: "rectangle.portrait.and.arrow.right")
            }
            .disabled(state.cloudAuthBusy)

            Divider().padding(.vertical, 2)

            // In-app account deletion (App Store Guideline 5.1.1(v)): an account-creation app must let
            // the user permanently delete their account and data from within the app.
            if showDeleteConfirm {
                VStack(alignment: .leading, spacing: 8) {
                    Text("This permanently deletes your Cortex Cloud account and its synced copy from the server. This cannot be undone. Your memory on this Mac stays local — to erase it from this device, use \"Delete All Local Data\" in the data settings.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    SecureField("Password (leave blank if you use Apple / Google / GitHub)", text: $deletePassword)
                        .textFieldStyle(.roundedBorder)
                        .textContentType(.password)
                    HStack {
                        Button(role: .destructive) {
                            state.deleteCloudAccount(password: deletePassword)
                            deletePassword = ""
                            showDeleteConfirm = false
                        } label: {
                            Label("Delete my account permanently", systemImage: "trash")
                        }
                        .disabled(state.cloudAuthBusy)
                        Button("Cancel") {
                            deletePassword = ""
                            showDeleteConfirm = false
                        }
                        if state.cloudAuthBusy { ProgressView().scaleEffect(0.6) }
                    }
                }
            } else {
                Button(role: .destructive) {
                    showDeleteConfirm = true
                } label: {
                    Label("Delete account…", systemImage: "trash")
                }
                .disabled(state.cloudAuthBusy)
            }
        }
    }

    private var signInForm: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Sign in with Apple — native (ASAuthorization). Shown ONLY when this build is actually
            // signed with the Apple Sign In entitlement AND the hosted backend has Apple configured.
            // Otherwise a native button could only open the OS sheet and then fail, so we hide it
            // rather than present a dead, disabled control. (Guideline 4.8: native SIWA when usable.)
            if canUseNativeAppleSignIn && backendOffersApple {
                SignInWithAppleButton(.signIn) { request in
                    request.requestedScopes = [.fullName, .email]
                } onCompletion: { result in
                    switch result {
                    case .success(let auth):
                        if let cred = auth.credential as? ASAuthorizationAppleIDCredential {
                            state.signInWithApple(
                                hostedURL: resolvedHostedURL,
                                idToken: cred.identityToken,
                                fullName: cred.fullName,
                                email: cred.email
                            )
                        }
                    case .failure(let error):
                        // A user-initiated cancel is not an error worth surfacing.
                        if (error as? ASAuthorizationError)?.code != .canceled {
                            state.cloudAuthMessage = "Apple sign-in failed. \(error.localizedDescription)"
                        }
                    }
                }
                .signInWithAppleButtonStyle(.black)
                .frame(maxWidth: .infinity, minHeight: 44, maxHeight: 44)
                .disabled(state.cloudAuthBusy)
            }

            // A real, labeled button per social provider the hosted backend actually offers
            // (e.g. "Sign in with GitHub", "Continue with Google"). Each opens the browser sign-in
            // handoff pre-pointed at that provider; the app polls the session out (no token in a URL).
            // This replaces the old single generic "Continue in browser" that hid GitHub/Google.
            ForEach(browserProviders) { provider in
                Button {
                    state.signInToCloudWithBrowser(hostedURL: resolvedHostedURL, provider: provider.provider)
                } label: {
                    Label(providerButtonLabel(provider), systemImage: providerButtonIcon(provider))
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
                .controlSize(.large)
                .disabled(state.cloudAuthBusy)
            }

            // Fallback: a generic browser sign-in when provider discovery hasn't loaded yet or the
            // backend advertises no social providers (email-only). Kept so there is always a path.
            if browserProviders.isEmpty {
                Button {
                    state.signInToCloudWithBrowser(hostedURL: resolvedHostedURL)
                } label: {
                    Label("Continue in browser", systemImage: "globe")
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
                .controlSize(.large)
                .disabled(state.cloudAuthBusy)
            }

            HStack(spacing: 8) {
                VStack { Divider() }
                Text("or").font(.caption).foregroundColor(.secondary)
                VStack { Divider() }
            }

            TextField("Email", text: $email)
                .textFieldStyle(.roundedBorder)
                .textContentType(.username)
                .disableAutocorrection(true)
            SecureField("Password", text: $password)
                .textFieldStyle(.roundedBorder)
                .textContentType(.password)
            HStack {
                Button {
                    state.signInToCloud(hostedURL: resolvedHostedURL, email: email, password: password)
                } label: {
                    Label("Sign in", systemImage: "person.crop.circle.badge.checkmark")
                }
                .disabled(credentialsIncomplete)
                Button {
                    state.signUpToCloud(hostedURL: resolvedHostedURL, email: email, password: password)
                } label: {
                    Label("Create account", systemImage: "person.crop.circle.badge.plus")
                }
                .disabled(credentialsIncomplete)
                if state.cloudAuthBusy {
                    ProgressView().scaleEffect(0.6)
                    // A started browser/social sign-in polls for up to 5 minutes and disables this
                    // whole surface; give the user an escape hatch so they are never stranded.
                    Button("Cancel") {
                        state.cancelCloudBrowserSignIn()
                    }
                }
                Spacer()
            }

            DisclosureGroup("Advanced") {
                TextField("Hosted URL", text: $hostedURL)
                    .textFieldStyle(.roundedBorder)
                    .disableAutocorrection(true)
            }
            .font(.caption)

            Text("After sign-in, Cortex opens setup: connect a source, review memory, then open Connections for Claude Desktop, ChatGPT exports, or other AI tools.")
                .font(.caption)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// The hosted API the sign-in targets: the user's entry if present, else the default.
    private var resolvedHostedURL: String {
        let trimmed = hostedURL.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? Self.defaultHostedURL : trimmed
    }

    /// Whether the hosted backend advertises Apple as a configured provider (from /v1/auth/providers).
    private var backendOffersApple: Bool {
        state.cloudAuthProviders.contains { $0.provider.lowercased() == "apple" }
    }

    /// The configured social providers reached via the browser handoff (everything except Apple,
    /// which has its own native button when usable).
    private var browserProviders: [CloudAuthProvider] {
        state.cloudAuthProviders.filter { $0.provider.lowercased() != "apple" }
    }

    private func providerButtonLabel(_ provider: CloudAuthProvider) -> String {
        switch provider.provider.lowercased() {
        case "github": return "Sign in with GitHub"
        case "google": return "Continue with Google"
        default: return "Continue with \(provider.display_name)"
        }
    }

    private func providerButtonIcon(_ provider: CloudAuthProvider) -> String {
        switch provider.provider.lowercased() {
        case "github": return "chevron.left.forwardslash.chevron.right"
        case "google": return "globe"
        default: return "arrow.up.forward.app"
        }
    }

    /// Email sign-in / create-account need an email + password (the hosted URL now defaults).
    /// The password is intentionally not trimmed (it may contain spaces).
    private var credentialsIncomplete: Bool {
        state.cloudAuthBusy
            || email.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || password.isEmpty
    }
}
