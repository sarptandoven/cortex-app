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

struct SearchResponse: Codable {
    let query: String
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
    let top_topics: [TopicBucket]
    let top_entities: [EntityBucket]
}

struct StatBucket: Codable, Hashable {
    let kind: String
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

struct AppSettingsResponse: Codable {
    var review_new_captures: Bool
    var allow_pending_in_context: Bool
    var context_pack_limit: Int
    var allow_agent_reads: Bool
    var allow_agent_writes: Bool
    var allow_agent_exports: Bool
    var redact_sensitive_context: Bool

    static let defaults = AppSettingsResponse(
        review_new_captures: true,
        allow_pending_in_context: true,
        context_pack_limit: 12,
        allow_agent_reads: true,
        allow_agent_writes: true,
        allow_agent_exports: true,
        redact_sensitive_context: true
    )
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
            setupHint: "Use Claude Desktop for direct MCP access to Cortex search, daily review, context packs, and memory capture.",
            browserURL: "https://claude.ai"
        ),
        AIIntegration(
            id: "cursor",
            name: "Cursor",
            category: .oneClick,
            systemImage: "cursorarrow.rays",
            summary: "Gives Cursor agent sessions access to Cortex project memory and context packs.",
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
            summary: "Copy a paste-ready Cortex context pack and usage instruction for ChatGPT.",
            restartHint: "Paste into a new or existing ChatGPT conversation.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Paste the Cortex context pack into ChatGPT when you want the assistant to reuse your local memory.",
            browserURL: "https://chatgpt.com"
        ),
        AIIntegration(
            id: "claude-web",
            name: "Claude Web",
            category: .browser,
            systemImage: "sparkle.magnifyingglass",
            summary: "Copy Cortex memory into Claude web chats without configuring local files.",
            restartHint: "Paste the context pack into Claude.",
            bundleIdentifiers: [],
            configTargets: [],
            setupHint: "Paste the context pack when Claude needs project, person, or decision memory.",
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
            summary: "Copy a concise memory brief for Perplexity research threads.",
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
            summary: "Copy a Cortex context pack for Grok conversations.",
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
            setupHint: "Paste the context pack into Poe bots that need personal/project memory.",
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
            setupHint: "Use the Markdown export/context pack as a NotebookLM source for grounded Q&A.",
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
            setupHint: "Configure Open WebUI or its pipelines to call Cortex on localhost, or paste context packs manually.",
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
            summary: "Copy Cortex exports/context packs into AnythingLLM workspaces.",
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

    func ensureRunning(endpoint: String, apiKey: String, vaultPath: String) async -> String {
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
            try startBundledBackend(apiKey: apiKey, vaultPath: vaultPath)
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

    private func startBundledBackend(apiKey: String, vaultPath: String) throws {
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
        environment["CORTEX_VAULT_PATH"] = vaultURL.path
        environment["CORTEX_DB_PATH"] = dbURL.path
        environment["CORTEX_API_KEY"] = apiKey.isEmpty ? "dev-local-key" : apiKey
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
            return runningPath == expectedPath ? .healthy : .incompatible
        } catch {
            return .unavailable
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
    @Published var endpoint: String = UserDefaults.standard.string(forKey: "endpoint") ?? "http://127.0.0.1:8766"
    @Published var apiKey: String = KeychainStore().load(account: "apiKey").isEmpty ? "dev-local-key" : KeychainStore().load(account: "apiKey")
    @Published var quickNote: String = ""
    @Published var captureURLString: String = ""
    @Published var captureTitle: String = ""
    @Published var captureNotes: String = ""
    @Published var captureDropTargeted: Bool = false
    @Published var lastFileCaptureSummary: String = ""
    @Published var searchQuery: String = ""
    @Published var status: String = "Ready"
    @Published var inbox: [CaptureItem] = []
    @Published var recent: [MemoryItem] = []
    @Published var searchResults: [MemoryItem] = []
    @Published var graphNodes: [GraphNode] = []
    @Published var graphEdges: [GraphEdge] = []
    @Published var stats: StatsResponse?
    @Published var review: DailyReviewResponse?
    @Published var productLoop: ProductLoopResponse?
    @Published var appSettings: AppSettingsResponse = .defaults
    @Published var trustSummary: TrustSummaryResponse?
    @Published var auditEvents: [AuditEventItem] = []
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
    @Published var vaultPath: String = UserDefaults.standard.string(forKey: "vaultPath") ?? BackendSupervisor.defaultVaultURL.path
    @Published var showOnboarding: Bool = !UserDefaults.standard.bool(forKey: "onboardingComplete.v1")
    @Published var onboardingStep: Int = min(4, max(0, UserDefaults.standard.integer(forKey: "onboardingStep.v1")))
    @Published var onboardingNote: String = "A durable preference, decision, project detail, or open loop I want AI assistants to remember."
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

    var preferredUpdateArtifact: UpdateArtifact? {
        updateManifest?.artifacts.first(where: { $0.kind == "dmg" }) ?? updateManifest?.artifacts.first
    }

    func persistSettings() {
        UserDefaults.standard.set(endpoint, forKey: "endpoint")
        UserDefaults.standard.set(vaultPath, forKey: "vaultPath")
        keychain.save(apiKey, account: "apiKey")
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
        await ensureBackend()
        await loadInbox()
        await loadRecent()
        await loadGraph()
        await loadStats()
        await loadSettings()
        await loadTrust()
        await loadReview()
        await loadProductLoop()
        await loadDiagnostics()
        await loadReliability()
        refreshIntegrationStates()
    }

    func ensureBackend() async {
        backendStatus = "Checking backend"
        let message = await backend.ensureRunning(endpoint: endpoint, apiKey: apiKey, vaultPath: vaultPath)
        backendStatus = message
        status = message
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
        Task { await capture(text: text, source: "macos-clipboard", title: "Clipboard capture") }
    }

    func captureQuickNote() {
        let text = quickNote.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty {
            status = "Quick note is empty"
            return
        }
        Task {
            await capture(text: text, source: "macos-quick-note", title: "Quick note")
            quickNote = ""
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
            await capture(text: content.joined(separator: "\n"), source: "web-url", title: title, sourceURL: urlText.isEmpty ? nil : urlText)
            captureURLString = ""
            captureTitle = ""
            captureNotes = ""
        }
    }

    func chooseFilesForCapture() {
        let panel = NSOpenPanel()
        panel.title = "Choose Files to Save to Cortex"
        panel.prompt = "Save Files"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
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
        Task { await captureFilesAsync(unique, moveImportedFromInbox: false) }
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
        let urls = (try? manager.contentsOfDirectory(at: captureInboxURL, includingPropertiesForKeys: [.isRegularFileKey], options: [.skipsHiddenFiles])) ?? []
        let files = urls.filter { url in
            guard url.lastPathComponent != "Imported" else { return false }
            let values = try? url.resourceValues(forKeys: [.isRegularFileKey])
            return values?.isRegularFile == true
        }
        guard !files.isEmpty else {
            status = "Capture Inbox is empty"
            return
        }
        Task { await captureFilesAsync(files, moveImportedFromInbox: true) }
    }

    func copyBrowserBookmarklet() {
        let action = endpoint.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/capture"
        let js = """
        javascript:(()=>{const d=document;const s=String(getSelection()||'').trim();const body=(d.body&&d.body.innerText)||'';const content=(s||body).slice(0,120000);const f=d.createElement('form');f.method='POST';f.action=\(jsString(action));f.target='_blank';const data={token:\(jsString(apiKey)),source:'browser-bookmarklet',title:d.title||location.href,url:location.href,content};for(const k in data){const i=d.createElement('input');i.type='hidden';i.name=k;i.value=data[k];f.appendChild(i)}d.body.appendChild(f);f.submit();setTimeout(()=>f.remove(),1000)})()
        """
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(js.replacingOccurrences(of: "\n", with: ""), forType: .string)
        status = "Browser capture bookmarklet copied"
    }

    func openBrowserCapturePage() {
        let urlString = endpoint.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/capture?token=\(apiKey.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? apiKey)"
        if let url = URL(string: urlString) {
            NSWorkspace.shared.open(url)
        }
    }

    func captureOnboardingNote() {
        let text = onboardingNote.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty {
            status = "First memory is empty"
            return
        }
        Task {
            await capture(text: text, source: "macos-onboarding", title: "First Cortex memory")
            onboardingNote = ""
            setOnboardingStep(max(onboardingStep, 3))
        }
    }

    func capture(text: String, source: String, title: String, sourceURL: String? = nil) async {
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
        } catch {
            status = "Save failed: \(error.localizedDescription)"
            notify("Cortex", "Save failed")
        }
    }

    private func captureFilesAsync(_ urls: [URL], moveImportedFromInbox: Bool) async {
        isBusy = true
        status = "Saving \(urls.count) file\(urls.count == 1 ? "" : "s")..."
        defer { isBusy = false }
        var saved = 0
        var failed = 0
        for url in urls {
            do {
                let payload = try fileCapturePayload(for: url)
                await capture(text: payload.content, source: payload.source, title: payload.title, sourceURL: payload.sourceURL)
                saved += 1
                if moveImportedFromInbox {
                    moveToImportedFolder(url)
                }
            } catch {
                failed += 1
            }
        }
        lastFileCaptureSummary = failed == 0 ? "Saved \(saved) file\(saved == 1 ? "" : "s")" : "Saved \(saved), failed \(failed)"
        status = lastFileCaptureSummary
        await loadInbox()
        await loadRecent()
        await loadGraph()
        await loadStats()
        await loadReview()
        await loadProductLoop()
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
            content += "\n\nCortex saved this file reference. This file type is not text-readable in the local MVP yet."
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

    func runSearch() {
        Task { await search() }
    }

    func search() async {
        let q = searchQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        if q.isEmpty { return }
        isBusy = true
        status = "Searching..."
        defer { isBusy = false }
        do {
            let encoded = q.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? q
            let data = try await request(path: "/v1/search?query=\(encoded)&limit=20", method: "GET")
            searchResults = try JSONDecoder().decode(SearchResponse.self, from: data).results
            status = "Found \(searchResults.count) memories"
        } catch {
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
        } catch {
            status = "Stats failed: \(error.localizedDescription)"
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
            await loadReview()
            await loadTrust()
        } catch {
            await loadProductLoop()
        }
    }

    func performProductLoopAction(_ action: ProductLoopAction) {
        switch action.action {
        case "capture":
            captureClipboard()
        case "review":
            status = "Review the inbox below"
        case "reuse":
            copyDailyContextPack()
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
                let body: [String: Any] = [
                    "review_new_captures": appSettings.review_new_captures,
                    "allow_pending_in_context": appSettings.allow_pending_in_context,
                    "context_pack_limit": appSettings.context_pack_limit,
                    "allow_agent_reads": appSettings.allow_agent_reads,
                    "allow_agent_writes": appSettings.allow_agent_writes,
                    "allow_agent_exports": appSettings.allow_agent_exports,
                    "redact_sensitive_context": appSettings.redact_sensitive_context
                ]
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

    func loadTrust() async {
        do {
            let summaryData = try await request(path: "/v1/trust/summary", method: "GET")
            trustSummary = try JSONDecoder().decode(TrustSummaryResponse.self, from: summaryData)
            let auditData = try await request(path: "/v1/audit-log?limit=80", method: "GET")
            auditEvents = try JSONDecoder().decode(AuditLogResponse.self, from: auditData).results
        } catch {
            status = "Trust failed: \(error.localizedDescription)"
        }
    }

    func copyMCPConfig() {
        copyMCPConfig(for: nil)
    }

    func copyMCPConfig(for integration: AIIntegration?) {
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
        let pack = review?.context_pack ?? "Open Cortex Today and copy a context pack after the local backend finishes loading."
        let text = """
        Use this Cortex memory pack as the source of truth for this conversation.

        Instructions:
        - Use the memory below before asking me to repeat context.
        - If something is missing or stale, ask a focused follow-up.
        - Treat saved decisions and open loops as higher priority than generic assumptions.
        - When you suggest next steps, preserve the user's local-first/privacy constraints.

        Target assistant: \(integration.name)

        \(pack)
        """
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        status = "\(integration.name) context copied"
        Task { await recordContextReuse(surface: "integration", target: integration.name) }
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
        let scriptURL = Bundle.main.resourceURL?
            .appendingPathComponent("scripts", isDirectory: true)
            .appendingPathComponent("cortex_mcp_stdio.py")
        let scriptPath = scriptURL?.path ?? "/path/to/cortex_mcp_stdio.py"
        return [
            "command": "/usr/bin/python3",
            "args": [scriptPath],
            "env": [
                "CORTEX_BASE_URL": endpoint,
                "CORTEX_API_KEY": apiKey
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

        Local API:
        Base URL: \(endpoint)
        API token: \(apiKey)

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

    func completeOnboarding() {
        saveMemorySettings()
        UserDefaults.standard.set(true, forKey: "onboardingComplete.v1")
        showOnboarding = false
        setOnboardingStep(0)
        status = "Setup complete"
        NotificationCenter.default.post(name: .cortexOnboardingCompleted, object: nil)
    }

    func showOnboardingAgain() {
        UserDefaults.standard.set(false, forKey: "onboardingComplete.v1")
        setOnboardingStep(0)
        showOnboarding = true
    }

    func nextOnboardingStep(maxStep: Int) {
        if onboardingStep == 1 {
            saveMemorySettings()
        }
        setOnboardingStep(min(maxStep, onboardingStep + 1))
    }

    func previousOnboardingStep() {
        setOnboardingStep(max(0, onboardingStep - 1))
    }

    private func setOnboardingStep(_ step: Int) {
        onboardingStep = min(4, max(0, step))
        UserDefaults.standard.set(onboardingStep, forKey: "onboardingStep.v1")
    }

    func copyDailyContextPack() {
        guard let review else {
            status = "Review not loaded"
            return
        }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(review.context_pack, forType: .string)
        status = "Context pack copied"
        Task { await recordContextReuse(surface: "today", target: "clipboard") }
    }

    func copyContextPack() {
        Task {
            do {
                let query = contextQuery.trimmingCharacters(in: .whitespacesAndNewlines)
                let encoded = query.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? query
                let limit = max(4, min(50, appSettings.context_pack_limit))
                let data = try await request(path: "/v1/context-pack?query=\(encoded)&limit=\(limit)", method: "GET")
                let pack = String(data: data, encoding: .utf8) ?? ""
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(pack, forType: .string)
                status = query.isEmpty ? "Recent context copied" : "Context for \(query) copied"
                await recordContextReuse(surface: "focused-context", query: query, target: "clipboard")
            } catch {
                status = "Copy failed: \(error.localizedDescription)"
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
                status = "Archived memory"
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
                status = "Archive failed: \(error.localizedDescription)"
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
                updateManifest = manifest
                if manifest.bundle_id != "com.cortex.doppl" {
                    updateStatus = "Feed is for another app"
                    return
                }
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
        guard let artifact = preferredUpdateArtifact, let url = URL(string: artifact.url) else {
            updateStatus = "No update artifact available"
            return
        }
        NSWorkspace.shared.open(url)
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
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
    }
}

struct CortexView: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(spacing: 0) {
            header
            TabView {
                TodayTab(state: state)
                    .tabItem { Label("Today", systemImage: "sun.max") }
                CaptureTab(state: state)
                    .tabItem { Label("Save", systemImage: "square.and.pencil") }
                SearchTab(state: state)
                    .tabItem { Label("Search", systemImage: "magnifyingglass") }
                IntegrationsTab(state: state)
                    .tabItem { Label("Connect", systemImage: "link.badge.plus") }
                TrustTab(state: state)
                    .tabItem { Label("Trust", systemImage: "lock.shield") }
                AdvancedTab(state: state)
                    .tabItem { Label("More", systemImage: "ellipsis.circle") }
            }
            footer
        }
        .frame(minWidth: 500, minHeight: 620)
        .sheet(isPresented: $state.showOnboarding) {
            OnboardingView(state: state)
                .frame(width: 760, height: 660)
        }
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("Cortex")
                    .font(.title2)
                    .fontWeight(.semibold)
                Text("Save once. Reuse anywhere.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            Spacer()
            Button {
                state.captureClipboard()
            } label: {
                Label("Save Clipboard", systemImage: "doc.on.clipboard")
            }
            .keyboardShortcut("v", modifiers: [.command, .shift])
        }
        .padding(16)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var footer: some View {
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

struct IntegrationsTab: View {
    @ObservedObject var state: AppState

    var body: some View {
        ScrollView {
            IntegrationCenterView(state: state, compact: false)
                .padding(16)
        }
        .onAppear {
            state.refreshIntegrationStates()
        }
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
            Text("Connect Cortex everywhere")
                .font(compact ? .headline : .title3)
                .fontWeight(.semibold)
            Text("Install direct MCP tools where safe, and use copy-ready context packs everywhere else.")
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
                Label("Install Detected", systemImage: "wand.and.stars")
            }
            .buttonStyle(.borderedProminent)

            Button {
                state.copyMCPConfig()
            } label: {
                Label("Copy MCP", systemImage: "doc.on.doc")
            }

            Button {
                if let chatGPT = state.integrations.first(where: { $0.id == "chatgpt" }) {
                    state.copyIntegrationContext(chatGPT)
                } else {
                    state.copyDailyContextPack()
                }
            } label: {
                Label("Copy Chat Context", systemImage: "text.quote")
            }

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
                        Label("Copy", systemImage: "doc.on.doc")
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
                        Label("Copy Context", systemImage: "text.quote")
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
    private let steps = ["Vault", "Rules", "First Save", "Connect", "Ready"]

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
                Text("Local memory for AI work")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            VStack(alignment: .leading, spacing: 8) {
                ForEach(steps.indices, id: \.self) { index in
                    OnboardingStepRow(
                        label: steps[index],
                        index: index,
                        selected: state.onboardingStep == index,
                        completed: state.onboardingStep > index
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
                Text("Step \(state.onboardingStep + 1) of \(steps.count)")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Text(steps[state.onboardingStep])
                    .font(.title2)
                    .fontWeight(.semibold)
                Text(stepSubtitle)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            Button {
                state.completeOnboarding()
            } label: {
                Label("Skip", systemImage: "xmark")
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
            .disabled(state.onboardingStep == 0)

            Spacer()

            Text(state.displayStatus)
                .font(.caption)
                .foregroundColor(.secondary)
                .lineLimit(1)
                .frame(maxWidth: 260)

            Spacer()

            if state.onboardingStep == steps.count - 1 {
                Button {
                    state.completeOnboarding()
                } label: {
                    Label("Start Using Cortex", systemImage: "checkmark.circle")
                }
                .buttonStyle(.borderedProminent)
            } else {
                Button {
                    state.nextOnboardingStep(maxStep: steps.count - 1)
                } label: {
                    Label("Continue", systemImage: "chevron.right")
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(18)
    }

    private var stepSubtitle: String {
        switch state.onboardingStep {
        case 0:
            return "Choose where your local memory lives and confirm the vault is healthy."
        case 1:
            return "Decide when new saves become available to your AI tools."
        case 2:
            return "Save one useful memory so Cortex is immediately useful."
        case 3:
            return "Copy the setup you need for local AI tools or browser chats."
        default:
            return "Create a first backup, then move into your daily memory cockpit."
        }
    }

    @ViewBuilder
    private var stepContent: some View {
        switch state.onboardingStep {
        case 0:
            OnboardingVaultStep(state: state)
        case 1:
            OnboardingRulesStep(state: state)
        case 2:
            OnboardingFirstSaveStep(state: state)
        case 3:
            OnboardingConnectStep(state: state)
        default:
            OnboardingReadyStep(state: state)
        }
    }
}

struct OnboardingStepRow: View {
    let label: String
    let index: Int
    let selected: Bool
    let completed: Bool

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: completed ? "checkmark.circle.fill" : "\(index + 1).circle")
                .frame(width: 18)
            Text(label)
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

struct OnboardingRulesStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Choose how strict Cortex should be before AI tools can use newly saved context. You can change this any time from More.")
                .foregroundColor(.secondary)

            VStack(alignment: .leading, spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    Toggle("Review new saves first", isOn: $state.appSettings.review_new_captures)
                    Text("Recommended for beta: new captures wait in Today until you approve or archive them.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                VStack(alignment: .leading, spacing: 3) {
                    Toggle("Allow pending saves in AI context", isOn: $state.appSettings.allow_pending_in_context)
                    Text("Turn this off when assistants should only see approved memory.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Stepper(value: $state.appSettings.context_pack_limit, in: 4...50, step: 2) {
                    Text("Context pack size: \(state.appSettings.context_pack_limit)")
                }
                HStack {
                    Button {
                        state.saveMemorySettings()
                    } label: {
                        Label("Save Rules", systemImage: "checkmark.circle")
                    }
                    .buttonStyle(.borderedProminent)
                    Button {
                        Task { await state.loadSettings() }
                    } label: {
                        Label("Reload", systemImage: "arrow.clockwise")
                    }
                    Spacer()
                }
            }
            .padding(12)
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            OnboardingCheckRow(
                title: state.appSettings.allow_pending_in_context ? "Fast mode" : "Strict mode",
                detail: state.appSettings.allow_pending_in_context ? "New saves can appear in context immediately." : "Only approved saves appear in context.",
                systemImage: state.appSettings.allow_pending_in_context ? "hare.fill" : "lock.shield.fill",
                color: state.appSettings.allow_pending_in_context ? .accentColor : .green
            )
        }
    }
}

struct OnboardingFirstSaveStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Start with one useful preference, decision, project detail, or open loop. A good first memory makes Cortex useful immediately.")
                .foregroundColor(.secondary)

            TextEditor(text: $state.onboardingNote)
                .font(.body)
                .frame(minHeight: 150)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.22)))

            HStack {
                Button {
                    state.captureOnboardingNote()
                } label: {
                    Label("Save First Memory", systemImage: "square.and.arrow.down")
                }
                .buttonStyle(.borderedProminent)
                Button {
                    state.captureClipboard()
                } label: {
                    Label("Save Clipboard", systemImage: "doc.on.clipboard")
                }
                Spacer()
            }

            if let stats = state.stats {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 110), spacing: 8)], spacing: 8) {
                    StatBox(label: "Captures", value: stats.captures)
                    StatBox(label: "Pending", value: stats.pending_captures)
                    StatBox(label: "Memories", value: stats.memories)
                }
            }
        }
    }
}

struct OnboardingConnectStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        IntegrationCenterView(state: state, compact: true)
    }
}

struct OnboardingReadyStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Cortex is ready to save memory locally and reuse it in AI sessions.")
                .foregroundColor(.secondary)

            VStack(alignment: .leading, spacing: 10) {
                OnboardingCheckRow(title: "Vault", detail: state.vaultPath, systemImage: "externaldrive.fill", color: .green)
                OnboardingCheckRow(title: "Backend", detail: state.backendStatus, systemImage: "checkmark.circle.fill", color: .green)
                OnboardingCheckRow(title: "Review", detail: state.appSettings.review_new_captures ? "New saves enter the inbox." : "New saves are approved automatically.", systemImage: "tray.full.fill", color: .accentColor)
            }
            .padding(12)
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            HStack {
                Button {
                    state.createBackup()
                } label: {
                    Label("Backup Now", systemImage: "archivebox")
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

struct TodayTab: View {
    @ObservedObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if let review = state.review {
                    TodayProductLoopSection(state: state)
                    TodayScoreHeader(review: review)
                    TodayContextSection(state: state, review: review)
                    TodayPendingSection(state: state, captures: review.pending)
                    if !review.recommended_actions.isEmpty {
                        TodayActionSection(actions: review.recommended_actions)
                    }
                    if !review.open_tasks.isEmpty {
                        TodayOpenLoopsSection(tasks: review.open_tasks)
                    }
                    if !review.recent_decisions.isEmpty {
                        TodayDecisionSection(decisions: review.recent_decisions)
                    }
                } else {
                    VStack(alignment: .center, spacing: 12) {
                        ProgressView()
                        Text("Preparing today")
                            .font(.headline)
                        Text("Cortex is starting the local memory engine.")
                            .foregroundColor(.secondary)
                    }
                    .frame(maxWidth: .infinity, minHeight: 420)
                }
            }
            .padding(16)
        }
    }
}

struct TodayScoreHeader: View {
    let review: DailyReviewResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(review.stats.pending_captures == 0 ? "All clear" : "Review \(review.stats.pending_captures)")
                        .font(.title2)
                        .fontWeight(.semibold)
                    Text("\(review.stats.memories) memories · \(review.stats.tasks) open loops")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
                Label("\(review.captured_today)", systemImage: "tray.and.arrow.down")
                    .font(.headline)
                    .foregroundColor(.accentColor)
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 92), spacing: 8)], spacing: 8) {
                StatBox(label: "Captured", value: review.captured_today)
                StatBox(label: "Pending", value: review.stats.pending_captures)
                StatBox(label: "Open", value: review.stats.tasks)
                StatBox(label: "Memories", value: review.stats.memories)
            }
        }
    }
}

