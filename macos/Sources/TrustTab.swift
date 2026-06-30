import SwiftUI

struct TrustTab: View {
    @ObservedObject var state: AppState
    @State private var integrationsExpanded = false
    @State private var sourcesExpanded = false
    @State private var advancedExpanded = false
    @State private var developerDetailsExpanded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let summary = state.trustSummary {
                    TrustChecklistSection(state: state, summary: summary)
                    TrustPolicySection(state: state)
                    SettingsPrivacySection(state: state)
                    DisclosureGroup("Sources and audit trail", isExpanded: $sourcesExpanded) {
                        VStack(alignment: .leading, spacing: 14) {
                            TrustSourceSection(state: state, summary: summary)
                            TrustAuditSection(events: state.auditEvents, refresh: {
                                Task { await state.loadTrust() }
                            })
                        }
                        .padding(.top, 8)
                    }
                    DisclosureGroup("Connected AI tools", isExpanded: $integrationsExpanded) {
                        VStack(alignment: .leading, spacing: 14) {
                            IntegrationCenterView(state: state, compact: true)
                            IntegrationTokensSection(state: state)
                        }
                        .padding(.top, 8)
                    }
                    DisclosureGroup("Advanced settings and diagnostics", isExpanded: $advancedExpanded) {
                        VStack(alignment: .leading, spacing: 14) {
                            SettingsDataRecoverySection(state: state)
                            Divider()
                            SettingsReliabilitySection(state: state)
                            Divider()
                            SettingsHealthSection(state: state)
                            DisclosureGroup("Developer details", isExpanded: $developerDetailsExpanded) {
                                VStack(alignment: .leading, spacing: 14) {
                                    if let lifecycle = state.dataLifecycleReport {
                                        TrustLifecycleSection(report: lifecycle)
                                        Divider()
                                    }
                                    Group {
                                        SettingsOnboardingSection(state: state)
                                        Divider()
                                        AdvancedGraphSection(state: state)
                                        SettingsStatsSection(state: state)
                                        Divider()
                                    }
                                    Group {
                                        TrustSyncManifestSection(state: state)
                                        Divider()
                                        SettingsUpdatesSection(state: state)
                                        Divider()
                                        SettingsBackendSection(state: state)
                                    }
                                }
                                .padding(.top, 8)
                            }
                            .onChange(of: developerDetailsExpanded) { expanded in
                                if expanded {
                                    Task { await loadDeveloperDiagnostics() }
                                }
                            }
                        }
                        .padding(.top, 8)
                    }
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
        .onChange(of: advancedExpanded) { expanded in
            if expanded {
                Task { await loadAdvancedDiagnostics() }
            }
        }
    }

    private func loadAdvancedDiagnostics() async {
        await state.loadDiagnostics()
        await state.loadReliability()
    }

    private func loadDeveloperDiagnostics() async {
        await state.loadStats()
        await state.loadGraph()
    }
}

struct TrustChecklistSection: View {
    @ObservedObject var state: AppState
    let summary: TrustSummaryResponse

    private var settings: AppSettingsResponse {
        summary.settings
    }

    private var pendingCaptures: Int {
        summary.counts["pending_captures"] ?? 0
    }

    private var activeMemories: Int {
        summary.counts["active_memories"] ?? 0
    }

    private var agentEventsThisWeek: Int {
        summary.counts["agent_events_7d"] ?? 0
    }

    private var privateSourceCount: Int {
        (settings.source_policies ?? [:]).values.filter { $0.mode == SourcePolicyMode.excluded.rawValue }.count
    }

    private var reviewFirstSourceCount: Int {
        (settings.source_policies ?? [:]).values.filter { $0.mode == SourcePolicyMode.review.rawValue }.count
    }

    private var backupStatus: TrustChecklistItem {
        guard let backups = state.dataLifecycleReport?.backups else {
            return TrustChecklistItem(
                title: "Checking backups",
                detail: "Reading the local lifecycle report for backup history.",
                systemImage: "hourglass",
                color: .secondary
            )
        }

        if let latest = backups.latest_backup {
            return TrustChecklistItem(
                title: "Backup recorded",
                detail: "Latest local backup \(shortDateTime(latest.created_at)) · \(latest.age_days) day\(latest.age_days == 1 ? "" : "s") old · \(formatBytes(latest.size_bytes)) · \(backups.count) total\n\(latest.backup_path)",
                systemImage: "checkmark.seal.fill",
                color: .green
            )
        }
        if backups.count > 0 {
            return TrustChecklistItem(
                title: "Backups recorded",
                detail: "\(backups.count) local backup archive\(backups.count == 1 ? "" : "s") tracked by the lifecycle report.",
                systemImage: "externaldrive.fill",
                color: .green
            )
        }

        return TrustChecklistItem(
            title: "No backup recorded",
            detail: "Create a local backup before connecting more tools or importing large source exports.",
            systemImage: "externaldrive.badge.exclamationmark",
            color: .orange
        )
    }

