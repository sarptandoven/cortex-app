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
                    Text("Memory stays on this Mac. You choose what other AI apps can use.")
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
                // AI apps is the hero: connecting your memory to Claude Desktop, ChatGPT, and
                // Cursor is why most people open this sheet, so it sits at the very top and opens
                // expanded — once there's memory to share. On first run there's nothing to connect
                // yet, so the Sources group leads instead and AI apps waits for a source.
                //
                // The Sources group stays visible at top level, even on first run: a user who
                // doesn't want the local notes folder needs a way to connect any other source to
                // get past onboarding. Privacy and Advanced wait until a source is connected.
                if !state.firstRunNeedsSource {
                    aiAppsGroup
                }
                sourcesGroup
                if !state.firstRunNeedsSource {
                    if let summary = state.trustSummary {
                        privacyDataGroup(summary: summary)
                        advancedGroup(summary: summary)
                    } else {
                        ConnectionsRetryState(
                            state: state,
                            title: "Preparing privacy controls",
                            detail: "\(DistributionMode.appDisplayName) is reading local privacy settings and connection history."
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

    // MARK: Group 2 — Sources (notes, chat imports, connector library)

    private var sourcesGroup: some View {
        CortexDisclosure(
            isExpanded: $sourcesExpanded,
            systemImage: "tray.and.arrow.down",
            title: "Bring your memory in",
            detail: sourcesGroupDetail,
            help: "Import your AI chats, sign in to a source, or connect a folder on your Mac. Everything is distilled into memory that stays on this Mac."
        ) {
            VStack(alignment: .leading, spacing: CortexDesign.Space.lg) {
                // The three inbound lanes, ordered by how fast they get a user's data in. AI-chat
                // refugees are the biggest first-run cohort, so their one-tap guided export leads.
                // Then one-tap sign-in sources, then the local folder / notes path. Each lane owns
                // exactly one clear action set; nothing is a dead "reference only" tile.
                inboundLaneAIChats
                inboundLaneSignIn
                inboundLaneFromYourMac

                // The full connector catalog stays reachable as a quiet catch-all below the three
                // lanes, for anything not surfaced above (Slack, Jira, Raindrop, Zotero, Calendar).
                otherSourceConnections

                ImportDiffEntryCard(state: state)

                if notesHealth.isNeedsAttention
                    || state.sourceAccounts.contains(where: { $0.disconnected_at == nil && $0.needsAttention }) {
                    connectedNow
                }
            }
            .padding(.top, 12)
        }
        .connectionsGroupCard()
    }

    /// Lane 1 — the one-tap guided AI-chat export. The obvious first thing for a ChatGPT / Claude /
    /// Gemini refugee: a one-line label, then the card that deep-links to each vendor's export page,
    /// surfaces the auto-detected export as a one-tap Import pill, and takes a drag-drop / file pick.
    private var inboundLaneAIChats: some View {
        VStack(alignment: .leading, spacing: 10) {
            InboundLaneHeader(
                systemImage: "bubble.left.and.text.bubble.right",
                title: "Import your AI chats",
                detail: "Coming from ChatGPT, Claude, or Gemini? Bring that whole history in. One tap opens the export page, then \(DistributionMode.appDisplayName) imports it for you."
            )
            AIChatsImportCard(state: state)
        }
    }

    /// Lane 2 — sign in once and Cortex imports with your consent. Real OAuth / token sources only
    /// (Notion, Google Drive, GitHub, Outlook, Linear, Readwise, Limitless). A provider the hosted
    /// broker hasn't configured yet degrades to a calm "Available soon" tile, never a broken button.
    private var inboundLaneSignIn: some View {
        VStack(alignment: .leading, spacing: 10) {
            InboundLaneHeader(
                systemImage: "person.crop.circle.badge.checkmark",
                title: "Sign in to a source",
                detail: "Sign in once and \(DistributionMode.appDisplayName) imports your data with your consent. Read-only, and it stays on this Mac."
            )
            ConnectionsSignInSourcesSection(state: state)
        }
    }

    /// Lane 3 — sources already on this Mac. The notes folder (the primary local path) plus a guided
    /// Apple Notes card. No network, no account: choose once and Cortex keeps it synced locally.
    private var inboundLaneFromYourMac: some View {
        VStack(alignment: .leading, spacing: 10) {
            InboundLaneHeader(
                systemImage: "desktopcomputer",
                title: "From your Mac",
                detail: "Point \(DistributionMode.appDisplayName) at a notes folder on this Mac, or bring your Apple Notes in. Nothing leaves your device."
            )
            ConnectionsObsidianSection(state: state)
            AppleNotesImportCard(state: state)
        }
    }

    private var sourcesGroupDetail: String {
        if notesHealth.isNeedsAttention {
            return "A source needs attention"
        }
        return "\(connectedSourceCount) source\(connectedSourceCount == 1 ? "" : "s") connected: notes, chat imports, more"
    }

    // MARK: Group 1 — AI apps (the hero: MCP setup, live connectors, tool permissions)

    private var aiAppsGroup: some View {
        CortexDisclosure(
            isExpanded: $advancedExpanded,
            systemImage: "wand.and.stars",
            title: "Use your memory in AI apps",
            detail: aiAppsGroupDetail,
            help: "Connect Claude Desktop, Cursor, or any MCP app to read reviewed memory with citations. ChatGPT and Claude web reach it through a live connector. \(DistributionMode.appDisplayName) never copies your memory out."
        ) {
            VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
                ConnectionsAIToolsSection(state: state)
                ConnectionsMCPAccessSection(state: state)
            }
            .padding(.top, 12)
        }
        .connectionsGroupCard()
    }

    /// Live subtitle for the hero group, driven by the app-wide contract counts (kept fresh by the
    /// app's live-refresh loop). Leads with what's connected, then nudges toward detected apps, and
    /// otherwise pitches the live paths: MCP for desktop apps, a live connector for web chats.
    private var aiAppsGroupDetail: String {
        let connected = state.connectedAIIntegrationCount
        if connected > 0 {
            return "\(connected) app\(connected == 1 ? "" : "s") connected. Claude Desktop, ChatGPT, Cursor, and web chats."
        }
        let detected = state.detectedAIIntegrationCount
        if detected > 0 {
            return "\(detected) AI app\(detected == 1 ? "" : "s") detected on this Mac. Connect Claude Desktop, ChatGPT, Cursor, or web chats."
        }
        return "Connect Claude Desktop, ChatGPT, Cursor, and other AI apps live. Your memory stays in \(DistributionMode.appDisplayName)."
    }

    // MARK: Group 3 — Privacy & data (permissions, stored data, backups, export)

    private func privacyDataGroup(summary: TrustSummaryResponse) -> some View {
        CortexDisclosure(
            isExpanded: $privacyDataExpanded,
            systemImage: "lock.shield",
            title: "Privacy & data",
            detail: "Permissions, stored data, backups & export"
        ) {
            VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
                ConnectionsPrivacyDefaultsSection(state: state, summary: summary)
                privacySettings(summary: summary)
                storedData
                recoveryTools
            }
            .padding(.top, 12)
        }
        .connectionsGroupCard()
    }

    // MARK: Group 4 — Advanced (metrics, audit history, diagnostics)

    private func advancedGroup(summary: TrustSummaryResponse) -> some View {
        CortexDisclosure(
            isExpanded: $advancedGroupExpanded,
            systemImage: "wrench.and.screwdriver",
            title: "Advanced",
            detail: "Activity metrics, audit history, developer diagnostics"
        ) {
            VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
                activityMetrics
                auditHistory(summary: summary)
                developerDetails
            }
            .padding(.top, 12)
        }
        .connectionsGroupCard()
    }

    private var otherSourceConnections: some View {
        CortexDisclosure(
            isExpanded: $advancedSourcesExpanded,
            systemImage: "square.grid.2x2",
            title: "Browse all connectors",
            detail: advancedSourceDisclosureDetail,
            accessibilityTitle: "Browse all connectors: Connections library"
        ) {
            ConnectionsDirectSourcesSection(state: state)
                .padding(.top, 10)
        }
        .connectionsSubCard()
    }

    private var advancedSourceDisclosureDetail: String {
        let extraSources = extraConnectedSourceCount
        if extraSources > 0 {
            return "\(extraSources) extra source\(extraSources == 1 ? "" : "s") connected"
        }
        return "Everything else, connect any time"
    }

    private func privacySettings(summary: TrustSummaryResponse) -> some View {
        CortexDisclosure(
            isExpanded: $privacySettingsExpanded,
            systemImage: "shield.lefthalf.filled",
            title: "Memory permissions",
            detail: "Reviewed memory reads, new AI saves go to Review"
        ) {
            VStack(alignment: .leading, spacing: 14) {
                TrustPolicySection(state: state)
            }
            .padding(.top, 10)
        }
        .connectionsSubCard()
    }

    private var connectedNow: some View {
        CortexDisclosure(
            isExpanded: $connectedExpanded,
            systemImage: "checkmark.seal",
            title: "Connection status",
            detail: connectionStatusDetail
        ) {
            ConnectionsActiveSourcesSection(state: state)
                .padding(.top, 10)
        }
        .connectionsSubCard()
    }

    /// Manage stored data: once memory exists, removing all of it from a single source is a
    /// first-class privacy action, so it lives in Privacy & data — not buried in diagnostics.
    private var storedData: some View {
        CortexDisclosure(
            isExpanded: $storedDataExpanded,
            systemImage: "tray.full",
            title: "Manage stored data",
            detail: "See what each source has saved, delete it by source in one step"
        ) {
            ConnectionsStoredDataSection(state: state)
                .padding(.top, 10)
        }
        .connectionsSubCard()
        .onChange(of: storedDataExpanded) { expanded in
            if expanded {
                Task { await state.loadSourceStats() }
            }
        }
    }

    private var recoveryTools: some View {
        CortexDisclosure(
            isExpanded: $recoveryToolsExpanded,
            systemImage: "arrow.counterclockwise.circle",
            title: "Backups, recovery & export",
            detail: "Back up, restore, repair, or export your local memory"
        ) {
            VStack(alignment: .leading, spacing: 14) {
                SettingsDataRecoverySection(state: state)
                Divider()
                SettingsReliabilitySection(state: state)
                Divider()
                SettingsStatsSection(state: state)
            }
            .padding(.top, 10)
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
        CortexDisclosure(
            isExpanded: $activityMetricsExpanded,
            systemImage: "gauge.with.needle",
            title: "Activity & alerts",
            detail: "How tools use memory, and how often \(DistributionMode.appDisplayName) may interrupt"
        ) {
            ConnectionsToolUsageSection(state: state)
                .padding(.top, 10)
        }
        .connectionsSubCard()
    }

    /// Privacy history (per-source trust decisions + the audit log), hoisted out of the old
    /// developer mega-disclosure so nothing in a group nests more than one level deep.
    private func auditHistory(summary: TrustSummaryResponse) -> some View {
        CortexDisclosure(
            isExpanded: $sourceAuditExpanded,
            systemImage: "clock.arrow.circlepath",
            title: "Privacy history",
            detail: "Per-source trust decisions and the audit log"
        ) {
            VStack(alignment: .leading, spacing: 14) {
                TrustSourceSection(state: state, summary: summary)
                TrustAuditSection(events: state.auditEvents, refresh: {
                    Task { await state.loadTrust() }
                })
            }
            .padding(.top, 10)
        }
        .connectionsSubCard()
    }

    private var developerDetails: some View {
        CortexDisclosure(
            isExpanded: $developerDetailsExpanded,
            systemImage: "wrench.and.screwdriver",
            title: "Developer & diagnostics",
            detail: "Support details, engine status, updates"
        ) {
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

/// A CortexDesign-styled disclosure — the archive's answer to the stock `DisclosureGroup` that made
/// the whole sheet read as System Settings. A serif header with a wax tick and a connector glyph
/// opens a hairline rule; a wax-red chevron rotates as it expands. The content lives below the rule.
/// One reusable shape for all ~14 groups so the hierarchy speaks the app's language, not AppKit's.
private struct CortexDisclosure<Content: View>: View {
    @Binding var isExpanded: Bool
    let systemImage: String
    let title: String
    let detail: String
    /// Rendered next to the disclosure label (e.g. a per-connector brand mark). Defaults to the glyph.
    var help: String = ""
    var accessibilityTitle: String? = nil
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(CortexMotion.press) { isExpanded.toggle() }
            } label: {
                HStack(alignment: .center, spacing: 12) {
                    Image(systemName: systemImage)
                        .font(.title3)
                        .foregroundColor(CortexDesign.accent)
                        .frame(width: 28)
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(title)
                                .font(CortexDesign.Typography.title)
                                .foregroundColor(CortexDesign.ink)
                            // The wax tick — a small sealing-wax rect that opens the header rule.
                            RoundedRectangle(cornerRadius: 0.5)
                                .fill(CortexDesign.accent)
                                .frame(width: 6, height: 3)
                                .offset(y: -2)
                        }
                        if !detail.isEmpty {
                            Text(detail)
                                .font(CortexDesign.Typography.caption)
                                .foregroundColor(CortexDesign.inkSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                                .multilineTextAlignment(.leading)
                        }
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.down")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(CortexDesign.inkFaint)
                        .rotationEffect(.degrees(isExpanded ? 0 : -90))
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(help)
            .accessibilityLabel(accessibilityTitle ?? title)
            .accessibilityValue(isExpanded ? "Expanded" : "Collapsed")

            if isExpanded {
                content()
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
            return "\(obsidianReadiness.syncPlanDisplayTitle), synced automatically."
        }
        return "Choose the local source \(DistributionMode.appDisplayName) should keep synced automatically."
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
                    detail: "\(DistributionMode.appDisplayName) is checking available local notes."
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
                QuietState(title: "Notes connection unavailable", detail: "Restart \(DistributionMode.appDisplayName) after the private memory store is ready.")
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
        return "\(list) \(verb) available for direct sign-in in this build yet. Import them via file/export import."
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
                    detail: "\(DistributionMode.appDisplayName) is loading available read-only connections."
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

    private var tint: Color { connectorBrandTint(connector.id) }

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 8) {
                // A real per-connector brand mark: the connector glyph in its brand tint, pressed
                // into a soft brand-tinted well, so the shelf reads as a catalog of distinct services.
                ZStack {
                    RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                        .fill(tint.opacity(0.14))
                    Image(systemName: connectorLibraryIcon(connector.id))
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundColor(tint)
                }
                .frame(width: 34, height: 34)
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
            .frame(maxWidth: .infinity, minHeight: 130, alignment: .topLeading)
            .background(hovering ? CortexDesign.accentSoft : CortexDesign.panelBackground)
            .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
            .embossedBorder(radius: CortexDesign.Radius.md)
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

/// A per-connector brand tint for the library tiles — a real brand mark, not a wall of identical
/// wax-red glyphs. Deterministic (a fixed table keyed on connector id, never `.random`); connectors
/// with no known brand fall back to the archive's ink so the tile still reads calm and on-palette.
private func connectorBrandTint(_ id: String) -> Color {
    switch id {
    case "calendar": return Color(red: 0.85, green: 0.28, blue: 0.24)   // red
    case "gmail": return Color(red: 0.83, green: 0.19, blue: 0.16)      // gmail red
    case "outlook": return Color(red: 0.00, green: 0.44, blue: 0.78)    // outlook blue
    case "google-drive": return Color(red: 0.13, green: 0.52, blue: 0.29) // drive green
    case "zotero": return Color(red: 0.80, green: 0.20, blue: 0.16)     // zotero red
    case "notion": return CortexDesign.ink                              // notion mono
    case "slack": return Color(red: 0.36, green: 0.16, blue: 0.42)      // aubergine
    case "github": return CortexDesign.ink                             // github mono
    case "readwise": return Color(red: 0.20, green: 0.44, blue: 0.86)   // readwise blue
    case "raindrop": return Color(red: 0.13, green: 0.53, blue: 0.90)   // raindrop blue
    case "linear": return Color(red: 0.36, green: 0.40, blue: 0.90)     // linear indigo
    case "jira": return Color(red: 0.14, green: 0.44, blue: 0.90)       // jira blue
    default: return CortexDesign.accent
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

/// A calm lane heading for the "Bring your memory in" surface: a wax glyph, a serif title, and one
/// honest line about what the lane does. Groups the three inbound paths so the surface reads as
/// "how do I want to get my data in?" at a glance, without turning each lane into its own disclosure.
private struct InboundLaneHeader: View {
    let systemImage: String
    let title: String
    let detail: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                    .fill(CortexDesign.accentSoft)
                Image(systemName: systemImage)
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(CortexDesign.accent)
            }
            .frame(width: 32, height: 32)
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(CortexDesign.Typography.title)
                    .foregroundColor(CortexDesign.ink)
                Text(detail)
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
            Spacer(minLength: 0)
        }
    }
}

/// Import your ChatGPT / Claude / Gemini history. There's no live sign-in for these (the providers
/// don't offer an import API), so the only real path is a guided export: one tap deep-links to the
/// vendor's export page, Cortex auto-detects the downloaded file and surfaces a one-tap Import pill,
/// and drag-drop / file pick are the fallback. Imported content is trusted and usable immediately.
private struct AIChatsImportCard: View {
    @ObservedObject var state: AppState
    @State private var isTargeted = false
    @State private var dropZoneHovering = false
    // The export walkthrough starts open until an export shows up — requesting the export is
    // where people stall, not the drop zone. Once one is detected or imported, it tucks away.
    @State private var guideExpanded = true

    private let steps = [
        "Tap your provider below. It opens the export page in your browser, already on the right screen.",
        "Ask for the export. The provider emails you a download link in a few minutes (ChatGPT, Claude, and Gemini all send it by email).",
        "Download the file, then drop it here or click Choose export file. \(DistributionMode.appDisplayName) often spots it in Downloads on its own."
    ]

    private var hasCompletedImport: Bool {
        state.importHistory.contains { $0.deleted_at == nil && $0.saved > 0 }
    }

    /// P6: vendors the user has tapped an export for but whose file hasn't landed yet, newest first.
    /// A request is considered "still waiting" for up to 24h — long enough to cover the provider's
    /// email delay, short enough that a stale request from days ago doesn't linger.
    private var waitingVendors: [(vendor: String, requestedAt: Date)] {
        let cutoff = Date().addingTimeInterval(-24 * 60 * 60)
        return state.exportRequestState
            .filter { $0.value > cutoff }
            .sorted { $0.value > $1.value }
            .map { (vendor: $0.key, requestedAt: $0.value) }
    }

    /// A human "a few minutes ago"-style label for when the export was requested.
    private func waitingRelative(_ date: Date) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // FRONT AND CENTER: the auto-detected export. If Cortex already found the file in
            // Downloads, importing is a single prominent primary tap and nothing else is needed.
            if let summary = state.detectedExportSummary {
                HStack(alignment: .center, spacing: 10) {
                    Image(systemName: "sparkles")
                        .font(.title3)
                        .foregroundColor(CortexDesign.accent)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(summary)
                            .font(.callout)
                            .fontWeight(.semibold)
                            .foregroundColor(CortexDesign.ink)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("Ready to import. It's usable the moment it lands.")
                            .font(.caption)
                            .foregroundColor(CortexDesign.inkSecondary)
                    }
                    Spacer(minLength: 8)
                    if state.importInFlight {
                        ProgressView().controlSize(.small)
                    } else {
                        CortexButton(title: "Import now", systemImage: "square.and.arrow.down", role: .primary, size: .regular) {
                            state.importDetectedExports()
                        }
                    }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: CortexDesign.Radius.md).fill(CortexDesign.accentSoft))
                .overlay(RoundedRectangle(cornerRadius: CortexDesign.Radius.md).stroke(CortexDesign.accent.opacity(0.3), lineWidth: 1))
            } else if !waitingVendors.isEmpty {
                // P6: after the user requested an export, flip to a calm "waiting" state until the file
                // lands. The watcher imports it automatically, so there's nothing more to click here.
                waitingBanner
            }

            // ONE TAP PER VENDOR: deep-link straight to each provider's export page. This is the step
            // people abandon, so it's the most prominent thing when no export has been detected yet.
            VStack(alignment: .leading, spacing: 8) {
                Text("Get your export in one tap")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(CortexDesign.inkSecondary)
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 8)], spacing: 8) {
                    exportVendorButton("ChatGPT", systemImage: "bubble.left.and.bubble.right", urlString: "https://chatgpt.com/#settings/DataControls")
                    exportVendorButton("Claude", systemImage: "sparkle", urlString: "https://claude.ai/settings/data-privacy-controls")
                    // Gemini history lives under Takeout's "My Activity" (the standalone "Gemini"
                    // product is Gems, not chats), so open My Activity pre-selected; the caption below
                    // tells the user to narrow it to "Gemini Apps".
                    exportVendorButton("Gemini", systemImage: "diamond", urlString: "https://takeout.google.com/settings/takeout/custom/my_activity")
                }
                Text("The provider emails you a download link, usually within a few minutes. Grab the file, then \(DistributionMode.appDisplayName) takes it from there. For Gemini, pick \u{201C}My Activity\u{201D} \u{2192} \u{201C}Gemini Apps\u{201D} in Takeout.")
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkFaint)
                    .fixedSize(horizontal: false, vertical: true)
            }

            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6]))
                .foregroundColor(isTargeted || dropZoneHovering ? CortexDesign.accent : CortexDesign.hairline)
                .frame(height: 66)
                .overlay(
                    HStack(spacing: 8) {
                        if state.importInFlight { ProgressView().scaleEffect(0.7) }
                        Text(state.importInFlight ? "Importing…" : "Already have the file? Drop it here, or")
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
                // Guided walkthrough: the highlight strolls through the steps on a loop while the
                // disclosure is open, and clicking a step jumps it there. U-CONN9: the per-vendor
                // deep-links live once, in the prominent grid above — step 1 just points back up to
                // them instead of repeating the same three links (which read as a second, competing
                // export affordance).
                GuidedStepWalkthrough(steps: steps, isActive: guideExpanded, textFont: .caption) { index in
                    if index == 0 {
                        Text("Use the provider buttons above ↑")
                            .font(CortexDesign.Typography.stamp)
                            .kerning(0.5)
                            .foregroundColor(CortexDesign.inkFaint)
                    }
                }
                .padding(.top, 6)
            } label: {
                Text("Walk me through it")
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
                // Auto-detect is the whole point of this card — a first-time user with a ChatGPT
                // export sitting in Downloads should get the one-tap "Found your export → Import"
                // path WITHOUT having imported once already (the old `hasCompletedImport` gate made
                // that path unreachable for exactly the first-timer it's for). `detectAvailableExports`
                // is a no-op under the App Store sandbox (it early-returns), so the sandboxed
                // TCC-prompt cascade the gate was guarding against cannot happen there; on a direct
                // build the detector scan is the expected, in-context action for opening this card.
                await state.detectAvailableExports()
                // Collapse the walkthrough only once an export exists or chats have landed;
                // otherwise it stays open so the path in is visible without a click.
                if state.detectedExportSummary != nil || hasCompletedImport {
                    guideExpanded = false
                }
            }
        }
    }

    /// A prominent per-vendor deep-link tile: opens that provider's export page in one tap AND records
    /// the request (P6) so the card can flip to a calm "waiting for your <vendor> export" state until
    /// the file lands and the watcher imports it. Requesting the export is where people stall, so this
    /// is the biggest, most obvious action in the card.
    private func exportVendorButton(_ name: String, systemImage: String, urlString: String) -> some View {
        let isWaiting = state.exportRequestState[name] != nil
        return Button {
            if let url = URL(string: urlString) {
                NSWorkspace.shared.open(url)
            }
            // P6: remember we asked for this vendor's export, keyed by the display name so the pill and
            // the waiting banner can name it. The watcher imports the file automatically once it lands.
            state.exportRequestState[name] = Date()
        } label: {
            HStack(spacing: 8) {
                Image(systemName: systemImage)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(CortexDesign.accent)
                Text(name)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(CortexDesign.ink)
                Spacer(minLength: 4)
                // P6: once requested, the tile shows a quiet "waiting" hourglass instead of the
                // open-page arrow, so the user knows Cortex is watching for that vendor's file.
                Image(systemName: isWaiting ? "hourglass" : "arrow.up.right")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(isWaiting ? CortexDesign.accent : CortexDesign.inkSecondary)
            }
            .padding(.horizontal, 12)
            .frame(maxWidth: .infinity, minHeight: 40, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous).fill(CortexDesign.cardBackground))
            .embossedBorder(radius: CortexDesign.Radius.md)
        }
        .buttonStyle(.plain)
        .help(isWaiting
              ? "Waiting for your \(name) export. Reopen the export page any time; \(DistributionMode.appDisplayName) imports the file automatically when it lands."
              : "Open the \(name) export page in your browser")
        .accessibilityLabel(isWaiting ? "Waiting for your \(name) export; reopen the export page" : "Open the \(name) export page")
    }

    /// P6: the calm "waiting for your export" banner shown after a per-vendor export request, until
    /// the file lands (at which point the detected-export card takes over). Names each pending vendor
    /// and reassures the user that Cortex imports it automatically, so there's nothing more to click.
    private var waitingBanner: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "hourglass")
                .font(.title3)
                .foregroundColor(CortexDesign.accent)
            VStack(alignment: .leading, spacing: 2) {
                Text(waitingHeadline)
                    .font(.callout)
                    .fontWeight(.semibold)
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text("We will import it automatically when it lands. The provider usually emails the link within a few minutes; nothing else to do here.")
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let first = waitingVendors.first {
                    Text("Requested \(waitingRelative(first.requestedAt))")
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.6)
                        .foregroundColor(CortexDesign.inkFaint)
                }
            }
            Spacer(minLength: 8)
            // A quiet way to dismiss a request that never produced a file (e.g. the user changed
            // their mind), so the waiting state can't get stuck.
            CortexButton(title: "Not waiting", systemImage: "xmark", role: .ghost, size: .small) {
                for entry in waitingVendors {
                    state.exportRequestState[entry.vendor] = nil
                }
            }
            .help("Stop waiting for the export you requested.")
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: CortexDesign.Radius.md).fill(CortexDesign.accentSoft.opacity(0.6)))
        .overlay(RoundedRectangle(cornerRadius: CortexDesign.Radius.md).stroke(CortexDesign.accent.opacity(0.25), lineWidth: 1))
    }

    /// "Waiting for your ChatGPT export…" — names one vendor, or lists a couple when several are
    /// pending, so the per-vendor detail the plan calls for is right in the headline.
    private var waitingHeadline: String {
        let names = waitingVendors.map(\.vendor)
        switch names.count {
        case 0:
            return "Waiting for your export…"
        case 1:
            return "Waiting for your \(names[0]) export…"
        case 2:
            return "Waiting for your \(names[0]) and \(names[1]) exports…"
        default:
            let head = names.dropLast().joined(separator: ", ")
            return "Waiting for your \(head), and \(names[names.count - 1]) exports…"
        }
    }
}

