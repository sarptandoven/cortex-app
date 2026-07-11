import AppKit
import Combine
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
        .frame(minWidth: 560, minHeight: 560)
        .background(connectionsSheetBackground)
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(CortexDesign.accentSoft)
                Image(systemName: "lock.shield")
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundColor(CortexDesign.accent)
            }
            .frame(width: 48, height: 48)

            VStack(alignment: .leading, spacing: 5) {
                Text("Connections & Privacy")
                    .font(CortexDesign.Typography.display(22))
                    .foregroundColor(CortexDesign.ink)
                HStack(spacing: 7) {
                    Circle()
                        .fill(CortexDesign.sealMoss)
                        .frame(width: 7, height: 7)
                    Text("Memory stays on this Mac — you choose what other AI apps can use.")
                        .font(CortexDesign.Typography.body)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .help("Connect notes or ChatGPT/Claude exports, keep memory local, then choose what Claude Desktop, ChatGPT, and other AI tools can use.")
                }
            }

            Spacer(minLength: 0)

            CortexIconButton(systemImage: "xmark", role: .ghost, size: .large, help: "Close") {
                dismiss()
            }
            .accessibilityLabel("Close Connections & Privacy")
        }
        .padding(20)
        .background(connectionsSheetBackground)
    }
}

private struct ConnectionsPrivacyOverview: View {
    @ObservedObject var state: AppState
    // Group-level expansion. Four top-level groups: Sources + AI apps start open,
    // Privacy & data + Advanced start closed. `advancedExpanded` is the AI-apps group
    // (its declaration is pinned by the connector UI contract tests).
    @State private var sourcesExpanded = true
    @State private var advancedExpanded = true
    @State private var privacyDataExpanded = false
    @State private var advancedGroupExpanded = false
    // Sub-disclosures — each at most ONE level deep inside its group.
    @State private var privacySettingsExpanded = false
    @State private var connectedExpanded = false
    @State private var activityMetricsExpanded = false
    @State private var advancedSourcesExpanded = false
    @State private var sourceAuditExpanded = false
    @State private var recoveryToolsExpanded = false
    @State private var developerDetailsExpanded = false
    @State private var storedDataExpanded = false

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

    /// Connector accounts beyond the primary notes source. Chat imports live in import
    /// history, not source accounts, so notes + chat import alone still counts as zero.
    private var extraConnectedSourceCount: Int {
        state.activeSourceAccounts.filter { $0.source != "obsidian" }.count
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
                // The Sources group stays visible at top level, even on first run — a user who
                // doesn't want the local notes folder needs a way to connect any other source to
                // get past onboarding. The other three groups wait until a source is connected.
                sourcesGroup
                if !state.firstRunNeedsSource {
                    aiAppsGroup
                    if let summary = state.trustSummary {
                        privacyDataGroup(summary: summary)
                        advancedGroup(summary: summary)
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
            .padding(.horizontal, 40)
            .padding(.vertical, CortexDesign.Space.xl)
        }
        .background(connectionsSheetBackground)
        .onAppear {
            // First run: the connector library is the discovery surface, so it starts open
            // until an extra source is connected. Expands once per presentation and never
            // auto-collapses while visible.
            if extraConnectedSourceCount == 0 {
                advancedSourcesExpanded = true
            }
        }
        .task {
            await state.loadTrust()
            if state.sourceConnectorCatalog.isEmpty {
                await state.loadSourceConnectivity()
            }
        }
    }

    // MARK: Group 1 — Sources (notes, chat imports, connector library)

    private var sourcesGroup: some View {
        DisclosureGroup(isExpanded: $sourcesExpanded) {
            VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
                ConnectionsObsidianSection(state: state)
                AIChatsImportCard(state: state)
                otherSourceConnections
                if notesHealth.isNeedsAttention
                    || state.sourceAccounts.contains(where: { $0.disconnected_at == nil && $0.needsAttention }) {
                    connectedNow
                }
            }
            .padding(.top, 12)
        } label: {
            ConnectionsDisclosureLabel(
                systemImage: "tray.and.arrow.down",
                title: "Sources",
                detail: sourcesGroupDetail
            )
            .help("Notes, ChatGPT/Claude chat exports, and optional read-only connectors feed your memory.")
        }
        .connectionsGroupCard()
    }

    private var sourcesGroupDetail: String {
        if notesHealth.isNeedsAttention {
            return "A source needs attention"
        }
        return "\(connectedSourceCount) source\(connectedSourceCount == 1 ? "" : "s") connected — notes, chat imports, more"
    }

    // MARK: Group 2 — AI apps (MCP setup, memory packs, tool permissions)

    private var aiAppsGroup: some View {
        DisclosureGroup(isExpanded: $advancedExpanded) {
            VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
                ConnectionsAIToolsSection(state: state)
                ConnectionsMCPAccessSection(state: state)
            }
            .padding(.top, 12)
        } label: {
            ConnectionsDisclosureLabel(
                systemImage: "wand.and.stars",
                title: "AI apps",
                detail: "Use your memory in Claude Desktop, ChatGPT, Cursor & other AI apps"
            )
        }
        .connectionsGroupCard()
    }

    // MARK: Group 3 — Privacy & data (permissions, stored data, backups, export)

    private func privacyDataGroup(summary: TrustSummaryResponse) -> some View {
        DisclosureGroup(isExpanded: $privacyDataExpanded) {
            VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
                ConnectionsPrivacyDefaultsSection(state: state, summary: summary)
                privacySettings(summary: summary)
                storedData
                recoveryTools
            }
            .padding(.top, 12)
        } label: {
            ConnectionsDisclosureLabel(
                systemImage: "lock.shield",
                title: "Privacy & data",
                detail: "Permissions, stored data, backups & export"
            )
        }
        .connectionsGroupCard()
    }

    // MARK: Group 4 — Advanced (metrics, audit history, diagnostics)

    private func advancedGroup(summary: TrustSummaryResponse) -> some View {
        DisclosureGroup(isExpanded: $advancedGroupExpanded) {
            VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
                activityMetrics
                auditHistory(summary: summary)
                developerDetails
            }
            .padding(.top, 12)
        } label: {
            ConnectionsDisclosureLabel(
                systemImage: "wrench.and.screwdriver",
                title: "Advanced",
                detail: "Activity metrics, audit history, developer diagnostics"
            )
        }
        .connectionsGroupCard()
    }

    private var otherSourceConnections: some View {
        DisclosureGroup(isExpanded: $advancedSourcesExpanded) {
            ConnectionsDirectSourcesSection(state: state)
                .padding(.top, 10)
        } label: {
            ConnectionsDisclosureLabel(
                systemImage: "square.grid.2x2",
                title: "Add more sources",
                detail: advancedSourceDisclosureDetail
            )
            .accessibilityLabel("Add more sources — Connections library")
        }
        .connectionsSubCard()
    }

    private var advancedSourceDisclosureDetail: String {
        let extraSources = extraConnectedSourceCount
        if extraSources > 0 {
            return "\(extraSources) extra source\(extraSources == 1 ? "" : "s") connected"
        }
        return "Optional extras — connect any time"
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
        .connectionsSubCard()
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
        .connectionsSubCard()
    }

    /// Manage stored data: once memory exists, removing all of it from a single source is a
    /// first-class privacy action, so it lives in Privacy & data — not buried in diagnostics.
    private var storedData: some View {
        DisclosureGroup(isExpanded: $storedDataExpanded) {
            ConnectionsStoredDataSection(state: state)
                .padding(.top, 10)
        } label: {
            ConnectionsDisclosureLabel(
                systemImage: "tray.full",
                title: "Manage stored data",
                detail: "See what each source has saved, delete it by source in one step"
            )
        }
        .connectionsSubCard()
        .onChange(of: storedDataExpanded) { expanded in
            if expanded {
                Task { await state.loadSourceStats() }
            }
        }
    }

    private var recoveryTools: some View {
        DisclosureGroup(isExpanded: $recoveryToolsExpanded) {
            VStack(alignment: .leading, spacing: 14) {
                SettingsDataRecoverySection(state: state)
                Divider()
                SettingsReliabilitySection(state: state)
                Divider()
                SettingsStatsSection(state: state)
            }
            .padding(.top, 10)
        } label: {
            ConnectionsDisclosureLabel(
                systemImage: "arrow.counterclockwise.circle",
                title: "Backups, recovery & export",
                detail: "Back up, restore, repair, or export your local memory"
            )
        }
        .connectionsSubCard()
        .onChange(of: recoveryToolsExpanded) { expanded in
            if expanded {
                Task {
                    await state.loadDiagnostics()
                    await state.loadReliability()
                    await state.loadStats()
                }
            }
        }
    }

    private var activityMetrics: some View {
        DisclosureGroup(isExpanded: $activityMetricsExpanded) {
            ConnectionsToolUsageSection(state: state)
                .padding(.top, 10)
        } label: {
            ConnectionsDisclosureLabel(
                systemImage: "gauge.with.needle",
                title: "Activity & alerts",
                detail: "How tools use memory, and how often Cortex may interrupt"
            )
        }
        .connectionsSubCard()
    }

    /// Privacy history (per-source trust decisions + the audit log), hoisted out of the old
    /// developer mega-disclosure so nothing in a group nests more than one level deep.
    private func auditHistory(summary: TrustSummaryResponse) -> some View {
        DisclosureGroup(isExpanded: $sourceAuditExpanded) {
            VStack(alignment: .leading, spacing: 14) {
                TrustSourceSection(state: state, summary: summary)
                TrustAuditSection(events: state.auditEvents, refresh: {
                    Task { await state.loadTrust() }
                })
            }
            .padding(.top, 10)
        } label: {
            ConnectionsDisclosureLabel(
                systemImage: "clock.arrow.circlepath",
                title: "Privacy history",
                detail: "Per-source trust decisions and the audit log"
            )
        }
        .connectionsSubCard()
    }

    private var developerDetails: some View {
        DisclosureGroup(isExpanded: $developerDetailsExpanded) {
            VStack(alignment: .leading, spacing: 14) {
                Group {
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
                    Divider()
                }
                Group {
                    TrustSyncManifestSection(state: state)
                    Divider()
                    // App Store builds ship updates through the Mac App Store; in-app
                    // self-update / external executable download is forbidden
                    // (Guideline 2.4.5/2.5.2), so the updates section is direct-mode only.
                    if !DistributionMode.isAppStore {
                        SettingsUpdatesSection(state: state)
                        Divider()
                    }
                    CortexCloudSection(state: state)
                    Divider()
                    SettingsBackendSection(state: state)
                }
            }
            .padding(.top, 10)
        } label: {
            ConnectionsDisclosureLabel(
                systemImage: "wrench.and.screwdriver",
                title: "Developer & diagnostics",
                detail: "Support details, engine status, updates"
            )
        }
        .connectionsSubCard()
        .onChange(of: developerDetailsExpanded) { expanded in
            if expanded {
                Task {
                    await state.loadStats()
                    await state.loadGraph()
                    await state.loadDiagnostics()
                    await state.loadReliability()
                }
            }
        }
    }
}

