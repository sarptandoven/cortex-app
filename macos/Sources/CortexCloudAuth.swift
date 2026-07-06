import AppKit
import Foundation
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

enum CortexCloudAuthError: LocalizedError {
    case invalidHostedURL
    case badResponse
    case httpStatus(Int, String?)
    case pollTimedOut
    case missingRefreshToken

    var errorDescription: String? {
        switch self {
        case .invalidHostedURL:
            return "That Cortex Cloud URL is not valid. Use something like https://api.trydoppl.com."
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
    var isCloudAuthAvailable: Bool {
        !DistributionMode.isAppStore
    }

    /// Message shown when a cloud-auth action is attempted in a build where it is disabled.
    static let cloudAuthUnavailableMessage = "Cortex Cloud is not available in this version."

    /// The one and only gate for every new cloud code path.
    ///
    /// True only when the endpoint host is NOT localhost AND a cxr_ refresh token
    /// is stored in the Keychain. In local mode (default), both conditions fail,
    /// so no new logic is ever reached.
    var isCloudMode: Bool {
        guard AppState.hostIsRemote(endpoint) else { return false }
        return AppState.storedCloudRefreshToken() != nil
    }

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

    func signInToCloudWithBrowser(hostedURL: String) {
        guard isCloudAuthAvailable else {
            cloudAuthMessage = AppState.cloudAuthUnavailableMessage
            return
        }
        Task { await performCloudBrowserSignIn(hostedURL: hostedURL) }
    }

    func signOutOfCloud() {
        Task { await performCloudSignOut() }
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

    private func performCloudBrowserSignIn(hostedURL: String) async {
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
            guard let browserURL = URL(string: started.browser_url) else {
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
                    cloudAuthMessage = "Browser sign-in cancelled."
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

    /// Calls /v1/auth/refresh with the stored cxr_ token, stores the NEW refresh
    /// token, and updates apiKey with the new access token. Returns true on success.
    /// Never call this from local mode.
    @discardableResult
    func refreshCloudAccessToken() async -> Bool {
        guard let refreshToken = AppState.storedCloudRefreshToken() else { return false }
        let base = endpoint.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        do {
            let data = try await cloudPost(base: base, path: "/v1/auth/refresh", body: [
                "refresh_token": refreshToken
            ])
            let tokens = try JSONDecoder().decode(CortexCloudTokenResponse.self, from: data)
            // Store the NEW refresh token first: the old one is now revoked server-side,
            // so we must never hold onto it (reusing it kills the whole token family).
            storeCloudRefreshToken(tokens.refresh_token)
            // The access token lives ONLY in memory. It is never persisted under the
            // local apiKey Keychain slot, so the local machine key stays intact and
            // local mode remains byte-identical.
            apiKey = tokens.access_token
            return true
        } catch {
            return false
        }
    }

    // MARK: Sign out

    private func performCloudSignOut() async {
        cloudAuthBusy = true
        defer { cloudAuthBusy = false }
        // Best-effort server-side revocation with the current access token.
        if AppState.hostIsRemote(endpoint) {
            let base = endpoint.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            _ = try? await cloudPost(base: base, path: "/v1/auth/logout", body: [:], bearer: apiKey)
        }
        resetToLocalDefaults()
        cloudAuthMessage = "Signed out of Cortex Cloud."
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
        endpoint = base
        // Access token is in-memory only (see refreshCloudAccessToken): the local
        // machine key in the Keychain is never overwritten by a cloud session.
        apiKey = tokens.access_token
        let email = (tokens.account?.email ?? fallbackEmail).trimmingCharacters(in: .whitespacesAndNewlines)
        cloudAccountEmail = email
        UserDefaults.standard.set(email, forKey: AppState.cloudAccountEmailDefaultsKey)
        UserDefaults.standard.set(base, forKey: "endpoint")
    }

    private func resetToLocalDefaults() {
        clearCloudRefreshToken()
        cloudAccountEmail = ""
        UserDefaults.standard.removeObject(forKey: AppState.cloudAccountEmailDefaultsKey)
        endpoint = AppState.localEndpointDefault
        UserDefaults.standard.set(AppState.localEndpointDefault, forKey: "endpoint")
        // Restore the local machine-generated key so local mode works exactly as before.
        apiKey = AppState.restoreLocalAPIKey()
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

    private func cloudPost(base: String, path: String, body: [String: Any], bearer: String? = nil) async throws -> Data {
        guard let url = URL(string: base + path) else { throw CortexCloudAuthError.invalidHostedURL }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
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

    private static let defaultHostedURL = "https://api.trydoppl.com"

    private var isSignedIn: Bool {
        AppState.storedCloudRefreshToken() != nil && AppState.hostIsRemote(state.endpoint)
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
                Text("Sign in to a hosted Cortex account instead of pasting a raw token.")
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
            Text("Endpoint: \(state.endpoint)")
                .font(.caption)
                .foregroundColor(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
            Button {
                state.signOutOfCloud()
            } label: {
                Label("Sign out", systemImage: "rectangle.portrait.and.arrow.right")
            }
            .disabled(state.cloudAuthBusy)
        }
    }

    private var signInForm: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField("Hosted URL", text: $hostedURL)
                .textFieldStyle(.roundedBorder)
                .disableAutocorrection(true)
            TextField("Email", text: $email)
                .textFieldStyle(.roundedBorder)
                .textContentType(.username)
                .disableAutocorrection(true)
            SecureField("Password", text: $password)
                .textFieldStyle(.roundedBorder)
                .textContentType(.password)
            HStack {
                Button {
                    state.signInToCloud(hostedURL: hostedURL, email: email, password: password)
                } label: {
                    Label("Sign in", systemImage: "person.crop.circle.badge.checkmark")
                }
                .disabled(credentialsIncomplete)
                Button {
                    state.signUpToCloud(hostedURL: hostedURL, email: email, password: password)
                } label: {
                    Label("Create account", systemImage: "person.crop.circle.badge.plus")
                }
                .disabled(credentialsIncomplete)
                Button {
                    state.signInToCloudWithBrowser(hostedURL: hostedURL)
                } label: {
                    Label("Browser (Google/GitHub)", systemImage: "globe")
                }
                .disabled(state.cloudAuthBusy || hostedURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                if state.cloudAuthBusy {
                    ProgressView().scaleEffect(0.6)
                }
                Spacer()
            }
            Text("Create an account or sign in to sync your memory to Cortex Cloud and reach it across devices and AI tools. Using Cortex locally needs no account.")
                .font(.caption)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Button {
                state.openCloudSignup(hostedURL: hostedURL)
            } label: {
                Text("Prefer the browser? Open the signup page")
                    .font(.caption)
            }
            .buttonStyle(.link)
        }
    }

    /// Sign in / Create account both need a hosted URL, an email, and a password. The password
    /// is intentionally not trimmed (it may contain spaces); the URL/email are.
    private var credentialsIncomplete: Bool {
        state.cloudAuthBusy
            || email.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || password.isEmpty
            || hostedURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}