/// Lane 2's tiles: the real "sign in once, import with consent" sources — one prominent tap each.
/// This is a curated shelf of the connectors that genuinely support account sign-in (managed OAuth,
/// GitHub device flow, or a pasted read-only token), so a user can go straight from "I use Notion"
/// to a consent screen without hunting the full library. It reuses the SAME connect dispatch the
/// library uses (one code path, no drift) and degrades honestly: a managed-OAuth provider the hosted
/// broker hasn't configured yet shows a calm "Available soon" tile, never a broken button.
private struct ConnectionsSignInSourcesSection: View {
    @ObservedObject var state: AppState
    @State private var selectedTokenConnector: SourceConnectorCatalogItem?

    /// The curated sign-in set, in priority order. Notion leads (best consent UX: a page picker),
    /// then the Google/GitHub/Microsoft sign-ins, then the token-issue sources. Only ids that are
    /// actually in the catalog and wired render, so the shelf can't drift into dead tiles.
    private static let signInSourceIDs = [
        "notion", "google-drive", "github", "outlook", "gmail", "linear", "readwise", "limitless",
    ]

    private var signInConnectors: [SourceConnectorCatalogItem] {
        Self.signInSourceIDs.compactMap { id in
            state.sourceConnectorCatalog.first { $0.id == id }
        }
        .filter { state.isDirectConnectorSyncWired($0) }
        // App Store builds are local-first: outbound HTTPS is stripped, so account sign-in can't
        // sync. Show nothing rather than a dead tile (mirrors the library's filter). The notes +
        // export + Apple Notes lanes, which work locally, still carry the surface.
        .filter { _ in !DistributionMode.isAppStore }
    }