/// Shared chrome for the Connections sheet's grouping cards. Top-level groups get the panel
/// treatment; sub-disclosures inside a group get the quieter index-card fill so the hierarchy
/// reads at a glance without extra copy.
private extension View {
    func connectionsGroupCard() -> some View {
        padding(14)
            .background(connectionsPanelBackground)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
            .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    func connectionsSubCard() -> some View {
        padding(12)
            .background(CortexDesign.cardBackground)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
            .clipShape(RoundedRectangle(cornerRadius: 8))
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
            return "\(obsidianReadiness.syncPlanDisplayTitle) — synced automatically."
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
            // App Store builds are local-first: outbound HTTPS is stripped, so token- and
            // OAuth-based connectors (Gmail, Slack, Notion, GitHub, …) can't sync and must not
            // show a connect button that can't work. Only local-file / local-API sources remain.
            .filter { !DistributionMode.isAppStore || isLocalFirstConnector($0) }
            .sorted {
                let leftRank = state.directConnectorSortRank($0.id)
                let rightRank = state.directConnectorSortRank($1.id)
                if leftRank != rightRank { return leftRank < rightRank }
                return $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
            }
    }

    /// A connector that reads from the local machine only (a chosen file/export or a
    /// loopback desktop API) and never opens an outbound network connection. In App Store
    /// (local-first) builds these are the only sources that can work.
    private func isLocalFirstConnector(_ connector: SourceConnectorCatalogItem) -> Bool {
        connector.connectionSetup?.mode == "native-local-connector"
    }

    /// Managed-OAuth connectors without provider credentials in this build can't be connected,
    /// so untouched ones don't get a tile at all — a hidden connector beats a dead end.
    /// Connectors the user already set up keep their management row regardless.
    private var browsableConnectors: [SourceConnectorCatalogItem] {
        wiredConnectors.filter { isManaged($0) || !isUnconfiguredOAuth($0) }
    }

    /// The unconfigured-OAuth connectors that are genuinely hidden from the library — those with
    /// no token fallback to fall through to. Notion (managed OAuth advertised but client id empty,
    /// plus a durable integration token) is NOT here: it browses as a token connector instead.
    private var hiddenOAuthConnectors: [SourceConnectorCatalogItem] {
        wiredConnectors.filter { !isManaged($0) && isUnconfiguredOAuth($0) }
    }

    /// Names exactly the connectors actually hidden this build — no more hardcoded "Email and
    /// Drive" that silently dropped Notion/Outlook. Points people at file/export import instead.
    private var hiddenOAuthFootnote: String? {
        let names = hiddenOAuthConnectors
            .map(\.name)
            .sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
        guard !names.isEmpty else { return nil }
        let list: String
        switch names.count {
        case 1:
            list = names[0]
        case 2:
            list = "\(names[0]) and \(names[1])"
        default:
            list = names.dropLast().joined(separator: ", ") + ", and \(names[names.count - 1])"
        }
        let verb = names.count == 1 ? "isn't" : "aren't"
        return "\(list) \(verb) available for direct sign-in in this build yet — import them via file/export import."
    }

    // Library search + category grouping (VSCode-extensions style: searchable, sectioned list of
    // optional connections). Reuses the existing, correct ConnectionsDirectSourceRow per connector.
    private var filteredConnectors: [SourceConnectorCatalogItem] {
        let query = connectorSearch.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !query.isEmpty else { return browsableConnectors }
        return browsableConnectors.filter {
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

    // Single summarizer for planned sign-in connectors. No longer rendered as a roadmap
    // footer — unavailable connectors simply aren't listed — but kept as the one place
    // that names the planned sign-in set.
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
                title: "Connections library",
                detail: "All optional."
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
                    Image(systemName: "magnifyingglass").foregroundColor(CortexDesign.inkSecondary)
                    TextField("Search connections", text: $connectorSearch)
                        .textFieldStyle(.plain)
                    if !connectorSearch.isEmpty {
                        CortexIconButton(systemImage: "xmark.circle.fill", role: .ghost, size: .small, help: "Clear search") {
                            connectorSearch = ""
                        }
                    }
                }
                .padding(8)
                .background(RoundedRectangle(cornerRadius: 8).fill(CortexDesign.quietBackground))

                if filteredConnectors.isEmpty {
                    QuietState(title: "No matching connections", detail: "Try a different search term.")
                } else {
                    ForEach(connectorsByCategory, id: \.category) { group in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(group.category.uppercased())
                                .font(CortexDesign.Typography.stamp)
                                .kerning(0.8)
                                .foregroundColor(CortexDesign.inkFaint)
                            // Full management rows only for connectors the user has actually
                            // touched; everything else browses as a compact app-store shelf.
                            let managedConnectors = group.connectors.filter { isManaged($0) }
                            let availableConnectors = group.connectors.filter { !isManaged($0) }
                            ForEach(managedConnectors) { connector in
                                ConnectionsDirectSourceRow(
                                    state: state,
                                    connector: connector,
                                    connected: isConnected(connector),
                                    openTokenSetup: {
                                        selectedTokenConnector = connector
                                    }
                                )
                            }
                            if !availableConnectors.isEmpty {
                                LazyVGrid(columns: [GridItem(.adaptive(minimum: 210), spacing: 10)], spacing: 10) {
                                    ForEach(availableConnectors) { connector in
                                        ConnectorLibraryTile(
                                            connector: connector,
                                            cta: connector.connectionSetup?.supportsDeviceFlow == true ? "Sign in" : "Set up"
                                        ) {
                                            libraryAction(connector)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            if let hiddenOAuthFootnote {
                Text(hiddenOAuthFootnote)
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkFaint)
            }
        }
        .sheet(item: $selectedTokenConnector) { connector in
            ConnectorTokenSetupSheet(state: state, connector: connector)
                .frame(width: 640, height: 680)
        }
        .sheet(item: Binding(get: { state.githubDeviceFlow }, set: { state.githubDeviceFlow = $0 })) { prompt in
            GitHubDeviceCodeView(state: state, prompt: prompt)
                .frame(width: 460, height: 440)
        }
    }

    private func isConnected(_ connector: SourceConnectorCatalogItem) -> Bool {
        state.activeSourceAccounts.contains { account in
            account.source == connector.id || (connector.source_ids ?? []).contains(account.source)
        }
    }

    /// Connected, paused, or configured connectors keep the full management row;
    /// untouched connectors render as compact library tiles instead.
    private func isManaged(_ connector: SourceConnectorCatalogItem) -> Bool {
        isConnected(connector)
            || state.disconnectedSourceAccount(connector) != nil
            || state.hasStoredDirectConnectorConfig(connector)
    }

    /// Managed-OAuth connector whose provider sign-in has no credentials in this build AND that
    /// has no durable token fallback to fall through to. Notion advertises managed OAuth whose
    /// client id ships empty, but it also carries an integration-token setup — so it is NOT
    /// "unconfigured" here: it browses and connects via the token sheet like Slack/Linear.
    private func isUnconfiguredOAuth(_ connector: SourceConnectorCatalogItem) -> Bool {
        connector.connectionSetup?.supportsManagedOAuth == true
            && !state.managedOAuthIsConfigured(connector)
            && !hasUsableTokenFallback(connector)
    }

    /// A connector reachable via a pasted integration token even when managed OAuth isn't wired up
    /// in this build — it advertises credential fields (e.g. Notion's internal integration token).
    private func hasUsableTokenFallback(_ connector: SourceConnectorCatalogItem) -> Bool {
        !(connector.connectionSetup?.credential_fields.isEmpty ?? true)
    }

    private func libraryAction(_ connector: SourceConnectorCatalogItem) {
        // Secretless "Sign in with GitHub" (device flow) is the primary path when advertised; the
        // pasted-token setup remains reachable from the connected row as an advanced fallback.
        if connector.connectionSetup?.supportsDeviceFlow == true {
            state.startGitHubDeviceFlow(connector)
            return
        }
        if connector.connectionSetup?.supportsManagedOAuth == true {
            if state.managedOAuthIsConfigured(connector) {
                state.startManagedOAuthConnector(connector)
                return
            }
            // Managed OAuth is advertised but not wired up in this build. If the connector also
            // ships a durable integration-token setup (Notion), don't dead-end — fall through to
            // the token sheet, the same path token-only connectors use. Only stop here when there
            // is genuinely nothing to connect with.
            if !hasUsableTokenFallback(connector) {
                return
            }
            selectedTokenConnector = connector
            return
        }
        switch connector.id {
        case "calendar":
            state.connectCalendarFile(connector)
        case "zotero":
            state.syncZoteroLocal(connector)
        default:
            selectedTokenConnector = connector
        }
    }
}

/// Compact, hoverable "app store" tile for a connector the user hasn't set up yet.
/// Only connectable connectors get a tile — dead "Coming soon" tiles are hidden upstream.
/// Management chrome (Sync / Pause / Remove) only appears once a connector is connected.
private struct ConnectorLibraryTile: View {
    let connector: SourceConnectorCatalogItem
    var cta: String = "Set up"
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 8) {
                Image(systemName: connectorLibraryIcon(connector.id))
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundColor(CortexDesign.accent)
                Text(connector.name)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundColor(CortexDesign.ink)
                Text(connector.category ?? "Read-only sync")
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                Spacer(minLength: 0)
                Text(cta)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(CortexDesign.accent)
            }
            .padding(14)
            .frame(maxWidth: .infinity, minHeight: 118, alignment: .topLeading)
            .background(hovering ? CortexDesign.accentSoft : CortexDesign.panelBackground)
            .overlay(RoundedRectangle(cornerRadius: CortexDesign.Radius.md).stroke(CortexDesign.hairline, lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md))
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .animation(.easeOut(duration: 0.12), value: hovering)
        .help("Connect \(connector.name)")
    }
}

/// Shared connector glyphs for the library tiles and the management rows.
private func connectorLibraryIcon(_ id: String) -> String {
    switch id {
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

/// One numbered walkthrough row: a serif numeral in a small circle, full ink while current,
/// faint otherwise. Clicking selects the step. The highlight is purely visual — it never
/// moves keyboard or VoiceOver focus.
private struct GuidedStepRow: View {
    let number: Int
    let text: String
    let font: Font
    let isCurrent: Bool
    let select: () -> Void

    var body: some View {
        Button(action: select) {
            HStack(alignment: .top, spacing: 10) {
                ZStack {
                    Circle()
                        .fill(isCurrent ? CortexDesign.accent : Color.clear)
                    Circle()
                        .stroke(isCurrent ? CortexDesign.accent : CortexDesign.inkFaint, lineWidth: 1)
                    Text("\(number)")
                        .font(.system(size: 10, weight: .semibold, design: .serif))
                        .foregroundColor(isCurrent ? CortexDesign.panelBackground : CortexDesign.inkFaint)
                }
                .frame(width: 18, height: 18)
                .padding(.top, 1)
                Text(text)
                    .font(font)
                    .foregroundColor(isCurrent ? CortexDesign.ink : CortexDesign.inkFaint)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 0)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Step \(number): \(text)")
    }
}

/// Shared guided walkthrough for external-step instructions (chat exports, token setup).
/// The current-step highlight auto-advances on a gentle ~2.5s loop while `isActive` and the
/// view is on screen; clicking any row jumps the highlight there. Every step renders — some
/// connectors (Slack) send more than three.
private struct GuidedStepWalkthrough<StepFootnote: View>: View {
    let steps: [String]
    let isActive: Bool
    let textFont: Font
    private let stepFootnote: (Int) -> StepFootnote

    @State private var currentStep = 0
    @State private var ticker = makeGuidedStepTicker()

    init(
        steps: [String],
        isActive: Bool = true,
        textFont: Font = .callout,
        @ViewBuilder stepFootnote: @escaping (Int) -> StepFootnote
    ) {
        self.steps = steps
        self.isActive = isActive
        self.textFont = textFont
        self.stepFootnote = stepFootnote
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                VStack(alignment: .leading, spacing: 5) {
                    GuidedStepRow(
                        number: index + 1,
                        text: step,
                        font: textFont,
                        isCurrent: index == currentStep
                    ) {
                        withAnimation(.easeInOut(duration: 0.25)) {
                            currentStep = index
                        }
                        restartTicker()
                    }
                    stepFootnote(index)
                        .padding(.leading, 28)
                }
            }
        }
        .onReceive(ticker) { _ in
            guard isActive, steps.count > 1 else { return }
            withAnimation(.easeInOut(duration: 0.35)) {
                currentStep = (currentStep + 1) % steps.count
            }
        }
        .onChange(of: isActive) { active in
            if active {
                restartTicker()
            } else {
                stopTicker()
            }
        }
        .onAppear {
            if isActive {
                restartTicker()
            }
        }
        .onDisappear {
            stopTicker()
        }
    }

    private func restartTicker() {
        stopTicker()
        ticker = makeGuidedStepTicker()
    }

    private func stopTicker() {
        ticker.upstream.connect().cancel()
    }
}

/// ~2.5s per step: slow enough to read, quick enough to feel alive.
private func makeGuidedStepTicker() -> Publishers.Autoconnect<Timer.TimerPublisher> {
    Timer.publish(every: 2.5, on: .main, in: .common).autoconnect()
}

/// Import your ChatGPT / Claude history. There's no live sign-in for these (the providers don't
/// offer one), so this card guides the export, auto-detects it in Downloads, and takes a
/// drag-drop or file pick. Imported content is trusted and usable immediately.
private struct AIChatsImportCard: View {
    @ObservedObject var state: AppState
    @State private var isTargeted = false
    @State private var dropZoneHovering = false
    // The export walkthrough starts open until an export shows up — requesting the export is
    // where people stall, not the drop zone. Once one is detected or imported, it tucks away.
    @State private var guideExpanded = true

    private let steps = [
        "In ChatGPT: Settings → Data controls → Export data. In Claude: Settings → Privacy → Export data.",
        "The export arrives by email after a short wait — watch your inbox for the download link.",
        "Download the .zip, then drop it above or click Choose export file."
    ]

    private var hasCompletedImport: Bool {
        state.importHistory.contains { $0.deleted_at == nil && $0.saved > 0 }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Image(systemName: "bubble.left.and.text.bubble.right")
                    .font(.title3)
                    .foregroundColor(CortexDesign.accent)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Import your ChatGPT or Claude chats")
                        .font(.headline)
                        .foregroundColor(CortexDesign.ink)
                    Text("Drop your export file here — it's ready to use right away.")
                        .font(.caption).foregroundColor(CortexDesign.inkSecondary).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }

            if let summary = state.detectedExportSummary {
                HStack(spacing: 8) {
                    Image(systemName: "sparkles").foregroundColor(CortexDesign.accent)
                    Text(summary).font(.callout).foregroundColor(CortexDesign.ink).fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 8)
                    CortexButton(title: "Import", systemImage: "square.and.arrow.down", role: .secondary, size: .small) {
                        state.importDetectedExports()
                    }
                    .disabled(state.importInFlight)
                }
                .padding(10)
                .background(RoundedRectangle(cornerRadius: 8).fill(CortexDesign.accentSoft))
            }

            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6]))
                .foregroundColor(isTargeted || dropZoneHovering ? CortexDesign.accent : CortexDesign.hairline)
                .frame(height: 66)
                .overlay(
                    HStack(spacing: 8) {
                        if state.importInFlight { ProgressView().scaleEffect(0.7) }
                        Text(state.importInFlight ? "Importing…" : "Drag your export here, or")
                            .font(.callout).foregroundColor(CortexDesign.inkSecondary)
                        if !state.importInFlight {
                            CortexButton(title: "Choose export file…", systemImage: "folder.badge.plus", role: .secondary, size: .small) {
                                state.importAIChatExport()
                            }
                        }
                    }
                )
                .onHover { dropZoneHovering = $0 }
                .animation(.easeOut(duration: 0.12), value: dropZoneHovering)
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

