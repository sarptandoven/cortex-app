import Foundation
import SwiftUI

private let connectionsSheetBackground = CortexDesign.appBackground
private let connectionsPanelBackground = CortexDesign.panelBackground

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
                Text("Connect memory sources, control AI access, and keep local recovery tools in one place.")
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
    @State private var aiToolsExpanded = false
    @State private var sourceAuditExpanded = false
    @State private var recoveryToolsExpanded = false
    @State private var developerDetailsExpanded = false

    private var connectedNotesConnectionCount: Int {
        notesHealth == .healthy ? 1 : 0
    }

    private var connectionStatusDetail: String {
        let notes = notesHealth.isNeedsAttention ? "notes need attention" : "\(connectedNotesConnectionCount) notes sync\(connectedNotesConnectionCount == 1 ? "" : "s")"
        let tools = "\(state.connectedAIIntegrationCount) AI tool\(state.connectedAIIntegrationCount == 1 ? "" : "s")"
        return "\(notes) · \(tools)"
    }

    private var notesHealth: NotesConnectionHealth {
        NotesConnectionHealth(state: state)
    }

    private var detectedAIToolCount: Int {
        state.integrations.filter { integration in
            guard integration.supportsInstall else { return false }
            let integrationState = state.integrationState(for: integration)
            return integrationState.appInstalled && !integrationState.configured
        }.count
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                ConnectionsOverviewHero(state: state)

                ConnectionsObsidianSection(state: state)
                ConnectionsDirectSourcesSection(state: state)
                if state.connectedAIIntegrationCount > 0 {
                    ConnectionsAIToolsSection(state: state)
                } else {
                    optionalAITools
                }

                if let summary = state.trustSummary {
                    ConnectionsPrivacyDefaultsSection(state: state, summary: summary)
                    privacySettings(summary: summary)
                    connectedNow
                    advancedControls(summary: summary)
                } else {
                    QuietState(
                        title: "Preparing privacy controls",
                        detail: CortexRecoveryText.needsAttention(state.displayStatus) ? state.displayStatus : "Cortex is reading local privacy settings and connection history."
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

    private var optionalAITools: some View {
        DisclosureGroup(isExpanded: $aiToolsExpanded) {
            ConnectionsAIToolsSection(state: state)
                .padding(.top, 10)
        } label: {
            ConnectionsDisclosureLabel(
                systemImage: "wand.and.stars",
                title: "Use memory elsewhere",
                detail: aiToolsDisclosureDetail
            )
        }
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.22)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var aiToolsDisclosureDetail: String {
        if detectedAIToolCount > 0 {
            return "\(detectedAIToolCount) app\(detectedAIToolCount == 1 ? "" : "s") detected, optional after Ask works"
        }
        return "Optional after Ask is useful"
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
                title: "Memory permissions",
                detail: "Reviewed memory reads, new AI saves go to Review"
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
                title: "Connection status",
                detail: connectionStatusDetail
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
                Text("Create a local backup from the main privacy card. Recovery, repair, and support tools stay here for setup help and incident response.")
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                DisclosureGroup("Recovery and support tools", isExpanded: $recoveryToolsExpanded) {
                    VStack(alignment: .leading, spacing: 14) {
                        SettingsDataRecoverySection(state: state)
                        Divider()
                        SettingsReliabilitySection(state: state)
                    }
                    .padding(.top, 8)
                }

                DisclosureGroup("Advanced support details", isExpanded: $developerDetailsExpanded) {
                    VStack(alignment: .leading, spacing: 14) {
                        Group {
                            DisclosureGroup("Privacy history", isExpanded: $sourceAuditExpanded) {
                                VStack(alignment: .leading, spacing: 14) {
                                    TrustSourceSection(state: state, summary: summary)
                                    TrustAuditSection(events: state.auditEvents, refresh: {
                                        Task { await state.loadTrust() }
                                    })
                                }
                                .padding(.top, 8)
                            }
                            Divider()
                            IntegrationTokensSection(state: state)
                            Divider()
                            IntegrationCenterView(state: state, compact: true)
                            Divider()
                        }
                        Group {
                            SettingsPrivacySection(state: state)
                            Divider()
                            SettingsHealthSection(state: state)
                            Divider()
                        }
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
                systemImage: "wrench.and.screwdriver",
                title: "Advanced",
                detail: "Recovery, diagnostics, support details"
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

    private var notesHealth: NotesConnectionHealth {
        NotesConnectionHealth(state: state)
    }

    private var notesConnected: Bool {
        notesHealth == .healthy
    }

    private var notesNeedContent: Bool {
        notesHealth == .empty
    }

    private var notesNeedAttention: Bool {
        notesHealth.isNeedsAttention
    }

    private var obsidianConnector: SourceConnectorCatalogItem? {
        state.sourceConnectorCatalog.first { $0.id == "obsidian" }
    }

    var body: some View {
        HStack(alignment: .center, spacing: 18) {
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.accentColor.opacity(0.14))
                Image(systemName: statusIcon)
                    .font(.system(size: 34, weight: .semibold))
                    .foregroundColor(statusColor)
            }
            .frame(width: 72, height: 72)

            VStack(alignment: .leading, spacing: 7) {
                Text(title)
                    .font(.system(size: 28, weight: .semibold))
                Text(detail)
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
        if notesConnected {
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
        if notesConnected {
            return state.hasConnectedObsidianVault ? "Sync notes" : "Reconnect notes"
        }
        if !notesConnected, let _ = obsidianConnector {
            if notesNeedAttention { return "Fix notes sync" }
            return notesNeedContent ? "Choose notes" : "Start notes sync"
        }
        if !notesConnected {
            return "Check status"
        }
        return "Sync notes"
    }

    private var primaryActionIcon: String {
        if notesConnected {
            return state.hasConnectedObsidianVault ? "arrow.triangle.2.circlepath" : "folder.badge.plus"
        }
        if !notesConnected, obsidianConnector != nil {
            if notesNeedAttention { return "exclamationmark.triangle.fill" }
            return notesNeedContent ? "folder.badge.questionmark" : "folder.badge.plus"
        }
        return "arrow.clockwise"
    }

    private var title: String {
        if notesConnected {
            return "Notes syncing"
        }
        if notesNeedAttention {
            return "Notes need attention"
        }
        if notesNeedContent {
            return "Choose notes with content"
        }
        return "Start notes sync once"
    }

    private var detail: String {
        if notesConnected {
            return "New notes go to Review first. Ask and connected AI tools use reviewed memory with citations."
        }
        if notesNeedAttention {
            return notesHealth.detail ?? "Cortex needs attention before these notes can keep syncing."
        }
        if notesNeedContent {
            return "Cortex could not find usable notes there. Choose a notes library with real content."
        }
        return "Choose the notes folder Cortex should sync. New memory goes to Review before Ask or AI tools can use it."
    }

    private var statusIcon: String {
        if notesConnected { return "checkmark.seal.fill" }
        if notesNeedAttention { return "exclamationmark.triangle.fill" }
        if notesNeedContent { return "folder.badge.questionmark" }
        return "link.circle.fill"
    }

    private var statusColor: Color {
        if notesConnected { return .green }
        if notesNeedAttention { return .orange }
        if notesNeedContent { return .orange }
        return .accentColor
    }

    private func runPrimaryAction() {
        if let connector = obsidianConnector {
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

    private var notesHealth: NotesConnectionHealth {
        NotesConnectionHealth(state: state)
    }

    private var obsidianConnector: SourceConnectorCatalogItem? {
        state.sourceConnectorCatalog.first { $0.id == "obsidian" }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionHeader(
                title: "Notes connection",
                detail: "Choose the folder Cortex should keep synced automatically."
            )

            if state.sourceConnectorCatalog.isEmpty {
                QuietState(title: "Checking note connections", detail: "Cortex is checking available local note connections.")
            } else if let connector = obsidianConnector {
                SourceConnectorStatusCard(
                    state: state,
                    connector: connector,
                    connected: isConnected(connector),
                    needsContent: notesHealth.isEmpty,
                    needsAttention: notesHealth.isNeedsAttention,
                    attentionDetail: notesHealth.detail
                )
            } else {
                QuietState(title: "Notes connection unavailable", detail: "Restart Cortex after the private memory store is ready.")
            }
        }
    }

    private func needsContent(_ connector: SourceConnectorCatalogItem) -> Bool {
        if state.sourceReadinessReport?.sources.contains(where: { $0.source == connector.id && $0.status == "empty" }) == true {
            return true
        }
        return state.activeSourceAccounts.contains { account in
            (account.source == connector.id || (connector.source_ids ?? []).contains(account.source))
                && (account.status.lowercased() == "empty" || account.auth_state.lowercased() == "needs-content")
        }
    }

    private func isConnected(_ connector: SourceConnectorCatalogItem) -> Bool {
        if needsContent(connector) {
            return false
        }
        if notesHealth.isNeedsAttention {
            return false
        }
        if connector.id == "obsidian", state.hasConnectedObsidianVault {
            return true
        }
        return state.activeSourceAccounts.contains { account in
            account.source == connector.id || (connector.source_ids ?? []).contains(account.source)
        }
    }
}

private struct ConnectionsDirectSourcesSection: View {
    @ObservedObject var state: AppState
    @State private var selectedTokenConnector: SourceConnectorCatalogItem?

    private let connectorOrder = [
        "calendar",
        "zotero",
        "notion",
        "slack",
        "github",
        "readwise",
        "raindrop",
        "linear",
        "jira"
    ]

    private var wiredConnectors: [SourceConnectorCatalogItem] {
        connectorOrder.compactMap { connectorID in
            state.sourceConnectorCatalog.first { $0.id == connectorID }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionHeader(
                title: "More source connections",
                detail: "Only working sync paths are shown here. Planned services stay hidden until they can actually connect."
            )

            if state.sourceConnectorCatalog.isEmpty {
                QuietState(title: "Checking source connections", detail: "Cortex is loading the local connector catalog.")
            } else if wiredConnectors.isEmpty {
                QuietState(title: "No extra source connections yet", detail: "Notes sync is ready. More direct connectors will appear here after the backend exposes them.")
            } else {
                VStack(spacing: 10) {
                    ForEach(wiredConnectors) { connector in
                        ConnectionsDirectSourceRow(
                            state: state,
                            connector: connector,
                            connected: isConnected(connector),
                            openTokenSetup: {
                                selectedTokenConnector = connector
                            }
                        )
                    }
                }
            }
        }
        .sheet(item: $selectedTokenConnector) { connector in
            ConnectorTokenSetupSheet(state: state, connector: connector)
                .frame(width: 560, height: connector.id == "jira" ? 500 : 430)
        }
    }

    private func isConnected(_ connector: SourceConnectorCatalogItem) -> Bool {
        state.activeSourceAccounts.contains { account in
            account.source == connector.id || (connector.source_ids ?? []).contains(account.source)
        }
    }
}

private struct ConnectionsDirectSourceRow: View {
    @ObservedObject var state: AppState
    let connector: SourceConnectorCatalogItem
    let connected: Bool
    let openTokenSetup: () -> Void

    private var isSyncing: Bool {
        state.connectorSyncingIDs.contains(connector.id)
    }

    private var activeAccount: SourceAccountItem? {
        state.activeSourceAccounts.first { account in
            account.source == connector.id || (connector.source_ids ?? []).contains(account.source)
        }
    }

    private var hasStoredConfig: Bool {
        state.hasStoredDirectConnectorConfig(connector)
    }

    var body: some View {
        HStack(alignment: .center, spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(statusColor.opacity(0.12))
                Image(systemName: sourceIcon)
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundColor(statusColor)
            }
            .frame(width: 52, height: 52)

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(connector.name)
                        .font(.headline)
                    SourceStatusChip(
                        title: statusTitle,
                        systemImage: statusChipIcon,
                        color: statusColor
                    )
                }
                Text(detail)
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let lastMessage = state.connectorLastMessages[connector.id] {
                    Text(lastMessage)
                        .font(.caption)
                        .foregroundColor(CortexRecoveryText.needsAttention(lastMessage) ? .orange : .secondary)
                        .lineLimit(2)
                }
            }

            Spacer(minLength: 12)

            Button {
                runAction()
            } label: {
                if isSyncing {
                    ProgressView()
                        .scaleEffect(0.78)
                        .frame(minWidth: 126, minHeight: 46)
                } else {
                    Label(actionTitle, systemImage: actionIcon)
                        .frame(minWidth: 126, minHeight: 46)
                }
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .disabled(state.isBusy || isSyncing)
        }
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.22)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var statusTitle: String {
        if activeAccount?.needsAttention == true { return "Needs attention" }
        if connected { return "Connected" }
        if hasStoredConfig { return "Configured" }
        if connector.connectorReadinessStatus == "token-ready" { return "Token sync" }
        if connector.id == "zotero" { return "Local app" }
        if connector.id == "calendar" { return "Local feed" }
        return "Ready"
    }

    private var statusChipIcon: String {
        if activeAccount?.needsAttention == true { return "exclamationmark.circle.fill" }
        if connected { return "checkmark.circle.fill" }
        if hasStoredConfig { return "checkmark.circle" }
        if connector.connectorReadinessStatus == "token-ready" { return "key.fill" }
        return "link.circle"
    }

    private var statusColor: Color {
        if activeAccount?.needsAttention == true { return .orange }
        if connected { return .green }
        if hasStoredConfig { return .green }
        if connector.connectorReadinessStatus == "token-ready" { return .accentColor }
        return .secondary
    }

    private var sourceIcon: String {
        switch connector.id {
        case "calendar": return "calendar"
        case "zotero": return "books.vertical.fill"
        case "notion": return "doc.richtext"
        case "slack": return "bubble.left.and.bubble.right.fill"
        case "github": return "chevron.left.forwardslash.chevron.right"
        case "readwise": return "highlighter"
        case "raindrop": return "bookmark.fill"
        case "linear", "jira": return "checklist.checked"
        default: return "link.circle.fill"
        }
    }

    private var detail: String {
        if activeAccount?.needsAttention == true {
            return activeAccount?.last_error ?? "This connector needs attention before it can sync again."
        }
        switch connector.id {
        case "calendar":
            if connected { return "Calendar events are available for Review and cited Ask." }
            if hasStoredConfig { return "Calendar sync is configured. Run it again when you want fresh events." }
            return "Sync a read-only calendar export or feed into Review."
        case "zotero":
            return connected ? "Zotero research is available for Review and cited Ask." : "Sync from the Zotero desktop local API when Zotero is running."
        default:
            if connected { return "\(connector.name) is connected. Run sync again when you want fresh memory." }
            if hasStoredConfig { return "\(connector.name) sync is configured. Run it again when you want fresh memory." }
            return "Connect with a read-only token, then Cortex sends useful items to Review with citations."
        }
    }

    private var actionTitle: String {
        if connected || hasStoredConfig {
            return connector.id == "calendar" ? "Sync" : "Sync again"
        }
        switch connector.id {
        case "calendar": return "Connect"
        case "zotero": return "Sync"
        default: return "Set up"
        }
    }

    private var actionIcon: String {
        switch connector.id {
        case "calendar": return "calendar.badge.plus"
        case "zotero": return "arrow.triangle.2.circlepath"
        default: return "key.fill"
        }
    }

    private func runAction() {
        switch connector.id {
        case "calendar":
            if hasStoredConfig {
                state.syncStoredDirectConnector(connector)
            } else {
                state.connectCalendarFile(connector)
            }
        case "zotero":
            state.syncZoteroLocal(connector)
        default:
            if hasStoredConfig {
                state.syncStoredDirectConnector(connector)
            } else {
                openTokenSetup()
            }
        }
    }
}

private struct ConnectorTokenSetupSheet: View {
    @ObservedObject var state: AppState
    let connector: SourceConnectorCatalogItem
    @Environment(\.dismiss) private var dismiss

    @State private var token = ""
    @State private var repositories = ""
    @State private var channels = ""
    @State private var siteURL = ""
    @State private var email = ""
    @State private var jql = ""
    @State private var collectionID = "0"

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color.accentColor.opacity(0.12))
                    Image(systemName: "key.fill")
                        .font(.system(size: 22, weight: .semibold))
                        .foregroundColor(.accentColor)
                }
                .frame(width: 46, height: 46)

                VStack(alignment: .leading, spacing: 4) {
                    Text("Connect \(connector.name)")
                        .font(.title2)
                        .fontWeight(.semibold)
                    Text("Cortex uses read-only access for this sync and sends new memory to Review first.")
                        .font(.callout)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Spacer(minLength: 0)

                Button {
                    dismiss()
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 15, weight: .semibold))
                        .frame(width: 38, height: 38)
                }
                .buttonStyle(.borderless)
            }
            .padding(20)

            Divider()

            VStack(alignment: .leading, spacing: 14) {
                if connector.id == "jira" {
                    labeledField("Site URL", text: $siteURL, placeholder: "https://your-team.atlassian.net")
                    labeledField("Account email", text: $email, placeholder: "name@company.com")
                    labeledSecureField("API token", text: $token, placeholder: "Atlassian API token")
                    labeledField("JQL filter", text: $jql, placeholder: "Optional")
                } else {
                    labeledSecureField(tokenLabel, text: $token, placeholder: tokenPlaceholder)
                }

                if connector.id == "github" {
                    labeledField("Repositories", text: $repositories, placeholder: "owner/repo, org/repo")
                }

                if connector.id == "slack" {
                    labeledField("Channels", text: $channels, placeholder: "C0123456789, C0987654321")
                }

                if connector.id == "raindrop" {
                    labeledField("Collection", text: $collectionID, placeholder: "0 for all bookmarks")
                }

                HStack(spacing: 10) {
                    Button {
                        dismiss()
                    } label: {
                        Text("Cancel")
                            .frame(minWidth: 104, minHeight: 44)
                    }
                    .controlSize(.large)

                    Spacer()

                    Button {
                        sync()
                    } label: {
                        Label("Sync \(connector.name)", systemImage: "arrow.triangle.2.circlepath")
                            .frame(minWidth: 156, minHeight: 46)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(!isValid || state.isBusy || state.connectorSyncingIDs.contains(connector.id))
                }
            }
            .padding(20)

            Spacer(minLength: 0)
        }
        .background(connectionsSheetBackground)
    }

    private var tokenLabel: String {
        connector.id == "linear" ? "API key" : "Read-only token"
    }

    private var tokenPlaceholder: String {
        switch connector.id {
        case "notion": return "Notion integration token"
        case "slack": return "Slack bot or user token"
        case "github": return "GitHub fine-grained token"
        case "readwise": return "Readwise access token"
        case "raindrop": return "Raindrop API token"
        case "linear": return "Linear API key"
        default: return "Token"
        }
    }

    private var isValid: Bool {
        switch connector.id {
        case "github":
            return !trimmed(token).isEmpty && !splitList(repositories).isEmpty
        case "slack":
            return !trimmed(token).isEmpty && !splitList(channels).isEmpty
        case "jira":
            return !trimmed(siteURL).isEmpty && !trimmed(email).isEmpty && !trimmed(token).isEmpty
        default:
            return !trimmed(token).isEmpty
        }
    }

    @ViewBuilder
    private func labeledField(_ label: String, text: Binding<String>, placeholder: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.caption)
                .fontWeight(.semibold)
                .foregroundColor(.secondary)
            TextField(placeholder, text: text)
                .textFieldStyle(.roundedBorder)
                .controlSize(.large)
        }
    }

    @ViewBuilder
    private func labeledSecureField(_ label: String, text: Binding<String>, placeholder: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.caption)
                .fontWeight(.semibold)
                .foregroundColor(.secondary)
            SecureField(placeholder, text: text)
                .textFieldStyle(.roundedBorder)
                .controlSize(.large)
        }
    }

    private func sync() {
        let payload = syncPayload()
        dismiss()
        Task {
            await state.syncDirectConnector(connector, payload: payload, rememberPayload: true)
        }
    }

    private func syncPayload() -> [String: Any] {
        var payload: [String: Any] = [
            "processing": "sync"
        ]
        switch connector.id {
        case "github":
            payload["token"] = trimmed(token)
            payload["repositories"] = splitList(repositories)
            payload["max_records"] = 500
        case "slack":
            payload["token"] = trimmed(token)
            payload["channels"] = splitList(channels)
            payload["max_records"] = 200
        case "jira":
            payload["api_token"] = trimmed(token)
            payload["email"] = trimmed(email)
            payload["site_url"] = trimmed(siteURL)
            payload["max_records"] = 500
            if !trimmed(jql).isEmpty {
                payload["jql"] = trimmed(jql)
            }
        case "notion":
            payload["token"] = trimmed(token)
            payload["max_records"] = 200
        case "raindrop":
            payload["token"] = trimmed(token)
            payload["collection_id"] = trimmed(collectionID).isEmpty ? "0" : trimmed(collectionID)
            payload["max_records"] = 500
        case "readwise", "linear":
            payload["token"] = trimmed(token)
            payload["max_records"] = 500
        default:
            payload["token"] = trimmed(token)
        }
        return payload
    }

    private func trimmed(_ value: String) -> String {
        value.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func splitList(_ value: String) -> [String] {
        value
            .components(separatedBy: CharacterSet(charactersIn: ",\n"))
            .map { trimmed($0) }
            .filter { !$0.isEmpty }
    }
}