struct TodayProductLoopSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let loop = state.productLoop {
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(loop.primary_action.title)
                            .font(.title3)
                            .fontWeight(.semibold)
                        Text(loop.primary_action.detail)
                            .foregroundColor(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer()
                    if loop.primary_action.action == "done" {
                        Button {
                            state.performProductLoopAction(loop.primary_action)
                        } label: {
                            Label(loop.primary_action.label, systemImage: icon(for: loop.primary_action.action))
                        }
                        .buttonStyle(.bordered)
                        .disabled(true)
                    } else {
                        Button {
                            state.performProductLoopAction(loop.primary_action)
                        } label: {
                            Label(loop.primary_action.label, systemImage: icon(for: loop.primary_action.action))
                        }
                        .buttonStyle(.borderedProminent)
                    }
                }

                LazyVGrid(columns: [GridItem(.adaptive(minimum: 118), spacing: 8)], spacing: 8) {
                    ForEach(loop.steps) { step in
                        LoopStepPill(step: step)
                    }
                }

                HStack(spacing: 10) {
                    HealthPill(label: "Loop", value: "\(loop.completion)%")
                    HealthPill(label: "Reused", value: String(loop.counts["reused_today"] ?? 0))
                    HealthPill(label: "Streak", value: "\(loop.counts["streak_days"] ?? 0)d")
                    Spacer()
                }
            } else {
                HStack {
                    ProgressView()
                    Text("Loading loop")
                        .foregroundColor(.secondary)
                    Spacer()
                }
            }
        }
        .padding(12)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func icon(for action: String) -> String {
        switch action {
        case "capture": return "square.and.arrow.down"
        case "review": return "checklist"
        case "reuse": return "doc.on.doc"
        default: return "checkmark.circle"
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

struct TodayContextSection: View {
    @ObservedObject var state: AppState
    let review: DailyReviewResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Use in an AI chat")
                .font(.headline)
            TextField("Focus, person, project, or topic", text: $state.contextQuery)
                .textFieldStyle(.roundedBorder)
                .onSubmit { state.copyContextPack() }
            HStack {
                Button {
                    state.copyDailyContextPack()
                } label: {
                    Label("Copy Context", systemImage: "doc.on.doc")
                }
                .buttonStyle(.borderedProminent)
                Button {
                    state.copyContextPack()
                } label: {
                    Label("Copy Focus", systemImage: "scope")
                }
                Button {
                    Task { await state.loadReview() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                Spacer()
            }
            Text("\(review.recent_memories.count) recent · \(review.recent_decisions.count) decisions · \(review.open_tasks.count) open")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding(10)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct TodayPendingSection: View {
    @ObservedObject var state: AppState
    let captures: [CaptureItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Inbox")
                    .font(.headline)
                Spacer()
                if !captures.isEmpty {
                    Button {
                        Task { await state.loadInbox() }
                    } label: {
                        Label("Refresh", systemImage: "arrow.clockwise")
                    }
                    .font(.caption)
                }
            }
            if captures.isEmpty {
                QuietState(title: "Inbox clear", detail: "Saved context is ready when you need it.")
            } else {
                ForEach(captures.prefix(3)) { capture in
                    CaptureCard(
                        capture: capture,
                        approve: { state.approveCapture(capture) },
                        archive: { state.archiveCapture(capture) }
                    )
                }
            }
        }
    }
}

struct TodayActionSection: View {
    let actions: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Recommended")
                .font(.headline)
            ForEach(actions.prefix(3), id: \.self) { action in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: "checkmark.circle")
                        .foregroundColor(.accentColor)
                    Text(action)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .font(.body)
            }
        }
    }
}

