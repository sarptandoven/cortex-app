import SwiftUI

struct ModelTab: View {
    @ObservedObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HomeHeroSection(state: state, review: state.review)
            }
            .padding(16)
        }
        .background(CortexDesign.appBackground)
    }
}

struct HomeHeroSection: View {
    @ObservedObject var state: AppState
    let review: DailyReviewResponse?

    private var activeSources: Int {
        if let connected = state.sourceReadinessReport?.summary.connected {
            return connected
        }
        return state.activeSourceAccounts.filter { account in
            !account.needsContent
        }.count
    }

    private var hasEmptySource: Bool {
        if let report = state.sourceReadinessReport {
            return report.sources.contains { $0.status == "empty" }
        }
        return state.activeSourceAccounts.contains { account in
            account.needsContent
        }
    }

    private var memoryCount: Int {
        review?.stats.memories ?? state.stats?.memories ?? 0
    }

    private var pendingCount: Int {
        review?.stats.pending_captures ?? state.inbox.count
    }

    private var hasMemory: Bool {
        memoryCount > 0
    }

    private var obsidianConnector: SourceConnectorCatalogItem? {
        state.sourceConnectorCatalog.first { $0.id == "obsidian" }
    }

    private var canSyncSource: Bool {
        state.hasConnectedObsidianVault && obsidianConnector != nil
    }

    private var statusSummary: (label: String, systemImage: String, color: Color) {
        if !state.isLocalServiceReady {
            if CortexRecoveryText.needsAttention(state.displayStatus) {
                return ("Needs attention", "exclamationmark.triangle.fill", .orange)
            }
            return ("Starting", "power", .accentColor)
        }
        if pendingCount > 0 {
            return ("Ready for Review", "tray.full.fill", .orange)
        }
        if hasMemory {
            return ("Ready to ask", "checkmark.seal.fill", .green)
        }
        if activeSources > 0 {
            return ("Syncing source", "arrow.triangle.2.circlepath", .accentColor)
        }
        if hasEmptySource {
            return ("No source content", "folder.badge.questionmark", .orange)
        }
        if state.connectedAIIntegrationCount > 0 {
            return ("Connect a source", "link.badge.plus", .accentColor)
        }
        return ("Private on this Mac", "lock.shield", .secondary)
    }

    private var title: String {
        if !state.isLocalServiceReady {
            return "Cortex is starting"
        }
        if pendingCount > 0 {
            return "Review new memory"
        }
        if hasMemory {
            return "Ask about your memory"
        }
        if activeSources > 0 {
            return "Your source is syncing"
        }
        if hasEmptySource {
            return "Choose a source with content"
        }
        if state.connectedAIIntegrationCount > 0 {
            return "Start source sync"
        }
        return "Start source sync"
    }

    private var detail: String {
        if !state.isLocalServiceReady {
            if CortexRecoveryText.needsAttention(state.displayStatus) {
                return state.displayStatus
            }
            return "This usually takes a moment."
        }
        if pendingCount > 0 {
            return "Choose what Cortex should remember before it appears in Ask."
        }
        if hasMemory {
            return "Cortex answers from saved memory and shows which source each answer came from."
        }
        if activeSources > 0 {
            return "New items will appear in Review when sync finishes."
        }
        if hasEmptySource {
            return "Cortex could not find usable content there. Pick a source with real notes or records."
        }
        if state.connectedAIIntegrationCount > 0 {
            return "Sync a source so Ask can answer with citations."
        }
        return "Connect one source once. Cortex keeps it in sync and brings new memory to Review."
    }

    private var actionTitle: String {
        if !state.isLocalServiceReady { return "Start Cortex" }
        if activeSources == 0 { return hasEmptySource ? "Choose source" : "Start source sync" }
        if pendingCount > 0 { return "Review memory" }
        if hasMemory { return "Ask a question" }
        return canSyncSource ? "Sync source" : "View source status"
    }

