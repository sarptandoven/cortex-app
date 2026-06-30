import SwiftUI

private let connectionsSheetBackground = Color(red: 0.985, green: 0.98, blue: 0.955)
private let connectionsPanelBackground = Color.white.opacity(0.88)

struct ConnectionsPrivacySheet: View {
    @ObservedObject var state: AppState
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()

            ConnectionsPrivacyOverview(state: state)
        }
        .frame(minWidth: 760, minHeight: 680)
        .background(connectionsSheetBackground)
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.accentColor.opacity(0.12))
                Image(systemName: "lock.shield")
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundColor(.accentColor)
            }
            .frame(width: 48, height: 48)

            VStack(alignment: .leading, spacing: 5) {
                Text("Connections & Privacy")
                    .font(.title2)
                    .fontWeight(.semibold)
                Text("Connect notes once, control what AI tools can read, and keep advanced controls out of the main flow.")
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 0)

            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 16, weight: .semibold))
                    .frame(width: 40, height: 40)
            }
            .buttonStyle(.borderless)
            .help("Close")
        }
        .padding(20)
        .background(connectionsSheetBackground)
    }
}

private struct ConnectionsPrivacyOverview: View {
    @ObservedObject var state: AppState
    @State private var privacySettingsExpanded = false
    @State private var connectedExpanded = false
    @State private var advancedExpanded = false
    @State private var sourceAuditExpanded = false
    @State private var tokenDetailsExpanded = false
    @State private var aiToolSetupExpanded = false
    @State private var developerDetailsExpanded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                ConnectionsOverviewHero(state: state)

                VStack(alignment: .leading, spacing: 18) {
                    SectionHeader(
                        title: "Connect",
                        detail: "Cortex works best when local tools and notes sync in the background."
                    )
                    ConnectionsObsidianSection(state: state)
                    ConnectionsAIToolsSection(state: state)
                }

                if let summary = state.trustSummary {
                    ConnectionsPrivacyDefaultsSection(state: state, summary: summary)
                    privacySettings(summary: summary)
                    connectedNow
                    advancedControls(summary: summary)
                } else {
                    QuietState(
                        title: "Preparing privacy controls",
                        detail: CortexRecoveryText.needsAttention(state.displayStatus) ? state.displayStatus : "Cortex is reading local trust settings and connection history."
                    )
                }
            }
            .padding(24)
        }
        .background(connectionsSheetBackground)
        .task {
            await state.loadTrust()
            if state.sourceConnectorCatalog.isEmpty {
                await state.loadSourceConnectivity()
            }
        }
    }

    private func privacySettings(summary: TrustSummaryResponse) -> some View {
        DisclosureGroup(isExpanded: $privacySettingsExpanded) {
            VStack(alignment: .leading, spacing: 14) {
                TrustPolicySection(state: state)
            }
            .padding(.top, 10)
        } label: {
            ConnectionsDisclosureLabel(
                systemImage: "shield.lefthalf.filled",
                title: "Privacy settings",
                detail: "\(summary.mode.capitalized) mode · trust \(summary.trust_score)/100"
            )
        }
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.22)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var connectedNow: some View {
        DisclosureGroup(isExpanded: $connectedExpanded) {
            ConnectionsActiveSourcesSection(state: state)
                .padding(.top, 10)
        } label: {
            ConnectionsDisclosureLabel(
                systemImage: "checkmark.seal",
                title: "Connected now",
                detail: "\(state.activeSourceAccounts.count) source\(state.activeSourceAccounts.count == 1 ? "" : "s") · \(state.connectedAIIntegrationCount) AI tool\(state.connectedAIIntegrationCount == 1 ? "" : "s")"
            )
        }
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.22)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func advancedControls(summary: TrustSummaryResponse) -> some View {
        DisclosureGroup(isExpanded: $advancedExpanded) {
            VStack(alignment: .leading, spacing: 16) {
                DisclosureGroup("Source privacy and audit trail", isExpanded: $sourceAuditExpanded) {
                    VStack(alignment: .leading, spacing: 14) {
                        TrustSourceSection(state: state, summary: summary)
                        TrustAuditSection(events: state.auditEvents, refresh: {
                            Task { await state.loadTrust() }
                        })
                    }
                    .padding(.top, 8)
                }

                DisclosureGroup("MCP tokens", isExpanded: $tokenDetailsExpanded) {
                    IntegrationTokensSection(state: state)
                        .padding(.top, 8)
                }

                DisclosureGroup("AI tool setup", isExpanded: $aiToolSetupExpanded) {
                    IntegrationCenterView(state: state, compact: false)
                        .padding(.top, 8)
                }

                Group {
                    SettingsPrivacySection(state: state)
                    Divider()
                    SettingsDataRecoverySection(state: state)
                    Divider()
                    SettingsReliabilitySection(state: state)
                    Divider()
                    SettingsHealthSection(state: state)
                }

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
                        Task {
                            await state.loadStats()
                            await state.loadGraph()
                        }
                    }
                }
            }
            .padding(.top, 10)
        } label: {
            ConnectionsDisclosureLabel(
                systemImage: "slider.horizontal.3",
                title: "Advanced diagnostics",
                detail: "Only needed for troubleshooting, recovery, token history, and developer details"
            )
        }
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.22)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .onChange(of: advancedExpanded) { expanded in
            if expanded {
                Task {
                    await state.loadDiagnostics()
                    await state.loadReliability()
                }
            }
        }
    }
}