    private var warningStatus: TrustChecklistItem {
        var warnings = summary.risk_flags
        if pendingCaptures > 0 {
            warnings.append("\(pendingCaptures) pending capture\(pendingCaptures == 1 ? "" : "s") need review")
        }

        guard !warnings.isEmpty else {
            return TrustChecklistItem(
                title: "No active warnings",
                detail: "Trust summary reports no risk flags and no pending captures.",
                systemImage: "checkmark.shield.fill",
                color: .green
            )
        }

        let shown = warnings.prefix(2).joined(separator: " · ")
        let remainder = warnings.count > 2 ? " · +\(warnings.count - 2) more" : ""
        return TrustChecklistItem(
            title: "\(warnings.count) warning\(warnings.count == 1 ? "" : "s")",
            detail: shown + remainder,
            systemImage: "exclamationmark.triangle.fill",
            color: .orange
        )
    }

    private var readStatus: TrustChecklistItem {
        guard settings.allow_agent_reads else {
            return TrustChecklistItem(
                title: "AI read access is off",
                detail: "Connected AI tools cannot read Cortex memory.",
                systemImage: "eye.slash.fill",
                color: .green
            )
        }

        var parts = [
            "Connected AI tools can search memory, read review queues, and inspect stats",
            settings.allow_pending_in_context ? "pending saves can appear in AI context" : "pending saves stay out of AI context",
            "shared context limit: \(settings.context_pack_limit)"
        ]
        parts.append(settings.redact_sensitive_context ? "redaction is on" : "redaction is off")
        if privateSourceCount > 0 {
            parts.append("\(privateSourceCount) source\(privateSourceCount == 1 ? "" : "s") kept private")
        }
        if reviewFirstSourceCount > 0 {
            parts.append("\(reviewFirstSourceCount) source\(reviewFirstSourceCount == 1 ? "" : "s") review first")
        }

        return TrustChecklistItem(
            title: "AI read access is on",
            detail: parts.joined(separator: " · "),
            systemImage: "eye.fill",
            color: settings.redact_sensitive_context ? .accentColor : .orange
        )
    }

    private var changeStatus: TrustChecklistItem {
        var enabled: [String] = []
        if settings.allow_agent_writes {
            enabled.append("save, approve, or archive memory")
        }
        if settings.allow_agent_exports {
            enabled.append("prepare profile artifacts, adaptation instructions, or exports")
        }
        if settings.allow_agent_maintenance {
            enabled.append("create backups or repair indexes")
        }
        if settings.allow_agent_destructive_actions {
            enabled.append("delete memories, captures, backups, or all local data")
        }

        guard !enabled.isEmpty else {
            return TrustChecklistItem(
                title: "AI changes are off",
                detail: "Connected AI tools cannot save, export, run maintenance, or delete local data.",
                systemImage: "lock.shield.fill",
                color: .green
            )
        }

        let color: Color = settings.allow_agent_destructive_actions ? .red : (settings.allow_agent_maintenance ? .orange : .accentColor)
        return TrustChecklistItem(
            title: "AI actions enabled",
            detail: enabled.joined(separator: " · "),
            systemImage: settings.allow_agent_destructive_actions ? "trash.fill" : "square.and.pencil",
            color: color
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Safety checklist")
                        .font(.title2)
                        .fontWeight(.semibold)
                    Text("\(summary.mode.capitalized) mode · trust \(summary.trust_score)/100 · \(activeMemories) active memories · \(agentEventsThisWeek) agent events this week")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                Button {
                    Task { await state.loadTrust() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }

            VStack(spacing: 0) {
                TrustChecklistRow(item: readStatus)
                Divider()
                TrustChecklistRow(item: changeStatus)
                Divider()
                TrustChecklistRow(
                    item: backupStatus,
                    actionTitle: "Back Up Now",
                    actionSystemImage: "archivebox",
                    action: {
                        state.createBackup()
                    }
                )
                Divider()
                TrustChecklistRow(item: warningStatus)
            }
        }
        .padding(12)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func shortDateTime(_ value: String) -> String {
        String(value.prefix(19)).replacingOccurrences(of: "T", with: " ")
    }

    private func formatBytes(_ bytes: Int) -> String {
        let formatter = ByteCountFormatter()
        formatter.allowedUnits = [.useKB, .useMB, .useGB]
        formatter.countStyle = .file
        return formatter.string(fromByteCount: Int64(bytes))
    }
}

struct TrustChecklistItem {
    let title: String
    let detail: String
    let systemImage: String
    let color: Color
}

struct TrustChecklistRow: View {
    let item: TrustChecklistItem
    let actionTitle: String?
    let actionSystemImage: String?
    let action: (() -> Void)?

    init(
        item: TrustChecklistItem,
        actionTitle: String? = nil,
        actionSystemImage: String? = nil,
        action: (() -> Void)? = nil
    ) {
        self.item = item
        self.actionTitle = actionTitle
        self.actionSystemImage = actionSystemImage
        self.action = action
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: item.systemImage)
                .foregroundColor(item.color)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title)
                    .font(.callout)
                    .fontWeight(.semibold)
                Text(item.detail)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            Spacer(minLength: 0)
            if let actionTitle, let action {
                Button(action: action) {
                    if let actionSystemImage {
                        Label(actionTitle, systemImage: actionSystemImage)
                    } else {
                        Text(actionTitle)
                    }
                }
                .buttonStyle(.bordered)
            }
        }
        .padding(.vertical, 9)
    }
}