    private var actionDetail: String {
        if !state.isLocalServiceReady { return "Start Cortex on this Mac." }
        if activeSources == 0 {
            if hasEmptySource {
                return "Pick a different folder with useful notes."
            }
            if state.connectedAIIntegrationCount > 0 {
                return "A synced source gives Ask something to cite."
            }
            return "Cortex syncs automatically after notes are selected."
        }
        if pendingCount > 0 {
            return "\(pendingCount) item\(pendingCount == 1 ? "" : "s") waiting"
        }
        if hasMemory {
            return "\(memoryCount) saved memor\(memoryCount == 1 ? "y" : "ies") ready"
        }
        return canSyncSource ? "Check the connected source now." : "Cortex checks sources in the background."
    }

    private var actionIcon: String {
        if !state.isLocalServiceReady { return "power" }
        if activeSources == 0 { return hasEmptySource ? "folder.badge.questionmark" : "folder.badge.plus" }
        if pendingCount > 0 { return "checklist" }
        if hasMemory { return "magnifyingglass" }
        return canSyncSource ? "arrow.triangle.2.circlepath" : "info.circle"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Label(statusSummary.label, systemImage: statusSummary.systemImage)
                .font(.callout)
                .fontWeight(.semibold)
                .foregroundColor(statusSummary.color)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(statusSummary.color.opacity(0.12))
                .clipShape(Capsule())

            VStack(alignment: .leading, spacing: 8) {
                Text(title)
                    .font(.system(size: 34, weight: .semibold))
                Text(detail)
                    .font(.title3)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 680, alignment: .leading)
            }

            HStack(alignment: .center, spacing: 12) {
                Button {
                    runNextAction()
                } label: {
                    Label(actionTitle, systemImage: actionIcon)
                        .frame(minWidth: 150, minHeight: 48)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(state.isBusy)

                Text(actionDetail)
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                Spacer(minLength: 0)
            }

            VStack(alignment: .leading, spacing: 8) {
                HomeStatusRow(
                    title: "Source",
                    detail: sourceStatus.detail,
                    systemImage: sourceStatus.systemImage,
                    color: sourceStatus.color
                )
                HomeStatusRow(
                    title: "Memory",
                    detail: memoryStatus.detail,
                    systemImage: memoryStatus.systemImage,
                    color: memoryStatus.color
                )
            }
            .frame(maxWidth: 620, alignment: .leading)
        }
        .padding(.vertical, 30)
        .padding(.horizontal, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var sourceStatus: (detail: String, systemImage: String, color: Color) {
        if activeSources > 0 {
            return ("\(activeSources) connected and syncing", "folder.fill.badge.checkmark", .green)
        }
        if hasEmptySource {
            return ("No usable content found", "folder.badge.questionmark", .orange)
        }
        return ("Source sync not set up", "folder.badge.plus", .accentColor)
    }

    private var memoryStatus: (detail: String, systemImage: String, color: Color) {
        if pendingCount > 0 {
            return ("\(pendingCount) waiting for Review", "tray.full.fill", .orange)
        }
        if memoryCount > 0 {
            return ("\(memoryCount) saved for Ask", "brain.head.profile", .green)
        }
        return ("No saved memory yet", "checklist", .secondary)
    }

    private func runNextAction() {
        if !state.isLocalServiceReady {
            Task {
                await state.ensureBackend()
                await state.loadDiagnostics()
                await state.loadReview()
                await state.loadStats()
            }
        } else if activeSources == 0 {
            if let connector = obsidianConnector {
                state.connectLocalNotesFolder(connector, chooseNew: hasEmptySource)
            } else {
                state.openConnectionsPrivacy(statusMessage: "Source sync")
            }
        } else if pendingCount > 0 {
            state.selectedTab = .review
            state.status = "Review memory"
        } else if hasMemory {
            state.selectedTab = .ask
            state.status = "Ask Cortex"
        } else {
            if canSyncSource, let connector = obsidianConnector {
                state.connectLocalNotesFolder(connector)
            } else {
                state.openConnectionsPrivacy(statusMessage: "Source sync status")
            }
        }
    }
}

struct HomeStatusRow: View {
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
                Text(detail)
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 6)
        .frame(minHeight: 44, alignment: .leading)
    }
}