struct TodayOpenLoopsSection: View {
    let tasks: [TaskItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Open loops")
                .font(.headline)
            ForEach(tasks.prefix(4)) { task in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(task.kind.uppercased())
                        .font(.caption2)
                        .fontWeight(.semibold)
                        .foregroundColor(.orange)
                        .frame(width: 62, alignment: .leading)
                    Text(task.content)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}

struct TodayDecisionSection: View {
    let decisions: [MemoryItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Recent decisions")
                .font(.headline)
            ForEach(decisions.prefix(3)) { decision in
                Text(decision.content)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(nsColor: .controlBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
    }
}

struct TodayTopicSection: View {
    let topics: [TopicSummary]
    let entities: [EntitySummary]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Active context")
                .font(.headline)
            if topics.isEmpty && entities.isEmpty {
                Text("Topics and entities appear after you save more context.")
                    .foregroundColor(.secondary)
            } else {
                if !topics.isEmpty {
                    Text(topics.prefix(8).map { "#\($0.topic)" }.joined(separator: " "))
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !entities.isEmpty {
                    Text(entities.prefix(6).map { "\($0.name) (\($0.kind))" }.joined(separator: " · "))
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}

struct CaptureTab: View {
    @ObservedObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                CaptureHeroSection(state: state)
                CaptureQuickNoteSection(state: state)
                CaptureWebSection(state: state)
                CaptureFileSection(state: state, handleDrop: handleDrop)
                CaptureRecentSection(state: state)
            }
            .padding(16)
        }
    }

    private func handleDrop(_ providers: [NSItemProvider]) -> Bool {
        let group = DispatchGroup()
        let lock = NSLock()
        var urls: [URL] = []
        for provider in providers {
            if provider.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) {
                group.enter()
                provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                    defer { group.leave() }
                    var url: URL?
                    if let itemURL = item as? URL {
                        url = itemURL
                    } else if let data = item as? Data,
                              let string = String(data: data, encoding: .utf8) {
                        url = URL(string: string)
                    } else if let string = item as? String {
                        url = URL(string: string)
                    }
                    if let url {
                        lock.lock()
                        urls.append(url)
                        lock.unlock()
                    }
                }
            }
        }
        group.notify(queue: .main) {
            state.captureDropTargeted = false
            state.captureFiles(urls)
        }
        return !providers.isEmpty
    }
}

struct CaptureHeroSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Capture")
                        .font(.title3)
                        .fontWeight(.semibold)
                    Text("Save once from any surface. Cortex structures it, reviews it, and makes it reusable by your AI agents.")
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                Button {
                    Task { await state.loadRecent() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 8)], spacing: 8) {
                CaptureActionTile(title: "Clipboard", detail: "Cmd Shift V", systemImage: "doc.on.clipboard", color: .accentColor) {
                    state.captureClipboard()
                }
                CaptureActionTile(title: "Files", detail: "Picker or drop", systemImage: "doc.badge.plus", color: .green) {
                    state.chooseFilesForCapture()
                }
                CaptureActionTile(title: "Browser", detail: "Bookmarklet", systemImage: "safari", color: .purple) {
                    state.copyBrowserBookmarklet()
                }
                CaptureActionTile(title: "Inbox", detail: "Drop folder", systemImage: "tray.and.arrow.down", color: .orange) {
                    state.openCaptureInbox()
                }
            }
        }
    }
}

