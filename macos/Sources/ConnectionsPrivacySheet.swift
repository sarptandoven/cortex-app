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
                Text("Connect notes, keep memory local, and choose what AI tools can use.")
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
            .accessibilityLabel("Close Connections & Privacy")
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
            VStack(alignment: .leading, spacing: CortexDesign.Space.lg) {
                ConnectionsOverviewHero(state: state)

                ConnectionsObsidianSection(state: state)
                // Source-connection options (the connections library + ChatGPT/Claude import) must be
                // available even on first run — a user who doesn't want the local notes folder needs a
                // way to connect any other source to get past onboarding. Only the privacy/trust and
                // advanced controls wait until at least one source is connected.
                otherSourceConnections
                if !state.firstRunNeedsSource {
                    if state.connectedAIIntegrationCount > 0 {
                        ConnectionsAIToolsSection(state: state)
                    }

                    if let summary = state.trustSummary {
                        ConnectionsPrivacyDefaultsSection(state: state, summary: summary)
                        privacySettings(summary: summary)
                        connectedNow
                        advancedControls(summary: summary)
                    } else {
                        ConnectionsRetryState(
                            state: state,
                            title: "Preparing privacy controls",
                            detail: "Cortex is reading local privacy settings and connection history."
                        ) {
                            Task {
                                await state.loadTrust()
                                await state.loadSourceConnectivity()
                            }
                        }
                    }
                }
            }
            .padding(CortexDesign.Space.lg)
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
                title: "More connections",
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
        return "Optional services with read-only sync"
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

                ConnectionsMCPAccessSection(state: state)

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
                            CortexCloudSection(state: state)
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
            return state.hasConnectedObsidianVault ? "Sync notes" : "Reconnect notes"
        }
        if !notesConnected, let _ = obsidianConnector {
            if notesNeedAttention { return "Fix notes" }
            return notesNeedContent ? "Choose notes" : "Connect notes"
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
            return "Notes connected"
        }
        if notesNeedAttention {
            return "Notes need attention"
        }
        if notesNeedContent {
            return "Choose notes with content"
        }
        return "Connect notes once"
    }

    private var detail: String {
        if notesConnected {
            return "New memory goes to Review first. Ask uses reviewed memory with citations."
        }
        if notesNeedAttention {
            return notesHealth.detail ?? "Cortex needs attention before notes can keep syncing."
        }
        if notesNeedContent {
            return "Cortex could not find usable content there. Choose a notes library with real content."
        }
        return "Choose the notes Cortex should sync. New memory goes to Review before Ask uses it."
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
            state.connectLocalNotesFolder(connector, chooseNew: notesNeedContent)
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
                title: "Primary notes",
                detail: primarySourceDetail
            )

            if state.sourceConnectorCatalog.isEmpty {
                ConnectionsRetryState(
                    state: state,
                    title: "Checking notes connection",
                    detail: "Cortex is checking available local notes."
                ) {
                    Task { await state.loadSourceConnectivity() }
                }
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
    @State private var connectorSearch: String = ""

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

    // Library search + category grouping (VSCode-extensions style: searchable, sectioned list of
    // optional connections). Reuses the existing, correct ConnectionsDirectSourceRow per connector.
    private var filteredConnectors: [SourceConnectorCatalogItem] {
        let query = connectorSearch.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !query.isEmpty else { return wiredConnectors }
        return wiredConnectors.filter {
            $0.name.lowercased().contains(query) || ($0.category ?? "").lowercased().contains(query)
        }
    }

    private var connectorsByCategory: [(category: String, connectors: [SourceConnectorCatalogItem])] {
        let groups = Dictionary(grouping: filteredConnectors) { $0.category ?? "Other" }
        return groups.keys.sorted().map { (category: $0, connectors: groups[$0] ?? []) }
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
            AIChatsImportCard(state: state)
            SectionHeader(
                title: "Connections library",
                detail: "Browse and connect optional read-only services. Search, pick one, and Cortex shows exactly how to connect it. Everything here is optional and syncs into your memory."
            )

            if state.sourceConnectorCatalog.isEmpty {
                ConnectionsRetryState(
                    state: state,
                    title: "Checking connections",
                    detail: "Cortex is loading available read-only connections."
                ) {
                    Task { await state.loadSourceConnectivity() }
                }
            } else if wiredConnectors.isEmpty {
                QuietState(title: "No extra connectors ready", detail: "Use notes sync as the default source path.")
            } else {
                HStack(spacing: 6) {
                    Image(systemName: "magnifyingglass").foregroundColor(.secondary)
                    TextField("Search connections", text: $connectorSearch)
                        .textFieldStyle(.plain)
                    if !connectorSearch.isEmpty {
                        Button { connectorSearch = "" } label: { Image(systemName: "xmark.circle.fill") }
                            .buttonStyle(.borderless)
                            .foregroundColor(.secondary)
                    }
                }
                .padding(8)
                .background(RoundedRectangle(cornerRadius: 8).fill(Color.secondary.opacity(0.08)))

                if filteredConnectors.isEmpty {
                    QuietState(title: "No matching connections", detail: "Try a different search term.")
                } else {
                    ForEach(connectorsByCategory, id: \.category) { group in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(group.category.uppercased())
                                .font(.caption2)
                                .fontWeight(.semibold)
                                .foregroundColor(.secondary)
                            ForEach(group.connectors) { connector in
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

/// Import your ChatGPT / Claude history. There's no live sign-in for these (the providers don't
/// offer one), so this card guides the export, auto-detects it in Downloads, and takes a
/// drag-drop or file pick. Imported content is trusted and usable immediately.
private struct AIChatsImportCard: View {
    @ObservedObject var state: AppState
    @State private var isTargeted = false

    private let steps = [
        "In ChatGPT: Settings → Data controls → Export data (Claude: Settings → Account → Export data).",
        "Download the export email's .zip — it contains conversations.json.",
        "Drop it below, or click Choose export file. Cortex imports it into your memory right away."
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Image(systemName: "bubble.left.and.text.bubble.right")
                    .font(.title3)
                    .foregroundColor(.accentColor)
                VStack(alignment: .leading, spacing: 2) {
                    Text("AI chats — ChatGPT & Claude").font(.headline)
                    Text("These have no live sign-in, so import your export. It becomes usable immediately.")
                        .font(.caption).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }

            if let summary = state.detectedExportSummary {
                HStack(spacing: 8) {
                    Image(systemName: "sparkles").foregroundColor(.accentColor)
                    Text(summary).font(.callout).fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 8)
                    Button { state.importDetectedExports() } label: { Text("Import") }
                        .buttonStyle(.borderedProminent)
                        .disabled(state.importInFlight)
                }
                .padding(10)
                .background(RoundedRectangle(cornerRadius: 8).fill(Color.accentColor.opacity(0.10)))
            }

            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                HStack(alignment: .top, spacing: 8) {
                    Text("\(index + 1).").font(.caption).fontWeight(.semibold).foregroundColor(.accentColor).monospacedDigit()
                    Text(step).font(.caption).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }

            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6]))
                .foregroundColor(isTargeted ? Color.accentColor : Color.secondary.opacity(0.4))
                .frame(height: 66)
                .overlay(
                    HStack(spacing: 8) {
                        if state.importInFlight { ProgressView().scaleEffect(0.7) }
                        Text(state.importInFlight ? "Importing…" : "Drag your export here, or")
                            .font(.callout).foregroundColor(.secondary)
                        if !state.importInFlight {
                            Button { state.importAIChatExport() } label: {
                                Label("Choose export file…", systemImage: "folder.badge.plus")
                            }
                        }
                    }
                )
                .onDrop(of: [.fileURL], isTargeted: $isTargeted) { providers in
                    guard let provider = providers.first else { return false }
                    provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                        var resolved: String?
                        if let data = item as? Data, let url = URL(dataRepresentation: data, relativeTo: nil) {
                            resolved = url.standardizedFileURL.path
                        } else if let url = item as? URL {
                            resolved = url.standardizedFileURL.path
                        }
                        guard let path = resolved else { return }
                        Task { @MainActor in await state.importFromPath(path) }
                    }
                    return true
                }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(connectionsPanelBackground))
        .onAppear { Task { await state.detectAvailableExports() } }
    }
}

