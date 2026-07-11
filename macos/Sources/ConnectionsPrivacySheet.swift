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
                    Text("Connect notes or ChatGPT/Claude exports, keep memory local, then choose what Claude Desktop, ChatGPT, and other AI tools can use.")
                        .font(CortexDesign.Typography.body)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
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
    @State private var advancedExpanded = true
    @State private var activityMetricsExpanded = false
    @State private var advancedSourcesExpanded = false
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

    /// Connector accounts beyond the primary notes source. Chat imports live in import
    /// history, not source accounts, so notes + chat import alone still counts as zero.
    private var extraConnectedSourceCount: Int {
        state.activeSourceAccounts.filter { $0.source != "obsidian" }.count
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: CortexDesign.Space.xl) {
                ConnectionsObsidianSection(state: state)
                // The two easy paths in (notes folder, chat import) stay visible at top level, even
                // on first run — a user who doesn't want the local notes folder needs a way to
                // connect any other source to get past onboarding. Only the privacy/trust and
                // advanced controls wait until at least one source is connected.
                AIChatsImportCard(state: state)
                otherSourceConnections
                if !state.firstRunNeedsSource {
                    if let summary = state.trustSummary {
                        ConnectionsPrivacyDefaultsSection(state: state, summary: summary)
                        privacySettings(summary: summary)
                        if notesHealth.isNeedsAttention
                            || state.sourceAccounts.contains(where: { $0.disconnected_at == nil && $0.needsAttention }) {
                            connectedNow
                        }
                        advancedControls(summary: summary)
                            .padding(.top, CortexDesign.Space.md)
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
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
        .clipShape(RoundedRectangle(cornerRadius: 8))
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
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
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
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    /// Formerly one "Advanced" mega-disclosure nesting three levels deep. Split into flat,
    /// clearly-named cards so users can find backups without wading through developer diagnostics.
    private func advancedControls(summary: TrustSummaryResponse) -> some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
            DisclosureGroup(isExpanded: $advancedExpanded) {
                VStack(alignment: .leading, spacing: 16) {
                    ConnectionsAIToolsSection(state: state)
                    ConnectionsMCPAccessSection(state: state)
                }
                .padding(.top, 10)
            } label: {
                ConnectionsDisclosureLabel(
                    systemImage: "wand.and.stars",
                    title: "AI tools & permissions",
                    detail: "Use your memory in Claude Desktop, ChatGPT, Cursor & other AI apps"
                )
            }
            .padding(14)
            .background(connectionsPanelBackground)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
            .clipShape(RoundedRectangle(cornerRadius: 8))

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
            .padding(14)
            .background(connectionsPanelBackground)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            DisclosureGroup(isExpanded: $recoveryToolsExpanded) {
                VStack(alignment: .leading, spacing: 14) {
                    SettingsDataRecoverySection(state: state)
                    Divider()
                    SettingsReliabilitySection(state: state)
                }
                .padding(.top, 10)
            } label: {
                ConnectionsDisclosureLabel(
                    systemImage: "arrow.counterclockwise.circle",
                    title: "Backups & recovery",
                    detail: "Back up, restore, repair your local memory"
                )
            }
            .padding(14)
            .background(connectionsPanelBackground)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .onChange(of: recoveryToolsExpanded) { expanded in
                if expanded {
                    Task {
                        await state.loadDiagnostics()
                        await state.loadReliability()
                    }
                }
            }

            DisclosureGroup(isExpanded: $developerDetailsExpanded) {
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
            .padding(14)
            .background(connectionsPanelBackground)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
            .clipShape(RoundedRectangle(cornerRadius: 8))
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

    private var hasHiddenOAuthConnectors: Bool {
        wiredConnectors.contains { !isManaged($0) && isUnconfiguredOAuth($0) }
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
                        Button { connectorSearch = "" } label: { Image(systemName: "xmark.circle.fill") }
                            .buttonStyle(.borderless)
                            .foregroundColor(CortexDesign.inkSecondary)
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

            if hasHiddenOAuthConnectors {
                Text("Email and Drive connect via export import for now.")
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

    /// Managed-OAuth connector whose provider sign-in has no credentials in this build.
    private func isUnconfiguredOAuth(_ connector: SourceConnectorCatalogItem) -> Bool {
        connector.connectionSetup?.supportsManagedOAuth == true
            && !state.managedOAuthIsConfigured(connector)
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
            }
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
                    Button { state.importDetectedExports() } label: { Text("Import") }
                        .buttonStyle(.borderedProminent)
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
                            Button { state.importAIChatExport() } label: {
                                Label("Choose export file…", systemImage: "folder.badge.plus")
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
                .foregroundColor(CortexDesign.inkSecondary)
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
                        VStack(alignment: .leading, spacing: 10) {
                            Text("How to connect")
                                .font(.caption)
                                .fontWeight(.semibold)
                                .foregroundColor(CortexDesign.inkSecondary)
                            // Every backend step renders (Slack sends more than three), and the
                            // help link sits with step 1 so the first action is obvious.
                            GuidedStepWalkthrough(steps: setup.setupSteps) { index in
                                if index == 0, let url = setup.helpURL {
                                    Link(destination: url) {
                                        Label("Open setup help", systemImage: "arrow.up.right.square")
                                            .font(.caption)
                                    }
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
                detail: "Optional. Claude Desktop and other MCP apps can read reviewed memory with citations. ChatGPT or Claude web chats should be imported as exports until direct browser memory support ships."
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
                    Button {
                        state.installDetectedIntegrations()
                    } label: {
                        Label("Enable in apps", systemImage: "link.circle")
                            .frame(minWidth: 138, minHeight: 46)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                } else if !detectedConnectable.isEmpty {
                    Button {
                        state.copyMCPConfig()
                    } label: {
                        Label("Copy setup config", systemImage: "doc.on.doc")
                            .frame(minWidth: 138, minHeight: 46)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .help("Copies the tool configuration to paste into your AI app's settings.")
                } else {
                    Button {
                        state.copyMCPConfig()
                    } label: {
                        Label("Copy tool config", systemImage: "doc.on.doc")
                            .frame(minWidth: 132, minHeight: 46)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)
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

            if !connectedIntegrations.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(connectedIntegrations.prefix(3)) { integration in
                        HStack(spacing: 10) {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundColor(CortexDesign.sealMoss)
                                .frame(width: 24)
                            Text(integration.name)
                                .font(.callout)
                                .fontWeight(.medium)
                                .foregroundColor(CortexDesign.ink)
                            Spacer(minLength: 0)
                            Text("Enabled")
                                .font(.system(size: 12, weight: .medium))
                                .foregroundColor(CortexDesign.sealMoss)
                        }
                        .padding(.vertical, 10)
                        .padding(.trailing, 10)
                        .padding(.leading, 25)
                        .background(CortexDesign.cardBackground)
                        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                        .archiveSpine(CortexDesign.accent)
                    }
                }
            }
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
    /// cited memory pack from the same context engine and opens the site. This is how browser
    /// assistants actually use Cortex data today.
    private var browserAssistantRow: some View {
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

            Button {
                state.copyMemoryPack()
            } label: {
                Label("Copy memory pack", systemImage: "doc.on.clipboard")
                    .frame(minWidth: 138, minHeight: 46)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .help("Builds a cited pack of your approved memory and copies it for ChatGPT, Claude web, Gemini, or any other assistant.")
        }
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
        .clipShape(RoundedRectangle(cornerRadius: 8))
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

    /// Redacted preview of the connection JSON — the real token is copied, never shown. Kept
    /// deliberately close to the canonical shape so what the user sees matches what they paste.
    private var previewConfig: String {
        """
        {
          "mcpServers" : {
            "cortex" : {
              "env" : {
                "CORTEX_API_KEY" : "<copied with the button below>",
                "CORTEX_BASE_URL" : "\(state.endpoint)"
              }
            }
          }
        }
        """
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
                        Button {
                            state.copyMCPConfig()
                        } label: {
                            Label("Copy configuration", systemImage: "doc.on.doc")
                                .frame(minHeight: 42)
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
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
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
        .clipShape(RoundedRectangle(cornerRadius: 8))
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
                Button {
                    state.createBackup()
                } label: {
                    Label(backupCount > 0 ? "Back Up Again" : "Back Up Now", systemImage: "archivebox")
                        .frame(minHeight: 44)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
            }
        }
        .padding(16)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
        .clipShape(RoundedRectangle(cornerRadius: 8))
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
                Button {
                    Task { await state.resetMCPIntegrationToken() }
                } label: {
                    Label("Reset Token", systemImage: "arrow.triangle.2.circlepath")
                }
                .controlSize(.large)
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
