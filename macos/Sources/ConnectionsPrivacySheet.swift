import Foundation
import SwiftUI
import UniformTypeIdentifiers

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
                Text("Connect a notes source, keep it synced, and manage local privacy settings.")
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
    @State private var advancedSourcesExpanded = false
    @State private var aiToolsExpanded = false
    @State private var sourceAuditExpanded = false
    @State private var recoveryToolsExpanded = false
    @State private var developerDetailsExpanded = false

    private var primaryActiveSourceAccounts: [SourceAccountItem] {
        state.activeSourceAccounts.filter { account in
            guard let readiness = readiness(for: account.source) else {
                return account.source == "obsidian"
            }
            return readiness.showInPrimaryUI
        }
    }

    private var connectedSourceCount: Int {
        max(primaryActiveSourceAccounts.count, notesHealth == .healthy ? 1 : 0)
    }

    private var connectionStatusDetail: String {
        if notesHealth.isNeedsAttention {
            return "source needs attention"
        }
        return "\(connectedSourceCount) source\(connectedSourceCount == 1 ? "" : "s")"
    }

    private var notesHealth: NotesConnectionHealth {
        NotesConnectionHealth(state: state)
    }

    private func readiness(for sourceID: String) -> SourceReadinessItem? {
        state.sourceReadinessReport?.sources.first { source in
            source.source == sourceID || (source.source_ids ?? []).contains(sourceID)
        }
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
                otherSourceConnections
                if state.connectedAIIntegrationCount > 0 {
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

    private var otherSourceConnections: some View {
        DisclosureGroup(isExpanded: $advancedSourcesExpanded) {
            ConnectionsDirectSourcesSection(state: state)
                .padding(.top, 10)
        } label: {
            ConnectionsDisclosureLabel(
                systemImage: "link.badge.plus",
                title: "Other source connections",
                detail: advancedSourceDisclosureDetail
            )
        }
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.22)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var advancedSourceDisclosureDetail: String {
        let extraSources = state.activeSourceAccounts.filter { $0.source != "obsidian" }.count
        if extraSources > 0 {
            return "\(extraSources) extra source\(extraSources == 1 ? "" : "s") connected"
        }
        return "Read-only token, local-file, and local-app sync"
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

                if state.connectedAIIntegrationCount == 0 {
                    optionalAITools
                }

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
        state.sourceConnectorCatalog.first { $0.id == "obsidian" && $0.showInPrimaryUI }
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
            return state.hasConnectedObsidianVault ? "Sync source" : "Reconnect source"
        }
        if !notesConnected, let _ = obsidianConnector {
            if notesNeedAttention { return "Fix source sync" }
            return notesNeedContent ? "Choose source" : "Start source sync"
        }
        if !notesConnected {
            return "Check status"
        }
        return "Sync source"
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
            return "Source syncing"
        }
        if notesNeedAttention {
            return "Source needs attention"
        }
        if notesNeedContent {
            return "Choose a source with content"
        }
        return "Start source sync once"
    }

    private var detail: String {
        if notesConnected {
            return "New source memory goes to Review first. Ask uses reviewed memory with citations."
        }
        if notesNeedAttention {
            return notesHealth.detail ?? "Cortex needs attention before this source can keep syncing."
        }
        if notesNeedContent {
            return "Cortex could not find usable content there. Choose a source with real notes or records."
        }
        return "Choose the source Cortex should sync. New memory goes to Review before Ask uses it."
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
        state.sourceConnectorCatalog.first { $0.id == "obsidian" && $0.showInPrimaryUI }
    }

    private var obsidianReadiness: SourceReadinessItem? {
        state.sourceReadinessReport?.sources.first { $0.source == "obsidian" }
    }

    private var primarySourceDetail: String {
        if let obsidianReadiness {
            return "\(obsidianReadiness.syncPlanDisplayTitle). Choose the local source Cortex should keep synced automatically."
        }
        return "Choose the local source Cortex should keep synced automatically."
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionHeader(
                title: "Primary source connection",
                detail: primarySourceDetail
            )

            if state.sourceConnectorCatalog.isEmpty {
                QuietState(title: "Checking source connections", detail: "Cortex is checking available local source connections.")
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
                QuietState(title: "Source connection unavailable", detail: "Restart Cortex after the private memory store is ready.")
            }
        }
    }

    private func needsContent(_ connector: SourceConnectorCatalogItem) -> Bool {
        if state.sourceReadinessReport?.sources.contains(where: { $0.source == connector.id && $0.status == "empty" }) == true {
            return true
        }
        return state.activeSourceAccounts.contains { account in
            (account.source == connector.id || (connector.source_ids ?? []).contains(account.source))
                && account.needsContent
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

    private var wiredConnectors: [SourceConnectorCatalogItem] {
        state.sourceConnectorCatalog
            .filter { state.isDirectConnectorSyncWired($0) }
            .sorted {
                let leftRank = state.directConnectorSortRank($0.id)
                let rightRank = state.directConnectorSortRank($1.id)
                if leftRank != rightRank { return leftRank < rightRank }
                return $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
            }
    }

    private var plannedConnectorNames: [String] {
        state.sourceConnectorCatalog
            .filter { connector in
                connector.isAccountSignInPlanned
                    || state.readiness(for: connector)?.sync_plan?.mode == "planned_account_sync"
            }
            .filter { !$0.showInPrimaryUI }
            .map(\.name)
            .sorted()
    }

    private var plannedConnectorSummary: String? {
        let names = plannedConnectorNames
        guard !names.isEmpty else { return nil }
        let visible = names.prefix(3).joined(separator: ", ")
        let remaining = names.count - min(3, names.count)
        if remaining > 0 {
            return "\(visible), and \(remaining) more are planned sign-in connectors."
        }
        return "\(visible) \(names.count == 1 ? "is" : "are") planned sign-in \(names.count == 1 ? "connector" : "connectors")."
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionHeader(
                title: "Other source connections",
                detail: "Optional read-only connections that already sync into Review. Planned sign-in services stay hidden until they are real."
            )

            if state.sourceConnectorCatalog.isEmpty {
                QuietState(title: "Checking source connections", detail: "Cortex is loading local source sync options.")
            } else if wiredConnectors.isEmpty {
                QuietState(title: "No extra connectors ready", detail: "Use notes sync as the default source path.")
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

            if let plannedConnectorSummary {
                Text(plannedConnectorSummary)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .sheet(item: $selectedTokenConnector) { connector in
            ConnectorTokenSetupSheet(state: state, connector: connector)
                .frame(width: 640, height: 680)
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
        state.sourceAccount(connector)
    }

    private var pausedAccount: SourceAccountItem? {
        state.disconnectedSourceAccount(connector)
    }

    private var isPaused: Bool {
        activeAccount == nil && pausedAccount != nil
    }

    private var readiness: SourceReadinessItem? {
        state.sourceReadinessReport?.sources.first { source in
            source.source == connector.id || (connector.source_ids ?? []).contains(source.source)
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
                if let sourceHealthLine {
                    Text(sourceHealthLine)
                        .font(.caption)
                        .fontWeight(.medium)
                        .foregroundColor(sourceHealthColor)
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

            if !isPaused && (activeAccount != nil || hasStoredConfig) {
                Button {
                    state.pauseDirectConnectorSync(connector)
                } label: {
                    Label("Pause", systemImage: "pause.circle")
                        .frame(minWidth: 98, minHeight: 42)
                }
                .buttonStyle(.bordered)
                .foregroundColor(.secondary)
                .help("Pause automatic sync. Already synced local memory is kept.")
                .disabled(state.isBusy || isSyncing)
            }
        }
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.22)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var statusTitle: String {
        if isPaused { return "Paused" }
        if activeAccount?.needsAttention == true { return "Needs attention" }
        if let readiness, readiness.pending > 0 { return "Review" }
        if let readiness { return readiness.syncPlanModeTitle }
        if connected { return "Connected" }
        if hasStoredConfig { return "Configured" }
        if let setupModeTitle { return setupModeTitle }
        return "Ready"
    }

    private var statusChipIcon: String {
        if isPaused { return "pause.circle.fill" }
        if activeAccount?.needsAttention == true { return "exclamationmark.circle.fill" }
        if let readiness, readiness.pending > 0 { return "tray.full.fill" }
        if let readiness { return readiness.syncPlanIcon }
        if connected { return "checkmark.circle.fill" }
        if hasStoredConfig { return "checkmark.circle" }
        if connector.connectionSetup?.mode == "native-token-connector" { return "key.fill" }
        if connector.connectionSetup?.mode == "native-local-connector" { return "externaldrive.fill" }
        return "link.circle"
    }

    private var statusColor: Color {
        if isPaused { return .secondary }
        if activeAccount?.needsAttention == true { return .orange }
        if let readiness, readiness.pending > 0 { return .orange }
        if let readiness { return readiness.syncPlanColor }
        if connected { return .green }
        if hasStoredConfig { return .green }
        if connector.connectionSetup?.available == true { return .accentColor }
        return .secondary
    }

    private var setupModeTitle: String? {
        switch connector.connectionSetup?.mode {
        case "native-token-connector":
            return "Token sync"
        case "native-local-connector":
            if connector.id == "zotero" { return "Local app" }
            if connector.id == "calendar" { return "Local feed" }
            return "Local sync"
        default:
            return nil
        }
    }

    private var sourceIcon: String {
        switch connector.id {
        case "calendar": return "calendar"
        case "gmail", "outlook": return "envelope.fill"
        case "google-drive": return "folder.fill"
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
        if isPaused {
            return "\(connector.name) sync is paused. Already synced local memory stays available; resume when you want fresh items."
        }
        if activeAccount?.needsAttention == true {
            return activeAccount?.last_error ?? "This connector needs attention before it can sync again."
        }
        switch connector.id {
        case "calendar":
            if connected { return "Calendar events are available for Review and cited Ask." }
            if hasStoredConfig { return "Calendar sync is configured. Run it again when you want fresh events." }
            return "Connect a read-only calendar export or feed. Cortex keeps synced events available for Review and cited Ask."
        case "zotero":
            return connected ? "Zotero research is available for Review and cited Ask." : "Sync from the Zotero desktop local API when Zotero is running."
        default:
            if connected { return "\(connector.name) is connected. Run sync again when you want fresh memory." }
            if hasStoredConfig { return "\(connector.name) sync is configured. Run it again when you want fresh memory." }
            return connector.connectionSetup?.mode == "native-token-connector"
                ? "Connect with a read-only token. Cortex sends useful items to Review with citations."
                : "Connect this source. Cortex keeps synced items local and sends useful memory to Review first."
        }
    }

    private var sourceHealthLine: String? {
        guard let readiness else {
            if let lastSync = activeAccount?.last_sync_at {
                return "Last sync \(shortTimestamp(lastSync))"
            }
            return nil
        }
        let autosyncLabel = backendAutosyncLabel(for: readiness)
        if readiness.pending > 0 {
            return "\(readiness.pending) item\(readiness.pending == 1 ? "" : "s") waiting in Review · \(autosyncLabel)"
        }
        if readiness.active_memories > 0 {
            return "\(autosyncLabel) · \(readiness.active_memories) reviewed memor\(readiness.active_memories == 1 ? "y" : "ies") with \(Int((readiness.citation_coverage * 100).rounded()))% citation coverage"
        }
        if let lastSeen = readiness.last_seen_at {
            return "\(autosyncLabel) · Last sync \(shortTimestamp(lastSeen))"
        }
        return autosyncLabel
    }

    private var sourceHealthColor: Color {
        if let readiness, readiness.pending > 0 { return .orange }
        return .secondary
    }

    private func shortTimestamp(_ value: String) -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return "recently" }
        return String(trimmed.prefix(10))
    }

    private func backendAutosyncLabel(for readiness: SourceReadinessItem) -> String {
        guard let syncPlan = readiness.sync_plan else {
            return readiness.syncPlanDisplayTitle
        }
        if syncPlan.scheduler_supported == true {
            if syncPlan.due_now == true {
                return "\(readiness.syncPlanDisplayTitle) · Autosync due"
            }
            if let interval = syncPlan.sync_interval_seconds, interval > 0 {
                let minutes = max(1, interval / 60)
                return "\(readiness.syncPlanDisplayTitle) · Autosync \(minutes)m"
            }
            return "\(readiness.syncPlanDisplayTitle) · Autosync on"
        }
        if syncPlan.blocked_reason == "stored_sync_configuration_required" {
            return "\(readiness.syncPlanDisplayTitle) · Finish setup for autosync"
        }
        return readiness.syncPlanDisplayTitle
    }

    private var actionTitle: String {
        if isPaused { return "Resume" }
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
        if isPaused { return "play.circle.fill" }
        switch connector.id {
        case "calendar": return "calendar.badge.plus"
        case "zotero": return "arrow.triangle.2.circlepath"
        default: return "key.fill"
        }
    }

    private func runAction() {
        if isPaused {
            state.resumeDirectConnectorSync(connector)
            return
        }
        switch connector.id {
        case "calendar":
            hasStoredConfig ? state.syncStoredDirectConnector(connector) : openTokenSetup()
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

    @State private var fieldValues: [String: String] = [:]
    @State private var boolValues: [String: Bool] = [:]
    @State private var optionsExpanded = false
    @State private var accountDetailsExpanded = false

    private var setup: SourceConnectorConnectionSetup? {
        connector.connectionSetup
    }

    private var requiredFields: [SourceConnectorSetupField] {
        setupFields.filter { field in
            field.isRequired || (setup?.require_one_of ?? []).contains(field.name)
        }
    }

    private var optionalFields: [SourceConnectorSetupField] {
        setupFields.filter { field in
            !field.isRequired && !(setup?.require_one_of ?? []).contains(field.name)
        }
    }

    private var setupFields: [SourceConnectorSetupField] {
        (setup?.credential_fields ?? []) + (setup?.configuration_fields ?? [])
    }

    private var accountFields: [SourceConnectorSetupField] {
        setup?.common_fields ?? []
    }

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
                    Text(headerDetail)
                        .font(.callout)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("Setup is stored locally on this Mac. Pausing sync keeps already-synced memory in Cortex.")
                        .font(.caption)
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

            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if setup == nil {
                        QuietState(title: "Setup contract unavailable", detail: "Update Cortex and try this connection again.")
                    } else {
                        if !requiredFields.isEmpty {
                            VStack(alignment: .leading, spacing: 12) {
                                Text("Required")
                                    .font(.caption)
                                    .fontWeight(.semibold)
                                    .foregroundColor(.secondary)
                                ForEach(requiredFields) { field in
                                    setupField(field)
                                }
                            }
                        }

                        if !optionalFields.isEmpty {
                            DisclosureGroup(isExpanded: $optionsExpanded) {
                                VStack(alignment: .leading, spacing: 12) {
                                    ForEach(optionalFields) { field in
                                        setupField(field)
                                    }
                                }
                                .padding(.top, 12)
                            } label: {
                                Text("Sync options")
                                    .font(.callout)
                                    .fontWeight(.semibold)
                            }
                        }

                        if !accountFields.isEmpty {
                            DisclosureGroup(isExpanded: $accountDetailsExpanded) {
                                VStack(alignment: .leading, spacing: 12) {
                                    ForEach(accountFields) { field in
                                        setupField(field)
                                    }
                                }
                                .padding(.top, 12)
                            } label: {
                                Text("Account label")
                                    .font(.callout)
                                    .fontWeight(.semibold)
                            }
                        }
                    }

                    if let validationMessage {
                        Text(validationMessage)
                            .font(.caption)
                            .foregroundColor(.orange)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(20)
            }

            Divider()

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
            .padding(20)
        }
        .background(connectionsSheetBackground)
    }

    private var headerDetail: String {
        switch setup?.mode {
        case "native-local-connector":
            return "Cortex reads this local source and sends useful memory to Review first."
        case "native-token-connector":
            return "Cortex uses your token for read-only sync and sends useful memory to Review first."
        default:
            return "Cortex sends useful memory to Review first, with citations preserved."
        }
    }

    private var validationMessage: String? {
        for field in setupFields where field.isRequired {
            if !hasValue(field) {
                return "\(field.displayLabel) is required."
            }
        }
        let requiredAny = setup?.require_one_of ?? []
        if !requiredAny.isEmpty && !requiredAny.contains(where: { name in
            guard let field = setupFields.first(where: { $0.name == name }) else { return false }
            return hasValue(field)
        }) {
            let labels = requiredAny
                .compactMap { name in setupFields.first(where: { $0.name == name })?.displayLabel }
                .joined(separator: " or ")
            return labels.isEmpty ? "Choose at least one source." : "Add \(labels)."
        }
        return nil
    }

    private var isValid: Bool {
        setup != nil && validationMessage == nil
    }

    @ViewBuilder
    private func setupField(_ field: SourceConnectorSetupField) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(field.displayLabel + (field.isRequired ? "" : " optional"))
                .font(.caption)
                .fontWeight(.semibold)
                .foregroundColor(.secondary)

            if field.isSecret {
                SecureField(placeholder(for: field), text: stringBinding(for: field))
                    .textFieldStyle(.roundedBorder)
                    .controlSize(.large)
            } else {
                switch field.normalizedKind {
                case "boolean":
                    Toggle(isOn: boolBinding(for: field)) {
                        Text(field.displayLabel)
                            .font(.callout)
                    }
                    .toggleStyle(.checkbox)
                case "select":
                    Picker("", selection: stringBinding(for: field)) {
                        ForEach(field.options ?? [], id: \.self) { option in
                            Text(option).tag(option)
                        }
                    }
                    .labelsHidden()
                    .pickerStyle(.menu)
                    .frame(maxWidth: 260, alignment: .leading)
                case "local_file", "local_folder":
                    HStack(spacing: 8) {
                        TextField(placeholder(for: field), text: stringBinding(for: field))
                            .textFieldStyle(.roundedBorder)
                            .controlSize(.large)
                        Button {
                            chooseLocalPath(for: field)
                        } label: {
                            Label("Choose", systemImage: field.normalizedKind == "local_folder" ? "folder" : "doc")
                                .frame(minHeight: 42)
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.large)
                    }
                default:
                    TextField(placeholder(for: field), text: stringBinding(for: field))
                        .textFieldStyle(.roundedBorder)
                        .controlSize(.large)
                }
            }
        }
    }

    private func stringBinding(for field: SourceConnectorSetupField) -> Binding<String> {
        Binding(
            get: {
                fieldValues[field.name] ?? defaultString(for: field)
            },
            set: { value in
                fieldValues[field.name] = value
            }
        )
    }

    private func boolBinding(for field: SourceConnectorSetupField) -> Binding<Bool> {
        Binding(
            get: {
                boolValues[field.name] ?? field.defaultBool
            },
            set: { value in
                boolValues[field.name] = value
            }
        )
    }

    private func defaultString(for field: SourceConnectorSetupField) -> String {
        if let option = field.options?.first, field.normalizedKind == "select", field.defaultString.isEmpty {
            return option
        }
        return field.defaultString
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
            "processing": setup?.default_processing ?? "sync"
        ]
        if let defaultMaxRecords = setup?.default_max_records {
            payload["max_records"] = defaultMaxRecords
        }
        if let defaultCursorName = setup?.default_cursor_name {
            payload["cursor_name"] = defaultCursorName
        }
        for field in accountFields + setupFields {
            switch field.normalizedKind {
            case "boolean":
                payload[field.name] = boolValues[field.name] ?? field.defaultBool
            case "integer":
                let value = trimmed(fieldValues[field.name] ?? field.defaultString)
                if !value.isEmpty, let number = Int(value) {
                    payload[field.name] = number
                }
            case "string_list":
                let values = splitList(fieldValues[field.name] ?? field.defaultString)
                if !values.isEmpty {
                    payload[field.name] = values
                }
            default:
                let value = trimmed(fieldValues[field.name] ?? defaultString(for: field))
                if !value.isEmpty {
                    payload[field.name] = value
                }
            }
        }
        return payload
    }

    private func hasValue(_ field: SourceConnectorSetupField) -> Bool {
        switch field.normalizedKind {
        case "boolean":
            return true
        case "string_list":
            return !splitList(fieldValues[field.name] ?? field.defaultString).isEmpty
        default:
            return !trimmed(fieldValues[field.name] ?? defaultString(for: field)).isEmpty
        }
    }

    private func placeholder(for field: SourceConnectorSetupField) -> String {
        switch field.normalizedKind {
        case "email":
            return "name@example.com"
        case "url":
            return "https://..."
        case "timestamp":
            return "YYYY-MM-DD or ISO timestamp"
        case "integer":
            if let minimum = field.minimum, let maximum = field.maximum {
                return "\(minimum)-\(maximum)"
            }
            return "Number"
        case "string_list":
            return "Separate values with commas or new lines"
        case "local_file":
            return "Select a local path"
        case "local_folder":
            return "Select a folder path"
        case "secret":
            return field.displayLabel
        default:
            return field.defaultString.isEmpty ? field.displayLabel : field.defaultString
        }
    }

    private func chooseLocalPath(for field: SourceConnectorSetupField) {
        let panel = NSOpenPanel()
        panel.title = "Choose \(field.displayLabel)"
        panel.prompt = "Choose"
        panel.canChooseFiles = field.normalizedKind == "local_file"
        panel.canChooseDirectories = field.normalizedKind == "local_folder"
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false
        if field.name == "ics_path", let calendarType = UTType(filenameExtension: "ics") {
            panel.allowedContentTypes = [calendarType]
        }
        if panel.runModal() == .OK, let url = panel.url {
            fieldValues[field.name] = url.standardizedFileURL.path
        }
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
            if account.needsContent {
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
                && !account.needsContent
                && shouldShowInPrimaryUI(account)
        }
    }

    private var accountsNeedingContent: [SourceAccountItem] {
        state.sourceAccounts.filter { account in
            account.disconnected_at == nil
                && account.needsContent
                && shouldShowInPrimaryUI(account)
        }
    }

    private func shouldShowInPrimaryUI(_ account: SourceAccountItem) -> Bool {
        guard let readiness = readiness(for: account.source) else {
            return account.source == "obsidian"
        }
        return readiness.showInPrimaryUI
    }

    private func readiness(for sourceID: String) -> SourceReadinessItem? {
        state.sourceReadinessReport?.sources.first { source in
            source.source == sourceID || (source.source_ids ?? []).contains(sourceID)
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