private struct ConnectionsDirectSourceRow: View {
    @ObservedObject var state: AppState
    let connector: SourceConnectorCatalogItem
    let connected: Bool
    let openTokenSetup: () -> Void
    @State private var confirmRemove = false
    @State private var confirmPause = false

    private var isSyncing: Bool {
        state.connectorSyncingIDs.contains(connector.id)
    }

    private var isOAuthStarting: Bool {
        state.connectorOAuthStartingIDs.contains(connector.id)
    }

    private var hasManagedOAuth: Bool {
        connector.connectionSetup?.supportsManagedOAuth == true
    }

    private var managedOAuthConfigured: Bool {
        !hasManagedOAuth || connected || state.managedOAuthIsConfigured(connector)
    }

    private var managedOAuthProviderName: String {
        switch connector.connectionSetup?.oauth_provider?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "google": return "Google"
        case "microsoft": return "Microsoft"
        case "notion": return "Notion"
        default: return "this service"
        }
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

    private var connectorSourceIDs: Set<String> {
        var ids: Set<String> = [connector.id]
        (connector.source_ids ?? []).forEach { ids.insert($0) }
        return ids
    }

    // A deletable import belonging to this connector, if any. Removing it clears
    // the captures and memories that import produced.
    private var removableImport: SourceImportHistoryItem? {
        state.importHistory.first { item in
            item.can_delete
                && item.deleted_at == nil
                && (connectorSourceIDs.contains(item.source_hint)
                    || item.sources.contains { connectorSourceIDs.contains($0.source) })
        }
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

            if hasManagedOAuth && !managedOAuthConfigured {
                // Unconfigured connector: show a status badge, not a dead button.
                Label("Setup required", systemImage: "key.slash")
                    .font(.callout)
                    .fontWeight(.medium)
                    .foregroundColor(.secondary)
                    .frame(minWidth: 126, minHeight: 46)
                    .padding(.horizontal, 12)
                    .background(Color(nsColor: .controlBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .help(state.managedOAuthConfigurationMessage(connector) ?? "\(connector.name) sign-in is not configured for this build yet.")
                    .accessibilityLabel("\(connector.name): setup required, not available in this build")
            } else {
                Button {
                    runAction()
                } label: {
                    if isSyncing || isOAuthStarting {
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
                .disabled(state.isBusy || isSyncing || isOAuthStarting)
            }

            if !isPaused && (activeAccount != nil || hasStoredConfig) {
                Button {
                    confirmPause = true
                } label: {
                    Label("Pause", systemImage: "pause.circle")
                        .frame(minWidth: 98, minHeight: 42)
                }
                .buttonStyle(.bordered)
                .foregroundColor(.secondary)
                .help("Pause automatic sync. Already synced local memory and the saved connection are kept, so you can resume without reconnecting.")
                .disabled(state.isBusy || isSyncing || isOAuthStarting)
                .confirmationDialog(
                    "Pause \(connector.name) sync?",
                    isPresented: $confirmPause,
                    titleVisibility: .visible
                ) {
                    Button("Pause sync") {
                        state.pauseDirectConnectorSync(connector)
                    }
                    Button("Cancel", role: .cancel) {}
                } message: {
                    Text("New items stop arriving from \(connector.name) until you resume. Synced memory and the saved connection are kept.")
                }
            }

            if let removableImport {
                Button(role: .destructive) {
                    confirmRemove = true
                } label: {
                    Label("Remove", systemImage: "trash")
                        .frame(minWidth: 104, minHeight: 42)
                }
                .buttonStyle(.bordered)
                .help("Disconnect this source and remove the memory it synced.")
                .disabled(state.isBusy || isSyncing || isOAuthStarting)
                .confirmationDialog(
                    "Remove \(connector.name) connection?",
                    isPresented: $confirmRemove,
                    titleVisibility: .visible
                ) {
                    Button("Remove connection", role: .destructive) {
                        state.deleteImport(removableImport)
                    }
                    Button("Cancel", role: .cancel) {}
                } message: {
                    Text("This removes the review items and memory Cortex synced from \(connector.name). Approved memory from this connection is deleted and can only be recovered from a backup. To stop syncing while keeping memory, use Pause instead.")
                }
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
        if hasManagedOAuth && !managedOAuthConfigured { return "Not configured" }
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
        if hasManagedOAuth && !managedOAuthConfigured { return "key.slash.fill" }
        if hasManagedOAuth { return "person.crop.circle.badge.checkmark" }
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
        if hasManagedOAuth && !managedOAuthConfigured { return .secondary }
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
        if hasManagedOAuth {
            if connected { return "\(connector.name) is connected. Cortex keeps new memory local and sends useful items to Review first." }
            if !managedOAuthConfigured {
                return state.managedOAuthConfigurationMessage(connector) ?? "\(connector.name) sign-in is not configured for this build yet."
            }
            return "Sign in with \(managedOAuthProviderName). Cortex stores tokens locally, syncs read-only data, and cites every useful memory."
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
        if isOAuthStarting { return "Waiting" }
        if isPaused { return "Resume" }
        if connected || hasStoredConfig {
            return connector.id == "calendar" ? "Sync" : "Sync again"
        }
        if hasManagedOAuth && !managedOAuthConfigured { return "Not configured" }
        if hasManagedOAuth { return "Sign in" }
        switch connector.id {
        case "calendar": return "Connect"
        case "zotero": return "Sync"
        default: return "Set up"
        }
    }

    private var actionIcon: String {
        if isPaused { return "play.circle.fill" }
        if hasManagedOAuth && !managedOAuthConfigured { return "key.slash.fill" }
        if hasManagedOAuth { return connected ? "arrow.triangle.2.circlepath" : "person.crop.circle.badge.checkmark" }
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
            hasStoredConfig ? state.syncStoredDirectConnector(connector) : state.connectCalendarFile(connector)
        case "zotero":
            state.syncZoteroLocal(connector)
        default:
            if hasManagedOAuth {
                guard managedOAuthConfigured else {
                    state.connectorLastMessages[connector.id] = state.managedOAuthConfigurationMessage(connector)
                    return
                }
                connected ? state.syncConnectedSourceConnector(connector) : state.startManagedOAuthConnector(connector)
            } else if hasStoredConfig {
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
    @State private var discoveredOptions: [String: [SourceConnectorDiscoveredOption]] = [:]
    @State private var discoveryMessages: [String: String] = [:]
    @State private var discoveryLoadingField: String?

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
                    Text("Setup is stored locally on this Mac. Pausing sync keeps already-synced memory and the saved connection, so you can resume without reconnecting.")
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
                .help("Close")
                .accessibilityLabel("Close \(connector.name) setup")
            }
            .padding(20)

            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let setup, !setup.setupSteps.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("How to connect")
                                .font(.caption)
                                .fontWeight(.semibold)
                                .foregroundColor(.secondary)
                            ForEach(Array(setup.setupSteps.enumerated()), id: \.offset) { index, step in
                                HStack(alignment: .top, spacing: 8) {
                                    Text("\(index + 1).")
                                        .font(.callout)
                                        .fontWeight(.semibold)
                                        .foregroundColor(.accentColor)
                                        .monospacedDigit()
                                    Text(step)
                                        .font(.callout)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                            if let url = setup.helpURL {
                                Link(destination: url) {
                                    Label("Open setup help", systemImage: "arrow.up.right.square")
                                        .font(.caption)
                                }
                            }
                        }
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(RoundedRectangle(cornerRadius: 10).fill(Color.accentColor.opacity(0.06)))
                    }
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

            if field.hasRemoteOptions {
                remoteOptionsControls(for: field)
            }
        }
    }

    @ViewBuilder
    private func remoteOptionsControls(for field: SourceConnectorSetupField) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Button {
                    loadRemoteOptions(for: field)
                } label: {
                    if discoveryLoadingField == field.name {
                        ProgressView()
                            .scaleEffect(0.75)
                            .frame(minWidth: 132, minHeight: 40)
                    } else {
                        Label(discoveredOptions[field.name] == nil ? "Find \(field.displayLabel)" : "Refresh \(field.displayLabel)", systemImage: "magnifyingglass")
                            .frame(minHeight: 40)
                    }
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
                .disabled(discoveryLoadingField != nil || missingDiscoveryCredentialMessage(for: field) != nil)

                if let missingDiscoveryCredentialMessage = missingDiscoveryCredentialMessage(for: field) {
                    Text(missingDiscoveryCredentialMessage)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            if let message = discoveryMessages[field.name] {
                Text(message)
                    .font(.caption)
                    .foregroundColor(CortexRecoveryText.needsAttention(message) ? .orange : .secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let options = discoveredOptions[field.name], !options.isEmpty {
                VStack(spacing: 6) {
                    ForEach(options) { option in
                        Button {
                            toggleRemoteOption(option, for: field)
                        } label: {
                            HStack(alignment: .top, spacing: 10) {
                                Image(systemName: remoteOptionSelected(option, for: field) ? "checkmark.circle.fill" : "circle")
                                    .font(.system(size: 18, weight: .semibold))
                                    .foregroundColor(remoteOptionSelected(option, for: field) ? .accentColor : .secondary)
                                    .frame(width: 22, height: 22)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(option.label)
                                        .font(.callout)
                                        .fontWeight(.medium)
                                        .foregroundColor(.primary)
                                    if let detail = option.detail {
                                        Text(detail)
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                            .lineLimit(2)
                                    }
                                }
                                Spacer(minLength: 0)
                            }
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(remoteOptionSelected(option, for: field) ? Color.accentColor.opacity(0.09) : CortexDesign.cardBackground)
                            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.22)))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                        .buttonStyle(.plain)
                    }
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

    private func loadRemoteOptions(for field: SourceConnectorSetupField) {
        discoveryLoadingField = field.name
        discoveryMessages[field.name] = nil
        Task {
            do {
                let options = try await state.discoverDirectConnectorOptions(
                    connector,
                    field: field,
                    payload: syncPayload()
                )
                discoveredOptions[field.name] = options
                if options.isEmpty {
                    discoveryMessages[field.name] = "No \(field.displayLabel.lowercased()) found for this account."
                } else {
                    discoveryMessages[field.name] = "Select the \(field.displayLabel.lowercased()) Cortex should keep synced."
                }
            } catch {
                discoveryMessages[field.name] = CortexRecoveryText.failureStatus("Find \(field.displayLabel.lowercased())", error: error)
            }
            discoveryLoadingField = nil
        }
    }

    private func missingDiscoveryCredentialMessage(for field: SourceConnectorSetupField) -> String? {
        for credential in setup?.credential_fields ?? [] where credential.isRequired {
            if !hasValue(credential) {
                return "Add \(credential.displayLabel) first."
            }
        }
        return nil
    }

    private func remoteOptionSelected(_ option: SourceConnectorDiscoveredOption, for field: SourceConnectorSetupField) -> Bool {
        if field.normalizedKind == "string_list" {
            return Set(splitList(fieldValues[field.name] ?? field.defaultString)).contains(option.value)
        }
        return trimmed(fieldValues[field.name] ?? defaultString(for: field)) == option.value
    }

    private func toggleRemoteOption(_ option: SourceConnectorDiscoveredOption, for field: SourceConnectorSetupField) {
        if field.normalizedKind != "string_list" {
            fieldValues[field.name] = option.value
            return
        }

        var values = Set(splitList(fieldValues[field.name] ?? field.defaultString))
        if values.contains(option.value) {
            values.remove(option.value)
        } else {
            let maxItems = field.max_items ?? Int.max
            guard values.count < maxItems else {
                discoveryMessages[field.name] = "You can select up to \(maxItems) \(field.displayLabel.lowercased())."
                return
            }
            values.insert(option.value)
        }
        fieldValues[field.name] = values.sorted().joined(separator: "\n")
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

private struct ConnectionsMCPAccessSection: View {
    @ObservedObject var state: AppState

    private var settings: AppSettingsResponse {
        state.appSettings
    }

    private var activeMCPTokens: [IntegrationTokenItem] {
        state.integrationTokens.filter { token in
            token.audience == "mcp" && token.revoked_at == nil
        }
    }

    private var recentToolEvents: [AuditEventItem] {
        Array(
            state.auditEvents
                .filter { event in
                    event.object_type == "agent"
                        || event.object_type == "mcp"
                        || event.event_type.lowercased().contains("tool")
                        || event.metadata_text.lowercased().contains("mcp")
                }
                .prefix(3)
        )
    }

    private var activeScopeSummary: String {
        let scopes = Set(activeMCPTokens.flatMap(\.scopes))
        if scopes.isEmpty { return "No AI tool connected" }
        return scopes.sorted().joined(separator: ", ")
    }

    private var lastUsedLabel: String {
        let lastUsed = activeMCPTokens.compactMap(\.last_used_at).sorted().last
        guard let lastUsed else {
            return activeMCPTokens.isEmpty ? "No tool connected" : "Not used yet"
        }
        return "Last used \(shortDate(lastUsed))"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .center, spacing: 12) {
                SectionHeader(
                    title: "Tool permissions",
                    detail: "Connected AI tools can only use the permissions below. Recent activity is shown without raw memory content."
                )
                Spacer(minLength: 12)
                Button {
                    Task {
                        await state.loadTrust()
                        await state.loadIntegrationTokens()
                    }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .controlSize(.large)
            }

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 10)], spacing: 10) {
                ConnectionsTrustTile(
                    title: settings.allow_agent_reads ? "Read" : "Read off",
                    detail: settings.allow_agent_reads ? "reviewed memory" : "blocked",
                    systemImage: settings.allow_agent_reads ? "eye.fill" : "eye.slash.fill",
                    color: settings.allow_agent_reads ? .green : .secondary
                )
                ConnectionsTrustTile(
                    title: settings.allow_agent_writes ? "Save" : "Save off",
                    detail: settings.allow_agent_writes ? "new memory to Review" : "blocked",
                    systemImage: settings.allow_agent_writes ? "square.and.pencil" : "pencil.slash",
                    color: settings.allow_agent_writes ? .accentColor : .secondary
                )
                ConnectionsTrustTile(
                    title: settings.allow_agent_exports ? "Export on" : "Export off",
                    detail: settings.allow_agent_exports ? "redacted exports" : "blocked",
                    systemImage: "square.and.arrow.up",
                    color: settings.allow_agent_exports ? .orange : .secondary
                )
                ConnectionsTrustTile(
                    title: settings.allow_agent_maintenance ? "Maintenance on" : "Maintenance off",
                    detail: settings.allow_agent_destructive_actions ? "delete allowed" : "no deletion",
                    systemImage: settings.allow_agent_maintenance ? "wrench.and.screwdriver.fill" : "wrench.and.screwdriver",
                    color: settings.allow_agent_maintenance ? .orange : .secondary
                )
            }

            HStack(alignment: .center, spacing: 12) {
                Image(systemName: activeMCPTokens.isEmpty ? "key.slash" : "key.fill")
                    .foregroundColor(activeMCPTokens.isEmpty ? .secondary : .accentColor)
                    .frame(width: 28)
                VStack(alignment: .leading, spacing: 3) {
                    Text(activeMCPTokens.isEmpty ? "No AI tool connected yet" : "\(activeMCPTokens.count) active MCP token\(activeMCPTokens.count == 1 ? "" : "s")")
                        .font(.callout)
                        .fontWeight(.semibold)
                    Text("\(activeScopeSummary) · \(lastUsedLabel)")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(2)
                }
                Spacer(minLength: 8)
                Button {
                    Task { await state.resetMCPIntegrationToken() }
                } label: {
                    Label("Reset Token", systemImage: "arrow.triangle.2.circlepath")
                }
                .controlSize(.large)
            }
            .padding(12)
            .background(CortexDesign.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: 8))

            VStack(alignment: .leading, spacing: 8) {
                Text("Recent tool activity")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(.secondary)
                if recentToolEvents.isEmpty {
                    Text("No AI tool activity recorded yet.")
                        .font(.callout)
                        .foregroundColor(.secondary)
                } else {
                    ForEach(recentToolEvents) { event in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Image(systemName: "wand.and.stars")
                                .foregroundColor(.accentColor)
                                .frame(width: 20)
                            Text(event.event_type.replacingOccurrences(of: "_", with: " ").capitalized)
                                .font(.callout)
                                .fontWeight(.medium)
                            Spacer(minLength: 0)
                            Text(shortDate(event.created_at))
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                }
            }
            .padding(12)
            .background(CortexDesign.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .padding(14)
        .background(CortexDesign.cardBackground.opacity(0.55))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.22)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .onAppear {
            Task {
                await state.loadIntegrationTokens()
            }
        }
    }

    private func shortDate(_ value: String) -> String {
        String(value.prefix(10))
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

/// A loading/retry placeholder for the Connections sheet. It shows the normal
/// "Checking…" copy, but surfaces the backend error and a Retry button when the
/// local service has clearly failed, so a crashed backend can be distinguished
/// from ordinary loading instead of spinning indefinitely.
private struct ConnectionsRetryState: View {
    @ObservedObject var state: AppState
    let title: String
    let detail: String
    let retry: () -> Void

    @State private var isRetrying = false

    private var backendFailed: Bool {
        state.backendNeedsRecovery || CortexRecoveryText.needsAttention(state.displayStatus)
    }

    var body: some View {
        VStack(spacing: 10) {
            QuietState(
                title: backendFailed ? "Connections unavailable" : title,
                detail: backendFailed ? state.displayStatus : detail
            )
            retryButton
        }
    }

    @ViewBuilder
    private var retryButton: some View {
        let label = Label(isRetrying ? "Checking…" : "Retry", systemImage: "arrow.clockwise")
            .frame(minWidth: 132, minHeight: 44)
        if backendFailed {
            Button(action: runRetry) { label }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(isRetrying)
        } else {
            Button(action: runRetry) { label }
                .buttonStyle(.bordered)
                .controlSize(.large)
                .disabled(isRetrying)
        }
    }

    private func runRetry() {
        isRetrying = true
        retry()
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 900_000_000)
            isRetrying = false
        }
    }
}