private struct ConnectionsOverviewHero: View {
    @ObservedObject var state: AppState

    private var activeConnections: Int {
        state.activeSourceAccounts.count
    }

    private var obsidianConnector: SourceConnectorCatalogItem? {
        state.sourceConnectorCatalog.first { $0.id == "obsidian" }
    }

    var body: some View {
        HStack(alignment: .center, spacing: 18) {
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.accentColor.opacity(0.14))
                Image(systemName: activeConnections > 0 ? "checkmark.seal.fill" : "link.circle.fill")
                    .font(.system(size: 34, weight: .semibold))
                    .foregroundColor(activeConnections > 0 ? .green : .accentColor)
            }
            .frame(width: 72, height: 72)

            VStack(alignment: .leading, spacing: 7) {
                Text(activeConnections > 0 ? "Cortex is connected" : "Connect notes once")
                    .font(.system(size: 28, weight: .semibold))
                Text("Memory syncs from connected notes. New signals go to Review first, then Ask and connected AI tools use approved memory with citations.")
                    .font(.title3)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 8)

            primaryButton
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.22)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    @ViewBuilder
    private var primaryButton: some View {
        if activeConnections > 0 {
            Button {
                runPrimaryAction()
            } label: {
                Label(primaryActionTitle, systemImage: primaryActionIcon)
                    .frame(minWidth: 158, minHeight: 50)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .disabled(primaryActionDisabled)
        } else {
            Button {
                runPrimaryAction()
            } label: {
                Label(primaryActionTitle, systemImage: primaryActionIcon)
                    .frame(minWidth: 158, minHeight: 50)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(primaryActionDisabled)
        }
    }

    private var primaryActionDisabled: Bool {
        state.isBusy || (!state.hasConnectedObsidianVault && obsidianConnector == nil && state.sourceConnectorCatalog.isEmpty)
    }

    private var primaryActionTitle: String {
        if activeConnections == 0, let _ = obsidianConnector {
            return "Connect Obsidian"
        }
        if activeConnections == 0 {
            return "Check tools"
        }
        return "Refresh status"
    }

    private var primaryActionIcon: String {
        if activeConnections == 0, obsidianConnector != nil {
            return "folder.badge.plus"
        }
        return "arrow.clockwise"
    }

    private func runPrimaryAction() {
        if activeConnections == 0, let connector = obsidianConnector {
            state.connectLocalNotesFolder(connector)
        } else {
            Task {
                await state.loadTrust()
                state.refreshIntegrationStates()
            }
        }
    }
}

private struct ConnectionsObsidianSection: View {
    @ObservedObject var state: AppState

    private var obsidianConnector: SourceConnectorCatalogItem? {
        state.sourceConnectorCatalog.first { $0.id == "obsidian" }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionHeader(
                title: "Obsidian",
                detail: "Choose a notes folder once. Cortex scans Markdown locally, keeps citations stable, and resyncs changed notes."
            )
            if state.sourceConnectorCatalog.isEmpty {
                QuietState(title: "Checking connectors", detail: "Cortex is reading the local source registry.")
            } else if let connector = obsidianConnector {
                SourceConnectorStatusCard(
                    state: state,
                    connector: connector,
                    connected: isConnected(connector)
                )
            } else {
                QuietState(title: "Obsidian unavailable", detail: "Restart Cortex after the local backend is healthy.")
            }
        }
    }

    private func isConnected(_ connector: SourceConnectorCatalogItem) -> Bool {
        if connector.id == "obsidian", state.hasConnectedObsidianVault {
            return true
        }
        return state.activeSourceAccounts.contains { account in
            account.source == connector.id || (connector.source_ids ?? []).contains(account.source)
        }
    }
}