            DisclosureGroup(isExpanded: $guideExpanded) {
                // Guided walkthrough: the highlight strolls through the steps on a loop while
                // the disclosure is open, and clicking a step jumps it there. The one-click
                // export links sit with step 1 so the first action is obvious.
                GuidedStepWalkthrough(steps: steps, isActive: guideExpanded, textFont: .caption) { index in
                    if index == 0 {
                        HStack(spacing: 16) {
                            exportSettingsLink("Open ChatGPT export settings", urlString: "https://chatgpt.com/#settings/DataControls")
                            exportSettingsLink("Open Claude export settings", urlString: "https://claude.ai/settings/data-privacy-controls")
                        }
                    }
                }
                .padding(.top, 6)
            } label: {
                Text("How do I get my export?")
            }
            .font(.caption)
            .foregroundColor(CortexDesign.inkSecondary)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: CortexDesign.Radius.md).fill(connectionsPanelBackground))
        .overlay(RoundedRectangle(cornerRadius: CortexDesign.Radius.md).stroke(CortexDesign.hairline, lineWidth: 1))
        .onAppear {
            Task {
                // The detector scans Downloads/Desktop/~/CortexImports, and on first run macOS
                // fires one TCC permission prompt PER folder — opening Connections used to greet
                // the user with a cascade of "Cortex would like to access…" dialogs before they
                // asked for anything. Only scan silently once the user has already granted the
                // folders (a prior import) — otherwise wait for an explicit import action, whose
                // prompt is then expected and in context.
                if hasCompletedImport {
                    await state.detectAvailableExports()
                }
                // Collapse the walkthrough only once an export exists or chats have landed;
                // otherwise it stays open so the path in is visible without a click.
                if state.detectedExportSummary != nil || hasCompletedImport {
                    guideExpanded = false
                }
            }
        }
    }

    /// Quiet mono link straight to the provider's export page — the step users abandon.
    private func exportSettingsLink(_ title: String, urlString: String) -> some View {
        Button {
            if let url = URL(string: urlString) {
                NSWorkspace.shared.open(url)
            }
        } label: {
            HStack(spacing: 4) {
                Text(title)
                Image(systemName: "arrow.up.right")
                    .font(.system(size: 9, weight: .semibold))
            }
            .font(CortexDesign.Typography.stamp)
            .foregroundColor(CortexDesign.inkSecondary)
            .underline()
        }
        .buttonStyle(.plain)
    }
}

private struct ConnectionsDirectSourceRow: View {
    @ObservedObject var state: AppState
    let connector: SourceConnectorCatalogItem
    let connected: Bool
    let openTokenSetup: () -> Void
    @State private var confirmRemove = false
    @State private var confirmPause = false
    @State private var hovering = false

    private var isSyncing: Bool {
        state.connectorSyncingIDs.contains(connector.id)
    }

    private var isOAuthStarting: Bool {
        state.connectorOAuthStartingIDs.contains(connector.id)
    }

    /// A connector that advertises managed OAuth we can't use in this build (client id ships
    /// empty) but that also carries a durable integration-token setup (Notion). When true, this
    /// row behaves as a plain token connector — the OAuth "Coming soon" gating never applies and
    /// the connect button opens the token sheet — matching the library tile's fallback route.
    private var usesTokenFallback: Bool {
        connector.connectionSetup?.supportsManagedOAuth == true
            && !connected
            && !state.managedOAuthIsConfigured(connector)
            && !(connector.connectionSetup?.credential_fields.isEmpty ?? true)
    }