    /// Whether this connector can be connected right now (a live consent path exists). A managed-OAuth
    /// connector whose provider isn't configured on the broker AND that has no pasted-token fallback
    /// is NOT connectable yet, so its tile degrades to "Available soon" instead of a broken button.
    private func isConnectable(_ connector: SourceConnectorCatalogItem) -> Bool {
        if connector.connectionSetup?.supportsDeviceFlow == true { return true }
        if connector.connectionSetup?.supportsManagedOAuth == true {
            if state.managedOAuthIsConfigured(connector) { return true }
            return hasUsableTokenFallback(connector)
        }
        // Pure token connectors (Linear, Readwise, Limitless) are always connectable via their setup.
        return !(connector.connectionSetup?.credential_fields.isEmpty ?? true)
    }

    private func hasUsableTokenFallback(_ connector: SourceConnectorCatalogItem) -> Bool {
        !(connector.connectionSetup?.credential_fields.isEmpty ?? true)
    }

    private func isConnected(_ connector: SourceConnectorCatalogItem) -> Bool {
        state.activeSourceAccounts.contains { account in
            account.source == connector.id || (connector.source_ids ?? []).contains(account.source)
        }
    }

    /// A short honest verb for each tile: "Sign in" for a real sign-in, "Connect" for token sources,
    /// "Synced" once connected, "Available soon" when the provider isn't configured yet.
    private func cta(_ connector: SourceConnectorCatalogItem) -> String {
        if isConnected(connector) { return "Synced" }
        if !isConnectable(connector) { return "Available soon" }
        if connector.connectionSetup?.supportsDeviceFlow == true { return "Sign in" }
        if connector.connectionSetup?.supportsManagedOAuth == true, state.managedOAuthIsConfigured(connector) {
            return "Sign in"
        }
        return "Connect"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if state.sourceConnectorCatalog.isEmpty {
                ConnectionsRetryState(
                    state: state,
                    title: "Checking sign-in sources",
                    detail: "\(DistributionMode.appDisplayName) is loading the sources you can sign in to."
                ) {
                    Task { await state.loadSourceConnectivity() }
                }
            } else if signInConnectors.isEmpty {
                QuietState(
                    title: "Sign-in sources unavailable in this build",
                    detail: "Bring your memory in with an AI-chat export or a notes folder on your Mac instead."
                )
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 200), spacing: 10)], spacing: 10) {
                    ForEach(signInConnectors) { connector in
                        SignInSourceTile(
                            connector: connector,
                            connectable: isConnectable(connector),
                            connected: isConnected(connector),
                            starting: state.connectorOAuthStartingIDs.contains(connector.id)
                                || state.connectorSyncingIDs.contains(connector.id),
                            cta: cta(connector)
                        ) {
                            connectAction(connector)
                        }
                    }
                }
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

    /// The one connect path, shared with the Connections library's `libraryAction` (device flow →
    /// managed OAuth → token sheet). A tile that isn't connectable never dispatches: the button is
    /// disabled upstream, so tapping "Available soon" is a calm no-op, not a broken request.
    private func connectAction(_ connector: SourceConnectorCatalogItem) {
        guard isConnectable(connector) else { return }
        if connector.connectionSetup?.supportsDeviceFlow == true {
            state.startGitHubDeviceFlow(connector)
            return
        }
        if connector.connectionSetup?.supportsManagedOAuth == true {
            if state.managedOAuthIsConfigured(connector) {
                state.startManagedOAuthConnector(connector)
                return
            }
            // Managed OAuth advertised but not configured on the broker: fall through to the durable
            // integration-token setup when the connector ships one (Notion), else do nothing.
            if hasUsableTokenFallback(connector) {
                selectedTokenConnector = connector
            }
            return
        }
        selectedTokenConnector = connector
    }
}

/// One prominent one-tap sign-in tile. Real brand mark, the source name, and a single honest verb.
/// A not-yet-configured provider renders calm and disabled with "Available soon"; a connected source
/// wears a moss check. Never a broken button.
private struct SignInSourceTile: View {
    let connector: SourceConnectorCatalogItem
    let connectable: Bool
    let connected: Bool
    let starting: Bool
    let cta: String
    let action: () -> Void
    @State private var hovering = false

