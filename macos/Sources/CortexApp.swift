import AppKit
import CryptoKit
import Foundation
import SwiftUI
import Carbon
import Security
import UserNotifications
import Darwin
import UniformTypeIdentifiers

struct CaptureResponse: Codable {
    let capture_id: String
    let summary: String
    let memories: [MemoryItem]
    let tasks: [TaskItem]
    let entities: [EntityItem]
}

struct SourceImportResponse: Codable {
    let import_id: String
    let status: String
    let records_found: Int
    let queued: Int
    let saved: Int
    let failed: Int
    let skipped: Int?
    let sources: [SourceImportCount]
    let records: [SourceImportRecordSummary]
    let errors: [SourceImportError]
}

struct SourceAnalyzeResponse: Codable {
    let records_found: Int
    let sources: [SourceImportCount]
    let sample: [SourceAnalyzeSample]
    let supported_sources: [SupportedSource]
}

struct SourceImportCount: Codable, Hashable {
    let source: String
    let count: Int
}

struct SourceAnalyzeSample: Codable, Hashable {
    let source: String
    let title: String
    let chars: Int
}

struct SupportedSource: Codable, Hashable {
    let id: String
    let name: String
    let formats: [String]
    let status: String
}

struct SourceConnectorCatalogResponse: Codable {
    let results: [SourceConnectorCatalogItem]
}

struct GoogleOAuthStartResponse: Codable {
    let source: String
    let provider: String
    let authorization_url: String
    let authorization_endpoint: String
    let token_endpoint: String
    let redirect_uri: String
    let state: String
    let scopes: [String]
    let access_type: String
}

struct SourceConnectorCatalogItem: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let category: String?
    let auth: String?
    let live_status: String?
    let readiness_status: String?
    let scopes: [String]?
    let permissions_required: [String]?
    let first_100_note: String?
    let notes: String?
    let import_status: String?
    let export_status: String?
    let source_ids: [String]?
    let source_aliases: [String]?
    let import_label: String?
    let supports_import: Bool?
    let formats: [String]?
    let primary_beta: Bool?
    let beta_status: String?
    let primary_beta_path: String?
    let show_in_primary_ui: Bool?
    let baseline_10k: Bool?
    let service_baseline: SourceServiceBaseline?
    let connection_setup: SourceConnectorConnectionSetup?

    var showInPrimaryUI: Bool {
        if let show_in_primary_ui {
            return show_in_primary_ui
        }
        return id == "obsidian" && primary_beta != false
    }

    var isImportReady: Bool {
        if supports_import == true {
            return true
        }
        let status = (import_status ?? "").lowercased()
        return ["native", "generic", "import_ready"].contains(status) || !(formats ?? []).isEmpty
    }

    var isLivePlanned: Bool {
        connectorReadinessStatus == "live-planned" || (live_status ?? "").lowercased() == "planned"
    }

    var connectorReadinessStatus: String {
        let explicit = (readiness_status ?? "").lowercased()
        if ["export-only", "import-ready", "live-planned", "token-ready"].contains(explicit) {
            return explicit
        }
        let status = (live_status ?? "").lowercased()
        if status == "planned" {
            return "live-planned"
        }
        if status == "api_token" {
            return "token-ready"
        }
        if ["import_ready", "local_api", "local_only", "imported"].contains(status) {
            return "import-ready"
        }
        return "export-only"
    }

    var authKind: String {
        (auth ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    var isAccountSignInPlanned: Bool {
        if connectionSetup?.supportsManagedOAuth == true {
            return false
        }
        return authKind == "oauth" || connectorReadinessStatus == "live-planned"
    }

    var hasNativeDirectSync: Bool {
        if let setup = connection_setup {
            return setup.available && setup.hasEndpoint
        }
        if service_baseline?.live_sync == true {
            return true
        }
        switch (service_baseline?.path ?? "").lowercased() {
        case "native-token-sync", "native-local-sync":
            return true
        default:
            break
        }
        let status = (live_status ?? "").lowercased()
        return ["api_token", "local_api", "local_only"].contains(status)
    }

    var connectionSetup: SourceConnectorConnectionSetup? {
        connection_setup
    }
}

struct SourceServiceBaseline: Codable, Hashable {
    let included: Bool?
    let records_supported: Bool?
    let live_sync: Bool?
    let manual_direct_sync: Bool?
    let local_app_autosync: Bool?
    let hosted_managed_sync: Bool?
    let primary_ui: Bool?
    let path: String?
    let source_ids: [String]?
}

struct SourceConnectorConnectionSetup: Codable, Hashable {
    let available: Bool
    let mode: String?
    let method: String?
    let endpoint: String?
    let discovery_endpoint: String?
    let discovery_target_field: String?
    let unavailable_reason: String?
    let managed_oauth_shipped: Bool?
    let oauth_provider: String?
    let oauth_start_endpoint: String?
    let oauth_complete_endpoint: String?
    // Device Flow (RFC 8628): secretless browser sign-in. GitHub uses this — the app shows a code,
    // opens the verification URL, then polls the poll endpoint until a token comes back.
    let device_flow_provider: String?
    let device_flow_start_endpoint: String?
    let device_flow_poll_endpoint: String?
    let credential_storage: String?
    let credential_retained_on_disconnect: Bool?
    let disconnect_behavior: String?
    let default_processing: String?
    let default_cursor_name: String?
    let default_max_records: Int?
    let max_records_limit: Int?
    let common_fields: [SourceConnectorSetupField]
    let credential_fields: [SourceConnectorSetupField]
    let configuration_fields: [SourceConnectorSetupField]
    let require_one_of: [String]
    // Human, step-by-step "how to connect this" instructions + a help link, supplied by the
    // backend so the setup sheet can show exactly what to do (get a token, sign in, export).
    let setup_instructions: [String]?
    let help_url: String?

    var setupSteps: [String] { setup_instructions ?? [] }
    var helpURL: URL? {
        guard let raw = help_url?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty else { return nil }
        return URL(string: raw)
    }

    var hasEndpoint: Bool {
        guard let endpoint else { return false }
        return !endpoint.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var hasDiscoveryEndpoint: Bool {
        guard let discovery_endpoint else { return false }
        return !discovery_endpoint.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var isPost: Bool {
        (method ?? "POST").uppercased() == "POST"
    }

    var supportsManagedOAuth: Bool {
        guard managed_oauth_shipped == true else { return false }
        guard let provider = oauth_provider?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(),
              ["google", "microsoft", "notion"].contains(provider) else {
            return false
        }
        guard let endpoint = oauth_start_endpoint?.trimmingCharacters(in: .whitespacesAndNewlines) else {
            return false
        }
        return !endpoint.isEmpty
    }

    var deviceFlowProvider: String {
        device_flow_provider?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() ?? ""
    }

    var deviceFlowStartEndpoint: String {
        device_flow_start_endpoint?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    var deviceFlowPollEndpoint: String {
        device_flow_poll_endpoint?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    // Secretless "Sign in with GitHub" is available when the backend advertised a device-flow
    // provider + start endpoint (the poll endpoint is required to complete it).
    var supportsDeviceFlow: Bool {
        !deviceFlowProvider.isEmpty && !deviceFlowStartEndpoint.isEmpty && !deviceFlowPollEndpoint.isEmpty
    }
}

/// Backend response for `POST .../device/start` — the RFC 8628 device authorization payload.
struct GitHubDeviceStartResponse: Codable {
    let device_code: String
    let user_code: String
    let verification_uri: String
    let expires_in: Int
    let interval: Int
}

/// Backend response for `POST .../device/poll`. `status` is one of ok / authorization_pending /
/// slow_down / expired_token / access_denied / error.
struct GitHubDevicePollResponse: Codable {
    let status: String
    let access_token: String?
    let scope: String?
    let detail: String?
}

/// Drives the "Sign in with GitHub" device-code sheet. Identifiable so cancelling (which sets the
/// AppState property to nil, changing `id`) cleanly stops the poll loop tied to a given `id`.
struct GitHubDeviceFlowPrompt: Identifiable, Equatable {
    enum Phase: Equatable { case waiting, syncing, done, failed }
    let id = UUID()
    let connectorID: String
    let connectorName: String
    let userCode: String
    let verificationURI: String
    var phase: Phase = .waiting
    var message: String

    var verificationURL: URL? { URL(string: verificationURI) }
}

struct SourceConnectorSetupField: Codable, Hashable, Identifiable {
    let name: String
    let label: String?
    let kind: String?
    let required: Bool?
    let secret: Bool?
    let local_path: Bool?
    let max_length: Int?
    let max_items: Int?
    let minimum: Int?
    let maximum: Int?
    let options: [String]?
    let options_endpoint: String?
    let option_label_key: String?
    let option_value_key: String?
    let defaultValue: JSONValue?

    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name
        case label
        case kind
        case required
        case secret
        case local_path
        case max_length
        case max_items
        case minimum
        case maximum
        case options
        case options_endpoint
        case option_label_key
        case option_value_key
        case defaultValue = "default"
    }

    var displayLabel: String {
        if let label, !label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return label
        }
        return name
            .replacingOccurrences(of: "_", with: " ")
            .split(separator: " ")
            .map { $0.prefix(1).uppercased() + $0.dropFirst() }
            .joined(separator: " ")
    }

    var normalizedKind: String {
        (kind ?? "text").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    var isRequired: Bool {
        required == true
    }

    var isSecret: Bool {
        secret == true || normalizedKind == "secret"
    }

    var defaultString: String {
        guard let defaultValue else { return "" }
        switch defaultValue {
        case .string(let value):
            return value
        case .int(let value):
            return String(value)
        case .double(let value):
            return String(value)
        case .bool(let value):
            return value ? "true" : "false"
        case .object, .array, .null:
            return ""
        }
    }

    var defaultBool: Bool {
        guard let defaultValue else { return false }
        switch defaultValue {
        case .bool(let value):
            return value
        case .string(let value):
            return ["1", "true", "yes", "on"].contains(value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased())
        case .int(let value):
            return value != 0
        case .double(let value):
            return value != 0
        case .object, .array, .null:
            return false
        }
    }

    var hasRemoteOptions: Bool {
        guard let options_endpoint else { return false }
        return !options_endpoint.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}

struct SourceConnectorDiscoveredOption: Hashable, Identifiable {
    let id: String
    let label: String
    let value: String
    let detail: String?
}

struct SourceReadinessResponse: Codable, Hashable {
    let generated_at: String
    let summary: SourceReadinessSummary
    let sources: [SourceReadinessItem]
    let recommendations: [String]
}

struct SourceReadinessSummary: Codable, Hashable {
    let sources_total: Int
    let import_ready: Int
    let planned_live: Int
    let connected: Int
    let synced: Int
    let syncing: Int?
    let processing: Int?
    let sources_with_data: Int
    let needs_review: Int
    let needs_attention: Int
    let empty: Int?
    let active_memories: Int
    let primary_beta_ready: Int?
    let primary_beta_active: Int?
    let planned_connectors: Int?
    let advanced_fallback_only: Int?
    let connector_needed: Int?
}

struct SourceSyncPlan: Codable, Hashable {
    let mode: String
    let credential_ref: String?
    let hosted_credential_ref: String?
    let managed_sync_status: String
    let next_sync_due_at: String?
    let sync_interval_seconds: Int?
    let due_now: Bool?
    let scheduler_supported: Bool?
    let blocked_reason: String?
    let last_attempt_at: String?
    let last_completed_at: String?
    let retry_after: String?

    var modeTitle: String {
        switch normalizedMode {
        case "hosted_managed_sync":
            return hasHostedSyncEvidence ? "Hosted sync" : "Planned sign-in"
        case "local_app_autosync":
            return "Local sync"
        case "planned_account_sync":
            return "Planned sign-in"
        case "manual_direct_sync":
            return "Advanced sync"
        case "direct_connector_needed":
            return "Connector needed"
        default:
            return Self.humanized(mode)
        }
    }

    var displayTitle: String {
        guard let statusTitle else {
            return modeTitle
        }
        return "\(modeTitle) · \(statusTitle)"
    }

    var statusIcon: String {
        switch normalizedManagedSyncStatus {
        case "needs_attention":
            return "exclamationmark.circle.fill"
        case "due":
            return "arrow.triangle.2.circlepath.circle.fill"
        case "backing_off":
            return "pause.circle.fill"
        case "healthy":
            return "checkmark.circle.fill"
        case "waiting_for_first_sync":
            return "clock.arrow.circlepath"
        case "planned":
            return "calendar.badge.clock"
        case "connector_needed":
            return "puzzlepiece.extension"
        default:
            switch normalizedMode {
            case "hosted_managed_sync", "planned_account_sync":
                return "person.crop.circle.badge.clock"
            case "local_app_autosync":
                return "arrow.triangle.2.circlepath"
            case "manual_direct_sync":
                return "slider.horizontal.3"
            default:
                return "link.circle"
            }
        }
    }

    var statusColor: Color {
        switch normalizedManagedSyncStatus {
        case "needs_attention":
            return .orange
        case "due":
            return .accentColor
        case "backing_off":
            return .orange
        case "healthy":
            return .green
        case "waiting_for_first_sync":
            return .accentColor
        default:
            switch normalizedMode {
            case "local_app_autosync", "hosted_managed_sync":
                return .accentColor
            default:
                return .secondary
            }
        }
    }

    private var normalizedMode: String {
        mode.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    private var normalizedManagedSyncStatus: String {
        managed_sync_status.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    private var statusTitle: String? {
        switch normalizedManagedSyncStatus {
        case "healthy":
            return "Synced"
        case "due":
            return "Due"
        case "backing_off":
            return "Paused"
        case "waiting_for_first_sync":
            return "Waiting"
        case "needs_attention":
            return "Needs attention"
        case "available_advanced", "planned", "connector_needed", "not_configured":
            return nil
        case "":
            return nil
        default:
            return Self.humanized(managed_sync_status)
        }
    }

    private var hasHostedSyncEvidence: Bool {
        if normalizedManagedSyncStatus == "healthy" || normalizedManagedSyncStatus == "waiting_for_first_sync" {
            return true
        }
        return hasValue(hosted_credential_ref)
            || hasValue(credential_ref)
            || hasValue(last_attempt_at)
            || hasValue(last_completed_at)
            || hasValue(next_sync_due_at)
    }

    private func hasValue(_ value: String?) -> Bool {
        guard let value else { return false }
        return !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private static func humanized(_ value: String) -> String {
        value
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "_", with: " ")
            .replacingOccurrences(of: "-", with: " ")
            .capitalized
    }
}

struct SourceReadinessItem: Codable, Identifiable, Hashable {
    var id: String { source }
    let source: String
    let name: String
    let category: String
    let status: String
    let next_action: String
    let import_status: String
    let export_status: String?
    let source_ids: [String]?
    let source_aliases: [String]?
    let import_label: String?
    let supports_import: Bool?
    let live_status: String
    let readiness_status: String?
    let permissions_required: [String]?
    let first_100_note: String?
    let primary_beta: Bool?
    let beta_status: String?
    let primary_beta_path: String?
    let show_in_primary_ui: Bool?
    let auth: String?
    let scopes: [String]?
    let formats: [String]
    let sync_plan: SourceSyncPlan?
    let accounts: Int
    let cursors: Int
    let captures: Int
    let pending: Int
    let approved: Int
    let archived: Int
    let active_memories: Int
    let citation_coverage: Double
    let last_seen_at: String?
    let warnings: [String]

    var showInPrimaryUI: Bool {
        if let show_in_primary_ui {
            return show_in_primary_ui
        }
        return source == "obsidian" && primary_beta != false
    }

    var syncPlanModeTitle: String {
        if let sync_plan {
            return sync_plan.modeTitle
        }
        switch connectorReadinessStatus {
        case "live-planned":
            return "Planned sign-in"
        case "token-ready", "import-ready":
            return "Local sync"
        case "export-only":
            return "Advanced sync"
        default:
            return "Connection"
        }
    }

    var syncPlanDisplayTitle: String {
        sync_plan?.displayTitle ?? syncPlanModeTitle
    }

    var syncPlanIcon: String {
        sync_plan?.statusIcon ?? statusIcon
    }

    var syncPlanColor: Color {
        sync_plan?.statusColor ?? statusColor
    }

    var connectorReadinessStatus: String {
        let explicit = (readiness_status ?? "").lowercased()
        if ["export-only", "import-ready", "live-planned", "token-ready"].contains(explicit) {
            return explicit
        }
        if live_status.lowercased() == "planned" {
            return "live-planned"
        }
        if supports_import == true || ["native", "generic", "import_ready"].contains(import_status.lowercased()) || !formats.isEmpty {
            return "import-ready"
        }
        return "export-only"
    }

    var needsAttention: Bool {
        status == "needs_attention"
    }

    var statusTitle: String {
        switch status {
        case "needs_attention": return "Needs attention"
        case "empty": return "No notes"
        case "needs_review": return "Review"
        case "synced": return "Synced"
        case "connected": return "Connected"
        case "imported": return "Synced"
        case "import_ready": return "Ready to connect"
        case "planned": return "Planned"
        case "advanced_fallback": return "Advanced only"
        case "connector_needed": return "Connector needed"
        default: return "Available"
        }
    }

    var statusIcon: String {
        switch status {
        case "needs_attention": return "exclamationmark.triangle.fill"
        case "empty": return "folder.badge.questionmark"
        case "needs_review": return "tray.full.fill"
        case "synced": return "checkmark.seal.fill"
        case "connected": return "link.circle.fill"
        case "imported": return "checkmark.seal.fill"
        case "import_ready": return "link.badge.plus"
        case "planned": return "calendar.badge.clock"
        default: return "circle"
        }
    }

    var statusColor: Color {
        switch status {
        case "needs_attention": return .orange
        case "empty": return CortexDesign.accent
        case "needs_review": return CortexDesign.gold
        case "synced": return CortexDesign.sealMoss
        case "connected": return CortexDesign.sealMoss
        case "imported": return CortexDesign.sealMoss
        case "import_ready": return CortexDesign.gold
        case "planned": return CortexDesign.inkFaint
        default: return CortexDesign.inkSecondary
        }
    }
}

struct SourceAccountListResponse: Codable {
    let results: [SourceAccountItem]
}

struct SourceAccountSyncResponse: Codable, Hashable {
    let source_account_id: String
    let source: String
    let status: String
    let processing: String
    let received: Int
    let queued: Int
    let saved: Int
    let skipped: Int
    let failed: Int
    let archived_missing: Int?
    let archive_missing_decision: SourceArchiveMissingDecision?
    let archive_missing_suppressed: Bool?
}

struct SourceArchiveMissingDecision: Codable, Hashable {
    let requested: Bool?
    let allowed: Bool?
    let reason: String?
    let error_count: Int?
    let records_returned: Int?
    let max_records: Int?
    let pagination_field: String?
}

struct ObsidianConnectorSyncResponse: Codable, Hashable {
    let source_account: SourceAccountItem
    let scan: ObsidianConnectorScanSummary
    let source_account_id: String
    let source: String
    let status: String
    let processing: String
    let received: Int
    let queued: Int
    let saved: Int
    let skipped: Int
    let failed: Int
}

struct ObsidianConnectorScanSummary: Codable, Hashable {
    let vault_name: String
    let records_found: Int
    let records_returned: Int
    let truncated: Bool?
    let high_water_mark: String?
    let cursor_value: String
    let extensions: [String]
}

struct SourceAccountItem: Codable, Identifiable, Hashable {
    let id: String
    let user_id: String
    let source: String
    let account_label: String
    let account_identifier: String?
    let connection_type: String
    let status: String
    let auth_state: String
    let last_sync_at: String?
    let last_error: String?
    let created_at: String
    let updated_at: String
    let disconnected_at: String?

    var needsAttention: Bool {
        last_error != nil || disconnected_at != nil || status.lowercased().contains("error") || auth_state.lowercased().contains("expired") || auth_state.lowercased().contains("revoked")
    }

    var normalizedStatus: String {
        status.lowercased().replacingOccurrences(of: "-", with: "_")
    }

    var normalizedAuthState: String {
        auth_state.lowercased().replacingOccurrences(of: "-", with: "_")
    }

    var needsContent: Bool {
        normalizedStatus == "empty" || normalizedAuthState == "needs_content"
    }

    /// True only once the account is genuinely authenticated — a credential was stored / a real
    /// connect happened. A freshly-created placeholder is status "available" + auth_state
    /// "not_configured"; it must NOT read as connected, or the app claims "connected" for a
    /// source the user never signed into.
    var isConnected: Bool {
        disconnected_at == nil && !["", "not_configured", "available"].contains(normalizedAuthState)
    }
}

enum JSONValue: Codable, Hashable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Int.self) {
            self = .int(value)
        } else if let value = try? container.decode(Double.self) {
            self = .double(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            self = .null
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value):
            try container.encode(value)
        case .int(let value):
            try container.encode(value)
        case .double(let value):
            try container.encode(value)
        case .bool(let value):
            try container.encode(value)
        case .object(let value):
            try container.encode(value)
        case .array(let value):
            try container.encode(value)
        case .null:
            try container.encodeNil()
        }
    }

    var intValue: Int? {
        switch self {
        case .int(let value):
            return value
        case .double(let value):
            return Int(value)
        case .string(let value):
            return Int(value)
        default:
            return nil
        }
    }
}

struct SyncCursorListResponse: Codable {
    let results: [SyncCursorItem]
}

struct SyncCursorItem: Codable, Identifiable, Hashable {
    let id: String
    let user_id: String
    let source_account_id: String?
    let source: String
    let cursor_name: String
    let cursor_value: String?
    let high_water_mark: String?
    let state: [String: JSONValue]?
    let last_started_at: String?
    let last_completed_at: String?
    let last_error: String?
    let created_at: String
    let updated_at: String

    var needsAttention: Bool {
        last_error != nil
    }
}

struct SourceImportRecordSummary: Codable, Hashable {
    let capture_id: String?
    let status: String
    let source: String
    let title: String
}

struct SourceImportError: Codable, Hashable {
    let source: String
    let title: String
    let error: String
}

struct SourceImportHistoryResponse: Codable {
    let results: [SourceImportHistoryItem]
}

struct SourceImportHistoryItem: Codable, Identifiable, Hashable {
    var id: String { import_id }
    let import_id: String
    let status: String
    let source_hint: String
    let processing: String
    let sources: [SourceImportCount]
    let records_found: Int
    let queued: Int
    let saved: Int
    let failed: Int
    let skipped: Int?
    let created_at: String
    let completed_at: String?
    let deleted_at: String?
    let remaining_captures: Int
    let remaining_memories: Int
    let remaining_tasks: Int
    let can_delete: Bool
}

struct SourceImportDeleteResponse: Codable {
    let import_id: String
    let deleted: Bool
    let status: String
    let deleted_captures: Int
    let deleted_memories: Int
    let deleted_tasks: Int
    let deleted_edges: Int
}

struct JobRunResponse: Codable {
    let processed: Int
    let scheduled_source_syncs: ScheduledSourceSyncSummary?
    let pending: Int?
    let failed: Int?
}

struct ScheduledSourceSyncSummary: Codable {
    let scheduled: Int
    let skipped: [ScheduledSourceSyncSkip]?
}

struct ScheduledSourceSyncSkip: Codable {
    let source_account_id: String?
    let source: String?
    let reason: String?
}

/// GET /v1/jobs/health — the queue snapshot that drives a real (changing) sync progress bar.
struct JobHealthResponse: Codable {
    let counts: JobCounts?
    let active: [ActiveJob]?
    struct JobCounts: Codable {
        let queued: Int?
        let running: Int?
        let succeeded: Int?
        let failed: Int?
    }
    struct ActiveJob: Codable {
        let job_type: String?
        let source: String?
        let created_at: String?
    }
}

/// A determinate sync-progress value derived from the job queue: `done` finished of `total`
/// in-flight+finished. `active` while there is still queued/running work. `detail` names what
/// is being worked on right now (the front job's source, else a readable job type).
struct SyncProgress: Equatable {
    let done: Int
    let total: Int
    var detail: String? = nil
    var active: Bool { total > 0 && done < total }
    var fraction: Double { total > 0 ? min(1.0, max(0.0, Double(done) / Double(total))) : 0 }
}

/// POST /v1/imports result (subset we render after importing an export). `has_more`/`next_offset`
/// drive the client-side pagination loop so a huge export imports fully across calls.
struct SourceImportResultLite: Codable {
    let saved: Int
    let queued: Int
    let skipped: Int
    let records_found: Int?
    let has_more: Bool?
    let next_offset: Int?
}

/// GET /v1/imports/detect — AI-chat / app exports auto-found in Downloads/CortexImports.
struct ExportDetectResponse: Codable {
    let candidates: [ExportCandidate]
    let drop_folder: String?
}

struct ExportCandidate: Codable, Identifiable, Hashable {
    var id: String { path }
    let path: String
    let filename: String
    let service: String
    let records_found: Int
}

struct SearchResponse: Codable {
    let query: String
    let results: [MemoryItem]
}

struct AskCitationItem: Codable, Identifiable, Hashable {
    var id: String { "\(index)-\(memory_id)" }
    let index: Int
    let memory_id: String
    let result_type: String?
    let kind: String
    let layer: String
    let status: String?
    let source: String
    let source_url: String?
    let source_account_id: String?
    let external_id: String?
    let source_record_id: String?
    let sector: String?
    let source_type: String?
    let citation_path: String?
    let line_start: Int?
    let line_end: Int?
    let record_scope: String?
    let section_title: String?
    let block_id: String?
    let captured_at: String?
    let occurred_at: String?
    let excerpt: String
    let topics: [String]?

    enum CodingKeys: String, CodingKey {
        case index
        case memory_id = "id"
        case result_type
        case kind
        case layer
        case status
        case source
        case source_url
        case source_account_id
        case external_id
        case source_record_id
        case sector
        case source_type
        case citation_path
        case line_start
        case line_end
        case record_scope
        case section_title
        case block_id
        case captured_at
        case occurred_at
        case excerpt
        case topics
    }
}

struct AskResponse: Codable {
    let query: String
    let status: String?
    let answer: String
    let citations: [AskCitationItem]
    let results: [MemoryItem]
}

struct RecentResponse: Codable {
    let results: [MemoryItem]
}

struct InboxResponse: Codable {
    let results: [CaptureItem]
}

struct TaskResponse: Codable {
    let results: [TaskItem]
}

struct GraphResponse: Codable {
    let nodes: [GraphNode]
    let edges: [GraphEdge]
    let analysis: GraphAnalysis?
}

struct GraphAnalysis: Codable, Hashable {
    let community_count: Int?
    let hub_ids: [String]?
    let bridge_count: Int?
    let community_labels: [String: String?]?
}

struct EntityNeighborhood: Codable, Hashable {
    struct Focal: Codable, Hashable {
        let entity_id: String
        let label: String?
        let kind: String?
        let centrality: Double?
        let community: Int?
        let supporting_memories: Int?
    }
    struct Connection: Codable, Hashable, Identifiable {
        var id: String { entity_id }
        let entity_id: String
        let label: String?
        let kind: String?
        let weight: Double?
        let relation: String?
        let centrality: Double?
        let shared_memory_ids: [String]?
        let example: String?
    }
    let focal: Focal
    let connections: [Connection]
    let community_peers: [String]
}

struct MemoryItem: Codable, Identifiable, Hashable {
    let id: String
    let result_type: String?
    let kind: String
    let layer: String?
    let content: String
    let status: String?
    let source: String
    let source_url: String?
    let confidence: String?
    let importance: Int?
    let topics: [String]?
    let entity_ids: [String]?
    let captured_at: String?
}

struct TaskItem: Codable, Identifiable, Hashable {
    let id: String
    let kind: String
    let content: String
    let status: String
    let importance: Int?
}

struct EntityItem: Codable, Identifiable, Hashable {
    let id: String
    let kind: String
    let name: String
    let context: String?
}

struct CaptureItem: Codable, Identifiable, Hashable {
    let id: String
    let import_id: String?
    let source: String
    let source_url: String?
    let title: String?
    let summary: String?
    let review_status: String
    let approved_at: String?
    let archived_at: String?
    let captured_at: String?
    let memory_count: Int?
    let task_count: Int?
    let preview_memories: [MemoryItem]?
    let preview_tasks: [TaskItem]?
}

struct StatsResponse: Codable {
    let captures: Int
    let pending_captures: Int
    let memories: Int
    let decisions: Int
    let tasks: Int
    let entities: Int
    let edges: Int
    let by_kind: [StatBucket]
    let by_layer: [LayerBucket]?
    let top_topics: [TopicBucket]
    let top_entities: [EntityBucket]
}

/// The single "holy-shit, it knows me" insight surfaced on Home. The backend either
/// abstains (`insight: null`) or returns one thing Cortex learned, in the user's words,
/// with its source. See GET /v1/mirror.
struct MirrorResponse: Codable {
    let insight: MirrorInsight?
}

struct MirrorInsight: Codable, Equatable {
    let headline: String
    let evidence: MirrorEvidence?
    let layer: String?
    let confidence: String?

    /// Stable identity for a given insight so a confirm/dismiss on one headline
    /// doesn't suppress a different one. Headline is the user-facing claim, so it's
    /// the natural key for the per-insight dismissed set.
    var dismissKey: String { headline }
}

struct MirrorEvidence: Codable, Equatable {
    let source: String?
    let count: Int?
    let memory_ids: [String]?
    let example: String?
}

/// "What Cortex knows about you" — the Personal Profile. A condensed, human portrait
/// grouped into a handful of sections (how you work, preferences, dislikes, ...), each
/// with a one-line statement and a few grounding elements. See GET /v1/profile. Every
/// field beyond the section identity is optional-tolerant so a partial backend response
/// never blanks the card; a missing/empty `sections` means "abstain" (render nothing).
struct ProfileResponse: Codable {
    let generatedAt: String?
    let readiness: Int?
    let condensed: Bool?
    let sections: [ProfileSection]
    let limitations: [String]?

    enum CodingKeys: String, CodingKey {
        case generatedAt = "generated_at"
        case readiness
        case condensed
        case sections
        case limitations
    }

    /// Decodes defensively: if the backend omits `sections` entirely (or sends null),
    /// treat it as an empty profile rather than failing the whole decode.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try container.decodeIfPresent(String.self, forKey: .generatedAt)
        readiness = try container.decodeIfPresent(Int.self, forKey: .readiness)
        condensed = try container.decodeIfPresent(Bool.self, forKey: .condensed)
        sections = (try container.decodeIfPresent([ProfileSection].self, forKey: .sections)) ?? []
        limitations = try container.decodeIfPresent([String].self, forKey: .limitations)
    }
}

struct ProfileSection: Codable, Identifiable, Equatable {
    let id: String
    let title: String
    let statement: String?
    let method: String?
    let confidence: String?
    let elements: [ProfileElement]

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case statement
        case method
        case confidence
        case elements
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        title = try container.decode(String.self, forKey: .title)
        statement = try container.decodeIfPresent(String.self, forKey: .statement)
        method = try container.decodeIfPresent(String.self, forKey: .method)
        confidence = try container.decodeIfPresent(String.self, forKey: .confidence)
        elements = (try container.decodeIfPresent([ProfileElement].self, forKey: .elements)) ?? []
    }

    /// True when the backend is confident enough to present this as settled fact,
    /// versus an emerging pattern still taking shape.
    var isConfident: Bool { (confidence ?? "").lowercased() == "high" }
}

struct ProfileElement: Codable, Equatable, Identifiable {
    let text: String?
    let source: String?
    let count: Int?
    let memoryIds: [String]?
    let sourceURL: String?

    // Stable-enough identity for ForEach; elements within a section are shown in order
    // and never mutated, so index-free identity from the content is sufficient.
    var id: String { "\(text ?? "")|\(source ?? "")|\(sourceURL ?? "")" }

    enum CodingKeys: String, CodingKey {
        case text
        case source
        case count
        case memoryIds = "memory_ids"
        case sourceURL = "source_url"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        text = try container.decodeIfPresent(String.self, forKey: .text)
        source = try container.decodeIfPresent(String.self, forKey: .source)
        count = try container.decodeIfPresent(Int.self, forKey: .count)
        memoryIds = try container.decodeIfPresent([String].self, forKey: .memoryIds)
        sourceURL = try container.decodeIfPresent(String.self, forKey: .sourceURL)
    }
}

struct MemoryQualityResponse: Codable {
    let generated_at: String
    let score: Int
    let status: String
    let citation_coverage: Double
    let date_coverage: Double?
    let review_coverage: Double
    let layer_coverage: Double
    let layers_present: [String]
    let totals: [String: Int]
    let source_health: [MemoryQualitySource]
    let warnings: [String]
    let recommendations: [String]
}

struct MemoryQualitySource: Codable, Identifiable, Hashable {
    var id: String { source }
    let source: String
    let captures: Int
    let pending: Int
    let approved: Int
    let archived: Int
    let active_memories: Int
    let cited_memories: Int
    let uncited_memories: Int
    let dated_memories: Int?
    let temporal_memories: Int?
    let dated_temporal_memories: Int?
    let undated_temporal_memories: Int?
    let citation_coverage: Double
    let date_coverage: Double?
    let last_seen: String?
    let status: String
    let warnings: [String]
}

struct StatBucket: Codable, Hashable {
    let kind: String
    let count: Int
}

struct LayerBucket: Codable, Hashable {
    let layer: String
    let count: Int
}

struct TopicBucket: Codable, Hashable {
    let topic: String
    let count: Int
}

struct EntityBucket: Codable, Hashable {
    let id: String
    let name: String
    let kind: String
    let count: Int
}

struct ActivityBucket: Codable, Hashable {
    let day: String
    let captures: Int
}

struct TopicSummary: Codable, Hashable {
    let topic: String
    let count: Int
    let last_seen: String?
}

struct EntitySummary: Codable, Hashable, Identifiable {
    let id: String
    let name: String
    let kind: String
    let context: String?
    let first_seen: String?
    let last_seen: String?
    let memory_count: Int?
}

struct DailyReviewResponse: Codable {
    let generated_at: String
    let momentum_score: Int
    let captured_today: Int
    let approved_today: Int
    let stats: StatsResponse
    let pending: [CaptureItem]
    let recent_memories: [MemoryItem]
    let recent_decisions: [MemoryItem]
    let open_tasks: [TaskItem]
    let top_topics: [TopicSummary]
    let top_entities: [EntitySummary]
    let capture_activity: [ActivityBucket]
    let recommended_actions: [String]
    let context_pack: String
}

struct ProductLoopResponse: Codable {
    let generated_at: String
    let status: String
    let completion: Int
    let primary_action: ProductLoopAction
    let steps: [ProductLoopStep]
    let counts: [String: Int]
    let last_reused_at: String?
    let today: String
}

struct ProductLoopAction: Codable, Hashable {
    let action: String
    let label: String
    let title: String
    let detail: String
}

struct ProductLoopStep: Codable, Identifiable, Hashable {
    var id: String { key }
    let key: String
    let title: String
    let status: String
    let detail: String
}

struct AppSettingsResponse: Codable, Equatable {
    var review_new_captures: Bool
    var allow_pending_in_context: Bool
    var context_pack_limit: Int
    var allow_agent_reads: Bool
    var allow_agent_writes: Bool
    var allow_agent_exports: Bool
    var allow_agent_maintenance: Bool
    var allow_agent_destructive_actions: Bool
    var redact_sensitive_context: Bool
    var source_policies: [String: SourcePolicySetting]?
    var identity_aliases: [String]?

    static let defaults = AppSettingsResponse(
        review_new_captures: true,
        allow_pending_in_context: false,
        context_pack_limit: 12,
        allow_agent_reads: true,
        allow_agent_writes: true,
        allow_agent_exports: false,
        allow_agent_maintenance: false,
        allow_agent_destructive_actions: false,
        redact_sensitive_context: true,
        source_policies: [:],
        identity_aliases: []
    )
}

struct SourcePolicySetting: Codable, Equatable, Hashable {
    var mode: String
    var allow_ai_context: Bool?
    var review_required: Bool?
}

enum SourcePolicyMode: String, CaseIterable, Identifiable, Hashable {
    case standard = "default"
    case review = "review"
    case excluded = "excluded"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .standard: return "Normal"
        case .review: return "Review first"
        case .excluded: return "Keep private"
        }
    }

    var systemImage: String {
        switch self {
        case .standard: return "checkmark.circle"
        case .review: return "tray.full"
        case .excluded: return "eye.slash"
        }
    }
}

enum TrustPreset: String, CaseIterable, Identifiable, Hashable {
    case privateMode
    case readOnly
    case canSave
    case advanced

    var id: String { rawValue }

    var title: String {
        switch self {
        case .privateMode: return "Private"
        case .readOnly: return "Read Only"
        case .canSave: return "Can Save"
        case .advanced: return "Advanced"
        }
    }

    var detail: String {
        switch self {
        case .privateMode:
            return "Connected AI tools cannot read, write, export, maintain, or delete memory."
        case .readOnly:
            return "Connected AI tools can read memory, but cannot save, maintain, or delete memory."
        case .canSave:
            return "Connected AI tools can read memory and save useful memories for review. Export, maintenance, and deletion stay off."
        case .advanced:
            return "Tune individual permissions when a connected AI workflow needs a narrower policy."
        }
    }

    static func matching(_ settings: AppSettingsResponse) -> TrustPreset {
        if !settings.allow_pending_in_context
            && !settings.allow_agent_reads
            && !settings.allow_agent_writes
            && !settings.allow_agent_exports
            && !settings.allow_agent_maintenance
            && !settings.allow_agent_destructive_actions
            && settings.redact_sensitive_context {
            return .privateMode
        }
        if !settings.allow_pending_in_context
            && settings.allow_agent_reads
            && !settings.allow_agent_writes
            && !settings.allow_agent_maintenance
            && !settings.allow_agent_destructive_actions
            && settings.redact_sensitive_context {
            return .readOnly
        }
        if !settings.allow_pending_in_context
            && settings.allow_agent_reads
            && settings.allow_agent_writes
            && !settings.allow_agent_exports
            && !settings.allow_agent_maintenance
            && !settings.allow_agent_destructive_actions
            && settings.redact_sensitive_context {
            return .canSave
        }
        return .advanced
    }

    func apply(to settings: inout AppSettingsResponse) {
        switch self {
        case .privateMode:
            settings.allow_pending_in_context = false
            settings.allow_agent_reads = false
            settings.allow_agent_writes = false
            settings.allow_agent_exports = false
            settings.allow_agent_maintenance = false
            settings.allow_agent_destructive_actions = false
            settings.redact_sensitive_context = true
        case .readOnly:
            settings.allow_pending_in_context = false
            settings.allow_agent_reads = true
            settings.allow_agent_writes = false
            settings.allow_agent_exports = false
            settings.allow_agent_maintenance = false
            settings.allow_agent_destructive_actions = false
            settings.redact_sensitive_context = true
        case .canSave:
            settings.allow_pending_in_context = false
            settings.allow_agent_reads = true
            settings.allow_agent_writes = true
            settings.allow_agent_exports = false
            settings.allow_agent_maintenance = false
            settings.allow_agent_destructive_actions = false
            settings.redact_sensitive_context = true
        case .advanced:
            return
        }
    }
}

struct TrustSummaryResponse: Codable {
    let generated_at: String
    let trust_score: Int
    let mode: String
    let settings: AppSettingsResponse
    let counts: [String: Int]
    let risk_flags: [String]
    let source_counts: [SourceTrustSummary]
    let last_agent_event_at: String?
    let redaction_labels: [String]
}

struct DataLifecycleReportResponse: Codable {
    let generated_at: String
    let status: String
    let storage: LifecycleStorage
    let record_counts: [String: Int]
    let backups: LifecycleBackups
    let export: LifecycleExport
    let deletion: LifecycleDeletion
    let ai_access: LifecycleAIAccess
    let audit: LifecycleAudit
    let recommended_actions: [String]
}

struct LifecycleStorage: Codable {
    let mode: String
    let database_path: String
    let vault_path: String
    let database_bytes: Int
    let wal_bytes: Int
    let vault_status: String
}

struct LifecycleBackups: Codable {
    let count: Int
    let latest_backup: LifecycleLatestBackup?
    let retention: [String: Int]
    let include_in_delete_default: Bool
}

struct LifecycleLatestBackup: Codable {
    let backup_path: String
    let size_bytes: Int
    let created_at: String
    let age_days: Int
}

struct LifecycleExport: Codable {
    let json_endpoint: String
    let markdown_endpoint: String
    let redaction_enabled: Bool
    let contains_raw_capture_text: Bool
    let contains_memory_content: Bool
}

struct LifecycleDeletion: Codable {
    let endpoint: String
    let include_backups_default: Bool
    let covered_sqlite: [String]
    let covered_vault: [String]
    let tombstones_count: Int
    let tombstone_policy: String
    let restore_preserves_tombstones: Bool
}

struct LifecycleAIAccess: Codable {
    let mode: String
    let trust_score: Int
    let allow_agent_reads: Bool
    let allow_agent_writes: Bool
    let allow_agent_exports: Bool
    let allow_agent_maintenance: Bool
    let allow_agent_destructive_actions: Bool
    let redaction_enabled: Bool
    let risk_flags: [String]
}

struct LifecycleAudit: Codable {
    let events: Int
    let last_event_at: String?
    let agent_events_7d: Int
}

struct SourceTrustSummary: Codable, Identifiable {
    var id: String { source }
    let source: String
    let total: Int
    let pending: Int
    let approved: Int
    let archived: Int
    let last_seen: String?
}

struct AuditLogResponse: Codable {
    let results: [AuditEventItem]
}

struct IntegrationTokenListResponse: Codable {
    let results: [IntegrationTokenItem]
}

struct IntegrationTokenRegistrationResponse: Codable {
    let token_id: String
    let user_id: String
    let label: String
    let audience: String
    let scopes: [String]
    let updated_at: String
}

/// Result of pairing a browser extension (or any local client) via POST /v1/pair — a fresh,
/// read-only token plus the loopback endpoints the client uses. The token is shown once so the
/// user can paste it into the extension's options.
struct BrowserExtensionPairing: Codable, Equatable {
    let token: String
    let base_url: String
    let mcp_endpoint: String
    let tools_schema_endpoint: String?
    let tools_call_endpoint: String?
    let context_endpoint: String?
    let surface: String?
    let scopes: [String]
}

struct IntegrationTokenItem: Codable, Identifiable {
    var id: String { token_id }
    let token_id: String
    let user_id: String
    let label: String
    let audience: String
    let scopes: [String]
    let created_at: String
    let updated_at: String
    let last_used_at: String?
    let revoked_at: String?
}

struct AuditEventItem: Codable, Identifiable {
    let id: String
    let object_id: String
    let object_type: String
    let event_type: String
    let metadata_text: String
    let created_at: String
}

struct UpdateManifestResponse: Codable {
    let app: String
    let bundle_id: String
    let channel: String
    let version: String
    let build: String
    let minimum_macos: String
    let released_at: String
    let mandatory: Bool
    let release_notes: [String]
    let artifacts: [UpdateArtifact]
}

struct UpdateArtifact: Codable, Identifiable {
    var id: String { "\(kind)-\(filename)" }
    let kind: String
    let filename: String
    let url: String
    let size_bytes: Int
    let sha256: String
}

struct DiagnosticsResponse: Codable {
    let status: String
    let quick_check: String
    let schema_version: Int
    let db_path: String
    let db_size_bytes: Int
    let wal_size_bytes: Int
    let counts: [String: Int]
    let fts_orphans: Int
    let inactive_fts_rows: Int
    let relation_orphans: Int
    let last_event_at: String?
    let vault: VaultDiagnostics?
}

struct VaultDiagnostics: Codable {
    let path: String
    let index_path: String
    let record_counts: [String: Int]
    let event_count: Int
    let index_size_bytes: Int
}

struct BackupResponse: Codable {
    let backup_path: String
    let size_bytes: Int
    let created_at: String
}

struct ReliabilityReportResponse: Codable {
    let status: String
    let generated_at: String
    let backend_version: String
    let health_contract: Int
    let features: [String]
    let checks: [ReliabilityCheck]
    let recommended_actions: [String]
    let latest_backup: ReliabilityBackup?
}

struct ReliabilityCheck: Codable, Identifiable, Hashable {
    var id: String { name }
    let name: String
    let title: String
    let status: String
    let detail: String
    let action: String?
}

struct ReliabilityBackup: Codable, Hashable {
    let backup_path: String
    let size_bytes: Int
    let created_at: String
    let age_days: Int
}

struct RepairStorageResponse: Codable {
    let repaired_at: String
    let backup_path: String
    let before: DiagnosticsResponse
    let after: DiagnosticsResponse
    let actions: [RepairAction]
}

struct RepairAction: Codable, Identifiable, Hashable {
    var id: String { name }
    let name: String
    let rows: Int
}

extension Notification.Name {
    static let cortexOnboardingCompleted = Notification.Name("CortexOnboardingCompleted")
    /// Posted from SwiftUI (Home "Open full view" button) to ask the AppDelegate to summon the
    /// full-screen Constellation overlay (P2), which lives at the AppKit layer.
    static let cortexPresentConstellation = Notification.Name("CortexPresentConstellation")
}

enum IntegrationCategory: String, CaseIterable, Hashable {
    case oneClick = "One-click tools"
    case developer = "Coding tools"
    case browser = "Browser assistants"
    case local = "Local and team stacks"
}

enum IntegrationRoot: Hashable {
    case home
    case applicationSupport

    func resolve(_ relativePath: String) -> URL {
        let base: URL
        switch self {
        case .home:
            base = URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
        case .applicationSupport:
            base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
                ?? URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true).appendingPathComponent("Library/Application Support", isDirectory: true)
        }
        return relativePath.split(separator: "/").reduce(base) { url, component in
            url.appendingPathComponent(String(component))
        }
    }
}

struct IntegrationConfigTarget: Hashable {
    let label: String
    let root: IntegrationRoot
    let relativePath: String

    var url: URL {
        root.resolve(relativePath)
    }
}

struct AIIntegration: Identifiable, Hashable {
    let id: String
    let name: String
    let category: IntegrationCategory
    let systemImage: String
    let summary: String
    let restartHint: String
    let bundleIdentifiers: [String]
    let configTargets: [IntegrationConfigTarget]
    let requiresExistingConfigTarget: Bool
    let setupHint: String
    let browserURL: String?

    init(
        id: String,
        name: String,
        category: IntegrationCategory,
        systemImage: String,
        summary: String,
        restartHint: String,
        bundleIdentifiers: [String],
        configTargets: [IntegrationConfigTarget],
        requiresExistingConfigTarget: Bool = false,
        setupHint: String,
        browserURL: String?
    ) {
        self.id = id
        self.name = name
        self.category = category
        self.systemImage = systemImage
        self.summary = summary
        self.restartHint = restartHint
        self.bundleIdentifiers = bundleIdentifiers
        self.configTargets = configTargets
        self.requiresExistingConfigTarget = requiresExistingConfigTarget
        self.setupHint = setupHint
        self.browserURL = browserURL
    }

    var supportsInstall: Bool {
        !configTargets.isEmpty
    }
}

struct AIIntegrationState: Hashable {
    var appInstalled: Bool = false
    var configured: Bool = false
    var configExists: Bool = false
    var needsRepair: Bool = false
    var configuredPaths: [String] = []
    var availablePaths: [String] = []
}

enum AIIntegrationCatalog {
    static let all: [AIIntegration] = [
        AIIntegration(
            id: "claude-desktop",
            name: "Claude Desktop",
            category: .oneClick,
            systemImage: "sparkles",
            summary: "Adds Cortex memory tools directly inside Claude Desktop.",
            restartHint: "Quit and reopen Claude Desktop after installing.",
            bundleIdentifiers: ["com.anthropic.claudefordesktop", "com.anthropic.Claude"],
            configTargets: [
                IntegrationConfigTarget(label: "Claude Desktop", root: .applicationSupport, relativePath: "Claude/claude_desktop_config.json")
            ],
            setupHint: "Use Claude Desktop to search reviewed memory and save useful updates for Review.",
            browserURL: "https://claude.ai"
        ),
        AIIntegration(
            id: "cursor",
            name: "Cursor",
            category: .oneClick,
            systemImage: "cursorarrow.rays",
            summary: "Gives Cursor agent sessions access to Cortex project memory and adaptation signals.",
            restartHint: "Restart Cursor, then enable the Cortex connection in Cursor settings if prompted.",
            bundleIdentifiers: ["com.todesktop.230313mzl4w4u92", "com.cursor.Cursor"],
            configTargets: [
                IntegrationConfigTarget(label: "Cursor connection", root: .home, relativePath: ".cursor/mcp.json")
            ],
            setupHint: "Use Cortex before implementation tasks: search memory for project decisions, people, and follow-ups.",
            browserURL: "https://cursor.com"
        ),
        AIIntegration(
            id: "windsurf",
            name: "Windsurf",
            category: .oneClick,
            systemImage: "wind",
            summary: "Connects Windsurf/Cascade to Cortex through a local bridge.",
            restartHint: "Restart Windsurf after connecting Cortex.",
            bundleIdentifiers: ["com.exafunction.windsurf", "com.codeium.windsurf"],
            configTargets: [
                IntegrationConfigTarget(label: "Windsurf connection", root: .home, relativePath: ".codeium/windsurf/mcp_config.json")
            ],
            setupHint: "Use Cortex in Cascade to retrieve saved decisions, project memory, and daily follow-ups.",
            browserURL: "https://windsurf.com"
        ),
        AIIntegration(
            id: "cline",
            name: "Cline",
            category: .developer,
            systemImage: "hammer",
            summary: "Installs Cortex memory tools for Cline agent workflows.",
            restartHint: "Reload VS Code after installing.",
            bundleIdentifiers: ["com.microsoft.VSCode"],
            configTargets: [
                IntegrationConfigTarget(label: "Cline connection settings", root: .applicationSupport, relativePath: "Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json")
            ],
            requiresExistingConfigTarget: true,
            setupHint: "Use Cline with Cortex to search memory before asking the user to repeat project context.",
            browserURL: "https://cline.bot"
        ),
        AIIntegration(
            id: "roo-code",
            name: "Roo Code",
            category: .developer,
            systemImage: "chevron.left.forwardslash.chevron.right",
            summary: "Adds Cortex memory tools for Roo Code coding sessions.",
            restartHint: "Reload VS Code after installing.",
            bundleIdentifiers: ["com.microsoft.VSCode"],
            configTargets: [
                IntegrationConfigTarget(label: "Roo Code connection settings", root: .applicationSupport, relativePath: "Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json")
            ],
            requiresExistingConfigTarget: true,
            setupHint: "Use Roo Code with Cortex to retrieve saved decisions, project memory, and open questions.",
            browserURL: nil
        ),
        AIIntegration(
            id: "vscode-copilot",
            name: "VS Code Copilot",
            category: .developer,
            systemImage: "rectangle.connected.to.line.below",
            summary: "Connect Cortex to VS Code user or workspace AI tools.",
            restartHint: "Add the fallback connection details to VS Code, then reload the window.",
            bundleIdentifiers: ["com.microsoft.VSCode"],
            configTargets: [],
            setupHint: "Use fallback connection details only if VS Code asks for them.",
            browserURL: "https://code.visualstudio.com"
        ),
        AIIntegration(
            id: "claude-code",
            name: "Claude Code",
            category: .developer,
            systemImage: "terminal",
            summary: "Connect Cortex memory tools to Claude Code.",
            restartHint: "Run the connection command from a terminal, then restart the Claude Code session.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Use the connection command to connect Cortex to Claude Code.",
            browserURL: "https://docs.anthropic.com"
        ),
        AIIntegration(
            id: "chatgpt",
            name: "ChatGPT",
            category: .browser,
            systemImage: "message.badge",
            summary: "Browser reference while direct tool connections mature.",
            restartHint: "Open ChatGPT when you want to work alongside Cortex.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Use direct local tool or API access where available; browser chat is not the primary memory path.",
            browserURL: "https://chatgpt.com"
        ),
        AIIntegration(
            id: "claude-web",
            name: "Claude Web",
            category: .browser,
            systemImage: "sparkle.magnifyingglass",
            summary: "Browser reference while direct tool access remains the primary path.",
            restartHint: "Open Claude when you want to work alongside Cortex.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Use Claude Desktop or another local tool client for connected Cortex memory.",
            browserURL: "https://claude.ai"
        ),
        AIIntegration(
            id: "gemini",
            name: "Gemini",
            category: .browser,
            systemImage: "diamond",
            summary: "Browser reference while direct connectors are planned.",
            restartHint: "Open Gemini or AI Studio when you want to work alongside Cortex.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Use direct connectors when available; browser chat is not the primary memory path.",
            browserURL: "https://gemini.google.com"
        ),
        AIIntegration(
            id: "perplexity",
            name: "Perplexity",
            category: .browser,
            systemImage: "magnifyingglass.circle",
            summary: "Browser reference for research workflows.",
            restartHint: "Open Perplexity when you want to work alongside Cortex.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Use direct connectors when available; Cortex memory remains local and review-first.",
            browserURL: "https://www.perplexity.ai"
        ),
        AIIntegration(
            id: "copilot-web",
            name: "Microsoft Copilot",
            category: .browser,
            systemImage: "square.stack.3d.up",
            summary: "Browser reference while direct connectors are planned.",
            restartHint: "Open Copilot when you want to work alongside Cortex.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Use direct connectors when available; browser chat is not the primary memory path.",
            browserURL: "https://copilot.microsoft.com"
        ),
        AIIntegration(
            id: "grok",
            name: "Grok",
            category: .browser,
            systemImage: "xmark.circle",
            summary: "Browser reference while direct connectors are planned.",
            restartHint: "Open Grok when you want to work alongside Cortex.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Use direct connectors when available; browser chat is not the primary memory path.",
            browserURL: "https://grok.com"
        ),
        AIIntegration(
            id: "poe",
            name: "Poe",
            category: .browser,
            systemImage: "bubble.left.and.bubble.right",
            summary: "Browser reference for bot workflows.",
            restartHint: "Open Poe when you want to work alongside Cortex.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Use direct connectors when available; browser bots are not the primary memory path.",
            browserURL: "https://poe.com"
        ),
        AIIntegration(
            id: "notebooklm",
            name: "NotebookLM",
            category: .browser,
            systemImage: "book.pages",
            summary: "Browser reference while direct source connectors are planned.",
            restartHint: "Open NotebookLM when you want to work alongside Cortex.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Use connected Cortex sources as the system of record; NotebookLM is not the primary memory path.",
            browserURL: "https://notebooklm.google.com"
        ),
        AIIntegration(
            id: "lm-studio",
            name: "LM Studio",
            category: .local,
            systemImage: "cpu",
            summary: "Use Cortex local tool/API settings for local model workflows.",
            restartHint: "Configure Cortex where your LM Studio workflow accepts local tools.",
            bundleIdentifiers: ["com.lmstudio.lmstudio"],
            configTargets: [],
            setupHint: "Use Cortex's local API or tool bridge with local model agents that support tools.",
            browserURL: "https://lmstudio.ai"
        ),
        AIIntegration(
            id: "open-webui",
            name: "Open WebUI",
            category: .local,
            systemImage: "server.rack",
            summary: "Connect self-hosted Open WebUI through Cortex API or local tools.",
            restartHint: "Update your tool/server configuration, then restart Open WebUI.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Configure Open WebUI or its pipelines to call Cortex on localhost.",
            browserURL: "https://openwebui.com"
        ),
        AIIntegration(
            id: "librechat",
            name: "LibreChat",
            category: .local,
            systemImage: "globe.desk",
            summary: "Connect team chat deployments through Cortex local tool/API settings.",
            restartHint: "Update your LibreChat tool configuration and restart the service.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Use Cortex as a local memory source for LibreChat where custom tools are enabled.",
            browserURL: "https://www.librechat.ai"
        ),
        AIIntegration(
            id: "anythingllm",
            name: "AnythingLLM",
            category: .local,
            systemImage: "tray.and.arrow.down",
            summary: "Connect AnythingLLM workflows through Cortex local API where available.",
            restartHint: "Configure local API or tool support in the workspace.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Wire the local API into agent workflows instead of treating files as the primary memory path.",
            browserURL: "https://anythingllm.com"
        ),
    ]
}

struct MaintenanceResponse: Codable {
    let indexed_memories: Int
    let rebuilt_at: String
}

struct GraphNode: Codable, Identifiable, Hashable {
    let id: String
    let type: String
    let label: String
    let detail: String?
    let importance: Int?
    // Graph-analysis fields, present only for entity nodes (all optional so older/partial payloads
    // still decode). centrality drives size + centre-pull; community drives color; is_hub marks a
    // "god node".
    let centrality: Double?
    let community: Int?
    let is_hub: Bool?
}

struct GraphEdge: Codable, Identifiable, Hashable {
    let id: String
    let source_id: String
    let target_id: String
    let kind: String
    let weight: Double?
    let is_bridge: Bool?  // cross-community "surprising connection"
}

enum DistributionMode {
    static var isAppStore: Bool {
        (Bundle.main.object(forInfoDictionaryKey: "CortexDistributionMode") as? String) == "app-store"
    }
}

struct CortexHTTPError: LocalizedError {
    let statusCode: Int
    let responseBody: String?

    var errorDescription: String? {
        "HTTP \(statusCode)"
    }
}

enum CortexRecoveryText {
    static func failureStatus(_ action: String, error: Error) -> String {
        "\(action) failed. \(recoveryText(for: error))"
    }

    static func statusLine(_ raw: String) -> String {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return trimmed }

        if let backend = backendStatusLine(trimmed) {
            return backend
        }
        if let range = trimmed.range(of: " failed: ", options: .caseInsensitive) {
            let action = String(trimmed[..<range.lowerBound])
            let detail = String(trimmed[range.upperBound...])
            return "\(action) failed. \(recoveryText(forRawMessage: detail))"
        }
        if looksLikeRawError(trimmed) {
            return recoveryText(forRawMessage: trimmed)
        }
        return trimmed
    }

    static func backendStatusLine(_ raw: String) -> String? {
        let lowered = raw.lowercased()
        if lowered.hasPrefix("backend start failed") || lowered.hasPrefix("memory engine start failed") {
            return "Local memory engine could not start. Click Reconnect, then try again."
        }
        if lowered == "backend did not become ready" || lowered == "local memory engine did not become ready" {
            return "Local memory engine is still starting. Wait a moment, then click Reconnect."
        }
        if (lowered.contains("backend") || lowered.contains("memory engine")) && lowered.contains("unavailable") {
            return "Local memory engine is unavailable. Click Reconnect, then try again."
        }
        return nil
    }

    static func inlineError(_ raw: String, fallback: String) -> String {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return fallback }
        if looksLikeRawError(trimmed) {
            return recoveryText(forRawMessage: trimmed)
        }
        if trimmed.count > 120 {
            return fallback
        }
        return trimmed
    }

    static func needsAttention(_ raw: String) -> Bool {
        let lowered = raw.lowercased()
        return lowered.contains("error")
            || lowered.contains("failed")
            || lowered.contains("offline")
            || lowered.contains("unhealthy")
            || lowered.contains("denied")
            || lowered.contains("unreachable")
            || lowered.contains("unavailable")
            || lowered.contains("could not")
            || lowered.contains("timed out")
            || lowered.contains("timeout")
            || lowered.contains("unexpected response")
            || lowered.contains("authentication needs")
            || lowered.contains("reconnect")
    }

    private static func recoveryText(for error: Error) -> String {
        if let httpError = error as? CortexHTTPError {
            return httpRecoveryText(statusCode: httpError.statusCode)
        }
        if let urlError = error as? URLError {
            return urlRecoveryText(urlError.code)
        }
        if error is DecodingError {
            return "Cortex received an unexpected response. Click Reconnect, then try again."
        }

        let nsError = error as NSError
        if nsError.domain == NSURLErrorDomain {
            return urlRecoveryText(URLError.Code(rawValue: nsError.code))
        }
        if nsError.domain == NSCocoaErrorDomain {
            return cocoaRecoveryText(nsError.code)
        }
        return recoveryText(forRawMessage: error.localizedDescription)
    }

    private static func recoveryText(forRawMessage raw: String) -> String {
        let lowered = raw.lowercased()
        if lowered.contains("http 401") || lowered.contains("http 403") || lowered.contains("unauthorized") || lowered.contains("forbidden") {
            return "Authentication needs a reset. Click Reconnect, then try again."
        }
        if lowered.contains("http 404") || lowered.contains("not found") {
            return "This app and local memory engine may be out of sync. Click Reconnect, then try again."
        }
        if lowered.contains("http 409") || lowered.contains("conflict") || lowered.contains("database is locked") {
            return "Cortex is finishing another change. Wait a moment, then try again."
        }
        if lowered.contains("http 413") || lowered.contains("request entity too large") || lowered.contains("payload too large") {
            return "That source is too large. Sync a narrower source or fewer notes."
        }
        if lowered.contains("http 429") || lowered.contains("too many requests") {
            return "Cortex is busy. Wait a moment, then try again."
        }
        if lowered.contains("http 5") || lowered.contains("internal server error") || lowered.contains("bad gateway") || lowered.contains("service unavailable") {
            return "Local memory engine hit a problem. Click Reconnect, then try again."
        }
        if lowered.contains("connection refused") || lowered.contains("could not connect to the server") || lowered.contains("cannot connect to host") || lowered.contains("failed to connect") || lowered.contains("nsurlerrordomain code=-1004") {
            return "Local memory engine is unreachable. Click Reconnect, then try again."
        }
        if lowered.contains("network connection was lost") || lowered.contains("nsurlerrordomain code=-1005") {
            return "Connection dropped. Click Reconnect, then try again."
        }
        if lowered.contains("timed out") || lowered.contains("timeout") || lowered.contains("nsurlerrordomain code=-1001") {
            return "The request timed out. Wait a moment, then try again."
        }
        if lowered.contains("not connected to the internet") || lowered.contains("offline") || lowered.contains("nsurlerrordomain code=-1009") {
            return "Network is offline. Check the connection, then try again."
        }
        if lowered.contains("unsupported url") || lowered.contains("bad url") || lowered.contains("nsurlerrordomain code=-1000") {
            return "The local memory endpoint is invalid. Check the endpoint, then reconnect."
        }
        if lowered.contains("existing config") || lowered.contains("config is not a json") {
            return "That tool connection could not be updated automatically. Open Advanced settings, then Copy tool config."
        }
        if lowered.contains("data couldn") || lowered.contains("correct format") || lowered.contains("decoding") {
            return "Cortex received an unexpected response. Click Reconnect, then try again."
        }
        if lowered.contains("operation not permitted") || lowered.contains("permission denied") || lowered.contains("not authorized") || lowered.contains("sandbox") {
            return "Cortex needs permission for those notes. Choose the folder again from Advanced settings."
        }
        if lowered.contains("no such file") || lowered.contains("file doesn") || lowered.contains("file not found") {
            return "That file is no longer available. Choose it again or refresh Advanced settings."
        }
        return "Refresh and try again. If it repeats, click Reconnect."
    }

    private static func httpRecoveryText(statusCode: Int) -> String {
        switch statusCode {
        case 401, 403:
            return "Authentication needs a reset. Click Reconnect, then try again."
        case 404:
            return "This app and local memory engine may be out of sync. Click Reconnect, then try again."
        case 409:
            return "Cortex is finishing another change. Wait a moment, then try again."
        case 413:
            return "That source is too large. Sync a narrower source or fewer notes."
        case 429:
            return "Cortex is busy. Wait a moment, then try again."
        case 500...599:
            return "Local memory engine hit a problem. Click Reconnect, then try again."
        default:
            return "Refresh and try again. If it repeats, click Reconnect."
        }
    }

    private static func urlRecoveryText(_ code: URLError.Code) -> String {
        switch code {
        case .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed:
            return "Local memory engine is unreachable. Click Reconnect, then try again."
        case .networkConnectionLost:
            return "Connection dropped. Click Reconnect, then try again."
        case .timedOut:
            return "The request timed out. Wait a moment, then try again."
        case .notConnectedToInternet:
            return "Network is offline. Check the connection, then try again."
        case .badURL, .unsupportedURL:
            return "The local memory endpoint is invalid. Check the endpoint, then reconnect."
        case .userAuthenticationRequired, .userCancelledAuthentication:
            return "Authentication needs a reset. Click Reconnect, then try again."
        default:
            return "Local memory engine is unreachable. Click Reconnect, then try again."
        }
    }

    private static func cocoaRecoveryText(_ code: Int) -> String {
        switch code {
        case NSFileReadNoPermissionError, NSFileWriteNoPermissionError:
            return "Cortex needs permission for those notes. Choose the folder again from Advanced settings."
        case NSFileNoSuchFileError:
            return "That file is no longer available. Choose it again or refresh Advanced settings."
        default:
            return "Refresh and try again. If it repeats, click Reconnect."
        }
    }

    private static func looksLikeRawError(_ raw: String) -> Bool {
        let lowered = raw.lowercased()
        return lowered.contains("http ")
            || lowered.contains("nsurlerrordomain")
            || lowered.contains("error domain=")
            || lowered.contains("localized description")
            || lowered.contains("connection refused")
            || lowered.contains("could not connect")
            || lowered.contains("cannot connect")
            || lowered.contains("timed out")
            || lowered.contains("timeout")
            || lowered.contains("not connected to the internet")
            || lowered.contains("internal server error")
            || lowered.contains("bad gateway")
            || lowered.contains("service unavailable")
            || lowered.contains("data couldn")
            || lowered.contains("correct format")
            || lowered.contains("decoding")
            || lowered.contains("permission denied")
            || lowered.contains("operation not permitted")
            || lowered.contains("no such file")
    }
}

final class BackendSupervisor {
    static let shared = BackendSupervisor()

    private enum BackendHealthState {
        case healthy
        case incompatible
        case unavailable
    }

    private var process: Process?
    private var logHandle: FileHandle?

    static var defaultVaultURL: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return base.appendingPathComponent("Cortex", isDirectory: true).appendingPathComponent("Cortex.vault", isDirectory: true)
    }

    var logURL: URL {
        appSupportURL.appendingPathComponent("cortex-backend.log")
    }

    private var appSupportURL: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return base.appendingPathComponent("Cortex", isDirectory: true)
    }

    func ensureRunning(endpoint: String, apiKey: String, mcpAPIKey: String, vaultPath: String, onProgress: ((String) -> Void)? = nil) async -> String {
        let normalizedEndpoint = endpoint.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard normalizedEndpoint.contains("127.0.0.1") || normalizedEndpoint.contains("localhost") else {
            return "Using remote memory engine"
        }
        // Keep the stable MCP bridge (and its recorded interpreter path) current on every
        // backend start — this is what lets AI-app configs survive app moves and updates.
        installStableMCPBridge()
        let initialHealth = await healthCheck(endpoint: normalizedEndpoint, apiKey: apiKey, expectedVaultPath: vaultPath)
        if initialHealth == .healthy {
            return "Local memory engine connected"
        }
        if let process, process.isRunning {
            for tick in 0..<90 {
                if await healthCheck(endpoint: normalizedEndpoint, apiKey: apiKey, expectedVaultPath: vaultPath) == .healthy {
                    return "Local memory engine started"
                }
                if tick > 0, tick % 10 == 0 {
                    onProgress?("Starting the local memory engine (\(tick / 2)s)...")
                }
                try? await Task.sleep(nanoseconds: 500_000_000)
            }
            return "Local memory engine is still starting"
        }
        do {
            if initialHealth == .incompatible {
                terminateLocalPortListener(endpoint: normalizedEndpoint)
            }
            terminate()
            try startBundledBackend(apiKey: apiKey, mcpAPIKey: mcpAPIKey, vaultPath: vaultPath)
            for tick in 0..<90 {
                if await healthCheck(endpoint: normalizedEndpoint, apiKey: apiKey, expectedVaultPath: vaultPath) == .healthy {
                    return "Local memory engine started"
                }
                // If the process exited during startup (most often because port 8766 is
                // already in use), stop waiting the full timeout and surface an actionable
                // failure now instead of leaving the user on a spinner. "failed" keeps this
                // routed to the recovery card (see backendNeedsRecovery).
                if tick > 1, let process, !process.isRunning {
                    return "Local memory engine failed to start — port 8766 may already be in use. Quit any other Cortex instance (or whatever is using that port), then click Reconnect."
                }
                if tick > 0, tick % 10 == 0 {
                    onProgress?("Starting the local memory engine (\(tick / 2)s)...")
                }
                try await Task.sleep(nanoseconds: 500_000_000)
            }
            return "Local memory engine did not become ready"
        } catch {
            return CortexRecoveryText.failureStatus("Memory engine start", error: error)
        }
    }

    func terminate() {
        if let process, process.isRunning {
            process.terminate()
        }
        try? logHandle?.close()
        process = nil
        logHandle = nil
    }

    private func startBundledBackend(apiKey: String, mcpAPIKey: String, vaultPath: String) throws {
        if let process, process.isRunning {
            return
        }
        guard let resources = Bundle.main.resourceURL else {
            throw NSError(domain: "Cortex", code: 1, userInfo: [NSLocalizedDescriptionKey: "App resources are unavailable"])
        }
        let backendURL = resources.appendingPathComponent("backend", isDirectory: true)
        // The App Store build ships the backend as .pyc only (source stripped for IP protection),
        // so accept either the .py source (direct/DMG build) or the compiled .pyc (app-store build).
        let moduleBase = backendURL.appendingPathComponent("app/standalone_server")
        let hasModule = FileManager.default.fileExists(atPath: moduleBase.appendingPathExtension("py").path)
            || FileManager.default.fileExists(atPath: moduleBase.appendingPathExtension("pyc").path)
        guard hasModule else {
            throw NSError(domain: "Cortex", code: 2, userInfo: [NSLocalizedDescriptionKey: "Bundled local memory engine is missing"])
        }

        try FileManager.default.createDirectory(at: appSupportURL, withIntermediateDirectories: true)
        let vaultURL = URL(fileURLWithPath: vaultPath)
        let dbURL = vaultURL.appendingPathComponent("index.sqlite")
        try FileManager.default.createDirectory(at: vaultURL, withIntermediateDirectories: true)
        let migratedLegacyDatabase = try migrateLegacyDatabaseIfNeeded(to: dbURL)
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        let handle = try FileHandle(forWritingTo: logURL)
        try handle.truncate(atOffset: 0)
        logHandle = handle

        let normalizedAPIKey = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let normalizedMCPAPIKey = mcpAPIKey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalizedAPIKey.isEmpty && normalizedAPIKey != "dev-local-key" else {
            throw NSError(domain: "Cortex", code: 4, userInfo: [NSLocalizedDescriptionKey: "Cortex API token is missing"])
        }
        guard !normalizedMCPAPIKey.isEmpty && normalizedMCPAPIKey != normalizedAPIKey else {
            throw NSError(domain: "Cortex", code: 5, userInfo: [NSLocalizedDescriptionKey: "Cortex tool access token is missing"])
        }

        writeLog("Starting bundled backend from \(backendURL.path)")
        writeLog("Vault path: \(vaultURL.path)")
        writeLog("Index path: \(dbURL.path)")
        if migratedLegacyDatabase {
            writeLog("Migrated legacy database into vault index")
        }

        let launched = Process()
        let pythonURL = pythonExecutableURL()
        // App Store builds are bundled-only. If the sandboxed interpreter isn't present the
        // launch would be silently killed by the sandbox, so fail with an explicit,
        // user-actionable error instead.
        if DistributionMode.isAppStore && !FileManager.default.isExecutableFile(atPath: pythonURL.path) {
            throw NSError(domain: "Cortex", code: 6, userInfo: [NSLocalizedDescriptionKey: "The bundled memory engine runtime is missing from this build. Reinstall Cortex from the App Store."])
        }
        writeLog("Python executable: \(pythonURL.path)")
        launched.executableURL = pythonURL
        // Interpreter isolation: `-S` skips site.py (no site-packages injection) and `-s` skips the
        // per-user site directory. We deliberately do NOT use `-I` or `-E`: both cause Python to
        // ignore ALL PYTHON* environment variables — including PYTHONPATH, which is exactly how we
        // point the bundled interpreter at the backend module + bundled wheels below. Adding `-I`/`-E`
        // would break `-m app.standalone_server` (module not found). `-S -s` gives us the strongest
        // isolation that is still compatible with the required PYTHONPATH.
        if pythonURL.lastPathComponent == "env" {
            launched.arguments = ["python3", "-S", "-s", "-m", "app.standalone_server", "--host", "127.0.0.1", "--port", "8766"]
        } else {
            launched.arguments = ["-S", "-s", "-m", "app.standalone_server", "--host", "127.0.0.1", "--port", "8766"]
        }
        launched.currentDirectoryURL = backendURL
        var environment = ProcessInfo.processInfo.environment
        let runtimeDepsURL = resources.appendingPathComponent("python", isDirectory: true)
        let pythonPaths = [backendURL.path, runtimeDepsURL.path]
            .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        environment["PYTHONPATH"] = pythonPaths.joined(separator: ":")
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["CORTEX_VAULT_PATH"] = vaultURL.path
        environment["CORTEX_DB_PATH"] = dbURL.path
        environment["CORTEX_API_KEY"] = normalizedAPIKey
        environment["CORTEX_MCP_API_KEY"] = normalizedMCPAPIKey
        environment["CORTEX_MCP_API_KEY_SCOPES"] = "read,write,export,maintenance"
        environment["CORTEX_PUBLIC_BASE_URL"] = "http://127.0.0.1:8766"
        // Turn on real on-device semantic embeddings when the local model is bundled (offline, no
        // API key). If it's absent for any reason, leave the provider unset so the backend uses its
        // deterministic hash fallback rather than attempting a network download.
        let model2vecURL = resources.appendingPathComponent("model2vec", isDirectory: true)
        if FileManager.default.fileExists(atPath: model2vecURL.appendingPathComponent("config.json").path) {
            environment["CORTEX_EMBEDDING_PROVIDER"] = "model2vec"
            environment["CORTEX_MODEL2VEC_PATH"] = model2vecURL.path
        }
        // Task-aware retrieval, enabled for the shipped app: query planning (semantic intent +
        // compound-question decomposition, additive to recall) and per-layer temporal decay
        // (capped tie-breaking). Both verified no-regression under model2vec by scripts/rerank_eval.py.
        environment["CORTEX_QUERY_PLAN"] = "1"
        environment["CORTEX_TEMPORAL_DECAY"] = "1"
        // Reranker ON: model2vec query↔candidate cosine + entity overlap + rank-prior blend, then MMR
        // dedup, reordering the fused candidates before diversification. Proven by
        // scripts/rerank_eval.py (ON never regresses OFF under real model2vec; CI-gated) and it
        // no-ops under the hash embedder, so it only activates with the bundled model2vec.
        environment["CORTEX_RERANK"] = "linear+mmr"
        // Entity-overlap boost in the fusion stage: a capped tie-breaker toward a memory that names
        // the query's entities. Unlike the reranker it works under any embedder, and it is proven
        // no-regression on scripts/retrieval_eval.py (status ok with the flag both off and on). The
        // bounded recovery hops (CORTEX_CONTEXT_HOP / _HOP2) stay opt-in — they add latency and only
        // help relational questions, so they are left off by default.
        environment["CORTEX_ENTITY_BOOST"] = "1"
        // Entity Map-of-Content pages: browsable People/Projects/Orgs/Topics notes in the vault,
        // one per canonical entity, cross-linked by [[wikilinks]]. A LOCAL desktop feature only —
        // the backend keeps this OFF by default so the shared hosted/bucket vault never generates
        // them; the single-user desktop app opts in here.
        environment["CORTEX_ENTITY_MOC"] = "1"
        if let googleClientID = Bundle.main.object(forInfoDictionaryKey: "CortexGoogleOAuthClientID") as? String {
            let trimmedGoogleClientID = googleClientID.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmedGoogleClientID.isEmpty {
                environment["CORTEX_GOOGLE_OAUTH_CLIENT_ID"] = trimmedGoogleClientID
            }
        }
        if let notionClientID = Bundle.main.object(forInfoDictionaryKey: "CortexNotionOAuthClientID") as? String {
            let trimmedNotionClientID = notionClientID.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmedNotionClientID.isEmpty {
                environment["CORTEX_NOTION_OAUTH_CLIENT_ID"] = trimmedNotionClientID
            }
        }
        if let notionClientSecret = Bundle.main.object(forInfoDictionaryKey: "CortexNotionOAuthClientSecret") as? String {
            let trimmedNotionClientSecret = notionClientSecret.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmedNotionClientSecret.isEmpty {
                environment["CORTEX_NOTION_OAUTH_CLIENT_SECRET"] = trimmedNotionClientSecret
            }
        }
        if let microsoftClientID = Bundle.main.object(forInfoDictionaryKey: "CortexMicrosoftOAuthClientID") as? String {
            let trimmedMicrosoftClientID = microsoftClientID.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmedMicrosoftClientID.isEmpty {
                environment["CORTEX_MICROSOFT_OAUTH_CLIENT_ID"] = trimmedMicrosoftClientID
                environment["CORTEX_OUTLOOK_OAUTH_CLIENT_ID"] = trimmedMicrosoftClientID
            }
        }
        if let microsoftClientSecret = Bundle.main.object(forInfoDictionaryKey: "CortexMicrosoftOAuthClientSecret") as? String {
            let trimmedMicrosoftClientSecret = microsoftClientSecret.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmedMicrosoftClientSecret.isEmpty {
                environment["CORTEX_MICROSOFT_OAUTH_CLIENT_SECRET"] = trimmedMicrosoftClientSecret
                environment["CORTEX_OUTLOOK_OAUTH_CLIENT_SECRET"] = trimmedMicrosoftClientSecret
            }
        }
        // GitHub uses the OAuth Device Flow (RFC 8628): the public client ID alone is enough — no
        // secret, no broker — so this is safe to embed and ship directly.
        if let githubClientID = Bundle.main.object(forInfoDictionaryKey: "CortexGitHubOAuthClientID") as? String {
            let trimmedGitHubClientID = githubClientID.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmedGitHubClientID.isEmpty {
                environment["CORTEX_GITHUB_OAUTH_CLIENT_ID"] = trimmedGitHubClientID
            }
        }
        if DistributionMode.isAppStore {
            // Sandboxed builds can only reach the bundled interpreter; advertising system
            // paths is misleading and any lookup there would be denied. Point PATH only at
            // the bundle's Python bin directory.
            let bundledBin = Bundle.main.privateFrameworksURL?
                .appendingPathComponent("Python.framework/Versions/3.12/bin")
                .path
            environment["PATH"] = bundledBin ?? ""
        } else {
            environment["PATH"] = "/Library/Frameworks/Python.framework/Versions/3.12/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        }
        launched.environment = environment
        launched.standardOutput = handle
        launched.standardError = handle
        launched.terminationHandler = { [weak self] process in
            self?.writeLog("Backend process exited with status \(process.terminationStatus)")
        }
        do {
            try launched.run()
            writeLog("Backend process launched with \(pythonURL.path)")
        } catch {
            writeLog("Backend process failed to launch: \(error.localizedDescription)")
            throw error
        }
        process = launched
    }

    private func migrateLegacyDatabaseIfNeeded(to dbURL: URL) throws -> Bool {
        let manager = FileManager.default
        let legacyDBURL = appSupportURL.appendingPathComponent("cortex.db")
        guard !manager.fileExists(atPath: dbURL.path), manager.fileExists(atPath: legacyDBURL.path) else {
            return false
        }
        try manager.copyItem(at: legacyDBURL, to: dbURL)
        let sidecars = [("-wal", "-wal"), ("-shm", "-shm")]
        for sidecar in sidecars {
            let source = URL(fileURLWithPath: legacyDBURL.path + sidecar.0)
            let target = URL(fileURLWithPath: dbURL.path + sidecar.1)
            if manager.fileExists(atPath: source.path) && !manager.fileExists(atPath: target.path) {
                try? manager.copyItem(at: source, to: target)
            }
        }
        return true
    }

    /// AI-app MCP configs must survive app moves, updates, and Gatekeeper translocation, so they
    /// can never point inside the .app bundle. This installs the stdio bridge at a stable
    /// Application Support path: a POSIX-sh launcher that re-resolves the interpreter from the
    /// sidecar `python-path` file (rewritten here on every backend start, so a moved app heals
    /// itself the next time it runs), falling back to /usr/bin/python3 and then PATH.
    @discardableResult
    func installStableMCPBridge() -> URL? {
        // App Store (sandbox / Guideline 2.5.2): never write or install an executable launcher.
        // The MAS build connects AI tools via guided MANUAL setup (copy config), so there is no
        // "3rd party MCP tools installation method" and no launched/installed executable code.
        if DistributionMode.isAppStore {
            return nil
        }
        let manager = FileManager.default
        let bridgeDir = appSupportURL.appendingPathComponent("mcp", isDirectory: true)
        let launcherURL = bridgeDir.appendingPathComponent("cortex-mcp-bridge")
        guard let bundledScript = Bundle.main.resourceURL?
                .appendingPathComponent("scripts", isDirectory: true)
                .appendingPathComponent("cortex_mcp_stdio.py"),
              manager.fileExists(atPath: bundledScript.path) else {
            return manager.isExecutableFile(atPath: launcherURL.path) ? launcherURL : nil
        }
        do {
            try manager.createDirectory(at: bridgeDir, withIntermediateDirectories: true)
            let scriptData = try Data(contentsOf: bundledScript)
            try scriptData.write(to: bridgeDir.appendingPathComponent("cortex_mcp_stdio.py"), options: .atomic)
            try Data((pythonExecutableURL().path + "\n").utf8)
                .write(to: bridgeDir.appendingPathComponent("python-path"), options: .atomic)
            let launcher = """
            #!/bin/sh
            # Cortex MCP bridge launcher. AI apps point here (a stable path outside the .app
            # bundle); the interpreter is re-resolved from python-path, which the Cortex app
            # rewrites every time its backend starts.
            DIR="$(cd "$(dirname "$0")" && pwd)"
            PY="${CORTEX_MCP_PYTHON:-}"
            if [ -z "$PY" ] && [ -f "$DIR/python-path" ]; then
              CANDIDATE="$(head -n 1 "$DIR/python-path" 2>/dev/null)"
              if [ -n "$CANDIDATE" ] && [ -x "$CANDIDATE" ]; then PY="$CANDIDATE"; fi
            fi
            if [ -z "$PY" ] && [ -x /usr/bin/python3 ]; then PY=/usr/bin/python3; fi
            if [ -z "$PY" ]; then PY="$(command -v python3 2>/dev/null || true)"; fi
            if [ -z "$PY" ]; then
              echo "cortex-mcp-bridge: no usable python3 found; open the Cortex app once to repair" >&2
              exit 1
            fi
            exec "$PY" -S "$DIR/cortex_mcp_stdio.py" "$@"
            """
            try Data(launcher.utf8).write(to: launcherURL, options: .atomic)
            try manager.setAttributes([.posixPermissions: 0o755], ofItemAtPath: launcherURL.path)
            return launcherURL
        } catch {
            writeLog("MCP bridge install failed: \(error.localizedDescription)")
            return manager.isExecutableFile(atPath: launcherURL.path) ? launcherURL : nil
        }
    }

    private func pythonExecutableURL() -> URL {
        let bundledPython = Bundle.main.privateFrameworksURL?
            .appendingPathComponent("Python.framework/Versions/3.12/bin/python3")
            .path
        // App Store (sandboxed) builds are bundled-only: the sandbox cannot reach any
        // system/Homebrew interpreter, so we never fall through to those paths. If the
        // bundled interpreter is missing we still return its expected path so the launch
        // fails loudly with a clear bundle-integrity error instead of silently invoking a
        // forbidden system binary that the sandbox would kill.
        if DistributionMode.isAppStore {
            guard let bundledPython else {
                return URL(fileURLWithPath: "/nonexistent/Cortex/bundled-python-missing")
            }
            return URL(fileURLWithPath: bundledPython)
        }
        let candidates = [
            bundledPython,
            "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
            "/usr/bin/python3",
            "/opt/homebrew/bin/python3",
            "/opt/homebrew/Caskroom/miniconda/base/bin/python3",
            "/usr/local/bin/python3",
        ]
        for candidate in candidates {
            guard let path = candidate, FileManager.default.isExecutableFile(atPath: path) else { continue }
            return URL(fileURLWithPath: path)
        }
        return URL(fileURLWithPath: "/usr/bin/env")
    }

    private func writeLog(_ line: String) {
        let text = "[CortexLauncher] \(Date()) \(line)\n"
        if let data = text.data(using: .utf8) {
            try? logHandle?.write(contentsOf: data)
        }
    }

    private func healthCheck(endpoint: String, apiKey: String, expectedVaultPath: String) async -> BackendHealthState {
        guard let url = URL(string: endpoint + "/health") else { return .unavailable }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 1.5
        if !apiKey.isEmpty {
            request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        }
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else { return .unavailable }
            guard (200...299).contains(http.statusCode) else { return .unavailable }
            guard let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let runningVaultPath = payload["vault_path"] as? String,
                  let healthContract = payload["health_contract"] as? Int,
                  let features = payload["features"] as? [String] else {
                return .incompatible
            }
            guard healthContract >= 3, features.contains("reliability-hardening"), features.contains("simple-product-loop"), features.contains("operational-readiness") else {
                return .incompatible
            }
            let runningPath = URL(fileURLWithPath: runningVaultPath).standardizedFileURL.path
            let expectedPath = URL(fileURLWithPath: expectedVaultPath).standardizedFileURL.path
            guard runningPath == expectedPath else { return .incompatible }
            return await authenticatedBackendProbe(endpoint: endpoint, apiKey: apiKey) ? .healthy : .incompatible
        } catch {
            return .unavailable
        }
    }

    private func authenticatedBackendProbe(endpoint: String, apiKey: String) async -> Bool {
        guard let url = URL(string: endpoint + "/v1/settings") else { return false }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 1.5
        if !apiKey.isEmpty {
            request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        }
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else { return false }
            return (200...299).contains(http.statusCode)
        } catch {
            return false
        }
    }

    private func terminateLocalPortListener(endpoint: String) {
        if DistributionMode.isAppStore {
            writeLog("Skipping external listener termination in App Store distribution mode")
            return
        }
        guard let url = URL(string: endpoint),
              let host = url.host?.lowercased(),
              ["127.0.0.1", "localhost", "::1"].contains(host),
              let port = url.port,
              port == 8766 else {
            return
        }
        let lsof = Process()
        let output = Pipe()
        lsof.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        lsof.arguments = ["-tiTCP:\(port)", "-sTCP:LISTEN"]
        lsof.standardOutput = output
        lsof.standardError = Pipe()
        do {
            try lsof.run()
            lsof.waitUntilExit()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            let pidText = String(data: data, encoding: .utf8) ?? ""
            for line in pidText.split(whereSeparator: \.isNewline) {
                guard let pid = Int32(line.trimmingCharacters(in: .whitespacesAndNewlines)), pid != getpid() else {
                    continue
                }
                writeLog("Stopping incompatible local backend listener on port \(port), pid \(pid)")
                kill(pid, SIGTERM)
            }
            Thread.sleep(forTimeInterval: 0.35)
        } catch {
            writeLog("Could not inspect local backend listener: \(error.localizedDescription)")
        }
    }
    }

enum CortexCredentialStore {
    private static let keychainService = Bundle.main.bundleIdentifier ?? "com.cortex.doppl"

    private static var credentialsURL: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return base
            .appendingPathComponent("Cortex", isDirectory: true)
            .appendingPathComponent("credentials.json", isDirectory: false)
    }

    static func loadSecret(forKey key: String) -> String? {
        if let value = loadKeychainSecret(forKey: key) {
            return value
        }
        guard let payload = readLegacyPayload(),
              let value = normalized(payload[key]) else {
            return nil
        }
        if saveKeychainSecret(value, forKey: key) {
            removeLegacyFileSecret(forKey: key)
        }
        return value
    }

    static func saveSecret(_ value: String, forKey key: String) {
        guard let normalized = normalized(value) else { return }
        if saveKeychainSecret(normalized, forKey: key) {
            removeLegacyFileSecret(forKey: key)
        } else {
            writeLegacyFileSecret(normalized, forKey: key)
        }
        UserDefaults.standard.removeObject(forKey: key)
    }

    static func removeSecret(forKey key: String) {
        deleteKeychainSecret(forKey: key)
        removeLegacyFileSecret(forKey: key)
        UserDefaults.standard.removeObject(forKey: key)
    }

    static func removeLegacyDefault(forKey key: String) {
        UserDefaults.standard.removeObject(forKey: key)
    }

    private static func normalized(_ value: String?) -> String? {
        let value = (value ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : value
    }

    private static func keychainQuery(forKey key: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: keychainService,
            kSecAttrAccount as String: key,
            // Use the data-protection keychain, NOT the file-based login keychain. The login
            // keychain gates access behind a code-signature ACL, and because this build is ad-hoc
            // signed its identity isn't stable across launches — so macOS re-prompted for the login
            // password on every launch and "Always Allow" never stuck. The data-protection keychain
            // is scoped to the app itself and reads/writes silently (and if it's unavailable for any
            // reason, saveKeychainSecret falls back to a 0600 file — also promptless).
            kSecUseDataProtectionKeychain as String: true
        ]
    }

    private static func loadKeychainSecret(forKey key: String) -> String? {
        var query = keychainQuery(forKey: key)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess,
              let data = result as? Data,
              let value = String(data: data, encoding: .utf8) else {
            return nil
        }
        return normalized(value)
    }

    private static func saveKeychainSecret(_ value: String, forKey key: String) -> Bool {
        guard let data = value.data(using: .utf8) else { return false }
        let query = keychainQuery(forKey: key)
        let update: [String: Any] = [kSecValueData as String: data]
        let updateStatus = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        if updateStatus == errSecSuccess {
            return true
        }
        guard updateStatus == errSecItemNotFound else {
            NSLog("Cortex keychain update failed for \(key): \(updateStatus)")
            return false
        }

        var add = query
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let addStatus = SecItemAdd(add as CFDictionary, nil)
        if addStatus != errSecSuccess {
            NSLog("Cortex keychain save failed for \(key): \(addStatus)")
        }
        return addStatus == errSecSuccess
    }

    @discardableResult
    private static func deleteKeychainSecret(forKey key: String) -> Bool {
        let status = SecItemDelete(keychainQuery(forKey: key) as CFDictionary)
        if status != errSecSuccess && status != errSecItemNotFound {
            NSLog("Cortex keychain delete failed for \(key): \(status)")
        }
        return status == errSecSuccess || status == errSecItemNotFound
    }

    private static func readLegacyPayload() -> [String: String]? {
        guard let data = try? Data(contentsOf: credentialsURL),
              let raw = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }
        var payload: [String: String] = [:]
        for (key, value) in raw {
            if let stringValue = value as? String {
                payload[key] = stringValue
            }
        }
        return payload
    }

    private static func writeLegacyFileSecret(_ value: String, forKey key: String) {
        var payload = readLegacyPayload() ?? [:]
        payload[key] = value
        writeLegacyPayload(payload)
    }

    private static func removeLegacyFileSecret(forKey key: String) {
        guard var payload = readLegacyPayload() else { return }
        payload.removeValue(forKey: key)
        writeLegacyPayload(payload)
    }

    private static func writeLegacyPayload(_ payload: [String: String]) {
        let manager = FileManager.default
        let directory = credentialsURL.deletingLastPathComponent()
        do {
            try manager.createDirectory(at: directory, withIntermediateDirectories: true)
            try? manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
            guard !payload.isEmpty else {
                try? manager.removeItem(at: credentialsURL)
                return
            }
            let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: credentialsURL, options: .atomic)
            try? manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: credentialsURL.path)
        } catch {
            NSLog("Cortex credential store write failed: \(error.localizedDescription)")
        }
    }
}

@MainActor
final class AppState: ObservableObject {
    private static let apiKeyDefaultsKey = "localBetaAPIKey.v1"
    private static let mcpAPIKeyDefaultsKey = "localBetaMCPAPIKey.v1"
    static let cloudRefreshTokenKey = "cortexCloudRefreshToken.v1"
    static let cloudAccountEmailDefaultsKey = "cortexCloudAccountEmail.v1"
    static let cloudSyncBaseDefaultsKey = "cortexCloudSyncBase.v1"
    static let pushCursorDefaultsKey = "cortexPushCursor.v1"
    static let pushDeviceIDDefaultsKey = "cortexPushDeviceID.v1"
    static let localEndpointDefault = "http://127.0.0.1:8766"
    private static let obsidianVaultPathDefaultsKey = "connectedObsidianVaultPath.v1"
    private static let obsidianVaultBookmarkDefaultsKey = "connectedObsidianVaultBookmark.v1"
    private static let obsidianPluginAPIKeyDefaultsKey = "obsidianPluginAPIKey.v1"
    private static let obsidianPluginID = "cortex-memory"

    // Internal accessor so the Cortex Cloud extension (separate file) can restore the
    // local machine key on sign-out. Does not change local-mode behavior.
    static func restoreLocalAPIKey() -> String {
        loadOrCreateAPIKey()
    }

    private static func loadOrCreateAPIKey() -> String {
        if let existing = CortexCredentialStore.loadSecret(forKey: apiKeyDefaultsKey),
           !existing.isEmpty,
           existing != "dev-local-key" {
            return existing
        }
        let legacy = UserDefaults.standard.string(forKey: apiKeyDefaultsKey)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !legacy.isEmpty && legacy != "dev-local-key" {
            CortexCredentialStore.saveSecret(legacy, forKey: apiKeyDefaultsKey)
            return legacy
        }
        let generated = generateAPIKey()
        CortexCredentialStore.saveSecret(generated, forKey: apiKeyDefaultsKey)
        return generated
    }

    private static func loadOrCreateMCPAPIKey() -> String {
        if let existing = CortexCredentialStore.loadSecret(forKey: mcpAPIKeyDefaultsKey),
           existing.hasPrefix("cxm_") {
            return existing
        }
        let existing = UserDefaults.standard.string(forKey: mcpAPIKeyDefaultsKey)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if existing.hasPrefix("cxm_") {
            CortexCredentialStore.saveSecret(existing, forKey: mcpAPIKeyDefaultsKey)
            return existing
        }
        let generated = generateMCPAPIKey()
        CortexCredentialStore.saveSecret(generated, forKey: mcpAPIKeyDefaultsKey)
        return generated
    }

    private static func generateAPIKey() -> String {
        generateSecret(prefix: "cx_")
    }

    private static func generateMCPAPIKey() -> String {
        generateSecret(prefix: "cxm_")
    }

    // Per-app MCP tokens: each installed AI host gets its own credential, so a leaked
    // config from one app exposes only that app and can be revoked without breaking the
    // rest. The shared mcpAPIKey stays as the legacy/manual-copy fallback.
    private static func mcpTokenDefaultsKey(for integrationID: String) -> String {
        "mcp-token-\(integrationID)"
    }

    private static func existingMCPToken(for integrationID: String) -> String? {
        guard let existing = CortexCredentialStore.loadSecret(forKey: mcpTokenDefaultsKey(for: integrationID)),
              existing.hasPrefix("cxm_") else {
            return nil
        }
        return existing
    }

    private static func loadOrCreateMCPToken(for integrationID: String) -> String {
        if let existing = existingMCPToken(for: integrationID) {
            return existing
        }
        let generated = generateMCPAPIKey()
        CortexCredentialStore.saveSecret(generated, forKey: mcpTokenDefaultsKey(for: integrationID))
        return generated
    }

    private static func loadOrCreateObsidianPluginAPIKey() -> String {
        if let existing = CortexCredentialStore.loadSecret(forKey: obsidianPluginAPIKeyDefaultsKey),
           existing.hasPrefix("cx_") {
            return existing
        }
        let generated = generateAPIKey()
        CortexCredentialStore.saveSecret(generated, forKey: obsidianPluginAPIKeyDefaultsKey)
        return generated
    }

    private static func generateSecret(prefix: String) -> String {
        var generator = SystemRandomNumberGenerator()
        let bytes = (0..<32).map { _ in UInt8.random(in: UInt8.min...UInt8.max, using: &generator) }
        return prefix + bytes.map { String(format: "%02x", $0) }.joined()
    }

    @Published var endpoint: String = UserDefaults.standard.string(forKey: "endpoint") ?? "http://127.0.0.1:8766"
    @Published var apiKey: String = AppState.loadOrCreateAPIKey()
    @Published var mcpAPIKey: String = AppState.loadOrCreateMCPAPIKey()
    // Cortex Cloud (hosted account) state. Never populated in local mode.
    @Published var cloudAccountEmail: String = UserDefaults.standard.string(forKey: AppState.cloudAccountEmailDefaultsKey) ?? ""
    /// Hosted base URL the signed-in account syncs to (Option A: the account is identity + the sync
    /// target; the memory data plane stays LOCAL). Empty when signed out.
    @Published var cloudSyncBaseURL: String = UserDefaults.standard.string(forKey: AppState.cloudSyncBaseDefaultsKey) ?? ""
    /// Short-lived cxs_ access token used for CLOUD SYNC requests only (in-memory; it must never
    /// overwrite the local machine `apiKey` the local engine uses).
    var cloudAccessToken: String = ""
    // Phase-2 background push-sync (local memory -> the signed-in account). See CortexPushSync.swift.
    @Published var pushSyncState: PushSyncState = .idle
    @Published var pushPendingCount: Int = 0
    var pushSyncTask: Task<Void, Never>?
    var pushSyncInFlight = false
    @Published var cloudAuthBusy: Bool = false
    @Published var cloudAuthMessage: String = ""
    @Published var importHistory: [SourceImportHistoryItem] = []
    @Published var sourceConnectorCatalog: [SourceConnectorCatalogItem] = []
    @Published var sourceReadinessReport: SourceReadinessResponse?
    @Published var sourceAccounts: [SourceAccountItem] = []
    @Published var allSourceAccounts: [SourceAccountItem] = []
    @Published var syncCursors: [SyncCursorItem] = []
    @Published var syncDevices: [SyncDeviceItem] = []
    @Published var syncReceiptsByDevice: [String: [SyncReceiptItem]] = [:]
    @Published var obsidianVaultPath: String = UserDefaults.standard.string(forKey: obsidianVaultPathDefaultsKey) ?? ""
    @Published var searchQuery: String = ""
    @Published var status: String = "Ready"
    @Published var inbox: [CaptureItem] = []
    @Published var recent: [MemoryItem] = []
    @Published var searchResults: [MemoryItem] = []
    @Published var askAnswer: String = ""
    @Published var askCitations: [AskCitationItem] = []
    @Published var hasSearched: Bool = false
    @Published var graphNodes: [GraphNode] = []
    @Published var graphEdges: [GraphEdge] = []
    @Published var graphAnalysis: GraphAnalysis?
    @Published var stats: StatsResponse?
    @Published var syncProgress: SyncProgress?
    @Published var detectedExportSummary: String?
    @Published var importInFlight: Bool = false
    private var detectedExportPaths: [String] = []
    @Published var mirrorInsight: MirrorInsight?
    @Published private var mirrorDismissedHeadlines: Set<String> = Set(
        UserDefaults.standard.stringArray(forKey: AppState.mirrorDismissedDefaultsKey) ?? []
    )
    @Published var profile: ProfileResponse?
    @Published var memoryQuality: MemoryQualityResponse?
    @Published var review: DailyReviewResponse?
    @Published var productLoop: ProductLoopResponse?
    @Published var appSettings: AppSettingsResponse = .defaults
    @Published var trustSummary: TrustSummaryResponse?
    @Published var dataLifecycleReport: DataLifecycleReportResponse?
    @Published var auditEvents: [AuditEventItem] = []
    @Published var integrationTokens: [IntegrationTokenItem] = []
    @Published var showRevokedIntegrationTokens: Bool = false
    @Published var diagnostics: DiagnosticsResponse?
    @Published var reliabilityReport: ReliabilityReportResponse?
    @Published var lastBackupPath: String?
    @Published var lastSupportBundlePath: String?
    @Published var lastRepairSummary: String = ""
    @Published var backendStatus: String = "Starting"
    @Published var backendLogPath: String = BackendSupervisor.shared.logURL.path
    @Published var updateFeedURL: String = UserDefaults.standard.string(forKey: "updateFeedURL")
        ?? (Bundle.main.object(forInfoDictionaryKey: "CortexUpdateFeedURL") as? String ?? "")
    @Published var updateStatus: String = "Not checked"
    @Published var updateManifest: UpdateManifestResponse?
    @Published var selectedTab: AppTab = .model
    @Published var vaultPath: String = UserDefaults.standard.string(forKey: "vaultPath") ?? BackendSupervisor.defaultVaultURL.path
    @Published var onboardingComplete: Bool = UserDefaults.standard.bool(forKey: "onboardingComplete.v1")
    @Published var showOnboarding: Bool = false
    @Published var showConnectionsPrivacy: Bool = false
    // Onboarding and Connections are separate sheets on the same presenter — only one can show at
    // a time. These coordinate handing off from one to the other (see openConnectionsPrivacy).
    private var pendingOpenConnectionsAfterOnboarding = false
    private var reopenOnboardingAfterConnections = false
    @Published var onboardingStep: OnboardingStep = OnboardingStep(rawValue: UserDefaults.standard.integer(forKey: "onboardingStep.v2")) ?? .privateVault
    @Published var firstSourceAdded: Bool = UserDefaults.standard.bool(forKey: "onboardingFirstSourceImported.v1")
    @Published var firstMemoryReviewed: Bool = UserDefaults.standard.bool(forKey: "onboardingFirstMemoryReviewed.v1")
    @Published var cortexUsed: Bool = UserDefaults.standard.bool(forKey: "onboardingCortexUsed.v1")
    @Published var onboardingFirstSourceNames: [String] = UserDefaults.standard.stringArray(forKey: "onboardingFirstSourceNames.v1") ?? []
    @Published var onboardingBackupDecision: String = UserDefaults.standard.string(forKey: "onboardingBackupDecision.v1") ?? ""
    @Published var integrationStates: [String: AIIntegrationState] = [:]
    @Published var isBusy: Bool = false
    @Published var askError: String?
    @Published var inFlightCaptureIds: Set<String> = []
    @Published var inFlightMemoryIds: Set<String> = []
    @Published var captureActionErrors: [String: String] = [:]
    @Published var backendRetryInProgress: Bool = false
    @Published var connectorSyncingIDs: Set<String> = []
    @Published var connectorOAuthStartingIDs: Set<String> = []
    @Published var connectorLastMessages: [String: String] = [:]
    @Published var configuredDirectConnectorIDs: Set<String> = []
    // Drives the "Sign in with GitHub" device-code sheet. Non-nil while a device flow is live; the
    // sheet reads it, and cancelling (or completing) clears it, which also stops the poll loop.
    @Published var githubDeviceFlow: GitHubDeviceFlowPrompt?
    // Last browser-extension pairing (read-only token + connection info) to display for copy.
    @Published var browserExtensionPairing: BrowserExtensionPairing?
    @Published var browserExtensionPairingInFlight: Bool = false

    // MARK: Settings surface + quick capture + "learned" signals
    //
    // presentSettings is a one-shot request flag: the App-menu "Settings…" item sets it true, the
    // window content observes it and opens the Connections & Privacy surface, then resets it.
    @Published var presentSettings: Bool = false

    /// Whether the bottom-of-screen live-activity surfaces (Learning HUD, edge glow, memory ripple,
    /// "N to review" pill) are shown at all. Persisted; default ON. Turn off from the menu-bar
    /// right-click menu or Settings when they're distracting — the coordinator then hides everything.
    static let liveActivityEnabledDefaultsKey = "liveActivityEnabled.v1"
    @Published var liveActivityEnabled: Bool = (UserDefaults.standard.object(forKey: AppState.liveActivityEnabledDefaultsKey) as? Bool) ?? true {
        didSet {
            guard liveActivityEnabled != oldValue else { return }
            UserDefaults.standard.set(liveActivityEnabled, forKey: AppState.liveActivityEnabledDefaultsKey)
        }
    }

    // Quick capture (highlight/screenshot → memory) preference + keybind. Both persisted. All
    // capture actions are no-ops in App Store (sandboxed) builds — see QuickCapture.setEnabled.
    static let quickCaptureEnabledDefaultsKey = "quickCaptureEnabled.v1"
    static let quickCaptureKeybindDefaultsKey = "quickCaptureKeybind.v1"
    @Published var quickCaptureEnabled: Bool = UserDefaults.standard.bool(forKey: AppState.quickCaptureEnabledDefaultsKey) {
        didSet {
            guard quickCaptureEnabled != oldValue else { return }
            UserDefaults.standard.set(quickCaptureEnabled, forKey: AppState.quickCaptureEnabledDefaultsKey)
            refreshQuickCaptureWiring()
        }
    }
    @Published var quickCaptureKeybind: KeyCombo? = AppState.loadQuickCaptureKeybind() {
        didSet {
            AppState.persistQuickCaptureKeybind(quickCaptureKeybind)
            refreshQuickCaptureWiring()
        }
    }

    // Deterministic "learned/captured" timestamps the menu-bar animator (agent 4) observes via
    // MenuBarSnapshot.learnedAt / .capturedAt. A NEW stamp fires the moss sparkle flourish; a level
    // signal can't be missed by the animator's sampling loop the way an edge-triggered event could.
    @Published private(set) var lastLearnedAt: Date?
    @Published private(set) var lastCapturedAt: Date?
    /// How many memories the most recent `announceLearned` reported. Drives the bottom Learning
    /// HUD's completion line ("Learned N new memories") — approximate (last event, not a sum), which
    /// is fine for a flourish.
    @Published private(set) var lastLearnedCount: Int = 0

    private static func loadQuickCaptureKeybind() -> KeyCombo? {
        guard let data = UserDefaults.standard.data(forKey: quickCaptureKeybindDefaultsKey) else { return nil }
        return try? JSONDecoder().decode(KeyCombo.self, from: data)
    }

    private static func persistQuickCaptureKeybind(_ combo: KeyCombo?) {
        if let combo, let data = try? JSONEncoder().encode(combo) {
            UserDefaults.standard.set(data, forKey: quickCaptureKeybindDefaultsKey)
        } else {
            UserDefaults.standard.removeObject(forKey: quickCaptureKeybindDefaultsKey)
        }
    }

    private let backend = BackendSupervisor.shared
    private var obsidianAutoSyncTask: Task<Void, Never>?
    private var directConnectorAutoSyncTask: Task<Void, Never>?
    private var ensureBackendTask: Task<Void, Never>?
    private var syncProgressPollTask: Task<Void, Never>?
    private var obsidianSyncInFlight = false
    private var jobDrainInFlight = false
    private var onboardingDismissedForSession = false

    private static let directConnectorConfigSecretPrefix = "directConnectorConfig.v1."
    private static let directConnectorSyncIDs: Set<String> = [
        "calendar",
        "gmail",
        "github",
        "google-drive",
        "jira",
        "linear",
        "notion",
        "outlook",
        "raindrop",
        "readwise",
        "slack",
        "zotero"
    ]
    private static let directConnectorSyncOrder = [
        "gmail",
        "google-drive",
        "notion",
        "outlook",
        "zotero",
        "calendar",
        "readwise",
        "raindrop",
        "linear",
        "slack",
        "github",
        "jira"
    ]

    var integrations: [AIIntegration] {
        AIIntegrationCatalog.all
    }

    var displayStatus: String {
        let backend = displayBackendStatus
        let current = CortexRecoveryText.statusLine(status)
        if backend == current { return current }
        return "\(backend) · \(current)"
    }

    var displayBackendStatus: String {
        CortexRecoveryText.statusLine(backendStatus)
    }

    var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.0.0"
    }

    var appBuild: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0"
    }

    var releaseChannel: String {
        Bundle.main.object(forInfoDictionaryKey: "CortexReleaseChannel") as? String ?? "local-beta"
    }

    var isLocalServiceReady: Bool {
        guard let diagnostics, let vault = diagnostics.vault else { return false }
        let lowered = backendStatus.lowercased()
        let configuredVault = URL(fileURLWithPath: vaultPath).standardizedFileURL.path
        let reportedVault = URL(fileURLWithPath: vault.path).standardizedFileURL.path
        return diagnostics.status == "ok"
            && reportedVault == configuredVault
            && !lowered.contains("failed")
            && !lowered.contains("offline")
            && !lowered.contains("unavailable")
            && !lowered.contains("incompatible")
    }

    var onboardingHasConnectedMemoryLayer: Bool {
        hasConnectedSourceAccount
            || hasConnectedObsidianVault
    }

    /// True when the local memory engine has clearly failed or stalled starting, so the
    /// first-run flow can offer a recovery path instead of spinning on "Starting…" forever.
    var backendNeedsRecovery: Bool {
        guard !isLocalServiceReady else { return false }
        let lowered = backendStatus.lowercased()
        return lowered.contains("failed")
            || lowered.contains("did not become ready")
            || lowered.contains("still starting")
            || lowered.contains("offline")
            || lowered.contains("unavailable")
            || lowered.contains("incompatible")
            || lowered.contains("error")
    }

    var onboardingHasSyncedMemory: Bool {
        if sourceReadinessReport != nil {
            return !onboardingHealthyMemorySources.isEmpty
        }
        return !inbox.isEmpty || (stats?.pending_captures ?? 0) > 0 || (stats?.memories ?? 0) > 0
    }

    var onboardingHasSource: Bool {
        onboardingHasConnectedMemoryLayer && onboardingHasSyncedMemory
    }

    var onboardingHealthyMemorySources: [SourceReadinessItem] {
        guard let sources = sourceReadinessReport?.sources else { return [] }
        return sources.filter { source in
            onboardingSourceIsHealthyAndUsable(source)
        }
    }

    var onboardingSourceHealthMessage: String? {
        guard onboardingHasConnectedMemoryLayer else { return nil }
        guard let report = sourceReadinessReport else {
            return "Checking source health before setup can continue."
        }
        if !onboardingHealthyMemorySources.isEmpty {
            return nil
        }
        if report.summary.needs_attention > 0 {
            return "A connected source needs attention before Cortex can finish setup."
        }
        if report.sources.contains(where: { $0.status == "empty" }) {
            return "The connected source has no usable content yet. Choose notes or a source with real memory."
        }
        if report.sources.contains(where: { $0.sync_plan?.due_now == true }) {
            return "The connected source is ready to sync. Sync it before setup finishes."
        }
        if report.sources.contains(where: { $0.sync_plan?.managed_sync_status == "waiting_for_first_sync" }) {
            return "The connected source is waiting for its first completed sync."
        }
        if report.sources.contains(where: { $0.captures > 0 || $0.pending > 0 || $0.approved > 0 || $0.active_memories > 0 }) {
            return "Source data exists, but Cortex is still checking whether it is fresh and citable."
        }
        return "Waiting for synced memory from the connected source."
    }

    private func onboardingSourceIsHealthyAndUsable(_ source: SourceReadinessItem) -> Bool {
        let hasUsableData = source.captures > 0
            || source.pending > 0
            || source.approved > 0
            || source.active_memories > 0
        guard hasUsableData else { return false }
        if source.status == "needs_attention" || source.status == "empty" {
            return false
        }
        let syncStatus = source.sync_plan?.managed_sync_status.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() ?? ""
        if ["needs_attention", "backing_off"].contains(syncStatus) {
            return false
        }
        if !source.warnings.isEmpty {
            return false
        }
        // A due or still-running sync must not hold onboarding hostage once usable data exists:
        // a large first sync can run for minutes while earlier batches are already citable.
        return ["needs_review", "synced", "imported", "connected", "syncing"].contains(source.status)
    }

    var hasConnectedSourceAccount: Bool {
        sourceAccounts.contains { $0.isConnected }
    }

    /// Cortex isn't usable until at least one real data source feeds it, so onboarding may only
    /// COMPLETE — and the vault is only treated as initialized — once the user has connected a
    /// service, their Obsidian vault, or completed an import. firstSourceAdded is authoritative
    /// (it persists the "a source was connected" event) since sourceAccounts may still be loading.
    var hasAtLeastOneConnectedSource: Bool {
        firstSourceAdded || hasConnectedSourceAccount || hasConnectedObsidianVault
    }

    // "Active" = registered and not disconnected (includes unconfigured placeholders); used to
    // locate the account row for a connector card. "Connected" = actually authenticated.
    var activeSourceAccounts: [SourceAccountItem] {
        sourceAccounts.filter { $0.disconnected_at == nil }
    }

    var connectedSourceAccounts: [SourceAccountItem] {
        sourceAccounts.filter { $0.isConnected }
    }

    var knownSourceAccounts: [SourceAccountItem] {
        allSourceAccounts.isEmpty ? sourceAccounts : allSourceAccounts
    }

    func sourceAccount(_ connector: SourceConnectorCatalogItem, includeDisconnected: Bool = false) -> SourceAccountItem? {
        let accounts = includeDisconnected ? knownSourceAccounts : activeSourceAccounts
        return accounts.first { account in
            sourceAccount(account, matches: connector)
        }
    }

    func disconnectedSourceAccount(_ connector: SourceConnectorCatalogItem) -> SourceAccountItem? {
        knownSourceAccounts.first { account in
            account.disconnected_at != nil && sourceAccount(account, matches: connector)
        }
    }

    private func sourceAccount(_ account: SourceAccountItem, matches connector: SourceConnectorCatalogItem) -> Bool {
        account.source == connector.id || (connector.source_ids ?? []).contains(account.source)
    }

    var hasConnectedObsidianVault: Bool {
        storedObsidianVaultURL() != nil
    }

    var obsidianReadiness: SourceReadinessItem? {
        sourceReadinessReport?.sources.first { source in
            source.source == "obsidian" || (source.source_ids ?? []).contains("obsidian")
        }
    }

    var notesNeedContent: Bool {
        if obsidianReadiness?.status == "empty" {
            return true
        }
        if activeSourceAccounts.contains(where: { $0.source == "obsidian" && $0.needsContent }) {
            return true
        }
        return false
    }

    var firstRunNeedsSource: Bool {
        !onboardingComplete && !onboardingHasSource
    }

    var detectedAIIntegrationCount: Int {
        integrations.filter { $0.supportsInstall && integrationState(for: $0).appInstalled }.count
    }

    var connectedAIIntegrationCount: Int {
        integrations.filter { integrationState(for: $0).configured }.count
    }

    var onboardingHasReviewedMemory: Bool {
        firstMemoryReviewed
            || (stats?.memories ?? 0) > 0
            || (sourceReadinessReport?.summary.active_memories ?? 0) > 0
    }

    var onboardingHasUsedCortex: Bool {
        cortexUsed
    }

    var onboardingHasBackupDecision: Bool {
        !onboardingBackupDecision.isEmpty
            || lastBackupPath != nil
            || (dataLifecycleReport?.backups.count ?? 0) > 0
            || reliabilityReport?.latest_backup != nil
    }

    var canCompleteOnboarding: Bool {
        OnboardingStep.allCases.allSatisfy { onboardingStepIsComplete($0) }
    }

    var incompleteOnboardingStepTitles: [String] {
        OnboardingStep.allCases.filter { !onboardingStepIsComplete($0) }.map(\.title)
    }

    var setupIncomplete: Bool {
        !onboardingComplete
    }

    var setupIncompleteDetail: String {
        let remaining = incompleteOnboardingStepTitles.prefix(2).joined(separator: ", ")
        if remaining.isEmpty {
            return "First memory loop is ready to finish."
        }
        return "Next: \(remaining)."
    }

    func onboardingStepIsComplete(_ step: OnboardingStep) -> Bool {
        switch step {
        case .privateVault:
            return isLocalServiceReady
        case .firstSource:
            // The "connect a source" step is done once a source has actually been connected +
            // synced (firstSourceAdded is the @Published flag set right after a successful
            // connect on every path — Obsidian folder, OAuth, or token). Previously this required
            // fully synced, health-checked, citable memory (onboardingHasSource), which trapped
            // users at "I connected my notes but Continue never enables" while the readiness
            // health check lagged. The next step (Review) handles the actual memory.
            return firstSourceAdded || onboardingHasSource
        case .reviewMemory:
            return onboardingHasReviewedMemory
        case .askUse:
            return onboardingHasUsedCortex || onboardingHasReviewedMemory
        case .trustBackup:
            return onboardingHasBackupDecision
        }
    }

    var canAdvanceOnboarding: Bool {
        switch onboardingStep {
        case .privateVault:
            // Legitimate hard prerequisite: Cortex is unusable without its memory engine.
            return isLocalServiceReady
        case .firstSource:
            // Legitimate gate: onboarding must connect at least one real source.
            return onboardingStepIsComplete(.firstSource)
        case .reviewMemory, .askUse:
            // Review and Ask are ENCOURAGED, not mandatory. Previously these were hard-gated on
            // having reviewed/used memory, which trapped any user with nothing to review (empty or
            // still-syncing source) on a permanently-disabled Continue. Always allow proceeding;
            // the footer shows "Skip for now" until the step is actually completed.
            return true
        case .trustBackup:
            return true
        }
    }

    var onboardingAskSuggestions: [String] {
        let sourceMatched = recent.filter { matchesOnboardingSource(source: $0.source, sourceURL: $0.source_url) }
        let candidates = (sourceMatched.isEmpty ? recent : sourceMatched).prefix(3)
        var suggestions = candidates.compactMap { onboardingAskSuggestion(from: $0.content) }
        if suggestions.isEmpty {
            suggestions = onboardingFirstSourceNames.prefix(2).map { "What useful memory came from \($0)?" }
        }
        var unique: [String] = []
        for suggestion in suggestions where !unique.contains(suggestion) {
            unique.append(suggestion)
            if unique.count == 3 {
                break
            }
        }
        return unique
    }

    var preferredUpdateArtifact: UpdateArtifact? {
        updateManifest?.artifacts.first(where: { $0.kind == "dmg" }) ?? updateManifest?.artifacts.first
    }

    func persistSettings() {
        ensureUsableAPIKey()
        ensureUsableMCPAPIKey()
        UserDefaults.standard.set(endpoint, forKey: "endpoint")
        UserDefaults.standard.set(vaultPath, forKey: "vaultPath")
        CortexCredentialStore.saveSecret(apiKey, forKey: Self.apiKeyDefaultsKey)
        CortexCredentialStore.saveSecret(mcpAPIKey, forKey: Self.mcpAPIKeyDefaultsKey)
        status = "Settings saved"
    }

    func saveUpdateSettings() {
        UserDefaults.standard.set(updateFeedURL.trimmingCharacters(in: .whitespacesAndNewlines), forKey: "updateFeedURL")
        updateStatus = "Update feed saved"
        status = "Update feed saved"
    }

    func useBundledUpdateFeedExample() {
        if let url = Bundle.main.url(forResource: "update-feed.example", withExtension: "json") {
            updateFeedURL = url.absoluteString
            saveUpdateSettings()
            checkForUpdates()
        } else {
            updateStatus = "Bundled example feed missing"
        }
    }

    func bootstrap() async {
        // Launch is real work — spin the menu-bar icon while the engine starts and data loads.
        // (Also serves as a visible on-launch proof that the icon animates at all.)
        beginMenuBarWork()
        defer { endMenuBarWork() }
        // Local-first + cloud-sync (Option A): make sure the data plane is local before anything reads
        // it — a prior build may have persisted a remote `endpoint` for a signed-in user.
        migrateToLocalFirstDataPlane()
        // First-run guidance must appear INSTANTLY — before the backend cold-starts and the
        // load*() chain runs below — otherwise a slow/cold engine leaves a genuine new user
        // staring at a blank window with no instructions. This is self-heal-free and gated purely
        // on the persisted onboardingComplete flag, so returning users are never affected.
        presentOnboardingForFirstRunIfNeeded()
        // Cortex Cloud only: the cxs_ access token lives in memory, so on launch (and any
        // full bootstrap) it must be re-minted from the stored refresh token before any
        // authenticated call fires. Local mode never enters this branch.
        if isCloudMode {
            if await refreshCloudAccessToken() == false {
                handleCloudSessionExpired()
            }
        }
        await ensureBackend()
        await loadInbox()
        await loadRecent()
        await loadGraph()
        await loadStats()
        await loadSettings()
        await loadTrust()
        await loadReview()
        await loadProductLoop()
        await loadImportHistory()
        await loadDiagnostics()
        await loadReliability()
        await loadMirrorInsight()
        await loadProfile()
        refreshStoredConnectorConfigState()
        refreshIntegrationStates()
        startConnectedSourceAutoSync()
        startPushSync()
        startSyncProgressPolling()
        activateQuickCaptureIfEnabled()
        presentOnboardingIfNeeded()
        // Fire a one-time "proof of life" notch so a new user actually sees the notch channel work
        // even before any learn/capture event happens. Gated on its OWN key (not onboardingComplete,
        // which may already be true on a machine that ran earlier builds) so it shows exactly once
        // per machine, and delayed so it lands after the window/sheet are on screen.
        if !UserDefaults.standard.bool(forKey: "welcomeNotchShown.v1") {
            UserDefaults.standard.set(true, forKey: "welcomeNotchShown.v1")
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
                NotchNotifier.shared.show(
                    title: "Welcome to Cortex",
                    subtitle: "This is your notch — Cortex speaks here.",
                    style: .info
                )
            }
        }
    }

    func ensureBackend() async {
        // Coalesce overlapping callers so concurrent requests don't each restart
        // (terminate + relaunch) the backend and race one another.
        if let inFlight = ensureBackendTask {
            await inFlight.value
            return
        }
        let task = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.performEnsureBackend()
        }
        ensureBackendTask = task
        await task.value
        ensureBackendTask = nil
    }

    private func performEnsureBackend() async {
        ensureUsableAPIKey()
        ensureUsableMCPAPIKey()
        backendStatus = "Checking memory engine"
        let message = await backend.ensureRunning(
            endpoint: endpoint,
            apiKey: apiKey,
            mcpAPIKey: mcpAPIKey,
            vaultPath: vaultPath,
            onProgress: { [weak self] progress in
                Task { @MainActor in
                    self?.backendStatus = progress
                    self?.status = progress
                }
            }
        )
        backendStatus = message
        status = message
        _ = await registerMCPToken()
        // Re-register existing per-app tokens so connected hosts keep working after an
        // engine reset. The backend upserts by token, so this is idempotent.
        for integration in integrations where AppState.existingMCPToken(for: integration.id) != nil {
            _ = await registerMCPToken(for: integration)
        }
    }

    /// User-triggered retry when the memory engine failed or stalled during first run.
    /// Re-runs the supervisor and reloads the health diagnostics that gate the vault step.
    func retryBackendStart() {
        guard !backendRetryInProgress else { return }
        backendRetryInProgress = true
        Task { [weak self] in
            guard let self else { return }
            await self.ensureBackend()
            await self.loadDiagnostics()
            await self.loadReliability()
            self.backendRetryInProgress = false
        }
    }

    /// Reveal the backend log in Finder so a stuck first-run user can share it for support.
    func revealBackendLog() {
        let url = URL(fileURLWithPath: backendLogPath)
        if FileManager.default.fileExists(atPath: url.path) {
            NSWorkspace.shared.activateFileViewerSelecting([url])
        } else {
            NSWorkspace.shared.open(url.deletingLastPathComponent())
        }
    }

    private func registerMCPToken() async -> Bool {
        // Legacy shared token: still used by manual copy flows and by configs written
        // before per-app tokens existed.
        await registerMCPToken(mcpAPIKey, label: "Connected AI tools")
    }

    private func registerMCPToken(for integration: AIIntegration) async -> Bool {
        await registerMCPToken(AppState.loadOrCreateMCPToken(for: integration.id), label: integration.name)
    }

    /// Fire-and-forget registration for install paths that must stay synchronous.
    /// The backend upserts by token, so repeat registrations are harmless.
    private func registerMCPTokenInBackground(for integration: AIIntegration) {
        Task { [weak self] in
            _ = await self?.registerMCPToken(for: integration)
        }
    }

    private func registerMCPToken(_ token: String, label: String) async -> Bool {
        do {
            _ = try await performRequest(
                path: "/v1/integrations/mcp-token",
                method: "POST",
                body: [
                    "token": token,
                    "label": label,
                    // Least privilege: read covers the whole distilled picture (profile, person
                    // map, context) and write covers remember_this + source syncs. Raw exports,
                    // maintenance, and review approvals stay off these agent tokens — the
                    // review gate is a human surface, and a leaked host config must not be able
                    // to bulk-dump the corpus.
                    "scopes": ["read", "write"]
                ]
            )
            return true
        } catch {
            status = CortexRecoveryText.failureStatus("tool access registration", error: error)
            return false
        }
    }

    private func registerObsidianPluginToken() async throws -> String {
        let token = Self.loadOrCreateObsidianPluginAPIKey()
        let data = try await performRequest(
            path: "/v1/integrations/api-token",
            method: "POST",
            body: [
                "token": token,
                "label": "Cortex notes bridge",
                "scopes": ["read", "write"]
            ]
        )
        _ = try JSONDecoder().decode(IntegrationTokenRegistrationResponse.self, from: data)
        return token
    }

    private func ensureUsableAPIKey() {
        // In Cortex Cloud mode the in-memory apiKey is a short-lived cxs_ access token
        // that must NOT be written over the local machine key. Skip entirely so the
        // local Keychain slot stays intact and local mode is unaffected.
        if isCloudMode { return }
        let normalized = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        if normalized.isEmpty || normalized == "dev-local-key" {
            apiKey = AppState.generateAPIKey()
            CortexCredentialStore.saveSecret(apiKey, forKey: Self.apiKeyDefaultsKey)
        } else if normalized != apiKey {
            apiKey = normalized
            CortexCredentialStore.saveSecret(apiKey, forKey: Self.apiKeyDefaultsKey)
        } else {
            CortexCredentialStore.saveSecret(apiKey, forKey: Self.apiKeyDefaultsKey)
        }
        CortexCredentialStore.removeLegacyDefault(forKey: Self.apiKeyDefaultsKey)
    }

    private func ensureUsableMCPAPIKey() {
        let normalized = mcpAPIKey.trimmingCharacters(in: .whitespacesAndNewlines)
        if !normalized.hasPrefix("cxm_") || normalized == apiKey {
            mcpAPIKey = AppState.generateMCPAPIKey()
            CortexCredentialStore.saveSecret(mcpAPIKey, forKey: Self.mcpAPIKeyDefaultsKey)
        } else if normalized != mcpAPIKey {
            mcpAPIKey = normalized
            CortexCredentialStore.saveSecret(mcpAPIKey, forKey: Self.mcpAPIKeyDefaultsKey)
        } else {
            CortexCredentialStore.saveSecret(mcpAPIKey, forKey: Self.mcpAPIKeyDefaultsKey)
        }
        CortexCredentialStore.removeLegacyDefault(forKey: Self.mcpAPIKeyDefaultsKey)
    }

    func chooseVaultFolder() {
        if DistributionMode.isAppStore {
            vaultPath = BackendSupervisor.defaultVaultURL.path
            UserDefaults.standard.set(vaultPath, forKey: "vaultPath")
            status = "App Store builds keep the vault inside the app container"
            return
        }
        let panel = NSOpenPanel()
        panel.title = "Choose Cortex Vault Folder"
        panel.prompt = "Use Folder"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        if FileManager.default.fileExists(atPath: vaultPath) {
            panel.directoryURL = URL(fileURLWithPath: vaultPath)
        }
        if panel.runModal() == .OK, let url = panel.url {
            setVaultPath(url.path)
        }
    }

    func useDefaultVaultFolder() {
        setVaultPath(BackendSupervisor.defaultVaultURL.path)
    }

    func openVaultFolder() {
        try? FileManager.default.createDirectory(atPath: vaultPath, withIntermediateDirectories: true, attributes: nil)
        NSWorkspace.shared.open(URL(fileURLWithPath: vaultPath))
    }

    private func setVaultPath(_ path: String) {
        let standardized = URL(fileURLWithPath: path).standardizedFileURL.path
        vaultPath = standardized
        UserDefaults.standard.set(standardized, forKey: "vaultPath")
        status = "Vault folder set"
        backend.terminate()
        Task { await bootstrap() }
    }

    func capture(text: String, source: String, title: String, sourceURL: String? = nil) async -> Bool {
        isBusy = true
        status = "Saving..."
        defer { isBusy = false }
        do {
            var body: [String: Any] = ["content": text, "source": source, "title": title]
            if let sourceURL, !sourceURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                body["source_url"] = sourceURL
            }
            let data = try await request(path: "/v1/captures", method: "POST", body: body)
            let response = try JSONDecoder().decode(CaptureResponse.self, from: data)
            status = "Saved \(response.memories.count) memories"
            notify("Cortex", "Saved \(response.memories.count) memories")
            await loadInbox()
            await loadRecent()
            await loadGraph()
            await loadStats()
            await loadReview()
            await loadProductLoop()
            await loadDiagnostics()
            await loadReliability()
            await loadTrust()
            return true
        } catch {
            status = CortexRecoveryText.failureStatus("Save", error: error)
            notify("Cortex", "Save failed")
            return false
        }
    }

    /// One-off ask for the menu-bar quick panel. Deliberately does NOT touch the shared Ask-tab
    /// @Published buffers (searchQuery/askAnswer/askCitations) or `isBusy`, so the panel is fully
    /// independent of the Ask tab and doesn't spin the menu-bar icon for a quick lookup.
    func askOnce(_ query: String) async -> AskResponse? {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { return nil }
        do {
            let encoded = q.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? q
            let data = try await request(path: "/v1/ask?query=\(encoded)&limit=8", method: "GET")
            return try JSONDecoder().decode(AskResponse.self, from: data)
        } catch {
            return nil
        }
    }

    /// Quick capture from the menu-bar panel: saves a manual memory via the same capture endpoint,
    /// then refreshes just the review/stats surfaces so the panel + icon counts update. Keeps its
    /// own in-flight state out of the shared `isBusy` so a 200ms save doesn't spin the menu-bar icon.
    func captureFromPanel(text: String) async -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }
        do {
            let title = String(trimmed.prefix(60))
            let body: [String: Any] = ["content": trimmed, "source": "quick-capture", "title": title]
            _ = try await request(path: "/v1/captures", method: "POST", body: body)
            await loadInbox()
            await loadReview()
            await loadStats()
            return true
        } catch {
            return false
        }
    }

    private func refreshAfterCapture() async {
        await loadInbox()
        await loadRecent()
        await loadGraph()
        await loadStats()
        await loadReview()
        await loadProductLoop()
        await loadImportHistory()
        await loadDiagnostics()
        await loadReliability()
        await loadTrust()
        await loadMirrorInsight()
        await loadProfile()
    }

    func loadInbox() async {
        do {
            let data = try await request(path: "/v1/inbox?limit=30", method: "GET")
            inbox = try JSONDecoder().decode(InboxResponse.self, from: data).results
        } catch {
            status = CortexRecoveryText.failureStatus("Review queue", error: error)
        }
    }

    func loadRecent() async {
        do {
            let data = try await request(path: "/v1/recent?limit=20", method: "GET")
            recent = try JSONDecoder().decode(RecentResponse.self, from: data).results
        } catch {
            status = CortexRecoveryText.failureStatus("Recent", error: error)
        }
    }

    func loadImportHistory() async {
        do {
            let data = try await request(path: "/v1/imports?limit=12&include_deleted=false", method: "GET")
            importHistory = try JSONDecoder().decode(SourceImportHistoryResponse.self, from: data).results
        } catch {
            status = CortexRecoveryText.failureStatus("Connection history", error: error)
        }
    }

    func runSearch() {
        Task { await search() }
    }

    /// Clear the current Ask answer/results (used when the query field is emptied) so a stale answer
    /// doesn't linger under an empty search box.
    func clearAskResults() {
        searchResults = []
        askAnswer = ""
        askCitations = []
        askError = nil
        hasSearched = false
    }

    // MARK: Menu-bar activity signaling

    // The menu-bar icon animates from these LEVEL-based signals, not from sampled transients.
    // Previously the spinner keyed off syncProgress (derived from a 4-second job poll — fast syncs
    // finished between polls and were never observed) and isBusy (a brief flag the 0.45s animator
    // sample could miss entirely), so in practice the icon never moved. A counter that stays >0 for
    // the whole duration of an operation cannot be missed by sampling, and an explicit completion
    // timestamp drives the checkmark deterministically instead of via poll edge-detection.
    @Published private(set) var menuBarWorkCount = 0
    @Published private(set) var menuBarSyncCompletedAt: Date?

    /// Mark the start of user-visible work (sync/import/backup/launch) for the menu-bar icon.
    func beginMenuBarWork() {
        // Reset the learned-count at the START of the OUTERMOST work unit (0→1). Otherwise a sync
        // that learns nothing never calls announceLearned, the stale count from a prior productive
        // sync lingers, and the bottom HUD would falsely celebrate "Learned N new memories" on the
        // next completion. Nested work units (count>0) must not reset a count set by an inner unit.
        if menuBarWorkCount == 0 { lastLearnedCount = 0 }
        menuBarWorkCount += 1
    }

    /// Mark the end of that work. `completedSync: true` also stamps the completion time so the
    /// menu-bar icon plays its "sync complete" checkmark.
    func endMenuBarWork(completedSync: Bool = false) {
        menuBarWorkCount = max(0, menuBarWorkCount - 1)
        if completedSync {
            menuBarSyncCompletedAt = Date()
        }
    }

    // MARK: Quick capture + "learned" flourish

    /// Cortex just learned `count` new memories. Announces it two ways: the top-center notch pill,
    /// and a NEW `lastLearnedAt` stamp the menu-bar animator turns into its moss sparkle flourish.
    /// No-op for count <= 0 so a sync that added nothing stays quiet.
    func announceLearned(count: Int) {
        guard count > 0 else { return }
        lastLearnedCount = count
        lastLearnedAt = Date()
        let subtitle = "\(count) new " + (count == 1 ? "memory" : "memories")
        NotchNotifier.shared.show(title: "Learned something new", subtitle: subtitle, style: .learned)
        // New memory just landed locally — nudge the background push-sync so it reaches the account
        // promptly instead of waiting for the next 5-minute tick (debounced by pushSyncInFlight).
        Task { await pushSyncNudge() }
    }

    /// Wire (or unwire) global quick-capture based on the current pref + keybind. Capture is only
    /// ever active on the direct/DMG build — the sandboxed App Store build can't run the
    /// highlight/screenshot capture path, so QuickCapture.setEnabled is a documented no-op there.
    private func refreshQuickCaptureWiring() {
        guard !DistributionMode.isAppStore else { return }
        QuickCapture.shared.onCapturedText = { [weak self] text, source in
            Task { @MainActor in
                await self?.saveQuickCapture(text, source)
            }
        }
        // Don't keep the quick-capture hotkey live while the sign-in wall is up: it would run the
        // capture pipeline and show a "Saved" notch even though saveQuickCapture correctly discards
        // the result. Re-wired (enabled) by the post-sign-in bootstrap re-run.
        QuickCapture.shared.setEnabled(quickCaptureEnabled && !requiresSignIn, keybind: quickCaptureKeybind)
    }

    /// Called once at launch (from bootstrap) so a persisted quick-capture pref is honored without
    /// waiting for the user to toggle it. Safe/no-op under the App Store sandbox.
    func activateQuickCaptureIfEnabled() {
        guard !DistributionMode.isAppStore else { return }
        refreshQuickCaptureWiring()
    }

    /// Persist a quick-captured snippet as a memory, then fire the "learned" flourish. Kept off the
    /// shared `isBusy`/status buffers (like captureFromPanel) so a fast capture doesn't spin the
    /// icon or overwrite the visible status line. The notch "captured" pill is raised by
    /// QuickCapture on the capture event itself; here we stamp lastCapturedAt and announce learning.
    func saveQuickCapture(_ text: String, _ source: String) async {
        // Respect the required-account gate: write no memory while the sign-in wall is up (covers the
        // edge where quick-capture was enabled while signed in, then the user signed back out).
        guard !requiresSignIn else { return }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        lastCapturedAt = Date()
        do {
            let title = String(trimmed.prefix(60))
            let body: [String: Any] = ["content": trimmed, "source": source, "title": title]
            _ = try await request(path: "/v1/captures", method: "POST", body: body)
            await loadInbox()
            await loadReview()
            await loadStats()
            announceLearned(count: 1)
        } catch {
            // Quick capture is a background convenience; surface the failure in the notch rather
            // than clobbering the main status line.
            NotchNotifier.shared.show(title: "Couldn't save capture", subtitle: "Try again in a moment", style: .info)
        }
    }

    func search() async {
        guard !isBusy else { return }
        let q = searchQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        if q.isEmpty {
            searchResults = []
            askAnswer = ""
            askCitations = []
            askError = nil
            hasSearched = false
            status = "Enter a search term"
            return
        }
        isBusy = true
        askError = nil
        status = "Searching..."
        defer { isBusy = false }
        do {
            let encoded = q.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? q
            let data = try await request(path: "/v1/ask?query=\(encoded)&limit=12", method: "GET")
            let answer = try JSONDecoder().decode(AskResponse.self, from: data)
            searchResults = answer.results
            askAnswer = answer.answer
            askCitations = answer.citations
            askError = nil
            hasSearched = true
            if answer.status == "conflicted" {
                status = "Found cited memory with a conflict"
            } else if answer.status == "low_confidence" {
                status = "Found related citations"
            } else if answer.status == "no_cited_evidence" || searchResults.isEmpty {
                status = "No cited memory found"
            } else {
                status = "Found \(answer.citations.count) citation\(answer.citations.count == 1 ? "" : "s")"
            }
            if hasUsableOnboardingCitation(answer.citations) {
                markCortexUsed()
            }
        } catch {
            hasSearched = true
            askAnswer = ""
            askCitations = []
            searchResults = []
            askError = CortexRecoveryText.failureStatus("Search", error: error)
            status = CortexRecoveryText.failureStatus("Search", error: error)
        }
    }

    func loadGraph() async {
        do {
            let data = try await request(path: "/v1/graph?limit=160", method: "GET")
            let graph = try JSONDecoder().decode(GraphResponse.self, from: data)
            graphNodes = graph.nodes
            graphEdges = graph.edges
            graphAnalysis = graph.analysis
        } catch {
            status = CortexRecoveryText.failureStatus("Graph", error: error)
        }
    }

    /// Drill into one entity node: the cited connected subgraph (people/projects it links to and the
    /// shared evidence). Returns nil for non-entity nodes / entities not in the graph (silent — the
    /// UI just shows no drill-down panel).
    func loadNeighborhood(_ entityID: String) async -> EntityNeighborhood? {
        guard let encoded = entityID.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) else { return nil }
        do {
            let data = try await request(path: "/v1/entity/\(encoded)/neighborhood?limit=12", method: "GET")
            return try JSONDecoder().decode(EntityNeighborhood.self, from: data)
        } catch {
            return nil
        }
    }

    func loadStats() async {
        do {
            let data = try await request(path: "/v1/stats", method: "GET")
            stats = try JSONDecoder().decode(StatsResponse.self, from: data)
            await loadMemoryQuality()
        } catch {
            status = CortexRecoveryText.failureStatus("Stats", error: error)
        }
    }

    /// Poll the job queue and expose a real, changing sync-progress fraction (done/total from
    /// GET /v1/jobs/health). While work is in flight, also refresh the "learning about you"
    /// panel (stats/profile/mirror) so the user watches memory build in real time.
    /// Best-effort: any failure leaves current progress untouched (never surfaces an error).
    func loadJobProgress() async {
        do {
            let data = try await request(path: "/v1/jobs/health", method: "GET")
            let health = try JSONDecoder().decode(JobHealthResponse.self, from: data)
            let queued = health.counts?.queued ?? 0
            let running = health.counts?.running ?? 0
            let succeeded = health.counts?.succeeded ?? 0
            let inFlight = queued + running
            if inFlight > 0 {
                let front = health.active?.first
                let detail = Self.syncDetailLabel(source: front?.source, jobType: front?.job_type)
                let progress = SyncProgress(done: succeeded, total: inFlight + succeeded, detail: detail)
                if syncProgress != progress { syncProgress = progress }
                // Actually DRAIN the queue on this poll. The bar was frozen because the 4s poll only
                // READ /v1/jobs/health while the queue was only drained on the 30-minute auto-sync
                // loop — so `succeeded` never moved between ticks and the "X of Y" sat still. Running
                // up to 100 embed jobs per tick makes the next poll read a higher `succeeded`, so the
                // bar advances live and disappears (syncProgress=nil) when queued+running hits 0. The
                // internal jobDrainInFlight guard makes this safe to call from the loop.
                _ = await drainQueuedMemoryJobs(limit: 100, automatic: true)
                await loadProfile()
                await loadMirrorInsight()
            } else if syncProgress != nil {
                syncProgress = nil
            }
        } catch {
            // best-effort; leave any existing progress as-is
        }
    }

    /// A human label for what the queue is working on right now — ONLY from the front job's source
    /// ("Notes", "ChatGPT"). Internal engine job types (embed_memory, extract_capture, …) must never
    /// surface: showing "Syncing Embed memory…" is engine jargon. When there's no real source we
    /// return nil so the bar falls back to the clean generic "Syncing your memory…". `jobType` is
    /// intentionally ignored for display (kept in the signature for callers/telemetry).
    static func syncDetailLabel(source: String?, jobType: String?) -> String? {
        let trimmedSource = (source ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedSource.isEmpty else { return nil }
        return SourceDisplayName.label(trimmedSource)
    }

    /// A lightweight repeating poll started at bootstrap. job_health is a cheap local query, and
    /// the profile/mirror refresh only fires while a sync is actually running.
    func startSyncProgressPolling() {
        guard syncProgressPollTask == nil else { return }
        syncProgressPollTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                await self?.loadJobProgress()
                // Poll fast (~1.2s) while a sync is active so the bottom Learning HUD's bar advances
                // live; fall back to a calm 4s cadence when idle to keep energy near zero.
                let active = self?.syncProgress?.active == true
                try? await Task.sleep(nanoseconds: active ? 1_200_000_000 : 4_000_000_000)
            }
        }
    }

    func loadMemoryQuality() async {
        do {
            let data = try await request(path: "/v1/memory/quality", method: "GET")
            memoryQuality = try JSONDecoder().decode(MemoryQualityResponse.self, from: data)
        } catch {
            status = CortexRecoveryText.failureStatus("Quality", error: error)
        }
    }

    static let mirrorDismissedDefaultsKey = "mirrorDismissedHeadlines.v1"

    /// Fetches the one thing Cortex learned about the user (GET /v1/mirror). The endpoint
    /// abstains with `insight: null` when it has nothing confident to say, so a nil result
    /// is normal — not an error. This degrades silently: any failure (offline backend,
    /// decode error, HTTP status) simply leaves `mirrorInsight` nil and never surfaces a
    /// message. Insights the user has already confirmed or dismissed are suppressed.
    func loadMirrorInsight() async {
        do {
            let data = try await request(path: "/v1/mirror", method: "GET")
            let insight = try JSONDecoder().decode(MirrorResponse.self, from: data).insight
            if let insight, mirrorDismissedHeadlines.contains(insight.dismissKey) {
                mirrorInsight = nil
            } else {
                mirrorInsight = insight
            }
        } catch {
            // Silent by design — the Mirror Moment is a bonus surface, never a failure point.
            mirrorInsight = nil
        }
    }

    /// Fetches the Personal Profile — "What Cortex knows about you" (GET /v1/profile). Like
    /// the Mirror Moment, this is a bonus surface: it degrades silently on any failure
    /// (offline backend, decode error, HTTP status) by leaving `profile` nil, and never
    /// surfaces an error status. The profile view abstains when there are no sections, so
    /// a nil or empty result simply renders nothing.
    func loadProfile() async {
        do {
            let data = try await request(path: "/v1/profile", method: "GET")
            profile = try JSONDecoder().decode(ProfileResponse.self, from: data)
        } catch {
            profile = nil
        }
    }

    /// "That's right" — the user confirmed the insight. Optimistically hide the card and
    /// remember the confirmation so this exact insight doesn't nag again. No network round
    /// trip is required for the UI to feel instant.
    func confirmMirrorInsight() {
        guard let insight = mirrorInsight else { return }
        rememberMirrorDismissal(insight.dismissKey)
        mirrorInsight = nil
        status = "Thanks — noted."
    }

    /// "Not quite" — the user rejected the insight. Optimistically hide the card and
    /// remember the dismissal so the same insight isn't reshown. This intentionally does
    /// NOT mutate or delete any memories (out of scope for v1); it only dismisses.
    func dismissMirrorInsight() {
        guard let insight = mirrorInsight else { return }
        rememberMirrorDismissal(insight.dismissKey)
        mirrorInsight = nil
    }

    private func rememberMirrorDismissal(_ headline: String) {
        mirrorDismissedHeadlines.insert(headline)
        UserDefaults.standard.set(Array(mirrorDismissedHeadlines), forKey: Self.mirrorDismissedDefaultsKey)
    }

    func loadReview() async {
        do {
            let data = try await request(path: "/v1/review/today", method: "GET")
            review = try JSONDecoder().decode(DailyReviewResponse.self, from: data)
        } catch {
            status = CortexRecoveryText.failureStatus("Review", error: error)
        }
    }

    func loadProductLoop() async {
        do {
            let data = try await request(path: "/v1/loop", method: "GET")
            productLoop = try JSONDecoder().decode(ProductLoopResponse.self, from: data)
        } catch {
            status = CortexRecoveryText.failureStatus("Loop", error: error)
        }
    }

    func performProductLoopAction(_ action: ProductLoopAction) {
        switch action.action {
        case "capture":
            openConnectionsPrivacy(statusMessage: "Connect notes")
        case "review":
            selectedTab = .review
            status = "Review new signals below"
        case "reuse":
            selectedTab = .ask
            searchQuery = ""
            searchResults = []
            askAnswer = ""
            askCitations = []
            hasSearched = false
            status = "Ask Cortex what it knows"
        default:
            status = "Loop complete"
        }
    }

    func openConnectionsPrivacy(statusMessage: String = "Connections and privacy") {
        // Required-account gate: the Connections sheet exposes connector management and local-API
        // controls that operate on account-gated memory, and it presents modally OVER the sign-in
        // wall. While the wall is up (e.g. opened via ⌘, / Settings), don't present it — the main
        // window's wall is where the user signs in.
        guard !requiresSignIn else {
            status = "Sign in to Doppl to manage connections."
            return
        }
        status = statusMessage
        // If the user tapped "Open Connections" from inside the onboarding sheet, we can't just
        // set showConnectionsPrivacy = true — both are sheets on the same presenter, so the new
        // one won't appear while onboarding is still up (this was the "button does nothing" bug).
        // Dismiss onboarding first and present Connections in its onDismiss; reopen onboarding
        // when Connections closes so the user returns to the flow.
        if showOnboarding {
            reopenOnboardingAfterConnections = !onboardingComplete
            pendingOpenConnectionsAfterOnboarding = true
            showOnboarding = false
            return
        }
        showConnectionsPrivacy = true
    }

    /// Called when the onboarding sheet finishes dismissing. If it was dismissed to hand off to
    /// Connections, present Connections now (after the first sheet is fully gone).
    func onboardingSheetDismissed() {
        guard pendingOpenConnectionsAfterOnboarding else { return }
        pendingOpenConnectionsAfterOnboarding = false
        showConnectionsPrivacy = true
    }

    /// Called when the Connections sheet finishes dismissing. If we interrupted onboarding to get
    /// here, return the user to onboarding (unless they finished or dismissed it meanwhile).
    func connectionsSheetDismissed() {
        let shouldReopen = reopenOnboardingAfterConnections
        reopenOnboardingAfterConnections = false
        if shouldReopen, !onboardingComplete, !onboardingDismissedForSession {
            showOnboardingAgain()
        }
    }

    func loadSettings() async {
        do {
            let data = try await request(path: "/v1/settings", method: "GET")
            appSettings = try JSONDecoder().decode(AppSettingsResponse.self, from: data)
            if shouldApplyFirstRunTrustDefaults {
                appSettings.allow_pending_in_context = false
                if await saveMemorySettingsNow(statusMessage: nil, reload: false) {
                    UserDefaults.standard.set(true, forKey: "onboardingTrustDefaultsApplied.v1")
                }
            }
        } catch {
            status = CortexRecoveryText.failureStatus("Settings", error: error)
        }
    }

    private var shouldApplyFirstRunTrustDefaults: Bool {
        !UserDefaults.standard.bool(forKey: "onboardingTrustDefaultsApplied.v1")
            && !UserDefaults.standard.bool(forKey: "onboardingComplete.v1")
    }

    func saveMemorySettings() {
        Task {
            _ = await saveMemorySettingsNow(statusMessage: "Memory settings saved", reload: true)
        }
    }

    private var settingsAutosaveTask: Task<Void, Never>?
    private var lastPersistedSettings: AppSettingsResponse?

    /// Persist privacy/AI-access toggles as soon as the user flips them. Before this, a toggle
    /// only changed local UI state until the separate Save button was clicked — closing the sheet
    /// left the server enforcing the OLD policy while the UI showed the new one.
    func scheduleSettingsAutosave() {
        settingsAutosaveTask?.cancel()
        settingsAutosaveTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 700_000_000)
            guard let self, !Task.isCancelled else { return }
            if let last = self.lastPersistedSettings, last == self.appSettings { return }
            _ = await self.saveMemorySettingsNow(statusMessage: "Privacy settings saved", reload: false)
            self.lastPersistedSettings = self.appSettings
        }
    }

    private func saveMemorySettingsNow(statusMessage: String?, reload: Bool) async -> Bool {
        do {
            var body: [String: Any] = [
                "review_new_captures": appSettings.review_new_captures,
                "allow_pending_in_context": appSettings.allow_pending_in_context,
                "context_pack_limit": appSettings.context_pack_limit,
                "allow_agent_reads": appSettings.allow_agent_reads,
                "allow_agent_writes": appSettings.allow_agent_writes,
                "allow_agent_exports": appSettings.allow_agent_exports,
                "allow_agent_maintenance": appSettings.allow_agent_maintenance,
                "allow_agent_destructive_actions": appSettings.allow_agent_destructive_actions,
                "redact_sensitive_context": appSettings.redact_sensitive_context,
                "identity_aliases": appSettings.identity_aliases ?? []
            ]
            if let policies = sourcePoliciesBody() {
                body["source_policies"] = policies
            }
            let data = try await request(path: "/v1/settings", method: "PUT", body: body)
            appSettings = try JSONDecoder().decode(AppSettingsResponse.self, from: data)
            if let statusMessage {
                status = statusMessage
            }
            if reload {
                await loadRecent()
                if !searchQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    await search()
                }
                await loadReview()
                await loadProductLoop()
                await loadStats()
                await loadTrust()
            }
            return true
        } catch {
            status = CortexRecoveryText.failureStatus("Settings save", error: error)
            return false
        }
    }

    private func sourcePoliciesBody() -> [String: [String: Any]]? {
        guard let policies = appSettings.source_policies else { return nil }
        var body: [String: [String: Any]] = [:]
        for (source, policy) in policies {
            body[source] = [
                "mode": policy.mode,
                "allow_ai_context": policy.allow_ai_context ?? (policy.mode != SourcePolicyMode.excluded.rawValue),
                "review_required": policy.review_required ?? (policy.mode == SourcePolicyMode.review.rawValue || policy.mode == SourcePolicyMode.excluded.rawValue)
            ]
        }
        return body
    }

    func currentTrustPreset() -> TrustPreset {
        TrustPreset.matching(appSettings)
    }

    func applyTrustPreset(_ preset: TrustPreset) {
        guard preset != .advanced else { return }
        preset.apply(to: &appSettings)
        saveMemorySettings()
    }

    func sourcePolicyMode(for source: String) -> SourcePolicyMode {
        guard let mode = appSettings.source_policies?[source]?.mode else { return .standard }
        return SourcePolicyMode(rawValue: mode) ?? .standard
    }

    func setSourcePolicy(source: String, mode: SourcePolicyMode) {
        var policies = appSettings.source_policies ?? [:]
        if mode == .standard {
            policies.removeValue(forKey: source)
        } else {
            policies[source] = SourcePolicySetting(
                mode: mode.rawValue,
                allow_ai_context: mode != .excluded,
                review_required: mode == .review || mode == .excluded
            )
        }
        appSettings.source_policies = policies
        saveMemorySettings()
    }

    func loadTrust() async {
        // Each fetch is independent and keeps its last-known value on failure. Previously all four
        // ran in one try/catch, so a single transient timeout or partial decode flipped the whole
        // app status to a scary "Trust failed" in the footer even though trust data was fine. Trust
        // summary / lifecycle / audit / tokens are separate surfaces — never let one drag the rest.
        if let summaryData = try? await request(path: "/v1/trust/summary", method: "GET"),
           let summary = try? JSONDecoder().decode(TrustSummaryResponse.self, from: summaryData) {
            trustSummary = summary
        }
        if let lifecycleData = try? await request(path: "/v1/privacy/lifecycle", method: "GET"),
           let lifecycle = try? JSONDecoder().decode(DataLifecycleReportResponse.self, from: lifecycleData) {
            dataLifecycleReport = lifecycle
        }
        if let auditData = try? await request(path: "/v1/audit-log?limit=80", method: "GET"),
           let audit = try? JSONDecoder().decode(AuditLogResponse.self, from: auditData) {
            auditEvents = audit.results
        }
        await loadIntegrationTokens()
        await loadSourceConnectivity()
    }

    func loadSourceConnectivity() async {
        // Each fetch is independent, and a failed fetch keeps the last-known value. One
        // transient timeout must not wipe the whole connectivity picture — that flipped the UI
        // to "nothing connected" while sources were still connected and healthy.
        if let catalogData = try? await request(path: "/v1/source-accounts/catalog", method: "GET"),
           let catalog = try? JSONDecoder().decode(SourceConnectorCatalogResponse.self, from: catalogData) {
            sourceConnectorCatalog = catalog.results
        }
        if let accountData = try? await request(path: "/v1/source-accounts", method: "GET"),
           let accounts = try? JSONDecoder().decode(SourceAccountListResponse.self, from: accountData) {
            sourceAccounts = accounts.results
            if let allAccountData = try? await request(path: "/v1/source-accounts?include_disconnected=true", method: "GET"),
               let all = try? JSONDecoder().decode(SourceAccountListResponse.self, from: allAccountData) {
                allSourceAccounts = all.results
            } else {
                allSourceAccounts = accounts.results
            }
        }
        if let cursorData = try? await request(path: "/v1/sync-cursors", method: "GET"),
           let cursors = try? JSONDecoder().decode(SyncCursorListResponse.self, from: cursorData) {
            syncCursors = cursors.results
        }
        if let deviceData = try? await request(path: "/v1/sync/devices?include_revoked=true", method: "GET"),
           let devices = try? JSONDecoder().decode(SyncDeviceListResponse.self, from: deviceData) {
            syncDevices = devices.results
            syncReceiptsByDevice = await loadSyncReceipts(for: devices.results)
        }
        if let readinessData = try? await request(path: "/v1/sources/readiness", method: "GET"),
           let readiness = try? JSONDecoder().decode(SourceReadinessResponse.self, from: readinessData) {
            sourceReadinessReport = readiness
        }
    }

    private func startConnectedSourceAutoSync(initialSync: Bool = true) {
        obsidianAutoSyncTask?.cancel()
        directConnectorAutoSyncTask?.cancel()
        // Required-account gate: never run background source syncs while the sign-in wall is up.
        // Re-invoked (unblocked) by the post-sign-in bootstrap re-run.
        guard !requiresSignIn else { return }
        guard storedObsidianVaultURL() != nil || !sourceAccounts.isEmpty || !configuredDirectConnectorIDs.isEmpty else { return }
        directConnectorAutoSyncTask = Task { [weak self] in
            if initialSync {
                await self?.runConnectedSourceAutoSyncTick()
            }
            while !Task.isCancelled {
                do {
                    try await Task.sleep(nanoseconds: 30 * 60 * 1_000_000_000)
                } catch {
                    return
                }
                await self?.runConnectedSourceAutoSyncTick()
            }
        }
    }

    private func runConnectedSourceAutoSyncTick() async {
        if storedObsidianVaultURL() != nil {
            await syncSavedObsidianVaultIfAvailable(automatic: true)
        }
        await syncDueConnectedSources(automatic: true)
        await drainQueuedMemoryJobs(automatic: true)
        if directConnectorIDsNeedingClientFallbackSync().isEmpty {
            return
        }
        await syncConfiguredDirectConnectorsIfAvailable(automatic: true)
        await drainQueuedMemoryJobs(automatic: true)
    }

    @discardableResult
    private func syncDueConnectedSources(automatic: Bool = false) async -> JobRunResponse? {
        do {
            let data = try await request(path: "/v1/sources/sync-due?limit=25", method: "POST")
            let response = try JSONDecoder().decode(JobRunResponse.self, from: data)
            await loadSourceConnectivity()
            if !automatic {
                let scheduled = response.scheduled_source_syncs?.scheduled ?? 0
                if response.processed > 0 || scheduled > 0 {
                    status = "Synced \(response.processed) connected source job\(response.processed == 1 ? "" : "s")"
                } else {
                    status = "Connected sources are up to date"
                }
            }
            return response
        } catch {
            if !automatic {
                status = CortexRecoveryText.failureStatus("Connected source", error: error)
            }
            return nil
        }
    }

    /// Menu-bar "Sync Now": run a real sync of connected sources, prepare queued jobs, then refresh
    /// Review/stats so the menu-bar count and Review tab reflect anything new.
    func syncNowFromMenu() {
        guard syncProgress?.active != true else { return }
        Task { @MainActor [weak self] in
            guard let self else { return }
            self.status = "Syncing your sources…"
            self.beginMenuBarWork()
            defer { self.endMenuBarWork(completedSync: true) }
            // Mirror the auto-sync tick so a manual "Sync Now" covers EVERY source — most importantly
            // the local notes folder (client-only path), which the backend sync-due job can't reach.
            // Previously this only ran sync-due, so the default notes-folder user saw "up to date"
            // and no new notes even after editing them.
            await self.syncSavedObsidianVaultIfAvailable(automatic: false)
            _ = await self.syncDueConnectedSources(automatic: false)
            await self.syncConfiguredDirectConnectorsIfAvailable(automatic: true)
            _ = await self.drainQueuedMemoryJobs(automatic: true)
            await self.loadInbox()
            await self.loadReview()
            await self.loadStats()
        }
    }

    @discardableResult
    private func drainQueuedMemoryJobs(limit: Int = 50, automatic: Bool = true) async -> JobRunResponse? {
        guard !jobDrainInFlight else { return nil }
        jobDrainInFlight = true
        defer { jobDrainInFlight = false }

        let boundedLimit = min(max(limit, 1), 100)
        do {
            let data = try await request(path: "/v1/jobs/run?limit=\(boundedLimit)&schedule_source_syncs=false", method: "POST")
            let response = try JSONDecoder().decode(JobRunResponse.self, from: data)
            if !automatic, response.processed > 0 {
                status = "Prepared \(response.processed) background job\(response.processed == 1 ? "" : "s") for Ask"
            }
            return response
        } catch {
            if !automatic {
                status = CortexRecoveryText.failureStatus("Prepare memory", error: error)
            }
            return nil
        }
    }

    private func directConnectorIDsNeedingClientFallbackSync() -> Set<String> {
        guard !configuredDirectConnectorIDs.isEmpty else { return [] }
        let readinessBySource = Dictionary(uniqueKeysWithValues: (sourceReadinessReport?.sources ?? []).map { ($0.source, $0) })
        return Set(configuredDirectConnectorIDs.filter { connectorID in
            guard let connector = sourceConnectorCatalog.first(where: { $0.id == connectorID }),
                  isDirectConnectorSyncWired(connector),
                  let readiness = readinessBySource[connectorID],
                  let syncPlan = readiness.sync_plan else {
                return true
            }
            if syncPlan.scheduler_supported == true {
                return false
            }
            return syncPlan.blocked_reason == "stored_sync_configuration_required"
        })
    }

    private func syncSavedObsidianVaultIfAvailable(automatic: Bool) async {
        guard let folderURL = storedObsidianVaultURL(),
              let connector = sourceConnectorCatalog.first(where: { $0.id == "obsidian" }) else {
            return
        }
        await syncLocalNotesFolder(connector, folderURL: folderURL, rememberPath: false, automatic: automatic)
    }

    /// One-tap onboarding path: distill the sample notes we ship inside the app bundle so a
    /// brand-new user can watch memory build without having to connect anything first.
    ///
    /// This is a pure bundle -> container copy (NO file picker / NSOpenPanel), so it works
    /// identically in App Store (sandboxed) and Developer-ID/DMG builds: the bundled notes are
    /// copied into a stable folder inside the app container that the sandboxed backend child can
    /// read, then synced through the existing local-notes distill path.
    func loadSampleNotes() async {
        guard !obsidianSyncInFlight else {
            status = "A sync is already running — one moment…"
            return
        }

        // 1. Locate the bundled sample notes (shipped by the build at
        //    Contents/Resources/sample-notes/*.md). If they aren't present, say so plainly.
        guard let bundledSampleNotes = Bundle.main.resourceURL?
            .appendingPathComponent("sample-notes", isDirectory: true),
              FileManager.default.fileExists(atPath: bundledSampleNotes.path) else {
            status = "Sample notes aren't available in this build. Connect a notes folder to get started."
            return
        }

        obsidianSyncInFlight = true
        isBusy = true
        beginMenuBarWork()
        var syncedSomething = false
        defer {
            obsidianSyncInFlight = false
            isBusy = false
            endMenuBarWork(completedSync: syncedSomething)
        }

        do {
            status = "Loading sample notes…"

            // 2. Copy the bundled notes into a stable, container-safe folder the backend child
            //    can read. This lives alongside the other Cortex support data and is refreshed
            //    on each run so edits to the shipped samples always win.
            let manager = FileManager.default
            // Container-safe support dir (matches BackendSupervisor.appSupportURL: <App Support>/Cortex);
            // under the App Store sandbox this resolves inside the app container, readable by the child.
            let cortexSupport = (manager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
                ?? manager.temporaryDirectory).appendingPathComponent("Cortex", isDirectory: true)
            let sampleNotesDir = cortexSupport.appendingPathComponent("sample-notes", isDirectory: true)
            if manager.fileExists(atPath: sampleNotesDir.path) {
                try? manager.removeItem(at: sampleNotesDir)
            }
            try manager.createDirectory(
                at: sampleNotesDir.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try manager.copyItem(at: bundledSampleNotes, to: sampleNotesDir)

            // 3. Distill them through the existing local-notes sync path. We drive the connector
            //    endpoint directly (rather than syncLocalNotesFolder) so every user-facing string
            //    here stays under our control and reads as "sample notes", and because the copied
            //    folder already lives in the container (no security scope / staging needed).
            let syncBody: [String: Any] = [
                "vault_path": sampleNotesDir.standardizedFileURL.path,
                "max_records": 5000,
                "processing": "sync"
            ]
            let syncData = try await request(
                path: "/v1/connectors/obsidian/sync",
                method: "POST",
                body: syncBody
            )
            let synced = try JSONDecoder().decode(ObsidianConnectorSyncResponse.self, from: syncData)
            guard synced.scan.records_found > 0, synced.scan.records_returned > 0 else {
                await loadSourceConnectivity()
                await loadTrust()
                status = "No sample notes found to load."
                return
            }
            syncedSomething = true

            // 4. Mark the sample notes as a connected local notes source so onboarding advances
            //    and the "first source" bookkeeping matches the folder-pick path.
            firstSourceAdded = true
            let sampleSourceLabel = "Sample notes"
            onboardingFirstSourceNames = Array(Set(onboardingFirstSourceNames + [sampleSourceLabel])).sorted()
            UserDefaults.standard.set(true, forKey: "onboardingFirstSourceImported.v1")
            UserDefaults.standard.set(onboardingFirstSourceNames, forKey: "onboardingFirstSourceNames.v1")

            // 5. Drain queued memory jobs and refresh every layer (profile + graph + mirror +
            //    connectivity) so the memory the samples produced shows up right away.
            await drainQueuedMemoryJobs(automatic: true)
            await loadSourceConnectivity()
            await loadTrust()
            await refreshAfterCapture()

            announceLearned(count: synced.saved + synced.queued)
            status = importSummary(synced, sourceName: "Sample notes")
        } catch {
            status = CortexRecoveryText.failureStatus("Sample notes", error: error)
        }
    }

    private func syncConfiguredDirectConnectorsIfAvailable(automatic: Bool) async {
        let configuredIDs = configuredDirectConnectorIDs.sorted()
        guard !configuredIDs.isEmpty else { return }
        for connectorID in configuredIDs {
            guard let connector = sourceConnectorCatalog.first(where: { $0.id == connectorID }),
                  isDirectConnectorSyncWired(connector),
                  let payload = storedDirectConnectorPayload(for: connectorID) else {
                continue
            }
            await syncDirectConnector(connector, payload: payload, automatic: automatic)
        }
    }

    // MARK: AI-chat exports (ChatGPT / Claude) — pick a file, drag it in, or auto-detect it.

    /// Open a file picker for a ChatGPT/Claude export (a .zip, its conversations.json, or the
    /// unzipped export folder) and import it.
    func importAIChatExport(sourceHint: String = "") {
        let panel = NSOpenPanel()
        panel.title = "Choose export file"
        panel.message = "Select your ChatGPT or Claude export — a .zip, its conversations.json, or the unzipped folder."
        panel.prompt = "Import"
        panel.canChooseFiles = true
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.allowedFileTypes = ["zip", "json", "jsonl"]
        if panel.runModal() == .OK, let url = panel.url {
            Task { await importFromPath(url.standardizedFileURL.path, sourceHint: sourceHint) }
        }
    }

    /// Import an export from a local path (POST /v1/imports). Imported content is trusted, so it
    /// becomes usable immediately; on success we mark the first-source-connected flag so onboarding
    /// can advance, and refresh memory + import history.
    func importFromPath(_ path: String, sourceHint: String = "", automatic: Bool = false) async {
        let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !importInFlight else { return }
        importInFlight = true
        if !automatic { isBusy = true }
        beginMenuBarWork()
        var importSucceeded = false
        defer {
            importInFlight = false
            if !automatic { isBusy = false }
            endMenuBarWork(completedSync: importSucceeded)
        }
        do {
            status = "Importing your chats…"
            let started = URL(fileURLWithPath: trimmed).startAccessingSecurityScopedResource()
            defer { if started { URL(fileURLWithPath: trimmed).stopAccessingSecurityScopedResource() } }
            // Under the App Store sandbox the backend child cannot read a user-picked path:
            // the dynamic security-scoped grant on `trimmed` lives only in this (parent)
            // process. Copy the picked file/folder into the app container — which the child
            // can always read — and hand the child that container path instead. Direct
            // (DeveloperID/DMG) builds keep passing the original path unchanged.
            var backendPath = trimmed
            var inboxToClean: URL?
            if DistributionMode.isAppStore {
                let staged = try stageImportIntoContainer(originalPath: trimmed)
                backendPath = staged.stagedPath
                inboxToClean = staged.inboxDir
            }
            defer {
                if let inboxToClean { try? FileManager.default.removeItem(at: inboxToClean) }
            }
            // Import ASYNC, not sync. Sync fully extracts+embeds every conversation INLINE in the
            // HTTP request — for a large Claude/ChatGPT export (thousands of conversations) that
            // blocks for minutes and reads as "never finishes." Async parses + enqueues each
            // conversation as a background job and returns fast; the queued jobs then drain via the
            // job poll with the bottom Learning HUD showing live progress. We paginate (the backend
            // caps each call at max_records) so the FULL export imports across calls, and cap the
            // loop so a pathological file can't loop forever.
            var added = 0
            var skipped = 0
            var offset = 0
            var pages = 0
            let maxPages = 40   // 40 × 2000 = up to 80k conversations, then stop (safety bound)
            while pages < maxPages {
                pages += 1
                let data = try await request(
                    path: "/v1/imports",
                    method: "POST",
                    body: ["paths": [backendPath], "source_hint": sourceHint, "processing": "async",
                           "max_records": 2000, "offset": offset]
                )
                let result = try JSONDecoder().decode(SourceImportResultLite.self, from: data)
                added += result.saved + result.queued
                skipped += result.skipped
                status = added > 0 ? "Importing your chats… \(added) so far" : "Importing your chats…"
                guard result.has_more == true, let next = result.next_offset, next > offset else { break }
                offset = next
            }
            if added > 0 {
                firstSourceAdded = true
                UserDefaults.standard.set(true, forKey: "onboardingFirstSourceImported.v1")
                let label = sourceHint.isEmpty ? "Imported chats" : sourceHint.capitalized
                onboardingFirstSourceNames = Array(Set(onboardingFirstSourceNames + [label])).sorted()
                UserDefaults.standard.set(onboardingFirstSourceNames, forKey: "onboardingFirstSourceNames.v1")
            }
            // Flip syncProgress.active on immediately (and start draining) so the bottom HUD appears
            // right away instead of after the next idle poll tick.
            await loadJobProgress()
            await refreshAfterCapture()
            await loadStats()
            await loadImportHistory()
            detectedExportSummary = nil
            if added > 0 {
                importSucceeded = true
                announceLearned(count: added)
                // The conversations are captured and queued; the bottom HUD now shows the background
                // extraction/embedding draining to completion (driven by the job-progress poll).
                status = "Imported \(added) conversation\(added == 1 ? "" : "s"). Building your memory in the background…"
            } else if skipped > 0 {
                status = "Already imported — nothing new to add."
            } else {
                status = "No conversations found in that file. Choose the export .zip or its conversations.json."
            }
        } catch {
            status = CortexRecoveryText.failureStatus("Import", error: error)
        }
    }

    /// App Store (sandboxed) helper: copy a user-picked import file/folder into the app
    /// container so the sandboxed backend child — which does not inherit this process's
    /// dynamic security-scoped grant — can read it. Returns the staged path to hand the
    /// backend plus the per-import inbox directory to delete after the import completes.
    /// The picked URL's security scope must already be active when this is called.
    private func stageImportIntoContainer(originalPath: String) throws -> (stagedPath: String, inboxDir: URL) {
        let manager = FileManager.default
        let base = manager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? manager.temporaryDirectory
        let inboxDir = base
            .appendingPathComponent("Cortex", isDirectory: true)
            .appendingPathComponent("imports-inbox", isDirectory: true)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try manager.createDirectory(at: inboxDir, withIntermediateDirectories: true)
        let source = URL(fileURLWithPath: originalPath)
        let destination = inboxDir.appendingPathComponent(source.lastPathComponent)
        // copyItem handles both a single file and a directory (recursive copy).
        try manager.copyItem(at: source, to: destination)
        return (destination.standardizedFileURL.path, inboxDir)
    }

    /// Scan Downloads / Desktop / ~/CortexImports for AI-chat exports so the app can offer a
    /// one-tap "found your ChatGPT export" import. Best-effort; silent on failure.
    func detectAvailableExports() async {
        // The detector scans Downloads/Desktop/~/CortexImports and returns raw external
        // paths the sandboxed child cannot read. Disable this whole flow in App Store mode;
        // users import via the explicit file picker (which stages into the container).
        if DistributionMode.isAppStore {
            detectedExportPaths = []
            detectedExportSummary = nil
            return
        }
        do {
            let data = try await request(path: "/v1/imports/detect", method: "GET")
            let result = try JSONDecoder().decode(ExportDetectResponse.self, from: data)
            detectedExportPaths = result.candidates.map { $0.path }
            if let first = result.candidates.first {
                let total = result.candidates.reduce(0) { $0 + $1.records_found }
                let svc = first.service.replacingOccurrences(of: "chatgpt", with: "ChatGPT").capitalized
                detectedExportSummary = "Found a \(svc) export (\(total) conversation\(total == 1 ? "" : "s")) in your Downloads."
            } else {
                detectedExportSummary = nil
            }
        } catch {
            detectedExportSummary = nil
        }
    }

    /// Import every export the detector found (the "Import found export" one-tap action).
    func importDetectedExports() {
        // Detected paths live outside the sandbox container; the child cannot read them and
        // there is no live security scope to stage them from. App Store users import via the
        // explicit picker instead.
        if DistributionMode.isAppStore { return }
        let paths = detectedExportPaths
        guard !paths.isEmpty else { return }
        Task {
            for path in paths { await importFromPath(path, sourceHint: "") }
            detectedExportPaths = []
        }
    }

    func connectLocalNotesFolder(_ connector: SourceConnectorCatalogItem, chooseNew: Bool = false) {
        guard connector.id == "obsidian" else {
            status = "Open Connections & Privacy to connect \(connector.name)"
            return
        }

        if !chooseNew, let storedURL = storedObsidianVaultURL() {
            var isDirectory: ObjCBool = false
            if FileManager.default.fileExists(atPath: storedURL.path, isDirectory: &isDirectory),
               isDirectory.boolValue {
                Task { await syncLocalNotesFolder(connector, folderURL: storedURL, rememberPath: false) }
                return
            }
            // The remembered folder moved, was renamed, or is on an unmounted disk. Don't silently
            // no-op — say so and fall through to the picker so the user can re-choose in place.
            status = "Your notes folder moved or is unavailable — choose it again."
            connectorLastMessages[connector.id] = "The connected notes folder could not be found. Choose the folder again to resume syncing."
        }

        let panel = NSOpenPanel()
        panel.title = "Select Notes Folder"
        panel.message = "Allow Cortex to keep this notes folder synced into Review."
        panel.prompt = "Use This Folder"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false
        if panel.runModal() == .OK, let url = panel.url {
            Task { await syncLocalNotesFolder(connector, folderURL: url, rememberPath: true) }
        }
    }

    /// Disconnects the primary notes folder. This is non-destructive: already
    /// synced local memory is kept and stays available to Ask. It only stops
    /// future automatic syncing by forgetting the stored folder and disconnecting
    /// the backend source account, so you can reconnect later.
    func disconnectObsidianNotes() {
        obsidianVaultPath = ""
        UserDefaults.standard.removeObject(forKey: Self.obsidianVaultPathDefaultsKey)
        UserDefaults.standard.removeObject(forKey: Self.obsidianVaultBookmarkDefaultsKey)
        Task {
            if let account = knownSourceAccounts.first(where: { $0.source == "obsidian" && $0.disconnected_at == nil }) {
                do {
                    let accountID = account.id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? account.id
                    _ = try await request(path: "/v1/source-accounts/\(accountID)/disconnect", method: "POST")
                } catch {
                    // Folder is already forgotten locally; surface the backend issue
                    // but keep the disconnect in effect.
                    status = CortexRecoveryText.failureStatus("Disconnect notes", error: error)
                }
            }
            await loadSourceConnectivity()
            status = "Notes disconnected. Already synced memory is kept."
        }
    }

    func connectCalendarFile(_ connector: SourceConnectorCatalogItem) {
        guard connector.id == "calendar" else { return }
        let panel = NSOpenPanel()
        panel.title = "Select Calendar File"
        panel.message = "Allow Cortex to sync this read-only calendar export into Review."
        panel.prompt = "Sync Calendar"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false
        if let calendarType = UTType(filenameExtension: "ics") {
            panel.allowedContentTypes = [calendarType]
        }
        if panel.runModal() == .OK, let url = panel.url {
            Task {
                await syncDirectConnector(
                    connector,
                    payload: [
                        "ics_path": url.standardizedFileURL.path,
                        "processing": "sync",
                        "max_records": 500
                    ],
                    rememberPayload: true
                )
            }
        }
    }

    func syncZoteroLocal(_ connector: SourceConnectorCatalogItem) {
        guard connector.id == "zotero" else { return }
        Task {
            await syncDirectConnector(
                connector,
                payload: [
                    "processing": "sync",
                    "max_records": 500
                ]
            )
        }
    }

    func startManagedOAuthConnector(_ connector: SourceConnectorCatalogItem) {
        guard let setup = connector.connectionSetup, setup.supportsManagedOAuth else {
            status = "\(connector.name) sign-in is not available in this build"
            return
        }
        guard !connectorOAuthStartingIDs.contains(connector.id) else {
            status = "\(connector.name) sign-in is already open"
            return
        }
        Task {
            await startManagedOAuthConnectorNow(connector, setup: setup)
        }
    }

    func managedOAuthIsConfigured(_ connector: SourceConnectorCatalogItem) -> Bool {
        guard let setup = connector.connectionSetup, setup.supportsManagedOAuth else {
            return false
        }
        let provider = setup.oauth_provider?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() ?? ""
        guard !configuredManagedOAuthClientID(provider: provider).isEmpty else {
            return false
        }
        if ["notion", "microsoft"].contains(provider) {
            return !configuredManagedOAuthClientSecret(provider: provider).isEmpty
        }
        return true
    }

    func managedOAuthConfigurationMessage(_ connector: SourceConnectorCatalogItem) -> String? {
        guard let setup = connector.connectionSetup, setup.supportsManagedOAuth else {
            return nil
        }
        let provider = setup.oauth_provider?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() ?? ""
        guard !managedOAuthIsConfigured(connector) else {
            return nil
        }
        return "\(managedOAuthProviderDisplayName(provider)) sign-in is not configured for this Cortex build yet."
    }

    private func startManagedOAuthConnectorNow(_ connector: SourceConnectorCatalogItem, setup: SourceConnectorConnectionSetup) async {
        guard let startEndpoint = setup.oauth_start_endpoint?.trimmingCharacters(in: .whitespacesAndNewlines),
              !startEndpoint.isEmpty else {
            status = "\(connector.name) sign-in is not available in this build"
            return
        }
        let provider = setup.oauth_provider?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() ?? ""
        let providerName = managedOAuthProviderDisplayName(provider)
        let clientID = configuredManagedOAuthClientID(provider: provider)
        guard !clientID.isEmpty else {
            status = "\(connector.name) sign-in needs a \(providerName) OAuth client ID in this build"
            connectorLastMessages[connector.id] = "\(providerName) sign-in is not configured for this Cortex build yet."
            return
        }
        let clientSecret = configuredManagedOAuthClientSecret(provider: provider)
        if ["notion", "microsoft"].contains(provider) && clientSecret.isEmpty {
            status = "\(connector.name) sign-in needs a \(providerName) OAuth client secret in this build"
            connectorLastMessages[connector.id] = "\(providerName) sign-in is not configured for this Cortex build yet."
            return
        }
        let pkce: (verifier: String, challenge: String)
        if provider == "google" {
            do {
                pkce = try Self.googleOAuthPKCEPair()
            } catch {
                status = CortexRecoveryText.failureStatus("\(connector.name) sign-in", error: error)
                return
            }
        } else {
            pkce = ("", "")
        }
        connectorOAuthStartingIDs.insert(connector.id)
        defer {
            connectorOAuthStartingIDs.remove(connector.id)
        }

        do {
            status = "Opening \(connector.name) sign-in..."
            var body: [String: Any] = [
                "source": connector.id,
                "client_id": clientID
            ]
            if !clientSecret.isEmpty {
                body["client_secret"] = clientSecret
            }
            if !pkce.verifier.isEmpty {
                body["code_verifier"] = pkce.verifier
                body["code_challenge"] = pkce.challenge
                body["code_challenge_method"] = "S256"
            }
            let data = try await request(
                path: startEndpoint,
                method: "POST",
                body: body
            )
            let started = try JSONDecoder().decode(GoogleOAuthStartResponse.self, from: data)
            // Only open an http(s) URL from the server-controlled authorization_url (never a
            // file://, custom-scheme, or app URL a malicious/MITM response could inject).
            guard let authURL = URL(string: started.authorization_url),
                  let scheme = authURL.scheme?.lowercased(), scheme == "https" || scheme == "http" else {
                status = "\(connector.name) sign-in returned an invalid link"
                return
            }
            NSWorkspace.shared.open(authURL)
            connectorLastMessages[connector.id] = "Finish sign-in in your browser. Cortex will start the first sync automatically."
            status = "Finish \(connector.name) sign-in in your browser"
            await waitForManagedOAuthCompletion(connector)
        } catch {
            let message = CortexRecoveryText.failureStatus("\(connector.name) sign-in", error: error)
            connectorLastMessages[connector.id] = message
            status = message
        }
    }

    private func managedOAuthProviderDisplayName(_ provider: String) -> String {
        switch provider {
        case "google": return "Google"
        case "microsoft": return "Microsoft"
        case "notion": return "Notion"
        default: return provider.isEmpty ? "service" : provider.capitalized
        }
    }

    private func configuredManagedOAuthClientID(provider: String) -> String {
        let envKey = "CORTEX_\(provider.replacingOccurrences(of: "-", with: "_").uppercased())_OAUTH_CLIENT_ID"
        let envValue = ProcessInfo.processInfo.environment[envKey]?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !envValue.isEmpty {
            return envValue
        }
        return (Bundle.main.object(forInfoDictionaryKey: managedOAuthClientIDInfoKey(provider: provider)) as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    private func configuredManagedOAuthClientSecret(provider: String) -> String {
        let envKey = "CORTEX_\(provider.replacingOccurrences(of: "-", with: "_").uppercased())_OAUTH_CLIENT_SECRET"
        let envValue = ProcessInfo.processInfo.environment[envKey]?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !envValue.isEmpty {
            return envValue
        }
        return (Bundle.main.object(forInfoDictionaryKey: managedOAuthClientSecretInfoKey(provider: provider)) as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    private func managedOAuthClientIDInfoKey(provider: String) -> String {
        switch provider {
        case "microsoft": return "CortexMicrosoftOAuthClientID"
        case "notion": return "CortexNotionOAuthClientID"
        default: return "CortexGoogleOAuthClientID"
        }
    }

    private func managedOAuthClientSecretInfoKey(provider: String) -> String {
        switch provider {
        case "microsoft": return "CortexMicrosoftOAuthClientSecret"
        case "notion": return "CortexNotionOAuthClientSecret"
        default: return "CortexGoogleOAuthClientSecret"
        }
    }

    private static func googleOAuthPKCEPair() throws -> (verifier: String, challenge: String) {
        var random = [UInt8](repeating: 0, count: 32)
        let status = random.withUnsafeMutableBytes { buffer in
            SecRandomCopyBytes(kSecRandomDefault, buffer.count, buffer.baseAddress!)
        }
        guard status == errSecSuccess else {
            throw NSError(domain: "CortexOAuth", code: Int(status), userInfo: [NSLocalizedDescriptionKey: "Could not create a secure OAuth challenge"])
        }
        let verifier = base64URLEncoded(Data(random))
        let challenge = base64URLEncoded(Data(SHA256.hash(data: Data(verifier.utf8))))
        return (verifier, challenge)
    }

    private static func base64URLEncoded(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    private func waitForManagedOAuthCompletion(_ connector: SourceConnectorCatalogItem) async {
        // Sign-in with 2FA/account-picker routinely takes minutes; poll for 3 minutes with
        // visible progress so the user is never staring at a silent, seemingly-hung app.
        for tick in 0..<36 {
            do {
                try await Task.sleep(nanoseconds: 5 * 1_000_000_000)
            } catch {
                return
            }
            await loadSourceConnectivity()
            if sourceAccount(connector) != nil {
                firstSourceAdded = true
                onboardingFirstSourceNames = Array(Set(onboardingFirstSourceNames + [connector.name])).sorted()
                UserDefaults.standard.set(true, forKey: "onboardingFirstSourceImported.v1")
                UserDefaults.standard.set(onboardingFirstSourceNames, forKey: "onboardingFirstSourceNames.v1")
                _ = await syncDueConnectedSources(automatic: true)
                await drainQueuedMemoryJobs(automatic: true)
                await refreshAfterCapture()
                connectorLastMessages[connector.id] = "\(connector.name) connected. First sync is starting."
                status = "\(connector.name) connected"
                return
            }
            if tick > 0, tick % 6 == 0 {
                status = "Waiting for \(connector.name) sign-in in your browser (\(tick * 5)s)..."
            }
        }
        connectorLastMessages[connector.id] = "Sign-in hasn't completed yet. Finish in the browser - Cortex connects automatically once it does. You can also click Connect again to re-check."
        status = "\(connector.name) sign-in not finished yet"
    }

    // MARK: - GitHub device-flow sign-in (RFC 8628)
    //
    // Secretless "Sign in with GitHub": the app shows a short user code, opens github.com/login/device
    // in the browser, then polls the backend until GitHub returns a token. The token then feeds the
    // exact same discover→sync path the pasted-token flow uses, so nothing downstream is special-cased.

    func startGitHubDeviceFlow(_ connector: SourceConnectorCatalogItem) {
        guard let setup = connector.connectionSetup, setup.supportsDeviceFlow else {
            status = "\(connector.name) sign-in is not available in this build"
            return
        }
        guard githubDeviceFlow == nil else {
            status = "\(connector.name) sign-in is already open"
            return
        }
        guard !connectorOAuthStartingIDs.contains(connector.id) else {
            status = "\(connector.name) sign-in is already starting"
            return
        }
        Task { await runGitHubDeviceFlow(connector, setup: setup) }
    }

    /// Pair a browser extension: mint a fresh read-only token via /v1/pair and surface the
    /// connection info for the user to paste into the extension. Read-only by default; the user
    /// can widen scope later. Phase 9 of the outbound plan.
    func pairBrowserExtension(label: String = "Browser extension") {
        guard !browserExtensionPairingInFlight else { return }
        Task {
            browserExtensionPairingInFlight = true
            defer { browserExtensionPairingInFlight = false }
            do {
                status = "Pairing browser extension..."
                let data = try await request(path: "/v1/pair", method: "POST", body: ["label": label, "surface": "chat"])
                let pairing = try JSONDecoder().decode(BrowserExtensionPairing.self, from: data)
                browserExtensionPairing = pairing
                status = "Browser extension paired — token copied to clipboard."
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(pairing.token, forType: .string)
                let alert = NSAlert()
                alert.messageText = "Browser extension paired"
                alert.informativeText = "A read-only access token has been copied to your clipboard.\n\nIn the Cortex extension's Options:\n  • Base URL: \(pairing.base_url)\n  • Token: paste from clipboard\n\nThen click ◆ Cortex on a supported site to inject your cited context."
                alert.addButton(withTitle: "Done")
                alert.runModal()
            } catch {
                status = CortexRecoveryText.failureStatus("Browser extension pairing", error: error)
            }
        }
    }

    /// Copy universal-API connection details for developers: mints a read-only token and puts the
    /// base URL, tool-schema endpoint, a runnable curl example, and the MCP endpoint on the
    /// clipboard so any function-calling app or the Cortex SDK can connect. Phase 9 connection UX.
    func copyUniversalAPIConnectionInfo() {
        Task {
            do {
                status = "Preparing API connection info..."
                let data = try await request(path: "/v1/pair", method: "POST", body: ["label": "Universal API client", "surface": "full"])
                let pairing = try JSONDecoder().decode(BrowserExtensionPairing.self, from: data)
                let base = pairing.base_url
                let info = """
                Cortex universal API — connect any app to your memory (read-only):

                Base URL: \(base)
                Token:    \(pairing.token)

                Tool schemas (OpenAI / Anthropic / OpenAPI):
                  GET \(base)/v1/tools/schema?format=openai

                Call a tool:
                  curl -s \(base)/v1/tools/call \\
                    -H "Authorization: Bearer \(pairing.token)" \\
                    -H "Content-Type: application/json" \\
                    -d '{"name":"ask_memory","arguments":{"query":"what did I decide about X?"}}'

                MCP endpoint: \(base)/mcp
                Python SDK:   CortexClient(base_url="\(base)", token="…")
                """
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(info, forType: .string)
                let alert = NSAlert()
                alert.messageText = "API connection info copied"
                alert.informativeText = "A read-only token plus connection details (base URL, tool-schema endpoint, a curl example, and the MCP endpoint) were copied to your clipboard. Paste them into your app, the Cortex SDK, or an MCP client."
                alert.addButton(withTitle: "Done")
                alert.runModal()
                status = "API connection info copied to clipboard."
            } catch {
                status = CortexRecoveryText.failureStatus("API connection info", error: error)
            }
        }
    }

    func cancelGitHubDeviceFlow() {
        // Clearing the prompt changes the tracked id, which the poll loop checks each tick to exit.
        if let connectorID = githubDeviceFlow?.connectorID {
            connectorLastMessages[connectorID] = "Sign-in cancelled. Click Sign in with GitHub to try again."
        }
        githubDeviceFlow = nil
    }

    private func runGitHubDeviceFlow(_ connector: SourceConnectorCatalogItem, setup: SourceConnectorConnectionSetup) async {
        connectorOAuthStartingIDs.insert(connector.id)
        defer { connectorOAuthStartingIDs.remove(connector.id) }

        let started: GitHubDeviceStartResponse
        do {
            status = "Starting \(connector.name) sign-in..."
            let startData = try await request(
                path: setup.deviceFlowStartEndpoint,
                method: "POST",
                body: ["source": connector.id]
            )
            started = try JSONDecoder().decode(GitHubDeviceStartResponse.self, from: startData)
        } catch {
            let message = CortexRecoveryText.failureStatus("\(connector.name) sign-in", error: error)
            connectorLastMessages[connector.id] = message
            status = message
            return
        }

        let prompt = GitHubDeviceFlowPrompt(
            connectorID: connector.id,
            connectorName: connector.name,
            userCode: started.user_code,
            verificationURI: started.verification_uri,
            message: "Enter this code on the GitHub page that just opened."
        )
        githubDeviceFlow = prompt
        let promptID = prompt.id
        if let url = prompt.verificationURL {
            NSWorkspace.shared.open(url)
        }
        connectorLastMessages[connector.id] = "Finish sign-in in your browser. Cortex starts the first sync automatically."
        status = "Finish \(connector.name) sign-in in your browser"

        var interval = max(started.interval, 1)
        var elapsed = 0
        let expiresIn = max(started.expires_in, interval)

        while githubDeviceFlow?.id == promptID {
            do {
                try await Task.sleep(nanoseconds: UInt64(interval) * 1_000_000_000)
            } catch {
                return
            }
            // Cancelled (or superseded) while we slept.
            guard githubDeviceFlow?.id == promptID else { return }
            elapsed += interval
            if elapsed >= expiresIn {
                failGitHubDeviceFlow(connector, promptID: promptID, message: "The sign-in code expired. Click Sign in with GitHub to get a new one.")
                return
            }

            let poll: GitHubDevicePollResponse
            do {
                let pollData = try await request(
                    path: setup.deviceFlowPollEndpoint,
                    method: "POST",
                    body: ["source": connector.id, "device_code": started.device_code]
                )
                poll = try JSONDecoder().decode(GitHubDevicePollResponse.self, from: pollData)
            } catch {
                // A transient network hiccup shouldn't kill the flow — keep polling until expiry.
                continue
            }
            guard githubDeviceFlow?.id == promptID else { return }

            switch poll.status {
            case "ok":
                let token = (poll.access_token ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                guard !token.isEmpty else {
                    failGitHubDeviceFlow(connector, promptID: promptID, message: "GitHub sign-in did not return a token. Please try again.")
                    return
                }
                await completeGitHubDeviceFlow(connector, token: token, promptID: promptID)
                return
            case "authorization_pending":
                continue
            case "slow_down":
                interval += 5
                continue
            case "expired_token":
                failGitHubDeviceFlow(connector, promptID: promptID, message: "The sign-in code expired. Click Sign in with GitHub to get a new one.")
                return
            case "access_denied":
                failGitHubDeviceFlow(connector, promptID: promptID, message: "GitHub sign-in was cancelled.")
                return
            default:
                failGitHubDeviceFlow(connector, promptID: promptID, message: poll.detail ?? "GitHub sign-in failed. Please try again.")
                return
            }
        }
    }

    private func failGitHubDeviceFlow(_ connector: SourceConnectorCatalogItem, promptID: UUID, message: String) {
        connectorLastMessages[connector.id] = message
        status = message
        if githubDeviceFlow?.id == promptID {
            githubDeviceFlow?.phase = .failed
            githubDeviceFlow?.message = message
        }
    }

    private func completeGitHubDeviceFlow(_ connector: SourceConnectorCatalogItem, token: String, promptID: UUID) async {
        if githubDeviceFlow?.id == promptID {
            githubDeviceFlow?.phase = .syncing
            githubDeviceFlow?.message = "Signed in. Importing your GitHub activity..."
        }
        status = "\(connector.name) signed in - importing your activity..."

        // Discover the account's repositories so the first sync has concrete targets. If discovery
        // fails or the account has none, we still attempt the sync (the backend handles an empty list).
        var repositories: [String] = []
        if let discoverEndpoint = connector.connectionSetup?.discovery_endpoint?
            .trimmingCharacters(in: .whitespacesAndNewlines), !discoverEndpoint.isEmpty {
            do {
                let data = try await request(path: discoverEndpoint, method: "POST", body: ["token": token, "limit": 100])
                if let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let rows = root["repositories"] as? [[String: Any]] {
                    repositories = rows.compactMap { row in
                        remoteOptionString(row["sync_value"]) ?? remoteOptionString(row["full_name"]) ?? remoteOptionString(row["name"])
                    }
                    if repositories.count > 25 {
                        repositories = Array(repositories.prefix(25))
                    }
                }
            } catch {
                // Non-fatal: fall through to a token-only sync.
            }
        }

        var payload: [String: Any] = ["token": token]
        if !repositories.isEmpty {
            payload["repositories"] = repositories
        }
        await syncDirectConnector(connector, payload: payload, rememberPayload: true)

        if githubDeviceFlow?.id == promptID {
            githubDeviceFlow?.phase = .done
            githubDeviceFlow?.message = repositories.isEmpty
                ? "Signed in. Cortex is building your memory."
                : "Signed in. Importing \(repositories.count) repositor\(repositories.count == 1 ? "y" : "ies")."
            // Let the success state show briefly, then dismiss the sheet if it's still ours.
            let doneID = promptID
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: 2_600_000_000)
                await MainActor.run {
                    if self?.githubDeviceFlow?.id == doneID {
                        self?.githubDeviceFlow = nil
                    }
                }
            }
        }
    }

    func syncStoredDirectConnector(_ connector: SourceConnectorCatalogItem) {
        guard let payload = storedDirectConnectorPayload(for: connector.id) else {
            status = "Set up \(connector.name) before syncing again"
            return
        }
        Task {
            await syncDirectConnector(connector, payload: payload)
        }
    }

    func syncConnectedSourceConnector(_ connector: SourceConnectorCatalogItem) {
        guard !connectorSyncingIDs.contains(connector.id) else {
            status = "\(connector.name) sync is already running"
            return
        }
        Task {
            connectorSyncingIDs.insert(connector.id)
            defer { connectorSyncingIDs.remove(connector.id) }
            status = "Checking \(connector.name) for new memory..."
            _ = await syncDueConnectedSources(automatic: false)
            await drainQueuedMemoryJobs(automatic: true)
            await loadSourceConnectivity()
            await refreshAfterCapture()
            if sourceAccount(connector) != nil {
                connectorLastMessages[connector.id] = "\(connector.name) is connected and ready for cited Ask."
            }
        }
    }

    func hasStoredDirectConnectorConfig(_ connector: SourceConnectorCatalogItem) -> Bool {
        configuredDirectConnectorIDs.contains(connector.id) || storedDirectConnectorPayload(for: connector.id) != nil
    }

    func pauseDirectConnectorSync(_ connector: SourceConnectorCatalogItem) {
        Task { await setDirectConnectorPaused(connector, paused: true) }
    }

    func resumeDirectConnectorSync(_ connector: SourceConnectorCatalogItem) {
        Task { await setDirectConnectorPaused(connector, paused: false) }
    }

    func forgetDirectConnectorConfig(_ connector: SourceConnectorCatalogItem) {
        CortexCredentialStore.removeSecret(forKey: Self.directConnectorConfigSecretKey(for: connector.id))
        connectorLastMessages[connector.id] = nil
        refreshStoredConnectorConfigState()
        startConnectedSourceAutoSync(initialSync: false)
        status = "\(connector.name) sync paused. Local memory already synced from this source is kept."
    }

    private func setDirectConnectorPaused(_ connector: SourceConnectorCatalogItem, paused: Bool) async {
        let account = paused ? sourceAccount(connector) : disconnectedSourceAccount(connector)
        guard let account else {
            if paused {
                // No active backend account to disconnect, but pausing must stay
                // non-destructive: keep the saved connection so Resume still works.
                startConnectedSourceAutoSync(initialSync: false)
                connectorLastMessages[connector.id] = "\(connector.name) sync paused. Synced local memory and the saved connection are retained, so you can resume without reconnecting."
                status = "\(connector.name) sync paused. Local memory already synced from this source is kept."
            } else {
                status = "Reconnect \(connector.name) to resume sync"
            }
            return
        }
        isBusy = true
        defer { isBusy = false }
        do {
            let accountID = account.id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? account.id
            let action = paused ? "disconnect" : "resume"
            _ = try await request(path: "/v1/source-accounts/\(accountID)/\(action)", method: "POST")
            if paused {
                // Pause is non-destructive: keep the stored credential so Resume works
                // without asking the user to reconnect and re-enter their token/secret.
                connectorLastMessages[connector.id] = "\(connector.name) sync paused. Synced local memory and the saved connection are retained, so you can resume without reconnecting."
                status = "\(connector.name) sync paused. Local memory already synced from this source is kept."
            } else {
                connectorLastMessages[connector.id] = "\(connector.name) sync resumed."
                status = "\(connector.name) sync resumed"
            }
            refreshStoredConnectorConfigState()
            await loadSourceConnectivity()
            startConnectedSourceAutoSync(initialSync: !paused)
        } catch {
            status = CortexRecoveryText.failureStatus(paused ? "\(connector.name) pause" : "\(connector.name) resume", error: error)
        }
    }

    func syncDirectConnector(_ connector: SourceConnectorCatalogItem, payload: [String: Any], rememberPayload: Bool = false, automatic: Bool = false) async {
        guard isDirectConnectorSyncWired(connector) else {
            if !automatic {
                status = "\(connector.name) is not wired for direct sync yet"
            }
            return
        }
        guard !connectorSyncingIDs.contains(connector.id) else {
            if !automatic {
                status = "\(connector.name) sync is already running"
            }
            return
        }

        connectorSyncingIDs.insert(connector.id)
        if !automatic {
            isBusy = true
        }
        defer {
            connectorSyncingIDs.remove(connector.id)
            if !automatic {
                isBusy = false
            }
        }

        do {
            if !automatic {
                status = "Syncing \(connector.name)..."
            }
            var requestBody = payload
            if requestBody["processing"] == nil {
                requestBody["processing"] = connector.connectionSetup?.default_processing ?? "sync"
            }
            if requestBody["max_records"] == nil, let defaultMaxRecords = connector.connectionSetup?.default_max_records {
                requestBody["max_records"] = defaultMaxRecords
            }
            if requestBody["cursor_name"] == nil, let defaultCursorName = connector.connectionSetup?.default_cursor_name {
                requestBody["cursor_name"] = defaultCursorName
            }
            let endpoint = connector.connectionSetup?.endpoint?.trimmingCharacters(in: .whitespacesAndNewlines)
            let syncPath = endpoint?.isEmpty == false ? endpoint! : "/v1/connectors/\(connector.id)/sync"
            let syncData = try await request(
                path: syncPath,
                method: "POST",
                body: requestBody
            )
            let synced = try JSONDecoder().decode(SourceAccountSyncResponse.self, from: syncData)
            if rememberPayload {
                saveDirectConnectorPayload(payload, for: connector.id)
            }
            firstSourceAdded = true
            onboardingFirstSourceNames = Array(Set(onboardingFirstSourceNames + [connector.name])).sorted()
            UserDefaults.standard.set(true, forKey: "onboardingFirstSourceImported.v1")
            UserDefaults.standard.set(onboardingFirstSourceNames, forKey: "onboardingFirstSourceNames.v1")
            await drainQueuedMemoryJobs(automatic: true)
            await loadSourceConnectivity()
            if !automatic {
                await loadTrust()
            }
            await refreshAfterCapture()

            let message = directConnectorSyncMessage(connector: connector, synced: synced)
            connectorLastMessages[connector.id] = message
            if !automatic {
                status = message
            }
        } catch {
            let message = CortexRecoveryText.failureStatus("\(connector.name) sync", error: error)
            connectorLastMessages[connector.id] = message
            if !automatic {
                status = message
            }
        }
    }

    private func directConnectorSyncMessage(connector: SourceConnectorCatalogItem, synced: SourceAccountSyncResponse) -> String {
        let changed = synced.saved + synced.queued
        let archived = synced.archived_missing ?? 0
        let base: String
        if changed > 0 {
            base = "\(connector.name) synced \(changed) item\(changed == 1 ? "" : "s") into Review"
        } else if synced.skipped > 0 || synced.received > 0 {
            base = "\(connector.name) already up to date"
        } else {
            base = "\(connector.name) sync finished"
        }

        if archived > 0 {
            return "\(base). Archived \(archived) stale item\(archived == 1 ? "" : "s") from Ask."
        }
        if synced.archive_missing_suppressed == true, let decision = synced.archive_missing_decision {
            return "\(base). Kept older memory because \(archiveSuppressionReason(decision))."
        }
        return base
    }

    private func archiveSuppressionReason(_ decision: SourceArchiveMissingDecision) -> String {
        switch decision.reason {
        case "connector_errors":
            return "sync was incomplete"
        case "pagination_incomplete":
            return "more source pages remain"
        case "record_cap_reached":
            if let maxRecords = decision.max_records, maxRecords > 0 {
                return "the sync reached \(maxRecords) items"
            }
            return "the sync reached its item limit"
        case "truncated":
            return "the source scan was truncated"
        default:
            return "the latest source view was incomplete"
        }
    }

    func discoverDirectConnectorOptions(
        _ connector: SourceConnectorCatalogItem,
        field: SourceConnectorSetupField,
        payload: [String: Any]
    ) async throws -> [SourceConnectorDiscoveredOption] {
        guard isDirectConnectorSyncWired(connector) else {
            return []
        }
        let endpoint = (
            field.options_endpoint ?? connector.connectionSetup?.discovery_endpoint ?? ""
        ).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !endpoint.isEmpty else {
            return []
        }

        var requestBody = payload
        if requestBody["limit"] == nil {
            requestBody["limit"] = min(max(field.max_items ?? 25, 1), 100)
        }

        let data = try await request(path: endpoint, method: "POST", body: requestBody)
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return []
        }

        let targetKey = connector.connectionSetup?.discovery_target_field?.trimmingCharacters(in: .whitespacesAndNewlines)
        let rows = remoteOptionRows(root, key: targetKey)
            ?? remoteOptionRows(root, key: field.name)
            ?? remoteOptionRows(root, key: "results")
            ?? []
        let labelKey = (field.option_label_key ?? "label").trimmingCharacters(in: .whitespacesAndNewlines)
        let valueKey = (field.option_value_key ?? "sync_value").trimmingCharacters(in: .whitespacesAndNewlines)
        let maxItems = min(max(field.max_items ?? rows.count, 1), rows.count)

        return rows.prefix(maxItems).compactMap { row in
            let value = remoteOptionString(row[valueKey])
                ?? remoteOptionString(row["sync_value"])
                ?? remoteOptionString(row["id"])
                ?? remoteOptionString(row["full_name"])
                ?? remoteOptionString(row["name"])
            guard let value, !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                return nil
            }
            let label = remoteOptionString(row[labelKey])
                ?? remoteOptionString(row["label"])
                ?? remoteOptionString(row["full_name"])
                ?? remoteOptionString(row["name"])
                ?? value
            return SourceConnectorDiscoveredOption(
                id: "\(field.name):\(value)",
                label: label,
                value: value,
                detail: remoteOptionDetail(row)
            )
        }
    }

    private func remoteOptionRows(_ root: [String: Any], key: String?) -> [[String: Any]]? {
        guard let key, !key.isEmpty else { return nil }
        return root[key] as? [[String: Any]]
    }

    private func remoteOptionString(_ value: Any?) -> String? {
        if let string = value as? String {
            let trimmed = string.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        }
        if let int = value as? Int {
            return String(int)
        }
        if let double = value as? Double {
            return String(double)
        }
        if let bool = value as? Bool {
            return bool ? "true" : "false"
        }
        return nil
    }

    private func remoteOptionDetail(_ row: [String: Any]) -> String? {
        var parts: [String] = []
        if let owner = remoteOptionString(row["owner"]) {
            parts.append(owner)
        }
        if let topic = remoteOptionString(row["topic"]) ?? remoteOptionString(row["purpose"]) {
            parts.append(topic)
        }
        if row["private"] as? Bool == true || row["is_private"] as? Bool == true {
            parts.append("Private")
        }
        if row["archived"] as? Bool == true || row["is_archived"] as? Bool == true {
            parts.append("Archived")
        }
        if let members = remoteOptionString(row["num_members"]) {
            parts.append("\(members) members")
        }
        if let updated = remoteOptionString(row["updated_at"]) ?? remoteOptionString(row["pushed_at"]) {
            parts.append("Updated \(String(updated.prefix(10)))")
        }
        let detail = parts
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .prefix(3)
            .joined(separator: " · ")
        return detail.isEmpty ? nil : detail
    }

    private static func directConnectorConfigSecretKey(for connectorID: String) -> String {
        directConnectorConfigSecretPrefix + connectorID
    }

    private func refreshStoredConnectorConfigState() {
        configuredDirectConnectorIDs = Set(Self.directConnectorSyncIDs.filter { storedDirectConnectorPayload(for: $0) != nil })
    }

    func directConnectorSortRank(_ connectorID: String) -> Int {
        Self.directConnectorSyncOrder.firstIndex(of: connectorID) ?? Self.directConnectorSyncOrder.count
    }

    func readiness(for connector: SourceConnectorCatalogItem) -> SourceReadinessItem? {
        sourceReadinessReport?.sources.first { source in
            source.source == connector.id
                || (source.source_ids ?? []).contains(connector.id)
                || (connector.source_ids ?? []).contains(source.source)
        }
    }

    func isDirectConnectorSyncWired(_ connector: SourceConnectorCatalogItem) -> Bool {
        guard Self.directConnectorSyncIDs.contains(connector.id), connector.id != "obsidian" else {
            return false
        }
        if let setup = connector.connectionSetup {
            return setup.available && setup.isPost && setup.hasEndpoint
        }
        if connector.isAccountSignInPlanned || !connector.hasNativeDirectSync {
            return false
        }
        if let readiness = readiness(for: connector) {
            if readiness.sync_plan?.mode == "planned_account_sync" {
                return false
            }
            if readiness.primary_beta_path == "account-sign-in-planned" {
                return false
            }
        }
        return true
    }

    private func storedDirectConnectorPayload(for connectorID: String) -> [String: Any]? {
        guard let json = CortexCredentialStore.loadSecret(forKey: Self.directConnectorConfigSecretKey(for: connectorID)),
              let data = json.data(using: .utf8),
              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              JSONSerialization.isValidJSONObject(payload) else {
            return nil
        }
        return payload
    }

    private func saveDirectConnectorPayload(_ payload: [String: Any], for connectorID: String) {
        var stored = payload
        stored["processing"] = "sync"
        guard JSONSerialization.isValidJSONObject(stored),
              let data = try? JSONSerialization.data(withJSONObject: stored, options: [.sortedKeys]),
              let json = String(data: data, encoding: .utf8) else {
            return
        }
        CortexCredentialStore.saveSecret(json, forKey: Self.directConnectorConfigSecretKey(for: connectorID))
        refreshStoredConnectorConfigState()
        startConnectedSourceAutoSync(initialSync: false)
    }

    private func resolvedObsidianVaultPath() -> String {
        let trimmed = obsidianVaultPath.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return "" }
        var isDirectory: ObjCBool = false
        if FileManager.default.fileExists(atPath: trimmed, isDirectory: &isDirectory), isDirectory.boolValue {
            return trimmed
        }
        return ""
    }

    private func storedObsidianVaultURL() -> URL? {
        if let bookmarkedURL = storedObsidianVaultBookmarkURL() {
            let path = bookmarkedURL.standardizedFileURL.path
            if obsidianVaultPath != path {
                obsidianVaultPath = path
                UserDefaults.standard.set(path, forKey: Self.obsidianVaultPathDefaultsKey)
            }
            return bookmarkedURL
        }
        let path = resolvedObsidianVaultPath()
        guard !path.isEmpty else { return nil }
        return URL(fileURLWithPath: path, isDirectory: true)
    }

    private func rememberObsidianVaultPath(_ url: URL) {
        let path = url.standardizedFileURL.path
        obsidianVaultPath = path
        UserDefaults.standard.set(path, forKey: Self.obsidianVaultPathDefaultsKey)
        do {
            let bookmark = try url.bookmarkData(options: .withSecurityScope, includingResourceValuesForKeys: nil, relativeTo: nil)
            UserDefaults.standard.set(bookmark, forKey: Self.obsidianVaultBookmarkDefaultsKey)
        } catch {
            UserDefaults.standard.removeObject(forKey: Self.obsidianVaultBookmarkDefaultsKey)
            NSLog("Cortex notes folder bookmark failed: \(error.localizedDescription)")
        }
    }

    private func storedObsidianVaultBookmarkURL() -> URL? {
        guard let bookmark = UserDefaults.standard.data(forKey: Self.obsidianVaultBookmarkDefaultsKey) else {
            return nil
        }
        var isStale = false
        do {
            let url = try URL(
                resolvingBookmarkData: bookmark,
                options: [.withSecurityScope, .withoutUI],
                relativeTo: nil,
                bookmarkDataIsStale: &isStale
            )
            let securityScopeStarted = url.startAccessingSecurityScopedResource()
            defer {
                if securityScopeStarted {
                    url.stopAccessingSecurityScopedResource()
                }
            }
            var isDirectory: ObjCBool = false
            guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory), isDirectory.boolValue else {
                return nil
            }
            if isStale {
                rememberObsidianVaultPath(url)
            }
            return url
        } catch {
            UserDefaults.standard.removeObject(forKey: Self.obsidianVaultBookmarkDefaultsKey)
            NSLog("Cortex notes folder bookmark restore failed: \(error.localizedDescription)")
            return nil
        }
    }

    private func installObsidianPluginIfPossible(vaultURL: URL) async -> Bool {
        // App Store (sandbox / Guideline 2.5.2): NEVER install or enable a third-party plugin inside
        // the user's Obsidian vault. Copying main.js into .obsidian/plugins and enabling it in
        // community-plugins.json is installing + launching executable code that changes another app —
        // the same class of behavior Apple rejected for the MCP installer. The MAS build syncs the
        // notes CONTENT only; auto-installing the notes-bridge plugin is a Developer-ID/DMG-only
        // convenience. (The plugin payload is also excluded from the MAS bundle by build.sh.)
        guard !DistributionMode.isAppStore else { return false }
        let obsidianConfigURL = vaultURL.appendingPathComponent(".obsidian", isDirectory: true)
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: obsidianConfigURL.path, isDirectory: &isDirectory),
              isDirectory.boolValue else {
            return false
        }

        do {
            let pluginToken = try await registerObsidianPluginToken()
            try installBundledObsidianPlugin(vaultURL: vaultURL, apiToken: pluginToken)
            return true
        } catch {
            NSLog("Cortex notes-bridge plugin install failed: \(error.localizedDescription)")
            return false
        }
    }

    private func installBundledObsidianPlugin(vaultURL: URL, apiToken: String) throws {
        guard let sourceURL = Bundle.main.resourceURL?.appendingPathComponent("obsidian-cortex-plugin", isDirectory: true) else {
            throw CocoaError(.fileNoSuchFile)
        }

        let manager = FileManager.default
        let targetURL = vaultURL
            .appendingPathComponent(".obsidian", isDirectory: true)
            .appendingPathComponent("plugins", isDirectory: true)
            .appendingPathComponent(Self.obsidianPluginID, isDirectory: true)
        try manager.createDirectory(at: targetURL, withIntermediateDirectories: true)

        for fileName in ["manifest.json", "main.js", "versions.json"] {
            let sourceFile = sourceURL.appendingPathComponent(fileName)
            guard manager.fileExists(atPath: sourceFile.path) else {
                throw CocoaError(.fileNoSuchFile)
            }
            let targetFile = targetURL.appendingPathComponent(fileName)
            if manager.fileExists(atPath: targetFile.path) {
                try manager.removeItem(at: targetFile)
            }
            try manager.copyItem(at: sourceFile, to: targetFile)
        }

        let settings: [String: Any] = [
            "endpoint": endpoint.trimmingCharacters(in: CharacterSet(charactersIn: "/")),
            "apiToken": apiToken,
            "autoSyncOnStartup": true,
            "maxRecords": 5000
        ]
        let settingsData = try JSONSerialization.data(withJSONObject: settings, options: [.prettyPrinted, .sortedKeys])
        try settingsData.write(to: targetURL.appendingPathComponent("data.json"), options: .atomic)
        try enableBundledObsidianPlugin(vaultURL: vaultURL)
    }

    private func enableBundledObsidianPlugin(vaultURL: URL) throws {
        let enabledPluginsURL = vaultURL
            .appendingPathComponent(".obsidian", isDirectory: true)
            .appendingPathComponent("community-plugins.json")
        let manager = FileManager.default
        var enabledPlugins: [String] = []

        if manager.fileExists(atPath: enabledPluginsURL.path) {
            let data = try Data(contentsOf: enabledPluginsURL)
            let parsed = try JSONSerialization.jsonObject(with: data)
            guard let plugins = parsed as? [String] else {
                return
            }
            enabledPlugins = plugins
        }

        guard !enabledPlugins.contains(Self.obsidianPluginID) else {
            return
        }
        enabledPlugins.append(Self.obsidianPluginID)
        let data = try JSONSerialization.data(withJSONObject: enabledPlugins, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: enabledPluginsURL, options: .atomic)
    }

    private func syncLocalNotesFolder(_ connector: SourceConnectorCatalogItem, folderURL: URL, rememberPath: Bool, automatic: Bool = false) async {
        guard !obsidianSyncInFlight else {
            if !automatic {
                status = "\(connector.name) sync is already running"
            }
            return
        }
        obsidianSyncInFlight = true
        if !automatic {
            isBusy = true
        }
        beginMenuBarWork()
        var syncedSomething = false
        defer {
            obsidianSyncInFlight = false
            if !automatic {
                isBusy = false
            }
            endMenuBarWork(completedSync: syncedSomething)
        }

        do {
            if !automatic {
                status = "Syncing \(connector.name)..."
            }
            let securityScopeStarted = folderURL.startAccessingSecurityScopedResource()
            defer {
                if securityScopeStarted {
                    folderURL.stopAccessingSecurityScopedResource()
                }
            }
            let pluginInstalled = await installObsidianPluginIfPossible(vaultURL: folderURL)
            // App Store sandbox: the backend child cannot read the picked notes folder from the
            // raw path (this process's dynamic grant doesn't cross to the child, and the
            // stdlib-only Python child cannot resolve a macOS security-scoped bookmark). Copy the
            // folder into the app container — which the child CAN read — and sync that snapshot.
            // Under sandbox this is a one-time/refresh import rather than live two-way sync.
            // Direct (DeveloperID/DMG) builds sync the folder in place, unchanged.
            var vaultPathForSync = folderURL.standardizedFileURL.path
            var stagedNotesInbox: URL? = nil
            if DistributionMode.isAppStore {
                do {
                    let staged = try stageImportIntoContainer(originalPath: folderURL.standardizedFileURL.path)
                    vaultPathForSync = staged.stagedPath
                    stagedNotesInbox = staged.inboxDir
                } catch {
                    NSLog("Cortex notes folder staging failed: \(error.localizedDescription)")
                }
            }
            defer {
                if let inbox = stagedNotesInbox {
                    try? FileManager.default.removeItem(at: inbox)
                }
            }
            let syncBody: [String: Any] = [
                "vault_path": vaultPathForSync,
                "max_records": 5000,
                "processing": "sync"
            ]
            let syncData = try await request(
                path: "/v1/connectors/obsidian/sync",
                method: "POST",
                body: syncBody
            )
            let synced = try JSONDecoder().decode(ObsidianConnectorSyncResponse.self, from: syncData)
            guard synced.scan.records_found > 0, synced.scan.records_returned > 0 else {
                await loadSourceConnectivity()
                await loadTrust()
                // Persist the guidance on the connector card (not just the transient status bar)
                // so an empty vault always leaves the user a visible next step.
                connectorLastMessages[connector.id] = "No Markdown notes found in \(synced.scan.vault_name). Choose the folder that contains your notes, then sync again."
                if !automatic {
                    status = "No usable content found in \(synced.scan.vault_name). Choose a folder with real notes."
                }
                return
            }
            syncedSomething = true

            if rememberPath {
                rememberObsidianVaultPath(folderURL)
                startConnectedSourceAutoSync(initialSync: false)
            }
            firstSourceAdded = true
            onboardingFirstSourceNames = Array(Set(onboardingFirstSourceNames + [connector.name])).sorted()
            UserDefaults.standard.set(true, forKey: "onboardingFirstSourceImported.v1")
            UserDefaults.standard.set(onboardingFirstSourceNames, forKey: "onboardingFirstSourceNames.v1")
            await drainQueuedMemoryJobs(automatic: true)
            await loadSourceConnectivity()
            await loadTrust()
            await refreshAfterCapture()

            announceLearned(count: synced.saved + synced.queued)
            if synced.saved > 0 || synced.queued > 0 {
                let bridge = pluginInstalled ? " Cortex bridge installed." : ""
                status = importSummary(synced, sourceName: connector.name) + bridge
            } else if synced.skipped > 0, !automatic {
                status = pluginInstalled ? "\(connector.name) already up to date. Cortex bridge installed." : "\(connector.name) already up to date"
            } else if !automatic {
                status = pluginInstalled ? "\(connector.name) sync finished. Cortex bridge installed." : "\(connector.name) sync finished"
            }
        } catch {
            if automatic {
                status = CortexRecoveryText.failureStatus("\(connector.name) background sync", error: error)
            } else {
                status = CortexRecoveryText.failureStatus("\(connector.name) sync", error: error)
            }
        }
    }

    /// Human-readable "what happened" summary for an import/sync, so the silent filters (duplicates
    /// skipped, items failed, a large library truncated) are VISIBLE instead of hidden — the counts
    /// come straight from the sync response the app already decodes.
    private func importSummary(_ r: ObsidianConnectorSyncResponse, sourceName: String) -> String {
        let kept = r.saved + r.queued
        let found = r.scan.records_found
        var parts: [String] = ["\(kept) memor\(kept == 1 ? "y" : "ies") saved"]
        if r.skipped > 0 { parts.append("\(r.skipped) duplicate\(r.skipped == 1 ? "" : "s") skipped") }
        if r.failed > 0 { parts.append("\(r.failed) couldn’t be read") }
        var summary = "\(sourceName): from \(found) note\(found == 1 ? "" : "s"), " + parts.joined(separator: ", ") + "."
        if r.scan.truncated == true {
            summary += " Large library — \(r.scan.records_returned) of \(found) scanned; sync again to continue."
        }
        return summary
    }

    private func loadSyncReceipts(for devices: [SyncDeviceItem]) async -> [String: [SyncReceiptItem]] {
        var receiptsByDevice: [String: [SyncReceiptItem]] = [:]
        for device in devices.prefix(8) {
            guard let encodedID = device.id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) else {
                receiptsByDevice[device.id] = []
                continue
            }
            do {
                let data = try await request(path: "/v1/sync/devices/\(encodedID)/receipts?limit=3", method: "GET")
                receiptsByDevice[device.id] = try JSONDecoder().decode(SyncReceiptListResponse.self, from: data).results
            } catch {
                receiptsByDevice[device.id] = []
            }
        }
        return receiptsByDevice
    }

    func loadIntegrationTokens(includeRevoked: Bool? = nil) async {
        let include = includeRevoked ?? showRevokedIntegrationTokens
        do {
            let data = try await request(path: "/v1/integrations/tokens?include_revoked=\(include ? "true" : "false")", method: "GET")
            integrationTokens = try JSONDecoder().decode(IntegrationTokenListResponse.self, from: data).results
        } catch {
            status = CortexRecoveryText.failureStatus("Token refresh", error: error)
        }
    }

    func toggleRevokedIntegrationTokens(_ include: Bool) {
        showRevokedIntegrationTokens = include
        Task {
            await loadIntegrationTokens(includeRevoked: include)
        }
    }

    func revokeIntegrationToken(_ token: IntegrationTokenItem) async {
        guard let tokenID = token.token_id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) else {
            status = "Token revoke failed: invalid token id"
            return
        }
        do {
            _ = try await request(path: "/v1/integrations/tokens/\(tokenID)", method: "DELETE")
            status = "\(token.label) revoked"
            await loadIntegrationTokens()
            refreshIntegrationStates()
        } catch {
            status = CortexRecoveryText.failureStatus("Token revoke", error: error)
        }
    }

    func resetMCPIntegrationToken() async {
        // Rotate the legacy shared token (manual copy flows and configs written before
        // per-app tokens existed).
        mcpAPIKey = AppState.generateMCPAPIKey()
        CortexCredentialStore.saveSecret(mcpAPIKey, forKey: Self.mcpAPIKeyDefaultsKey)
        var registered = await registerMCPToken()
        // Rotate every per-app token and rewrite the configs of currently-installed
        // hosts so they pick up their fresh credential without a manual reinstall.
        var rewriteFailures: [String] = []
        for integration in integrations {
            let installedTargets = DistributionMode.isAppStore
                ? []
                : integration.configTargets.filter { configContainsCortex(at: $0.url) }
            guard AppState.existingMCPToken(for: integration.id) != nil || !installedTargets.isEmpty else {
                continue
            }
            CortexCredentialStore.saveSecret(
                AppState.generateMCPAPIKey(),
                forKey: Self.mcpTokenDefaultsKey(for: integration.id)
            )
            if !(await registerMCPToken(for: integration)) {
                registered = false
            }
            for target in installedTargets {
                do {
                    try mergeMCPConfig(at: target.url, integration: integration)
                } catch {
                    rewriteFailures.append(integration.name)
                    break
                }
            }
        }
        await loadIntegrationTokens()
        refreshIntegrationStates()
        if !rewriteFailures.isEmpty {
            status = "Tool access reset, but rewriting \(rewriteFailures.joined(separator: ", ")) failed. Reconnect those apps from Connections."
        } else if registered {
            status = "Tool access reset. Connected apps received fresh tokens; use fallback connection details if an app asks."
        } else {
            status = "Tool access token reset locally, but re-registering it with the memory engine failed. Reconnect your AI tools once the engine is reachable."
        }
    }

    func copyMCPConfig() {
        copyMCPConfig(for: nil)
    }

    func copyMCPConfig(for integration: AIIntegration?) {
        ensureUsableMCPAPIKey()
        if let integration = integration {
            registerMCPTokenInBackground(for: integration)
        }
        let text = mcpConfigJSON(for: integration)
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        status = integration.map { "\($0.name) connection details copied" } ?? "Connection details copied"
    }

    func copyIntegrationGuide(_ integration: AIIntegration) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(integrationGuide(for: integration), forType: .string)
        status = "\(integration.name) guide copied"
    }

    func installIntegration(_ integration: AIIntegration) {
        if DistributionMode.isAppStore {
            copyIntegrationGuide(integration)
            status = "Copied \(integration.name) connection guide"
            return
        }
        guard integration.supportsInstall else {
            copyIntegrationGuide(integration)
            return
        }
        do {
            registerMCPTokenInBackground(for: integration)
            for target in integration.configTargets {
                try mergeMCPConfig(at: target.url, integration: integration)
            }
            refreshIntegrationStates()
            status = "\(integration.name) connected"
        } catch {
            status = CortexRecoveryText.failureStatus("\(integration.name) install", error: error)
        }
    }

    func installDetectedIntegrations() {
        if DistributionMode.isAppStore {
            status = "App Store builds require advanced AI tool setup"
            return
        }
        let detected = integrations.filter { integration in
            integration.supportsInstall && integrationState(for: integration).appInstalled
        }
        guard !detected.isEmpty else {
            status = "No detected local AI apps yet"
            return
        }
        var installed = 0
        var failures: [String] = []
        for integration in detected {
            do {
                registerMCPTokenInBackground(for: integration)
                for target in integration.configTargets {
                    try mergeMCPConfig(at: target.url, integration: integration)
                }
                installed += 1
            } catch {
                failures.append(integration.name)
            }
        }
        refreshIntegrationStates()
        status = failures.isEmpty ? "Connected \(installed) detected apps" : "Connected \(installed), failed \(failures.joined(separator: ", "))"
    }

    func refreshIntegrationStates() {
        var next: [String: AIIntegrationState] = [:]
        for integration in integrations {
            let paths = integration.configTargets.map { $0.url.path }
            let installed = integrationAppearsInstalled(integration)
            if DistributionMode.isAppStore {
                next[integration.id] = AIIntegrationState(
                    appInstalled: installed,
                    configured: false,
                    configExists: false,
                    needsRepair: false,
                    configuredPaths: [],
                    availablePaths: paths
                )
                continue
            }
            let verified = integration.configTargets.filter { target in
                configHasVerifiedCortexServer(at: target.url, for: integration)
            }
            let cortexPresent = integration.configTargets.filter { target in
                configContainsCortex(at: target.url)
            }
            let exists = integration.configTargets.contains { target in
                FileManager.default.fileExists(atPath: target.url.path)
            }
            next[integration.id] = AIIntegrationState(
                appInstalled: installed,
                configured: !verified.isEmpty,
                configExists: exists,
                needsRepair: verified.isEmpty && !cortexPresent.isEmpty,
                configuredPaths: (verified.isEmpty ? cortexPresent : verified).map { $0.url.path },
                availablePaths: paths
            )
        }
        integrationStates = next
    }

    func integrationState(for integration: AIIntegration) -> AIIntegrationState {
        if DistributionMode.isAppStore {
            return AIIntegrationState(
                appInstalled: integrationAppearsInstalled(integration),
                configured: false,
                configExists: false,
                needsRepair: false,
                configuredPaths: [],
                availablePaths: integration.configTargets.map { $0.url.path }
            )
        }
        let verified = integration.configTargets.filter { configHasVerifiedCortexServer(at: $0.url, for: integration) }
        let cortexPresent = integration.configTargets.filter { configContainsCortex(at: $0.url) }
        return integrationStates[integration.id] ?? AIIntegrationState(
            appInstalled: integrationAppearsInstalled(integration),
            configured: !verified.isEmpty,
            configExists: integration.configTargets.contains { FileManager.default.fileExists(atPath: $0.url.path) },
            needsRepair: verified.isEmpty && !cortexPresent.isEmpty,
            configuredPaths: (verified.isEmpty ? cortexPresent : verified).map { $0.url.path },
            availablePaths: integration.configTargets.map { $0.url.path }
        )
    }

    private func integrationAppearsInstalled(_ integration: AIIntegration) -> Bool {
        let hostAppInstalled = integration.bundleIdentifiers.contains { bundleID in
            NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleID) != nil
        }
        guard integration.requiresExistingConfigTarget else {
            return hostAppInstalled
        }
        let hasExtensionConfig = integration.configTargets.contains { target in
            FileManager.default.fileExists(atPath: target.url.path) || configContainsCortex(at: target.url)
        }
        return hostAppInstalled && hasExtensionConfig
    }

    func openIntegrationConfig(_ integration: AIIntegration) {
        if DistributionMode.isAppStore {
            copyIntegrationGuide(integration)
            status = "Copied \(integration.name) connection guide"
            return
        }
        guard let target = integration.configTargets.first else {
            if let browserURL = integration.browserURL, let url = URL(string: browserURL) {
                NSWorkspace.shared.open(url)
            }
            return
        }
        try? FileManager.default.createDirectory(at: target.url.deletingLastPathComponent(), withIntermediateDirectories: true)
        NSWorkspace.shared.open(target.url.deletingLastPathComponent())
        status = "Opened \(integration.name) config folder"
    }

    private func mcpServerDefinition(for integration: AIIntegration? = nil, redactToken: Bool = false) -> [String: Any] {
        ensureUsableMCPAPIKey()
        // Least privilege: each host config embeds that host's own token. Flows with no
        // integration context (manual copy) fall back to the shared legacy token.
        let token: String
        if redactToken {
            token = "<copy-secret-connection-details>"
        } else if let integration = integration {
            token = AppState.loadOrCreateMCPToken(for: integration.id)
        } else {
            token = mcpAPIKey
        }
        let env = [
            "CORTEX_BASE_URL": endpoint,
            "CORTEX_API_KEY": token
        ]
        // App Store build (Guideline 2.5.2): the sandboxed, local-first build never ships or
        // references a runnable bridge script and installs/launches no executable code. It
        // presents the loopback HTTP tool API instead — any function-calling client points at
        // this URL with the token; the user wires it up themselves. (The stdio bridge + one-click
        // setup live only in the Developer-ID/DMG build, below.)
        if DistributionMode.isAppStore {
            return [
                "type": "http",
                "url": "\(endpoint)/v1/tools",
                "headers": ["Authorization": "Bearer \(token)"]
            ]
        }
        // Host configs must never point inside the .app bundle — moves, updates, and Gatekeeper
        // translocation would silently break every connected AI app. The supervisor maintains a
        // stable bridge launcher in Application Support that also resolves a working interpreter
        // (the old shape hardcoded /usr/bin/python3, which needs the CLT on a fresh Mac).
        if let launcher = BackendSupervisor.shared.installStableMCPBridge() {
            return [
                "command": launcher.path,
                "args": [] as [String],
                "env": env
            ]
        }
        // Pathological fallback (Application Support unwritable): the legacy in-bundle shape.
        let scriptURL = Bundle.main.resourceURL?
            .appendingPathComponent("scripts", isDirectory: true)
            .appendingPathComponent("cortex_mcp_stdio.py")
        let scriptPath = scriptURL?.path ?? "/path/to/cortex_mcp_stdio.py"
        return [
            "command": "/usr/bin/python3",
            "args": [scriptPath],
            "env": env
        ]
    }

    private func mcpConfigJSON(for integration: AIIntegration? = nil, redactToken: Bool = false) -> String {
        let config: [String: Any] = [
            "mcpServers": [
                "cortex": mcpServerDefinition(for: integration, redactToken: redactToken)
            ]
        ]
        let data = try? JSONSerialization.data(withJSONObject: config, options: [.prettyPrinted, .sortedKeys])
        return data.flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
    }

    private func integrationGuide(for integration: AIIntegration) -> String {
        let targetPaths = integration.configTargets.isEmpty
            ? "This app uses fallback connection details from Cortex."
            : integration.configTargets.map { "- \($0.label): \($0.url.path)" }.joined(separator: "\n")
        return """
        Cortex integration: \(integration.name)

        What this does:
        \(integration.summary)

        Recommended connection:
        \(integration.setupHint)

        Connection targets:
        \(targetPaths)

        Fallback connection preview:
        \(mcpConfigJSON(redactToken: true))

        Local Cortex service:
        Base URL: \(endpoint)
        Token: copy fallback connection details from Backup & recovery only if this app asks for them.

        Assistant rule:
        Search Cortex memory before asking the user to repeat project, person, decision, or open-loop details. Prefer cited memory search or agent adaptation when another app needs approved personal memory.

        After connecting:
        \(integration.restartHint)
        """
    }

    private func mergeMCPConfig(at url: URL, integration: AIIntegration? = nil) throws {
        let manager = FileManager.default
        try manager.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)

        var root: [String: Any] = [:]
        if manager.fileExists(atPath: url.path) {
            let data = try Data(contentsOf: url)
            if !data.isEmpty {
                guard let existing = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    throw NSError(domain: "Cortex", code: 1001, userInfo: [NSLocalizedDescriptionKey: "Existing config is not a JSON object"])
                }
                root = existing
            }
            try backupConfig(url)
        }

        var servers = root["mcpServers"] as? [String: Any] ?? [:]
        servers["cortex"] = mcpServerDefinition(for: integration)
        root["mcpServers"] = servers

        let data = try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: url, options: .atomic)
    }

    private func backupConfig(_ url: URL) throws {
        let manager = FileManager.default
        guard manager.fileExists(atPath: url.path) else { return }
        let stamp = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "-")
        let backupURL = url.deletingLastPathComponent().appendingPathComponent(url.lastPathComponent + ".cortex-backup-\(stamp)")
        try? manager.removeItem(at: backupURL)
        try manager.copyItem(at: url, to: backupURL)
    }

    private func configContainsCortex(at url: URL) -> Bool {
        guard let data = try? Data(contentsOf: url),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let servers = root["mcpServers"] as? [String: Any] else {
            return false
        }
        return servers["cortex"] != nil
    }

    private func configHasVerifiedCortexServer(at url: URL, for integration: AIIntegration? = nil) -> Bool {
        guard let data = try? Data(contentsOf: url),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let servers = root["mcpServers"] as? [String: Any],
              let cortex = servers["cortex"] as? [String: Any] else {
            return false
        }
        guard let command = cortex["command"] as? String,
              let env = cortex["env"] as? [String: String] else {
            return false
        }
        // Current shape: the stable Application Support bridge launcher. Legacy shape
        // (/usr/bin/python3 + in-bundle script) stays verified while its path still resolves;
        // reinstalling upgrades it in place.
        let args = cortex["args"] as? [String] ?? []
        let stableShape = command.hasSuffix("cortex-mcp-bridge")
            && FileManager.default.isExecutableFile(atPath: command)
        let legacyShape = command == "/usr/bin/python3"
            && (args.first?.hasSuffix("cortex_mcp_stdio.py") ?? false)
            && FileManager.default.fileExists(atPath: args.first ?? "")
        guard stableShape || legacyShape else {
            return false
        }
        let configuredBaseURL = (env["CORTEX_BASE_URL"] ?? "").trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let expectedBaseURL = endpoint.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let configuredToken = (env["CORTEX_API_KEY"] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard configuredBaseURL == expectedBaseURL, !configuredToken.isEmpty else {
            return false
        }
        // Legacy shared-token configs stay verified; reinstalling upgrades them to a
        // per-app token, which is the other accepted credential for this host.
        if configuredToken == mcpAPIKey {
            return true
        }
        guard let integration = integration else { return false }
        return configuredToken == AppState.existingMCPToken(for: integration.id)
    }

    func copyLocalAPISettings() {
        let text = """
        Cortex local API
        Base URL: \(endpoint)
        API token: \(apiKey)
        Vault: \(vaultPath)
        """
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        status = "Local settings copied"
    }

    func markFirstSourceAdded(sources: [String]) {
        firstSourceAdded = true
        onboardingFirstSourceNames = sources
        UserDefaults.standard.set(true, forKey: "onboardingFirstSourceImported.v1")
        UserDefaults.standard.set(sources, forKey: "onboardingFirstSourceNames.v1")
    }

    func markFirstMemoryReviewed(capture: CaptureItem) {
        firstMemoryReviewed = true
        UserDefaults.standard.set(true, forKey: "onboardingFirstMemoryReviewed.v1")
    }

    func markCortexUsed() {
        cortexUsed = true
        UserDefaults.standard.set(true, forKey: "onboardingCortexUsed.v1")
    }

    private func hasUsableOnboardingCitation(_ citations: [AskCitationItem]) -> Bool {
        guard !citations.isEmpty else { return false }
        return onboardingHasReviewedMemory
    }

    private func matchesOnboardingSource(source: String, sourceURL: String?) -> Bool {
        let sourceNames = Set(onboardingFirstSourceNames.map { $0.lowercased() })
        guard !sourceNames.isEmpty else { return true }
        let normalizedSource = source.lowercased()
        let normalizedURL = sourceURL?.lowercased() ?? ""
        return sourceNames.contains(normalizedSource)
            || sourceNames.contains { sourceName in
                normalizedURL.contains("service=\(sourceName)")
            }
    }

    private func onboardingAskSuggestion(from content: String) -> String? {
        let words = content
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { $0.count > 2 }
        guard words.count >= 4 else { return nil }
        let phrase = words.prefix(10).joined(separator: " ")
        return "What should I remember about \(phrase)?"
    }

    func markBackupDecision(_ decision: String) {
        onboardingBackupDecision = decision
        UserDefaults.standard.set(decision, forKey: "onboardingBackupDecision.v1")
    }

    func completeOnboarding() {
        // Hard requirement: you cannot finish setup (and initialize storage) without connecting a
        // real data source. This is the "onboarding requires a data connection" gate.
        guard hasAtLeastOneConnectedSource else {
            setOnboardingStep(.firstSource)
            status = "Connect at least one source — a service, your notes folder, or an export — to finish setup."
            return
        }
        guard canCompleteOnboarding else {
            let remaining = incompleteOnboardingStepTitles.prefix(2).joined(separator: ", ")
            status = remaining.isEmpty ? "Finish the first memory loop before completing." : "Finish: \(remaining)."
            return
        }
        saveMemorySettings()
        onboardingComplete = true
        onboardingDismissedForSession = false
        UserDefaults.standard.set(true, forKey: "onboardingComplete.v1")
        showOnboarding = false
        setOnboardingStep(.privateVault)
        status = "First memory loop complete"
        NotificationCenter.default.post(name: .cortexOnboardingCompleted, object: nil)
    }

    func finishOnboarding() {
        guard canCompleteOnboarding else {
            dismissOnboardingForSession()
            status = "Getting started closed. Continue from Home."
            return
        }
        completeOnboarding()
    }

    func dismissOnboardingForSession() {
        onboardingDismissedForSession = true
        showOnboarding = false
        status = "Getting started closed. Continue from Home anytime."
    }

    func showOnboardingAgain() {
        onboardingDismissedForSession = false
        setOnboardingStep(firstIncompleteOnboardingStep())
        showConnectionsPrivacy = false
        DispatchQueue.main.async { [weak self] in
            self?.showOnboarding = true
        }
    }

    func resetOnboardingProgressAfterDataDeletion() {
        onboardingComplete = false
        onboardingDismissedForSession = false
        firstSourceAdded = false
        firstMemoryReviewed = false
        cortexUsed = false
        onboardingFirstSourceNames = []
        onboardingBackupDecision = ""
        showOnboarding = false
        setOnboardingStep(.privateVault)
        for key in [
            "onboardingComplete.v1",
            "onboardingFirstSourceImported.v1",
            "onboardingFirstMemoryReviewed.v1",
            "onboardingCortexUsed.v1",
            "onboardingFirstImportID.v1",
            "onboardingFirstSourceNames.v1",
            "onboardingBackupDecision.v1",
            "onboardingTrustDefaultsApplied.v1",
        ] {
            UserDefaults.standard.removeObject(forKey: key)
        }
    }

    /// First-run only: present the onboarding walkthrough immediately at the very start of
    /// bootstrap — before ensureBackend()/the load*() chain — so a genuine new user sees the guided
    /// setup instantly instead of a blank window while the engine cold-starts.
    ///
    /// Gated PURELY on the persisted `onboardingComplete` flag (initialized from UserDefaults at
    /// init): a returning finished user has `onboardingComplete == true`, so the guard returns and
    /// nothing is shown or mutated. Deliberately does NOT run the self-heal that
    /// `presentOnboardingIfNeeded()` does, because that reads `hasAtLeastOneConnectedSource`, and
    /// `sourceAccounts` isn't loaded this early — evaluating it here could wrongly re-onboard a
    /// returning user. The self-heal still runs at the late `presentOnboardingIfNeeded()` call,
    /// after `loadTrust()` has populated sources.
    ///
    /// Called once at the top of bootstrap. Idempotent: the guards — re-checked inside the async
    /// closure — make a repeat call a no-op once the sheet is shown. Deliberately NOT re-driven from
    /// windowDidBecomeKey: doing so re-opened onboarding at step 1 during the deliberate
    /// onboarding→Connections handoff (openConnectionsPrivacy sets showOnboarding=false to let the
    /// Connections sheet present), which broke "Open Connections" on the final step.
    func presentOnboardingForFirstRunIfNeeded() {
        // Never present onboarding while the sign-in wall is up: the onboarding sheet ("Explore with
        // sample notes") would present modally OVER the wall and let an unsigned user drive the
        // pipeline, bypassing the required-account gate. Re-runs after sign-in (applySignedInSession
        // re-bootstraps), by which point requiresSignIn is false.
        guard !requiresSignIn else { return }
        guard !onboardingComplete, !onboardingDismissedForSession, !showOnboarding else { return }
        setOnboardingStep(firstIncompleteOnboardingStep())
        // Flip on the next runloop tick so the `.sheet` is driven AFTER the NSHostingController's
        // window is on screen. Setting an `isPresented` binding before the hosting view is attached
        // to a visible/key window can silently no-op with no retry; this mirrors the proven pattern
        // in showOnboardingAgain().
        DispatchQueue.main.async { [weak self] in
            guard let self,
                  !self.requiresSignIn,
                  !self.onboardingComplete,
                  !self.onboardingDismissedForSession,
                  !self.showOnboarding else { return }
            self.showOnboarding = true
            self.status = "Connect a source to finish setting up Cortex."
        }
    }

    func presentOnboardingIfNeeded() {
        // See presentOnboardingForFirstRunIfNeeded: onboarding must not present over the sign-in wall.
        guard !requiresSignIn else { return }
        // Self-heal: if a prior run left onboarding "complete" but no source is actually connected
        // (stale flag, or the user reset their data), setup isn't really done — reopen onboarding,
        // because Cortex has nothing to work from until a source is connected.
        if onboardingComplete && !hasAtLeastOneConnectedSource {
            onboardingComplete = false
            UserDefaults.standard.set(false, forKey: "onboardingComplete.v1")
        }
        guard !onboardingComplete, !onboardingDismissedForSession, !showOnboarding else { return }
        setOnboardingStep(firstIncompleteOnboardingStep())
        showOnboarding = true
        status = "Connect a source to finish setting up Cortex."
    }

    private func firstIncompleteOnboardingStep() -> OnboardingStep {
        OnboardingStep.allCases.first { !onboardingStepIsComplete($0) } ?? .privateVault
    }

    func nextOnboardingStep() {
        let steps = OnboardingStep.allCases
        if !canAdvanceOnboarding {
            switch onboardingStep {
            case .privateVault:
                status = "Start the local memory engine before continuing"
            case .firstSource:
                status = onboardingSourceHealthMessage ?? "Connect a source, then review memory"
            case .reviewMemory:
                status = "Approve one review item before asking Cortex"
            case .askUse:
                status = "Ask once with citations before finishing"
            case .trustBackup:
                status = "Back up local memory or skip backup for now"
            }
            return
        }
        let nextIndex = min(steps.count - 1, onboardingStep.rawValue + 1)
        setOnboardingStep(steps[nextIndex])
    }

    func previousOnboardingStep() {
        let steps = OnboardingStep.allCases
        let previousIndex = max(0, onboardingStep.rawValue - 1)
        setOnboardingStep(steps[previousIndex])
    }

    private func setOnboardingStep(_ step: OnboardingStep) {
        onboardingStep = step
        UserDefaults.standard.set(step.rawValue, forKey: "onboardingStep.v2")
    }

    func skipFirstBackup() {
        markBackupDecision("skipped")
        status = "First backup skipped"
    }

    func loadDiagnostics() async {
        do {
            let data = try await request(path: "/v1/diagnostics", method: "GET")
            diagnostics = try JSONDecoder().decode(DiagnosticsResponse.self, from: data)
        } catch {
            status = CortexRecoveryText.failureStatus("Diagnostics", error: error)
        }
    }

    func loadReliability() async {
        do {
            let data = try await request(path: "/v1/reliability/report", method: "GET")
            reliabilityReport = try JSONDecoder().decode(ReliabilityReportResponse.self, from: data)
        } catch {
            status = CortexRecoveryText.failureStatus("Reliability check", error: error)
        }
    }

    func createBackup() {
        Task {
            do {
                let data = try await request(path: "/v1/backups", method: "POST")
                let backup = try JSONDecoder().decode(BackupResponse.self, from: data)
                lastBackupPath = backup.backup_path
                markBackupDecision("backed-up")
                status = "Backup saved"
                await loadDiagnostics()
                await loadReliability()
                await loadTrust()
            } catch {
                status = CortexRecoveryText.failureStatus("Backup", error: error)
            }
        }
    }

    func repairStorage() {
        Task {
            isBusy = true
            status = "Repairing storage..."
            defer { isBusy = false }
            do {
                let data = try await request(path: "/v1/maintenance/repair-storage", method: "POST")
                let result = try JSONDecoder().decode(RepairStorageResponse.self, from: data)
                let changedRows = result.actions.reduce(0) { $0 + $1.rows }
                lastBackupPath = result.backup_path
                lastRepairSummary = "Storage repair finished. \(changedRows) index rows cleaned. Backup saved before repair."
                status = result.after.status == "ok" ? "Storage repaired" : "Repair finished with warnings"
                await loadRecent()
                await loadStats()
                await loadGraph()
                await loadReview()
                await loadProductLoop()
                await loadDiagnostics()
                await loadReliability()
                await loadTrust()
            } catch {
                let message = CortexRecoveryText.failureStatus("Repair", error: error)
                lastRepairSummary = message
                status = message
            }
        }
    }

    func rebuildSearchIndex() {
        Task {
            do {
                let data = try await request(path: "/v1/maintenance/rebuild-search", method: "POST")
                let result = try JSONDecoder().decode(MaintenanceResponse.self, from: data)
                status = "Indexed \(result.indexed_memories) memories"
                await loadRecent()
                await loadStats()
                await loadReview()
                await loadProductLoop()
                await loadDiagnostics()
                await loadReliability()
                await loadTrust()
            } catch {
                status = CortexRecoveryText.failureStatus("Rebuild", error: error)
            }
        }
    }

    func saveSupportBundle() {
        Task {
            do {
                let data = try await request(path: "/v1/support/bundle", method: "GET")
                let directory = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
                    ?? FileManager.default.temporaryDirectory
                let timestamp = ISO8601DateFormatter().string(from: Date())
                    .replacingOccurrences(of: ":", with: "-")
                let fileURL = directory.appendingPathComponent("Cortex-Support-\(timestamp).json")
                try data.write(to: fileURL, options: [.atomic])
                lastSupportBundlePath = fileURL.path
                status = "Support bundle saved"
                NSWorkspace.shared.open(fileURL)
            } catch {
                status = CortexRecoveryText.failureStatus("Support bundle", error: error)
            }
        }
    }

    func approveCapture(_ capture: CaptureItem) {
        guard !inFlightCaptureIds.contains(capture.id) else { return }
        inFlightCaptureIds.insert(capture.id)
        captureActionErrors[capture.id] = nil
        Task {
            defer { inFlightCaptureIds.remove(capture.id) }
            do {
                _ = try await request(path: "/v1/captures/\(capture.id)/approve", method: "POST")
                // The POST succeeded, so drop the card now — loadInbox below reassigns the
                // array anyway; this just makes the slide-out immediate instead of seconds late.
                withAnimation(.easeOut(duration: 0.25)) {
                    inbox.removeAll { $0.id == capture.id }
                }
                status = "Approved review item"
                markFirstMemoryReviewed(capture: capture)
                await drainQueuedMemoryJobs(automatic: true)
                await loadInbox()
                await loadRecent()
                await loadStats()
                await loadGraph()
                await loadReview()
                await loadProductLoop()
                await loadDiagnostics()
                await loadReliability()
                await loadTrust()
                await loadMirrorInsight()
                await loadProfile()
            } catch {
                captureActionErrors[capture.id] = CortexRecoveryText.failureStatus("Approve", error: error)
                status = CortexRecoveryText.failureStatus("Approve", error: error)
            }
        }
    }

    func archiveCapture(_ capture: CaptureItem) {
        guard !inFlightCaptureIds.contains(capture.id) else { return }
        inFlightCaptureIds.insert(capture.id)
        captureActionErrors[capture.id] = nil
        Task {
            defer { inFlightCaptureIds.remove(capture.id) }
            do {
                _ = try await request(path: "/v1/captures/\(capture.id)/archive", method: "POST")
                // Same optimistic removal as approveCapture: server confirmed, so animate out now.
                withAnimation(.easeOut(duration: 0.25)) {
                    inbox.removeAll { $0.id == capture.id }
                }
                status = "Archived review item"
                await loadInbox()
                await loadRecent()
                await loadStats()
                await loadGraph()
                await loadReview()
                await loadProductLoop()
                await loadDiagnostics()
                await loadReliability()
                await loadTrust()
            } catch {
                captureActionErrors[capture.id] = CortexRecoveryText.failureStatus("Archive", error: error)
                status = CortexRecoveryText.failureStatus("Archive", error: error)
            }
        }
    }

    func approveCaptures(_ captures: [CaptureItem]) {
        let visibleCaptures = Array(captures.prefix(10))
        guard !visibleCaptures.isEmpty else { return }
        Task {
            // Per-item like the single-action path: one failure doesn't abort the rest, each row
            // shows its own in-flight state, and errors land on the failing capture — not a vague
            // batch message that leaves the user guessing which items made it.
            visibleCaptures.forEach { inFlightCaptureIds.insert($0.id) }
            defer { visibleCaptures.forEach { inFlightCaptureIds.remove($0.id) } }
            var approved = 0
            var failed = 0
            for capture in visibleCaptures {
                do {
                    _ = try await request(path: "/v1/captures/\(capture.id)/approve", method: "POST")
                    captureActionErrors[capture.id] = nil
                    markFirstMemoryReviewed(capture: capture)
                    approved += 1
                } catch {
                    captureActionErrors[capture.id] = CortexRecoveryText.failureStatus("Approve", error: error)
                    failed += 1
                }
            }
            if failed == 0 {
                status = "Approved \(approved) review item\(approved == 1 ? "" : "s")"
            } else {
                status = "Approved \(approved), \(failed) failed — see the item\(failed == 1 ? "" : "s") for details"
            }
            await drainQueuedMemoryJobs(automatic: true)
            await loadInbox()
            await loadRecent()
            await loadStats()
            await loadGraph()
            await loadReview()
            await loadProductLoop()
            await loadDiagnostics()
            await loadReliability()
            await loadTrust()
            await loadMirrorInsight()
            await loadProfile()
        }
    }

    func archiveCaptures(_ captures: [CaptureItem]) {
        let visibleCaptures = Array(captures.prefix(10))
        guard !visibleCaptures.isEmpty else { return }
        Task {
            visibleCaptures.forEach { inFlightCaptureIds.insert($0.id) }
            defer { visibleCaptures.forEach { inFlightCaptureIds.remove($0.id) } }
            var archived = 0
            var failed = 0
            for capture in visibleCaptures {
                do {
                    _ = try await request(path: "/v1/captures/\(capture.id)/archive", method: "POST")
                    captureActionErrors[capture.id] = nil
                    archived += 1
                } catch {
                    captureActionErrors[capture.id] = CortexRecoveryText.failureStatus("Archive", error: error)
                    failed += 1
                }
            }
            if failed == 0 {
                status = "Archived \(archived) review item\(archived == 1 ? "" : "s")"
            } else {
                status = "Archived \(archived), \(failed) failed — see the item\(failed == 1 ? "" : "s") for details"
            }
            await loadInbox()
            await loadRecent()
            await loadStats()
            await loadGraph()
            await loadReview()
            await loadProductLoop()
            await loadDiagnostics()
            await loadReliability()
            await loadTrust()
        }
    }

    func deleteMemory(_ memory: MemoryItem) {
        guard !inFlightMemoryIds.contains(memory.id) else { return }
        inFlightMemoryIds.insert(memory.id)
        Task {
            defer { inFlightMemoryIds.remove(memory.id) }
            do {
                _ = try await request(path: "/v1/memories/\(memory.id)", method: "DELETE")
                status = "Forgot memory"
                await loadRecent()
                await search()
                await loadStats()
                await loadGraph()
                await loadReview()
                await loadProductLoop()
                await loadDiagnostics()
                await loadReliability()
                await loadTrust()
            } catch {
                status = CortexRecoveryText.failureStatus("Forget", error: error)
            }
        }
    }

    func deleteImport(_ item: SourceImportHistoryItem) {
        Task {
            do {
                let data = try await request(path: "/v1/imports/\(item.import_id)", method: "DELETE")
                let response = try JSONDecoder().decode(SourceImportDeleteResponse.self, from: data)
                if response.deleted {
                    status = "Removed \(response.deleted_captures) review item\(response.deleted_captures == 1 ? "" : "s") from this connection"
                } else {
                    status = "Connection already removed"
                }
                await refreshAfterCapture()
            } catch {
                status = CortexRecoveryText.failureStatus("Remove connection", error: error)
            }
        }
    }

    func deleteBackups() {
        Task {
            do {
                let data = try await request(path: "/v1/backups", method: "DELETE")
                let payload = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
                let deleted = payload?["deleted"] as? Int ?? 0
                status = deleted == 1 ? "Deleted 1 backup archive" : "Deleted \(deleted) backup archives"
                await loadDiagnostics()
                await loadReliability()
                await loadTrust()
            } catch {
                status = CortexRecoveryText.failureStatus("Delete backups", error: error)
            }
        }
    }

    func restoreLatestBackup() {
        Task {
            do {
                let data = try await request(path: "/v1/backups/restore-latest", method: "POST")
                let payload = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
                if let rebuild = payload?["rebuild"] as? [String: Any],
                   let captures = rebuild["captures"] as? Int,
                   let memories = rebuild["memories"] as? Int {
                    status = "Restored \(captures) captures and \(memories) memories"
                } else {
                    status = "Restored latest backup"
                }
                await loadSettings()
                await loadInbox()
                await loadRecent()
                await loadStats()
                await loadGraph()
                await loadReview()
                await loadProductLoop()
                await loadImportHistory()
                await loadDiagnostics()
                await loadReliability()
                await loadTrust()
            } catch {
                status = CortexRecoveryText.failureStatus("Restore", error: error)
            }
        }
    }

    func deleteAllUserData() {
        Task {
            do {
                _ = try await request(path: "/v1/user-data?include_backups=true", method: "DELETE")
                inbox = []
                recent = []
                searchResults = []
                askAnswer = ""
                askCitations = []
                hasSearched = false
                graphNodes = []
                graphEdges = []
                importHistory = []
                sourceAccounts = []
                allSourceAccounts = []
                syncCursors = []
                stats = nil
                review = nil
                productLoop = nil
                trustSummary = nil
                dataLifecycleReport = nil
                auditEvents = []
                diagnostics = nil
                reliabilityReport = nil
                resetOnboardingProgressAfterDataDeletion()
                status = "Deleted local Cortex data"
                await loadSettings()
                await loadInbox()
                await loadRecent()
                await loadStats()
                await loadGraph()
                await loadReview()
                await loadProductLoop()
                await loadImportHistory()
                await loadDiagnostics()
                await loadReliability()
                await loadTrust()
                // Onboarding progress was reset above; bring the user back to first-run.
                presentOnboardingIfNeeded()
            } catch {
                status = CortexRecoveryText.failureStatus("Delete all data", error: error)
            }
        }
    }

    func openExport(format: String) {
        Task { await exportFile(format: format) }
    }

    func exportFile(format: String) async {
        do {
            let isMarkdown = format == "markdown"
            let suffix = isMarkdown ? "/v1/export.md" : "/v1/export.json"
            let data = try await request(path: suffix, method: "GET")
            let directory = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
                ?? FileManager.default.temporaryDirectory
            let timestamp = ISO8601DateFormatter().string(from: Date())
                .replacingOccurrences(of: ":", with: "-")
            let fileURL = directory.appendingPathComponent("Cortex-Export-\(timestamp).\(isMarkdown ? "md" : "json")")
            try data.write(to: fileURL, options: [.atomic])
            status = "Export saved"
            NSWorkspace.shared.open(fileURL)
        } catch {
            status = CortexRecoveryText.failureStatus("Export", error: error)
        }
    }

    func openBackendHealth() {
        if let url = URL(string: endpoint + "/health") {
            NSWorkspace.shared.open(url)
        }
    }

    func checkForUpdates() {
        Task {
            let raw = updateFeedURL.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !raw.isEmpty else {
                updateStatus = "Add an update feed URL"
                return
            }
            guard let url = URL(string: raw) else {
                updateStatus = "Invalid update feed URL"
                return
            }
            updateStatus = "Checking..."
            do {
                let data: Data
                if url.isFileURL {
                    data = try Data(contentsOf: url)
                } else {
                    let (remoteData, response) = try await URLSession.shared.data(from: url)
                    if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
                        throw NSError(domain: "CortexUpdate", code: http.statusCode, userInfo: [NSLocalizedDescriptionKey: "HTTP \(http.statusCode)"])
                    }
                    data = remoteData
                }
                let manifest = try JSONDecoder().decode(UpdateManifestResponse.self, from: data)
                if manifest.bundle_id != "com.cortex.doppl" {
                    updateStatus = "Feed is for another app"
                    return
                }
                guard manifest.artifacts.allSatisfy({ resolvedUpdateArtifactURL($0) != nil }) else {
                    updateStatus = "Feed has invalid download URLs"
                    return
                }
                updateManifest = manifest
                let versionComparison = compareVersion(manifest.version, appVersion)
                let buildComparison = compareBuild(manifest.build, appBuild)
                if versionComparison > 0 || (versionComparison == 0 && buildComparison > 0) {
                    updateStatus = "Cortex \(manifest.version) (\(manifest.build)) is available"
                } else {
                    updateStatus = "Cortex is up to date"
                }
            } catch {
                updateStatus = CortexRecoveryText.failureStatus("Update check", error: error)
            }
        }
    }

    func openUpdateDownload() {
        guard let artifact = preferredUpdateArtifact, let url = resolvedUpdateArtifactURL(artifact) else {
            updateStatus = "No update artifact available"
            return
        }
        NSWorkspace.shared.open(url)
    }

    private func resolvedUpdateArtifactURL(_ artifact: UpdateArtifact) -> URL? {
        let raw = artifact.url.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty else { return nil }
        if let absolute = URL(string: raw), absolute.scheme != nil {
            return absolute
        }
        let feedRaw = updateFeedURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let feedURL = URL(string: feedRaw) else {
            return URL(string: raw)
        }
        if feedURL.isFileURL {
            if raw.hasPrefix("/") {
                return URL(fileURLWithPath: raw)
            }
            let base = raw.hasPrefix("downloads/")
                ? feedURL.deletingLastPathComponent().deletingLastPathComponent()
                : feedURL.deletingLastPathComponent()
            return URL(fileURLWithPath: raw, relativeTo: base).standardizedFileURL
        }
        if raw.hasPrefix("/") {
            var components = URLComponents()
            components.scheme = feedURL.scheme
            components.host = feedURL.host
            components.port = feedURL.port
            components.path = raw
            return components.url
        }
        let base = raw.hasPrefix("downloads/")
            ? feedURL.deletingLastPathComponent().deletingLastPathComponent()
            : feedURL.deletingLastPathComponent()
        return URL(string: raw, relativeTo: base)?.absoluteURL
    }

    func openReleaseDownloadsFolder() {
        let directory = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        NSWorkspace.shared.open(directory)
    }

    private func compareVersion(_ left: String, _ right: String) -> Int {
        let leftParts = left.split(separator: ".").map { Int($0) ?? 0 }
        let rightParts = right.split(separator: ".").map { Int($0) ?? 0 }
        let count = max(leftParts.count, rightParts.count)
        for index in 0..<count {
            let lhs = index < leftParts.count ? leftParts[index] : 0
            let rhs = index < rightParts.count ? rightParts[index] : 0
            if lhs != rhs { return lhs > rhs ? 1 : -1 }
        }
        return 0
    }

    private func compareBuild(_ left: String, _ right: String) -> Int {
        let lhs = Int(left) ?? 0
        let rhs = Int(right) ?? 0
        if lhs == rhs { return 0 }
        return lhs > rhs ? 1 : -1
    }

    func request(path: String, method: String, body: [String: Any]? = nil) async throws -> Data {
        do {
            return try await performRequest(path: path, method: method, body: body)
        } catch {
            // Cortex Cloud ONLY: a 401 means the short-lived cxs_ access token expired.
            // Refresh it exactly once and retry exactly once. This whole branch is
            // unreachable unless isCloudMode is true, so local mode is never touched.
            if isCloudMode, isUnauthorizedError(error) {
                if await refreshCloudAccessToken() {
                    // One retry with the freshly minted access token.
                    return try await performRequest(path: path, method: method, body: body)
                }
                // Refresh failed -> the session is gone. Sign out and surface it.
                handleCloudSessionExpired()
                throw error
            }
            // Only retry (and only then restart the backend) for connection-level
            // failures on idempotent methods. Never replay non-idempotent mutations,
            // and never restart the backend on an HTTP status error (4xx/5xx).
            guard isRetriableConnectionError(error), isIdempotentMethod(method) else {
                throw error
            }
            await ensureBackend()
            return try await performRequest(path: path, method: method, body: body)
        }
    }

    private func isUnauthorizedError(_ error: Error) -> Bool {
        (error as? CortexHTTPError)?.statusCode == 401
    }

    private func isIdempotentMethod(_ method: String) -> Bool {
        switch method.uppercased() {
        case "GET", "HEAD":
            return true
        default:
            return false
        }
    }

    private func isRetriableConnectionError(_ error: Error) -> Bool {
        guard let urlError = error as? URLError else { return false }
        switch urlError.code {
        case .cannotConnectToHost, .timedOut, .networkConnectionLost, .cannotFindHost:
            return true
        default:
            return false
        }
    }

    /// Long-running operations (source syncs, imports, backups, maintenance) legitimately take
    /// longer than an interactive read; a 15s cap made large first syncs "fail" while the backend
    /// was still working, leaving the UI in a false-error state.
    private func requestTimeout(path: String, method: String) -> TimeInterval {
        let longRunning = ["/sync", "/v1/imports", "/v1/backups", "/v1/maintenance/",
                           "/v1/jobs/run", "/v1/sources/sync-due", "/v1/user-data", "/v1/export"]
        if method != "GET" || path.hasPrefix("/v1/export") {
            if longRunning.contains(where: { path.contains($0) }) {
                return 180
            }
        }
        return 15
    }

    private func performRequest(path: String, method: String, body: [String: Any]? = nil) async throws -> Data {
        guard let url = URL(string: endpoint.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + path) else {
            throw URLError(.badURL)
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = requestTimeout(path: path, method: method)
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let body {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw CortexHTTPError(statusCode: http.statusCode, responseBody: String(data: data, encoding: .utf8))
        }
        return data
    }

    private func notify(_ title: String, _ body: String) {
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            let send = {
                let content = UNMutableNotificationContent()
                content.title = title
                content.body = body
                center.add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
            }
            switch settings.authorizationStatus {
            case .authorized, .provisional, .ephemeral:
                send()
            case .notDetermined:
                return
            default:
                break
            }
        }
    }

    deinit {
        obsidianAutoSyncTask?.cancel()
        directConnectorAutoSyncTask?.cancel()
    }
}

/// The primary navigation, in the archive's voice: serif Home / Review / Ask labels with a
/// wax-red pen-stroke underline that slides between tabs (the "ink tick"). ⌘1/2/3 shortcuts
/// stay registered and are taught on hover. Reads and writes the same `state.selectedTab`
/// every other setter uses, so navigation stays consistent everywhere.
struct CortexTabBar: View {
    @ObservedObject var state: AppState
    @Namespace private var inkTick

    private var pendingCount: Int {
        max(state.review?.stats.pending_captures ?? 0, state.inbox.count)
    }

    var body: some View {
        HStack(alignment: .bottom, spacing: CortexDesign.Space.xl) {
            ForEach(Array(AppTab.allCases.enumerated()), id: \.element) { index, tab in
                let selected = state.selectedTab == tab
                Button {
                    state.selectedTab = tab
                } label: {
                    VStack(spacing: 7) {
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            Text(tab.label)
                                .font(.system(size: 16, weight: selected ? .semibold : .regular, design: .serif))
                                .foregroundColor(selected ? CortexDesign.ink : CortexDesign.inkSecondary)
                            if tab == .review, pendingCount > 0 {
                                // Review's backlog badge: gold fill, ink numerals (gold is never text).
                                Text(pendingCount > 99 ? "99+" : String(pendingCount))
                                    .font(.system(size: 10, weight: .semibold, design: .monospaced))
                                    .foregroundColor(CortexDesign.ink)
                                    .padding(.horizontal, 5)
                                    .padding(.vertical, 1.5)
                                    .background(
                                        RoundedRectangle(cornerRadius: 4, style: .continuous)
                                            .fill(CortexDesign.gold.opacity(0.85))
                                    )
                            }
                        }
                        ZStack {
                            Color.clear.frame(height: 2.5)
                            if selected {
                                Capsule()
                                    .fill(CortexDesign.accent)
                                    .frame(height: 2.5)
                                    .matchedGeometryEffect(id: "inkTick", in: inkTick)
                            }
                        }
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .keyboardShortcut(KeyEquivalent(Character("\(index + 1)")), modifiers: .command)
                .help("\(tab.label) (⌘\(index + 1))")
                .accessibilityLabel(tab.label)
                .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, CortexDesign.Space.lg)
        .padding(.top, CortexDesign.Space.md)
        .background(CortexDesign.appBackground)
        .animation(.spring(response: 0.3, dampingFraction: 0.85), value: state.selectedTab)
    }
}

struct CortexView: View {
    @ObservedObject var state: AppState

    var body: some View {
        ZStack {
            mainContent
            // Required-account gate (Option B): when the build requires an account
            // (Info.plist CortexRequireAccount) and the user is not signed in, a full-window
            // sign-in wall covers everything. Off by default so the app never bricks before the
            // hosted backend is live; the founder flips CortexRequireAccount=true once it is.
            if state.requiresSignIn {
                CortexSignInWall(state: state)
                    .transition(.opacity)
            }
        }
        .animation(.easeInOut(duration: 0.2), value: state.cloudAccountEmail)
    }

    private var mainContent: some View {
        VStack(spacing: 0) {
            header
            CortexTabBar(state: state)
            Divider().overlay(CortexDesign.hairline)
            // A ZStack (not a switch) keeps all three tabs mounted so their .onChange(of:
            // selectedTab) reload triggers keep firing on tab switch — identical lifecycle to the
            // TabView this replaced, but with a large, custom, easy-to-see tab bar above.
            ZStack {
                ModelTab(state: state)
                    .opacity(state.selectedTab == .model ? 1 : 0)
                    .allowsHitTesting(state.selectedTab == .model)
                    .accessibilityHidden(state.selectedTab != .model)
                ReviewTab(state: state)
                    .opacity(state.selectedTab == .review ? 1 : 0)
                    .allowsHitTesting(state.selectedTab == .review)
                    .accessibilityHidden(state.selectedTab != .review)
                AskTab(state: state)
                    .opacity(state.selectedTab == .ask ? 1 : 0)
                    .allowsHitTesting(state.selectedTab == .ask)
                    .accessibilityHidden(state.selectedTab != .ask)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            footer
        }
        .background(CortexDesign.appBackground)
        .preferredColorScheme(.light)
        .accentColor(CortexDesign.accent)
        .frame(minWidth: 560, minHeight: 640)
        .sheet(isPresented: $state.showOnboarding, onDismiss: { state.onboardingSheetDismissed() }) {
            OnboardingView(state: state)
                .preferredColorScheme(.light)
                .accentColor(CortexDesign.accent)
                .frame(width: 820, height: 720)
        }
        .sheet(isPresented: $state.showConnectionsPrivacy, onDismiss: { state.connectionsSheetDismissed() }) {
            ConnectionsPrivacySheet(state: state)
                .preferredColorScheme(.light)
                .accentColor(CortexDesign.accent)
                .frame(width: 840, height: 720)
        }
        // App-menu "Settings…" (⌘,) requests the settings surface by flipping presentSettings; the
        // window content owns the actual presentation. Reuse the Connections & Privacy sheet — that
        // is Cortex's settings surface — then reset the one-shot flag.
        .onChange(of: state.presentSettings) { present in
            guard present else { return }
            state.presentSettings = false
            state.openConnectionsPrivacy()
        }
    }

    private var header: some View {
        VStack(spacing: 0) {
            HStack(spacing: CortexDesign.Space.md) {
                // The wordmark speaks in the archive's voice: serif ink on bare paper.
                Text("Cortex")
                    .font(.system(size: 20, weight: .semibold, design: .serif))
                    .foregroundColor(CortexDesign.ink)
                Spacer()
                Button {
                    state.openConnectionsPrivacy()
                } label: {
                    Label("Connections", systemImage: "lock.shield")
                        .labelStyle(.titleAndIcon)
                        .frame(minHeight: CortexDesign.controlHeight)
                }
                .buttonStyle(.bordered)
                .controlSize(.regular)
            }
            .padding(.horizontal, CortexDesign.Space.lg)
            .padding(.vertical, CortexDesign.Space.md)
        }
        .background(CortexDesign.appBackground)
    }

    private var footer: some View {
        // The ledger line: a hairline top rule with the gold progress beam running along it while
        // Cortex syncs (gold is a fill, never text), then quiet working-voice status below.
        VStack(spacing: 0) {
            ZStack(alignment: .leading) {
                Rectangle().fill(CortexDesign.hairline).frame(height: 1)
                if state.syncProgress?.active == true || state.menuBarWorkCount > 0 {
                    GeometryReader { geo in
                        Rectangle()
                            .fill(CortexDesign.gold)
                            .frame(width: max(24, geo.size.width * beamFraction), height: 2)
                            .animation(.easeInOut(duration: 0.5), value: beamFraction)
                    }
                    .frame(height: 2)
                }
            }
            if state.isBusy || footerNeedsAttention {
                HStack {
                    if state.isBusy {
                        ProgressView().scaleEffect(0.7)
                    }
                    Text(state.displayStatus)
                        .font(CortexDesign.Typography.caption)
                        .foregroundColor(footerNeedsAttention ? CortexDesign.accent : CortexDesign.inkSecondary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                        .help(state.displayStatus)
                    Spacer()
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(CortexDesign.quietBackground)
            }
        }
    }

    private var beamFraction: CGFloat {
        guard let progress = state.syncProgress, progress.total > 0 else { return 0.35 }
        return CGFloat(max(0.08, min(1.0, progress.fraction)))
    }

    private var footerNeedsAttention: Bool {
        CortexRecoveryText.needsAttention(state.displayStatus)
    }
}

/// Full-window required-account sign-in wall (Option B). Shown by CortexView when the build
/// requires an account and the user is not signed in. Offers every provider: Sign in with Apple
/// (native), Google/GitHub (browser), and email — reusing CortexCloudSection's sign-in form.
struct CortexSignInWall: View {
    @ObservedObject var state: AppState

    var body: some View {
        ZStack {
            CortexDesign.appBackground.ignoresSafeArea()
            VStack(spacing: 18) {
                Spacer(minLength: 0)
                Image(systemName: "brain.head.profile")
                    .font(.system(size: 42, weight: .semibold))
                    .foregroundColor(CortexDesign.accent)
                Text("Sign in to Doppl")
                    .font(.system(size: 26, weight: .bold, design: .serif))
                    .foregroundColor(CortexDesign.ink)
                Text("Create your account or sign in to build your memory and reach it across your devices and AI tools.")
                    .font(.callout)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 440)
                    .fixedSize(horizontal: false, vertical: true)
                CortexCloudSection(state: state)
                    .frame(maxWidth: 440)
                    .padding(22)
                    .background(CortexDesign.cardBackground)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(CortexDesign.hairline))
                Spacer(minLength: 0)
            }
            .padding(40)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .preferredColorScheme(.light)
        .accentColor(CortexDesign.accent)
    }
}

struct IntegrationCenterView: View {
    @ObservedObject var state: AppState
    let compact: Bool

    private var connectedCount: Int {
        state.integrations.filter { state.integrationState(for: $0).configured }.count
    }

    private var detectedCount: Int {
        state.integrations.filter { $0.supportsInstall && state.integrationState(for: $0).appInstalled }.count
    }

    private var compactIntegrations: [AIIntegration] {
        state.integrations.filter { integration in
            guard integration.supportsInstall else { return false }
            let integrationState = state.integrationState(for: integration)
            return integrationState.configured || integrationState.appInstalled
        }
    }

    var body: some View {
        Group {
            if compact {
                compactBody
            } else {
                fullBody
            }
        }
        .onAppear {
            state.refreshIntegrationStates()
        }
    }

    private var compactBody: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            IntegrationCompactHero(
                connectedCount: connectedCount,
                detectedCount: detectedCount,
                connectDetected: {
                    state.installDetectedIntegrations()
                },
                refresh: {
                    state.refreshIntegrationStates()
                }
            )
            if !compactIntegrations.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Detected tools")
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(.secondary)
                    ForEach(compactIntegrations.prefix(4)) { integration in
                        IntegrationCompactRow(state: state, integration: integration)
                    }
                }
            }
        }
    }

    private var fullBody: some View {
        VStack(alignment: .leading, spacing: compact ? 12 : 16) {
            header
            summary
            quickActions
            if !compact {
                troubleshootingSetupActions
            }
            ForEach(IntegrationCategory.allCases, id: \.self) { category in
                let categoryIntegrations = integrations(in: category)
                if !categoryIntegrations.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(category.rawValue)
                            .font(.headline)
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: compact ? 250 : 300), spacing: 10)], spacing: 10) {
                            ForEach(categoryIntegrations) { integration in
                                IntegrationCard(state: state, integration: integration, compact: compact)
                            }
                        }
                    }
                }
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(compact ? "AI tools" : "AI access")
                .font(compact ? .headline : .title3)
                .fontWeight(.semibold)
            Text(compact ? "Connect local AI tools so reviewed memory is available where you already work." : "Connect local tools so reviewed memory is available where you work. Advanced setup stays collapsed unless an app asks for it.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var summary: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 130), spacing: 8)], spacing: 8) {
            IntegrationMetricBadge(title: "Connected", value: "\(connectedCount)", systemImage: "checkmark.seal.fill", color: .green)
            IntegrationMetricBadge(title: "Detected", value: "\(detectedCount)", systemImage: "app.badge.checkmark", color: .accentColor)
            IntegrationMetricBadge(title: "Local memory", value: state.endpoint.replacingOccurrences(of: "http://", with: ""), systemImage: "network", color: .purple)
        }
    }

    private var quickActions: some View {
        HStack {
            if detectedCount > connectedCount {
                Button {
                    state.installDetectedIntegrations()
                } label: {
                    Label("Connect tools", systemImage: "wand.and.stars")
                }
                .buttonStyle(.borderedProminent)
            }

            if !compact || detectedCount == 0 {
                Button {
                    state.refreshIntegrationStates()
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }

            Spacer()
        }
    }

    private var troubleshootingSetupActions: some View {
        DisclosureGroup("Advanced tool config") {
            VStack(alignment: .leading, spacing: 8) {
                Text("Most tools connect automatically. Open this only when a local AI app asks for connection details.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                HStack {
                    Button {
                        state.copyMCPConfig()
                    } label: {
                        Label("Copy tool config", systemImage: "doc.on.doc")
                    }
                    Spacer()
                }
            }
            .padding(.top, 4)
        }
    }

    private func integrations(in category: IntegrationCategory) -> [AIIntegration] {
        state.integrations.filter { integration in
            integration.category == category && integration.supportsInstall
        }
    }
}

struct IntegrationCompactHero: View {
    let connectedCount: Int
    let detectedCount: Int
    let connectDetected: () -> Void
    let refresh: () -> Void

    private var needsConnection: Bool {
        detectedCount > connectedCount
    }

    private var needsManualFallback: Bool {
        connectedCount == 0 && detectedCount == 0
    }

    var body: some View {
        HStack(alignment: .center, spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill((needsConnection ? Color.accentColor : Color.green).opacity(0.14))
                Image(systemName: needsConnection ? "wand.and.stars" : (connectedCount > 0 ? "checkmark.seal.fill" : "app.badge"))
                    .font(.system(size: 28, weight: .semibold))
                    .foregroundColor(needsConnection ? .accentColor : (connectedCount > 0 ? .green : .secondary))
            }
            .frame(width: 56, height: 56)

            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.title3)
                    .fontWeight(.semibold)
                Text(detail)
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            if needsConnection {
                Button {
                    connectDetected()
                } label: {
                    Label("Connect", systemImage: "link.circle")
                        .frame(minWidth: 110, minHeight: 46)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            } else if needsManualFallback {
                Button {
                    refresh()
                } label: {
                    Label("Check Again", systemImage: "arrow.clockwise")
                        .frame(minWidth: 128, minHeight: 46)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
            } else {
                Button {
                    refresh()
                } label: {
                    Label("Check", systemImage: "arrow.clockwise")
                        .frame(minWidth: 110, minHeight: 46)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
            }
        }
        .padding(14)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var title: String {
        if needsConnection {
            return "\(detectedCount - connectedCount) tool\(detectedCount - connectedCount == 1 ? "" : "s") ready"
        }
        if connectedCount > 0 {
            return "\(connectedCount) tool\(connectedCount == 1 ? "" : "s") connected"
        }
        return "No local AI tool detected"
    }

    private var detail: String {
        if needsConnection {
            return "Cortex can connect detected local AI tools automatically."
        }
        if connectedCount > 0 {
            return "Approved memory is available to connected AI tools."
        }
        return "Open a supported local AI tool, then check again. Recovery controls stay in Backup & recovery."
    }
}

struct IntegrationCompactRow: View {
    @ObservedObject var state: AppState
    let integration: AIIntegration

    private var integrationState: AIIntegrationState {
        state.integrationState(for: integration)
    }

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: integrationState.configured ? "checkmark.circle.fill" : integration.systemImage)
                .foregroundColor(integrationState.configured ? .green : (integrationState.needsRepair ? .orange : .accentColor))
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text(integration.name)
                    .font(.callout)
                    .fontWeight(.medium)
                Text(statusDetail)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            Spacer(minLength: 8)
            if integrationState.configured {
                Text("Connected")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(.green)
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .background(Color.green.opacity(0.12))
                    .clipShape(Capsule())
            } else {
                Button {
                    state.installIntegration(integration)
                } label: {
                    Label(integrationState.needsRepair ? "Repair" : "Connect", systemImage: integrationState.needsRepair ? "wrench.and.screwdriver" : "link.circle")
                        .frame(minHeight: 38)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }
        }
        .padding(10)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.70))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var statusDetail: String {
        if integrationState.configured {
            return "Connected locally"
        }
        if integrationState.needsRepair {
            return "Connection needs repair"
        }
        return "Installed and ready to connect"
    }
}

struct IntegrationMetricBadge: View {
    let title: String
    let value: String
    let systemImage: String
    let color: Color

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: systemImage)
                .foregroundColor(color)
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                Text(value)
                    .font(.caption)
                    .fontWeight(.semibold)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer(minLength: 0)
        }
        .padding(9)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct IntegrationCard: View {
    @ObservedObject var state: AppState
    let integration: AIIntegration
    let compact: Bool

    private var integrationState: AIIntegrationState {
        state.integrationState(for: integration)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: integration.systemImage)
                    .font(.title3)
                    .frame(width: 28, height: 28)
                    .foregroundColor(.accentColor)
                VStack(alignment: .leading, spacing: 3) {
                    HStack {
                        Text(integration.name)
                            .font(.headline)
                            .lineLimit(1)
                        Spacer(minLength: 6)
                        IntegrationStatusBadge(state: integrationState, supportsInstall: integration.supportsInstall)
                    }
                    Text(integration.summary)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            if !integrationState.configuredPaths.isEmpty {
                Text(integrationState.configuredPaths.first ?? "")
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            } else if integration.supportsInstall, let path = integrationState.availablePaths.first {
                Text(path)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }

            if compact {
                compactAction
            } else {
                fullActionRow
            }

            Text(footnote)
                .font(.caption2)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private var compactAction: some View {
        if integrationState.configured {
            HStack {
                Label("Connected", systemImage: "checkmark.circle.fill")
                    .font(.callout)
                    .fontWeight(.semibold)
                    .foregroundColor(.green)
                Spacer()
            }
            .frame(minHeight: 36)
        } else if integrationState.needsRepair {
            Button {
                state.installIntegration(integration)
            } label: {
                Label("Repair", systemImage: "wrench.and.screwdriver")
                    .frame(maxWidth: .infinity, minHeight: 38)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
        } else if integration.supportsInstall && integrationState.appInstalled {
            Button {
                state.installIntegration(integration)
            } label: {
                Label("Connect", systemImage: "link.circle")
                    .frame(maxWidth: .infinity, minHeight: 38)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
        } else if integration.supportsInstall {
            HStack {
                Label("Install app to connect", systemImage: "app.badge")
                    .font(.callout)
                    .foregroundColor(.secondary)
                Spacer()
            }
            .frame(minHeight: 38)
        } else {
            EmptyView()
        }
    }

    private var fullActionRow: some View {
        HStack(spacing: 8) {
            if integration.supportsInstall {
                Button {
                    state.installIntegration(integration)
                } label: {
                    Label(integrationState.configured || integrationState.needsRepair ? "Repair" : "Connect", systemImage: integrationState.configured || integrationState.needsRepair ? "wrench.and.screwdriver" : "link.circle")
                        .frame(minHeight: 40)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            } else {
                Button {
                    state.openIntegrationConfig(integration)
                } label: {
                    Label("Open", systemImage: "arrow.up.right.square")
                        .frame(minHeight: 40)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }

            Spacer(minLength: 0)
        }
        .labelStyle(.titleAndIcon)
    }

    private var footnote: String {
        if compact {
            if integrationState.configured {
                return integration.restartHint
            }
            if integrationState.needsRepair {
                return "Repair the connection, then reopen the tool if it asks."
            }
            if integration.supportsInstall && integrationState.appInstalled {
                return "Connect once, then reopen the tool if it asks."
            }
            if integration.supportsInstall {
                return "Cortex will show a Connect action after the app is installed."
            }
        }
        if !integration.supportsInstall {
            return "Reference only. Direct connections will appear here when they are ready."
        }
        if integrationState.needsRepair {
            return "Connection settings are present but need to be updated."
        }
        return integration.restartHint
    }
}

struct IntegrationStatusBadge: View {
    let state: AIIntegrationState
    let supportsInstall: Bool

    var body: some View {
        Text(label)
            .font(.caption2)
            .fontWeight(.semibold)
            .foregroundColor(color)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(color.opacity(0.12))
            .clipShape(RoundedRectangle(cornerRadius: 7))
    }

    private var label: String {
        if state.configured { return "Connected" }
        if state.needsRepair { return "Repair" }
        if supportsInstall && state.appInstalled { return "Detected" }
        if supportsInstall && state.configExists { return "Config" }
        return supportsInstall ? "Ready" : "Reference"
    }

    private var color: Color {
        if state.configured { return .green }
        if state.needsRepair { return .orange }
        if state.appInstalled { return .accentColor }
        if state.configExists { return .orange }
        return supportsInstall ? .secondary : .purple
    }
}

struct LoopStepPill: View {
    let step: ProductLoopStep

    var body: some View {
        HStack(spacing: 7) {
            Image(systemName: icon)
                .foregroundColor(color)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(step.title)
                    .font(.caption)
                    .fontWeight(.semibold)
                Text(step.detail)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 8)
        .background(background)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var icon: String {
        switch step.status {
        case "done": return "checkmark.circle.fill"
        case "current": return "arrow.right.circle.fill"
        default: return "circle"
        }
    }

    private var color: Color {
        switch step.status {
        case "done": return .green
        case "current": return .accentColor
        default: return .secondary
        }
    }

    private var background: Color {
        switch step.status {
        case "current": return Color.accentColor.opacity(0.12)
        default: return Color(nsColor: .windowBackgroundColor)
        }
    }
}

struct SourceHealthSummarySection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title: "Connection health", detail: "Connected notes, AI tools, and review backlog.")
            if let report = state.sourceReadinessReport, !report.sources.isEmpty {
                SourceReadinessPanel(report: report)
            } else if !state.sourceConnectorCatalog.isEmpty || !state.sourceAccounts.isEmpty || !state.syncCursors.isEmpty {
                SourceConnectivityPanel(state: state)
            }
            if let summary = state.trustSummary, !summary.source_counts.isEmpty {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 160), spacing: 8)], spacing: 8) {
                    ForEach(summary.source_counts.prefix(6)) { source in
                        SourceHealthTile(source: source)
                    }
                }
            } else {
                QuietState(title: "No source connected", detail: "Connect a local source to start. AI tools can use reviewed memory later.")
            }
        }
    }
}

struct SourceReadinessPanel: View {
    let report: SourceReadinessResponse

    private var attentionSources: [SourceReadinessItem] {
        report.sources.filter { source in
            source.needsAttention || source.status == "needs_review" || source.pending > 0 || !source.warnings.isEmpty
        }
    }

    private var displaySources: [SourceReadinessItem] {
        let base = attentionSources.isEmpty
            ? report.sources.sorted { lhs, rhs in
                if lhs.active_memories != rhs.active_memories {
                    return lhs.active_memories > rhs.active_memories
                }
                return lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
            }
            : attentionSources
        return Array(base.prefix(5))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: 8)], spacing: 8) {
                SourceConnectivityMetric(title: "Notes ready", value: "\(report.summary.import_ready)", systemImage: "folder.badge.plus", color: .accentColor)
                SourceConnectivityMetric(title: "Connected notes", value: "\(report.summary.connected)", systemImage: "link.circle.fill", color: report.summary.connected == 0 ? .secondary : .green)
                SourceConnectivityMetric(title: "With reviewed memory", value: "\(report.summary.sources_with_data)", systemImage: "brain.head.profile", color: report.summary.sources_with_data == 0 ? .secondary : .blue)
                SourceConnectivityMetric(title: "Needs attention", value: "\(report.summary.needs_attention + report.summary.needs_review)", systemImage: "exclamationmark.triangle.fill", color: report.summary.needs_attention + report.summary.needs_review == 0 ? .secondary : .orange)
            }

            if let recommendation = report.recommendations.first {
                HStack(spacing: 8) {
                    Image(systemName: report.summary.needs_attention == 0 && report.summary.needs_review == 0 ? "checkmark.seal.fill" : "lightbulb.fill")
                        .foregroundColor(report.summary.needs_attention == 0 && report.summary.needs_review == 0 ? .green : .orange)
                        .frame(width: 18)
                    Text(displayRecommendation(recommendation))
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
                .padding(8)
                .background(Color(nsColor: .controlBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }

            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text(attentionSources.isEmpty ? "Current sources" : "Needs attention")
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(.secondary)
                    Spacer()
                    if !attentionSources.isEmpty {
                        Text("\(attentionSources.count)")
                            .font(.caption2)
                            .foregroundColor(.orange)
                    }
                }
                ForEach(displaySources) { source in
                    SourceReadinessRow(source: source)
                }
            }
        }
        .padding(10)
        .background(Color(nsColor: .textBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func displayRecommendation(_ value: String) -> String {
        if value.lowercased().contains("import one high-signal source") {
            return "Connect a local source so Cortex can sync useful memory into Review."
        }
        if value.lowercased().contains("local beta use") {
            return "Connections are healthy for current and planned sync."
        }
        return value
    }
}

struct SourceReadinessRow: View {
    let source: SourceReadinessItem

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: source.statusIcon)
                .foregroundColor(source.statusColor)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(source.name)
                        .font(.callout)
                        .fontWeight(.medium)
                        .lineLimit(1)
                    Text(source.statusTitle)
                        .font(.caption2)
                        .foregroundColor(source.statusColor)
                        .lineLimit(1)
                }
                Text(detail)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
        }
        .padding(8)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var detail: String {
        let memoryText = "\(source.active_memories) memories"
        let reviewText = source.pending > 0 ? "\(source.pending) review" : "\(source.approved) approved"
        if source.needsAttention, let warning = source.warnings.first {
            return "\(warning) · \(source.syncPlanModeTitle) · \(memoryText) · \(reviewText)"
        }
        return "\(source.syncPlanDisplayTitle) · \(memoryText) · \(reviewText)"
    }

}

struct SourceConnectivityPanel: View {
    @ObservedObject var state: AppState

    private var importReadyCount: Int {
        state.sourceConnectorCatalog.filter(\.isImportReady).count
    }

    private var accountsNeedingAttention: [SourceAccountItem] {
        state.sourceAccounts.filter(\.needsAttention)
    }

    private var cursorErrors: Int {
        state.syncCursors.filter(\.needsAttention).count
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: 8)], spacing: 8) {
                SourceConnectivityMetric(title: "Notes ready", value: "\(importReadyCount)", systemImage: "folder.badge.plus", color: .accentColor)
                SourceConnectivityMetric(title: "Connected", value: "\(state.connectedSourceAccounts.count)", systemImage: "link.circle.fill", color: state.connectedSourceAccounts.isEmpty ? .secondary : .green)
                SourceConnectivityMetric(title: "Needs attention", value: "\(accountsNeedingAttention.count + cursorErrors)", systemImage: "exclamationmark.triangle.fill", color: accountsNeedingAttention.isEmpty && cursorErrors == 0 ? .secondary : .orange)
            }

            if state.sourceAccounts.isEmpty {
                HStack(spacing: 8) {
                    Image(systemName: "point.3.connected.trianglepath.dotted")
                        .foregroundColor(.secondary)
                    Text("No connections yet")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Spacer()
                }
                .padding(.horizontal, 2)
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(state.sourceAccounts.prefix(3)) { account in
                        SourceAccountHealthRow(
                            account: account,
                            cursor: state.syncCursors.first(where: { $0.source_account_id == account.id })
                        )
                    }
                }
            }
        }
        .padding(10)
        .background(Color(nsColor: .textBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct SourceConnectivityMetric: View {
    let title: String
    let value: String
    let systemImage: String
    let color: Color

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: systemImage)
                .foregroundColor(color)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 0) {
                Text(value)
                    .font(.headline)
                Text(title)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .padding(8)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct SourceAccountHealthRow: View {
    let account: SourceAccountItem
    let cursor: SyncCursorItem?

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: needsAttention ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                .foregroundColor(needsAttention ? .orange : .green)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(accountTitle)
                    .font(.callout)
                    .fontWeight(.medium)
                    .lineLimit(1)
                Text(detail)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
        }
        .padding(8)
        .background(CortexDesign.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var detail: String {
        if let error = account.last_error ?? cursor?.last_error {
            return CortexRecoveryText.inlineError(error, fallback: "Refresh Connections. If it repeats, reconnect this source.")
        }
        if account.needsContent {
            return "No usable content found. Choose a source with real content."
        }
        if let synced = account.last_sync_at ?? cursor?.last_completed_at {
            if let summary = latestBatchSummary {
                return "\(sourceName) · \(summary) · \(shortDate(synced))"
            }
            return "\(sourceName) · \(statusText) · synced \(shortDate(synced))"
        }
        return "\(sourceName) · \(statusText)"
    }

    private var needsAttention: Bool {
        account.needsAttention
            || cursor?.needsAttention == true
            || account.needsContent
    }

    private var accountTitle: String {
        let label = account.account_label.trimmingCharacters(in: .whitespacesAndNewlines)
        if label.isEmpty {
            return sourceName
        }
        if account.source.lowercased() == "obsidian", label.lowercased().contains("obsidian") {
            return "Notes folder"
        }
        return label
    }

    private var sourceName: String {
        let source = account.source.lowercased()
        if source == "obsidian" {
            return "Notes"
        }
        if source == "mcp" {
            return "AI tools"
        }
        return account.source
            .replacingOccurrences(of: "_", with: " ")
            .replacingOccurrences(of: "-", with: " ")
            .capitalized
    }

    private var statusText: String {
        switch account.status.lowercased() {
        case "needs_review":
            return "waiting for Review"
        case "synced", "imported", "active":
            return "synced"
        case "empty":
            return "no notes found"
        case "error":
            return "needs attention"
        default:
            break
        }
        switch account.normalizedAuthState {
        case "connected", "authorized":
            return "connected"
        case "needs_content":
            return "choose notes"
        case "needs_auth":
            return "needs permission"
        default:
            return account.status.isEmpty ? "connected" : account.status.replacingOccurrences(of: "_", with: " ")
        }
    }

    private var latestBatchSummary: String? {
        guard let state = cursor?.state else { return nil }
        let received = state["last_batch_received"]?.intValue ?? 0
        guard received > 0 else { return nil }
        let saved = state["last_batch_saved"]?.intValue ?? 0
        let queued = state["last_batch_queued"]?.intValue ?? 0
        let skipped = state["last_batch_skipped"]?.intValue ?? 0
        let failed = state["last_batch_failed"]?.intValue ?? 0

        var parts: [String] = []
        if saved > 0 {
            parts.append("\(saved) saved")
        }
        if queued > 0 {
            parts.append("\(queued) queued")
        }
        if skipped > 0 {
            parts.append("\(skipped) unchanged")
        }
        if failed > 0 {
            parts.append("\(failed) failed")
        }
        if parts.isEmpty {
            return "\(received) checked"
        }
        return parts.joined(separator: ", ")
    }

    private func shortDate(_ value: String) -> String {
        String(value.prefix(19)).replacingOccurrences(of: "T", with: " ")
    }
}

struct SourceHealthTile: View {
    let source: SourceTrustSummary

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: source.pending > 0 ? "tray.full.fill" : "checkmark.seal.fill")
                .foregroundColor(source.pending > 0 ? .orange : .green)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(source.source)
                    .font(.callout)
                    .fontWeight(.medium)
                    .lineLimit(1)
                Text("\(source.approved) approved · \(source.pending) review")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .padding(10)
        .background(Color(nsColor: .controlBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct TrustScoreSection: View {
    let summary: TrustSummaryResponse

    var color: Color {
        if summary.trust_score >= 80 { return .green }
        if summary.trust_score >= 55 { return .orange }
        return .red
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(summary.mode.capitalized)
                        .font(.title2)
                        .fontWeight(.semibold)
                    Text("\(summary.counts["active_memories"] ?? 0) active memories · \(summary.counts["pending_captures"] ?? 0) pending · \(summary.counts["agent_events_7d"] ?? 0) agent events this week")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
                ZStack {
                    Circle()
                        .stroke(color.opacity(0.18), lineWidth: 10)
                    Circle()
                        .trim(from: 0, to: CGFloat(summary.trust_score) / 100)
                        .stroke(color, style: StrokeStyle(lineWidth: 10, lineCap: .round))
                        .rotationEffect(.degrees(-90))
                    Text("\(summary.trust_score)")
                        .font(.headline)
                        .fontWeight(.bold)
                }
                .frame(width: 68, height: 68)
            }

            if summary.risk_flags.isEmpty {
                TrustNotice(systemImage: "checkmark.shield.fill", title: "No active trust warnings", detail: "New memory is reviewed, shared memory is redacted, and storage health is clean.", color: .green)
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(summary.risk_flags.prefix(4).enumerated()), id: \.offset) { _, flag in
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .foregroundColor(.orange)
                            Text(flag)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .font(.caption)
                    }
                }
            }
        }
        .padding(12)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct TrustLifecycleSection: View {
    let report: DataLifecycleReportResponse

    private var statusColor: Color {
        report.status == "ok" ? .green : .orange
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Data Lifecycle")
                        .font(.headline)
                    Text("\(report.record_counts["active_memories"] ?? 0) active memories · \(report.audit.events) audit events · \(report.deletion.tombstones_count) deletion receipts")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
                Label(report.status == "ok" ? "Ready" : "Review", systemImage: report.status == "ok" ? "checkmark.shield.fill" : "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundColor(statusColor)
            }

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 10)], spacing: 10) {
                LifecycleFact(label: "Vault", value: report.storage.vault_status.capitalized, systemImage: "externaldrive")
                LifecycleFact(label: "Database", value: formatBytes(report.storage.database_bytes + report.storage.wal_bytes), systemImage: "cylinder.split.1x2")
                LifecycleFact(label: "Backups", value: "\(report.backups.count)", systemImage: "clock.arrow.circlepath")
                LifecycleFact(label: "Sync Manifests", value: "\(report.record_counts["sync_devices"] ?? 0) devices", systemImage: "macbook.and.iphone")
                LifecycleFact(label: "Export Redaction", value: report.export.redaction_enabled ? "On" : "Off", systemImage: report.export.redaction_enabled ? "text.badge.checkmark" : "text.badge.xmark")
            }

            VStack(alignment: .leading, spacing: 8) {
                LifecyclePathRow(title: "Vault path", value: report.storage.vault_path)
                LifecyclePathRow(title: "Database path", value: report.storage.database_path)
            }

            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "trash.slash")
                    .foregroundColor(.accentColor)
                    .frame(width: 22)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Delete covers local memory, source accounts, sync manifests, jobs, tokens, settings, audit events, and backups by default.")
                    Text(report.deletion.restore_preserves_tombstones ? "Restore keeps deletion receipts active." : "Restore does not preserve deletion receipts.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                .font(.caption)
                Spacer()
            }

            if let action = report.recommended_actions.first {
                TrustNotice(systemImage: report.status == "ok" ? "checkmark.circle" : "exclamationmark.triangle", title: report.status == "ok" ? "Lifecycle posture" : "Lifecycle action", detail: action, color: statusColor)
            }
        }
        .padding(12)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func formatBytes(_ bytes: Int) -> String {
        let formatter = ByteCountFormatter()
        formatter.allowedUnits = [.useKB, .useMB, .useGB]
        formatter.countStyle = .file
        return formatter.string(fromByteCount: Int64(bytes))
    }
}

struct LifecycleFact: View {
    let label: String
    let value: String
    let systemImage: String

    var body: some View {
        HStack(alignment: .center, spacing: 8) {
            Image(systemName: systemImage)
                .foregroundColor(.accentColor)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 1) {
                Text(label)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                Text(value)
                    .font(.callout)
                    .fontWeight(.semibold)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .frame(minHeight: 42)
    }
}

struct LifecyclePathRow: View {
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.caption2)
                .foregroundColor(.secondary)
            Text(value)
                .font(.caption)
                .lineLimit(2)
                .truncationMode(.middle)
                .textSelection(.enabled)
        }
    }
}

struct TrustPolicySection: View {
    @ObservedObject var state: AppState
    @State private var selectedPreset: TrustPreset = .advanced
    @State private var advancedExpanded = false
    @State private var highRiskExpanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("AI access")
                    .font(.headline)
                Spacer()
                TrustAccessBadge(text: selectedPreset.title, systemImage: "shield.lefthalf.filled", color: selectedPreset == .privateMode ? .green : .accentColor)
            }

            Text("Pick the default level of memory access for connected AI tools.")
                .font(.body)
                .foregroundColor(.secondary)

            Picker("AI access", selection: $selectedPreset) {
                ForEach(TrustPreset.allCases) { preset in
                    Text(preset.title).tag(preset)
                }
            }
            .pickerStyle(.segmented)
            .onAppear {
                selectedPreset = state.currentTrustPreset()
                advancedExpanded = false
            }
            .onChange(of: selectedPreset) { preset in
                if preset == .advanced {
                    advancedExpanded = true
                } else {
                    advancedExpanded = false
                    state.applyTrustPreset(preset)
                }
            }
            .onChange(of: state.appSettings) { settings in
                let preset = TrustPreset.matching(settings)
                selectedPreset = preset
            }

            TrustNotice(systemImage: "shield.lefthalf.filled", title: selectedPreset.title, detail: selectedPreset.detail, color: selectedPreset == .privateMode ? .green : .accentColor)

            DisclosureGroup("Fine-tune access", isExpanded: $advancedExpanded) {
                VStack(alignment: .leading, spacing: 14) {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Review")
                            .font(.caption)
                            .fontWeight(.semibold)
                            .foregroundColor(.secondary)
                        TrustToggleRow(
                            title: "Review new saves",
                            detail: "New source memories wait in Review before you treat them as trusted.",
                            systemImage: "tray.full",
                            isOn: $state.appSettings.review_new_captures
                        )
                        TrustToggleRow(
                            title: "Let AI use pending saves",
                            detail: "Turn this off when only reviewed memory should appear in search and AI access.",
                            systemImage: "lock.open",
                            isOn: $state.appSettings.allow_pending_in_context
                        )
                        TrustToggleRow(
                            title: "Redact shared memory",
                            detail: "Secrets, tokens, emails, and long account-like numbers are masked before sharing.",
                            systemImage: "text.badge.xmark",
                            isOn: $state.appSettings.redact_sensitive_context
                        )
                    }

                    Divider()

                    VStack(alignment: .leading, spacing: 10) {
                        Text("AI actions")
                            .font(.caption)
                            .fontWeight(.semibold)
                            .foregroundColor(.secondary)
                        TrustToggleRow(
                            title: "Let connected AI read memory",
                            detail: "Connected AI tools can search memory, read review queues, and inspect stats.",
                            systemImage: "eye",
                            isOn: $state.appSettings.allow_agent_reads
                        )
                        TrustToggleRow(
                            title: "Let connected AI save memory",
                            detail: "Connected AI tools can save, approve, or archive memory.",
                            systemImage: "square.and.pencil",
                            isOn: $state.appSettings.allow_agent_writes
                        )
                        TrustToggleRow(
                            title: "Allow AI data exports",
                            detail: "Connected AI tools can request redacted exports only when you turn this on.",
                            systemImage: "square.and.arrow.up",
                            isOn: $state.appSettings.allow_agent_exports
                        )
                    }

                    DisclosureGroup("Maintenance and deletion", isExpanded: $highRiskExpanded) {
                        VStack(alignment: .leading, spacing: 10) {
                            TrustToggleRow(
                                title: "Let connected AI run maintenance",
                                detail: "Connected AI tools can create backups, repair storage, or rebuild local indexes.",
                                systemImage: "wrench.and.screwdriver",
                                isOn: $state.appSettings.allow_agent_maintenance
                            )
                            TrustToggleRow(
                                title: "Let connected AI delete data",
                                detail: "Connected AI tools can delete memories, review items, backups, or all local user data.",
                                systemImage: "trash",
                                isOn: $state.appSettings.allow_agent_destructive_actions
                            )
                        }
                        .padding(.top, 8)
                    }

                    Divider()

                    VStack(alignment: .leading, spacing: 6) {
                        Text("Identity")
                            .font(.caption)
                            .fontWeight(.semibold)
                            .foregroundColor(.secondary)
                        Label("Your source aliases", systemImage: "person.text.rectangle")
                            .font(.callout)
                            .fontWeight(.medium)
                        Text("Names, handles, or email addresses that mark synced notes as written by you.")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        TextField("sarpt, @sarpt, sarpt@example.com", text: Binding(
                            get: {
                                (state.appSettings.identity_aliases ?? []).joined(separator: ", ")
                            },
                            set: { value in
                                state.appSettings.identity_aliases = value
                                    .split { character in
                                        character == "," || character == ";" || character == "\n"
                                    }
                                    .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
                                    .filter { !$0.isEmpty }
                            }
                        ))
                        .textFieldStyle(.roundedBorder)
                    }

                }
                .padding(.top, 8)
            }

            if advancedExpanded {
                HStack {
                    Button {
                        state.saveMemorySettings()
                    } label: {
                        Label("Save", systemImage: "checkmark.circle")
                    }
                    .buttonStyle(.borderedProminent)
                    Button {
                        Task {
                            await state.loadSettings()
                            await state.loadTrust()
                        }
                    } label: {
                        Label("Reload", systemImage: "arrow.clockwise")
                    }
                    Spacer()
                }
            }
        }
        .onChange(of: state.appSettings) { _ in
            // Auto-persist toggles so a flipped switch is never UI-only state.
            state.scheduleSettingsAutosave()
        }
    }
}

struct TrustAccessBadge: View {
    let text: String
    let systemImage: String
    let color: Color

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: systemImage)
            Text(text)
                .lineLimit(1)
        }
        .font(.caption)
        .fontWeight(.medium)
        .foregroundColor(color)
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(color.opacity(0.1))
        .clipShape(Capsule())
    }
}

struct TrustToggleRow: View {
    let title: String
    let detail: String
    let systemImage: String
    @Binding var isOn: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: systemImage)
                .foregroundColor(.accentColor)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 2) {
                Toggle(title, isOn: $isOn)
                    .toggleStyle(.switch)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

struct TrustSourceSection: View {
    @ObservedObject var state: AppState
    let summary: TrustSummaryResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Memory sources")
                .font(.headline)
            if summary.source_counts.isEmpty {
                QuietState(title: "No source connected yet", detail: "Connected sources and AI-tool activity will appear here.")
            } else {
                ForEach(summary.source_counts.prefix(8)) { source in
                    HStack(spacing: 10) {
                        Image(systemName: source.pending > 0 ? "tray.full.fill" : "checkmark.seal.fill")
                            .foregroundColor(source.pending > 0 ? .orange : .green)
                            .frame(width: 20)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(source.source)
                                .fontWeight(.medium)
                            Text("\(source.total) total · \(source.approved) approved · \(source.pending) pending · \(source.archived) archived")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        Spacer()
                        if let lastSeen = source.last_seen {
                            Text(shortDate(lastSeen))
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                        Menu {
                            ForEach(SourcePolicyMode.allCases) { mode in
                                Button {
                                    state.setSourcePolicy(source: source.source, mode: mode)
                                } label: {
                                    Label(mode.title, systemImage: mode.systemImage)
                                }
                            }
                        } label: {
                            Label(state.sourcePolicyMode(for: source.source).title, systemImage: state.sourcePolicyMode(for: source.source).systemImage)
                                .font(.caption)
                        }
                        .menuStyle(.borderlessButton)
                        .fixedSize()
                    }
                    .padding(8)
                    .background(Color(nsColor: .controlBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
            }
        }
    }

    private func shortDate(_ value: String) -> String {
        String(value.prefix(10))
    }
}

struct TrustAuditSection: View {
    let events: [AuditEventItem]
    let refresh: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Audit Trail")
                    .font(.headline)
                Spacer()
                Button(action: refresh) {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
            if events.isEmpty {
                QuietState(title: "No audit events yet", detail: "Source saves, approvals, backups, settings, and agent tool calls will appear here.")
            } else {
                ForEach(events.prefix(12)) { event in
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: icon(for: event))
                            .foregroundColor(color(for: event))
                            .frame(width: 20)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(event.object_type.capitalized) \(event.event_type.replacingOccurrences(of: "_", with: " "))")
                                .fontWeight(.medium)
                            if !event.metadata_text.isEmpty {
                                Text(event.metadata_text)
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                                    .lineLimit(2)
                            }
                            Text(event.created_at)
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                        Spacer()
                    }
                    .padding(.vertical, 4)
                }
            }
        }
    }

    private func icon(for event: AuditEventItem) -> String {
        if event.object_type == "agent" { return "wand.and.stars" }
        if event.event_type.contains("archive") { return "archivebox" }
        if event.event_type.contains("approve") { return "checkmark.seal" }
        if event.object_type == "settings" { return "slider.horizontal.3" }
        if event.object_type == "backup" { return "externaldrive" }
        return "list.bullet.rectangle"
    }

    private func color(for event: AuditEventItem) -> Color {
        if event.metadata_text.contains("success=False") { return .red }
        if event.object_type == "agent" { return CortexDesign.accent }
        if event.event_type.contains("archive") { return CortexDesign.inkSecondary }
        return CortexDesign.accent
    }
}

struct IntegrationTokensSection: View {
    @ObservedObject var state: AppState
    @State private var confirmResetToolToken = false

    var revokedTokens: [IntegrationTokenItem] {
        state.integrationTokens.filter { $0.revoked_at != nil }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Integration tokens")
                        .font(.headline)
                    Text("Review and revoke local tokens used by AI tools and REST clients.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
                Button {
                    Task { await state.loadIntegrationTokens() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }

            HStack {
                Toggle("Show revoked", isOn: Binding(
                    get: { state.showRevokedIntegrationTokens },
                    set: { state.toggleRevokedIntegrationTokens($0) }
                ))
                .toggleStyle(.checkbox)
                Spacer()
                Button {
                    confirmResetToolToken = true
                } label: {
                    Label("Reset Tool Token", systemImage: "key")
                }
                .confirmationDialog("Reset the tool access token?", isPresented: $confirmResetToolToken, titleVisibility: .visible) {
                    Button("Reset Token", role: .destructive) {
                        Task { await state.resetMCPIntegrationToken() }
                    }
                    Button("Cancel", role: .cancel) {}
                } message: {
                    Text("This immediately disconnects every AI tool using this token. You'll need to reconnect each tool with the new connection details.")
                }
            }

            if state.integrationTokens.isEmpty {
                QuietState(title: "No integration tokens", detail: "Connect AI tools to create local access.")
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(state.integrationTokens) { token in
                        IntegrationTokenRow(state: state, token: token)
                    }
                }
            }

            if !revokedTokens.isEmpty && !state.showRevokedIntegrationTokens {
                Text("\(revokedTokens.count) revoked token\(revokedTokens.count == 1 ? "" : "s") hidden")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .onAppear {
            Task { await state.loadIntegrationTokens() }
        }
    }
}

struct IntegrationTokenRow: View {
    @ObservedObject var state: AppState
    let token: IntegrationTokenItem
    @State private var confirmRevoke = false

    var isRevoked: Bool {
        token.revoked_at != nil
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .foregroundColor(isRevoked ? .secondary : .accentColor)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(token.label)
                        .fontWeight(.medium)
                    Text(token.audience.uppercased())
                        .font(.caption2)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.accentColor.opacity(0.12))
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                    if isRevoked {
                        Text("Revoked")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                }
                Text(token.token_id)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            if !isRevoked {
                Button(role: .destructive) {
                    confirmRevoke = true
                } label: {
                    Label("Revoke", systemImage: "xmark.shield")
                }
                .confirmationDialog("Revoke \(token.label)?", isPresented: $confirmRevoke, titleVisibility: .visible) {
                    Button("Revoke Token", role: .destructive) {
                        Task { await state.revokeIntegrationToken(token) }
                    }
                    Button("Cancel", role: .cancel) {}
                } message: {
                    Text("This immediately disconnects every AI tool using this token. This cannot be undone.")
                }
            }
        }
        .padding(8)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var icon: String {
        token.audience == "mcp" ? "wand.and.stars" : "network.badge.shield.half.filled"
    }

    private var detail: String {
        let scopes = token.scopes.isEmpty ? "no scopes" : token.scopes.joined(separator: ", ")
        let lastUsed = token.last_used_at.map { "last used \(shortDate($0))" } ?? "not used yet"
        if let revoked = token.revoked_at {
            return "\(scopes) · revoked \(shortDate(revoked))"
        }
        return "\(scopes) · \(lastUsed)"
    }

    private func shortDate(_ value: String) -> String {
        String(value.prefix(19)).replacingOccurrences(of: "T", with: " ")
    }
}

struct TrustNotice: View {
    let systemImage: String
    let title: String
    let detail: String
    let color: Color

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: systemImage)
                .foregroundColor(color)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .fontWeight(.medium)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
        }
        .padding(10)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct SettingsOnboardingSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Getting started")
                .font(.headline)
            HStack {
                Button {
                    state.showOnboardingAgain()
                } label: {
                    Label("Open Getting Started", systemImage: "sparkles")
                }
                Button {
                    state.openVaultFolder()
                } label: {
                    Label("Open Memory Folder", systemImage: "folder")
                }
                Spacer()
            }
            Text("Memory folder: \(state.vaultPath)")
                .font(.caption)
                .foregroundColor(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
        }
    }
}

struct SettingsUpdatesSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Installer and updates")
                    .font(.headline)
                Spacer()
                Text("v\(state.appVersion) (\(state.appBuild)) · \(state.releaseChannel)")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            TextField("Update feed URL", text: $state.updateFeedURL)
                .textFieldStyle(.roundedBorder)
            HStack {
                Button {
                    state.saveUpdateSettings()
                } label: {
                    Label("Save Feed", systemImage: "checkmark.circle")
                }
                Button {
                    state.checkForUpdates()
                } label: {
                    Label("Check", systemImage: "arrow.down.circle")
                }
                .buttonStyle(.borderedProminent)
                Button {
                    state.useBundledUpdateFeedExample()
                } label: {
                    Label("Use Example", systemImage: "doc.text")
                }
                Button {
                    state.openUpdateDownload()
                } label: {
                    Label("Open Download", systemImage: "square.and.arrow.down")
                }
                .disabled(state.preferredUpdateArtifact == nil)
                Spacer()
            }
            Text(state.updateStatus)
                .font(.caption)
                .foregroundColor(.secondary)
            if let manifest = state.updateManifest {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Latest: Cortex \(manifest.version) (\(manifest.build)) · \(manifest.channel)")
                        .fontWeight(.medium)
                    Text("Released: \(manifest.released_at) · macOS \(manifest.minimum_macos)+")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    if !manifest.release_notes.isEmpty {
                        ForEach(Array(manifest.release_notes.prefix(4).enumerated()), id: \.offset) { _, note in
                            HStack(alignment: .top, spacing: 6) {
                                Image(systemName: "checkmark.circle")
                                    .foregroundColor(.accentColor)
                                Text(note)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            .font(.caption)
                        }
                    }
                    ForEach(manifest.artifacts.prefix(3)) { artifact in
                        HStack {
                            Image(systemName: artifact.kind == "dmg" ? "opticaldiscdrive" : "archivebox")
                                .foregroundColor(.accentColor)
                            Text("\(artifact.filename) · \(formatBytes(artifact.size_bytes))")
                                .font(.caption)
                            Spacer()
                            Text(String(artifact.sha256.prefix(10)) + "...")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                    }
                }
                .padding(10)
                .background(Color(nsColor: .controlBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }
            Text("For the local beta, updates are manual: download the new DMG, quit Cortex, replace the app, and reopen. Your vault stays on disk.")
                .font(.caption)
                .foregroundColor(.secondary)
        }
    }

    private func formatBytes(_ bytes: Int) -> String {
        let formatter = ByteCountFormatter()
        formatter.allowedUnits = [.useKB, .useMB, .useGB]
        formatter.countStyle = .file
        return formatter.string(fromByteCount: Int64(bytes))
    }
}

struct SettingsIntegrationsSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("AI integrations")
                    .font(.headline)
                Spacer()
                Button {
                    state.refreshIntegrationStates()
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
            IntegrationCenterView(state: state, compact: true)
        }
    }
}

struct AdvancedGraphSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Map")
                    .font(.headline)
                Spacer()
                Button {
                    Task { await state.loadGraph() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
            GraphCanvas(nodes: state.graphNodes, edges: state.graphEdges)
                .frame(height: 220)
                .background(Color(nsColor: .textBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            Text("\(state.graphNodes.count) nodes · \(state.graphEdges.count) edges")
                .font(.caption)
                .foregroundColor(.secondary)
        }
    }
}

struct SettingsBackendSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Local memory engine")
                .font(.headline)
            TextField("Endpoint", text: $state.endpoint)
                .textFieldStyle(.roundedBorder)
            SecureField("API token", text: $state.apiKey)
                .textFieldStyle(.roundedBorder)
            HStack {
                Button {
                    state.persistSettings()
                } label: {
                    Label("Save", systemImage: "checkmark.circle")
                }
                Button {
                    state.openBackendHealth()
                } label: {
                    Label("Open Status", systemImage: "waveform.path.ecg")
                }
                Button {
                    Task { await state.bootstrap() }
                } label: {
                    Label("Reconnect", systemImage: "arrow.triangle.2.circlepath")
                }
                Spacer()
            }
            Text("Engine: \(state.displayBackendStatus)")
                .font(.caption)
                .foregroundColor(.secondary)
            Button {
                state.revealBackendLog()
            } label: {
                Label("Reveal Log in Finder", systemImage: "doc.text.magnifyingglass")
                    .font(.caption)
            }
            .buttonStyle(.link)
            .help(state.backendLogPath)
            Text(state.backendLogPath)
                .font(.caption)
                .foregroundColor(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
                .textSelection(.enabled)
        }
    }
}

struct SettingsReliabilitySection: View {
    @ObservedObject var state: AppState
    @State private var confirmRepairStorage = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Reliability")
                    .font(.headline)
                Spacer()
                if let report = state.reliabilityReport {
                    Label(statusTitle(report.status), systemImage: statusIcon(report.status))
                        .foregroundColor(statusColor(report.status))
                }
            }
            if let report = state.reliabilityReport {
                HStack(spacing: 8) {
                    HealthPill(label: "Engine", value: report.backend_version)
                    HealthPill(label: "Contract", value: String(report.health_contract))
                    HealthPill(label: "Checks", value: "\(report.checks.filter { $0.status == "ok" }.count)/\(report.checks.count)")
                }
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(report.checks) { check in
                        ReliabilityCheckRow(check: check)
                    }
                }
                if !report.recommended_actions.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Next action")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        ForEach(report.recommended_actions.prefix(2), id: \.self) { action in
                            Text(action)
                                .font(.caption)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                if let backup = report.latest_backup {
                    Text("Latest backup: \(backup.age_days) day\(backup.age_days == 1 ? "" : "s") old")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            } else {
                Text("Reliability report not loaded yet")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            if !state.lastRepairSummary.isEmpty {
                Text(state.lastRepairSummary)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let supportPath = state.lastSupportBundlePath {
                Text("Support bundle: \(supportPath)")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 138), spacing: 8)], alignment: .leading, spacing: 8) {
                Button {
                    Task {
                        await state.loadDiagnostics()
                        await state.loadReliability()
                    }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                Button {
                    confirmRepairStorage = true
                } label: {
                    Label("Repair Storage", systemImage: "cross.case")
                }
                Button {
                    state.saveSupportBundle()
                } label: {
                    Label("Support Bundle", systemImage: "lifepreserver")
                }
            }
        }
        .alert("Repair local storage?", isPresented: $confirmRepairStorage) {
            Button("Repair") {
                state.repairStorage()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Cortex checks the local database and repairs it if needed. Memory stays on this Mac; this can take a minute on large vaults.")
        }
    }

    private func statusTitle(_ status: String) -> String {
        switch status {
        case "ok": return "Ready"
        case "critical": return "Critical"
        default: return "Needs attention"
        }
    }

    private func statusIcon(_ status: String) -> String {
        switch status {
        case "ok": return "checkmark.circle.fill"
        case "critical": return "xmark.octagon.fill"
        default: return "exclamationmark.triangle.fill"
        }
    }

    private func statusColor(_ status: String) -> Color {
        switch status {
        case "ok": return .green
        case "critical": return .red
        default: return .orange
        }
    }
}

struct ReliabilityCheckRow: View {
    let check: ReliabilityCheck

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .foregroundColor(color)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(check.title)
                    .fontWeight(.medium)
                Text(check.detail)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let action = check.action, !action.isEmpty {
                    Text(action)
                        .font(.caption2)
                        .foregroundColor(color)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer()
        }
        .padding(8)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var icon: String {
        switch check.status {
        case "ok": return "checkmark.circle.fill"
        case "critical": return "xmark.octagon.fill"
        default: return "exclamationmark.triangle.fill"
        }
    }

    private var color: Color {
        switch check.status {
        case "ok": return .green
        case "critical": return .red
        default: return .orange
        }
    }
}

struct SettingsHealthSection: View {
    @ObservedObject var state: AppState
    @State private var confirmRebuildSearch = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Health")
                .font(.headline)
            if let diagnostics = state.diagnostics {
                HStack {
                    Label(diagnostics.status == "ok" ? "Healthy" : "Needs maintenance", systemImage: diagnostics.status == "ok" ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .foregroundColor(diagnostics.status == "ok" ? .green : .orange)
                    Spacer()
                    Text(formatBytes(diagnostics.db_size_bytes + diagnostics.wal_size_bytes))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                HStack(spacing: 8) {
                    HealthPill(label: "Check", value: diagnostics.quick_check)
                    HealthPill(label: "FTS", value: String(diagnostics.fts_orphans + diagnostics.inactive_fts_rows))
                    HealthPill(label: "Relations", value: String(diagnostics.relation_orphans))
                }
                if let vault = diagnostics.vault {
                    let records = vault.record_counts.values.reduce(0, +)
                    HStack(spacing: 8) {
                        HealthPill(label: "Vault", value: String(records))
                        HealthPill(label: "Events", value: String(vault.event_count))
                    }
                    Text("Vault: \(vault.path)")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                if let backup = state.lastBackupPath {
                    Text("Latest backup: \(backup)")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            } else {
                Text("Health not loaded yet")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            HStack {
                Button {
                    Task { await state.loadDiagnostics() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                Button {
                    state.createBackup()
                } label: {
                    Label("Backup", systemImage: "archivebox")
                }
                Button {
                    confirmRebuildSearch = true
                } label: {
                    Label("Rebuild Search", systemImage: "magnifyingglass.circle")
                }
                Spacer()
            }
        }
        .alert("Rebuild the search index?", isPresented: $confirmRebuildSearch) {
            Button("Rebuild") {
                state.rebuildSearchIndex()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Cortex rebuilds the local search index from your memories. Nothing is deleted; search may be briefly unavailable while it runs.")
        }
    }

    private func formatBytes(_ bytes: Int) -> String {
        let formatter = ByteCountFormatter()
        formatter.allowedUnits = [.useKB, .useMB, .useGB]
        formatter.countStyle = .file
        return formatter.string(fromByteCount: Int64(bytes))
    }
}

struct HealthPill: View {
    let label: String
    let value: String

    var body: some View {
        HStack(spacing: 4) {
            Text(label)
                .foregroundColor(.secondary)
            Text(value)
                .fontWeight(.medium)
        }
        .font(.caption)
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct SettingsStatsSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Memory stats")
                .font(.headline)
            StatsGrid(stats: state.stats)
            HStack {
                Button {
                    Task { await state.loadStats() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                Button {
                    state.openExport(format: "json")
                } label: {
                    Label("Export JSON", systemImage: "curlybraces")
                }
                Button {
                    state.openExport(format: "markdown")
                } label: {
                    Label("Export Markdown", systemImage: "doc.text")
                }
                Spacer()
            }
        }
    }
}

struct SettingsPrivacySection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Privacy")
                .font(.headline)
            Text("Cortex does not monitor the clipboard, record the screen, capture ambient activity, or send background data.")
                .font(.body)
                .foregroundColor(.secondary)
        }
    }
}

struct SettingsDataRecoverySection: View {
    @ObservedObject var state: AppState
    @State private var confirmRestoreBackup = false
    @State private var confirmDeleteBackups = false
    @State private var confirmDeleteAllData = false
    @State private var deleteAllConfirmationText = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Data & Recovery")
                .font(.headline)
            Text("Backups and destructive actions affect only this Mac's Cortex memory folder.")
                .font(.body)
                .foregroundColor(.secondary)
            HStack {
                Button {
                    state.createBackup()
                } label: {
                    Label("Back Up Now", systemImage: "archivebox")
                }
                Button {
                    confirmRestoreBackup = true
                } label: {
                    Label("Restore Latest Backup", systemImage: "arrow.counterclockwise")
                }
                Button {
                    state.openVaultFolder()
                } label: {
                    Label("Open Memory Folder", systemImage: "folder")
                }
                Spacer()
            }
            HStack {
                Button {
                    confirmDeleteBackups = true
                } label: {
                    Label("Delete Backup Archives", systemImage: "externaldrive.badge.minus")
                }
                Button(role: .destructive) {
                    confirmDeleteAllData = true
                } label: {
                    Label("Delete All Local Data", systemImage: "trash")
                }
                Spacer()
            }
            if let backup = state.lastBackupPath {
                Text("Latest backup: \(backup)")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
                    .truncationMode(.middle)
            }
        }
        .alert("Restore latest backup?", isPresented: $confirmRestoreBackup) {
            Button("Restore", role: .destructive) {
                state.restoreLatestBackup()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This replaces current vault records with the latest local backup, then rebuilds the local search index.")
        }
        .alert("Delete local backup archives?", isPresented: $confirmDeleteBackups) {
            Button("Delete Backups", role: .destructive) {
                state.deleteBackups()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This removes local backup archives from the Cortex memory folder. Current memories are not deleted.")
        }
        .sheet(isPresented: $confirmDeleteAllData, onDismiss: { deleteAllConfirmationText = "" }) {
            deleteAllDataConfirmationSheet
                .preferredColorScheme(.light)
                .accentColor(CortexDesign.accent)
                .frame(width: 460)
        }
    }

    private var deleteAllDataConfirmationSheet: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Delete all local Cortex data", systemImage: "exclamationmark.triangle.fill")
                .font(.headline)
                .foregroundColor(.red)
            Text("This permanently erases every review item, memory, task, graph link, setting, event, and attachment from this vault. Your local backup archives are also removed.")
                .font(.body)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("This cannot be undone. There is no way to recover this data afterward.")
                .font(.body.weight(.semibold))
                .foregroundColor(.red)
                .fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 6) {
                Text("Type DELETE to confirm.")
                    .font(.callout)
                    .foregroundColor(.secondary)
                TextField("DELETE", text: $deleteAllConfirmationText)
                    .textFieldStyle(.roundedBorder)
                    .disableAutocorrection(true)
            }
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) {
                    confirmDeleteAllData = false
                }
                .keyboardShortcut(.cancelAction)
                Button(role: .destructive) {
                    confirmDeleteAllData = false
                    state.deleteAllUserData()
                } label: {
                    Text("Delete All Data")
                }
                .buttonStyle(.borderedProminent)
                .tint(.red)
                .disabled(deleteAllConfirmationText.trimmingCharacters(in: .whitespacesAndNewlines) != "DELETE")
            }
        }
        .padding(20)
    }
}

struct StatsGrid: View {
    let stats: StatsResponse?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let stats {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 86), spacing: 8)], spacing: 8) {
                    StatBox(label: "Review Items", value: stats.captures)
                    StatBox(label: "Pending", value: stats.pending_captures)
                    StatBox(label: "Memories", value: stats.memories)
                    StatBox(label: "Decisions", value: stats.decisions)
                    StatBox(label: "Tasks", value: stats.tasks)
                    StatBox(label: "Entities", value: stats.entities)
                }
                if !stats.top_topics.isEmpty {
                    Text("Top topics: " + stats.top_topics.prefix(5).map { "#\($0.topic)" }.joined(separator: " "))
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(2)
                }
            } else {
                Text("Stats not loaded yet")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }
}

struct QuietState: View {
    let title: String
    let detail: String
    var systemImage: String = "sparkles"

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: systemImage)
                .font(.system(size: 26, weight: .regular))
                .foregroundColor(CortexDesign.accent.opacity(0.55))
            Text(title)
                .font(.headline)
            Text(detail)
                .font(.body)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 460)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 26)
        .padding(.horizontal, 18)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

struct StatBox: View {
    let label: String
    let value: Int

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(value)")
                .font(.title3)
                .fontWeight(.semibold)
            Text(label)
                .font(.caption2)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(8)
        .background(CortexDesign.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct CaptureCard: View {
    let capture: CaptureItem
    let approve: () -> Void
    let archive: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text((capture.title ?? capture.source).isEmpty ? "Untitled review item" : (capture.title ?? capture.source))
                    .font(.headline)
                    .lineLimit(1)
                Spacer()
                Text(capture.source)
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            Text(capture.summary ?? "")
                .font(.body)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Text("\(capture.memory_count ?? 0) memories")
                Text("\(capture.task_count ?? 0) tasks")
                if let date = capture.captured_at {
                    Text(String(date.prefix(10)))
                }
                Spacer()
            }
            .font(.caption)
            .foregroundColor(.secondary)
            HStack {
                Button("Approve") { approve() }
                Button("Archive") { archive() }
                Spacer()
            }
        }
        .padding(10)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct MemoryCard: View {
    let item: MemoryItem
    var onArchive: (() -> Void)? = nil
    var isInFlight: Bool = false
    @State private var confirmForget = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(item.kind.uppercased())
                    .font(.caption2)
                    .fontWeight(.semibold)
                    .foregroundColor(color(for: item.kind))
                if let layer = item.layer, layer != item.kind {
                    Text(layer.uppercased())
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
                Text(item.source)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                Spacer()
                if let importance = item.importance {
                    Text(String(repeating: "★", count: max(1, min(5, importance))))
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
                if let onArchive, canForget {
                    if isInFlight {
                        ProgressView()
                            .controlSize(.small)
                    }
                    Button("Forget") { confirmForget = true }
                        .font(.caption)
                        .disabled(isInFlight)
                        .confirmationDialog("Forget this memory?", isPresented: $confirmForget) {
                            Button("Forget", role: .destructive) {
                                onArchive()
                            }
                            .disabled(isInFlight)
                            Button("Cancel", role: .cancel) {}
                        } message: {
                            Text("Cortex will remove this saved memory from local search and exports.")
                        }
                }
            }
            Text(item.content)
                .font(.body)
                .fixedSize(horizontal: false, vertical: true)
            if let citation = citationLabel {
                HStack(spacing: 5) {
                    Image(systemName: "link")
                        .font(.caption2)
                    Text(citation)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer(minLength: 0)
                }
                .font(.caption)
                .foregroundColor(.secondary)
                .help(item.source_url ?? citation)
            }
            if let topics = item.topics, !topics.isEmpty {
                Text(topics.prefix(5).map { "#\($0)" }.joined(separator: " "))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .padding(10)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func color(for kind: String) -> Color {
        switch kind {
        case "decision": return CortexDesign.accent
        case "preference": return CortexDesign.accent
        case "style": return CortexDesign.inkSecondary
        case "negative": return CortexDesign.gold
        case "question": return CortexDesign.gold
        case "action": return CortexDesign.sealMoss
        default: return CortexDesign.inkSecondary
        }
    }

    private var canForget: Bool {
        let type = item.result_type?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return type == nil || type == "" || type == "memory"
    }

    private var citationLabel: String? {
        CitationDisplay.label(sourceURL: item.source_url)
    }
}

struct GraphCanvas: View {
    let nodes: [GraphNode]
    let edges: [GraphEdge]

    var body: some View {
        Canvas { context, size in
            let visible = Array(nodes.prefix(70))
            guard !visible.isEmpty else {
                context.draw(Text("No graph yet. Save a memory to create nodes."), at: CGPoint(x: size.width / 2, y: size.height / 2))
                return
            }
            var positions: [String: CGPoint] = [:]
            let center = CGPoint(x: size.width / 2, y: size.height / 2)
            let radius = max(80, min(size.width, size.height) * 0.38)
            for (index, node) in visible.enumerated() {
                let angle = (Double(index) / Double(max(visible.count, 1))) * Double.pi * 2
                let r = radius * (node.type == "source" ? 0.45 : 1.0)
                positions[node.id] = CGPoint(
                    x: center.x + CGFloat(Darwin.cos(angle)) * r,
                    y: center.y + CGFloat(Darwin.sin(angle)) * r
                )
            }
            for edge in edges.prefix(180) {
                guard let a = positions[edge.source_id], let b = positions[edge.target_id] else { continue }
                var path = Path()
                path.move(to: a)
                path.addLine(to: b)
                context.stroke(path, with: .color(.secondary.opacity(0.22)), lineWidth: 1)
            }
            for node in visible {
                guard let point = positions[node.id] else { continue }
                let color = color(for: node.type)
                let rect = CGRect(x: point.x - 7, y: point.y - 7, width: 14, height: 14)
                context.fill(Path(ellipseIn: rect), with: .color(color))
                if node.type == "person" || node.type == "project" || node.type == "source" {
                    context.draw(Text(node.label.prefix(18)).font(.caption2).foregroundColor(.primary), at: CGPoint(x: point.x, y: point.y + 18))
                }
            }
        }
    }

    private func color(for type: String) -> Color {
        switch type {
        case "person": return CortexDesign.accent
        case "project": return CortexDesign.inkSecondary
        case "decision": return CortexDesign.accent
        case "style": return CortexDesign.inkSecondary
        case "negative": return CortexDesign.gold
        case "action", "question": return CortexDesign.gold
        case "source": return CortexDesign.sealMoss
        default: return CortexDesign.inkSecondary
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSPopoverDelegate {
    private let state = AppState()
    private var statusItem: NSStatusItem!
    private var menuBarAnimator: MenuBarAnimator?
    /// Owns the bottom-of-screen live-activity surfaces (Learning HUD, edge glow, ripple, pill).
    private var liveActivity: LiveActivityCenter?
    /// The interactive bottom "N to review" pill (P5), injected into the coordinator.
    private var liveActivityPill: LiveActivityPill?
    private var quickPanelPopover: NSPopover?
    // When a transient popover auto-dismisses on the mouse-DOWN that lands on the status button, the
    // button's action still fires on mouse-UP; without this we'd immediately re-open it. We record
    // the close time and suppress a click-driven re-open that lands within a short window.
    private var popoverClosedAt: Date?
    fileprivate var hotKeyRef: EventHotKeyRef?
    private var mainWindow: NSWindow!
    private var mainWindowController: NSWindowController!

    func applicationDidFinishLaunching(_ notification: Notification) {
        logApp("applicationDidFinishLaunching")
        NSApp.setActivationPolicy(.regular)
        // Cortex ships a single, deliberately light/white look. Pin the whole app (native controls,
        // sheets, and the palette's dynamic colors) to Aqua so it stays light regardless of the
        // system's Dark Mode setting.
        NSApp.appearance = NSAppearance(named: .aqua)
        setupApplicationMenu()
        setupStatusItem()
        registerGlobalHotKey()
        setupMainWindow()
        NotificationCenter.default.addObserver(self, selector: #selector(onboardingCompleted), name: .cortexOnboardingCompleted, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(presentConstellationOverlay), name: .cortexPresentConstellation, object: nil)
        showMainWindow()
        DispatchQueue.main.async { [weak self] in
            self?.showMainWindow()
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) { [weak self] in
            self?.showMainWindow()
        }
        Task {
            await state.bootstrap()
            await MainActor.run {
                self.showMainWindow()
            }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        logApp("applicationWillTerminate")
        unregisterGlobalHotKey()
        BackendSupervisor.shared.terminate()
    }

    /// The user-facing app name for menu items. Uses the bundle's display name
    /// (CFBundleDisplayName, else CFBundleName) so the App-menu items read the same
    /// brand the menu-bar title shows — "Doppl" in the Mac App Store build, "Cortex" in
    /// the Developer-ID/DMG build — rather than the executable name (always "Cortex").
    private var appDisplayName: String {
        (Bundle.main.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String)
            ?? (Bundle.main.object(forInfoDictionaryKey: "CFBundleName") as? String)
            ?? ProcessInfo.processInfo.processName
    }

    /// A standard macOS main menu. Cortex runs with .regular activation policy (Dock icon +
    /// menu bar), so App Review requires a proper App menu with a Quit item (Guideline 4) — the
    /// status-bar menu is not the App menu. We also add Edit (so text fields get Cut/Copy/Paste/
    /// Select All + Undo) and Window, which reviewers expect from a Mac app.
    private func setupApplicationMenu() {
        let mainMenu = NSMenu()

        // App menu (first submenu; the menu-bar title is set by AppKit from the bundle name).
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenuItem.submenu = appMenu
        let appName = appDisplayName
        appMenu.addItem(withTitle: "About \(appName)", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        // Standard macOS Settings item (⌘,) — HIG/Guideline 4 expect it in the App menu. Opens the
        // Connections & Privacy surface (Cortex's settings) via the window content's observer.
        let settingsItem = appMenu.addItem(withTitle: "Settings…", action: #selector(menuOpenSettings), keyEquivalent: ",")
        settingsItem.target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide \(appName)", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let hideOthers = appMenu.addItem(withTitle: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit \(appName)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        // Edit menu — standard responder-chain actions so text entry (search, tokens) works.
        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "Edit")
        editMenuItem.submenu = editMenu
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        // Window menu.
        let windowMenuItem = NSMenuItem()
        mainMenu.addItem(windowMenuItem)
        let windowMenu = NSMenu(title: "Window")
        windowMenuItem.submenu = windowMenu
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        windowMenu.addItem(.separator())
        windowMenu.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")

        // Help menu — reviewers (HIG/Guideline 4) expect a Help menu with a usable entry. Opens the
        // bundled help/README if present, otherwise the product site.
        let helpMenuItem = NSMenuItem()
        mainMenu.addItem(helpMenuItem)
        let helpMenu = NSMenu(title: "Help")
        helpMenuItem.submenu = helpMenu
        let helpItem = helpMenu.addItem(withTitle: "\(appName) Help", action: #selector(menuOpenHelp), keyEquivalent: "?")
        helpItem.target = self

        NSApp.mainMenu = mainMenu
        NSApp.windowsMenu = windowMenu
        NSApp.helpMenu = helpMenu
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        logApp("applicationShouldHandleReopen visibleWindows=\(flag)")
        showMainWindow()
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.toolTip = "Cortex — click to ask your memory (⌃⌥Space)"
        // Click-split so both surfaces coexist on one status item WITHOUT statusItem.menu hijacking
        // every click: LEFT-click / hotkey → the rich "Cortex Spotlight" popover; RIGHT-click (or
        // ⌃-click) → a lean native menu (Open/Review/Ask/Sync/Connections/Quit).
        statusItem.button?.action = #selector(statusItemClicked)
        statusItem.button?.target = self
        statusItem.button?.sendAction(on: [.leftMouseUp, .rightMouseUp])
        // A live menu-bar icon: spins while Cortex syncs, shows the review count when memory is
        // waiting, flashes a checkmark when a sync completes, and rests as a calm brain otherwise.
        let animator = MenuBarAnimator(statusItem: statusItem) { [weak self] in
            self?.currentMenuBarSnapshot() ?? MenuBarSnapshot(syncing: false, syncCompletedAt: nil, learnedAt: nil, capturedAt: nil, pendingCount: 0)
        }
        menuBarAnimator = animator
        animator.start()

        // Bottom-of-screen live activity: a Learning HUD + ambient glow + "memory formed" ripple
        // while Cortex works, and an idle "N to review" pill. One coordinator owns the bottom region
        // so these never collide. It samples the same state the menu-bar icon does.
        let pill = LiveActivityPill(actions: LiveActivityPill.Actions(
            openReview: { [weak self] in
                guard let self else { return }
                self.state.selectedTab = .review
                self.showMainWindow()
            },
            openApp: { [weak self] in self?.showMainWindow() }
        ))
        let center = LiveActivityCenter { [weak self] in
            self?.currentLiveActivitySnapshot() ?? LiveActivitySnapshot(
                enabled: false, working: false, done: 0, total: 0, detail: nil, pendingCount: 0,
                learnedAt: nil, capturedAt: nil, learnedCount: 0, syncCompletedAt: nil
            )
        }
        center.pill = pill
        liveActivityPill = pill
        liveActivity = center
        center.start()
    }

    /// Translates the app's live state into the snapshot the bottom live-activity surfaces render.
    /// Mirrors the menu-bar snapshot's "working"/"pending" derivation so the two stay in lockstep,
    /// and adds the sync progress counts + display-safe detail label the Learning HUD needs.
    private func currentLiveActivitySnapshot() -> LiveActivitySnapshot {
        // The bottom Learning HUD is a prominent "building your memory" surface, so it reflects REAL
        // memory work only: an active sync/import/backup (menuBarWorkCount) or the job queue draining
        // (syncProgress). It deliberately does NOT key off the generic `isBusy` flag — that also covers
        // trivial UI work (search, saves, stats/profile loads) and would pop the HUD "just for fun".
        // (The small menu-bar icon keeps the broader signal below.)
        let working = state.menuBarWorkCount > 0
            || state.syncProgress?.active == true
        let pending = max(state.review?.stats.pending_captures ?? 0, state.inbox.count)
        let progress = state.syncProgress
        // Required-account gate: while the sign-in wall is up, surface nothing — no pill, no HUD, and
        // in particular no memory-derived pending count (which would leak a "N to review" number).
        let gated = state.requiresSignIn
        return LiveActivitySnapshot(
            enabled: state.liveActivityEnabled && !gated,
            working: working && !gated,
            done: progress?.done ?? 0,
            total: progress?.total ?? 0,
            detail: progress?.detail,  // already display-safe (syncDetailLabel → SourceDisplayName)
            pendingCount: gated ? 0 : max(0, pending),
            learnedAt: state.lastLearnedAt,
            capturedAt: state.lastCapturedAt,
            learnedCount: state.lastLearnedCount,
            syncCompletedAt: state.menuBarSyncCompletedAt
        )
    }

    /// Translates the app's live state into the snapshot the menu-bar icon renders.
    private func currentMenuBarSnapshot() -> MenuBarSnapshot {
        // menuBarWorkCount is the primary (level-based) signal — it stays raised for the entire
        // duration of any sync/import/backup/launch, so the sampling loop can never miss it. The
        // job-poll progress and isBusy remain as supplements for backend-side queue work.
        let working = state.menuBarWorkCount > 0
            || state.syncProgress?.active == true
            || state.isBusy
        // Use the larger of the two pending signals so the badge shows whenever either source knows
        // about waiting items (they can lag each other right after a sync/import).
        let pending = max(state.review?.stats.pending_captures ?? 0, state.inbox.count)
        return MenuBarSnapshot(
            syncing: working,
            syncCompletedAt: state.menuBarSyncCompletedAt,
            learnedAt: state.lastLearnedAt,
            capturedAt: state.lastCapturedAt,
            pendingCount: max(0, pending)
        )
    }

    // MARK: - Status-item click routing

    @objc private func statusItemClicked() {
        if let event = NSApp.currentEvent,
           event.type == .rightMouseUp || event.modifierFlags.contains(.control) {
            showStatusMenu()
        } else {
            togglePopover(fromClick: true)
        }
    }

    // MARK: - Cortex Spotlight popover

    private func ensurePopover() -> NSPopover {
        if let quickPanelPopover { return quickPanelPopover }
        let popover = NSPopover()
        popover.behavior = .transient
        popover.animates = true
        let panel = MenuBarQuickPanel(
            state: state,
            onOpenApp: { [weak self] in self?.quickPanelOpenApp() },
            onOpenReview: { [weak self] in self?.quickPanelOpenReview() },
            onClose: { [weak self] in self?.quickPanelPopover?.performClose(nil) }
        )
        let hosting = NSHostingController(rootView: panel)
        // FIXED size — do NOT use sizingOptions=[.preferredContentSize]. AppState publishes
        // constantly while the panel is open (the 4s job-progress poll rewrites syncProgress and
        // refreshes profile/mirror), and with content-driven sizing every such re-render resized the
        // transient popover under the cursor — that was the real "flicker + can't click" bug, not the
        // menu-bar animator. A constant size decouples popover geometry from panel re-renders. The
        // panel pins itself to this same height (MenuBarQuickPanel .frame height) so its content
        // rearranges WITHIN the fixed box instead of trying to grow it.
        popover.contentViewController = hosting
        popover.contentSize = NSSize(width: 384, height: 520)
        popover.delegate = self
        quickPanelPopover = popover
        return popover
    }

    /// Open (or, from a hotkey/click on an already-open panel, close) the Spotlight popover.
    /// `fromClick` distinguishes a status-button click (which may have just auto-dismissed a transient
    /// popover) from the hotkey path (which never does), so only the click path guards against re-open.
    func togglePopover(fromClick: Bool = false) {
        let popover = ensurePopover()
        if popover.isShown {
            popover.performClose(nil)
            return
        }
        if fromClick, let closedAt = popoverClosedAt, Date().timeIntervalSince(closedAt) < 0.3 {
            // This click is the same gesture that just dismissed the popover — leave it closed.
            popoverClosedAt = nil
            return
        }
        showPopover()
    }

    private func showPopover() {
        guard let button = statusItem.button else { return }
        let popover = ensurePopover()
        // Freeze the menu-bar animation while the popover is anchored to the button. (Now that the
        // .attention badge is static and the popover has a fixed size this is no longer load-bearing
        // for the flicker fix, but it's harmless and keeps the anchor button perfectly still.)
        menuBarAnimator?.paused = true
        // Do NOT call NSApp.activate(ignoringOtherApps: true) here. Activating the whole app to show a
        // menu-bar panel, combined with .transient + a delayed makeKey, raced the popover's own
        // transient dismissal and stole first-mouse (part of the "can't click" symptom). We make the
        // popover's own window key in popoverDidShow instead — no global activation.
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
    }

    /// Give the popover's window key focus once it is actually on screen, WITHOUT activating the whole
    /// app — so the Ask field can take first responder while the transient popover stays stable.
    func popoverDidShow(_ notification: Notification) {
        guard let popover = quickPanelPopover, popover.isShown else { return }
        popover.contentViewController?.view.window?.makeKeyAndOrderFront(nil)
        NotificationCenter.default.post(name: .cortexFocusQuickPanel, object: nil)
    }

    func popoverDidClose(_ notification: Notification) {
        popoverClosedAt = Date()
        // Resume the live menu-bar icon now that the popover is no longer anchored to the button.
        menuBarAnimator?.paused = false
    }

    private func quickPanelOpenApp() {
        quickPanelPopover?.performClose(nil)
        showMainWindow()
    }

    private func quickPanelOpenReview() {
        quickPanelPopover?.performClose(nil)
        state.selectedTab = .review
        showMainWindow()
    }

    private func quickPanelOpenConnections() {
        quickPanelPopover?.performClose(nil)
        showMainWindow()
        state.openConnectionsPrivacy()
    }

    // MARK: - Right-click quick menu

    private func showStatusMenu() {
        guard let button = statusItem.button else { return }
        let menu = buildStatusMenu()
        menu.popUp(positioning: nil, at: NSPoint(x: 0, y: button.bounds.height + 5), in: button)
    }

    private func buildStatusMenu() -> NSMenu {
        let menu = NSMenu()
        menu.autoenablesItems = false

        let header = NSMenuItem(title: menuStatusTitle(), action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)
        menu.addItem(.separator())

        // Required-account gate: when the build requires an account and the user is not signed in, the
        // menu must not expose Ask / Review / Sync / Connections / extension-pairing / Copy-API — each
        // operates on account-gated memory or exposes the local API. Offer only sign-in and Quit; the
        // rest returns once signed in. (menuOpenCortex brings up the main window, i.e. the sign-in wall.)
        if state.requiresSignIn {
            addMenuItem(to: menu, title: "Sign in to Doppl…", action: #selector(menuOpenCortex), key: "")
            menu.addItem(.separator())
            addMenuItem(to: menu, title: "Quit \(appDisplayName)", action: #selector(menuQuit), key: "q")
            return menu
        }

        addMenuItem(to: menu, title: "Ask Cortex…  (⌃⌥Space)", action: #selector(menuAskSpotlight), key: "")
        addMenuItem(to: menu, title: "Open Cortex", action: #selector(menuOpenCortex), key: "o")

        let pending = state.review?.stats.pending_captures ?? state.inbox.count
        let reviewTitle = pending > 0 ? "Review (\(pending))" : "Review"
        addMenuItem(to: menu, title: reviewTitle, action: #selector(menuOpenReview), key: "")

        let syncItem = addMenuItem(to: menu, title: "Sync Now", action: #selector(menuSyncNow), key: "")
        syncItem.isEnabled = !(state.syncProgress?.active == true)

        addMenuItem(to: menu, title: "Connections…", action: #selector(menuOpenConnections), key: "")

        let constellationItem = addMenuItem(to: menu, title: "Your Constellation", action: #selector(presentConstellationOverlay), key: "")
        constellationItem.isEnabled = !state.graphNodes.isEmpty

        menu.addItem(.separator())
        // The user's direct "turn this off" control for the bottom-of-screen live-activity surfaces.
        let liveItem = addMenuItem(to: menu, title: "Show live activity", action: #selector(toggleLiveActivity), key: "")
        liveItem.state = state.liveActivityEnabled ? .on : .off

        menu.addItem(.separator())
        // "Use Cortex from other apps": connection actions for the outbound surface.
        let pairItem = addMenuItem(to: menu, title: "Connect browser extension…", action: #selector(menuPairBrowserExtension), key: "")
        pairItem.isEnabled = !state.browserExtensionPairingInFlight
        addMenuItem(to: menu, title: "Copy API connection info…", action: #selector(menuCopyAPIConnectionInfo), key: "")

        menu.addItem(.separator())
        addMenuItem(to: menu, title: "Quit \(appDisplayName)", action: #selector(menuQuit), key: "q")
        return menu
    }

    @objc private func menuPairBrowserExtension() {
        state.pairBrowserExtension()
    }

    @objc private func menuCopyAPIConnectionInfo() {
        state.copyUniversalAPIConnectionInfo()
    }

    @discardableResult
    private func addMenuItem(to menu: NSMenu, title: String, action: Selector, key: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        item.isEnabled = true
        menu.addItem(item)
        return item
    }

    private func menuStatusTitle() -> String {
        if state.syncProgress?.active == true {
            return "Syncing your memory…"
        }
        let pending = state.review?.stats.pending_captures ?? state.inbox.count
        if pending > 0 {
            return "\(pending) item\(pending == 1 ? "" : "s") to review"
        }
        if (state.stats?.memories ?? 0) > 0 {
            return "Memory ready"
        }
        return "No sources connected yet"
    }

    @objc private func menuAskSpotlight() {
        showPopover()
    }

    @objc private func menuOpenCortex() {
        showMainWindow()
    }

    @objc private func menuOpenReview() {
        state.selectedTab = .review
        showMainWindow()
    }

    @objc private func menuOpenAsk() {
        state.selectedTab = .ask
        showMainWindow()
    }

    @objc private func menuSyncNow() {
        state.syncNowFromMenu()
    }

    @objc private func menuOpenConnections() {
        showMainWindow()
        state.openConnectionsPrivacy()
    }

    /// Toggle the bottom-of-screen live-activity surfaces. The coordinator reads this each tick and
    /// hides everything when off.
    @objc private func toggleLiveActivity() {
        state.liveActivityEnabled.toggle()
    }

    /// Summon the full-screen Constellation overlay (P2). Wired to a Home button (via the
    /// .cortexPresentConstellation notification) and the right-click menu. The node "Explore in Ask"
    /// action runs a real Ask for that node and brings the main window forward.
    @objc private func presentConstellationOverlay() {
        // Required-account gate: the Constellation overlay is a sibling NSPanel NOT covered by the
        // main-window wall and displays memory nodes — never show it while the wall is up.
        guard !state.requiresSignIn else { showMainWindow(); return }
        guard !state.graphNodes.isEmpty else {
            // Nothing to show yet — take the user to Home so they see the "learning" empty state.
            showMainWindow()
            state.selectedTab = .model
            return
        }
        ConstellationOverlay.shared.present(state: state) { [weak self] node in
            guard let self else { return }
            self.state.searchQuery = node.label
            self.state.selectedTab = .ask
            self.state.runSearch()
            self.showMainWindow()
        }
    }

    /// App-menu "Settings…" (⌘,). Brings the window forward and requests the Connections & Privacy
    /// surface via the observed `presentSettings` flag (CortexView resets it once it acts).
    @objc private func menuOpenSettings() {
        showMainWindow()
        state.presentSettings = true
    }

    /// Help menu → open bundled help/README if present, else the product site.
    @objc private func menuOpenHelp() {
        let candidates = ["help", "README", "README.md"]
        for name in candidates {
            let base = (name as NSString).deletingPathExtension
            let ext = (name as NSString).pathExtension
            if let url = Bundle.main.url(forResource: base, withExtension: ext.isEmpty ? nil : ext) {
                NSWorkspace.shared.open(url)
                return
            }
        }
        if let site = URL(string: "https://trydoppl.com") {
            NSWorkspace.shared.open(site)
        }
    }

    @objc private func menuQuit() {
        NSApp.terminate(nil)
    }

    // MARK: - Global hotkey (Carbon)

    fileprivate func handleGlobalHotKey() {
        togglePopover()
    }

    private func registerGlobalHotKey() {
        var eventSpec = EventTypeSpec(
            eventClass: OSType(kEventClassKeyboard),
            eventKind: OSType(kEventHotKeyPressed)
        )
        InstallEventHandler(
            GetApplicationEventTarget(),
            cortexHotKeyEventHandler,
            1,
            &eventSpec,
            Unmanaged.passUnretained(self).toOpaque(),
            nil
        )
        let hotKeyID = EventHotKeyID(signature: cortexHotKeySignature, id: 1)
        // ⌃⌥Space — deliberately not a default macOS shortcut (avoids ⌘Space Spotlight and the
        // ⌥⌘Space Finder search-window collision). Failure to register is non-fatal.
        let status = RegisterEventHotKey(
            UInt32(kVK_Space),
            UInt32(controlKey | optionKey),
            hotKeyID,
            GetApplicationEventTarget(),
            0,
            &hotKeyRef
        )
        if status != noErr {
            logApp("global hotkey registration failed (status \(status))")
        }
    }

    private func unregisterGlobalHotKey() {
        if let hotKeyRef {
            UnregisterEventHotKey(hotKeyRef)
            self.hotKeyRef = nil
        }
    }

    // A comfortable, desktop-app-sized default derived from the current screen: large on big
    // displays, still fitting on small laptops. Cortex is a multi-tab knowledge app, so it should
    // open roomy rather than as a compact utility window.
    private static func preferredDefaultWindowSize() -> NSSize {
        let visible = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        // Cortex is a full knowledge workspace, so it should open large — taking up most of the
        // screen — rather than as a compact utility window. Caps keep it sensible on huge displays.
        let width = min(1680, max(1200, visible.width * 0.88))
        let height = min(1080, max(820, visible.height * 0.90))
        return NSSize(width: width, height: height)
    }

    private func setupMainWindow() {
        logApp("setupMainWindow")
        let defaultSize = Self.preferredDefaultWindowSize()
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: defaultSize.width, height: defaultSize.height),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Cortex"
        // Unified paper titlebar: the transparent titlebar + hidden system title + paper background
        // let the Archive canvas run edge-to-edge under the traffic lights, with no gray system bar
        // (the hallmark of a premium Mac app). Content still lays out below the titlebar, so the
        // custom header never collides with the traffic-light buttons.
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.backgroundColor = NSColor(srgbRed: 0xF7 / 255, green: 0xF4 / 255, blue: 0xED / 255, alpha: 1)
        window.minSize = NSSize(width: 820, height: 640)
        window.isReleasedWhenClosed = false
        window.hidesOnDeactivate = false
        window.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        window.level = .normal
        window.delegate = self
        window.contentViewController = NSHostingController(rootView: CortexView(state: state))
        // Bumped autosave name so the new larger default replaces any previously-saved small
        // frame once; subsequent user resizes persist under this name.
        window.setFrameAutosaveName("CortexMainWindowV3")
        // If no frame was restored from the (new) autosave name, open at the roomy default.
        if window.frame.width < defaultSize.width || window.frame.height < defaultSize.height {
            window.setContentSize(defaultSize)
        }
        window.center()
        mainWindow = window
        mainWindowController = NSWindowController(window: window)
        ensureMainWindowIsVisible()
        logWindowState("after setupMainWindow")
    }

    @objc private func toggleMainWindow() {
        if mainWindow.isVisible && NSApp.isActive {
            mainWindow.orderOut(nil)
        } else {
            showMainWindow()
        }
    }

    @objc private func onboardingCompleted() {
        mainWindow?.level = .normal
        mainWindow?.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        showMainWindow()
    }

    private func showMainWindow() {
        if mainWindow == nil {
            setupMainWindow()
        }
        ensureMainWindowIsVisible()
        mainWindow.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        mainWindow.level = .normal
        NSApp.unhide(nil)
        mainWindow.deminiaturize(nil)
        mainWindowController.showWindow(nil)
        mainWindow.makeKeyAndOrderFront(NSApp)
        mainWindow.orderFrontRegardless()
        NSRunningApplication.current.activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
        NSApp.activate(ignoringOtherApps: true)
        logWindowState("after showMainWindow")
    }

    private func ensureMainWindowIsVisible() {
        guard let window = mainWindow else { return }
        let frame = window.frame
        let visibleFrames = NSScreen.screens.map(\.visibleFrame)
        let preferredScreenFrame = preferredVisibleFrame()
        let visibleEnough = visibleFrames.contains { screenFrame in
            let intersection = screenFrame.intersection(frame)
            return hasUsefulIntersection(intersection, for: frame)
        }
        let visibleOnPreferredScreen = {
            let intersection = preferredScreenFrame.intersection(frame)
            return self.hasUsefulIntersection(intersection, for: frame)
        }
        let tooSmall = frame.width < window.minSize.width || frame.height < window.minSize.height
        guard !visibleEnough || tooSmall || !visibleOnPreferredScreen() else { return }

        center(window, in: preferredScreenFrame)
        logWindowState("after ensureMainWindowIsVisible reset")
    }

    private func center(_ window: NSWindow, in screenFrame: NSRect) {
        let preferred = Self.preferredDefaultWindowSize()
        let width = min(max(window.minSize.width, preferred.width), screenFrame.width - 80)
        let height = min(max(window.minSize.height, preferred.height), screenFrame.height - 80)
        let x = screenFrame.midX - width / 2
        let y = screenFrame.midY - height / 2
        window.setFrame(NSRect(x: x, y: y, width: width, height: height), display: true)
    }

    private func logWindowState(_ context: String) {
        guard let window = mainWindow else {
            logApp("\(context): no mainWindow")
            return
        }
        logApp("\(context): visible=\(window.isVisible) key=\(window.isKeyWindow) mini=\(window.isMiniaturized) frame=\(NSStringFromRect(window.frame)) appActive=\(NSApp.isActive) windows=\(NSApp.windows.count)")
    }

    private func hasUsefulIntersection(_ intersection: NSRect, for frame: NSRect) -> Bool {
        intersection.width >= min(320, frame.width * 0.6)
            && intersection.height >= min(360, frame.height * 0.6)
    }

    private func preferredVisibleFrame() -> NSRect {
        let mouseLocation = NSEvent.mouseLocation
        if let mouseScreen = NSScreen.screens.first(where: { NSMouseInRect(mouseLocation, $0.frame, false) }) {
            return mouseScreen.visibleFrame
        }
        return NSScreen.main?.visibleFrame
            ?? NSScreen.screens.first?.visibleFrame
            ?? NSRect(x: 0, y: 0, width: 1200, height: 800)
    }

    private func defaultLaunchVisibleFrame() -> NSRect {
        if let primaryScreen = NSScreen.screens.min(by: { lhs, rhs in
            let lhsDistance = abs(lhs.frame.origin.x) + abs(lhs.frame.origin.y)
            let rhsDistance = abs(rhs.frame.origin.x) + abs(rhs.frame.origin.y)
            return lhsDistance < rhsDistance
        }) {
            return primaryScreen.visibleFrame
        }
        return NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1200, height: 800)
    }

    private func logApp(_ message: String) {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        let directory = base.appendingPathComponent("Cortex", isDirectory: true)
        let url = directory.appendingPathComponent("cortex-app.log")
        let text = "[CortexApp] \(Date()) \(message)\n"
        guard let data = text.data(using: .utf8) else { return }
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            if FileManager.default.fileExists(atPath: url.path),
               let handle = try? FileHandle(forWritingTo: url) {
                try handle.seekToEnd()
                try handle.write(contentsOf: data)
                try handle.close()
            } else {
                try data.write(to: url)
            }
        } catch {
            // Launch logging should never block app startup.
        }
    }
}

// FourCharCode 'CXTX' identifying the Cortex global hotkey.
private let cortexHotKeySignature = OSType(0x43585458)

/// Carbon C event handler for the global hotkey. It runs as a bare C function pointer (no captured
/// context), recovers the AppDelegate from userData, and hops to the main actor to toggle the
/// Spotlight popover.
private func cortexHotKeyEventHandler(
    _ nextHandler: EventHandlerCallRef?,
    _ event: EventRef?,
    _ userData: UnsafeMutableRawPointer?
) -> OSStatus {
    guard let userData else { return noErr }
    let delegate = Unmanaged<AppDelegate>.fromOpaque(userData).takeUnretainedValue()
    Task { @MainActor in
        delegate.handleGlobalHotKey()
    }
    return noErr
}

@main
enum CortexApplication {
    private static var delegate: AppDelegate!

    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}