    private var hasManagedOAuth: Bool {
        connector.connectionSetup?.supportsManagedOAuth == true && !usesTokenFallback
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
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(connector.name)
                        .font(.headline)
                        .foregroundColor(CortexDesign.ink)
                        .lineLimit(1)   // truncate a long name rather than pushing the trailing action buttons
                    // Quiet catalog stamp instead of a colored capsule: state is words and ink.
                    Text(statusTitle.uppercased())
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.8)
                        .foregroundColor(
                            activeAccount?.needsAttention == true
                                ? CortexDesign.accent
                                : CortexDesign.inkFaint
                        )
                        .lineLimit(1)
                }
                Text(detail)
                    .font(.callout)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let lastMessage = state.connectorLastMessages[connector.id] {
                    Text(lastMessage)
                        .font(.caption)
                        .foregroundColor(CortexRecoveryText.needsAttention(lastMessage) ? CortexDesign.accent : CortexDesign.inkSecondary)
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
                // Sign-in for this service isn't enabled in this build yet (Cortex hasn't shipped the
                // OAuth client for it). This is a Cortex rollout gap, NOT something the user can fix —
                // so say "Coming soon," never "Setup required" (which wrongly implies user action).
                Label("Coming soon", systemImage: "clock")
                    .font(.callout)
                    .fontWeight(.medium)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .frame(minWidth: 126, minHeight: 46)
                    .padding(.horizontal, 12)
                    .background(CortexDesign.quietBackground)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .help("One-tap sign-in for \(connector.name) is coming soon.")
                    .accessibilityLabel("\(connector.name): one-tap sign-in coming soon")
            } else if isSyncing || isOAuthStarting {
                ProgressView()
                    .scaleEffect(0.78)
                    .frame(minWidth: 126, minHeight: 46)
            } else {
                CortexButton(title: actionTitle, systemImage: actionIcon, role: .secondary, size: .regular) {
                    runAction()
                }
                .disabled(state.isBusy || isSyncing || isOAuthStarting)
            }

            if !isPaused && (activeAccount != nil || hasStoredConfig) {
                CortexButton(title: "Pause", systemImage: "pause.circle", role: .ghost, size: .regular) {
                    confirmPause = true
                }
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
                CortexButton(title: "Remove", systemImage: "trash", role: .destructive, size: .regular) {
                    confirmRemove = true
                }
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
        .padding(.vertical, 14)
        .padding(.trailing, 14)
        .padding(.leading, connected ? 25 : 14)
        .background(hovering ? CortexDesign.accentSoft.opacity(0.5) : connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        // Kept and recorded: connected sources carry the wax-red margin spine.
        .archiveSpine(connected ? CortexDesign.accent : Color.clear)
        .onHover { hovering = $0 }
        .animation(.easeOut(duration: 0.12), value: hovering)
    }

    private var statusTitle: String {
        if isPaused { return "Paused" }
        if activeAccount?.needsAttention == true { return "Needs attention" }
        if let readiness, readiness.pending > 0 { return "Review" }
        if let readiness { return readiness.syncPlanModeTitle }
        if connected { return "Connected" }
        if hasStoredConfig { return "Configured" }
        if hasManagedOAuth && !managedOAuthConfigured { return "Coming soon" }
        if let setupModeTitle { return setupModeTitle }
        return "Ready"
    }

    private var statusColor: Color {
        if isPaused { return CortexDesign.inkSecondary }
        if activeAccount?.needsAttention == true { return CortexDesign.accent }
        // Pending review is a gold moment — used only for the large glyph and its soft fill.
        if let readiness, readiness.pending > 0 { return CortexDesign.gold }
        if let readiness { return readiness.syncPlanColor }
        if connected { return CortexDesign.sealMoss }
        if hasStoredConfig { return CortexDesign.sealMoss }
        if hasManagedOAuth && !managedOAuthConfigured { return CortexDesign.inkSecondary }
        if connector.connectionSetup?.available == true { return CortexDesign.accent }
        return CortexDesign.inkSecondary
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
        connectorLibraryIcon(connector.id)
    }

    private var detail: String {
        if isPaused {
            return "Paused. Already synced local memory stays available."
        }
        if activeAccount?.needsAttention == true {
            return activeAccount?.last_error ?? "This connector needs attention before it can sync again."
        }
        if hasManagedOAuth {
            if connected { return "New items go to Review first." }
            if !managedOAuthConfigured {
                return state.managedOAuthConfigurationMessage(connector) ?? "\(connector.name) sign-in is not configured for this build yet."
            }
            return "Sign in with \(managedOAuthProviderName) — read-only."
        }
        switch connector.id {
        case "calendar":
            if connected { return "Calendar events are available for Review and cited Ask." }
            if hasStoredConfig { return "Configured. Sync again for fresh events." }
            return "Connect a read-only calendar export or feed."
        case "zotero":
            return connected ? "Zotero research is available for Review and cited Ask." : "Sync from the Zotero desktop local API when Zotero is running."
        default:
            if connected { return "Run sync again for fresh items." }
            if hasStoredConfig { return "Configured. Run sync again for fresh items." }
            return connector.connectionSetup?.mode == "native-token-connector"
                ? "Connect with a read-only token."
                : "Synced items go to Review first."
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
            return autosyncLabel
        }
        if let lastSeen = readiness.last_seen_at {
            return "\(autosyncLabel) · Last sync \(shortTimestamp(lastSeen))"
        }
        return autosyncLabel
    }

    private var sourceHealthColor: Color {
        // Gold stays a fill; pending emphasis in running text is full ink instead.
        if let readiness, readiness.pending > 0 { return CortexDesign.ink }
        return CortexDesign.inkSecondary
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
        if hasManagedOAuth && !managedOAuthConfigured { return "Coming soon" }
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
                        .fill(CortexDesign.accentSoft)
                    Image(systemName: "key.fill")
                        .font(.system(size: 22, weight: .semibold))
                        .foregroundColor(CortexDesign.accent)
                }
                .frame(width: 46, height: 46)

                VStack(alignment: .leading, spacing: 4) {
                    Text("Connect \(connector.name)")
                        .font(CortexDesign.Typography.display(22))
                        .foregroundColor(CortexDesign.ink)
                    Text(headerDetail)
                        .font(.callout)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    // The moss mark: the local-only promise carries the privacy signature.
                    HStack(alignment: .top, spacing: 7) {
                        Circle()
                            .fill(CortexDesign.sealMoss)
                            .frame(width: 7, height: 7)
                            .padding(.top, 3)
                        Text("Setup is stored locally on this Mac. Pausing sync keeps already-synced memory and the saved connection, so you can resume without reconnecting.")
                            .font(.caption)
                            .foregroundColor(CortexDesign.inkSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                Spacer(minLength: 0)

                CortexIconButton(systemImage: "xmark", role: .ghost, size: .regular, help: "Close") {
                    dismiss()
                }
                .accessibilityLabel("Close \(connector.name) setup")
            }
            .padding(20)

            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let setup, !setup.setupSteps.isEmpty || setup.helpURL != nil {
                        VStack(alignment: .leading, spacing: 10) {
                            Text("How to connect")
                                .font(.caption)
                                .fontWeight(.semibold)
                                .foregroundColor(CortexDesign.inkSecondary)
                            // Every backend step renders (Slack sends more than three) as a
                            // numbered checklist so "what token? where from?" is answered right
                            // above the credential field.
                            if !setup.setupSteps.isEmpty {
                                GuidedStepWalkthrough(steps: setup.setupSteps) { _ in
                                    EmptyView()
                                }
                            }
                            // Persistent "Open setup page" link whenever the backend supplies one —
                            // it stays put instead of riding along a single auto-advancing step, so
                            // the "where do I get this?" jump is always one click away.
                            if let url = setup.helpURL {
                                Link(destination: url) {
                                    Label("Open setup page", systemImage: "arrow.up.right.square")
                                        .font(.caption)
                                        .fontWeight(.medium)
                                        .foregroundColor(CortexDesign.accent)
                                }
                            }
                        }
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(RoundedRectangle(cornerRadius: CortexDesign.Radius.md).fill(CortexDesign.accentSoft))
                    }
                    if setup == nil {
                        QuietState(title: "Setup contract unavailable", detail: "Update Cortex and try this connection again.")
                    } else {
                        if !requiredFields.isEmpty {
                            VStack(alignment: .leading, spacing: 12) {
                                Text("Required")
                                    .font(.caption)
                                    .fontWeight(.semibold)
                                    .foregroundColor(CortexDesign.inkSecondary)
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
                            .foregroundColor(CortexDesign.accent)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(20)
            }

            Divider()

            HStack(spacing: 10) {
                CortexButton(title: "Cancel", role: .ghost, size: .large) {
                    dismiss()
                }

                Spacer()

                // This modal's one main action — the sheet-wide "no global primary" rule applies
                // to the Connections overview, not to a focused setup dialog.
                CortexButton(title: "Sync \(connector.name)", systemImage: "arrow.triangle.2.circlepath", role: .primary, size: .large) {
                    sync()
                }
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
                .foregroundColor(CortexDesign.inkSecondary)

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
                        CortexButton(
                            title: "Choose",
                            systemImage: field.normalizedKind == "local_folder" ? "folder" : "doc",
                            role: .secondary,
                            size: .regular
                        ) {
                            chooseLocalPath(for: field)
                        }
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
                if discoveryLoadingField == field.name {
                    ProgressView()
                        .scaleEffect(0.75)
                        .frame(minWidth: 132, minHeight: 40)
                } else {
                    CortexButton(
                        title: discoveredOptions[field.name] == nil ? "Find \(field.displayLabel)" : "Refresh \(field.displayLabel)",
                        systemImage: "magnifyingglass",
                        role: .secondary,
                        size: .regular
                    ) {
                        loadRemoteOptions(for: field)
                    }
                    .disabled(discoveryLoadingField != nil || missingDiscoveryCredentialMessage(for: field) != nil)
                }

                if let missingDiscoveryCredentialMessage = missingDiscoveryCredentialMessage(for: field) {
                    Text(missingDiscoveryCredentialMessage)
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            if let message = discoveryMessages[field.name] {
                Text(message)
                    .font(.caption)
                    .foregroundColor(CortexRecoveryText.needsAttention(message) ? CortexDesign.accent : CortexDesign.inkSecondary)
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
                                    .foregroundColor(remoteOptionSelected(option, for: field) ? CortexDesign.accent : CortexDesign.inkSecondary)
                                    .frame(width: 22, height: 22)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(option.label)
                                        .font(.callout)
                                        .fontWeight(.medium)
                                        .foregroundColor(CortexDesign.ink)
                                    if let detail = option.detail {
                                        Text(detail)
                                            .font(.caption)
                                            .foregroundColor(CortexDesign.inkSecondary)
                                            .lineLimit(2)
                                    }
                                }
                                Spacer(minLength: 0)
                            }
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(remoteOptionSelected(option, for: field) ? CortexDesign.accentSoft : CortexDesign.cardBackground)
                            .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
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
    /// Local "Test connection" results, keyed by integration id. Set from testToolConnection.
    @State private var testResults: [String: ConnectionTestResult] = [:]
    /// Memory-pack preview disclosure state for the browser-assistant row.
    @State private var packPreviewExpanded = false
    /// Presents the guided "Connect an app" wizard (pick → connect → verify → done). Additive
    /// front door over the per-tool tiles below — the same catalog, primitives, and honesty gates.
    @State private var showConnectWizard = false

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
                title: "AI tools & permissions",
                detail: "Optional. Connected apps read reviewed memory with citations."
            )
            .help("Claude Desktop and other MCP apps can read reviewed memory with citations. ChatGPT or Claude web chats should be imported as exports until direct browser memory support ships.")

            connectWizardEntry

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
                    Text("AI apps".uppercased())
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.8)
                        .foregroundColor(CortexDesign.inkFaint)
                    Text(statusTitle)
                        .font(.title3)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.ink)
                    Text(statusDetail)
                        .font(.callout)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Spacer(minLength: 8)

                if !detectedConnectable.isEmpty, !DistributionMode.isAppStore {
                    // Automatic config-file install only works outside the App Store sandbox; in
                    // App Store builds installDetectedIntegrations() is a no-op, so showing this
                    // prominent button there was a dead end. Those builds get the copy-guide path
                    // below instead.
                    CortexButton(title: "Enable in apps", systemImage: "link.circle", role: .secondary, size: .large) {
                        state.installDetectedIntegrations()
                    }
                } else if !detectedConnectable.isEmpty {
                    CortexButton(title: "Copy setup config", systemImage: "doc.on.doc", role: .secondary, size: .large) {
                        state.copyMCPConfig()
                    }
                    .help("Copies the tool configuration to paste into your AI app's settings.")
                } else {
                    CortexButton(title: "Copy tool config", systemImage: "doc.on.doc", role: .secondary, size: .large) {
                        state.copyMCPConfig()
                    }
                    .help("Copies the Cortex MCP configuration to paste into Claude Desktop or another compatible tool.")
                }
            }
            .padding(14)
            .background(connectionsPanelBackground)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            if DistributionMode.isAppStore {
                // Local-first App Store builds can't write into other apps' config files
                // (sandbox) and don't auto-install. Instead of a dead end, guide the exact
                // manual paste: the connection JSON in a copyable block plus numbered steps.
                ConnectionsGuidedMCPSetup(state: state)
            }

            browserAssistantRow

            universalReachRow

            if !connectedIntegrations.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(connectedIntegrations.prefix(3)) { integration in
                        connectedIntegrationRow(integration)
                    }
                }
            }
        }
        .sheet(isPresented: $showConnectWizard) {
            ConnectAppWizard(state: state)
        }
    }

    /// The guided "Connect an app" front door: one wax-red primary that opens the step-by-step
    /// wizard (pick → connect → verify → done). Additive over the per-tool tiles below — it does
    /// not replace them, it's just the easier entry point for a non-technical user.
    private var connectWizardEntry: some View {
        HStack(alignment: .center, spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(CortexDesign.accent.opacity(0.13))
                Image(systemName: "wand.and.stars")
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundColor(CortexDesign.accent)
            }
            .frame(width: 56, height: 56)

            VStack(alignment: .leading, spacing: 4) {
                Text("Guided setup".uppercased())
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.inkFaint)
                Text("Connect an AI app in a few steps")
                    .font(.title3)
                    .fontWeight(.semibold)
                    .foregroundColor(CortexDesign.ink)
                Text("Pick a tool, copy its connection, and verify it works — no config files to hunt for.")
                    .font(.callout)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 8)

            CortexButton(title: "Connect an app", systemImage: "wand.and.stars", role: .primary, size: .large) {
                showConnectWizard = true
            }
            .help("Opens a step-by-step wizard: pick a tool, copy its connection, and test that it can reach your memory.")
        }
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.accent.opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    /// The browser extension + universal API paths, previously reachable ONLY from the menu-bar
    /// right-click menu — invisible to anyone who never right-clicks the status item. Surfacing
    /// them here puts every "use Cortex anywhere" path on the same screen as MCP + memory packs.
    private var universalReachRow: some View {
        HStack(alignment: .center, spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(CortexDesign.sealMoss.opacity(0.13))
                Image(systemName: "puzzlepiece.extension")
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundColor(CortexDesign.sealMoss)
            }
            .frame(width: 56, height: 56)

            VStack(alignment: .leading, spacing: 4) {
                Text("Browser extension & any other app".uppercased())
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.inkFaint)
                Text("Use your memory anywhere")
                    .font(.title3)
                    .fontWeight(.semibold)
                    .foregroundColor(CortexDesign.ink)
                Text("Pair the browser extension for one-click context on chat sites, or copy API details for SDKs and self-hosted tools.")
                    .font(.callout)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 8)

            VStack(alignment: .trailing, spacing: 8) {
                CortexButton(title: "Connect extension", systemImage: "puzzlepiece.extension", role: .secondary, size: .small) {
                    state.pairBrowserExtension()
                }
                .help("Mints a read-only pairing token and copies it for the Cortex browser extension.")

                CortexButton(title: "Copy API details", systemImage: "curlybraces", role: .secondary, size: .small) {
                    state.copyUniversalAPIConnectionInfo()
                }
                .help("Copies the base URL, token, and tool-schema endpoints for SDKs and any function-calling app.")
            }
        }
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    /// A connected-tool row with an inline "Test" button. Testing confirms the tool can actually
    /// reach Cortex memory (not just that a config file exists), and shows the result in place:
    /// a moss check + message on success, an amber warning + message on failure.
    @ViewBuilder
    private func connectedIntegrationRow(_ integration: AIIntegration) -> some View {
        let result = testResults[integration.id]
        let isTesting = state.testingConnectionID == integration.id
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(CortexDesign.sealMoss)
                    .frame(width: 24)
                Text(integration.name)
                    .font(.callout)
                    .fontWeight(.medium)
                    .foregroundColor(CortexDesign.ink)
                Spacer(minLength: 8)
                if isTesting {
                    ProgressView()
                        .scaleEffect(0.7)
                        .frame(minWidth: 78, minHeight: 30)
                } else {
                    CortexButton(title: "Test", systemImage: "checklist", role: .ghost, size: .small) {
                        runTest(integration)
                    }
                    .disabled(state.testingConnectionID != nil)
                    .help("Check that \(integration.name) can reach your reviewed memory.")
                }
                Text("Enabled")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(CortexDesign.sealMoss)
            }

            if let result {
                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: result.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(result.ok ? CortexDesign.sealMoss : CortexDesign.gold)
                    Text(result.message)
                        .font(.caption)
                        .foregroundColor(result.ok ? CortexDesign.inkSecondary : CortexDesign.accent)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
                .padding(.leading, 34)
            }
        }
        .padding(.vertical, 10)
        .padding(.trailing, 10)
        .padding(.leading, 25)
        .background(CortexDesign.cardBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .archiveSpine(CortexDesign.accent)
    }

    private func runTest(_ integration: AIIntegration) {
        Task {
            let result = await state.testToolConnection(integration)
            testResults[integration.id] = result
        }
    }

    private var statusColor: Color {
        if connectedCount > 0 { return CortexDesign.sealMoss }
        if !detectedConnectable.isEmpty { return CortexDesign.accent }
        return CortexDesign.inkSecondary
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
        return "Connect Claude Desktop or another tool"
    }

    private var statusDetail: String {
        if connectedCount > 0 {
            return "These apps can read reviewed memory with citations."
        }
        if !detectedConnectable.isEmpty {
            return "Enable this only when you want reviewed memory available outside Cortex."
        }
        return "Use Copy tool config for Claude Desktop or any MCP-compatible app. ChatGPT web cannot read local memory directly yet; import exported chats as sources."
    }

    /// The ChatGPT / Claude-web path, given equal footing with MCP installs: one tap builds a
    /// cited memory pack from the same context engine and opens the site. Preview shows exactly
    /// what will land on the clipboard before you copy it. This is how browser assistants actually
    /// use Cortex data today.
    private var browserAssistantRow: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center, spacing: 14) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(CortexDesign.accent.opacity(0.13))
                    Image(systemName: "doc.on.clipboard")
                        .font(.system(size: 24, weight: .semibold))
                        .foregroundColor(CortexDesign.accent)
                }
                .frame(width: 56, height: 56)

                VStack(alignment: .leading, spacing: 4) {
                    Text("ChatGPT, Claude web & other chats".uppercased())
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.8)
                        .foregroundColor(CortexDesign.inkFaint)
                    Text("Copy a memory pack")
                        .font(.title3)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.ink)
                    Text("Puts your reviewed, cited memory on the clipboard — paste it at the start of any chat.")
                        .font(.callout)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Spacer(minLength: 8)

                CortexButton(title: "Copy memory pack", systemImage: "doc.on.clipboard", role: .secondary, size: .large) {
                    state.copyMemoryPack()
                }
                .help("Builds a cited pack of your approved memory and copies it for ChatGPT, Claude web, Gemini, or any other assistant.")
            }

            memoryPackPreview
        }
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    /// A lightweight preview of the memory pack: "N memories ready · M characters" plus an
    /// expandable, read-only, monospaced excerpt of the first ~600 characters so the user sees
    /// exactly what they'll paste before copying it.
    @ViewBuilder
    private var memoryPackPreview: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                CortexButton(
                    title: state.memoryPackPreview == nil ? "Preview" : "Refresh preview",
                    systemImage: "eye",
                    role: .ghost,
                    size: .small
                ) {
                    Task { await state.loadMemoryPackPreview() }
                }
                .help("See exactly what Cortex will copy before you paste it into a chat.")

                if let preview = state.memoryPackPreview {
                    Text("\(preview.itemCount) memor\(preview.itemCount == 1 ? "y" : "ies") ready · \(preview.characterCount) characters")
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                }
                Spacer(minLength: 0)
            }

            if let preview = state.memoryPackPreview {
                DisclosureGroup(isExpanded: $packPreviewExpanded) {
                    ScrollView {
                        Text(memoryPackExcerpt(preview.text))
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundColor(CortexDesign.ink)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(12)
                    }
                    .frame(maxHeight: 180)
                    .background(RoundedRectangle(cornerRadius: 8).fill(CortexDesign.quietBackground))
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))
                    .padding(.top, 6)
                } label: {
                    Text(packPreviewExpanded ? "Hide preview" : "Show what will be copied")
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.inkSecondary)
                }
            }
        }
    }

    /// First ~600 characters of the pack, with an ellipsis when there's more. Kept read-only —
    /// this is a preview, not an editor.
    private func memoryPackExcerpt(_ text: String) -> String {
        guard text.count > 600 else { return text }
        return String(text.prefix(600)) + "…"
    }
}