    private var tint: Color { connectorBrandTint(connector.id) }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 11) {
                ZStack {
                    RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                        .fill(tint.opacity(connectable ? 0.14 : 0.08))
                    Image(systemName: connectorLibraryIcon(connector.id))
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundColor(connectable ? tint : CortexDesign.inkFaint)
                }
                .frame(width: 34, height: 34)
                VStack(alignment: .leading, spacing: 2) {
                    Text(connector.name)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(CortexDesign.ink)
                        .lineLimit(1)
                    Text(subtitle)
                        .font(.caption2)
                        .foregroundColor(subtitleColor)
                        .lineLimit(1)
                }
                Spacer(minLength: 4)
                if starting {
                    ProgressView().controlSize(.small)
                } else if connected {
                    Image(systemName: "checkmark.seal.fill")
                        .foregroundColor(CortexDesign.sealMoss)
                } else if connectable {
                    Image(systemName: "arrow.right.circle.fill")
                        .foregroundColor(CortexDesign.accent)
                } else {
                    Image(systemName: "clock")
                        .foregroundColor(CortexDesign.inkFaint)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(hovering && connectable ? CortexDesign.accentSoft : CortexDesign.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
            .embossedBorder(radius: CortexDesign.Radius.md)
            .opacity(connectable || connected ? 1 : 0.7)
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .animation(.easeOut(duration: 0.12), value: hovering)
        .disabled((!connectable && !connected) || starting)
        .help(connectable
              ? "Sign in to \(connector.name) and import your data, read-only"
              : "One-tap sign-in for \(connector.name) is coming soon")
        .accessibilityLabel(connectable ? "Sign in to \(connector.name)" : "\(connector.name): sign-in coming soon")
    }

    private var subtitle: String {
        if connected { return "Synced" }
        if starting { return "Opening sign-in…" }
        return cta
    }

    private var subtitleColor: Color {
        if connected { return CortexDesign.sealMoss }
        if !connectable { return CortexDesign.inkFaint }
        return CortexDesign.inkSecondary
    }
}

/// Lane 3's second card: bring your Apple Notes in. There's no Apple Notes import API, so the honest
/// path is a short guided export: open Notes, export the notes you want, then drop the file here.
/// It reuses the SAME import plumbing the AI-chat card uses (importAIChatExport / importFromPath), so
/// there's one import path and the file lands as trusted, usable memory.
private struct AppleNotesImportCard: View {
    @ObservedObject var state: AppState
    @State private var isTargeted = false
    @State private var guideExpanded = false

    private let steps = [
        "Open the Notes app, select the notes you want, then use the File menu and pick Export as PDF (or use the Shortcuts app to save them as text).",
        "Save the exported file somewhere easy to find, like your Desktop or Downloads.",
        "Drop the file here, or click Choose export file. \(DistributionMode.appDisplayName) distills it into cited memory.",
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Image(systemName: "note.text")
                    .font(.title3)
                    .foregroundColor(CortexDesign.accent)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Bring in your Apple Notes")
                        .font(.headline)
                        .foregroundColor(CortexDesign.ink)
                    Text("Export the notes you want, then \(DistributionMode.appDisplayName) imports them. Nothing leaves your Mac.")
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }

            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6]))
                .foregroundColor(isTargeted ? CortexDesign.accent : CortexDesign.hairline)
                .frame(height: 58)
                .overlay(
                    HStack(spacing: 8) {
                        if state.importInFlight { ProgressView().scaleEffect(0.7) }
                        Text(state.importInFlight ? "Importing…" : "Drop your exported notes here, or")
                            .font(.callout).foregroundColor(CortexDesign.inkSecondary)
                        if !state.importInFlight {
                            CortexButton(title: "Choose export file…", systemImage: "folder.badge.plus", role: .secondary, size: .small) {
                                state.importAIChatExport()
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

            DisclosureGroup(isExpanded: $guideExpanded) {
                GuidedStepWalkthrough(steps: steps, isActive: guideExpanded, textFont: .caption) { _ in
                    EmptyView()
                }
                .padding(.top, 6)
            } label: {
                Text("How do I export from Apple Notes?")
            }
            .font(.caption)
            .foregroundColor(CortexDesign.inkSecondary)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: CortexDesign.Radius.md).fill(connectionsPanelBackground))
        .overlay(RoundedRectangle(cornerRadius: CortexDesign.Radius.md).stroke(CortexDesign.hairline, lineWidth: 1))
    }
}

/// A quiet entry, right beside the ChatGPT/Claude importer, into "What the AIs think of you" — the
/// import-diff surface. It doesn't import; it opens the compare sheet where a vendor memory export
/// is checked, with citations, against the Cortex Mirror. One tap, one job.
private struct ImportDiffEntryCard: View {
    @ObservedObject var state: AppState
    @State private var hovering = false