private enum NotesConnectionHealth: Equatable {
    case healthy
    case needsAttention(String?)
    case empty
    case disconnected

    @MainActor init(state: AppState) {
        if let readiness = state.sourceReadinessReport?.sources.first(where: { $0.source == "obsidian" }) {
            let status = readiness.status.lowercased()
            if readiness.needsAttention || !readiness.warnings.isEmpty {
                self = .needsAttention(readiness.warnings.first)
                return
            }
            if status == "empty" {
                self = .empty
                return
            }
            if ["connected", "synced", "needs_review", "imported"].contains(status) {
                self = .healthy
                return
            }
        }

        if let account = state.activeSourceAccounts.first(where: { $0.source == "obsidian" }) {
            if account.needsAttention {
                self = .needsAttention(account.last_error)
                return
            }
            if account.status.lowercased() == "empty" || account.auth_state.lowercased() == "needs-content" {
                self = .empty
                return
            }
            self = .healthy
            return
        }

        self = state.hasConnectedObsidianVault ? .healthy : .disconnected
    }

    static func == (lhs: NotesConnectionHealth, rhs: NotesConnectionHealth) -> Bool {
        switch (lhs, rhs) {
        case (.healthy, .healthy), (.empty, .empty), (.disconnected, .disconnected), (.needsAttention, .needsAttention):
            return true
        default:
            return false
        }
    }