// MARK: - Connect an app wizard
//
// A guided front door for wiring Cortex memory into an external AI tool in four clear steps:
// pick tool → connect → verify → done. It is ADDITIVE over the per-tool tiles in
// ConnectionsAIToolsSection — same catalog (AIIntegrationCatalog via `state.integrations`), same
// primitives (mcpConfigJSON / copyMCPConfig+markIntegrationConfigCopied / connectIntegration /
// testToolConnection), same honesty gates. No new backend endpoints, no parallel config system.

/// The four wizard steps. `.pick` has no selected tool yet; the rest all operate on the tool the
/// user chose in `.pick`.
private enum ConnectAppWizardStep: Int, CaseIterable {
    case pick, connect, verify, done

    var index: Int { rawValue }
    static var count: Int { allCases.count }
}

/// Self-contained sheet: pick a tool, copy its ready-to-paste connection, test that the tool can
/// reach Cortex, then a success confirmation. Dismissable at every step; Back/Next navigation with
/// a 1..4 of 4 progress affordance. Honesty invariant: step 4 is only reachable after the copy+mark
/// step (which drives connected-state) AND, where a real probe exists, a passing `testToolConnection`.
struct ConnectAppWizard: View {
    @ObservedObject var state: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var step: ConnectAppWizardStep = .pick
    @State private var selected: AIIntegration?
    /// True once the user copied the config / command / pack for the selected tool (drives
    /// connected-state on MAS via markIntegrationConfigCopied, called inside copyMCPConfig).
    @State private var didCopy = false
    /// The most recent `testToolConnection` result for the selected tool. nil = not tested yet.
    @State private var testResult: ConnectionTestResult?
    @State private var isTesting = false

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    progressBar
                    stepContent
                }
                .padding(24)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            Divider()
            footer
        }
        .frame(minWidth: 560, minHeight: 560)
        .background(connectionsSheetBackground)
    }

    // MARK: Chrome

    private var header: some View {
        HStack(alignment: .top, spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(CortexDesign.accentSoft)
                Image(systemName: "wand.and.stars")
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundColor(CortexDesign.accent)
            }
            .frame(width: 46, height: 46)

            VStack(alignment: .leading, spacing: 4) {
                Text("Connect an AI app")
                    .font(CortexDesign.Typography.display(20))
                    .foregroundColor(CortexDesign.ink)
                Text(headerSubtitle)
                    .font(CortexDesign.Typography.body)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 0)

            CortexIconButton(systemImage: "xmark", role: .ghost, size: .large, help: "Close") {
                dismiss()
            }
            .accessibilityLabel("Close Connect an AI app")
        }
        .padding(20)
        .background(connectionsSheetBackground)
    }

    private var headerSubtitle: String {
        switch step {
        case .pick: return "Pick the tool you want to give access to your reviewed memory."
        case .connect: return selected.map { "Add Cortex to \($0.name)." } ?? "Add Cortex to your tool."
        case .verify: return selected.map { "Check that \($0.name) can reach your memory." } ?? "Check the connection."
        case .done: return "You're set — your memory is available where you work."
        }
    }

    /// The 1..4 of 4 affordance: a filled pip per completed/current step, a hairline pill per
    /// upcoming one, plus a plain "Step N of 4" label for screen readers and small windows.
    private var progressBar: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                ForEach(ConnectAppWizardStep.allCases, id: \.self) { s in
                    Capsule()
                        .fill(s.index <= step.index ? CortexDesign.accent : CortexDesign.hairline)
                        .frame(height: 4)
                        .frame(maxWidth: .infinity)
                }
            }
            Text("Step \(step.index + 1) of \(ConnectAppWizardStep.count) · \(stepTitle)")
                .font(CortexDesign.Typography.stamp)
                .kerning(0.6)
                .foregroundColor(CortexDesign.inkFaint)
        }
    }

    private var stepTitle: String {
        switch step {
        case .pick: return "Pick a tool"
        case .connect: return "Connect"
        case .verify: return "Verify"
        case .done: return "Done"
        }
    }

    private var footer: some View {
        HStack(spacing: 10) {
            if step != .pick && step != .done {
                CortexButton(title: "Back", systemImage: "chevron.left", role: .ghost, size: .large) {
                    goBack()
                }
            }
            Spacer(minLength: 0)
            footerPrimary
        }
        .padding(16)
        .background(connectionsSheetBackground)
    }

    /// Exactly one primary per step. On `.connect` and `.verify` the forward move is disabled until
    /// the honest precondition is met (copied / test passed), so the user can never skip to "Done"
    /// without actually connecting.
    @ViewBuilder
    private var footerPrimary: some View {
        switch step {
        case .pick:
            CortexButton(title: "Continue", systemImage: "arrow.right", role: .primary, size: .large) {
                withAnimation(.easeInOut(duration: 0.2)) { step = .connect }
            }
            .disabled(selected == nil)
        case .connect:
            CortexButton(title: "Next: verify", systemImage: "arrow.right", role: .primary, size: .large) {
                withAnimation(.easeInOut(duration: 0.2)) { step = .verify }
            }
            .disabled(!didCopy)
            .help(didCopy ? "" : "Copy the connection first.")
        case .verify:
            CortexButton(title: "Finish", systemImage: "checkmark", role: .primary, size: .large) {
                withAnimation(.easeInOut(duration: 0.2)) { step = .done }
            }
            .disabled(!(testResult?.ok ?? false))
            .help((testResult?.ok ?? false) ? "" : "Run the test and pass it first.")
        case .done:
            CortexButton(title: "Close", role: .primary, size: .large) {
                dismiss()
            }
        }
    }

    // MARK: Step body

    @ViewBuilder
    private var stepContent: some View {
        switch step {
        case .pick: pickStep
        case .connect: connectStep
        case .verify: verifyStep
        case .done: doneStep
        }
    }

    // Step 1 — Pick tool: the same catalog the tiles use, grouped by category. Each card shows
    // icon + name + the tool's own one-line summary ("what you'll be able to do").
    private var pickStep: some View {
        VStack(alignment: .leading, spacing: 18) {
            ForEach(IntegrationCategory.allCases, id: \.self) { category in
                let tools = state.integrations.filter { $0.category == category }
                if !tools.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(category.rawValue.uppercased())
                            .font(CortexDesign.Typography.stamp)
                            .kerning(0.8)
                            .foregroundColor(CortexDesign.inkFaint)
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 220), spacing: 10)], spacing: 10) {
                            ForEach(tools) { tool in
                                toolPickCard(tool)
                            }
                        }
                    }
                }
            }
        }
    }

    private func toolPickCard(_ tool: AIIntegration) -> some View {
        let isSelected = selected?.id == tool.id
        return Button {
            selectTool(tool)
        } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: tool.systemImage)
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundColor(isSelected ? CortexDesign.accent : CortexDesign.inkSecondary)
                    .frame(width: 24)
                VStack(alignment: .leading, spacing: 3) {
                    Text(tool.name)
                        .font(.callout)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.ink)
                    Text(tool.summary)
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
                Spacer(minLength: 0)
                if isSelected {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(CortexDesign.accent)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(CortexDesign.cardBackground)
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(isSelected ? CortexDesign.accent : CortexDesign.hairline, lineWidth: isSelected ? 1.5 : 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .contentShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
    }

    // Step 2 — Connect: the tool's real connection path (mcpConfig / cliCommand / memoryPack /
    // httpAPI), with one big primary that reuses the existing per-kind copy action and, for the
    // config path, marks the tool connected via markIntegrationConfigCopied.
    @ViewBuilder
    private var connectStep: some View {
        if let tool = selected {
            VStack(alignment: .leading, spacing: 16) {
                selectedToolBanner(tool)

                switch tool.connectionKind {
                case .mcpConfig:
                    mcpConnectBody(tool)
                case .cliCommand:
                    commandConnectBody(tool)
                case .memoryPack:
                    memoryPackConnectBody(tool)
                case .httpAPI:
                    httpAPIConnectBody(tool)
                }

                privacyNote
            }
        }
    }

    /// The MCP config path. On DMG we offer the one-click file merge (installIntegration via
    /// connectIntegration) AND the copy path; on MAS the sandbox can't write other apps' files, so
    /// only the copy+paste path is honest (and copyMCPConfig records the connected-state signal).
    private func mcpConnectBody(_ tool: AIIntegration) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            configPreview
            pasteInstructions(configPasteSteps(tool))
            HStack(spacing: 10) {
                CortexButton(title: "Copy config", systemImage: "doc.on.doc", role: .primary, size: .large) {
                    state.copyMCPConfig(for: tool)
                    didCopy = true
                }
                if tool.supportsInstall && !DistributionMode.isAppStore {
                    CortexButton(title: "Connect automatically", systemImage: "link.circle", role: .secondary, size: .large) {
                        state.connectIntegration(tool)
                        didCopy = true
                    }
                    .help("Writes the Cortex server into \(tool.name)'s config file for you (a backup is saved first).")
                }
                Spacer(minLength: 0)
            }
            copiedConfirmation
        }
    }

    /// CLI tools register MCP from their own terminal command; copyCLICommand builds the exact
    /// line (with this tool's scoped token) and puts it on the clipboard.
    private func commandConnectBody(_ tool: AIIntegration) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            pasteInstructions([
                "Copy the connection command below.",
                "Paste it into a terminal and press Return.",
                tool.restartHint
            ])
            CortexButton(title: "Copy command", systemImage: "terminal", role: .primary, size: .large) {
                state.copyCLICommand(for: tool)
                didCopy = true
            }
            copiedConfirmation
        }
    }

    /// Browser assistants can't run tools; copyMemoryPack puts a cited, reviewed pack on the
    /// clipboard (and opens the site) to paste at the start of a chat.
    private func memoryPackConnectBody(_ tool: AIIntegration) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            pasteInstructions([
                "Copy your reviewed memory pack.",
                "Open \(tool.name) and start a new chat.",
                tool.restartHint
            ])
            HStack(spacing: 10) {
                CortexButton(title: "Copy memory pack", systemImage: "doc.on.clipboard", role: .primary, size: .large) {
                    state.copyMemoryPack(for: tool, openSite: true)
                    didCopy = true
                }
                CortexButton(title: "Connect extension", systemImage: "puzzlepiece.extension", role: .secondary, size: .large) {
                    state.pairBrowserExtension()
                    didCopy = true
                }
                .help("For a one-click browser path, pair the Cortex extension instead of pasting a pack each time.")
                Spacer(minLength: 0)
            }
            copiedConfirmation
        }
    }

    /// Self-hosted/local stacks call tools over HTTP; copyHTTPAPIDetails copies base URL + scoped
    /// token + schema endpoints.
    private func httpAPIConnectBody(_ tool: AIIntegration) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            pasteInstructions([
                "Copy the local API details (base URL, token, endpoints).",
                "Add them where \(tool.name) accepts a local tool or OpenAI-compatible API.",
                tool.restartHint
            ])
            CortexButton(title: "Copy API details", systemImage: "curlybraces", role: .primary, size: .large) {
                state.copyHTTPAPIDetails(for: tool)
                didCopy = true
            }
            copiedConfirmation
        }
    }

    /// Redacted config preview — byte-for-byte the copied shape with the token replaced, straight
    /// from the same builder Copy uses (mcpConfigJSON → mcpServerDefinition). Never a hand literal.
    private var configPreview: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Configuration")
                .font(.caption)
                .fontWeight(.semibold)
                .foregroundColor(CortexDesign.inkSecondary)
            ScrollView(.horizontal, showsIndicators: false) {
                Text(state.mcpConfigJSON(for: selected, redactToken: true))
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundColor(CortexDesign.ink)
                    .textSelection(.enabled)
                    .padding(12)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 8).fill(CortexDesign.quietBackground))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))
        }
    }

    /// Concrete, tool-specific paste steps for the MCP config path, built from the tool's own
    /// setupHint / config targets / restartHint rather than a divergent hardcoded script.
    private func configPasteSteps(_ tool: AIIntegration) -> [String] {
        if DistributionMode.isAppStore || tool.configTargets.isEmpty {
            return [
                "Copy the configuration below.",
                "Open \(tool.name)'s MCP settings and paste it into the mcpServers block, then save.",
                tool.restartHint
            ]
        }
        let target = tool.configTargets[0]
        return [
            "Copy the configuration below.",
            "Paste it into \(tool.name)'s config (\(target.url.path)) inside the mcpServers block, then save.",
            tool.restartHint
        ]
    }

    private func pasteInstructions(_ steps: [String]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                HStack(alignment: .top, spacing: 8) {
                    Text("\(index + 1)")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundColor(CortexDesign.panelBackground)
                        .frame(width: 20, height: 20)
                        .background(Circle().fill(CortexDesign.accent))
                    Text(step)
                        .font(.callout)
                        .foregroundColor(CortexDesign.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
            }
        }
    }

    @ViewBuilder
    private var copiedConfirmation: some View {
        if didCopy {
            HStack(spacing: 6) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(CortexDesign.sealMoss)
                Text("Copied — paste it, then continue to verify.")
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
            }
        }
    }

    private var privacyNote: some View {
        HStack(alignment: .top, spacing: 7) {
            Circle()
                .fill(CortexDesign.sealMoss)
                .frame(width: 7, height: 7)
                .padding(.top, 3)
            Text("The token stays on this Mac and only reaches the app you paste it into. Memory stays local.")
                .font(.caption)
                .foregroundColor(CortexDesign.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // Step 3 — Verify: the honest probe. testToolConnection refuses to report success for an
    // mcpConfig tool whose config isn't configured yet, and always confirms Cortex is serving
    // tools. A failure shows the actionable message and a retry.
    @ViewBuilder
    private var verifyStep: some View {
        if let tool = selected {
            VStack(alignment: .leading, spacing: 16) {
                selectedToolBanner(tool)

                Text("Run the test to confirm \(tool.name) can reach your reviewed memory.")
                    .font(.callout)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)

                if let result = testResult {
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: result.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundColor(result.ok ? CortexDesign.sealMoss : CortexDesign.gold)
                        Text(result.message)
                            .font(.callout)
                            .foregroundColor(result.ok ? CortexDesign.ink : CortexDesign.accent)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                    }
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 8).fill((result.ok ? CortexDesign.sealMoss : CortexDesign.gold).opacity(0.1)))
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))
                }

                HStack(spacing: 10) {
                    if isTesting {
                        ProgressView().scaleEffect(0.8).frame(minHeight: 44)
                    } else {
                        CortexButton(
                            title: testResult == nil ? "Test connection" : (testResult?.ok == true ? "Test again" : "Retry test"),
                            systemImage: "checklist",
                            role: .primary,
                            size: .large
                        ) {
                            runTest(tool)
                        }
                    }
                    Spacer(minLength: 0)
                }
            }
        }
    }

    // Step 4 — Done: success confirmation + a concrete "what you can now do" line + a way to
    // connect another tool or close.
    @ViewBuilder
    private var doneStep: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 12) {
                Image(systemName: "checkmark.seal.fill")
                    .font(.system(size: 36, weight: .semibold))
                    .foregroundColor(CortexDesign.sealMoss)
                VStack(alignment: .leading, spacing: 3) {
                    Text(selected.map { "\($0.name) is connected" } ?? "Connected")
                        .font(.title3)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.ink)
                    Text(selected?.setupHint ?? "Your reviewed memory is now available to this app.")
                        .font(.callout)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 8).fill(CortexDesign.sealMoss.opacity(0.1)))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))

            CortexButton(title: "Connect another app", systemImage: "plus", role: .secondary, size: .large) {
                resetForAnother()
            }
        }
    }

    // MARK: Shared bits

    private func selectedToolBanner(_ tool: AIIntegration) -> some View {
        HStack(spacing: 10) {
            Image(systemName: tool.systemImage)
                .font(.system(size: 18, weight: .semibold))
                .foregroundColor(CortexDesign.accent)
                .frame(width: 24)
            Text(tool.name)
                .font(.title3)
                .fontWeight(.semibold)
                .foregroundColor(CortexDesign.ink)
            Spacer(minLength: 0)
        }
    }

    // MARK: Actions

    private func selectTool(_ tool: AIIntegration) {
        selected = tool
        // Fresh selection resets the per-tool progress so we never carry a stale copied/tested
        // state onto a different tool.
        didCopy = false
        testResult = nil
    }

    private func goBack() {
        withAnimation(.easeInOut(duration: 0.2)) {
            switch step {
            case .connect: step = .pick
            case .verify: step = .connect
            default: break
            }
        }
    }

    private func runTest(_ tool: AIIntegration) {
        isTesting = true
        Task {
            let result = await state.testToolConnection(tool)
            testResult = result
            isTesting = false
        }
    }

    private func resetForAnother() {
        withAnimation(.easeInOut(duration: 0.2)) {
            selected = nil
            didCopy = false
            testResult = nil
            step = .pick
        }
    }
}

