import SwiftUI
import AppKit

struct ModelTab: View {
    @ObservedObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if state.mirrorInsight != nil {
                    MirrorMomentCard(state: state)
                }
                if let profile = state.profile, !profile.sections.isEmpty {
                    ForEach(profile.sections) { section in
                        ProfileCard(section: section)
                    }
                }
                HomeHeroSection(state: state, review: state.review)
            }
            .padding(16)
        }
        .background(CortexDesign.appBackground)
    }
}

/// The "Mirror Moment" — the "holy-shit, it knows me" beat. Surfaces the single thing
/// Cortex learned about the user, in their words, with its source, and lets them confirm
/// or dismiss in one tap. Only rendered when `state.mirrorInsight != nil`; if the backend
/// abstains, this view is never shown (no empty card).
struct MirrorMomentCard: View {
    @ObservedObject var state: AppState

    private var insight: MirrorInsight? { state.mirrorInsight }

    /// A calm, human caption naming the source and how many times Cortex saw it,
    /// e.g. "From your calendar · seen 6 times". Degrades gracefully when the
    /// backend omits parts of the evidence.
    private var evidenceCaption: String? {
        guard let evidence = insight?.evidence else { return nil }
        var parts: [String] = []
        if let source = evidence.source, !source.isEmpty {
            parts.append("From your \(source)")
        }
        if let count = evidence.count, count > 0 {
            parts.append("seen \(count) time\(count == 1 ? "" : "s")")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    var body: some View {
        if let insight {
            VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
                Label("Cortex noticed", systemImage: "sparkles")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(CortexDesign.accent)
                    .accessibilityHidden(true)

                Text(insight.headline)
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundColor(.primary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 620, alignment: .leading)

                if let evidenceCaption {
                    Label(evidenceCaption, systemImage: "doc.text.magnifyingglass")
                        .font(.callout)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                HStack(spacing: CortexDesign.Space.sm) {
                    Button {
                        state.confirmMirrorInsight()
                    } label: {
                        Label("That's right", systemImage: "checkmark")
                            .frame(minHeight: CortexDesign.controlHeight - 8)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .accessibilityLabel("That's right, this is accurate")

                    Button {
                        state.dismissMirrorInsight()
                    } label: {
                        Label("Not quite", systemImage: "xmark")
                            .frame(minHeight: CortexDesign.controlHeight - 8)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)
                    .accessibilityLabel("Not quite, dismiss this")

                    Spacer(minLength: 0)
                }
            }
            .cortexCard(background: CortexDesign.accentSoft)
            .frame(maxWidth: 620, alignment: .leading)
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Cortex noticed: \(insight.headline)")
        }
    }
}

/// One card in the "What Cortex knows about you" Personal Profile stack. Each card is a
/// single section (how you work, preferences, ...): a confident one-line statement plus a
/// few quiet grounding rows. Confidence is signalled with a small pill — green "Confident"
/// for a settled fact, a secondary "Emerging" for a pattern still taking shape. Rows whose
/// element carries a source_url can be opened. This view assumes the caller only renders it
/// for non-empty profiles; a section with no statement and no elements shows just its title.
struct ProfileCard: View {
    let section: ProfileSection

    /// At most three grounding elements, and only those with something to show.
    private var visibleElements: [ProfileElement] {
        Array(section.elements.filter { ($0.text?.isEmpty == false) }.prefix(3))
    }

    private var confidencePill: some View {
        CortexStatusPill(
            label: section.isConfident ? "Confident" : "Emerging",
            systemImage: section.isConfident ? "checkmark.seal" : "sparkles",
            color: section.isConfident ? .green : .secondary
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
            HStack(alignment: .firstTextBaseline, spacing: CortexDesign.Space.sm) {
                Text(section.title)
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(CortexDesign.accent)
                Spacer(minLength: 0)
                confidencePill
                    .accessibilityHidden(true)
            }

            if let statement = section.statement, !statement.isEmpty {
                Text(statement)
                    .font(.system(size: 21, weight: .semibold))
                    .foregroundColor(.primary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 620, alignment: .leading)
            }

            if !visibleElements.isEmpty {
                VStack(alignment: .leading, spacing: CortexDesign.Space.sm) {
                    ForEach(visibleElements) { element in
                        ProfileElementRow(element: element)
                    }
                }
            }
        }
        .cortexCard()
        .frame(maxWidth: 620, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(accessibilityLabel)
    }

    private var accessibilityLabel: String {
        let confidence = section.isConfident ? "Confident" : "Emerging"
        if let statement = section.statement, !statement.isEmpty {
            return "\(section.title), \(confidence): \(statement)"
        }
        return "\(section.title), \(confidence)"
    }
}

/// A quiet grounding row under a profile statement: the observed snippet in quotes and a
/// caption naming the source and how often Cortex saw it. When the element carries a
/// source_url, the whole row becomes a button that opens it.
private struct ProfileElementRow: View {
    let element: ProfileElement

    /// "From your calendar · seen 6 times" — degrades gracefully when parts are missing.
    private var sourceCaption: String? {
        var parts: [String] = []
        if let source = element.source, !source.isEmpty {
            parts.append("From your \(source)")
        }
        if let count = element.count, count > 0 {
            parts.append("seen \(count) time\(count == 1 ? "" : "s")")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private var openableURL: URL? {
        ProfileElementRow.resolveURL(element.sourceURL)
    }

    var body: some View {
        if let url = openableURL {
            Button {
                NSWorkspace.shared.open(url)
            } label: {
                rowContent
            }
            .buttonStyle(.plain)
            .accessibilityLabel(accessibilityLabel)
            .accessibilityHint("Opens the source")
        } else {
            rowContent
                .accessibilityElement(children: .combine)
                .accessibilityLabel(accessibilityLabel)
        }
    }

    private var rowContent: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.xs) {
            if let text = element.text, !text.isEmpty {
                Text("\u{201C}\(text)\u{201D}")
                    .font(.callout)
                    .foregroundColor(.primary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            if let sourceCaption {
                Text(sourceCaption)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.vertical, CortexDesign.Space.xs)
        .padding(.horizontal, CortexDesign.Space.sm)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(CortexDesign.quietBackground)
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous))
        .contentShape(Rectangle())
    }

    private var accessibilityLabel: String {
        var parts: [String] = []
        if let text = element.text, !text.isEmpty { parts.append(text) }
        if let sourceCaption { parts.append(sourceCaption) }
        return parts.isEmpty ? "Profile detail" : parts.joined(separator: ". ")
    }

    /// Turns a backend `source_url` into an openable URL. Handles Cortex's custom
    /// `local-file://` scheme (a local path), plus ordinary `file://` and web URLs.
    /// Returns nil for empty or unusable values so the row stays non-interactive.
    static func resolveURL(_ raw: String?) -> URL? {
        guard let value = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else {
            return nil
        }
        let localPrefix = "local-file://"
        if value.hasPrefix(localPrefix) {
            var path = String(value.dropFirst(localPrefix.count))
            if let hashIndex = path.firstIndex(of: "#") {
                path = String(path[..<hashIndex])
            }
            if let queryIndex = path.firstIndex(of: "?") {
                path = String(path[..<queryIndex])
            }
            let decoded = path.removingPercentEncoding ?? path
            let trimmed = decoded.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : URL(fileURLWithPath: trimmed)
        }
        return URL(string: value)
    }
}

struct HomeHeroSection: View {
    @ObservedObject var state: AppState
    let review: DailyReviewResponse?

    private var activeSources: Int {
        if let connected = state.sourceReadinessReport?.summary.connected {
            return connected
        }
        return state.connectedSourceAccounts.filter { account in
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

    private var needsAttentionSources: Int {
        state.sourceReadinessReport?.summary.needs_attention ?? 0
    }

    private var dueSyncSources: Int {
        state.sourceReadinessReport?.sources.filter { $0.sync_plan?.due_now == true }.count ?? 0
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
        if needsAttentionSources > 0 {
            return ("Source needs attention", "exclamationmark.triangle.fill", .orange)
        }
        if pendingCount > 0 {
            return ("Ready for Review", "tray.full.fill", .orange)
        }
        if dueSyncSources > 0 {
            return ("Sync due", "arrow.triangle.2.circlepath.circle.fill", .accentColor)
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
        if needsAttentionSources > 0 {
            return "Check your source connection"
        }
        if pendingCount > 0 {
            return "Review new memory"
        }
        if dueSyncSources > 0 {
            return "Refresh connected memory"
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
            return "Connect your notes"
        }
        return "Connect your notes"
    }

    private var detail: String {
        if !state.isLocalServiceReady {
            if CortexRecoveryText.needsAttention(state.displayStatus) {
                return state.displayStatus
            }
            return "This usually takes a moment."
        }
        if needsAttentionSources > 0 {
            return "Cortex keeps already synced memory local, but one or more sources need attention before fresh items arrive."
        }
        if pendingCount > 0 {
            return "Choose what Cortex should remember before it appears in Ask."
        }
        if dueSyncSources > 0 {
            return "A connected source is ready to sync. Cortex will keep new memory local and bring useful items to Review."
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
            return "Connect notes so Ask can answer with citations."
        }
        return "Connect your notes once. Cortex keeps them synced and brings new memory to Review."
    }

    private var actionTitle: String {
        if !state.isLocalServiceReady { return "Start Cortex" }
        if needsAttentionSources > 0 { return "Open Connections" }
        if dueSyncSources > 0 { return "Sync now" }
        if activeSources == 0 { return hasEmptySource ? "Choose notes" : "Connect notes" }
        if pendingCount > 0 { return "Review memory" }
        if hasMemory { return "Ask a question" }
        return canSyncSource ? "Sync notes" : "View notes"
    }

    private var actionDetail: String {
        if !state.isLocalServiceReady { return "Start Cortex on this Mac." }
        if needsAttentionSources > 0 {
            return "\(needsAttentionSources) source\(needsAttentionSources == 1 ? "" : "s") need attention"
        }
        if dueSyncSources > 0 {
            return "\(dueSyncSources) source\(dueSyncSources == 1 ? "" : "s") ready"
        }
        if activeSources == 0 {
            if hasEmptySource {
                return "Pick a folder with useful notes."
            }
            if state.connectedAIIntegrationCount > 0 {
                return "Synced notes give Ask something to cite."
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
        if needsAttentionSources > 0 { return "exclamationmark.circle" }
        if dueSyncSources > 0 { return "arrow.triangle.2.circlepath" }
        if activeSources == 0 { return hasEmptySource ? "folder.badge.questionmark" : "folder.badge.plus" }
        if pendingCount > 0 { return "checklist" }
        if hasMemory { return "magnifyingglass" }
        return canSyncSource ? "arrow.triangle.2.circlepath" : "info.circle"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.lg) {
            CortexStatusPill(
                label: statusSummary.label,
                systemImage: statusSummary.systemImage,
                color: statusSummary.color
            )

            VStack(alignment: .leading, spacing: CortexDesign.Space.sm) {
                Text(title)
                    .font(.system(size: 34, weight: .semibold))
                Text(detail)
                    .font(.title3)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 680, alignment: .leading)
            }

            HStack(alignment: .center, spacing: CortexDesign.Space.md) {
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

            VStack(alignment: .leading, spacing: CortexDesign.Space.xs) {
                HomeStatusRow(
                    title: "Source",
                    detail: sourceStatus.detail,
                    systemImage: sourceStatus.systemImage,
                    color: sourceStatus.color
                )
                Divider().overlay(CortexDesign.hairline)
                HomeStatusRow(
                    title: "Memory",
                    detail: memoryStatus.detail,
                    systemImage: memoryStatus.systemImage,
                    color: memoryStatus.color
                )
            }
            .cortexCard()
            .frame(maxWidth: 620, alignment: .leading)
        }
        .padding(.vertical, CortexDesign.Space.xl)
        .padding(.horizontal, CortexDesign.Space.xs)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var sourceStatus: (detail: String, systemImage: String, color: Color) {
        if needsAttentionSources > 0 {
            return ("\(needsAttentionSources) source\(needsAttentionSources == 1 ? "" : "s") need attention", "exclamationmark.triangle.fill", .orange)
        }
        if dueSyncSources > 0 {
            return ("\(dueSyncSources) connected source\(dueSyncSources == 1 ? "" : "s") ready to sync", "arrow.triangle.2.circlepath.circle.fill", .accentColor)
        }
        if activeSources > 0 {
            return ("\(activeSources) connected and syncing", "folder.fill.badge.checkmark", .green)
        }
        if hasEmptySource {
            return ("No usable content found", "folder.badge.questionmark", .orange)
        }
        return ("Notes not connected", "folder.badge.plus", .accentColor)
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
        } else if needsAttentionSources > 0 {
            state.openConnectionsPrivacy(statusMessage: "Check source connection")
        } else if dueSyncSources > 0 {
            state.openConnectionsPrivacy(statusMessage: "Sync connected sources")
        } else if activeSources == 0 {
            if let connector = obsidianConnector {
                state.connectLocalNotesFolder(connector, chooseNew: hasEmptySource)
            } else {
                state.openConnectionsPrivacy(statusMessage: "Connect notes")
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
                state.openConnectionsPrivacy(statusMessage: "Notes status")
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