struct CaptureActionTile: View {
    let title: String
    let detail: String
    let systemImage: String
    let color: Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 9) {
                Image(systemName: systemImage)
                    .font(.title3)
                    .foregroundColor(color)
                    .frame(width: 24)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title)
                        .font(.headline)
                    Text(detail)
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
                Spacer()
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .buttonStyle(.plain)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct CaptureQuickNoteSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Quick note")
                .font(.headline)
            TextEditor(text: $state.quickNote)
                .font(.body)
                .frame(minHeight: 110)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.2)))
            HStack {
                Button {
                    state.captureQuickNote()
                } label: {
                    Label("Save Note", systemImage: "square.and.arrow.down")
                }
                .buttonStyle(.borderedProminent)
                Button {
                    state.captureClipboard()
                } label: {
                    Label("Save Clipboard", systemImage: "doc.on.clipboard")
                }
                Spacer()
            }
        }
    }
}

struct CaptureWebSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Web and links")
                .font(.headline)
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
                        Label("Save URL", systemImage: "link.badge.plus")
                    }
                    .buttonStyle(.borderedProminent)
                    Button {
                        state.copyBrowserBookmarklet()
                    } label: {
                        Label("Copy Bookmarklet", systemImage: "bookmark")
                    }
                    Button {
                        state.openBrowserCapturePage()
                    } label: {
                        Label("Open Capture Page", systemImage: "safari")
                    }
                    Spacer()
                }
                Text("Use the bookmarklet to capture selected text or page text from ChatGPT, Claude, docs, email, and web research into local Cortex memory.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding(12)
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }
}