private struct ConnectionsAIToolsSection: View {
    @ObservedObject var state: AppState

    private var connectedCount: Int {
        state.integrations.filter { state.integrationState(for: $0).configured }.count
    }

    private var detectedConnectable: [AIIntegration] {
        state.integrations.filter { integration in
            guard integration.supportsInstall else { return false }
            let integrationState = state.integrationState(for: integration)
            return integrationState.appInstalled && !integrationState.configured
        }
    }

    private var connectedIntegrations: [AIIntegration] {
        state.integrations.filter { state.integrationState(for: $0).configured }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionHeader(
                title: "AI tools",
                detail: "Detected local tools can use approved memory automatically."
            )

            HStack(alignment: .center, spacing: 14) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(statusColor.opacity(0.13))
                    Image(systemName: statusIcon)
                        .font(.system(size: 28, weight: .semibold))
                        .foregroundColor(statusColor)
                }
                .frame(width: 56, height: 56)

                VStack(alignment: .leading, spacing: 4) {
                    Text(statusTitle)
                        .font(.title3)
                        .fontWeight(.semibold)
                    Text(statusDetail)
                        .font(.callout)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Spacer(minLength: 8)

                if !detectedConnectable.isEmpty {
                    Button {
                        state.installDetectedIntegrations()
                    } label: {
                        Label("Connect", systemImage: "link.circle")
                            .frame(minWidth: 118, minHeight: 46)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                } else {
                    Button {
                        state.refreshIntegrationStates()
                    } label: {
                        Label("Check", systemImage: "arrow.clockwise")
                            .frame(minWidth: 108, minHeight: 46)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)
                }
            }
            .padding(14)
            .background(Color(nsColor: .controlBackgroundColor).opacity(0.70))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            if !connectedIntegrations.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(connectedIntegrations.prefix(3)) { integration in
                        HStack(spacing: 10) {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundColor(.green)
                                .frame(width: 24)
                            Text(integration.name)
                                .font(.callout)
                                .fontWeight(.medium)
                            Spacer(minLength: 0)
                            Text("Connected")
                                .font(.caption)
                                .fontWeight(.semibold)
                                .foregroundColor(.green)
                        }
                        .padding(10)
                        .background(Color(nsColor: .controlBackgroundColor).opacity(0.55))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                }
            }
        }
    }

    private var statusColor: Color {
        if connectedCount > 0 { return .green }
        if !detectedConnectable.isEmpty { return .accentColor }
        return .secondary
    }

    private var statusIcon: String {
        if connectedCount > 0 { return "checkmark.seal.fill" }
        if !detectedConnectable.isEmpty { return "app.badge.checkmark" }
        return "app.badge"
    }

    private var statusTitle: String {
        if connectedCount > 0 {
            return "\(connectedCount) tool\(connectedCount == 1 ? "" : "s") connected"
        }
        if !detectedConnectable.isEmpty {
            return "\(detectedConnectable.count) tool\(detectedConnectable.count == 1 ? "" : "s") ready"
        }
        return "No local AI tool detected"
    }

    private var statusDetail: String {
        if connectedCount > 0 {
            return "Approved memory is available to connected tools."
        }
        if !detectedConnectable.isEmpty {
            return "Connect detected tools once. Cortex handles the local setup."
        }
        return "Open Claude, ChatGPT, or another supported local tool, then check again."
    }
}