/// Guided manual MCP setup for local-first (App Store) builds. The sandbox blocks writing into
/// other apps' config files, so instead of automating the connection we hand the user the exact
/// server JSON in a copyable block plus numbered steps. The visible block shows the shape with a
/// redacted token; the Copy button puts the real, tokened configuration on the clipboard.
private struct ConnectionsGuidedMCPSetup: View {
    @ObservedObject var state: AppState
    @State private var setupExpanded = false

    private let steps = [
        "Open your AI app's MCP settings (Claude Desktop: Settings → Developer → Edit Config).",
        "Paste the configuration below into the mcpServers block, then save.",
        "Quit and reopen the app — Cortex memory tools appear once it restarts."
    ]

    /// Redacted preview of the connection JSON — the real token is copied, never shown. Rendered
    /// from the SAME builder the Copy button uses (`mcpConfigJSON`, which funnels through
    /// `mcpServerDefinition`), just with the token redacted, so what the user sees is byte-for-byte
    /// the real (redacted) shape. Never a hand-written literal: when the App Store branch of
    /// `mcpServerDefinition` changes shape (e.g. type:http / url / headers), this preview inherits
    /// it automatically.
    private var previewConfig: String {
        state.mcpConfigJSON(redactToken: true)
    }

    var body: some View {
        DisclosureGroup(isExpanded: $setupExpanded) {
            VStack(alignment: .leading, spacing: 12) {
                GuidedStepWalkthrough(steps: steps, isActive: setupExpanded, textFont: .callout) { _ in
                    EmptyView()
                }

                VStack(alignment: .leading, spacing: 8) {
                    Text("Connection")
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.inkSecondary)
                    ScrollView(.horizontal, showsIndicators: false) {
                        Text(previewConfig)
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundColor(CortexDesign.ink)
                            .textSelection(.enabled)
                            .padding(12)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 8).fill(CortexDesign.quietBackground))
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))