    var detail: String? {
        if case .needsAttention(let detail) = self {
            return detail
        }
        return nil
    }

    var isNeedsAttention: Bool {
        if case .needsAttention = self {
            return true
        }
        return false
    }

    var isEmpty: Bool {
        self == .empty
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
                title: "Use reviewed memory outside Cortex",
                detail: "Optional. Ask in Cortex first, then enable this when another AI app should read reviewed memory."
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
                    Text("AI apps")
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(.secondary)
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
                        Label("Enable in apps", systemImage: "link.circle")
                            .frame(minWidth: 138, minHeight: 46)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                } else {
                    Button {
                        state.refreshIntegrationStates()
                    } label: {
                        Label("Refresh", systemImage: "arrow.clockwise")
                            .frame(minWidth: 108, minHeight: 46)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)
                }
            }
            .padding(14)
            .background(connectionsPanelBackground)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.22)))
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
                            Text("Enabled")
                                .font(.caption)
                                .fontWeight(.semibold)
                                .foregroundColor(.green)
                        }
                        .padding(10)
                        .background(CortexDesign.cardBackground)
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
            return "Enabled in \(connectedCount) app\(connectedCount == 1 ? "" : "s")"
        }
        if !detectedConnectable.isEmpty {
            return "\(detectedConnectable.count) app\(detectedConnectable.count == 1 ? "" : "s") detected"
        }
        return "No app needed"
    }

    private var statusDetail: String {
        if connectedCount > 0 {
            return "These apps can read reviewed memory with citations."
        }
        if !detectedConnectable.isEmpty {
            return "Enable this only when you want reviewed memory available outside Cortex."
        }
        return "Cortex works without another app. Ask uses reviewed memory with citations."
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
                    title: "Backup & privacy",
                    detail: "Review first, reviewed AI reads, AI saves to Review, and local backups."
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
                    detail: settings.review_new_captures ? "new memory waits for approval" : "new memory can activate",
                    systemImage: settings.review_new_captures ? "checklist" : "bolt.fill",
                    color: settings.review_new_captures ? .green : .orange
                )
                ConnectionsTrustTile(
                    title: settings.allow_agent_reads ? "AI access on" : "AI access off",
                    detail: aiAccessDetail(settings),
                    systemImage: settings.allow_agent_reads ? "eye.fill" : "eye.slash.fill",
                    color: settings.allow_agent_reads ? .accentColor : .secondary
                )
                ConnectionsTrustTile(
                    title: backupCount > 0 ? "Backup ready" : "No backup yet",
                    detail: backupCount > 0 ? "\(backupCount) local archive\(backupCount == 1 ? "" : "s")" : "create one before big changes",
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

    private func aiAccessDetail(_ settings: AppSettingsResponse) -> String {
        guard settings.allow_agent_reads else { return "tools cannot read memory" }
        let readText = settings.allow_pending_in_context ? "pending reads allowed" : "reviewed memory reads"
        return settings.allow_agent_writes ? "\(readText), saves to Review" : readText
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
        .background(CortexDesign.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

private struct ConnectionsActiveSourcesSection: View {
    @ObservedObject var state: AppState

    private var activeAccounts: [SourceAccountItem] {
        state.sourceAccounts.filter { account in
            account.disconnected_at == nil
                && account.status.lowercased() != "empty"
                && account.auth_state.lowercased() != "needs-content"
        }
    }

    private var accountsNeedingContent: [SourceAccountItem] {
        state.sourceAccounts.filter { account in
            account.disconnected_at == nil
                && (account.status.lowercased() == "empty" || account.auth_state.lowercased() == "needs-content")
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if activeAccounts.isEmpty {
                if accountsNeedingContent.isEmpty {
                    QuietState(title: "No source connected", detail: "Connect a local source once. Cortex syncs after that.")
                } else {
                    QuietState(title: "Choose a source with content", detail: "The selected folder did not produce usable memory yet.")
                }
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