    var body: some View {
        Button {
            state.openImportDiff()
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "sparkle.magnifyingglass")
                    .font(.title3)
                    .foregroundColor(CortexDesign.accent)
                VStack(alignment: .leading, spacing: 2) {
                    Text("See what the AIs think of you")
                        .font(.headline)
                        .foregroundColor(CortexDesign.ink)
                    Text("Compare a ChatGPT, Claude, or Gemini memory export against your \(DistributionMode.appDisplayName), with citations.")
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
                Spacer(minLength: 0)
                Image(systemName: "arrow.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(CortexDesign.inkSecondary)
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: CortexDesign.Radius.md).fill(hovering ? CortexDesign.accentSoft.opacity(0.5) : connectionsPanelBackground))
            .overlay(RoundedRectangle(cornerRadius: CortexDesign.Radius.md).stroke(CortexDesign.hairline, lineWidth: 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .help("Open “What the AIs think of you”: compare an AI memory export against your \(DistributionMode.appDisplayName).")
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

    /// Whether there is a real connection to fully disconnect/forget: an attached backend account
    /// (active OR paused) or a stored credential. This backs the always-available disconnect path so
    /// a token/OAuth source that synced zero items — which has no removableImport — can still be
    /// fully removed instead of only paused.
    private var canDisconnect: Bool {
        activeAccount != nil || pausedAccount != nil || hasStoredConfig
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
                    Text("This removes the review items and memory \(DistributionMode.appDisplayName) synced from \(connector.name). Approved memory from this connection is deleted and can only be recovered from a backup. To stop syncing while keeping memory, use Pause instead.")
                }
            } else if canDisconnect {
                // A connector that's connected/configured but has no deletable import (an OAuth or
                // token source that synced zero items yet) still needs a full disconnect — Pause
                // alone leaves the account attached and the credential stored, so the user could
                // never truly forget the source. This disconnects the backend account (if any) AND
                // forgets the saved credential so the source is fully removed. Any memory it did
                // sync is removed from Privacy & data → Manage stored data (delete by source).
                CortexButton(title: "Disconnect", systemImage: "xmark.circle", role: .destructive, size: .regular) {
                    confirmRemove = true
                }
                .help("Fully disconnect this source and forget its saved connection. To stop syncing while keeping the connection, use Pause instead.")
                .disabled(state.isBusy || isSyncing || isOAuthStarting)
                .confirmationDialog(
                    "Disconnect \(connector.name)?",
                    isPresented: $confirmRemove,
                    titleVisibility: .visible
                ) {
                    Button("Disconnect \(connector.name)", role: .destructive) {
                        if activeAccount != nil {
                            state.pauseDirectConnectorSync(connector)
                        }
                        state.forgetDirectConnectorConfig(connector)
                    }
                    Button("Cancel", role: .cancel) {}
                } message: {
                    Text("\(DistributionMode.appDisplayName) stops syncing \(connector.name) and forgets its saved connection, so it won't resume on its own. Memory already synced from this source is kept: remove it from Privacy & data → Manage stored data. To pause without forgetting the connection, use Pause instead.")
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
            return "Sign in with \(managedOAuthProviderName), read-only."
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
                        QuietState(title: "Setup contract unavailable", detail: "Update \(DistributionMode.appDisplayName) and try this connection again.")
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
            return "\(DistributionMode.appDisplayName) reads this local source and sends useful memory to Review first."
        case "native-token-connector":
            return "\(DistributionMode.appDisplayName) uses your token for read-only sync and sends useful memory to Review first."
        default:
            return "\(DistributionMode.appDisplayName) sends useful memory to Review first, with citations preserved."
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
                    discoveryMessages[field.name] = "Select the \(field.displayLabel.lowercased()) \(DistributionMode.appDisplayName) should keep synced."
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
    /// Context-file ("Sync to CLAUDE.md") block-preview disclosure state.
    @State private var contextBlockPreviewExpanded = false
    /// U-CONN3: whether to show every connected tool row or just the first few.
    @State private var showAllConnected = false
    /// U-CONN4: which copy button most recently fired, so its label can flip to "Copied" for 2s.
    @State private var copiedCluster: CopiedCluster?
    /// U-CONN6: a pre-targeted Connect-an-app wizard, presented locally so "Add a connector" can land
    /// straight on the remote-connector path without touching the shared wizard's presentation.
    @State private var remoteConnectorWizard = false

    /// U-CONN3: how many connected rows to show collapsed before "Show all N".
    private static let connectedPreviewLimit = 3

    /// U-CONN4: the copy clusters that show an inline "Copied" confirmation.
    private enum CopiedCluster: Equatable { case toolConfig, extensionPairing, apiDetails }

    private func flashCopied(_ cluster: CopiedCluster) {
        withAnimation(.easeOut(duration: 0.15)) { copiedCluster = cluster }
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            if copiedCluster == cluster {
                withAnimation(.easeOut(duration: 0.2)) { copiedCluster = nil }
            }
        }
    }

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
                detail: "Every connection is live: your tools read \(DistributionMode.appDisplayName) on demand. Your memory only ever lives here."
            )
            .help("Claude Desktop, Cursor and other MCP apps read reviewed memory with citations. ChatGPT and Claude web reach it through a live connector. \(DistributionMode.appDisplayName) never copies your memory out.")

            // The hero front door, first: the guided wizard opens as a top-level sheet.
            connectWizardEntry

            // The web-chat live path right under the wizard, so a user who isn't on a desktop app
            // sees the ChatGPT/Claude-web connector route immediately instead of scrolling past MCP.
            browserAssistantRow

            desktopAppsStatusRow

            if DistributionMode.isAppStore {
                // Local-first App Store builds can't write into other apps' config files
                // (sandbox) and don't auto-install. Instead of a dead end, guide the exact
                // manual paste: the connection JSON in a copyable block plus numbered steps.
                ConnectionsGuidedMCPSetup(state: state)
            }

            universalReachRow

            contextFilesRow

            if !connectedIntegrations.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    // U-CONN3: don't silently cap connected tools at 3. Show a preview, then let the
                    // user reveal the rest so a 4th-plus connected tool isn't invisible here.
                    let shown = showAllConnected
                        ? connectedIntegrations
                        : Array(connectedIntegrations.prefix(Self.connectedPreviewLimit))
                    ForEach(shown) { integration in
                        connectedIntegrationRow(integration)
                    }
                    if connectedIntegrations.count > Self.connectedPreviewLimit {
                        CortexButton(
                            title: showAllConnected
                                ? "Show fewer"
                                : "Show all \(connectedIntegrations.count) connected",
                            systemImage: showAllConnected ? "chevron.up" : "chevron.down",
                            role: .ghost,
                            size: .small
                        ) {
                            withAnimation(CortexMotion.press) { showAllConnected.toggle() }
                        }
                    }
                }
            }
        }
        // U-CONN3: seed the inline test badges from the app-wide cache so a connected tool that was
        // tested earlier (here or in the wizard) still shows its last result after the sheet reopens.
        .onAppear {
            for (id, result) in state.lastToolTestResults where testResults[id] == nil {
                testResults[id] = result
            }
        }
        // U-CONN6: the pre-targeted Connect-an-app wizard for the web-chat / remote-connector path.
        .sheet(isPresented: $remoteConnectorWizard) {
            ConnectAppWizard(state: state, preselectToolID: "chatgpt", preselectKind: .remoteMCP)
        }
    }

    /// The desktop MCP path (Claude Desktop, Cursor, and other MCP apps): live status plus the
    /// right copy/enable action for this build. Sits under the web memory-pack row so both paths
    /// read as peers, with the guided wizard above as the easiest way to do either.
    private var desktopAppsStatusRow: some View {
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
                Text("Desktop apps".uppercased())
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
                // U-CONN4: confirm the copy in place, so a silent clipboard write isn't the only feedback.
                CortexButton(
                    title: copiedCluster == .toolConfig ? "Copied" : "Copy setup config",
                    systemImage: copiedCluster == .toolConfig ? "checkmark" : "doc.on.doc",
                    role: .secondary,
                    size: .large
                ) {
                    state.copyMCPConfig()
                    flashCopied(.toolConfig)
                }
                .help("Copies the tool configuration to paste into your AI app's settings.")
            } else {
                // U-CONN7: nothing detected or connected yet. Handing out a config blob with no
                // destination was a dead end, so lead with the guided wizard (pick → connect →
                // verify) and keep the raw config copy as a quiet secondary for power users.
                VStack(alignment: .trailing, spacing: 8) {
                    CortexButton(title: "Connect an app", systemImage: "wand.and.stars", role: .primary, size: .large) {
                        state.presentConnectToolsWizard()
                    }
                    .help("Opens the guided wizard: pick a tool, connect it, and verify it can reach your memory.")
                    CortexButton(
                        title: copiedCluster == .toolConfig ? "Copied" : "Copy tool config",
                        systemImage: copiedCluster == .toolConfig ? "checkmark" : "doc.on.doc",
                        role: .ghost,
                        size: .small
                    ) {
                        state.copyMCPConfig()
                        flashCopied(.toolConfig)
                    }
                    .help("Copies the \(DistributionMode.appDisplayName) MCP configuration to paste into Claude Desktop or another compatible tool.")
                }
            }
        }
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    /// The hero of the whole sheet: the guided "Connect an app" front door. One wax-red primary
    /// opens the step-by-step wizard (pick, connect, verify, done) as a top-level sheet via the
    /// app-wide contract (`state.presentConnectToolsWizard()`), the same wizard Home presents. It is
    /// the first and most prominent thing here because using your memory in Claude Desktop, ChatGPT,
    /// and Cursor is the point. The per-tool tiles below stay as the manual path for power users.
    private var connectWizardEntry: some View {
        HStack(alignment: .center, spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(CortexDesign.accent.opacity(0.13))
                Image(systemName: "wand.and.stars")
                    .font(.system(size: 30, weight: .semibold))
                    .foregroundColor(CortexDesign.accent)
            }
            .frame(width: 64, height: 64)

            VStack(alignment: .leading, spacing: 4) {
                Text("Start here".uppercased())
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.accent)
                Text("Use your memory in Claude, ChatGPT & Cursor")
                    .font(.title2)
                    .fontWeight(.semibold)
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text(wizardEntryDetail)
                    .font(.callout)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 8)

            CortexButton(title: "Connect an app", systemImage: "wand.and.stars", role: .primary, size: .large) {
                state.presentConnectToolsWizard()
            }
            .help("Opens a step-by-step wizard: pick a tool, copy its connection, and test that it can reach your memory.")
        }
        .padding(18)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.accent.opacity(0.35), lineWidth: 1.5))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    /// Live status line under the hero, driven by the app-wide contract counts (kept fresh by the
    /// app's live-refresh loop, so no local Timer here). It names how many AI tools are connected,
    /// nudges toward apps detected on this Mac, and otherwise pitches the guided path.
    private var wizardEntryDetail: String {
        let connected = state.connectedAIIntegrationCount
        if connected > 0 {
            let detected = state.detectedAIIntegrationCount
            if detected > 0 {
                return "\(connected) app\(connected == 1 ? "" : "s") connected. \(detected) more detected on this Mac, connect \(detected == 1 ? "it" : "them") too."
            }
            return "\(connected) app\(connected == 1 ? "" : "s") connected. Add another any time, no config files to hunt for."
        }
        let detected = state.detectedAIIntegrationCount
        if detected > 0 {
            return "\(detected) AI app\(detected == 1 ? "" : "s") detected on this Mac. Pick one, copy its connection, and verify it works."
        }
        return "Pick a tool, copy its connection, and verify it works. No config files to hunt for."
    }

    /// The browser extension + universal API paths, previously reachable ONLY from the menu-bar
    /// right-click menu — invisible to anyone who never right-clicks the status item. Surfacing
    /// them here puts every "use Cortex anywhere" path on the same screen as MCP + memory packs.
    private var universalReachRow: some View {
        VStack(alignment: .leading, spacing: 10) {
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

                // U-CONN4: confirm each copy in place (mirrors the device-code sheet's "Copied" flip),
                // and add an explicit paste next-step for the browser-extension pairing token.
                VStack(alignment: .trailing, spacing: 8) {
                    CortexButton(
                        title: copiedCluster == .extensionPairing ? "Token copied" : "Connect extension",
                        systemImage: copiedCluster == .extensionPairing ? "checkmark" : "puzzlepiece.extension",
                        role: .secondary,
                        size: .small
                    ) {
                        // #6: only flip to "Token copied" after the token is actually minted + copied.
                        state.pairBrowserExtension(onPaired: { flashCopied(.extensionPairing) })
                    }
                    .help("Mints a read-only pairing token and copies it for the \(DistributionMode.appDisplayName) browser extension.")

                    CortexButton(
                        title: copiedCluster == .apiDetails ? "Copied" : "Copy API details",
                        systemImage: copiedCluster == .apiDetails ? "checkmark" : "curlybraces",
                        role: .secondary,
                        size: .small
                    ) {
                        // #6: only flip to "Copied" after the details are actually on the clipboard.
                        state.copyUniversalAPIConnectionInfo(onCopied: { flashCopied(.apiDetails) })
                    }
                    .help("Copies the base URL, token, and tool-schema endpoints for SDKs and any function-calling app.")
                }
            }

            // The concrete next-step for the paired extension, shown once the pairing token is copied.
            if copiedCluster == .extensionPairing {
                HStack(alignment: .top, spacing: 7) {
                    Image(systemName: "arrow.turn.down.right")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(CortexDesign.accent)
                    Text("Paste this token into the \(DistributionMode.appDisplayName) browser extension's Options, then click \(DistributionMode.appDisplayName) on a supported site.")
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
            }
        }
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    /// "Sync to CLAUDE.md": keeps a file the user ALREADY hand-maintains (CLAUDE.md / AGENTS.md /
    /// .cursorrules / GEMINI.md) in sync with the cited profile — meeting the developer ICP inside
    /// their existing manual-memory workaround instead of asking them to adopt something new. One
    /// shared block preview (the rendered Markdown is identical regardless of which file it lands
    /// in) plus a per-file "Sync now" row for every remembered path.
    ///
    /// Manual "Sync now" only for this slice; auto-refresh-on-memory-change (e.g. re-syncing after
    /// a capture the same way loadRecent()/loadReview() already do) is a natural next slice.
    private var contextFilesRow: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center, spacing: 14) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(CortexDesign.gold.opacity(0.13))
                    Image(systemName: "doc.badge.gearshape")
                        .font(.system(size: 24, weight: .semibold))
                        .foregroundColor(CortexDesign.gold)
                }
                .frame(width: 56, height: 56)

                VStack(alignment: .leading, spacing: 4) {
                    Text("Context files".uppercased())
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.8)
                        .foregroundColor(CortexDesign.inkFaint)
                    Text("Keep your CLAUDE.md in sync")
                        .font(.title3)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.ink)
                    Text("Keep your CLAUDE.md in sync with your memory: cited, visible, yours.")
                        .font(.callout)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Spacer(minLength: 8)

                CortexButton(title: "Add file…", systemImage: "plus", role: .secondary, size: .large) {
                    state.chooseContextFile()
                }
                .help("Pick an existing CLAUDE.md, AGENTS.md, or .cursorrules, or type a new filename to create one.")
            }

            if !state.contextFilePaths.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(state.contextFilePaths, id: \.self) { path in
                        contextFileRow(path)
                    }
                }
            }

            contextBlockPreview
        }
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    /// One remembered context-file path: its filename, a "Sync now" button (spinner while in
    /// flight), and the last sync's result line once available.
    @ViewBuilder
    private func contextFileRow(_ path: String) -> some View {
        let result = state.contextFileSyncResults[path]
        let isSyncing = state.syncingContextFilePath == path
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 10) {
                Image(systemName: "doc.text")
                    .foregroundColor(CortexDesign.inkSecondary)
                    .frame(width: 24)
                VStack(alignment: .leading, spacing: 1) {
                    Text(URL(fileURLWithPath: path).lastPathComponent)
                        .font(.callout)
                        .fontWeight(.medium)
                        .foregroundColor(CortexDesign.ink)
                    Text(path)
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkFaint)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer(minLength: 8)
                if isSyncing {
                    ProgressView()
                        .scaleEffect(0.7)
                        .frame(minWidth: 78, minHeight: 30)
                } else {
                    CortexButton(title: "Sync now", systemImage: "arrow.triangle.2.circlepath", role: .ghost, size: .small) {
                        Task { await state.syncContextFile(path: path) }
                    }
                    .disabled(state.syncingContextFilePath != nil)
                    .help("Renders your cited profile into this file's \(DistributionMode.appDisplayName)-managed block.")
                }
            }
            if let result {
                Text("\(result.created ? "Created" : "Synced") · \(result.blockLines) lines · \(result.bytesWritten) bytes")
                    .font(.caption)
                    .foregroundColor(CortexDesign.sealMoss)
            }
        }
    }

    /// A lightweight preview of the rendered managed block — an expandable, read-only, monospaced
    /// excerpt so a user sees exactly what will be written before they hit "Sync now" on any file.
    @ViewBuilder
    private var contextBlockPreview: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                CortexButton(
                    title: state.contextFileBlockPreview == nil ? "Preview" : "Refresh preview",
                    systemImage: "eye",
                    role: .ghost,
                    size: .small
                ) {
                    Task { await state.loadContextFilePreview() }
                }
                .help("See exactly what \(DistributionMode.appDisplayName) will write before you sync any file.")
                Spacer(minLength: 0)
            }

            if let preview = state.contextFileBlockPreview {
                DisclosureGroup(isExpanded: $contextBlockPreviewExpanded) {
                    ScrollView {
                        Text(preview.text)
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
                    Text(contextBlockPreviewExpanded ? "Hide preview" : "Show what will be written")
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.inkSecondary)
                }
            }
        }
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
            return "Enable this only when you want reviewed memory available outside \(DistributionMode.appDisplayName)."
        }
        return "Connect Claude Desktop, Cursor, or any MCP app with one click. ChatGPT and Claude web reach your memory through a live connector; nothing is copied out of \(DistributionMode.appDisplayName)."
    }

    /// The ChatGPT / Claude-web path, given equal footing with MCP installs: opens the wizard to add
    /// Cortex as a LIVE remote connector (a link + key, a credential). Your memory stays in Cortex
    /// and is served on demand; nothing is ever copied out. This replaces the old memory-pack export.
    private var browserAssistantRow: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center, spacing: 14) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(CortexDesign.accent.opacity(0.13))
                    Image(systemName: "cloud")
                        .font(.system(size: 24, weight: .semibold))
                        .foregroundColor(CortexDesign.accent)
                }
                .frame(width: 56, height: 56)

                VStack(alignment: .leading, spacing: 4) {
                    Text("ChatGPT & Claude on the web".uppercased())
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.8)
                        .foregroundColor(CortexDesign.inkFaint)
                    Text("Add \(DistributionMode.appDisplayName) as a live connector")
                        .font(.title3)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.ink)
                    Text("Hand the web chat a connector link and key so it reads your memory on demand. Your memory stays in \(DistributionMode.appDisplayName), never copied out.")
                        .font(.callout)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Spacer(minLength: 8)

                CortexButton(title: "Add a connector", systemImage: "cloud", role: .secondary, size: .large) {
                    // U-CONN6: land directly on the remote-connector path instead of the generic pick grid.
                    remoteConnectorWizard = true
                }
                .help("Opens the wizard to add \(DistributionMode.appDisplayName) as a live connector in ChatGPT or Claude web. It reads your memory on demand; nothing is copied out.")
            }
        }
        .padding(14)
        .background(connectionsPanelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
        .clipShape(RoundedRectangle(cornerRadius: 8))
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

    /// The catalog-stamp label under each seal on the wax step rail.
    var stampLabel: String {
        switch self {
        case .pick: return "Pick"
        case .connect: return "Connect"
        case .verify: return "Verify"
        case .done: return "Done"
        }
    }
}