                    HStack(spacing: 10) {
                        CortexButton(title: "Copy configuration", systemImage: "doc.on.doc", role: .secondary, size: .large) {
                            state.copyMCPConfig()
                        }
                        .help("Copies the full configuration, including this Mac's local connection token, to paste into your AI app.")
                        Spacer(minLength: 0)
                    }

                    // The moss mark — the archive's private-by-default signature.
                    HStack(alignment: .top, spacing: 7) {
                        Circle()
                            .fill(CortexDesign.sealMoss)
                            .frame(width: 7, height: 7)
                            .padding(.top, 3)
                        Text("The token stays on this Mac and only reaches the AI app you paste it into. Memory stays local.")
                            .font(.caption)
                            .foregroundColor(CortexDesign.inkSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .padding(.top, 10)
        } label: {
            ConnectionsDisclosureLabel(
                systemImage: "text.and.command.macwindow",
                title: "Connect an AI tool manually",
                detail: "Copy the connection and paste it into Claude, Cursor & other AI apps"
            )
        }
        .connectionsSubCard()
    }
}

private struct ConnectionsPrivacyDefaultsSection: View {
    @ObservedObject var state: AppState
    let summary: TrustSummaryResponse

    private var backupCount: Int {
        state.dataLifecycleReport?.backups.count ?? 0
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 6) {
                    SectionHeader(
                        title: "Backup & privacy",
                        detail: "New memory waits for your approval."
                    )
                    // The moss mark — the archive's private-by-default signature.
                    HStack(spacing: 7) {
                        Circle()
                            .fill(CortexDesign.sealMoss)
                            .frame(width: 7, height: 7)
                        Text("Everything stays on this Mac.")
                            .font(.system(size: 12, weight: .medium))
                            .foregroundColor(CortexDesign.sealMoss)
                    }
                }
                Spacer(minLength: 12)
                CortexButton(
                    title: backupCount > 0 ? "Back Up Again" : "Back Up Now",
                    systemImage: "archivebox",
                    role: .secondary,
                    size: .large
                ) {
                    state.createBackup()
                }
                .help("Writes a local backup of your memory folder on this Mac.")
            }
        }
        .connectionsSubCard()
    }
}

private struct ConnectionsMCPAccessSection: View {
    @ObservedObject var state: AppState
    @State private var recentActivityExpanded = false

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
                VStack(alignment: .leading, spacing: 6) {
                    SectionHeader(
                        title: "Tool permissions",
                        detail: "Connected AI tools can only use the permissions below."
                    )
                    // The moss mark — the archive's private-by-default signature.
                    HStack(spacing: 7) {
                        Circle()
                            .fill(CortexDesign.sealMoss)
                            .frame(width: 7, height: 7)
                        Text("Recent activity is shown without raw memory content.")
                            .font(.system(size: 12, weight: .medium))
                            .foregroundColor(CortexDesign.sealMoss)
                    }
                }
                Spacer(minLength: 12)
            }

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 10)], spacing: 10) {
                ConnectionsTrustTile(
                    title: settings.allow_agent_reads ? "Read" : "Read off",
                    detail: settings.allow_agent_reads ? "reviewed memory" : "blocked",
                    systemImage: settings.allow_agent_reads ? "eye.fill" : "eye.slash.fill",
                    color: settings.allow_agent_reads ? CortexDesign.sealMoss : CortexDesign.inkSecondary
                )
                ConnectionsTrustTile(
                    title: settings.allow_agent_writes ? "Save" : "Save off",
                    detail: settings.allow_agent_writes ? "new memory to Review" : "blocked",
                    systemImage: settings.allow_agent_writes ? "square.and.pencil" : "pencil.slash",
                    color: settings.allow_agent_writes ? CortexDesign.accent : CortexDesign.inkSecondary
                )
                ConnectionsTrustTile(
                    title: settings.allow_agent_exports ? "Export on" : "Export off",
                    detail: settings.allow_agent_exports ? "redacted exports" : "blocked",
                    systemImage: "square.and.arrow.up",
                    color: settings.allow_agent_exports ? CortexDesign.accent : CortexDesign.inkSecondary
                )
                ConnectionsTrustTile(
                    title: settings.allow_agent_maintenance ? "Maintenance on" : "Maintenance off",
                    detail: settings.allow_agent_destructive_actions ? "delete allowed" : "no deletion",
                    systemImage: settings.allow_agent_maintenance ? "wrench.and.screwdriver.fill" : "wrench.and.screwdriver",
                    color: settings.allow_agent_maintenance ? CortexDesign.accent : CortexDesign.inkSecondary
                )
            }

            HStack(alignment: .center, spacing: 12) {
                Image(systemName: activeMCPTokens.isEmpty ? "key.slash" : "key.fill")
                    .foregroundColor(activeMCPTokens.isEmpty ? CortexDesign.inkSecondary : CortexDesign.accent)
                    .frame(width: 28)
                VStack(alignment: .leading, spacing: 3) {
                    Text(activeMCPTokens.isEmpty ? "No AI tool connected yet" : "\(activeMCPTokens.count) active MCP token\(activeMCPTokens.count == 1 ? "" : "s")")
                        .font(.callout)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.ink)
                    Text("\(activeScopeSummary) · \(lastUsedLabel)")
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .lineLimit(2)
                }
                Spacer(minLength: 8)
                CortexButton(title: "Reset Token", systemImage: "arrow.triangle.2.circlepath", role: .secondary, size: .small) {
                    Task { await state.resetMCPIntegrationToken() }
                }
                .help("Revokes the current MCP token and mints a new one. Connected tools must be reconfigured.")
            }
            .padding(12)
            .background(CortexDesign.cardBackground)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            DisclosureGroup(isExpanded: $recentActivityExpanded) {
                VStack(alignment: .leading, spacing: 8) {
                    if recentToolEvents.isEmpty {
                        Text("No AI tool activity recorded yet.")
                            .font(.callout)
                            .foregroundColor(CortexDesign.inkSecondary)
                    } else {
                        ForEach(recentToolEvents) { event in
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Image(systemName: "wand.and.stars")
                                    .foregroundColor(CortexDesign.accent)
                                    .frame(width: 20)
                                Text(event.event_type.replacingOccurrences(of: "_", with: " ").capitalized)
                                    .font(.callout)
                                    .fontWeight(.medium)
                                    .foregroundColor(CortexDesign.ink)
                                Spacer(minLength: 0)
                                // Dates speak in the catalog-stamp voice.
                                Text(shortDate(event.created_at).uppercased())
                                    .font(CortexDesign.Typography.stamp)
                                    .kerning(0.8)
                                    .foregroundColor(CortexDesign.inkFaint)
                            }
                        }
                    }
                }
                .padding(.top, 8)
            } label: {
                Text("Recent tool activity")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(CortexDesign.inkSecondary)
            }
            .padding(12)
            .background(CortexDesign.cardBackground)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
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