struct CaptureFileSection: View {
    @ObservedObject var state: AppState
    let handleDrop: ([NSItemProvider]) -> Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Files and drop folder")
                .font(.headline)
            VStack(alignment: .leading, spacing: 10) {
                VStack(spacing: 8) {
                    Image(systemName: state.captureDropTargeted ? "arrow.down.doc.fill" : "arrow.down.doc")
                        .font(.largeTitle)
                        .foregroundColor(state.captureDropTargeted ? .accentColor : .secondary)
                    Text(state.captureDropTargeted ? "Drop to save files" : "Drop files here")
                        .font(.headline)
                    Text("Text, Markdown, code, CSV, JSON, RTF, and PDFs are extracted locally. Other files are saved as source references.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity, minHeight: 130)
                .background(Color(nsColor: .textBackgroundColor))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(state.captureDropTargeted ? Color.accentColor : Color.secondary.opacity(0.22), lineWidth: state.captureDropTargeted ? 2 : 1))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .onDrop(of: [UTType.fileURL.identifier], isTargeted: $state.captureDropTargeted, perform: handleDrop)

                HStack {
                    Button {
                        state.chooseFilesForCapture()
                    } label: {
                        Label("Choose Files", systemImage: "doc.badge.plus")
                    }
                    .buttonStyle(.borderedProminent)
                    Button {
                        state.openCaptureInbox()
                    } label: {
                        Label("Open Inbox", systemImage: "tray")
                    }
                    Button {
                        state.importCaptureInbox()
                    } label: {
                        Label("Import Inbox", systemImage: "tray.and.arrow.down")
                    }
                    Button {
                        state.copyCaptureInboxPath()
                    } label: {
                        Label("Copy Path", systemImage: "doc.on.doc")
                    }
                    Spacer()
                }
                Text("Capture Inbox: \(state.captureInboxURL.path)")
                    .font(.system(.caption, design: .monospaced))
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                if !state.lastFileCaptureSummary.isEmpty {
                    Text(state.lastFileCaptureSummary)
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
            .padding(12)
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }
}

struct CaptureRecentSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Recent")
                    .font(.headline)
                Spacer()
                Text("\(state.recent.count)")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            if state.recent.isEmpty {
                QuietState(title: "Nothing saved yet", detail: "Saved notes, web snippets, files, and clipboard text appear here.")
            } else {
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(state.recent) { item in
                        MemoryCard(item: item) {
                            state.deleteMemory(item)
                        }
                    }
                }
            }
        }
    }
}

