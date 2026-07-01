import AppKit
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
        authKind == "oauth" || connectorReadinessStatus == "live-planned"
    }
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
        case "empty": return .orange
        case "needs_review": return .yellow
        case "synced": return .green
        case "connected": return .blue
        case "imported": return .accentColor
        case "import_ready": return .purple
        case "planned": return .secondary
        default: return .secondary
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
    static let cortexHotkeyPreferenceChanged = Notification.Name("CortexHotkeyPreferenceChanged")
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
}

struct GraphEdge: Codable, Identifiable, Hashable {
    let id: String
    let source_id: String
    let target_id: String
    let kind: String
    let weight: Double?
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
            return "That tool connection could not be updated automatically. Open Advanced settings, then MCP config."
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

    func ensureRunning(endpoint: String, apiKey: String, mcpAPIKey: String, vaultPath: String) async -> String {
        let normalizedEndpoint = endpoint.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard normalizedEndpoint.contains("127.0.0.1") || normalizedEndpoint.contains("localhost") else {
            return "Using remote memory engine"
        }
        let initialHealth = await healthCheck(endpoint: normalizedEndpoint, apiKey: apiKey, expectedVaultPath: vaultPath)
        if initialHealth == .healthy {
            return "Local memory engine connected"
        }
        if let process, process.isRunning {
            for _ in 0..<90 {
                if await healthCheck(endpoint: normalizedEndpoint, apiKey: apiKey, expectedVaultPath: vaultPath) == .healthy {
                    return "Local memory engine started"
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
            for _ in 0..<90 {
                if await healthCheck(endpoint: normalizedEndpoint, apiKey: apiKey, expectedVaultPath: vaultPath) == .healthy {
                    return "Local memory engine started"
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
        let moduleURL = backendURL.appendingPathComponent("app/standalone_server.py")
        guard FileManager.default.fileExists(atPath: moduleURL.path) else {
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
        writeLog("Python executable: \(pythonURL.path)")
        launched.executableURL = pythonURL
        if pythonURL.lastPathComponent == "env" {
            launched.arguments = ["python3", "-S", "-m", "app.standalone_server", "--host", "127.0.0.1", "--port", "8766"]
        } else {
            launched.arguments = ["-S", "-m", "app.standalone_server", "--host", "127.0.0.1", "--port", "8766"]
        }
        launched.currentDirectoryURL = backendURL
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = backendURL.path
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["CORTEX_VAULT_PATH"] = vaultURL.path
        environment["CORTEX_DB_PATH"] = dbURL.path
        environment["CORTEX_API_KEY"] = normalizedAPIKey
        environment["CORTEX_MCP_API_KEY"] = normalizedMCPAPIKey
        environment["CORTEX_MCP_API_KEY_SCOPES"] = "read,write,export,maintenance"
        environment["CORTEX_PUBLIC_BASE_URL"] = "http://127.0.0.1:8766"
        environment["PATH"] = "/Library/Frameworks/Python.framework/Versions/3.12/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
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

    private func pythonExecutableURL() -> URL {
        let bundledPython = Bundle.main.privateFrameworksURL?
            .appendingPathComponent("Python.framework/Versions/3.12/bin/python3")
            .path
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

private enum CortexCredentialStore {
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
            kSecAttrAccount as String: key
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
    private static let obsidianVaultPathDefaultsKey = "connectedObsidianVaultPath.v1"
    private static let obsidianVaultBookmarkDefaultsKey = "connectedObsidianVaultBookmark.v1"

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

    private static func generateSecret(prefix: String) -> String {
        var generator = SystemRandomNumberGenerator()
        let bytes = (0..<32).map { _ in UInt8.random(in: UInt8.min...UInt8.max, using: &generator) }
        return prefix + bytes.map { String(format: "%02x", $0) }.joined()
    }

    @Published var endpoint: String = UserDefaults.standard.string(forKey: "endpoint") ?? "http://127.0.0.1:8766"
    @Published var apiKey: String = AppState.loadOrCreateAPIKey()
    @Published var mcpAPIKey: String = AppState.loadOrCreateMCPAPIKey()
    @Published var importHistory: [SourceImportHistoryItem] = []
    @Published var sourceConnectorCatalog: [SourceConnectorCatalogItem] = []
    @Published var sourceReadinessReport: SourceReadinessResponse?
    @Published var sourceAccounts: [SourceAccountItem] = []
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
    @Published var stats: StatsResponse?
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
    @Published var globalClipboardHotkeyEnabled: Bool = UserDefaults.standard.bool(forKey: "globalClipboardHotkeyEnabled.v1")
    @Published var onboardingComplete: Bool = UserDefaults.standard.bool(forKey: "onboardingComplete.v1")
    @Published var showOnboarding: Bool = false
    @Published var showConnectionsPrivacy: Bool = false
    @Published var onboardingStep: OnboardingStep = OnboardingStep(rawValue: UserDefaults.standard.integer(forKey: "onboardingStep.v2")) ?? .privateVault
    @Published var firstSourceAdded: Bool = UserDefaults.standard.bool(forKey: "onboardingFirstSourceImported.v1")
    @Published var firstMemoryReviewed: Bool = UserDefaults.standard.bool(forKey: "onboardingFirstMemoryReviewed.v1")
    @Published var cortexUsed: Bool = UserDefaults.standard.bool(forKey: "onboardingCortexUsed.v1")
    @Published var onboardingFirstSourceNames: [String] = UserDefaults.standard.stringArray(forKey: "onboardingFirstSourceNames.v1") ?? []
    @Published var onboardingBackupDecision: String = UserDefaults.standard.string(forKey: "onboardingBackupDecision.v1") ?? ""
    @Published var integrationStates: [String: AIIntegrationState] = [:]
    @Published var isBusy: Bool = false
    @Published var connectorSyncingIDs: Set<String> = []
    @Published var connectorLastMessages: [String: String] = [:]
    @Published var configuredDirectConnectorIDs: Set<String> = []

    private let backend = BackendSupervisor.shared
    private var obsidianAutoSyncTask: Task<Void, Never>?
    private var directConnectorAutoSyncTask: Task<Void, Never>?
    private var obsidianSyncInFlight = false
    private var onboardingDismissedForSession = false

    private static let directConnectorConfigSecretPrefix = "directConnectorConfig.v1."
    private static let directConnectorSyncIDs: Set<String> = [
        "calendar",
        "github",
        "jira",
        "linear",
        "notion",
        "raindrop",
        "readwise",
        "slack",
        "zotero"
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

    var onboardingHasSyncedMemory: Bool {
        if !inbox.isEmpty || (stats?.pending_captures ?? 0) > 0 || (stats?.memories ?? 0) > 0 {
            return true
        }
        return sourceReadinessReport?.sources.contains { source in
            let hasUsableData = source.captures > 0
                || source.pending > 0
                || source.approved > 0
                || source.active_memories > 0
            return hasUsableData && ["needs_review", "synced", "imported"].contains(source.status)
        } ?? false
    }

    var onboardingHasSource: Bool {
        onboardingHasConnectedMemoryLayer && onboardingHasSyncedMemory
    }

    var hasConnectedSourceAccount: Bool {
        sourceAccounts.contains { account in
            account.disconnected_at == nil
        }
    }

    var activeSourceAccounts: [SourceAccountItem] {
        sourceAccounts.filter { $0.disconnected_at == nil }
    }

    var hasConnectedObsidianVault: Bool {
        storedObsidianVaultURL() != nil
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
            return onboardingHasSource
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
            return isLocalServiceReady
        case .firstSource:
            return onboardingStepIsComplete(.firstSource)
        case .reviewMemory:
            return onboardingStepIsComplete(.reviewMemory)
        case .askUse:
            return onboardingStepIsComplete(.askUse)
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

    func saveHotkeyPreference() {
        UserDefaults.standard.set(globalClipboardHotkeyEnabled, forKey: "globalClipboardHotkeyEnabled.v1")
        NotificationCenter.default.post(name: .cortexHotkeyPreferenceChanged, object: nil)
        status = globalClipboardHotkeyEnabled ? "Global clipboard hotkey enabled" : "Global clipboard hotkey disabled"
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
        refreshStoredConnectorConfigState()
        refreshIntegrationStates()
        startConnectedSourceAutoSync()
        presentOnboardingIfNeeded()
    }

    func ensureBackend() async {
        ensureUsableAPIKey()
        ensureUsableMCPAPIKey()
        backendStatus = "Checking memory engine"
        let message = await backend.ensureRunning(endpoint: endpoint, apiKey: apiKey, mcpAPIKey: mcpAPIKey, vaultPath: vaultPath)
        backendStatus = message
        status = message
        _ = await registerMCPToken()
    }

    private func registerMCPToken() async -> Bool {
        do {
            _ = try await performRequest(
                path: "/v1/integrations/mcp-token",
                method: "POST",
                body: [
                    "token": mcpAPIKey,
                    "label": "Connected AI tools",
                    "scopes": ["read", "write", "export", "maintenance"]
                ]
            )
            return true
        } catch {
            status = CortexRecoveryText.failureStatus("tool access registration", error: error)
            return false
        }
    }

    private func ensureUsableAPIKey() {
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

    func captureClipboard() {
        let text = NSPasteboard.general.string(forType: .string) ?? ""
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            status = "Clipboard is empty"
            notify("Cortex", "Clipboard is empty")
            return
        }
        Task {
            if await capture(text: text, source: "macos-clipboard", title: "Clipboard note") {
                status = "Clipboard note saved to Review"
            }
        }
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
            status = CortexRecoveryText.failureStatus("Source sync history", error: error)
        }
    }

    func runSearch() {
        Task { await search() }
    }

    func search() async {
        let q = searchQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        if q.isEmpty {
            searchResults = []
            askAnswer = ""
            askCitations = []
            hasSearched = false
            status = "Enter a search term"
            return
        }
        isBusy = true
        status = "Searching..."
        defer { isBusy = false }
        do {
            let encoded = q.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? q
            let data = try await request(path: "/v1/ask?query=\(encoded)&limit=12", method: "GET")
            let answer = try JSONDecoder().decode(AskResponse.self, from: data)
            searchResults = answer.results
            askAnswer = answer.answer
            askCitations = answer.citations
            hasSearched = true
            status = searchResults.isEmpty ? "No cited memory found" : "Found \(answer.citations.count) citation\(answer.citations.count == 1 ? "" : "s")"
            if hasUsableOnboardingCitation(answer.citations) {
                markCortexUsed()
            }
        } catch {
            hasSearched = true
            askAnswer = ""
            askCitations = []
            status = CortexRecoveryText.failureStatus("Search", error: error)
        }
    }

    func loadGraph() async {
        do {
            let data = try await request(path: "/v1/graph?limit=160", method: "GET")
            let graph = try JSONDecoder().decode(GraphResponse.self, from: data)
            graphNodes = graph.nodes
            graphEdges = graph.edges
        } catch {
            status = CortexRecoveryText.failureStatus("Graph", error: error)
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

    func loadMemoryQuality() async {
        do {
            let data = try await request(path: "/v1/memory/quality", method: "GET")
            memoryQuality = try JSONDecoder().decode(MemoryQualityResponse.self, from: data)
        } catch {
            status = CortexRecoveryText.failureStatus("Quality", error: error)
        }
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
            openConnectionsPrivacy(statusMessage: "Start source sync")
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
        showConnectionsPrivacy = true
        status = statusMessage
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
        do {
            let summaryData = try await request(path: "/v1/trust/summary", method: "GET")
            trustSummary = try JSONDecoder().decode(TrustSummaryResponse.self, from: summaryData)
            let lifecycleData = try await request(path: "/v1/privacy/lifecycle", method: "GET")
            dataLifecycleReport = try JSONDecoder().decode(DataLifecycleReportResponse.self, from: lifecycleData)
            let auditData = try await request(path: "/v1/audit-log?limit=80", method: "GET")
            auditEvents = try JSONDecoder().decode(AuditLogResponse.self, from: auditData).results
            await loadIntegrationTokens()
            await loadSourceConnectivity()
        } catch {
            status = CortexRecoveryText.failureStatus("Trust", error: error)
        }
    }

    func loadSourceConnectivity() async {
        do {
            let catalogData = try await request(path: "/v1/source-accounts/catalog", method: "GET")
            sourceConnectorCatalog = try JSONDecoder().decode(SourceConnectorCatalogResponse.self, from: catalogData).results
            let accountData = try await request(path: "/v1/source-accounts", method: "GET")
            sourceAccounts = try JSONDecoder().decode(SourceAccountListResponse.self, from: accountData).results
            let cursorData = try await request(path: "/v1/sync-cursors", method: "GET")
            syncCursors = try JSONDecoder().decode(SyncCursorListResponse.self, from: cursorData).results
            do {
                let deviceData = try await request(path: "/v1/sync/devices?include_revoked=true", method: "GET")
                let devices = try JSONDecoder().decode(SyncDeviceListResponse.self, from: deviceData).results
                syncDevices = devices
                syncReceiptsByDevice = await loadSyncReceipts(for: devices)
            } catch {
                syncDevices = []
                syncReceiptsByDevice = [:]
            }
            do {
                let readinessData = try await request(path: "/v1/sources/readiness", method: "GET")
                sourceReadinessReport = try JSONDecoder().decode(SourceReadinessResponse.self, from: readinessData)
            } catch {
                sourceReadinessReport = nil
            }
        } catch {
            sourceConnectorCatalog = []
            sourceReadinessReport = nil
            sourceAccounts = []
            syncCursors = []
            syncDevices = []
            syncReceiptsByDevice = [:]
        }
    }

    private func startConnectedSourceAutoSync(initialSync: Bool = true) {
        obsidianAutoSyncTask?.cancel()
        directConnectorAutoSyncTask?.cancel()
        if storedObsidianVaultURL() != nil {
            obsidianAutoSyncTask = Task { [weak self] in
                if initialSync {
                    await self?.syncSavedObsidianVaultIfAvailable(automatic: true)
                }
                while !Task.isCancelled {
                    do {
                        try await Task.sleep(nanoseconds: 30 * 60 * 1_000_000_000)
                    } catch {
                        return
                    }
                    await self?.syncSavedObsidianVaultIfAvailable(automatic: true)
                }
            }
        }
        guard !configuredDirectConnectorIDs.isEmpty else { return }
        directConnectorAutoSyncTask = Task { [weak self] in
            if initialSync {
                await self?.syncConfiguredDirectConnectorsIfAvailable(automatic: true)
            }
            while !Task.isCancelled {
                do {
                    try await Task.sleep(nanoseconds: 30 * 60 * 1_000_000_000)
                } catch {
                    return
                }
                await self?.syncConfiguredDirectConnectorsIfAvailable(automatic: true)
            }
        }
    }

    private func syncSavedObsidianVaultIfAvailable(automatic: Bool) async {
        guard let folderURL = storedObsidianVaultURL(),
              let connector = sourceConnectorCatalog.first(where: { $0.id == "obsidian" }) else {
            return
        }
        await syncLocalNotesFolder(connector, folderURL: folderURL, rememberPath: false, automatic: automatic)
    }

    private func syncConfiguredDirectConnectorsIfAvailable(automatic: Bool) async {
        let configuredIDs = configuredDirectConnectorIDs.sorted()
        guard !configuredIDs.isEmpty else { return }
        for connectorID in configuredIDs {
            guard let connector = sourceConnectorCatalog.first(where: { $0.id == connectorID }),
                  let payload = storedDirectConnectorPayload(for: connectorID) else {
                continue
            }
            await syncDirectConnector(connector, payload: payload, automatic: automatic)
        }
    }

    func connectLocalNotesFolder(_ connector: SourceConnectorCatalogItem, chooseNew: Bool = false) {
        guard connector.id == "obsidian" else {
            status = "Open Connections & Privacy to connect \(connector.name)"
            return
        }

        if !chooseNew, let storedURL = storedObsidianVaultURL() {
            Task { await syncLocalNotesFolder(connector, folderURL: storedURL, rememberPath: false) }
            return
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

    func syncStoredDirectConnector(_ connector: SourceConnectorCatalogItem) {
        guard let payload = storedDirectConnectorPayload(for: connector.id) else {
            status = "Set up \(connector.name) before syncing again"
            return
        }
        Task {
            await syncDirectConnector(connector, payload: payload)
        }
    }

    func hasStoredDirectConnectorConfig(_ connector: SourceConnectorCatalogItem) -> Bool {
        configuredDirectConnectorIDs.contains(connector.id) || storedDirectConnectorPayload(for: connector.id) != nil
    }

    func forgetDirectConnectorConfig(_ connector: SourceConnectorCatalogItem) {
        CortexCredentialStore.removeSecret(forKey: Self.directConnectorConfigSecretKey(for: connector.id))
        connectorLastMessages[connector.id] = nil
        refreshStoredConnectorConfigState()
        startConnectedSourceAutoSync(initialSync: false)
        status = "\(connector.name) automatic sync setup forgotten"
    }

    func syncDirectConnector(_ connector: SourceConnectorCatalogItem, payload: [String: Any], rememberPayload: Bool = false, automatic: Bool = false) async {
        guard Self.directConnectorSyncIDs.contains(connector.id) else {
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
                requestBody["processing"] = "sync"
            }
            let syncData = try await request(
                path: "/v1/connectors/\(connector.id)/sync",
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
            await loadSourceConnectivity()
            if !automatic {
                await loadTrust()
            }
            await refreshAfterCapture()

            let changed = synced.saved + synced.queued
            let message: String
            if changed > 0 {
                message = "\(connector.name) synced \(changed) item\(changed == 1 ? "" : "s") into Review"
            } else if synced.skipped > 0 || synced.received > 0 {
                message = "\(connector.name) already up to date"
            } else {
                message = "\(connector.name) sync finished"
            }
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

    private static func directConnectorConfigSecretKey(for connectorID: String) -> String {
        directConnectorConfigSecretPrefix + connectorID
    }

    private func refreshStoredConnectorConfigState() {
        configuredDirectConnectorIDs = Set(Self.directConnectorSyncIDs.filter { storedDirectConnectorPayload(for: $0) != nil })
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
        defer {
            obsidianSyncInFlight = false
            if !automatic {
                isBusy = false
            }
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
            let syncData = try await request(
                path: "/v1/connectors/obsidian/sync",
                method: "POST",
                body: [
                    "vault_path": folderURL.standardizedFileURL.path,
                    "max_records": 5000,
                    "processing": "sync"
                ]
            )
            let synced = try JSONDecoder().decode(ObsidianConnectorSyncResponse.self, from: syncData)
            guard synced.scan.records_found > 0, synced.scan.records_returned > 0 else {
                await loadSourceConnectivity()
                await loadTrust()
                if !automatic {
                    status = "No usable content found in \(synced.scan.vault_name). Choose a source with real content."
                }
                return
            }

            if rememberPath {
                rememberObsidianVaultPath(folderURL)
                startConnectedSourceAutoSync(initialSync: false)
            }
            firstSourceAdded = true
            onboardingFirstSourceNames = Array(Set(onboardingFirstSourceNames + [connector.name])).sorted()
            UserDefaults.standard.set(true, forKey: "onboardingFirstSourceImported.v1")
            UserDefaults.standard.set(onboardingFirstSourceNames, forKey: "onboardingFirstSourceNames.v1")
            await loadSourceConnectivity()
            await loadTrust()
            await refreshAfterCapture()

            if synced.scan.truncated == true {
                status = "\(connector.name) synced \(synced.scan.records_returned) of \(synced.scan.records_found) notes. Larger-vault sync is partial."
            } else if synced.saved > 0 || synced.queued > 0 {
                let count = synced.saved + synced.queued
                status = "\(connector.name) synced \(count) note\(count == 1 ? "" : "s") into Review"
            } else if synced.skipped > 0, !automatic {
                status = "\(connector.name) already up to date"
            } else if !automatic {
                status = "\(connector.name) sync finished"
            }
        } catch {
            if automatic {
                status = CortexRecoveryText.failureStatus("\(connector.name) background sync", error: error)
            } else {
                status = CortexRecoveryText.failureStatus("\(connector.name) sync", error: error)
            }
        }
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
        mcpAPIKey = AppState.generateMCPAPIKey()
        CortexCredentialStore.saveSecret(mcpAPIKey, forKey: Self.mcpAPIKeyDefaultsKey)
        let registered = await registerMCPToken()
        await loadIntegrationTokens()
        refreshIntegrationStates()
        if registered {
            status = "Tool access reset. Reconnect detected AI tools or use fallback connection details if an app asks."
        }
    }

    func copyMCPConfig() {
        copyMCPConfig(for: nil)
    }

    func copyMCPConfig(for integration: AIIntegration?) {
        ensureUsableMCPAPIKey()
        let text = mcpConfigJSON()
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
            for target in integration.configTargets {
                try mergeMCPConfig(at: target.url)
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
                for target in integration.configTargets {
                    try mergeMCPConfig(at: target.url)
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
                configHasVerifiedCortexServer(at: target.url)
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
        let verified = integration.configTargets.filter { configHasVerifiedCortexServer(at: $0.url) }
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

    private func mcpServerDefinition(redactToken: Bool = false) -> [String: Any] {
        ensureUsableMCPAPIKey()
        let scriptURL = Bundle.main.resourceURL?
            .appendingPathComponent("scripts", isDirectory: true)
            .appendingPathComponent("cortex_mcp_stdio.py")
        let scriptPath = scriptURL?.path ?? "/path/to/cortex_mcp_stdio.py"
        return [
            "command": "/usr/bin/python3",
            "args": [scriptPath],
            "env": [
                "CORTEX_BASE_URL": endpoint,
                "CORTEX_API_KEY": redactToken ? "<copy-secret-connection-details>" : mcpAPIKey
            ]
        ]
    }

    private func mcpConfigJSON(redactToken: Bool = false) -> String {
        let config: [String: Any] = [
            "mcpServers": [
                "cortex": mcpServerDefinition(redactToken: redactToken)
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

    private func mergeMCPConfig(at url: URL) throws {
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
        servers["cortex"] = mcpServerDefinition()
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

    private func configHasVerifiedCortexServer(at url: URL) -> Bool {
        guard let data = try? Data(contentsOf: url),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let servers = root["mcpServers"] as? [String: Any],
              let cortex = servers["cortex"] as? [String: Any] else {
            return false
        }
        guard let command = cortex["command"] as? String,
              command == "/usr/bin/python3",
              let args = cortex["args"] as? [String],
              let scriptPath = args.first,
              scriptPath.hasSuffix("cortex_mcp_stdio.py"),
              FileManager.default.fileExists(atPath: scriptPath),
              let env = cortex["env"] as? [String: String] else {
            return false
        }
        let configuredBaseURL = (env["CORTEX_BASE_URL"] ?? "").trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let expectedBaseURL = endpoint.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let configuredToken = (env["CORTEX_API_KEY"] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return configuredBaseURL == expectedBaseURL && !configuredToken.isEmpty && configuredToken == mcpAPIKey
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

    func presentOnboardingIfNeeded() {
        guard !onboardingComplete, !onboardingDismissedForSession, !showOnboarding else { return }
        setOnboardingStep(firstIncompleteOnboardingStep())
        showOnboarding = true
        status = "Finish the first memory loop to activate Cortex."
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
                status = "Start source sync, then review memory"
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
        Task {
            do {
                _ = try await request(path: "/v1/captures/\(capture.id)/approve", method: "POST")
                status = "Approved review item"
                markFirstMemoryReviewed(capture: capture)
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
                status = CortexRecoveryText.failureStatus("Approve", error: error)
            }
        }
    }

    func archiveCapture(_ capture: CaptureItem) {
        Task {
            do {
                _ = try await request(path: "/v1/captures/\(capture.id)/archive", method: "POST")
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
                status = CortexRecoveryText.failureStatus("Archive", error: error)
            }
        }
    }

    func approveCaptures(_ captures: [CaptureItem]) {
        let visibleCaptures = Array(captures.prefix(10))
        guard !visibleCaptures.isEmpty else { return }
        Task {
            do {
                for capture in visibleCaptures {
                    _ = try await request(path: "/v1/captures/\(capture.id)/approve", method: "POST")
                    markFirstMemoryReviewed(capture: capture)
                }
                status = "Approved \(visibleCaptures.count) visible review item\(visibleCaptures.count == 1 ? "" : "s")"
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
                status = CortexRecoveryText.failureStatus("Batch approve", error: error)
            }
        }
    }

    func archiveCaptures(_ captures: [CaptureItem]) {
        let visibleCaptures = Array(captures.prefix(10))
        guard !visibleCaptures.isEmpty else { return }
        Task {
            do {
                for capture in visibleCaptures {
                    _ = try await request(path: "/v1/captures/\(capture.id)/archive", method: "POST")
                }
                status = "Archived \(visibleCaptures.count) visible review item\(visibleCaptures.count == 1 ? "" : "s")"
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
                status = CortexRecoveryText.failureStatus("Batch archive", error: error)
            }
        }
    }

    func deleteMemory(_ memory: MemoryItem) {
        Task {
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
                    status = "Removed \(response.deleted_captures) review item\(response.deleted_captures == 1 ? "" : "s") from source sync"
                } else {
                    status = "Source sync already removed"
                }
                await refreshAfterCapture()
            } catch {
                status = CortexRecoveryText.failureStatus("Remove source sync", error: error)
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

    private func request(path: String, method: String, body: [String: Any]? = nil) async throws -> Data {
        do {
            return try await performRequest(path: path, method: method, body: body)
        } catch {
            await ensureBackend()
            return try await performRequest(path: path, method: method, body: body)
        }
    }

    private func performRequest(path: String, method: String, body: [String: Any]? = nil) async throws -> Data {
        guard let url = URL(string: endpoint.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + path) else {
            throw URLError(.badURL)
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
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

struct CortexView: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(spacing: 0) {
            header
            TabView(selection: $state.selectedTab) {
                ModelTab(state: state)
                    .tabItem { Label("Home", systemImage: "circle.grid.cross") }
                    .tag(AppTab.model)
                ReviewTab(state: state)
                    .tabItem { Label("Review", systemImage: "checklist") }
                    .tag(AppTab.review)
                AskTab(state: state)
                    .tabItem { Label("Ask", systemImage: "magnifyingglass") }
                    .tag(AppTab.ask)
            }
            footer
        }
        .background(CortexDesign.appBackground)
        .preferredColorScheme(.light)
        .accentColor(CortexDesign.accent)
        .frame(minWidth: 560, minHeight: 640)
        .sheet(isPresented: $state.showOnboarding) {
            OnboardingView(state: state)
                .preferredColorScheme(.light)
                .accentColor(CortexDesign.accent)
                .frame(width: 760, height: 660)
        }
        .sheet(isPresented: $state.showConnectionsPrivacy) {
            ConnectionsPrivacySheet(state: state)
                .preferredColorScheme(.light)
                .accentColor(CortexDesign.accent)
                .frame(width: 840, height: 720)
        }
    }

    private var header: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                HStack(spacing: 8) {
                    Image(systemName: "brain.head.profile")
                        .foregroundColor(.accentColor)
                    Text("Cortex")
                        .font(.headline)
                        .fontWeight(.semibold)
                }
                Spacer()
                Button {
                    state.openConnectionsPrivacy()
                } label: {
                    Label("Connections", systemImage: "lock.shield")
                        .labelStyle(.titleAndIcon)
                        .frame(minHeight: 36)
                }
                .buttonStyle(.bordered)
                .controlSize(.regular)
                CortexLayerStatusPill(state: state)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .background(CortexDesign.appBackground)
    }

    private var footer: some View {
        Group {
            if state.isBusy || footerNeedsAttention {
                HStack {
                    if state.isBusy {
                        ProgressView().scaleEffect(0.7)
                    }
                    Text(state.displayStatus)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                    Spacer()
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(CortexDesign.panelBackground)
            }
        }
    }

    private var footerNeedsAttention: Bool {
        CortexRecoveryText.needsAttention(state.displayStatus)
    }
}

struct CortexLayerStatusPill: View {
    @ObservedObject var state: AppState

    private var activeAccounts: Int {
        state.sourceAccounts.filter { account in
            account.disconnected_at == nil
                && account.status.lowercased() != "empty"
                && account.auth_state.lowercased() != "needs-content"
        }.count
    }

    private var pending: Int {
        state.review?.stats.pending_captures ?? state.inbox.count
    }

    private var label: String {
        if pending > 0 {
            return "\(pending) to review"
        }
        if (state.stats?.memories ?? 0) > 0 {
            return "Memory ready"
        }
        if activeAccounts > 0 {
            return activeAccounts == 1 ? "1 source syncing" : "\(activeAccounts) sources syncing"
        }
        return "No sources"
    }

    private var icon: String {
        if pending > 0 { return "tray.full.fill" }
        if (state.stats?.memories ?? 0) > 0 { return "sparkle.magnifyingglass" }
        if activeAccounts > 0 { return "arrow.triangle.2.circlepath" }
        return "circle.dashed"
    }

    private var color: Color {
        if pending > 0 { return .orange }
        if (state.stats?.memories ?? 0) > 0 { return .accentColor }
        if activeAccounts > 0 { return .green }
        return .secondary
    }

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: icon)
                .foregroundColor(color)
            Text(label)
                .font(.caption)
                .fontWeight(.medium)
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .background(CortexDesign.panelBackground)
        .clipShape(Capsule())
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
            Text(compact ? "Connect local AI tools so reviewed memory is available where you already work." : "Connect local tools so reviewed memory is available where you work. Advanced MCP config stays collapsed unless an app asks for it.")
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
        DisclosureGroup("Advanced MCP config") {
            VStack(alignment: .leading, spacing: 8) {
                Text("Most tools connect automatically. Open this only when a local AI app asks for connection details.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                HStack {
                    Button {
                        state.copyMCPConfig()
                    } label: {
                        Label("Copy MCP config", systemImage: "doc.on.doc")
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
            return "\(warning) · \(memoryText) · \(reviewText)"
        }
        return "\(connectionAction) · \(memoryText) · \(reviewText)"
    }

    private var connectionAction: String {
        switch readinessStatus {
        case "live-planned":
            return "Direct connection planned"
        case "import-ready":
            return "Ready to connect"
        case "export-only":
            return "Advanced connection only"
        default:
            return source.next_action
        }
    }

    private var readinessStatus: String {
        let explicit = (source.readiness_status ?? "").lowercased()
        if ["export-only", "import-ready", "live-planned"].contains(explicit) {
            return explicit
        }
        if source.live_status.lowercased() == "planned" {
            return "live-planned"
        }
        if source.supports_import == true || ["native", "generic", "import_ready"].contains(source.import_status.lowercased()) || !source.formats.isEmpty {
            return "import-ready"
        }
        return "export-only"
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
                SourceConnectivityMetric(title: "Connected", value: "\(state.sourceAccounts.count)", systemImage: "link.circle.fill", color: state.sourceAccounts.isEmpty ? .secondary : .green)
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
        if account.status.lowercased() == "empty" || account.auth_state.lowercased() == "needs-content" {
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
            || account.status.lowercased() == "empty"
            || account.auth_state.lowercased() == "needs-content"
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
        switch account.auth_state.lowercased() {
        case "connected", "authorized":
            return "connected"
        case "needs-content":
            return "choose notes"
        case "needs-auth":
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
        if event.object_type == "agent" { return .purple }
        if event.event_type.contains("archive") { return .orange }
        return .accentColor
    }
}

struct IntegrationTokensSection: View {
    @ObservedObject var state: AppState

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
                    Task { await state.resetMCPIntegrationToken() }
                } label: {
                    Label("Reset Tool Token", systemImage: "key")
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
                    Task { await state.revokeIntegrationToken(token) }
                } label: {
                    Label("Revoke", systemImage: "xmark.shield")
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
            Text("Log: \(state.backendLogPath)")
                .font(.caption)
                .foregroundColor(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
        }
    }
}

struct SettingsReliabilitySection: View {
    @ObservedObject var state: AppState

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
                    state.repairStorage()
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
                    state.rebuildSearchIndex()
                } label: {
                    Label("Rebuild Search", systemImage: "magnifyingglass.circle")
                }
                Spacer()
            }
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
        .alert("Delete all local Cortex data?", isPresented: $confirmDeleteAllData) {
            Button("Delete All Data", role: .destructive) {
                state.deleteAllUserData()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This removes current review items, memories, tasks, graph data, settings, events, attachments, and backup archives from this vault.")
        }
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

    var body: some View {
        VStack(spacing: 6) {
            Text(title)
                .font(.headline)
            Text(detail)
                .font(.body)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(18)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
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
                    Button("Forget") { confirmForget = true }
                        .font(.caption)
                        .confirmationDialog("Forget this memory?", isPresented: $confirmForget) {
                            Button("Forget", role: .destructive) {
                                onArchive()
                            }
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
        case "decision": return .red
        case "preference": return .purple
        case "style": return .teal
        case "negative": return .orange
        case "question": return .orange
        case "action": return .green
        default: return .blue
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
        case "person": return .pink
        case "project": return .purple
        case "decision": return .red
        case "style": return .teal
        case "negative": return .orange
        case "action", "question": return .orange
        case "source": return .green
        default: return .blue
        }
    }
}

final class HotKeyManager {
    private var hotKeyRef: EventHotKeyRef?
    private var eventHandler: EventHandlerRef?
    private let handler: () -> Void

    init(handler: @escaping () -> Void) {
        self.handler = handler
    }

    func register() {
        guard hotKeyRef == nil else { return }
        var eventType = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let callback: EventHandlerUPP = { _, _, userData in
            guard let userData else { return noErr }
            let manager = Unmanaged<HotKeyManager>.fromOpaque(userData).takeUnretainedValue()
            DispatchQueue.main.async { manager.handler() }
            return noErr
        }
        InstallEventHandler(GetApplicationEventTarget(), callback, 1, &eventType, Unmanaged.passUnretained(self).toOpaque(), &eventHandler)
        let hotKeyID = EventHotKeyID(signature: fourCharCode("CRTX"), id: 1)
        RegisterEventHotKey(UInt32(kVK_ANSI_V), UInt32(cmdKey | shiftKey), hotKeyID, GetApplicationEventTarget(), 0, &hotKeyRef)
    }

    func unregister() {
        if let hotKeyRef {
            UnregisterEventHotKey(hotKeyRef)
            self.hotKeyRef = nil
        }
        if let eventHandler {
            RemoveEventHandler(eventHandler)
            self.eventHandler = nil
        }
    }

    deinit {
        unregister()
    }
}

func fourCharCode(_ string: String) -> OSType {
    var result: OSType = 0
    for scalar in string.unicodeScalars.prefix(4) {
        result = (result << 8) + OSType(scalar.value)
    }
    return result
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private let state = AppState()
    private var statusItem: NSStatusItem!
    private var mainWindow: NSWindow!
    private var mainWindowController: NSWindowController!
    private var hotKey: HotKeyManager!

    func applicationDidFinishLaunching(_ notification: Notification) {
        logApp("applicationDidFinishLaunching")
        NSApp.setActivationPolicy(.regular)
        setupStatusItem()
        setupMainWindow()
        NotificationCenter.default.addObserver(self, selector: #selector(onboardingCompleted), name: .cortexOnboardingCompleted, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(hotkeyPreferenceChanged), name: .cortexHotkeyPreferenceChanged, object: nil)
        hotKey = HotKeyManager { [weak self] in
            self?.state.captureClipboard()
            self?.showMainWindow()
        }
        applyHotkeyPreference()
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
        BackendSupervisor.shared.terminate()
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
        statusItem.button?.title = "Cortex"
        statusItem.button?.toolTip = "Cortex"
        statusItem.button?.action = #selector(toggleMainWindow)
        statusItem.button?.target = self
    }

    private func setupMainWindow() {
        logApp("setupMainWindow")
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 820, height: 760),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Cortex"
        window.minSize = NSSize(width: 640, height: 620)
        window.center()
        window.isReleasedWhenClosed = false
        window.hidesOnDeactivate = false
        window.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        window.level = .normal
        window.delegate = self
        window.contentViewController = NSHostingController(rootView: CortexView(state: state))
        window.setFrameAutosaveName("CortexMainWindow")
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

    @objc private func hotkeyPreferenceChanged() {
        applyHotkeyPreference()
    }

    private func applyHotkeyPreference() {
        if UserDefaults.standard.bool(forKey: "globalClipboardHotkeyEnabled.v1") {
            hotKey.register()
        } else {
            hotKey.unregister()
        }
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
        let width = min(max(window.minSize.width, 820), screenFrame.width - 80)
        let height = min(max(window.minSize.height, 760), screenFrame.height - 80)
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
