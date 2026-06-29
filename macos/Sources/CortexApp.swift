import AppKit
import Foundation
import SwiftUI
import Carbon
import UserNotifications
import Security
import Darwin
import PDFKit
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
    let scopes: [String]?
    let notes: String?
    let import_status: String?
    let formats: [String]?

    var isImportReady: Bool {
        let status = (import_status ?? "").lowercased()
        return ["native", "generic", "import_ready"].contains(status) || !(formats ?? []).isEmpty
    }

    var isLivePlanned: Bool {
        (live_status ?? "").lowercased() == "planned"
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
    let active_memories: Int
}

struct SourceReadinessItem: Codable, Identifiable, Hashable {
    var id: String { source }
    let source: String
    let name: String
    let category: String
    let status: String
    let next_action: String
    let import_status: String
    let live_status: String
    let auth: String?
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
        case "needs_review": return "Review"
        case "synced": return "Synced"
        case "connected": return "Connected"
        case "imported": return "Imported"
        case "import_ready": return "Ready"
        case "planned": return "Planned"
        default: return "Available"
        }
    }

    var statusIcon: String {
        switch status {
        case "needs_attention": return "exclamationmark.triangle.fill"
        case "needs_review": return "tray.full.fill"
        case "synced": return "checkmark.seal.fill"
        case "connected": return "link.circle.fill"
        case "imported": return "tray.and.arrow.down.fill"
        case "import_ready": return "square.and.arrow.down.fill"
        case "planned": return "calendar.badge.clock"
        default: return "circle"
        }
    }

    var statusColor: Color {
        switch status {
        case "needs_attention": return .orange
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
    let kind: String
    let layer: String
    let source: String
    let source_url: String?
    let captured_at: String?
    let occurred_at: String?
    let excerpt: String
    let topics: [String]?

    enum CodingKeys: String, CodingKey {
        case index
        case memory_id = "id"
        case kind
        case layer
        case source
        case source_url
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
    let kind: String
    let layer: String?
    let content: String
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
    let citation_coverage: Double
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
        allow_pending_in_context: true,
        context_pack_limit: 12,
        allow_agent_reads: true,
        allow_agent_writes: false,
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
            return "Connected AI tools can read approved memory and prepare redacted handoffs, but cannot save or delete memory."
        case .canSave:
            return "Connected AI tools can read memory, save useful context, and prepare redacted handoffs. Deletion and maintenance stay off."
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
        if settings.allow_pending_in_context
            && settings.allow_agent_reads
            && !settings.allow_agent_writes
            && settings.allow_agent_exports
            && !settings.allow_agent_maintenance
            && !settings.allow_agent_destructive_actions
            && settings.redact_sensitive_context {
            return .readOnly
        }
        if settings.allow_pending_in_context
            && settings.allow_agent_reads
            && settings.allow_agent_writes
            && settings.allow_agent_exports
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
            settings.allow_pending_in_context = true
            settings.allow_agent_reads = true
            settings.allow_agent_writes = false
            settings.allow_agent_exports = true
            settings.allow_agent_maintenance = false
            settings.allow_agent_destructive_actions = false
            settings.redact_sensitive_context = true
        case .canSave:
            settings.allow_pending_in_context = true
            settings.allow_agent_reads = true
            settings.allow_agent_writes = true
            settings.allow_agent_exports = true
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
    case oneClick = "One-click MCP"
    case developer = "Developer tools"
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
    let setupHint: String
    let browserURL: String?

    var supportsInstall: Bool {
        !configTargets.isEmpty
    }
}

struct AIIntegrationState: Hashable {
    var appInstalled: Bool = false
    var configured: Bool = false
    var configExists: Bool = false
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
            summary: "Adds Cortex memory tools directly inside Claude Desktop through MCP.",
            restartHint: "Quit and reopen Claude Desktop after installing.",
            bundleIdentifiers: ["com.anthropic.claudefordesktop", "com.anthropic.Claude"],
            configTargets: [
                IntegrationConfigTarget(label: "Claude Desktop", root: .applicationSupport, relativePath: "Claude/claude_desktop_config.json")
            ],
            setupHint: "Use Claude Desktop for direct MCP access to Cortex search, review, approved memory, and source capture.",
            browserURL: "https://claude.ai"
        ),
        AIIntegration(
            id: "cursor",
            name: "Cursor",
            category: .oneClick,
            systemImage: "cursorarrow.rays",
            summary: "Gives Cursor agent sessions access to Cortex project memory and adaptation signals.",
            restartHint: "Restart Cursor, then enable the cortex MCP server in Cursor settings if prompted.",
            bundleIdentifiers: ["com.todesktop.230313mzl4w4u92", "com.cursor.Cursor"],
            configTargets: [
                IntegrationConfigTarget(label: "Cursor global MCP", root: .home, relativePath: ".cursor/mcp.json")
            ],
            setupHint: "Use Cortex before implementation tasks: search memory for project decisions, people, and open loops.",
            browserURL: "https://cursor.com"
        ),
        AIIntegration(
            id: "windsurf",
            name: "Windsurf",
            category: .oneClick,
            systemImage: "wind",
            summary: "Connects Windsurf/Cascade to Cortex through the local MCP stdio bridge.",
            restartHint: "Restart Windsurf after installing the MCP server.",
            bundleIdentifiers: ["com.exafunction.windsurf", "com.codeium.windsurf"],
            configTargets: [
                IntegrationConfigTarget(label: "Windsurf MCP", root: .home, relativePath: ".codeium/windsurf/mcp_config.json")
            ],
            setupHint: "Use Cortex in Cascade to retrieve decisions, previous implementation context, and daily open loops.",
            browserURL: "https://windsurf.com"
        ),
        AIIntegration(
            id: "cline",
            name: "Cline",
            category: .developer,
            systemImage: "hammer",
            summary: "Installs Cortex as a Cline MCP server for VS Code agent workflows.",
            restartHint: "Reload VS Code after installing.",
            bundleIdentifiers: ["com.microsoft.VSCode"],
            configTargets: [
                IntegrationConfigTarget(label: "Cline MCP settings", root: .applicationSupport, relativePath: "Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json")
            ],
            setupHint: "Use Cline with Cortex to search memory before asking the user to repeat project context.",
            browserURL: "https://cline.bot"
        ),
        AIIntegration(
            id: "roo-code",
            name: "Roo Code",
            category: .developer,
            systemImage: "chevron.left.forwardslash.chevron.right",
            summary: "Adds Cortex MCP tools for Roo Code coding sessions.",
            restartHint: "Reload VS Code after installing.",
            bundleIdentifiers: ["com.microsoft.VSCode"],
            configTargets: [
                IntegrationConfigTarget(label: "Roo Code MCP settings", root: .applicationSupport, relativePath: "Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json")
            ],
            setupHint: "Use Roo Code with Cortex to retrieve saved decisions, source context, and open questions.",
            browserURL: nil
        ),
        AIIntegration(
            id: "vscode-copilot",
            name: "VS Code Copilot",
            category: .developer,
            systemImage: "rectangle.connected.to.line.below",
            summary: "Copy a Cortex MCP server definition for VS Code user or workspace MCP setup.",
            restartHint: "Add the copied MCP server to VS Code's MCP configuration, then reload the window.",
            bundleIdentifiers: ["com.microsoft.VSCode"],
            configTargets: [],
            setupHint: "Paste the copied MCP server into VS Code's user or workspace MCP configuration.",
            browserURL: "https://code.visualstudio.com"
        ),
        AIIntegration(
            id: "claude-code",
            name: "Claude Code",
            category: .developer,
            systemImage: "terminal",
            summary: "Copy a ready command/config snippet for Claude Code MCP setup.",
            restartHint: "Run the copied setup from a terminal, then restart the Claude Code session.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Use the copied command to add Cortex as a local MCP server for Claude Code.",
            browserURL: "https://docs.anthropic.com"
        ),
        AIIntegration(
            id: "chatgpt",
            name: "ChatGPT",
            category: .browser,
            systemImage: "message.badge",
            summary: "Prepare approved Cortex memory for ChatGPT when direct tools are unavailable.",
            restartHint: "Paste into a new or existing ChatGPT conversation.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Paste approved Cortex memory into ChatGPT when you want continuity from your local model.",
            browserURL: "https://chatgpt.com"
        ),
        AIIntegration(
            id: "claude-web",
            name: "Claude Web",
            category: .browser,
            systemImage: "sparkle.magnifyingglass",
            summary: "Copy Cortex memory into Claude web chats without configuring local files.",
            restartHint: "Paste approved Cortex memory into Claude.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Paste approved Cortex memory when Claude needs project, person, or decision memory.",
            browserURL: "https://claude.ai"
        ),
        AIIntegration(
            id: "gemini",
            name: "Gemini",
            category: .browser,
            systemImage: "diamond",
            summary: "Copy local Cortex context for Gemini and Google AI Studio sessions.",
            restartHint: "Paste into Gemini or AI Studio.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Paste Cortex context into Gemini before research, writing, or planning tasks.",
            browserURL: "https://gemini.google.com"
        ),
        AIIntegration(
            id: "perplexity",
            name: "Perplexity",
            category: .browser,
            systemImage: "magnifyingglass.circle",
            summary: "Prepare relevant Cortex memory for Perplexity research threads.",
            restartHint: "Paste into Perplexity.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Use Cortex context to ground research questions in your existing decisions and constraints.",
            browserURL: "https://www.perplexity.ai"
        ),
        AIIntegration(
            id: "copilot-web",
            name: "Microsoft Copilot",
            category: .browser,
            systemImage: "square.stack.3d.up",
            summary: "Copy Cortex context into Copilot chats and Microsoft 365 work.",
            restartHint: "Paste into Copilot.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Paste Cortex context before asking Copilot to draft, summarize, or plan from your memory.",
            browserURL: "https://copilot.microsoft.com"
        ),
        AIIntegration(
            id: "grok",
            name: "Grok",
            category: .browser,
            systemImage: "xmark.circle",
            summary: "Prepare approved Cortex memory for Grok conversations.",
            restartHint: "Paste into Grok.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Paste Cortex context into Grok when you need continuity from previous work.",
            browserURL: "https://grok.com"
        ),
        AIIntegration(
            id: "poe",
            name: "Poe",
            category: .browser,
            systemImage: "bubble.left.and.bubble.right",
            summary: "Copy reusable Cortex context into any Poe bot.",
            restartHint: "Paste into the target Poe bot.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Paste approved Cortex memory into Poe bots that need personal/project memory.",
            browserURL: "https://poe.com"
        ),
        AIIntegration(
            id: "notebooklm",
            name: "NotebookLM",
            category: .browser,
            systemImage: "book.pages",
            summary: "Export or copy Cortex memory as source material for NotebookLM.",
            restartHint: "Paste the copied Markdown into a NotebookLM source.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Use approved Cortex memory as a NotebookLM source for grounded Q&A.",
            browserURL: "https://notebooklm.google.com"
        ),
        AIIntegration(
            id: "lm-studio",
            name: "LM Studio",
            category: .local,
            systemImage: "cpu",
            summary: "Copy Cortex MCP/API settings for local model workflows.",
            restartHint: "Paste the MCP config where your LM Studio workflow accepts local tools.",
            bundleIdentifiers: ["com.lmstudio.lmstudio"],
            configTargets: [],
            setupHint: "Use Cortex's local API or MCP bridge with local model agents that support tools.",
            browserURL: "https://lmstudio.ai"
        ),
        AIIntegration(
            id: "open-webui",
            name: "Open WebUI",
            category: .local,
            systemImage: "server.rack",
            summary: "Copy Cortex API and context instructions for self-hosted Open WebUI setups.",
            restartHint: "Paste into your tool/server configuration.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Configure Open WebUI or its pipelines to call Cortex on localhost, or paste approved memory manually.",
            browserURL: "https://openwebui.com"
        ),
        AIIntegration(
            id: "librechat",
            name: "LibreChat",
            category: .local,
            systemImage: "globe.desk",
            summary: "Copy Cortex MCP/API settings for team chat deployments.",
            restartHint: "Paste into your LibreChat MCP/tool configuration and restart the service.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Use Cortex as a local memory source for LibreChat where MCP or custom tools are enabled.",
            browserURL: "https://www.librechat.ai"
        ),
        AIIntegration(
            id: "anythingllm",
            name: "AnythingLLM",
            category: .local,
            systemImage: "tray.and.arrow.down",
            summary: "Bring approved Cortex memory into AnythingLLM workspaces.",
            restartHint: "Paste or import the Markdown/JSON export into the workspace.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Use Cortex exports as local knowledge documents, or wire the local API into agent workflows.",
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

final class KeychainStore {
    private let service = "com.cortex.doppl"

    func save(_ value: String, account: String) {
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        SecItemDelete(query as CFDictionary)
        var add = query
        add[kSecValueData as String] = data
        SecItemAdd(add as CFDictionary, nil)
    }

    func load(account: String) -> String {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return "" }
        return String(data: data, encoding: .utf8) ?? ""
    }
}

enum DistributionMode {
    static var isAppStore: Bool {
        (Bundle.main.object(forInfoDictionaryKey: "CortexDistributionMode") as? String) == "app-store"
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
            return "Using remote backend"
        }
        let initialHealth = await healthCheck(endpoint: normalizedEndpoint, apiKey: apiKey, expectedVaultPath: vaultPath)
        if initialHealth == .healthy {
            return "Backend connected"
        }
        do {
            if initialHealth == .incompatible {
                terminateLocalPortListener(endpoint: normalizedEndpoint)
            }
            terminate()
            try startBundledBackend(apiKey: apiKey, mcpAPIKey: mcpAPIKey, vaultPath: vaultPath)
            for _ in 0..<30 {
                if await healthCheck(endpoint: normalizedEndpoint, apiKey: apiKey, expectedVaultPath: vaultPath) == .healthy {
                    return "Local backend started"
                }
                try await Task.sleep(nanoseconds: 250_000_000)
            }
            return "Backend did not become ready"
        } catch {
            return "Backend start failed: \(error.localizedDescription)"
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
            throw NSError(domain: "Cortex", code: 2, userInfo: [NSLocalizedDescriptionKey: "Bundled backend is missing"])
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
            throw NSError(domain: "Cortex", code: 5, userInfo: [NSLocalizedDescriptionKey: "Cortex MCP token is missing"])
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
            launched.arguments = ["python3", "-m", "app.standalone_server", "--host", "127.0.0.1", "--port", "8766"]
        } else {
            launched.arguments = ["-m", "app.standalone_server", "--host", "127.0.0.1", "--port", "8766"]
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

@MainActor
final class AppState: ObservableObject {
    private static func loadOrCreateAPIKey() -> String {
        let store = KeychainStore()
        let existing = store.load(account: "apiKey").trimmingCharacters(in: .whitespacesAndNewlines)
        if !existing.isEmpty && existing != "dev-local-key" {
            return existing
        }
        let generated = generateAPIKey()
        store.save(generated, account: "apiKey")
        return generated
    }

    private static func loadOrCreateMCPAPIKey() -> String {
        let store = KeychainStore()
        let existing = store.load(account: "mcpAPIKey").trimmingCharacters(in: .whitespacesAndNewlines)
        if existing.hasPrefix("cxm_") {
            return existing
        }
        let generated = generateMCPAPIKey()
        store.save(generated, account: "mcpAPIKey")
        return generated
    }

    private static func generateAPIKey() -> String {
        generateSecret(prefix: "cx_")
    }

    private static func generateMCPAPIKey() -> String {
        generateSecret(prefix: "cxm_")
    }

    private static func generateSecret(prefix: String) -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        let count = bytes.count
        let status = bytes.withUnsafeMutableBytes { buffer in
            SecRandomCopyBytes(kSecRandomDefault, count, buffer.baseAddress!)
        }
        if status == errSecSuccess {
            return prefix + bytes.map { String(format: "%02x", $0) }.joined()
        }
        return prefix + UUID().uuidString.replacingOccurrences(of: "-", with: "") + UUID().uuidString.replacingOccurrences(of: "-", with: "")
    }

    @Published var endpoint: String = UserDefaults.standard.string(forKey: "endpoint") ?? "http://127.0.0.1:8766"
    @Published var apiKey: String = AppState.loadOrCreateAPIKey()
    @Published var mcpAPIKey: String = AppState.loadOrCreateMCPAPIKey()
    @Published var quickNote: String = ""
    @Published var captureURLString: String = ""
    @Published var captureTitle: String = ""
    @Published var captureNotes: String = ""
    @Published var captureDropTargeted: Bool = false
    @Published var lastFileCaptureSummary: String = ""
    @Published var importPreview: SourceAnalyzeResponse?
    @Published var importPreviewURLs: [URL] = []
    @Published var importPreviewMoveImportedFromInbox: Bool = false
    @Published var showImportPreview: Bool = false
    @Published var importHistory: [SourceImportHistoryItem] = []
    @Published var sourceConnectorCatalog: [SourceConnectorCatalogItem] = []
    @Published var sourceReadinessReport: SourceReadinessResponse?
    @Published var sourceAccounts: [SourceAccountItem] = []
    @Published var syncCursors: [SyncCursorItem] = []
    @Published var syncDevices: [SyncDeviceItem] = []
    @Published var syncReceiptsByDevice: [String: [SyncReceiptItem]] = [:]
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
    @Published var contextQuery: String = ""
    @Published var selectedTab: AppTab = .model
    @Published var vaultPath: String = UserDefaults.standard.string(forKey: "vaultPath") ?? BackendSupervisor.defaultVaultURL.path
    @Published var globalClipboardHotkeyEnabled: Bool = UserDefaults.standard.bool(forKey: "globalClipboardHotkeyEnabled.v1")
    @Published var showOnboarding: Bool = !UserDefaults.standard.bool(forKey: "onboardingComplete.v1")
    @Published var onboardingStep: OnboardingStep = OnboardingStep(rawValue: UserDefaults.standard.integer(forKey: "onboardingStep.v2")) ?? .privateVault
    @Published var onboardingNote: String = ""
    @Published var firstSourceAdded: Bool = UserDefaults.standard.bool(forKey: "onboardingFirstSourceImported.v1")
    @Published var firstMemoryReviewed: Bool = UserDefaults.standard.bool(forKey: "onboardingFirstMemoryReviewed.v1")
    @Published var cortexUsed: Bool = UserDefaults.standard.bool(forKey: "onboardingCortexUsed.v1")
    @Published var onboardingBackupDecision: String = UserDefaults.standard.string(forKey: "onboardingBackupDecision.v1") ?? ""
    @Published var integrationStates: [String: AIIntegrationState] = [:]
    @Published var isBusy: Bool = false

    private let keychain = KeychainStore()
    private let backend = BackendSupervisor.shared

    var integrations: [AIIntegration] {
        AIIntegrationCatalog.all
    }

    var captureInboxURL: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return base.appendingPathComponent("Cortex", isDirectory: true).appendingPathComponent("Capture Inbox", isDirectory: true)
    }

    var displayStatus: String {
        if backendStatus == status { return status }
        return "\(backendStatus) · \(status)"
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

    var onboardingStepIndex: Int {
        onboardingStep.rawValue
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

    var onboardingHasSource: Bool {
        firstSourceAdded
            || importHistory.contains { item in
                item.deleted_at == nil && item.records_found > 0 && (item.queued + item.saved + item.remaining_captures) > 0
            }
            || (sourceReadinessReport?.summary.sources_with_data ?? 0) > 0
    }

    var onboardingHasReviewedMemory: Bool {
        firstMemoryReviewed
            || ((stats?.memories ?? 0) > 0 && (stats?.pending_captures ?? 0) == 0)
    }

    var onboardingHasUsedCortex: Bool {
        cortexUsed
    }

    var onboardingHasBackupDecision: Bool {
        !onboardingBackupDecision.isEmpty || lastBackupPath != nil
    }

    var canCompleteOnboarding: Bool {
        isLocalServiceReady
            && onboardingHasSource
            && onboardingHasReviewedMemory
            && onboardingHasUsedCortex
            && onboardingHasBackupDecision
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
            return onboardingHasUsedCortex
        case .trustBackup:
            return onboardingHasBackupDecision
        }
    }

    var canAdvanceOnboarding: Bool {
        onboardingStepIsComplete(onboardingStep)
    }

    var preferredUpdateArtifact: UpdateArtifact? {
        updateManifest?.artifacts.first(where: { $0.kind == "dmg" }) ?? updateManifest?.artifacts.first
    }

    func persistSettings() {
        ensureUsableAPIKey()
        ensureUsableMCPAPIKey()
        UserDefaults.standard.set(endpoint, forKey: "endpoint")
        UserDefaults.standard.set(vaultPath, forKey: "vaultPath")
        keychain.save(apiKey, account: "apiKey")
        keychain.save(mcpAPIKey, account: "mcpAPIKey")
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
        refreshIntegrationStates()
    }

    func ensureBackend() async {
        ensureUsableAPIKey()
        ensureUsableMCPAPIKey()
        backendStatus = "Checking backend"
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
                    "label": "Local MCP integrations",
                    "scopes": ["read", "write", "export", "maintenance"]
                ]
            )
            return true
        } catch {
            status = "MCP token registration failed: \(error.localizedDescription)"
            return false
        }
    }

    private func ensureUsableAPIKey() {
        let normalized = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        if normalized.isEmpty || normalized == "dev-local-key" {
            apiKey = AppState.generateAPIKey()
            keychain.save(apiKey, account: "apiKey")
        } else if normalized != apiKey {
            apiKey = normalized
            keychain.save(apiKey, account: "apiKey")
        }
    }

    private func ensureUsableMCPAPIKey() {
        let normalized = mcpAPIKey.trimmingCharacters(in: .whitespacesAndNewlines)
        if !normalized.hasPrefix("cxm_") || normalized == apiKey {
            mcpAPIKey = AppState.generateMCPAPIKey()
            keychain.save(mcpAPIKey, account: "mcpAPIKey")
        } else if normalized != mcpAPIKey {
            mcpAPIKey = normalized
            keychain.save(mcpAPIKey, account: "mcpAPIKey")
        }
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
            if await capture(text: text, source: "macos-clipboard", title: "Clipboard capture") {
                status = "Clipboard saved. Import a source to finish setup."
            }
        }
    }

    func captureQuickNote() {
        let text = quickNote.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty {
            status = "Quick note is empty"
            return
        }
        Task {
            if await capture(text: text, source: "macos-quick-note", title: "Quick note") {
                quickNote = ""
                status = "Quick memory saved. Import a source to finish setup."
            }
        }
    }

    func captureURLSurface() {
        let urlText = captureURLString.trimmingCharacters(in: .whitespacesAndNewlines)
        let notes = captureNotes.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !urlText.isEmpty || !notes.isEmpty else {
            status = "URL capture is empty"
            return
        }
        var content: [String] = []
        if !urlText.isEmpty {
            content.append("URL: \(urlText)")
        }
        if !captureTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            content.append("Title: \(captureTitle.trimmingCharacters(in: .whitespacesAndNewlines))")
        }
        if !notes.isEmpty {
            content.append("\nNotes:\n\(notes)")
        }
        let title = captureTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? (URL(string: urlText)?.host ?? "Web capture")
            : captureTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        Task {
            if await capture(text: content.joined(separator: "\n"), source: "web-url", title: title, sourceURL: urlText.isEmpty ? nil : urlText) {
                captureURLString = ""
                captureTitle = ""
                captureNotes = ""
                status = "Web memory saved. Import a source to finish setup."
            }
        }
    }

    func chooseFilesForCapture() {
        let panel = NSOpenPanel()
        panel.title = "Choose Sources to Add to Cortex"
        panel.prompt = "Add Sources"
        panel.canChooseFiles = true
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = true
        if panel.runModal() == .OK {
            captureFiles(panel.urls)
        }
    }

    func captureFiles(_ urls: [URL]) {
        let unique = Array(Set(urls)).sorted { $0.path < $1.path }
        guard !unique.isEmpty else {
            status = "No files selected"
            return
        }
        Task { await prepareImportPreview(unique, moveImportedFromInbox: false) }
    }

    func openCaptureInbox() {
        try? FileManager.default.createDirectory(at: captureInboxURL, withIntermediateDirectories: true)
        NSWorkspace.shared.open(captureInboxURL)
    }

    func copyCaptureInboxPath() {
        try? FileManager.default.createDirectory(at: captureInboxURL, withIntermediateDirectories: true)
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(captureInboxURL.path, forType: .string)
        status = "Capture Inbox path copied"
    }

    func importCaptureInbox() {
        try? FileManager.default.createDirectory(at: captureInboxURL, withIntermediateDirectories: true)
        let manager = FileManager.default
        let urls = (try? manager.contentsOfDirectory(at: captureInboxURL, includingPropertiesForKeys: [.isRegularFileKey, .isDirectoryKey], options: [.skipsHiddenFiles])) ?? []
        let files = urls.filter { url in
            guard url.lastPathComponent != "Imported" else { return false }
            let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .isDirectoryKey])
            return values?.isRegularFile == true || values?.isDirectory == true
        }
        guard !files.isEmpty else {
            status = "Capture Inbox is empty"
            return
        }
        Task { await prepareImportPreview(files, moveImportedFromInbox: true) }
    }

    func copyBrowserBookmarklet() {
        let action = endpoint.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/capture"
        let js = """
        javascript:(()=>{const u=new URL(\(jsString(action)));u.searchParams.set('title',document.title||location.href);u.searchParams.set('url',location.href);window.open(u.toString(),'_blank','noopener')})()
        """
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(js.replacingOccurrences(of: "\n", with: ""), forType: .string)
        status = "Browser capture bookmarklet copied without API token"
    }

    func openBrowserCapturePage() {
        let urlString = endpoint.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/capture"
        if let url = URL(string: urlString) {
            NSWorkspace.shared.open(url)
        }
    }

    func captureOnboardingQuickNote() {
        let text = onboardingNote.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty {
            status = "First memory is empty"
            return
        }
        Task {
            if await capture(text: text, source: "macos-onboarding", title: "First Cortex memory") {
                onboardingNote = ""
                status = "Quick memory saved. Import a real source to continue setup."
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
            status = "Save failed: \(error.localizedDescription)"
            notify("Cortex", "Save failed")
            return false
        }
    }

    private func prepareImportPreview(_ urls: [URL], moveImportedFromInbox: Bool) async {
        isBusy = true
        status = "Scanning \(urls.count) source\(urls.count == 1 ? "" : "s")..."
        defer { isBusy = false }
        do {
            let body: [String: Any] = [
                "paths": urls.map { $0.path },
                "max_records": 500
            ]
            let data = try await request(path: "/v1/imports/analyze", method: "POST", body: body)
            let preview = try JSONDecoder().decode(SourceAnalyzeResponse.self, from: data)
            if preview.records_found > 0 {
                importPreview = preview
                importPreviewURLs = urls
                importPreviewMoveImportedFromInbox = moveImportedFromInbox
                showImportPreview = true
                let sourceSummary = preview.sources.prefix(3).map { "\($0.source): \($0.count)" }.joined(separator: ", ")
                lastFileCaptureSummary = "Detected \(preview.records_found) record\(preview.records_found == 1 ? "" : "s")" + (sourceSummary.isEmpty ? "" : " (\(sourceSummary))")
                status = lastFileCaptureSummary
            } else {
                lastFileCaptureSummary = "No structured records found; saving file references"
                status = lastFileCaptureSummary
                await fallbackCaptureFilesAsync(urls, moveImportedFromInbox: moveImportedFromInbox)
            }
        } catch {
            status = "Importer fallback for \(urls.count) file\(urls.count == 1 ? "" : "s")"
            await fallbackCaptureFilesAsync(urls, moveImportedFromInbox: moveImportedFromInbox)
        }
    }

    func confirmImportPreview() {
        let urls = importPreviewURLs
        let moveImported = importPreviewMoveImportedFromInbox
        cancelImportPreview()
        guard !urls.isEmpty else {
            status = "No sources selected"
            return
        }
        Task { await captureFilesAsync(urls, moveImportedFromInbox: moveImported) }
    }

    func cancelImportPreview() {
        showImportPreview = false
        importPreview = nil
        importPreviewURLs = []
        importPreviewMoveImportedFromInbox = false
    }

    private func captureFilesAsync(_ urls: [URL], moveImportedFromInbox: Bool) async {
        isBusy = true
        status = "Importing \(urls.count) source\(urls.count == 1 ? "" : "s")..."
        defer { isBusy = false }
        do {
            let body: [String: Any] = [
                "paths": urls.map { $0.path },
                "processing": "async",
                "max_records": 1000
            ]
            let data = try await request(path: "/v1/imports", method: "POST", body: body)
            let response = try JSONDecoder().decode(SourceImportResponse.self, from: data)
            if response.records_found > 0 {
                let jobData = try? await request(path: "/v1/maintenance/jobs/run?limit=50", method: "POST")
                var processed = 0
                if let jobData, let run = try? JSONDecoder().decode(JobRunResponse.self, from: jobData) {
                    processed = run.processed
                }
                let sourceSummary = response.sources.prefix(3).map { "\($0.source): \($0.count)" }.joined(separator: ", ")
                let skippedCount = response.skipped ?? 0
                let queueSummary = response.queued > 0 ? "Queued \(response.queued)" : "Saved \(response.saved)"
                let failureSummary = response.failed > 0 ? ", \(response.failed) failed" : ""
                let skippedSummary = skippedCount > 0 ? ", skipped \(skippedCount) duplicate\(skippedCount == 1 ? "" : "s")" : ""
                let processedSummary = processed > 0 ? ", started \(processed)" : ""
                lastFileCaptureSummary = "Detected \(response.records_found) source record\(response.records_found == 1 ? "" : "s"). \(queueSummary)\(processedSummary)\(failureSummary)\(skippedSummary)" + (sourceSummary.isEmpty ? "" : " (\(sourceSummary))")
                status = lastFileCaptureSummary
                markFirstSourceAdded()
                if moveImportedFromInbox {
                    for url in urls {
                        moveToImportedFolder(url)
                    }
                }
                await refreshAfterCapture()
                return
            }
        } catch {
            status = "Importer fallback for \(urls.count) file\(urls.count == 1 ? "" : "s")"
        }
        await fallbackCaptureFilesAsync(urls, moveImportedFromInbox: moveImportedFromInbox)
    }

    private func fallbackCaptureFilesAsync(_ urls: [URL], moveImportedFromInbox: Bool) async {
        var saved = 0
        var failed = 0
        for url in urls {
            do {
                let payload = try fileCapturePayload(for: url)
                if await capture(text: payload.content, source: payload.source, title: payload.title, sourceURL: payload.sourceURL) {
                    saved += 1
                    if moveImportedFromInbox {
                        moveToImportedFolder(url)
                    }
                } else {
                    failed += 1
                }
            } catch {
                failed += 1
            }
        }
        if saved > 0 {
            markFirstSourceAdded()
        }
        lastFileCaptureSummary = failed == 0 ? "Saved \(saved) file\(saved == 1 ? "" : "s")" : "Saved \(saved), failed \(failed)"
        status = lastFileCaptureSummary
        await refreshAfterCapture()
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

    private func fileCapturePayload(for url: URL) throws -> (content: String, source: String, title: String, sourceURL: String) {
        let access = url.startAccessingSecurityScopedResource()
        defer {
            if access { url.stopAccessingSecurityScopedResource() }
        }
        let values = try url.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey, .localizedTypeDescriptionKey])
        let size = values.fileSize ?? 0
        let modified = values.contentModificationDate.map { ISO8601DateFormatter().string(from: $0) } ?? "unknown"
        var content = """
        File: \(url.lastPathComponent)
        Path: \(url.path)
        Type: \(values.localizedTypeDescription ?? url.pathExtension.uppercased())
        Size: \(ByteCountFormatter.string(fromByteCount: Int64(size), countStyle: .file))
        Modified: \(modified)
        """
        if let extracted = extractText(from: url, size: size), !extracted.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            content += "\n\n--- Extracted content ---\n\(extracted)"
        } else {
            content += "\n\nCortex saved this file reference. This file type is not text-readable in this local build yet."
        }
        if content.count > 190_000 {
            content = String(content.prefix(190_000)) + "\n\n[Truncated by Cortex before capture.]"
        }
        return (content, "file", url.lastPathComponent, url.absoluteString)
    }

    private func extractText(from url: URL, size: Int) -> String? {
        let ext = url.pathExtension.lowercased()
        if ext == "pdf" {
            guard size <= 25_000_000, let document = PDFDocument(url: url) else { return nil }
            return (0..<document.pageCount).compactMap { document.page(at: $0)?.string }.joined(separator: "\n\n")
        }
        if ext == "rtf" || ext == "rtfd" {
            guard let attributed = try? NSAttributedString(url: url, options: [:], documentAttributes: nil) else { return nil }
            return attributed.string
        }
        let textExtensions: Set<String> = [
            "txt", "md", "markdown", "json", "jsonl", "csv", "tsv", "log", "xml", "yaml", "yml",
            "py", "js", "ts", "tsx", "jsx", "swift", "go", "rs", "java", "c", "h", "cpp", "hpp",
            "rb", "php", "sh", "zsh", "bash", "sql", "html", "css", "scss", "toml", "ini", "env"
        ]
        guard textExtensions.contains(ext) || size <= 1_000_000 else { return nil }
        if let text = try? String(contentsOf: url, encoding: .utf8) {
            return text
        }
        if let text = try? String(contentsOf: url, encoding: .isoLatin1) {
            return text
        }
        return nil
    }

    private func moveToImportedFolder(_ url: URL) {
        let manager = FileManager.default
        let imported = captureInboxURL.appendingPathComponent("Imported", isDirectory: true)
        try? manager.createDirectory(at: imported, withIntermediateDirectories: true)
        var target = imported.appendingPathComponent(url.lastPathComponent)
        if manager.fileExists(atPath: target.path) {
            let stamp = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "-")
            target = imported.appendingPathComponent("\(stamp)-\(url.lastPathComponent)")
        }
        try? manager.moveItem(at: url, to: target)
    }

    private func jsString(_ value: String) -> String {
        let escaped = value
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "'", with: "\\'")
            .replacingOccurrences(of: "\n", with: "\\n")
            .replacingOccurrences(of: "\r", with: "")
        return "'\(escaped)'"
    }

    func loadInbox() async {
        do {
            let data = try await request(path: "/v1/inbox?limit=30", method: "GET")
            inbox = try JSONDecoder().decode(InboxResponse.self, from: data).results
        } catch {
            status = "Inbox failed: \(error.localizedDescription)"
        }
    }

    func loadRecent() async {
        do {
            let data = try await request(path: "/v1/recent?limit=20", method: "GET")
            recent = try JSONDecoder().decode(RecentResponse.self, from: data).results
        } catch {
            status = "Recent failed: \(error.localizedDescription)"
        }
    }

    func loadImportHistory() async {
        do {
            let data = try await request(path: "/v1/imports?limit=12&include_deleted=false", method: "GET")
            importHistory = try JSONDecoder().decode(SourceImportHistoryResponse.self, from: data).results
        } catch {
            status = "Import history failed: \(error.localizedDescription)"
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
            status = searchResults.isEmpty ? "No cited memory found" : "Answered with \(answer.citations.count) citation\(answer.citations.count == 1 ? "" : "s")"
            if !searchResults.isEmpty || !answer.citations.isEmpty {
                markCortexUsed()
            }
        } catch {
            hasSearched = true
            askAnswer = ""
            askCitations = []
            status = "Search failed: \(error.localizedDescription)"
        }
    }

    func loadGraph() async {
        do {
            let data = try await request(path: "/v1/graph?limit=160", method: "GET")
            let graph = try JSONDecoder().decode(GraphResponse.self, from: data)
            graphNodes = graph.nodes
            graphEdges = graph.edges
        } catch {
            status = "Graph failed: \(error.localizedDescription)"
        }
    }

    func loadStats() async {
        do {
            let data = try await request(path: "/v1/stats", method: "GET")
            stats = try JSONDecoder().decode(StatsResponse.self, from: data)
            await loadMemoryQuality()
        } catch {
            status = "Stats failed: \(error.localizedDescription)"
        }
    }

    func loadMemoryQuality() async {
        do {
            let data = try await request(path: "/v1/memory/quality", method: "GET")
            memoryQuality = try JSONDecoder().decode(MemoryQualityResponse.self, from: data)
        } catch {
            status = "Quality failed: \(error.localizedDescription)"
        }
    }

    func loadReview() async {
        do {
            let data = try await request(path: "/v1/review/today", method: "GET")
            review = try JSONDecoder().decode(DailyReviewResponse.self, from: data)
        } catch {
            status = "Review failed: \(error.localizedDescription)"
        }
    }

    func loadProductLoop() async {
        do {
            let data = try await request(path: "/v1/loop", method: "GET")
            productLoop = try JSONDecoder().decode(ProductLoopResponse.self, from: data)
        } catch {
            status = "Loop failed: \(error.localizedDescription)"
        }
    }

    func recordContextReuse(surface: String, query: String = "", target: String = "") async {
        do {
            let body: [String: Any] = ["surface": surface, "query": query, "target": target]
            let data = try await request(path: "/v1/loop/reuse", method: "POST", body: body)
            let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            if let loopObject = payload?["product_loop"] as? [String: Any],
               let loopData = try? JSONSerialization.data(withJSONObject: loopObject) {
                productLoop = try? JSONDecoder().decode(ProductLoopResponse.self, from: loopData)
            } else {
                await loadProductLoop()
            }
            markCortexUsed()
            await loadReview()
            await loadTrust()
        } catch {
            await loadProductLoop()
        }
    }

    func performProductLoopAction(_ action: ProductLoopAction) {
        switch action.action {
        case "capture":
            selectedTab = .sources
            status = "Add data sources to build your model"
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

    func loadSettings() async {
        do {
            let data = try await request(path: "/v1/settings", method: "GET")
            appSettings = try JSONDecoder().decode(AppSettingsResponse.self, from: data)
        } catch {
            status = "Settings failed: \(error.localizedDescription)"
        }
    }

    func saveMemorySettings() {
        Task {
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
                status = "Memory settings saved"
                await loadRecent()
                if !searchQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    await search()
                }
                await loadReview()
                await loadProductLoop()
                await loadStats()
                await loadTrust()
            } catch {
                status = "Settings save failed: \(error.localizedDescription)"
            }
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
            status = "Trust failed: \(error.localizedDescription)"
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
            status = "Token refresh failed: \(error.localizedDescription)"
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
            status = "Token revoke failed: \(error.localizedDescription)"
        }
    }

    func resetMCPIntegrationToken() async {
        mcpAPIKey = AppState.generateMCPAPIKey()
        keychain.save(mcpAPIKey, account: "mcpAPIKey")
        let registered = await registerMCPToken()
        await loadIntegrationTokens()
        refreshIntegrationStates()
        if registered {
            status = "MCP token reset. Reinstall or copy setup for connected AI tools."
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
        status = integration.map { "\($0.name) MCP config copied" } ?? "MCP config copied"
    }

    func copyIntegrationGuide(_ integration: AIIntegration) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(integrationGuide(for: integration), forType: .string)
        status = "\(integration.name) guide copied"
    }

    func copyIntegrationContext(_ integration: AIIntegration) {
        copyPersonalProfile(query: "", surface: "integration", target: integration.name, label: "\(integration.name) memory prepared")
    }

    func installIntegration(_ integration: AIIntegration) {
        if DistributionMode.isAppStore {
            copyIntegrationGuide(integration)
            status = "Copied \(integration.name) setup guide"
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
            status = "\(integration.name) install failed: \(error.localizedDescription)"
        }
    }

    func installDetectedIntegrations() {
        if DistributionMode.isAppStore {
            status = "App Store builds use copy setup instead of editing other apps"
            return
        }
        let detected = integrations.filter { integration in
            integration.supportsInstall && integrationState(for: integration).appInstalled
        }
        guard !detected.isEmpty else {
            status = "No detected MCP apps yet"
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
            let installed = integration.bundleIdentifiers.contains { bundleID in
                NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleID) != nil
            }
            if DistributionMode.isAppStore {
                next[integration.id] = AIIntegrationState(
                    appInstalled: installed,
                    configured: false,
                    configExists: false,
                    configuredPaths: [],
                    availablePaths: paths
                )
                continue
            }
            let configured = integration.configTargets.filter { target in
                configContainsCortex(at: target.url)
            }
            let exists = integration.configTargets.contains { target in
                FileManager.default.fileExists(atPath: target.url.path)
            }
            next[integration.id] = AIIntegrationState(
                appInstalled: installed,
                configured: !configured.isEmpty,
                configExists: exists,
                configuredPaths: configured.map { $0.url.path },
                availablePaths: paths
            )
        }
        integrationStates = next
    }

    func integrationState(for integration: AIIntegration) -> AIIntegrationState {
        if DistributionMode.isAppStore {
            return AIIntegrationState(
                appInstalled: integration.bundleIdentifiers.contains { NSWorkspace.shared.urlForApplication(withBundleIdentifier: $0) != nil },
                configured: false,
                configExists: false,
                configuredPaths: [],
                availablePaths: integration.configTargets.map { $0.url.path }
            )
        }
        return integrationStates[integration.id] ?? AIIntegrationState(
            appInstalled: integration.bundleIdentifiers.contains { NSWorkspace.shared.urlForApplication(withBundleIdentifier: $0) != nil },
            configured: integration.configTargets.contains { configContainsCortex(at: $0.url) },
            configExists: integration.configTargets.contains { FileManager.default.fileExists(atPath: $0.url.path) },
            configuredPaths: integration.configTargets.filter { configContainsCortex(at: $0.url) }.map { $0.url.path },
            availablePaths: integration.configTargets.map { $0.url.path }
        )
    }

    func openIntegrationConfig(_ integration: AIIntegration) {
        if DistributionMode.isAppStore {
            copyIntegrationGuide(integration)
            status = "Copied \(integration.name) setup guide"
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

    private func mcpServerDefinition() -> [String: Any] {
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
                "CORTEX_API_KEY": mcpAPIKey
            ]
        ]
    }

    private func mcpConfigJSON() -> String {
        let config: [String: Any] = [
            "mcpServers": [
                "cortex": mcpServerDefinition()
            ]
        ]
        let data = try? JSONSerialization.data(withJSONObject: config, options: [.prettyPrinted, .sortedKeys])
        return data.flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
    }

    private func integrationGuide(for integration: AIIntegration) -> String {
        let targetPaths = integration.configTargets.isEmpty
            ? "Manual setup required by the target app."
            : integration.configTargets.map { "- \($0.label): \($0.url.path)" }.joined(separator: "\n")
        return """
        Cortex integration: \(integration.name)

        What this does:
        \(integration.summary)

        Recommended setup:
        \(integration.setupHint)

        Config targets:
        \(targetPaths)

        MCP server config:
        \(mcpConfigJSON())

        Local MCP service:
        Base URL: \(endpoint)
        Token: scoped integration token

        Assistant rule:
        Search Cortex memory before asking the user to repeat project, person, decision, or open-loop context. Use build_context_pack when the assistant needs a concise handoff brief.

        After setup:
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

    func markFirstSourceAdded() {
        firstSourceAdded = true
        UserDefaults.standard.set(true, forKey: "onboardingFirstSourceImported.v1")
    }

    func markFirstMemoryReviewed() {
        firstMemoryReviewed = true
        UserDefaults.standard.set(true, forKey: "onboardingFirstMemoryReviewed.v1")
    }

    func markCortexUsed() {
        cortexUsed = true
        UserDefaults.standard.set(true, forKey: "onboardingCortexUsed.v1")
    }

    func markBackupDecision(_ decision: String) {
        onboardingBackupDecision = decision
        UserDefaults.standard.set(decision, forKey: "onboardingBackupDecision.v1")
    }

    func completeOnboarding() {
        guard canCompleteOnboarding else {
            status = "Complete each setup step before starting Cortex"
            return
        }
        saveMemorySettings()
        UserDefaults.standard.set(true, forKey: "onboardingComplete.v1")
        showOnboarding = false
        setOnboardingStep(.privateVault)
        status = "Setup complete"
        NotificationCenter.default.post(name: .cortexOnboardingCompleted, object: nil)
    }

    func dismissOnboardingForSession() {
        showOnboarding = false
        status = "Setup can be reopened from Trust"
    }

    func showOnboardingAgain() {
        UserDefaults.standard.set(false, forKey: "onboardingComplete.v1")
        setOnboardingStep(.privateVault)
        showOnboarding = true
    }

    func nextOnboardingStep() {
        guard canAdvanceOnboarding else {
            status = "Finish this setup step first"
            return
        }
        let steps = OnboardingStep.allCases
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

    func copyDailyContextPack() {
        copyPersonalProfile(query: "", surface: "model", target: "clipboard", label: "Personal profile prepared")
    }

    func copyContextPack() {
        let query = contextQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        copyPersonalProfile(
            query: query,
            surface: "focused-context",
            target: "clipboard",
            label: query.isEmpty ? "Personal profile prepared" : "Profile for \(query) prepared"
        )
    }

    func copyAgentAdaptation() {
        let query = searchQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        copyAgentAdaptation(
            query: query,
            surface: "agent-adaptation",
            target: "assistant",
            label: query.isEmpty ? "Agent adaptation layer prepared" : "Agent adaptation layer for \(query) prepared"
        )
    }

    private func copyAgentAdaptation(query: String, surface: String, target: String, label: String) {
        Task {
            do {
                var allowed = CharacterSet.urlQueryAllowed
                allowed.remove(charactersIn: "&+=")
                let encodedQuery = query.addingPercentEncoding(withAllowedCharacters: allowed) ?? query
                let encodedTarget = target.addingPercentEncoding(withAllowedCharacters: allowed) ?? target
                let limit = max(1, min(20, appSettings.context_pack_limit))
                let data = try await request(path: "/v1/agent-adaptation?format=markdown&target=\(encodedTarget)&query=\(encodedQuery)&limit=\(limit)", method: "GET")
                let adaptation = String(data: data, encoding: .utf8) ?? ""
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(adaptation, forType: .string)
                status = label
                await recordContextReuse(surface: surface, query: query, target: target)
            } catch {
                status = "Agent adaptation layer failed: \(error.localizedDescription)"
            }
        }
    }

    private func copyPersonalProfile(query: String, surface: String, target: String, label: String) {
        Task {
            do {
                var allowed = CharacterSet.urlQueryAllowed
                allowed.remove(charactersIn: "&+=")
                let encoded = query.addingPercentEncoding(withAllowedCharacters: allowed) ?? query
                let limit = max(1, min(20, appSettings.context_pack_limit))
                let data = try await request(path: "/v1/personal-profile?format=markdown&query=\(encoded)&limit=\(limit)", method: "GET")
                let profile = String(data: data, encoding: .utf8) ?? ""
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(profile, forType: .string)
                status = label
                await recordContextReuse(surface: surface, query: query, target: target)
            } catch {
                if !query.isEmpty {
                    status = "Profile failed: \(error.localizedDescription)"
                } else if let review {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(review.context_pack, forType: .string)
                    status = "Memory view prepared"
                    await recordContextReuse(surface: surface, query: query, target: target)
                } else {
                    status = "Profile failed: \(error.localizedDescription)"
                }
            }
        }
    }

    func loadDiagnostics() async {
        do {
            let data = try await request(path: "/v1/diagnostics", method: "GET")
            diagnostics = try JSONDecoder().decode(DiagnosticsResponse.self, from: data)
        } catch {
            status = "Diagnostics failed: \(error.localizedDescription)"
        }
    }

    func loadReliability() async {
        do {
            let data = try await request(path: "/v1/reliability/report", method: "GET")
            reliabilityReport = try JSONDecoder().decode(ReliabilityReportResponse.self, from: data)
        } catch {
            status = "Reliability check failed: \(error.localizedDescription)"
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
                status = "Backup failed: \(error.localizedDescription)"
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
                lastRepairSummary = "Repair failed: \(error.localizedDescription)"
                status = "Repair failed: \(error.localizedDescription)"
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
                status = "Rebuild failed: \(error.localizedDescription)"
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
                status = "Support bundle failed: \(error.localizedDescription)"
            }
        }
    }

    func approveCapture(_ capture: CaptureItem) {
        Task {
            do {
                _ = try await request(path: "/v1/captures/\(capture.id)/approve", method: "POST")
                status = "Approved capture"
                markFirstMemoryReviewed()
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
                status = "Approve failed: \(error.localizedDescription)"
            }
        }
    }

    func archiveCapture(_ capture: CaptureItem) {
        Task {
            do {
                _ = try await request(path: "/v1/captures/\(capture.id)/archive", method: "POST")
                status = "Archived capture"
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
                status = "Archive failed: \(error.localizedDescription)"
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
                status = "Forget failed: \(error.localizedDescription)"
            }
        }
    }

    func deleteImport(_ item: SourceImportHistoryItem) {
        Task {
            do {
                let data = try await request(path: "/v1/imports/\(item.import_id)", method: "DELETE")
                let response = try JSONDecoder().decode(SourceImportDeleteResponse.self, from: data)
                if response.deleted {
                    status = "Removed \(response.deleted_captures) imported capture\(response.deleted_captures == 1 ? "" : "s")"
                } else {
                    status = "Import already removed"
                }
                await refreshAfterCapture()
            } catch {
                status = "Remove import failed: \(error.localizedDescription)"
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
                status = "Delete backups failed: \(error.localizedDescription)"
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
                status = "Restore failed: \(error.localizedDescription)"
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
                status = "Delete all data failed: \(error.localizedDescription)"
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
            status = "Export failed: \(error.localizedDescription)"
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
                updateStatus = "Update check failed: \(error.localizedDescription)"
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
            throw NSError(domain: "Cortex", code: http.statusCode, userInfo: [NSLocalizedDescriptionKey: "HTTP \(http.statusCode)"])
        }
        return data
    }

    private func notify(_ title: String, _ body: String) {
        let center = UNUserNotificationCenter.current()
        let shouldRequestPermission = UserDefaults.standard.bool(forKey: "onboardingComplete.v1") && !showOnboarding
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
                guard shouldRequestPermission else { return }
                center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
                    if granted {
                        send()
                    }
                }
            default:
                break
            }
        }
    }
}

struct CortexView: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(spacing: 0) {
            header
            TabView(selection: $state.selectedTab) {
                ModelTab(state: state)
                    .tabItem { Label("Model", systemImage: "brain.head.profile") }
                    .tag(AppTab.model)
                SourcesTab(state: state)
                    .tabItem { Label("Sources", systemImage: "tray.and.arrow.down") }
                    .tag(AppTab.sources)
                ReviewTab(state: state)
                    .tabItem { Label("Review", systemImage: "checklist") }
                    .tag(AppTab.review)
                SearchTab(state: state)
                    .tabItem { Label("Ask", systemImage: "magnifyingglass") }
                    .tag(AppTab.ask)
                TrustTab(state: state)
                    .tabItem { Label("Trust", systemImage: "lock.shield") }
                    .tag(AppTab.trust)
            }
            footer
        }
        .frame(minWidth: 520, minHeight: 620)
        .sheet(isPresented: $state.showOnboarding) {
            OnboardingView(state: state)
                .frame(width: 760, height: 660)
        }
        .sheet(isPresented: $state.showImportPreview) {
            ImportPreviewSheet(state: state)
                .frame(width: 680, height: 560)
        }
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("Cortex")
                    .font(.title2)
                    .fontWeight(.semibold)
                Text("Private adaptation layer for your work, memory, and style.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            Spacer()
            Button {
                state.selectedTab = .sources
                state.status = "Import sources to build your model"
            } label: {
                Label("Add Sources", systemImage: "tray.and.arrow.down")
            }
        }
        .padding(16)
        .background(Color(nsColor: .windowBackgroundColor))
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
                .background(Color(nsColor: .controlBackgroundColor))
            }
        }
    }

    private var footerNeedsAttention: Bool {
        let status = state.displayStatus.lowercased()
        return status.contains("error")
            || status.contains("failed")
            || status.contains("offline")
            || status.contains("unhealthy")
            || status.contains("denied")
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

    var body: some View {
        VStack(alignment: .leading, spacing: compact ? 12 : 16) {
            header
            summary
            quickActions
            manualHandoffActions
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
        .onAppear {
            state.refreshIntegrationStates()
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text("AI access")
                .font(compact ? .headline : .title3)
                .fontWeight(.semibold)
            Text("Give trusted tools direct MCP access to approved memory, with redacted browser handoffs as a fallback.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var summary: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 130), spacing: 8)], spacing: 8) {
            IntegrationMetricBadge(title: "Connected", value: "\(connectedCount)", systemImage: "checkmark.seal.fill", color: .green)
            IntegrationMetricBadge(title: "Detected", value: "\(detectedCount)", systemImage: "app.badge.checkmark", color: .accentColor)
            IntegrationMetricBadge(title: "Local API", value: state.endpoint.replacingOccurrences(of: "http://", with: ""), systemImage: "network", color: .purple)
        }
    }

    private var quickActions: some View {
        HStack {
            Button {
                state.installDetectedIntegrations()
            } label: {
                Label("Install Tools", systemImage: "wand.and.stars")
            }
            .buttonStyle(.borderedProminent)

            if !compact {
                Button {
                    state.refreshIntegrationStates()
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }

            Spacer()
        }
    }

    private var manualHandoffActions: some View {
        DisclosureGroup("Manual setup and browser fallback") {
            VStack(alignment: .leading, spacing: 8) {
                Text("Use these only when a tool cannot install or call Cortex directly.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                HStack {
                    Button {
                        state.copyMCPConfig()
                    } label: {
                        Label("Copy MCP Setup", systemImage: "doc.on.doc")
                    }

                    Button {
                        if let chatGPT = state.integrations.first(where: { $0.id == "chatgpt" }) {
                            state.copyIntegrationContext(chatGPT)
                        } else {
                            state.copyDailyContextPack()
                        }
                    } label: {
                        Label("Prepare Browser Memory", systemImage: "text.quote")
                    }
                    Spacer()
                }
            }
            .padding(.top, 4)
        }
    }

    private func integrations(in category: IntegrationCategory) -> [AIIntegration] {
        state.integrations.filter { $0.category == category }
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
        .background(Color(nsColor: .controlBackgroundColor))
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

            HStack(spacing: 8) {
                if integration.supportsInstall {
                    Button {
                        state.installIntegration(integration)
                    } label: {
                        Label(integrationState.configured ? "Repair" : "Install", systemImage: integrationState.configured ? "wrench.and.screwdriver" : "plus.circle")
                    }
                    .buttonStyle(.borderedProminent)

                    Button {
                        state.copyMCPConfig(for: integration)
                    } label: {
                        Label("Setup", systemImage: "doc.on.doc")
                    }

                    Button {
                        state.openIntegrationConfig(integration)
                    } label: {
                        Label("Open", systemImage: "folder")
                    }
                } else {
                    Button {
                        state.copyIntegrationContext(integration)
                    } label: {
                        Label("Prepare", systemImage: "text.quote")
                    }
                    .buttonStyle(.borderedProminent)

                    Button {
                        state.copyIntegrationGuide(integration)
                    } label: {
                        Label("Guide", systemImage: "list.bullet.clipboard")
                    }
                }

                if integration.supportsInstall && !compact {
                    Button {
                        state.copyIntegrationGuide(integration)
                    } label: {
                        Label("Guide", systemImage: "questionmark.circle")
                    }
                }

                Spacer(minLength: 0)
            }
            .labelStyle(.titleAndIcon)

            Text(integration.restartHint)
                .font(.caption2)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
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
        if supportsInstall && state.appInstalled { return "Detected" }
        if supportsInstall && state.configExists { return "Config" }
        return supportsInstall ? "Ready" : "Paste"
    }

    private var color: Color {
        if state.configured { return .green }
        if state.appInstalled { return .accentColor }
        if state.configExists { return .orange }
        return supportsInstall ? .secondary : .purple
    }
}

struct OnboardingView: View {
    @ObservedObject var state: AppState
    private let steps = OnboardingStep.allCases

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider()
            VStack(spacing: 0) {
                header
                Divider()
                ScrollView {
                    stepContent
                        .padding(22)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                Divider()
                footer
            }
        }
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
                Image(systemName: "brain.head.profile")
                    .font(.system(size: 28, weight: .semibold))
                    .foregroundColor(.accentColor)
                Text("Cortex")
                    .font(.title2)
                    .fontWeight(.semibold)
                Text("Private personal model")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            VStack(alignment: .leading, spacing: 8) {
                ForEach(steps) { step in
                    OnboardingStepRow(
                        step: step,
                        selected: state.onboardingStep == step,
                        completed: state.onboardingStepIsComplete(step)
                    )
                }
            }

            Spacer()

            HStack(spacing: 6) {
                Image(systemName: "lock.shield")
                Text("Local-first")
            }
            .font(.caption)
            .foregroundColor(.secondary)
            .padding(.horizontal, 8)
            .padding(.vertical, 5)
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .padding(20)
        .frame(minWidth: 190, maxWidth: 190, maxHeight: .infinity, alignment: .topLeading)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.55))
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 5) {
                Text("Step \(state.onboardingStepIndex + 1) of \(steps.count)")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Text(state.onboardingStep.title)
                    .font(.title2)
                    .fontWeight(.semibold)
                Text(state.onboardingStep.subtitle)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            Button {
                state.dismissOnboardingForSession()
            } label: {
                Label("Finish Later", systemImage: "xmark")
            }
        }
        .padding(22)
    }

    private var footer: some View {
        HStack {
            Button {
                state.previousOnboardingStep()
            } label: {
                Label("Back", systemImage: "chevron.left")
            }
            .disabled(state.onboardingStep == .privateVault)

            Spacer()

            Text(state.displayStatus)
                .font(.caption)
                .foregroundColor(.secondary)
                .lineLimit(1)
                .frame(maxWidth: 260)

            Spacer()

            if state.onboardingStep == .trustBackup {
                Button {
                    state.completeOnboarding()
                } label: {
                    Label("Start Using Cortex", systemImage: "checkmark.circle")
                }
                .buttonStyle(.borderedProminent)
                .disabled(!state.canCompleteOnboarding)
            } else {
                Button {
                    state.nextOnboardingStep()
                } label: {
                    Label("Continue", systemImage: "chevron.right")
                }
                .buttonStyle(.borderedProminent)
                .disabled(!state.canAdvanceOnboarding)
            }
        }
        .padding(18)
    }

    @ViewBuilder
    private var stepContent: some View {
        switch state.onboardingStep {
        case .privateVault:
            OnboardingVaultStep(state: state)
        case .firstSource:
            OnboardingFirstSourceStep(state: state)
        case .reviewMemory:
            OnboardingReviewMemoryStep(state: state)
        case .askUse:
            OnboardingAskUseStep(state: state)
        case .trustBackup:
            OnboardingTrustBackupStep(state: state)
        }
    }
}

struct OnboardingStepRow: View {
    let step: OnboardingStep
    let selected: Bool
    let completed: Bool

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: completed ? "checkmark.circle.fill" : step.systemImage)
                .frame(width: 18)
            Text(step.title)
            Spacer()
        }
        .font(.caption)
        .fontWeight(selected ? .semibold : .regular)
        .foregroundColor(selected || completed ? .accentColor : .secondary)
        .padding(.horizontal, 9)
        .padding(.vertical, 7)
        .background((selected ? Color.accentColor.opacity(0.12) : Color(nsColor: .controlBackgroundColor)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct OnboardingVaultStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Your memory stays in a normal folder on this Mac. Cortex uses a local index for speed, but the vault files remain readable, portable, and backup-friendly.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 132), spacing: 8)], spacing: 8) {
                OnboardingValueBadge(title: "Readable", detail: "JSON files", systemImage: "doc.text")
                OnboardingValueBadge(title: "Recoverable", detail: "Rebuild index", systemImage: "arrow.clockwise")
                OnboardingValueBadge(title: "Private", detail: "No account", systemImage: "lock.shield")
            }

            VStack(alignment: .leading, spacing: 8) {
                Text("Vault folder")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Text(state.vaultPath)
                    .font(.system(.caption, design: .monospaced))
                    .lineLimit(2)
                    .textSelection(.enabled)
                HStack {
                    Button {
                        state.useDefaultVaultFolder()
                    } label: {
                        Label("Use Default", systemImage: "house")
                    }
                    Button {
                        state.chooseVaultFolder()
                    } label: {
                        Label("Choose Folder", systemImage: "folder")
                    }
                    Button {
                        state.openVaultFolder()
                    } label: {
                        Label("Open Folder", systemImage: "arrow.up.right.square")
                    }
                    Spacer()
                }
            }
            .padding(12)
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            if let vault = state.diagnostics?.vault {
                VStack(alignment: .leading, spacing: 8) {
                    OnboardingCheckRow(title: "Vault ready", detail: vault.path, systemImage: "checkmark.seal.fill", color: .green)
                    OnboardingCheckRow(title: "Local index", detail: vault.index_path, systemImage: "bolt.horizontal.circle.fill", color: .accentColor)
                    OnboardingCheckRow(title: "Audit log", detail: "\(vault.event_count) events", systemImage: "list.bullet.rectangle", color: .secondary)
                }
            } else {
                OnboardingCheckRow(title: "Starting local backend", detail: state.backendStatus, systemImage: "clock", color: .orange)
            }
        }
    }
}

struct OnboardingFirstSourceStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Start with the sources that carry your real context: AI chat exports, notes, docs, email, messages, writing samples, decisions, bookmarks, calendar, and project files.")
                .foregroundColor(.secondary)

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 145), spacing: 8)], spacing: 8) {
                OnboardingValueBadge(title: "AI chats", detail: "ChatGPT, Claude", systemImage: "bubble.left.and.bubble.right")
                OnboardingValueBadge(title: "Work tools", detail: "Slack, Notion, docs", systemImage: "folder.badge.gearshape")
                OnboardingValueBadge(title: "Personal data", detail: "Email, messages, notes", systemImage: "person.text.rectangle")
                OnboardingValueBadge(title: "Style", detail: "Writing samples", systemImage: "signature")
            }

            HStack {
                Button {
                    state.chooseFilesForCapture()
                } label: {
                    Label("Choose Sources", systemImage: "doc.badge.plus")
                }
                .buttonStyle(.borderedProminent)
                Button {
                    state.openCaptureInbox()
                } label: {
                    Label("Open Inbox", systemImage: "tray")
                }
                Spacer()
            }

            DisclosureGroup("Add a quick memory instead") {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Quick memories are useful later, but setup continues after Cortex imports a real source.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    ZStack(alignment: .topLeading) {
                        TextEditor(text: $state.onboardingNote)
                            .font(.body)
                            .frame(minHeight: 120)
                            .accessibilityLabel("First source")
                        if state.onboardingNote.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                            Text("Example: I prefer concise technical answers with clear next steps.")
                                .foregroundColor(.secondary)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 8)
                                .allowsHitTesting(false)
                        }
                    }
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.22)))
                    HStack {
                        Button {
                            state.captureOnboardingQuickNote()
                        } label: {
                            Label("Add Quick Memory", systemImage: "square.and.arrow.down")
                        }
                        Button {
                            state.captureClipboard()
                        } label: {
                            Label("Add Clipboard", systemImage: "doc.on.clipboard")
                        }
                        Spacer()
                    }
                }
                .padding(.top, 8)
            }

            if let stats = state.stats {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 110), spacing: 8)], spacing: 8) {
                    StatBox(label: "Captures", value: stats.captures)
                    StatBox(label: "Pending", value: stats.pending_captures)
                    StatBox(label: "Memories", value: stats.memories)
                }
            }

            OnboardingCheckRow(
                title: state.onboardingHasSource ? "First source imported" : "Waiting for an imported source",
                detail: state.onboardingHasSource ? "Continue to review and approve useful memory." : "Choose sources, drop files, or import the inbox.",
                systemImage: state.onboardingHasSource ? "checkmark.seal.fill" : "tray.and.arrow.down",
                color: state.onboardingHasSource ? .green : .orange
            )
        }
    }
}

struct OnboardingReviewMemoryStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Cortex works best when the first memories are reviewed. Approve useful signals and archive anything noisy before the model starts adapting around them.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            if state.inbox.isEmpty {
                QuietState(title: "No pending memory", detail: state.onboardingHasReviewedMemory ? "You already have approved memory." : "Import a source or add a quick memory, then return here.")
            } else {
                ForEach(state.inbox.prefix(3)) { capture in
                    CaptureCard(
                        capture: capture,
                        approve: { state.approveCapture(capture) },
                        archive: { state.archiveCapture(capture) }
                    )
                }
            }

            HStack {
                Button {
                    Task {
                        await state.loadInbox()
                        await state.loadReview()
                        await state.loadStats()
                    }
                } label: {
                    Label("Refresh Review", systemImage: "arrow.clockwise")
                }
                Button {
                    state.selectedTab = .review
                    state.dismissOnboardingForSession()
                } label: {
                    Label("Open Full Review", systemImage: "checklist")
                }
                Spacer()
            }

            OnboardingCheckRow(
                title: state.onboardingHasReviewedMemory ? "Memory reviewed" : "Approve one useful memory",
                detail: state.onboardingHasReviewedMemory ? "Cortex has at least one approved memory to use." : "Approve a pending item, or add another source if the current source was noisy.",
                systemImage: state.onboardingHasReviewedMemory ? "checkmark.seal.fill" : "tray.full",
                color: state.onboardingHasReviewedMemory ? .green : .orange
            )
        }
        .task {
            await state.loadInbox()
            await state.loadReview()
            await state.loadStats()
        }
    }
}

struct OnboardingAskUseStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Ask Cortex about the memory you just approved. This proves the local model can retrieve useful personal context before any AI tool uses it.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 10) {
                TextField("Ask about your first source", text: $state.searchQuery)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { state.runSearch() }
                HStack {
                    Button {
                        state.runSearch()
                    } label: {
                        Label("Ask Cortex", systemImage: "magnifyingglass")
                    }
                    .buttonStyle(.borderedProminent)
                    Spacer()
                }
            }
            .padding(12)
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            if state.hasSearched && !state.askAnswer.isEmpty {
                AskAnswerPanel(answer: state.askAnswer, citations: state.askCitations)
            }

            if !state.searchResults.isEmpty {
                ForEach(state.searchResults.prefix(3)) { item in
                    MemoryCard(item: item) {
                        state.deleteMemory(item)
                    }
                }
            } else {
                QuietState(title: "Ask one real question", detail: "Use a person, project, decision, preference, or phrase from your approved source.")
            }

            OnboardingCheckRow(
                title: state.onboardingHasUsedCortex ? "Cortex used once" : "Use Cortex once",
                detail: state.onboardingHasUsedCortex ? "The personal model returned cited memory from your approved source." : "Run a query that returns memory from the source you reviewed.",
                systemImage: state.onboardingHasUsedCortex ? "checkmark.seal.fill" : "sparkle.magnifyingglass",
                color: state.onboardingHasUsedCortex ? .green : .orange
            )
        }
    }
}

struct OnboardingTrustBackupStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Finish by choosing the trust posture for connected AI tools and deciding how the local vault should be backed up.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 10) {
                Toggle("Review new memories first", isOn: $state.appSettings.review_new_captures)
                Toggle("Strict mode: only approved memory in AI context", isOn: Binding(
                    get: { !state.appSettings.allow_pending_in_context },
                    set: { state.appSettings.allow_pending_in_context = !$0 }
                ))
                Toggle("Redact shared context", isOn: $state.appSettings.redact_sensitive_context)
                HStack {
                    Button {
                        state.saveMemorySettings()
                    } label: {
                        Label("Save Trust Settings", systemImage: "checkmark.circle")
                    }
                    .buttonStyle(.borderedProminent)
                    Button {
                        state.selectedTab = .trust
                        state.dismissOnboardingForSession()
                    } label: {
                        Label("Open Trust", systemImage: "lock.shield")
                    }
                    Spacer()
                }
            }
            .padding(12)
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            HStack {
                Button {
                    state.createBackup()
                } label: {
                    Label("Back Up Now", systemImage: "archivebox")
                }
                .buttonStyle(.borderedProminent)
                Button {
                    state.skipFirstBackup()
                } label: {
                    Label("Skip First Backup", systemImage: "forward")
                }
                Button {
                    state.openVaultFolder()
                } label: {
                    Label("Open Vault", systemImage: "folder")
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

            OnboardingCheckRow(
                title: state.onboardingHasBackupDecision ? "Backup decision recorded" : "Back up or skip explicitly",
                detail: state.onboardingHasBackupDecision ? "Setup can be completed." : "Create a first backup, or explicitly skip it for now.",
                systemImage: state.onboardingHasBackupDecision ? "checkmark.seal.fill" : "externaldrive",
                color: state.onboardingHasBackupDecision ? .green : .orange
            )
        }
    }
}

struct OnboardingValueBadge: View {
    let title: String
    let detail: String
    let systemImage: String

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: systemImage)
                .foregroundColor(.accentColor)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(.caption)
                    .fontWeight(.semibold)
                Text(detail)
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            Spacer()
        }
        .padding(9)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct OnboardingCheckRow: View {
    let title: String
    let detail: String
    let systemImage: String
    let color: Color

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: systemImage)
                .foregroundColor(color)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .fontWeight(.medium)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
            }
            Spacer()
        }
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
            SectionHeader(title: "Source health", detail: "Imported batches stay removable, and pending source records wait for review.")
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
                QuietState(title: "No sources yet", detail: "Choose sources or drop exports here to start building memory.")
            }
        }
    }
}

struct SourceReadinessPanel: View {
    let report: SourceReadinessResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: 8)], spacing: 8) {
                SourceConnectivityMetric(title: "Import ready", value: "\(report.summary.import_ready)", systemImage: "tray.and.arrow.down.fill", color: .accentColor)
                SourceConnectivityMetric(title: "Connected", value: "\(report.summary.connected)", systemImage: "link.circle.fill", color: report.summary.connected == 0 ? .secondary : .green)
                SourceConnectivityMetric(title: "With memory", value: "\(report.summary.sources_with_data)", systemImage: "brain.head.profile", color: report.summary.sources_with_data == 0 ? .secondary : .blue)
                SourceConnectivityMetric(title: "Attention", value: "\(report.summary.needs_attention + report.summary.needs_review)", systemImage: "exclamationmark.triangle.fill", color: report.summary.needs_attention + report.summary.needs_review == 0 ? .secondary : .orange)
            }

            if let recommendation = report.recommendations.first {
                HStack(spacing: 8) {
                    Image(systemName: report.summary.needs_attention == 0 && report.summary.needs_review == 0 ? "checkmark.seal.fill" : "lightbulb.fill")
                        .foregroundColor(report.summary.needs_attention == 0 && report.summary.needs_review == 0 ? .green : .orange)
                        .frame(width: 18)
                    Text(recommendation)
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
                ForEach(report.sources.prefix(5)) { source in
                    SourceReadinessRow(source: source)
                }
            }
        }
        .padding(10)
        .background(Color(nsColor: .textBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
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
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var detail: String {
        let memoryText = "\(source.active_memories) memories"
        let reviewText = source.pending > 0 ? "\(source.pending) review" : "\(source.approved) approved"
        if source.needsAttention, let warning = source.warnings.first {
            return "\(warning) · \(memoryText) · \(reviewText)"
        }
        return "\(source.next_action) · \(memoryText) · \(reviewText)"
    }
}

struct SourceConnectivityPanel: View {
    @ObservedObject var state: AppState

    private var importReadyCount: Int {
        state.sourceConnectorCatalog.filter(\.isImportReady).count
    }

    private var livePlannedCount: Int {
        state.sourceConnectorCatalog.filter(\.isLivePlanned).count
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
                SourceConnectivityMetric(title: "Import ready", value: "\(importReadyCount)", systemImage: "tray.and.arrow.down.fill", color: .accentColor)
                SourceConnectivityMetric(title: "Live planned", value: "\(livePlannedCount)", systemImage: "arrow.triangle.2.circlepath", color: .blue)
                SourceConnectivityMetric(title: "Connected", value: "\(state.sourceAccounts.count)", systemImage: "link.circle.fill", color: state.sourceAccounts.isEmpty ? .secondary : .green)
                SourceConnectivityMetric(title: "Needs attention", value: "\(accountsNeedingAttention.count + cursorErrors)", systemImage: "exclamationmark.triangle.fill", color: accountsNeedingAttention.isEmpty && cursorErrors == 0 ? .secondary : .orange)
            }

            if state.sourceAccounts.isEmpty {
                HStack(spacing: 8) {
                    Image(systemName: "point.3.connected.trianglepath.dotted")
                        .foregroundColor(.secondary)
                    Text("No live source accounts connected")
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
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct SourceAccountHealthRow: View {
    let account: SourceAccountItem
    let cursor: SyncCursorItem?

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: account.needsAttention || cursor?.needsAttention == true ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                .foregroundColor(account.needsAttention || cursor?.needsAttention == true ? .orange : .green)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(account.account_label.isEmpty ? account.source : account.account_label)
                    .font(.callout)
                    .fontWeight(.medium)
                    .lineLimit(1)
                Text(detail)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .padding(8)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var detail: String {
        if let error = account.last_error ?? cursor?.last_error {
            return error
        }
        if let synced = account.last_sync_at ?? cursor?.last_completed_at {
            return "\(account.source) · \(account.status) · synced \(shortDate(synced))"
        }
        return "\(account.source) · \(account.status) · \(account.auth_state)"
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

struct CaptureQuickNoteSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Quick signal")
                .font(.headline)
            TextEditor(text: $state.quickNote)
                .font(.body)
                .frame(minHeight: 110)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.2)))
            HStack {
                Button {
                    state.captureQuickNote()
                } label: {
                    Label("Add to Model", systemImage: "square.and.arrow.down")
                }
                .buttonStyle(.borderedProminent)
                Button {
                    state.captureClipboard()
                } label: {
                    Label("Add Clipboard", systemImage: "doc.on.clipboard")
                }
                Spacer()
            }
        }
    }
}

struct CaptureWebSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField("URL", text: $state.captureURLString)
                .textFieldStyle(.roundedBorder)
            TextField("Title or source name", text: $state.captureTitle)
                .textFieldStyle(.roundedBorder)
            TextEditor(text: $state.captureNotes)
                .font(.body)
                .frame(minHeight: 80)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.2)))
            HStack {
                Button {
                    state.captureURLSurface()
                } label: {
                    Label("Add Link", systemImage: "link.badge.plus")
                }
                .buttonStyle(.borderedProminent)
                Button {
                    state.copyBrowserBookmarklet()
                } label: {
                    Label("Copy Capture Bookmarklet", systemImage: "bookmark")
                }
                Button {
                    state.openBrowserCapturePage()
                } label: {
                    Label("Open Capture Page", systemImage: "safari")
                }
                Spacer()
            }
            Text("Use the bookmarklet to send selected pages or research notes to the local importer. Cortex does not place your API token in browser URLs or page scripts.")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding(12)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct ImportPreviewSheet: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Review Source Import")
                        .font(.title2)
                        .fontWeight(.semibold)
                    Text("\(state.importPreviewURLs.count) selected source\(state.importPreviewURLs.count == 1 ? "" : "s")")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
                Button {
                    state.cancelImportPreview()
                } label: {
                    Label("Close", systemImage: "xmark")
                }
                .labelStyle(.iconOnly)
                .help("Close")
            }

            if let preview = state.importPreview {
                HStack(spacing: 8) {
                    ImportMetric(label: "Records", value: preview.records_found)
                    ImportMetric(label: "Sources", value: preview.sources.count)
                    ImportMetric(label: "Samples", value: preview.sample.count)
                }

                if !preview.sources.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Detected Types")
                            .font(.headline)
                        FlowWrap(items: preview.sources.map { "\($0.source) \($0.count)" })
                    }
                }

                VStack(alignment: .leading, spacing: 8) {
                    Text("Sample Records")
                        .font(.headline)
                    if preview.sample.isEmpty {
                        QuietState(title: "No sample records", detail: "Cortex did not find structured records in the selected sources.")
                    } else {
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 8) {
                                ForEach(preview.sample, id: \.self) { sample in
                                    HStack(alignment: .top, spacing: 10) {
                                        Image(systemName: "doc.text.magnifyingglass")
                                            .foregroundColor(.accentColor)
                                            .frame(width: 18)
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(sample.title.isEmpty ? "Untitled record" : sample.title)
                                                .font(.headline)
                                                .lineLimit(2)
                                            Text("\(sample.source) · \(ByteCountFormatter.string(fromByteCount: Int64(sample.chars), countStyle: .file))")
                                                .font(.caption)
                                                .foregroundColor(.secondary)
                                        }
                                        Spacer()
                                    }
                                    .padding(9)
                                    .background(Color(nsColor: .controlBackgroundColor))
                                    .clipShape(RoundedRectangle(cornerRadius: 8))
                                }
                            }
                        }
                    }
                }
            } else {
                QuietState(title: "Preview unavailable", detail: "Choose sources again to scan them before import.")
            }

            Spacer(minLength: 0)
            HStack {
                Button {
                    state.cancelImportPreview()
                } label: {
                    Label("Cancel", systemImage: "xmark")
                }
                Spacer()
                Button {
                    state.confirmImportPreview()
                } label: {
                    Label("Import to Model", systemImage: "tray.and.arrow.down.fill")
                }
                .buttonStyle(.borderedProminent)
                .disabled(state.importPreview?.records_found ?? 0 == 0)
            }
        }
        .padding(18)
    }
}

struct ImportMetric: View {
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
        .padding(10)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct FlowWrap: View {
    let items: [String]

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: 8)], alignment: .leading, spacing: 8) {
            ForEach(items, id: \.self) { item in
                Text(item)
                    .font(.caption)
                    .lineLimit(1)
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.accentColor.opacity(0.12))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
    }
}

struct ImportHistorySection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Import History")
                    .font(.headline)
                Spacer()
                Button {
                    Task { await state.loadImportHistory() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .labelStyle(.iconOnly)
                .help("Refresh")
            }
            if state.importHistory.isEmpty {
                QuietState(title: "No imports yet", detail: "Confirmed source imports appear here as removable batches.")
            } else {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(state.importHistory) { item in
                        ImportHistoryRow(item: item) {
                            state.deleteImport(item)
                        }
                    }
                }
            }
        }
    }
}

struct ImportHistoryRow: View {
    let item: SourceImportHistoryItem
    let undoImport: () -> Void
    @State private var confirmUndo = false

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: iconName)
                .foregroundColor(iconColor)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 5) {
                HStack(alignment: .firstTextBaseline) {
                    Text(title)
                        .font(.headline)
                        .lineLimit(1)
                    Spacer()
                    Text(String(item.created_at.prefix(10)))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Text(summary)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
                HStack(spacing: 10) {
                    Text("\(item.remaining_memories) memories")
                    Text("\(item.remaining_tasks) tasks")
                    if (item.skipped ?? 0) > 0 {
                        Text("\(item.skipped ?? 0) skipped")
                    }
                    if item.failed > 0 {
                        Text("\(item.failed) failed")
                    }
                    Spacer()
                    Button {
                        confirmUndo = true
                    } label: {
                        Label("Undo Import", systemImage: "arrow.uturn.backward")
                    }
                    .font(.caption)
                    .disabled(!item.can_delete)
                    .confirmationDialog("Undo this import?", isPresented: $confirmUndo) {
                        Button("Remove Imported Data", role: .destructive) {
                            undoImport()
                        }
                        Button("Cancel", role: .cancel) {}
                    } message: {
                        Text("Cortex will remove captures and derived memory created by this import batch.")
                    }
                }
                .font(.caption)
                .foregroundColor(.secondary)
            }
        }
        .padding(10)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var title: String {
        if let first = item.sources.first?.source, !first.isEmpty {
            return item.sources.count > 1 ? "\(first) + \(item.sources.count - 1) more" : first
        }
        return item.source_hint.isEmpty ? "Source import" : item.source_hint
    }

    private var summary: String {
        let sourceSummary = item.sources.prefix(3).map { "\($0.source): \($0.count)" }.joined(separator: ", ")
        let skipped = item.skipped ?? 0
        let skippedText = skipped > 0 ? ", \(skipped) skipped" : ""
        let base = "\(item.records_found) record\(item.records_found == 1 ? "" : "s"), \(item.queued) queued, \(item.saved) saved\(skippedText)"
        return sourceSummary.isEmpty ? base : "\(base) · \(sourceSummary)"
    }

    private var iconName: String {
        item.status == "partial" ? "exclamationmark.triangle.fill" : "tray.full.fill"
    }

    private var iconColor: Color {
        item.status == "partial" ? .orange : .accentColor
    }
}

struct SearchTab: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Ask Cortex")
                    .font(.title3)
                    .fontWeight(.semibold)
                Text("Search your approved personal model with citations before an agent uses the memory.")
                    .foregroundColor(.secondary)
            }

            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    TextField("Ask about a person, project, decision, preference, or writing pattern", text: $state.searchQuery)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { state.runSearch() }
                    Button {
                        state.runSearch()
                    } label: {
                        Label("Ask", systemImage: "magnifyingglass")
                    }
                    .buttonStyle(.borderedProminent)
                }
                HStack {
                    if state.hasSearched {
                        Text("\(state.searchResults.count) cited result\(state.searchResults.count == 1 ? "" : "s")")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    Spacer()
                }
                DisclosureGroup("AI handoff options") {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Use these when a tool cannot connect to Cortex directly.")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        HStack {
                            Button {
                                state.copyAgentAdaptation()
                            } label: {
                                Label("Agent Layer", systemImage: "wand.and.stars")
                            }
                            Button {
                                state.contextQuery = state.searchQuery
                                state.copyContextPack()
                            } label: {
                                Label("Focused Context", systemImage: "text.quote")
                            }
                            Button {
                                state.copyDailyContextPack()
                            } label: {
                                Label("Model Context", systemImage: "brain.head.profile")
                            }
                            Spacer()
                        }
                    }
                    .padding(.top, 4)
                }
            }
            .padding(12)
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            if state.hasSearched && !state.askAnswer.isEmpty {
                AskAnswerPanel(answer: state.askAnswer, citations: state.askCitations)
            }

            if state.searchResults.isEmpty && !state.hasSearched {
                QuietState(title: "Ask your model", detail: "Try a question about a project, person, decision, preference, or phrase from your imported sources.")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if state.searchResults.isEmpty {
                QuietState(title: "No saved memory matched that", detail: "Try a different person, project, decision, or topic, or import more source context.")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        ForEach(state.searchResults) { item in
                            MemoryCard(item: item) {
                                state.deleteMemory(item)
                            }
                        }
                    }
                }
            }
        }
        .padding(16)
    }
}

struct AskAnswerPanel: View {
    let answer: String
    let citations: [AskCitationItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label("Answer", systemImage: "quote.bubble")
                    .font(.headline)
                Spacer()
                if !citations.isEmpty {
                    Text("\(citations.count) citation\(citations.count == 1 ? "" : "s")")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
            Text(answer)
                .font(.body)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            if !citations.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(citations.prefix(4)) { citation in
                        HStack(spacing: 6) {
                            Text("[\(citation.index)]")
                                .font(.caption)
                                .fontWeight(.semibold)
                                .foregroundColor(.secondary)
                            Text(citation.source_url ?? citation.source)
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                                .truncationMode(.middle)
                            Spacer(minLength: 0)
                        }
                    }
                }
            }
        }
        .padding(12)
        .background(Color(nsColor: .textBackgroundColor))
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
                TrustNotice(systemImage: "checkmark.shield.fill", title: "No active trust warnings", detail: "New memory is reviewed, shared context is redacted, and storage health is clean.", color: .green)
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
        .background(Color(nsColor: .controlBackgroundColor))
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
        .background(Color(nsColor: .controlBackgroundColor))
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

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Connected AI Access")
                    .font(.headline)
                Spacer()
                Button {
                    Task { await state.loadTrust() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }

            Text("Choose what connected AI tools can do with memory stored on this Mac.")
                .font(.body)
                .foregroundColor(.secondary)

            Picker("Connected AI Access", selection: $selectedPreset) {
                ForEach(TrustPreset.allCases) { preset in
                    Text(preset.title).tag(preset)
                }
            }
            .pickerStyle(.segmented)
            .onAppear {
                selectedPreset = state.currentTrustPreset()
                advancedExpanded = selectedPreset == .advanced
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
                if preset == .advanced {
                    advancedExpanded = true
                }
            }

            TrustNotice(systemImage: "shield.lefthalf.filled", title: selectedPreset.title, detail: selectedPreset.detail, color: selectedPreset == .privateMode ? .green : .accentColor)

            DisclosureGroup("Advanced permissions", isExpanded: $advancedExpanded) {
                VStack(alignment: .leading, spacing: 12) {
                    TrustToggleRow(
                        title: "Review new saves",
                        detail: "New captures enter the inbox before you treat them as trusted.",
                        systemImage: "tray.full",
                        isOn: $state.appSettings.review_new_captures
                    )
                    TrustToggleRow(
                        title: "Let AI use pending saves",
                        detail: "Turn this off when only approved captures should appear in search and AI access.",
                        systemImage: "lock.open",
                        isOn: $state.appSettings.allow_pending_in_context
                    )
                    TrustToggleRow(
                        title: "Let connected AI read memory",
                        detail: "Connected MCP agents can search memory, read review context, and inspect stats.",
                        systemImage: "eye",
                        isOn: $state.appSettings.allow_agent_reads
                    )
                    TrustToggleRow(
                        title: "Let connected AI save memory",
                        detail: "Connected MCP agents can save, approve, or archive memory.",
                        systemImage: "square.and.pencil",
                        isOn: $state.appSettings.allow_agent_writes
                    )
                    TrustToggleRow(
                        title: "Let connected AI prepare artifacts",
                        detail: "Connected MCP agents can prepare redacted handoffs, adaptation layers, context packs, or exports.",
                        systemImage: "square.and.arrow.up",
                        isOn: $state.appSettings.allow_agent_exports
                    )
                    TrustToggleRow(
                        title: "Let connected AI run maintenance",
                        detail: "Connected MCP agents can create backups, repair storage, or rebuild local indexes.",
                        systemImage: "wrench.and.screwdriver",
                        isOn: $state.appSettings.allow_agent_maintenance
                    )
                    TrustToggleRow(
                        title: "Let connected AI delete data",
                        detail: "Connected MCP agents can delete memories, captures, backups, or all local user data.",
                        systemImage: "trash",
                        isOn: $state.appSettings.allow_agent_destructive_actions
                    )
                    TrustToggleRow(
                        title: "Redact shared context",
                        detail: "Secrets, tokens, emails, and long account-like numbers are masked before sharing.",
                        systemImage: "text.badge.xmark",
                        isOn: $state.appSettings.redact_sensitive_context
                    )

                    VStack(alignment: .leading, spacing: 6) {
                        Label("Your source aliases", systemImage: "person.text.rectangle")
                            .font(.callout)
                            .fontWeight(.medium)
                        Text("Names, handles, or email addresses that mark Slack and email imports as written by you.")
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

                    Stepper(value: $state.appSettings.context_pack_limit, in: 4...50, step: 2) {
                        Text("Shared memory limit: \(state.appSettings.context_pack_limit)")
                    }
                }
                .padding(.top, 8)
            }

            if advancedExpanded {
                HStack {
                    Button {
                        state.saveMemorySettings()
                    } label: {
                        Label("Save Settings", systemImage: "checkmark.circle")
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
            Text("Sources")
                .font(.headline)
            if summary.source_counts.isEmpty {
                QuietState(title: "No sources yet", detail: "Captured memory sources will appear here.")
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
                QuietState(title: "No audit events yet", detail: "Captures, approvals, backups, settings, and agent tool calls will appear here.")
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

struct TrustActionsSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Privacy posture")
                .font(.headline)
            TrustNotice(systemImage: "lock.doc", title: "Local-first", detail: "Trust controls apply to the local backend, MCP agents, safe sharing artifacts, and exports. The vault remains on this Mac.", color: .accentColor)
            DisclosureGroup("Export and sharing fallback") {
                HStack {
                    Button {
                        state.copyDailyContextPack()
                    } label: {
                        Label("Prepare Redacted Artifact", systemImage: "doc.on.doc")
                    }
                    Spacer()
                }
                .padding(.top, 4)
            }
        }
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
                    Label("Reset MCP Token", systemImage: "key")
                }
            }

            if state.integrationTokens.isEmpty {
                QuietState(title: "No integration tokens", detail: "Connect AI tools to create the local MCP token.")
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
            Text("Setup")
                .font(.headline)
            HStack {
                Button {
                    state.showOnboardingAgain()
                } label: {
                    Label("Open Setup", systemImage: "sparkles")
                }
                Button {
                    state.openVaultFolder()
                } label: {
                    Label("Open Vault", systemImage: "folder")
                }
            Button {
                state.copyMCPConfig()
            } label: {
                Label("Copy MCP Setup", systemImage: "doc.on.doc")
            }
                Spacer()
            }
            Text("Vault: \(state.vaultPath)")
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
            Text("Backend")
                .font(.headline)
            TextField("Endpoint", text: $state.endpoint)
                .textFieldStyle(.roundedBorder)
            SecureField("API token", text: $state.apiKey)
                .textFieldStyle(.roundedBorder)
            HStack {
                Button {
                    state.persistSettings()
                } label: {
                    Label("Save Settings", systemImage: "checkmark.circle")
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
            Text("Backend: \(state.backendStatus)")
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
                    HealthPill(label: "Backend", value: report.backend_version)
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
            Text("Cortex only reads the clipboard when you click Save or enable the optional keyboard shortcut. No screen recording, no ambient capture, and no background upload.")
                .font(.body)
                .foregroundColor(.secondary)
            Toggle("Keyboard shortcut: Cmd Shift V saves the clipboard", isOn: $state.globalClipboardHotkeyEnabled)
                .onChange(of: state.globalClipboardHotkeyEnabled) { _ in
                    state.saveHotkeyPreference()
                }
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
            Text("This removes current captures, memories, tasks, graph data, settings, events, attachments, and backup archives from this vault.")
        }
    }
}

struct StatsGrid: View {
    let stats: StatsResponse?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let stats {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 86), spacing: 8)], spacing: 8) {
                    StatBox(label: "Captures", value: stats.captures)
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
        .background(Color(nsColor: .controlBackgroundColor))
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
        .background(Color(nsColor: .controlBackgroundColor))
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
                Text((capture.title ?? capture.source).isEmpty ? "Untitled capture" : (capture.title ?? capture.source))
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
                Button("Keep") { approve() }
                Button("Ignore") { archive() }
                Spacer()
            }
        }
        .padding(10)
        .background(Color(nsColor: .controlBackgroundColor))
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
                if let onArchive {
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
        .background(Color(nsColor: .controlBackgroundColor))
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

    private var citationLabel: String? {
        guard let sourceURL = item.source_url?.trimmingCharacters(in: .whitespacesAndNewlines),
              !sourceURL.isEmpty else {
            return nil
        }
        if sourceURL.hasPrefix("file://"), let url = URL(string: sourceURL) {
            return url.path
        }
        return sourceURL
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
        window.collectionBehavior = state.showOnboarding ? [.canJoinAllSpaces, .fullScreenAuxiliary] : [.moveToActiveSpace, .fullScreenAuxiliary]
        window.level = state.showOnboarding ? .screenSaver : .normal
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
        mainWindow.collectionBehavior = state.showOnboarding ? [.canJoinAllSpaces, .fullScreenAuxiliary] : [.moveToActiveSpace, .fullScreenAuxiliary]
        mainWindow.level = state.showOnboarding ? .screenSaver : .normal
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
        if state.showOnboarding {
            center(window, in: defaultLaunchVisibleFrame())
            logWindowState("after ensureMainWindowIsVisible onboarding center")
            return
        }

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