private struct ConnectionsPrivacyDefaultsSection: View {
    @ObservedObject var state: AppState
    let summary: TrustSummaryResponse

    private var settings: AppSettingsResponse {
        summary.settings
    }

    private var backupCount: Int {
        state.dataLifecycleReport?.backups.count ?? 0
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .center) {
                SectionHeader(
                    title: "Privacy defaults",
                    detail: "Cortex stays local-first and review-first unless you explicitly widen access."
                )
                Spacer(minLength: 12)
                Button {
                    state.createBackup()
                } label: {
                    Label(backupCount > 0 ? "Back Up Again" : "Back Up Now", systemImage: "archivebox")
                        .frame(minHeight: 44)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
            }

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 190), spacing: 10)], spacing: 10) {
                ConnectionsTrustTile(
                    title: settings.review_new_captures ? "Review first" : "Auto-approve",
                    detail: settings.review_new_captures ? "new memory waits" : "new memory can activate",
                    systemImage: settings.review_new_captures ? "checklist" : "bolt.fill",
                    color: settings.review_new_captures ? .green : .orange
                )
                ConnectionsTrustTile(
                    title: settings.allow_agent_reads ? "AI can read approved memory" : "AI reads are off",
                    detail: settings.allow_agent_reads ? (settings.allow_pending_in_context ? "pending can be shared" : "approved only") : "tools cannot search memory",
                    systemImage: settings.allow_agent_reads ? "eye.fill" : "eye.slash.fill",
                    color: settings.allow_agent_reads ? .accentColor : .secondary
                )
                ConnectionsTrustTile(
                    title: settings.redact_sensitive_context ? "Redaction on" : "Redaction off",
                    detail: settings.redact_sensitive_context ? "sensitive text is masked" : "full text can be shared",
                    systemImage: settings.redact_sensitive_context ? "text.badge.xmark" : "text.viewfinder",
                    color: settings.redact_sensitive_context ? .green : .orange
                )
                ConnectionsTrustTile(
                    title: backupCount > 0 ? "Backup ready" : "No backup yet",
                    detail: backupCount > 0 ? "\(backupCount) local archive\(backupCount == 1 ? "" : "s")" : "create one before adding more sources",
                    systemImage: backupCount > 0 ? "externaldrive.fill" : "externaldrive.badge.exclamationmark",
                    color: backupCount > 0 ? .green : .orange
                )
            }
        }
        .padding(16)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.22)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

private struct ConnectionsTrustTile: View {
    let title: String
    let detail: String
    let systemImage: String
    let color: Color

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: systemImage)
                .font(.title3)
                .foregroundColor(color)
                .frame(width: 28, height: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.callout)
                    .fontWeight(.semibold)
                    .lineLimit(2)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
        }
        .padding(12)
        .frame(minHeight: 72, alignment: .leading)
        .background(Color(nsColor: .windowBackgroundColor).opacity(0.72))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

private struct ConnectionsActiveSourcesSection: View {
    @ObservedObject var state: AppState

    private var activeAccounts: [SourceAccountItem] {
        state.sourceAccounts.filter { $0.disconnected_at == nil }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if activeAccounts.isEmpty {
                QuietState(title: "No source accounts connected", detail: "Connect Obsidian or a local AI tool once. Cortex syncs after that.")
            } else {
                ForEach(activeAccounts.prefix(8)) { account in
                    SourceAccountHealthRow(
                        account: account,
                        cursor: state.syncCursors.first(where: { $0.source_account_id == account.id })
                    )
                }
            }
        }
    }
}

private struct ConnectionsDisclosureLabel: View {
    let systemImage: String
    let title: String
    let detail: String

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: systemImage)
                .font(.title3)
                .foregroundColor(.accentColor)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.headline)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
    }
}