/// Self-contained sheet: pick a tool, copy its ready-to-paste connection, test that the tool can
/// reach Cortex, then a success confirmation. Dismissable at every step; Back/Next navigation with
/// a 1..4 of 4 progress affordance. Honesty invariant: step 4 is only reachable after the copy+mark
/// step (which drives connected-state) AND, where a real probe exists, a passing `testToolConnection`.
struct ConnectAppWizard: View {
    @ObservedObject var state: AppState
    @Environment(\.dismiss) private var dismiss

    /// U-CONN6/7: an optional pre-target. When a caller already knows which tool (or which kind of
    /// tool) the user wants, the wizard opens on the connect step with that tool selected, so "Add a
    /// connector" lands on the remote-connector path instead of the generic pick grid. Defaults keep
    /// the plain `ConnectAppWizard(state:)` call site working unchanged.
    var preselectToolID: String? = nil
    var preselectKind: IntegrationConnectionKind? = nil

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
        .onAppear { applyPreselectIfNeeded() }
    }

    /// U-CONN6/7: honor a caller's pre-target. Prefers an exact tool id, else the first tool of the
    /// requested connection kind (e.g. the remote-connector tool for "Add a connector"). Only fires
    /// while still on the pick step and nothing is selected yet, so it never yanks the user back.
    private func applyPreselectIfNeeded() {
        guard step == .pick, selected == nil else { return }
        // Prefer the exact tool id; if it isn't in the catalog, fall back to the first non-reference
        // tool of the requested kind (e.g. the remote-connector tool for "Add a connector").
        var target: AIIntegration?
        if let id = preselectToolID {
            target = state.integrations.first { $0.id == id }
        }
        if target == nil, let kind = preselectKind {
            target = state.integrations.first { $0.connectionKind == kind && !$0.referenceOnly }
        }
        guard let target else { return }
        selectTool(target)
        step = .connect
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

    /// Whether the honest "finish once the action fired" escape applies. Some live paths can't be
    /// verified by a local probe: a deeplink install happens INSIDE the tool (Cursor/VS Code confirm
    /// it, Cortex can't read their state); a remote connector needs the hosted side; a reference-only
    /// tool has no live path yet. For those, once the connect action fired (didCopy) the user isn't
    /// trapped — they can finish. The config/cli/http paths keep the strict test-passes gate, so the
    /// honesty invariant holds for everything Cortex can actually verify.
    private var canFinishWithoutProbe: Bool {
        guard let tool = selected, didCopy else { return false }
        if tool.referenceOnly { return true }
        switch tool.connectionKind {
        case .mcpDeeplink, .remoteMCP:
            return true
        case .mcpConfig, .cliCommand, .httpAPI:
            return false
        }
    }

    private var headerSubtitle: String {
        switch step {
        case .pick: return "Pick the tool you want to give access to your reviewed memory."
        case .connect: return selected.map { "Add \(DistributionMode.appDisplayName) to \($0.name)." } ?? "Add \(DistributionMode.appDisplayName) to your tool."
        case .verify: return selected.map { "Check that \($0.name) can reach your memory." } ?? "Check the connection."
        case .done: return "You're set. Your memory is available where you work."
        }
    }

    /// The wax-sealed stamped step rail: each step is a small sealing-wax disc — a check once
    /// stamped (completed), a domed wax seal for the current step, a hairline ring for what's ahead —
    /// joined by a rule that fills wax-red as you advance, with the step name in the catalog-stamp
    /// voice beneath. Replaces the four flat capsules; a plain "Step N of 4" line stays for a11y.
    private var progressBar: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 0) {
                ForEach(Array(ConnectAppWizardStep.allCases.enumerated()), id: \.element) { index, s in
                    stepStamp(s)
                    if index < ConnectAppWizardStep.count - 1 {
                        Rectangle()
                            .fill(s.index < step.index ? CortexDesign.accent : CortexDesign.hairline)
                            .frame(height: 2)
                            .frame(maxWidth: .infinity)
                            .padding(.horizontal, 4)
                            .offset(y: -8)
                    }
                }
            }
            Text("Step \(step.index + 1) of \(ConnectAppWizardStep.count) · \(stepTitle)")
                .font(CortexDesign.Typography.stamp)
                .kerning(0.6)
                .foregroundColor(CortexDesign.inkFaint)
        }
    }

    @ViewBuilder
    private func stepStamp(_ s: ConnectAppWizardStep) -> some View {
        let done = s.index < step.index
        let current = s.index == step.index
        VStack(spacing: 5) {
            ZStack {
                if done || current {
                    // A domed sealing-wax disc for the reached steps.
                    CortexSealSurface(cornerRadius: 9)
                        .clipShape(Circle())
                        .frame(width: 18, height: 18)
                    Image(systemName: done ? "checkmark" : "circle.fill")
                        .font(.system(size: done ? 9 : 5, weight: .bold))
                        .foregroundColor(CortexDesign.panelBackground)
                } else {
                    Circle()
                        .strokeBorder(CortexDesign.hairline, lineWidth: 1.5)
                        .frame(width: 18, height: 18)
                }
            }
            Text(s.stampLabel.uppercased())
                .font(CortexDesign.Typography.hint)
                .kerning(0.5)
                .foregroundColor(current ? CortexDesign.accent : CortexDesign.inkFaint)
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
            .help(didCopy ? "" : "Run the connect action first.")
        case .verify:
            HStack(spacing: 10) {
                // The honest escape for the paths Cortex can't verify locally: a deeplink install
                // happens inside the tool (Cursor/VS Code confirm it), a remote connector needs the
                // hosted side, and a reference-only tool has no live path yet. For those, once the
                // connect action fired the user isn't trapped. The config/cli/http paths keep the
                // strict test-passes gate, so the honesty invariant holds for anything verifiable.
                if canFinishWithoutProbe {
                    CortexButton(title: "Finish", systemImage: "checkmark", role: .primary, size: .large) {
                        withAnimation(.easeInOut(duration: 0.2)) { step = .done }
                    }
                    .help("The connection is set up on \(DistributionMode.appDisplayName)'s side. This tool confirms it on its own end.")
                } else {
                    CortexButton(title: "Finish", systemImage: "checkmark", role: .primary, size: .large) {
                        withAnimation(.easeInOut(duration: 0.2)) { step = .done }
                    }
                    .disabled(!(testResult?.ok ?? false))
                    .help((testResult?.ok ?? false) ? "" : "Run the test and pass it first.")
                }
            }
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
    // icon + name + the tool's own one-line summary ("what you'll be able to do"). The one-click
    // "Connect all my AI apps" hero sits on top so the fastest path is the first thing offered.
    private var pickStep: some View {
        VStack(alignment: .leading, spacing: 18) {
            connectAllHero
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

    /// The one-click "Connect all my AI apps" hero at the top of step 1. It wires up EVERY
    /// MCP-capable app detected on this Mac in a single click (config-write + relaunch for config
    /// apps, native install deeplink for Cursor / VS Code) via state.installDetectedIntegrations().
    /// On the App Store build (no file write) it copies a combined setup config instead, which is
    /// honest about what the sandbox can do. Always shown so the fastest path is discoverable, with
    /// the button disabled and a plain nudge when nothing MCP-capable is detected yet.
    @ViewBuilder
    private var connectAllHero: some View {
        let detected = state.connectAllDetectedIntegrations
        let count = detected.count
        HStack(alignment: .center, spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(CortexDesign.accent.opacity(0.13))
                Image(systemName: "bolt.circle.fill")
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundColor(CortexDesign.accent)
            }
            .frame(width: 48, height: 48)

            VStack(alignment: .leading, spacing: 3) {
                Text("Fastest".uppercased())
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.accent)
                Text("Connect all my AI apps")
                    .font(.headline)
                    .foregroundColor(CortexDesign.ink)
                Text(connectAllDetail(count: count))
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 8)

            CortexButton(
                title: DistributionMode.isAppStore ? "Copy setup config" : "Connect all",
                systemImage: DistributionMode.isAppStore ? "doc.on.doc" : "bolt.fill",
                role: .primary,
                size: .large
            ) {
                state.installDetectedIntegrations()
            }
            .disabled(!DistributionMode.isAppStore && count == 0)
            .help(DistributionMode.isAppStore
                  ? "Copies the \(DistributionMode.appDisplayName) setup config to paste into each AI app's MCP settings."
                  : "Wires up every AI app detected on this Mac at once. A backup is saved before any config change.")
        }
        .padding(14)
        .background(CortexDesign.cardBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.accent.opacity(0.35), lineWidth: 1.5))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func connectAllDetail(count: Int) -> String {
        if DistributionMode.isAppStore {
            return "Copy one config and paste it into each AI app's MCP settings."
        }
        if count == 0 {
            return "No AI apps detected on this Mac yet. Install one, or pick a tool below."
        }
        return "\(count) AI app\(count == 1 ? "" : "s") detected on this Mac. Connect \(count == 1 ? "it" : "them all") in one click."
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

    // Step 2 — Connect: ONE primary button per tool that does the right LIVE thing by kind. Every
    // path is a live connection back into Cortex; none exports a copy of your data. Deeplink tools
    // open the tool's own one-click install; config tools write + relaunch (DMG) or copy-config
    // (App Store); remote tools get the hosted connector credential; cliCommand/httpAPI tools copy
    // their command / API details. Reference-only tools have no live path yet and just open the site.
    @ViewBuilder
    private var connectStep: some View {
        if let tool = selected {
            VStack(alignment: .leading, spacing: 16) {
                selectedToolBanner(tool)

                if tool.referenceOnly {
                    referenceOnlyConnectBody(tool)
                } else {
                    switch tool.connectionKind {
                    case .mcpDeeplink:
                        deeplinkConnectBody(tool)
                    case .mcpConfig:
                        mcpConnectBody(tool)
                    case .cliCommand:
                        commandConnectBody(tool)
                    case .remoteMCP:
                        remoteMCPConnectBody(tool)
                    case .httpAPI:
                        httpAPIConnectBody(tool)
                    }
                }

                privacyNote
            }
        }
    }

    /// Native one-click deeplink (Cursor / VS Code): a single button opens the tool, which pops up
    /// to confirm. No copy, no manual paste, no restart on our side.
    private func deeplinkConnectBody(_ tool: AIIntegration) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            pasteInstructions([
                "Click Install in \(tool.name) below.",
                "\(tool.name) pops up to confirm the \(DistributionMode.appDisplayName) connection. Approve it.",
                "That's it. \(tool.name) is connected live."
            ])
            CortexButton(title: "Install in \(tool.name)", systemImage: "arrow.down.app", role: .primary, size: .large) {
                state.connectViaDeeplink(for: tool)
                didCopy = true
            }
            .help("Opens \(tool.name) with a one-click install link. It confirms, and you're live. No file editing.")
            actionConfirmation("\(tool.name) will pop up to confirm. Approve it, then continue.")
        }
    }

    /// Remote-connector path (ChatGPT / Claude web): P8. The connector URL and key are shown as two
    /// SEPARATE labeled rows, each with its own copy button, because these web forms have separate
    /// fields — one combined blob meant a second copy wiped the key. A combined copy stays as a
    /// secondary. Concrete, per-host guided steps run through the shared GuidedStepWalkthrough so the
    /// user knows exactly where developer mode lives and which field the key goes in. Requires sign-in
    /// so the hosted connector can actually reach the user's memory.
    private func remoteMCPConnectBody(_ tool: AIIntegration) -> some View {
        RemoteMCPConnectBody(state: state, tool: tool, didCopy: $didCopy)
    }

    /// Reference-only tools (Perplexity, Copilot web, Grok, Poe, NotebookLM): no live path yet, so
    /// we say so honestly and offer to open the site alongside Cortex. Never exports data.
    private func referenceOnlyConnectBody(_ tool: AIIntegration) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 7) {
                Image(systemName: "clock.badge.questionmark")
                    .foregroundColor(CortexDesign.inkSecondary)
                Text("A live \(DistributionMode.appDisplayName) connection for \(tool.name) is not supported yet. \(DistributionMode.appDisplayName) never copies your memory out, so there's nothing to paste here. Open \(tool.name) alongside \(DistributionMode.appDisplayName) in the meantime.")
                    .font(.callout)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let urlString = tool.browserURL, let url = URL(string: urlString) {
                CortexButton(title: "Open \(tool.name)", systemImage: "arrow.up.right.square", role: .secondary, size: .large) {
                    NSWorkspace.shared.open(url)
                    didCopy = true
                }
            }
        }
    }

    /// The MCP config path. On DMG the primary is one-click LIVE: write the Cortex server into the
    /// tool's config, then relaunch the tool so it picks it up (connectAndRelaunch) — no manual
    /// restart. A copy-config fallback stays for anyone who prefers to paste. On MAS the sandbox
    /// can't write other apps' files, so only the copy+paste path is honest (and copyMCPConfig
    /// records the connected-state signal).
    private func mcpConnectBody(_ tool: AIIntegration) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            if DistributionMode.isAppStore {
                configPreview
                pasteInstructions(configPasteSteps(tool))
                CortexButton(title: "Copy config", systemImage: "doc.on.doc", role: .primary, size: .large) {
                    state.copyMCPConfig(for: tool)
                    didCopy = true
                }
            } else {
                pasteInstructions([
                    "Click Connect & restart \(tool.name) below.",
                    "\(DistributionMode.appDisplayName) writes the connection into \(tool.name) and restarts it for you (a backup is saved first).",
                    "\(tool.name) reopens with \(DistributionMode.appDisplayName) live."
                ])
                HStack(spacing: 10) {
                    CortexButton(title: "Connect & restart \(tool.name)", systemImage: "link.circle", role: .primary, size: .large) {
                        state.connectAndRelaunch(for: tool)
                        didCopy = true
                    }
                    .help("Writes the \(DistributionMode.appDisplayName) server into \(tool.name)'s config file and restarts it for you.")
                    CortexButton(title: "Copy config instead", systemImage: "doc.on.doc", role: .ghost, size: .large) {
                        state.copyMCPConfig(for: tool)
                        didCopy = true
                    }
                    Spacer(minLength: 0)
                }
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
                Text("Copied. Paste it, then continue to verify.")
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
            }
        }
    }

    /// Like copiedConfirmation but for the non-copy live paths (deeplink / remote connector): a
    /// green mark with a custom line once the action has fired, so the wizard can advance without
    /// implying "copied".
    @ViewBuilder
    private func actionConfirmation(_ message: String) -> some View {
        if didCopy {
            HStack(spacing: 6) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(CortexDesign.sealMoss)
                Text(message)
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
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
            // The seal is set: a wax-seal hero over the confirmation, the archive's "it's done" mark
            // rather than a stock SF checkmark.
            HStack(spacing: 14) {
                CortexWaxSeal(size: 48)
                VStack(alignment: .leading, spacing: 3) {
                    Text(selected.map { "\($0.name) is connected" } ?? "Connected")
                        .font(CortexDesign.Typography.display(20))
                        .foregroundColor(CortexDesign.ink)
                    Text(selected?.setupHint ?? "Your reviewed memory is now available to this app.")
                        .font(.callout)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous).fill(CortexDesign.sealMoss.opacity(0.1)))
            .embossedBorder(radius: CortexDesign.Radius.md)

            // The payoff, given room to breathe: the proof moment scoped to the tool just connected.
            // It waits for ITS first read (per-app token label == tool name) and flips to "<tool>
            // just read your memory. Continuity, proven." Reference-only tools (no live path) and
            // remote-connector tools (whose hosted connector may not be live yet) are excluded
            // honestly — a watcher there could never flip. Additive; never blocks Close.
            if let tool = selected, !tool.referenceOnly, tool.connectionKind != .remoteMCP {
                RecallProofWatcher(
                    state: state,
                    toolLabel: tool.name,
                    waitingLine: "Waiting for \(tool.name) to read your memory. Ask it anything about you."
                )
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, CortexDesign.Space.sm)
            }

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

/// P8 — the remote-connector connect body (ChatGPT / Claude web). Custom remote connectors have no
/// one-click deeplink anywhere, so the ceiling is precise guided steps plus a copy affordance that
/// actually fits the destination form. The connector URL and key are rendered as two SEPARATE
/// labeled rows, each with its own copy button, so pasting the key never clobbers the URL (their
/// forms have distinct fields). A combined "Copy both" stays as a secondary. Concrete per-host
/// steps run through the shared GuidedStepWalkthrough. Everything the user pastes is a credential,
/// never their memory.
private struct RemoteMCPConnectBody: View {
    @ObservedObject var state: AppState
    let tool: AIIntegration
    @Binding var didCopy: Bool

    /// The minted connector URL + key for this tool, fetched once sign-in is present. nil until the
    /// user taps "Prepare connector" (minting a hosted key can round-trip, so it isn't done eagerly).
    @State private var connectorURL: String?
    @State private var connectorKey: String?
    @State private var preparing = false
    @State private var prepareError: String?
    @State private var copiedField: CopiedField?

    private enum CopiedField: Equatable { case url, key, both }

    private var hostedBase: String {
        (state.cloudSyncBaseURL.isEmpty ? AppState.defaultHostedURL : state.cloudSyncBaseURL)
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if !state.isSignedIn {
                HStack(alignment: .top, spacing: 7) {
                    Image(systemName: "person.crop.circle.badge.exclamationmark")
                        .foregroundColor(CortexDesign.gold)
                    Text("Sign in and sync first so \(tool.name) can reach your memory through the hosted connector. Nothing is copied out of \(DistributionMode.appDisplayName).")
                        .font(.callout)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            // Concrete, per-host steps: where developer mode lives and which field each value goes in.
            GuidedStepWalkthrough(steps: guidedSteps, isActive: true, textFont: .callout) { _ in
                EmptyView()
            }

            if connectorURL != nil {
                connectorRows
            } else {
                CortexButton(title: "Prepare connector", systemImage: "cloud", role: .primary, size: .large) {
                    prepare()
                }
                .disabled(!state.isSignedIn || preparing)
                .help("Mints the \(DistributionMode.appDisplayName) connector URL and key for \(tool.name). Paste each into its own field in \(tool.name).")
                if preparing {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("Preparing your \(tool.name) connector…")
                            .font(.caption)
                            .foregroundColor(CortexDesign.inkSecondary)
                    }
                }
            }

            if let prepareError {
                HStack(alignment: .top, spacing: 7) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundColor(CortexDesign.gold)
                    Text(prepareError)
                        .font(.caption)
                        .foregroundColor(CortexDesign.accent)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            Text("A fully live \(tool.name) connection needs the hosted connector enabled, which may not be live yet.")
                .font(.caption)
                .foregroundColor(CortexDesign.inkFaint)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// The two separate credential rows, each with its own copy button, plus a combined copy and a
    /// button to open the tool's connector settings.
    @ViewBuilder
    private var connectorRows: some View {
        VStack(alignment: .leading, spacing: 10) {
            credentialRow(
                label: "Connector URL",
                value: connectorURL ?? "",
                field: .url,
                systemImage: "link"
            )
            credentialRow(
                label: "Connector key",
                value: connectorKey ?? "",
                field: .key,
                systemImage: "key.fill",
                secret: true
            )

            HStack(spacing: 10) {
                CortexButton(
                    title: copiedField == .both ? "Copied both" : "Copy both",
                    systemImage: copiedField == .both ? "checkmark" : "doc.on.doc",
                    role: .ghost,
                    size: .regular
                ) {
                    copyBoth()
                }
                .help("Copies the URL and key together. Some forms take them as one block.")

                if let urlString = tool.browserURL, let url = URL(string: urlString) {
                    CortexButton(title: "Open \(tool.name)", systemImage: "arrow.up.right.square", role: .secondary, size: .regular) {
                        NSWorkspace.shared.open(url)
                        didCopy = true
                    }
                    .help("Opens \(tool.name)'s connector settings so you can paste each value into its own field.")
                }
                Spacer(minLength: 0)
            }

            // The moss mark — the archive's private-by-default signature.
            HStack(alignment: .top, spacing: 7) {
                Circle()
                    .fill(CortexDesign.sealMoss)
                    .frame(width: 7, height: 7)
                    .padding(.top, 3)
                Text("The key is a secure connection, not your data. Your memory stays in \(DistributionMode.appDisplayName) and is served on demand.")
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// One labeled credential row: a mono value (secret keys are masked until copied) and a dedicated
    /// copy button that flips to "Copied" for 2s.
    @ViewBuilder
    private func credentialRow(label: String, value: String, field: CopiedField, systemImage: String, secret: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(label.uppercased())
                .font(CortexDesign.Typography.stamp)
                .kerning(0.8)
                .foregroundColor(CortexDesign.inkFaint)
            HStack(spacing: 8) {
                Image(systemName: systemImage)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(CortexDesign.inkSecondary)
                    .frame(width: 18)
                Text(secret ? maskedKey(value) : value)
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundColor(CortexDesign.ink)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                CortexButton(
                    title: copiedField == field ? "Copied" : "Copy",
                    systemImage: copiedField == field ? "checkmark" : "doc.on.doc",
                    role: .secondary,
                    size: .small
                ) {
                    copy(value, field: field)
                }
                .help("Copies the \(label.lowercased()) on its own, so pasting one value never overwrites the other.")
            }
            .padding(10)
            .background(RoundedRectangle(cornerRadius: 8).fill(CortexDesign.quietBackground))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))
        }
    }

    /// Masks all but the last 4 characters of a secret so the row can be shown without exposing the
    /// full key on screen; the copy button still copies the real value.
    private func maskedKey(_ value: String) -> String {
        guard value.count > 4 else { return String(repeating: "•", count: max(value.count, 4)) }
        return String(repeating: "•", count: 6) + String(value.suffix(4))
    }

    /// Concrete per-host connect steps. ChatGPT and Claude web get their exact settings path; anything
    /// else gets a precise generic remote-connector recipe. No host offers a one-click deeplink here,
    /// so exact steps are the honest ceiling.
    private var guidedSteps: [String] {
        switch tool.id {
        case "chatgpt":
            return [
                "In ChatGPT, open Settings, then Connectors, then Advanced, and turn on Developer mode.",
                "Choose Create, then paste the Connector URL below into the URL field.",
                "Set Authentication to API key (Bearer), then paste the Connector key below into the key field.",
                "Choose Create. \(DistributionMode.appDisplayName) appears as a connector ChatGPT can read on demand.",
            ]
        case "claude":
            return [
                "In Claude on the web, open Settings, then Connectors, and choose Add custom connector.",
                "Paste the Connector URL below into the remote MCP server URL field.",
                "Choose API key or Bearer token authentication, then paste the Connector key below.",
                "Save. \(DistributionMode.appDisplayName) is now a connector Claude reaches live.",
            ]
        default:
            return [
                "Open \(tool.name)'s custom or remote connector settings.",
                "Paste the Connector URL below as the remote MCP server URL.",
                "Choose Bearer or API key authentication, then paste the Connector key below.",
                "Save the connector. \(DistributionMode.appDisplayName) is served live; nothing is copied out.",
            ]
        }
    }

    private func prepare() {
        guard state.isSignedIn, !preparing else { return }
        preparing = true
        prepareError = nil
        Task { @MainActor in
            defer { preparing = false }
            let base = hostedBase
            guard !base.isEmpty else {
                prepareError = "Set up cloud sync first so \(tool.name) has a hosted connector to reach."
                return
            }
            let token = await state.mintHostedConnectorToken(base: base, integration: tool)
            guard let token, !token.isEmpty else {
                prepareError = "Couldn't mint a hosted connector key yet. The hosted connector may not be enabled for your account."
                return
            }
            connectorURL = "\(base)/mcp"
            connectorKey = token
        }
    }

    private func copy(_ value: String, field: CopiedField) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(value, forType: .string)
        markCopied(field)
    }

    private func copyBoth() {
        let details = """
        \(DistributionMode.appDisplayName) connector for \(tool.name) (a secure connection, not your data)

        Connector URL: \(connectorURL ?? "")
        Connector key: \(connectorKey ?? "")
        """
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(details, forType: .string)
        markCopied(.both)
    }

    private func markCopied(_ field: CopiedField) {
        withAnimation(.easeOut(duration: 0.15)) { copiedField = field }
        // The connect action has fired: the wizard can advance to verify.
        didCopy = true
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            if copiedField == field {
                withAnimation(.easeOut(duration: 0.2)) { copiedField = nil }
            }
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
        "Quit and reopen the app. \(DistributionMode.appDisplayName) memory tools appear once it restarts."
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
        CortexDisclosure(
            isExpanded: $setupExpanded,
            systemImage: "text.and.command.macwindow",
            title: "Connect an AI tool manually",
            detail: "Copy the connection and paste it into Claude, Cursor & other AI apps"
        ) {
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

    /// U-CONN8: a relative "Last backup …" line from the lifecycle report, so the button reports a
    /// real result instead of silently firing. createBackup reloads dataLifecycleReport (via
    /// loadTrust) so this refreshes on its own once a backup lands.
    private var lastBackupLabel: String? {
        guard let created = state.dataLifecycleReport?.backups.latest_backup?.created_at,
              let relative = relativeCapturedLabel(created) else {
            return nil
        }
        return "Last backup \(relative)"
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
                if state.backupInFlight {
                    ProgressView()
                        .controlSize(.small)
                        .frame(minWidth: 132, minHeight: 44)
                } else {
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

            // The result of the last backup: an error to retry, or the relative time it landed.
            if let error = state.lastBackupError {
                HStack(alignment: .top, spacing: 7) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundColor(CortexDesign.gold)
                    Text(error)
                        .font(.caption)
                        .foregroundColor(CortexDesign.accent)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                    CortexButton(title: "Try again", systemImage: "arrow.clockwise", role: .ghost, size: .small) {
                        state.createBackup()
                    }
                }
            } else if let lastBackupLabel {
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.seal")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(CortexDesign.sealMoss)
                    Text(lastBackupLabel)
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                    Spacer(minLength: 0)
                }
            }
        }
        .connectionsSubCard()
        .onAppear {
            // Ensure the "Last backup …" line has data even before the first backup this session.
            if state.dataLifecycleReport == nil {
                Task { await state.loadTrust() }
            }
        }
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

            // U-CONN1: each tile is a live control now. Tapping flips the matching agent-permission
            // in appSettings and persists it through the existing settings-save path, so the four
            // permissions are directly editable here instead of only reflecting the policy section.
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 10)], spacing: 10) {
                ConnectionsTrustTile(
                    title: settings.allow_agent_reads ? "Read" : "Read off",
                    detail: settings.allow_agent_reads ? "reviewed memory" : "blocked",
                    systemImage: settings.allow_agent_reads ? "eye.fill" : "eye.slash.fill",
                    color: settings.allow_agent_reads ? CortexDesign.sealMoss : CortexDesign.inkSecondary,
                    isOn: settings.allow_agent_reads
                ) {
                    state.appSettings.allow_agent_reads.toggle()
                    state.saveMemorySettings()
                }
                ConnectionsTrustTile(
                    title: settings.allow_agent_writes ? "Save" : "Save off",
                    detail: settings.allow_agent_writes ? "new memory to Review" : "blocked",
                    systemImage: settings.allow_agent_writes ? "square.and.pencil" : "pencil.slash",
                    color: settings.allow_agent_writes ? CortexDesign.accent : CortexDesign.inkSecondary,
                    isOn: settings.allow_agent_writes
                ) {
                    state.appSettings.allow_agent_writes.toggle()
                    state.saveMemorySettings()
                }
                ConnectionsTrustTile(
                    title: settings.allow_agent_exports ? "Export on" : "Export off",
                    detail: settings.allow_agent_exports ? "redacted exports" : "blocked",
                    systemImage: "square.and.arrow.up",
                    color: settings.allow_agent_exports ? CortexDesign.accent : CortexDesign.inkSecondary,
                    isOn: settings.allow_agent_exports
                ) {
                    state.appSettings.allow_agent_exports.toggle()
                    state.saveMemorySettings()
                }
                ConnectionsTrustTile(
                    title: settings.allow_agent_maintenance ? "Maintenance on" : "Maintenance off",
                    detail: settings.allow_agent_destructive_actions ? "delete allowed" : "no deletion",
                    systemImage: settings.allow_agent_maintenance ? "wrench.and.screwdriver.fill" : "wrench.and.screwdriver",
                    color: settings.allow_agent_maintenance ? CortexDesign.accent : CortexDesign.inkSecondary,
                    isOn: settings.allow_agent_maintenance
                ) {
                    state.appSettings.allow_agent_maintenance.toggle()
                    state.saveMemorySettings()
                }
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
                // U-CONN5: a manual Refresh next to the label so recent tool activity can be pulled
                // on demand (mirrors the audit section's refresh); it also reloads on expand below.
                HStack(spacing: 8) {
                    Text("Recent tool activity")
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.inkSecondary)
                    Spacer(minLength: 0)
                    CortexIconButton(systemImage: "arrow.clockwise", role: .ghost, size: .small, help: "Refresh recent tool activity") {
                        Task { await state.loadTrust() }
                    }
                }
            }
            .padding(12)
            .background(CortexDesign.cardBackground)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .onChange(of: recentActivityExpanded) { expanded in
                if expanded {
                    Task { await state.loadTrust() }
                }
            }
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
        // U-CONN2: keep the token + audit summary fresh while the sheet is open. A tool that reads
        // memory (or a newly minted/reset token) shows up within a few seconds instead of only after
        // reopening the sheet. The loop ends when the view leaves the hierarchy (task cancellation).
        .task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 6_000_000_000)
                if Task.isCancelled { break }
                await state.loadIntegrationTokens()
                await state.loadTrust()
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