struct SearchTab: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                TextField("Search your Cortex memory", text: $state.searchQuery)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { state.runSearch() }
                Button {
                    state.runSearch()
                } label: {
                    Label("Search", systemImage: "magnifyingglass")
                }
            }
            if state.searchResults.isEmpty {
                QuietState(title: "No results", detail: "Try a person, project, decision, or topic.")
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

struct TrustTab: View {
    @ObservedObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let summary = state.trustSummary {
                    TrustScoreSection(summary: summary)
                    TrustPolicySection(state: state)
                    TrustSourceSection(summary: summary)
                    TrustAuditSection(events: state.auditEvents, refresh: {
                        Task { await state.loadTrust() }
                    })
                    TrustActionsSection(state: state)
                } else {
                    VStack(spacing: 12) {
                        ProgressView()
                        Text("Preparing trust controls")
                            .font(.headline)
                        Text("Cortex is reading local policy, source history, and recent audit events.")
                            .foregroundColor(.secondary)
                    }
                    .frame(maxWidth: .infinity, minHeight: 420)
                }
            }
            .padding(16)
        }
        .task {
            await state.loadTrust()
        }
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

struct TrustPolicySection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Controls")
                    .font(.headline)
                Spacer()
                Button {
                    Task { await state.loadTrust() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }

            TrustToggleRow(
                title: "Review new saves",
                detail: "New captures enter the inbox before you treat them as trusted.",
                systemImage: "tray.full",
                isOn: $state.appSettings.review_new_captures
            )
            TrustToggleRow(
                title: "Let AI use pending saves",
                detail: "Turn this off when only approved captures should appear in search and context packs.",
                systemImage: "lock.open",
                isOn: $state.appSettings.allow_pending_in_context
            )
            TrustToggleRow(
                title: "Agent read access",
                detail: "Connected MCP agents can search memory, read review context, and inspect stats.",
                systemImage: "eye",
                isOn: $state.appSettings.allow_agent_reads
            )
            TrustToggleRow(
                title: "Agent write access",
                detail: "Connected MCP agents can save, approve, archive, or forget memory.",
                systemImage: "square.and.pencil",
                isOn: $state.appSettings.allow_agent_writes
            )
            TrustToggleRow(
                title: "Agent export access",
                detail: "Connected MCP agents can build context packs or export memory.",
                systemImage: "square.and.arrow.up",
                isOn: $state.appSettings.allow_agent_exports
            )
            TrustToggleRow(
                title: "Redact shared context",
                detail: "Secrets, tokens, emails, and long account-like numbers are masked before sharing.",
                systemImage: "text.badge.xmark",
                isOn: $state.appSettings.redact_sensitive_context
            )

            Stepper(value: $state.appSettings.context_pack_limit, in: 4...50, step: 2) {
                Text("Context pack size: \(state.appSettings.context_pack_limit)")
            }

            HStack {
                Button {
                    state.saveMemorySettings()
                } label: {
                    Label("Save Controls", systemImage: "checkmark.circle")
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
            Text("Receipts and recovery")
                .font(.headline)
            HStack {
                Button {
                    state.copyDailyContextPack()
                } label: {
                    Label("Copy Redacted Context", systemImage: "doc.on.doc")
                }
                Button {
                    state.createBackup()
                } label: {
                    Label("Backup Now", systemImage: "archivebox")
                }
                Button {
                    state.openVaultFolder()
                } label: {
                    Label("Open Vault", systemImage: "folder")
                }
                Spacer()
            }
            TrustNotice(systemImage: "lock.doc", title: "Local-first", detail: "Trust controls apply to the local backend, MCP agents, context packs, and exports. The vault remains on this Mac.", color: .accentColor)
        }
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

struct AdvancedTab: View {
    @ObservedObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Group {
                    AdvancedGraphSection(state: state)
                    Divider()
                    SettingsOnboardingSection(state: state)
                    Divider()
                    SettingsUpdatesSection(state: state)
                }
                Group {
                    Divider()
                    SettingsIntegrationsSection(state: state)
                    Divider()
                    SettingsBehaviorSection(state: state)
                    Divider()
                    SettingsReliabilitySection(state: state)
                    Divider()
                    SettingsHealthSection(state: state)
                }
                Group {
                    Divider()
                    SettingsStatsSection(state: state)
                    Divider()
                    SettingsBackendSection(state: state)
                    Divider()
                    SettingsPrivacySection()
                }
            }
            .padding(16)
        }
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
                    Label("Copy MCP Config", systemImage: "doc.on.doc")
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

struct SettingsBehaviorSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Memory behavior")
                .font(.headline)
            Toggle("Review new saves before clearing them", isOn: $state.appSettings.review_new_captures)
            Toggle("Let AI use pending saves", isOn: $state.appSettings.allow_pending_in_context)
            Stepper(value: $state.appSettings.context_pack_limit, in: 4...50, step: 2) {
                Text("Context pack size: \(state.appSettings.context_pack_limit)")
            }
            HStack {
                Button {
                    state.saveMemorySettings()
                } label: {
                    Label("Save Settings", systemImage: "checkmark.circle")
                }
                .buttonStyle(.borderedProminent)
                Button {
                    Task { await state.loadSettings() }
                } label: {
                    Label("Reload", systemImage: "arrow.clockwise")
                }
                Spacer()
            }
            Text("Strict mode: turn off pending saves if assistants should only see approved memory.")
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
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Privacy")
                .font(.headline)
            Text("Cortex only reads the clipboard when you press the hotkey or click a capture button. No screen recording, no ambient capture, and no background upload in this MVP.")
                .font(.body)
                .foregroundColor(.secondary)
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
                Button("Approve") { approve() }
                Button("Archive") { archive() }
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

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(item.kind.uppercased())
                    .font(.caption2)
                    .fontWeight(.semibold)
                    .foregroundColor(color(for: item.kind))
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
                    Button("Archive") { onArchive() }
                        .font(.caption)
                }
            }
            Text(item.content)
                .font(.body)
                .fixedSize(horizontal: false, vertical: true)
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
        case "question": return .orange
        case "action": return .green
        default: return .blue
        }
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
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        setupStatusItem()
        setupMainWindow()
        NotificationCenter.default.addObserver(self, selector: #selector(onboardingCompleted), name: .cortexOnboardingCompleted, object: nil)
        hotKey = HotKeyManager { [weak self] in
            self?.state.captureClipboard()
            self?.showMainWindow()
        }
        hotKey.register()
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