/// "Manage stored data" — everything Cortex has learned, grouped by the source it came from,
/// with a one-step destructive purge per source. Backed by the AppState source-stats contract
/// (loadSourceStats / purgeSource / purgingSource). Loads its stats on appear so opening the
/// card is enough to see current counts.
private struct ConnectionsStoredDataSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if state.sourceStats.isEmpty {
                QuietState(
                    title: "Nothing stored yet",
                    detail: "Connect a source or import to build your memory.",
                    systemImage: "tray"
                )
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(state.sourceStats) { stat in
                        ConnectionsStoredDataRow(state: state, stat: stat)
                    }
                }
            }
        }
        .task {
            await state.loadSourceStats()
        }
    }
}

/// One source's stored-data row: humanized name, memory count, a relative "last added",
/// and a destructive "Delete all…" that confirms before purging. While this source is being
/// purged the button is replaced by a spinner and disabled.
private struct ConnectionsStoredDataRow: View {
    @ObservedObject var state: AppState
    let stat: SourceMemoryStat
    @State private var confirmDelete = false

    private var isPurging: Bool {
        state.purgingSource == stat.source
    }

    private var displayName: String {
        humanizeSourceName(stat.source)
    }

    private var countLabel: String {
        "\(stat.count) memor\(stat.count == 1 ? "y" : "ies")"
    }

    private var lastAddedLabel: String? {
        relativeCapturedLabel(stat.last_captured_at)
    }

    var body: some View {
        HStack(alignment: .center, spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(CortexDesign.accent.opacity(0.12))
                Image(systemName: "archivebox.fill")
                    .font(.system(size: 20, weight: .semibold))
                    .foregroundColor(CortexDesign.accent)
            }
            .frame(width: 44, height: 44)

            VStack(alignment: .leading, spacing: 3) {
                Text(displayName)
                    .font(.headline)
                    .foregroundColor(CortexDesign.ink)
                    .lineLimit(1)
                HStack(spacing: 6) {
                    Text(countLabel)
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                    if let lastAddedLabel {
                        Text("·")
                            .font(.caption)
                            .foregroundColor(CortexDesign.inkFaint)
                        Text("last added \(lastAddedLabel)")
                            .font(.caption)
                            .foregroundColor(CortexDesign.inkSecondary)
                    }
                }
            }

            Spacer(minLength: 12)

            if isPurging {
                ProgressView()
                    .scaleEffect(0.8)
                    .frame(minWidth: 118, minHeight: 42)
            } else {
                CortexButton(title: "Delete all…", systemImage: "trash", role: .destructive, size: .regular) {
                    confirmDelete = true
                }
                .disabled(state.purgingSource != nil)
                .help("Permanently delete every memory that came from \(displayName).")
                .confirmationDialog(
                    "Delete all data from \(displayName)?",
                    isPresented: $confirmDelete,
                    titleVisibility: .visible
                ) {
                    Button("Delete all", role: .destructive) {
                        Task { await state.purgeSource(stat.source) }
                    }
                    Button("Cancel", role: .cancel) {}
                } message: {
                    Text("This permanently removes \(stat.count) memor\(stat.count == 1 ? "y" : "ies") (and everything derived from them). This can't be undone.")
                }
            }
        }
        .padding(.vertical, 12)
        .padding(.horizontal, 14)
        .background(CortexDesign.cardBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

/// Best-effort Title Case for a raw source key, with a few well-known brands spelled the way
/// people expect ("chatgpt" → "ChatGPT"). Anything unknown falls back to Title Case of the
/// slug (dashes/underscores become spaces), and truly empty strings stay as a quiet placeholder.
private func humanizeSourceName(_ raw: String) -> String {
    let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return "Unknown source" }
    let known: [String: String] = [
        "chatgpt": "ChatGPT",
        "openai": "OpenAI",
        "claude": "Claude",
        "github": "GitHub",
        "google-drive": "Google Drive",
        "gmail": "Gmail",
        "outlook": "Outlook",
        "notion": "Notion",
        "slack": "Slack",
        "obsidian": "Obsidian",
        "zotero": "Zotero",
        "readwise": "Readwise",
        "raindrop": "Raindrop",
        "linear": "Linear",
        "jira": "Jira",
        "calendar": "Calendar"
    ]
    if let mapped = known[trimmed.lowercased()] {
        return mapped
    }
    return trimmed
        .replacingOccurrences(of: "-", with: " ")
        .replacingOccurrences(of: "_", with: " ")
        .split(separator: " ")
        .map { word -> String in
            guard let first = word.first else { return String(word) }
            return first.uppercased() + word.dropFirst()
        }
        .joined(separator: " ")
}

/// A gentle relative "last added" label from a backend timestamp string. Parses ISO-8601 first
/// (with and without fractional seconds); if that fails it falls back to the file's existing
/// prefix-10 date shortening so a non-ISO value still reads sensibly. Returns nil for empty input.
private func relativeCapturedLabel(_ value: String?) -> String? {
    guard let raw = value?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty else {
        return nil
    }
    let isoWithFractional = ISO8601DateFormatter()
    isoWithFractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let isoPlain = ISO8601DateFormatter()
    isoPlain.formatOptions = [.withInternetDateTime]
    if let date = isoWithFractional.date(from: raw) ?? isoPlain.date(from: raw) {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        return formatter.localizedString(for: date, relativeTo: Date())
    }
    // Non-ISO string — show a trimmed date the way the rest of this sheet does.
    return String(raw.prefix(10))
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
                    .foregroundColor(CortexDesign.ink)
                    .lineLimit(2)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
        }
        .padding(12)
        .frame(minHeight: 72, alignment: .leading)
        .background(CortexDesign.cardBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))
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
                .foregroundColor(CortexDesign.accent)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.headline)
                    .foregroundColor(CortexDesign.ink)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
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

    private var retryButton: some View {
        CortexButton(
            title: isRetrying ? "Checking…" : "Retry",
            systemImage: "arrow.clockwise",
            role: .secondary,
            size: .large,
            action: runRetry
        )
        .disabled(isRetrying)
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

/// "Sign in with GitHub" device-code sheet. Shows the short code the user types on
/// github.com/login/device, reflects live progress (waiting → syncing → done/failed) by observing
/// AppState, and lets the user cancel — which stops the poll loop.
struct GitHubDeviceCodeView: View {
    @ObservedObject var state: AppState
    /// Stable code + verification URL captured when the sheet was presented. Live phase/message are
    /// read from `state.githubDeviceFlow` so the same sheet animates through the flow.
    let prompt: GitHubDeviceFlowPrompt
    @State private var copied = false

    private var phase: GitHubDeviceFlowPrompt.Phase { state.githubDeviceFlow?.phase ?? prompt.phase }
    private var statusMessage: String { state.githubDeviceFlow?.message ?? prompt.message }
    private var isFinished: Bool { phase == .done || phase == .failed }

    var body: some View {
        VStack(spacing: 18) {
            VStack(spacing: 6) {
                Image(systemName: "chevron.left.forwardslash.chevron.right")
                    .font(.system(size: 26, weight: .semibold))
                    .foregroundColor(CortexDesign.accent)
                Text("Sign in with GitHub")
                    .font(.system(size: 19, weight: .semibold))
                    .foregroundColor(CortexDesign.ink)
                Text("Cortex reads your GitHub activity so your memory can cite it. Read-only — Cortex never writes to your repositories.")
                    .font(.system(size: 12))
                    .foregroundColor(CortexDesign.inkSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }

            VStack(spacing: 8) {
                Text("Enter this code on the GitHub page")
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkFaint)
                Text(prompt.userCode)
                    .font(.system(size: 30, weight: .bold, design: .monospaced))
                    .kerning(4)
                    .foregroundColor(CortexDesign.ink)
                    .textSelection(.enabled)
                Button {
                    copyCode()
                } label: {
                    Label(copied ? "Copied" : "Copy code", systemImage: copied ? "checkmark" : "doc.on.doc")
                        .font(.system(size: 12, weight: .medium))
                }
                .buttonStyle(.plain)
                .foregroundColor(CortexDesign.accent)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
            .background(RoundedRectangle(cornerRadius: CortexDesign.Radius.md).fill(CortexDesign.accentSoft))

            statusRow

            Spacer(minLength: 0)

            HStack(spacing: 10) {
                if isFinished {
                    Button("Done") { state.githubDeviceFlow = nil }
                        .keyboardShortcut(.defaultAction)
                } else {
                    Button("Cancel") { state.cancelGitHubDeviceFlow() }
                    Spacer(minLength: 0)
                    if let url = prompt.verificationURL {
                        Button("Open GitHub") { NSWorkspace.shared.open(url) }
                            .keyboardShortcut(.defaultAction)
                    }
                }
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(CortexDesign.panelBackground)
    }

    @ViewBuilder
    private var statusRow: some View {
        HStack(spacing: 8) {
            switch phase {
            case .waiting, .syncing:
                ProgressView().controlSize(.small)
            case .done:
                Image(systemName: "checkmark.circle.fill").foregroundColor(.green)
            case .failed:
                Image(systemName: "exclamationmark.triangle.fill").foregroundColor(.orange)
            }
            Text(statusMessage)
                .font(.system(size: 12))
                .foregroundColor(CortexDesign.inkSecondary)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func copyCode() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(prompt.userCode, forType: .string)
        copied = true
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            copied = false
        }
    }
}