/// U-CONN1: a permission tile that is a real control, not a dead read-out. When `isOn` and `toggle`
/// are supplied the whole tile is a button that flips the matching agent-permission and persists it;
/// an on/off pip and a hover lift make the interactivity legible. Passing no `toggle` renders the
/// original static tile (kept for any read-only callers).
private struct ConnectionsTrustTile: View {
    let title: String
    let detail: String
    let systemImage: String
    let color: Color
    var isOn: Bool? = nil
    var toggle: (() -> Void)? = nil
    @State private var hovering = false

    private var isInteractive: Bool { toggle != nil }

    var body: some View {
        Group {
            if let toggle {
                Button(action: toggle) { tileContent }
                    .buttonStyle(.plain)
                    .onHover { hovering = $0 }
                    .animation(.easeOut(duration: 0.12), value: hovering)
                    .help(helpText)
                    .accessibilityLabel(title)
                    .accessibilityValue((isOn ?? false) ? "On" : "Off")
                    .accessibilityHint("Toggles this permission for connected AI tools.")
            } else {
                tileContent
            }
        }
    }

    private var helpText: String {
        (isOn ?? false)
            ? "On. Tap to turn this off for connected AI tools."
            : "Off. Tap to allow this for connected AI tools."
    }

    private var tileContent: some View {
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
            if let isOn {
                // A small state pip so the tile reads on/off at a glance.
                Circle()
                    .fill(isOn ? color : CortexDesign.inkFaint.opacity(0.4))
                    .frame(width: 9, height: 9)
            }
        }
        .padding(12)
        .frame(minHeight: 72, alignment: .leading)
        .background(hovering && isInteractive ? CortexDesign.accentSoft : CortexDesign.cardBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .contentShape(RoundedRectangle(cornerRadius: 8))
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
                    QuietState(title: "No source connected", detail: "Connect a local source once. \(DistributionMode.appDisplayName) syncs after that.")
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
                Text("\(DistributionMode.appDisplayName) reads your GitHub activity so your memory can cite it. Read-only: \(DistributionMode.appDisplayName) never writes to your repositories.")
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
