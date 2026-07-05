import SwiftUI

struct AskTab: View {
    @ObservedObject var state: AppState
    @State private var citedMemoriesExpanded = false
    @State private var initialLoadDone = false
    @State private var isReloading = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
                AskHeaderSection()
                if hasReviewedMemory {
                    AskQuerySection(state: state)
                    AskMemoryContextStrip(state: state)

                    if state.isBusy {
                        AskLoadingCard()
                    } else if let askError = state.askError {
                        AskErrorCard(state: state, message: askError)
                    } else if state.hasSearched {
                        AskResponseSection(
                            state: state,
                            citedMemoriesExpanded: $citedMemoriesExpanded
                        )
                    } else {
                        AskEmptyGuidance(
                            state: state,
                            title: "Ask your notes",
                            detail: "Ask about a project, person, decision, or detail from your reviewed notes. Cortex answers with sources.",
                            showActionsWhenMemoryExists: false
                        )
                    }
                } else {
                    AskEmptyGuidance(
                        state: state,
                        title: askSetupTitle,
                        detail: askSetupDetail,
                        showActionsWhenMemoryExists: false
                    )
                }
            }
            .frame(maxWidth: .infinity, alignment: .topLeading)
            .padding(CortexDesign.Space.md)
        }
        .task {
            await reload()
            initialLoadDone = true
        }
        .onChange(of: state.selectedTab) { tab in
            guard tab == .ask, initialLoadDone else { return }
            Task { await reload() }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(CortexDesign.appBackground)
    }

    private func reload() async {
        guard !isReloading else { return }
        isReloading = true
        defer { isReloading = false }
        await state.loadSourceConnectivity()
        await state.loadReview()
        await state.loadStats()
    }

    private var hasReviewedMemory: Bool {
        state.onboardingHasReviewedMemory
            || (state.review?.stats.memories ?? 0) > 0
            || (state.stats?.memories ?? 0) > 0
    }

    private var pendingReviewCount: Int {
        max(state.inbox.count, state.review?.stats.pending_captures ?? 0)
    }

    private var askSetupTitle: String {
        if pendingReviewCount > 0 { return "Review memory first" }
        if state.hasConnectedSourceAccount || state.hasConnectedObsidianVault { return "Sync memory first" }
        return "Connect notes first"
    }

    private var askSetupDetail: String {
        if pendingReviewCount > 0 {
            return "Approve one useful memory in Review. Then Ask can answer with citations."
        }
        if state.hasConnectedSourceAccount || state.hasConnectedObsidianVault {
            return "Cortex needs reviewed memory before Ask can answer. Sync your source, then approve one useful item."
        }
        return "Choose notes or a connected source. Cortex will sync locally, send useful memory to Review, and only then answer with sources."
    }
}

struct AskHeaderSection: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Ask Cortex")
                .font(.title3)
                .fontWeight(.semibold)
            Text("Cortex answers from reviewed notes and shows sources.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct AskQuerySection: View {
    @ObservedObject var state: AppState

    private var trimmedQuery: String {
        state.searchQuery.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        HStack(spacing: CortexDesign.Space.md) {
            TextField("Ask about a project, person, decision, or phrase", text: $state.searchQuery)
                .textFieldStyle(.roundedBorder)
                .font(.title3)
                .frame(minHeight: 52)
                .onSubmit {
                    guard !state.isBusy, !trimmedQuery.isEmpty else { return }
                    state.runSearch()
                }
            Button {
                state.runSearch()
            } label: {
                Label("Ask", systemImage: "magnifyingglass")
                    .frame(minWidth: 104, minHeight: 52)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(state.isBusy || trimmedQuery.isEmpty)
        }
        .cortexCard(padding: CortexDesign.Space.md, background: CortexDesign.panelBackground)
    }

}

struct AskMemoryContextStrip: View {
    @ObservedObject var state: AppState

    private var report: SourceReadinessResponse? {
        state.sourceReadinessReport
    }

    private var relevantSources: [SourceReadinessItem] {
        guard let report else { return [] }
        return report.sources
            .filter { source in
                // A source is relevant only if it has data OR is genuinely connected — never for a
                // placeholder account that was created but never signed into (status "available"/
                // "import_ready"). Raw account count includes those placeholders, so gate on status.
                source.active_memories > 0 || source.pending > 0
                    || ["connected", "synced", "syncing", "imported", "needs_review"].contains(source.status)
            }
            .sorted { lhs, rhs in
                if lhs.active_memories != rhs.active_memories {
                    return lhs.active_memories > rhs.active_memories
                }
                return lhs.name < rhs.name
            }
    }

    private var sourceCount: Int {
        relevantSources.count
    }

    private var memoryCount: Int {
        report?.summary.active_memories ?? state.review?.stats.memories ?? state.stats?.memories ?? 0
    }

    private var pendingCount: Int {
        report?.summary.needs_review ?? state.review?.stats.pending_captures ?? state.inbox.count
    }

    private var needsAttentionCount: Int {
        report?.summary.needs_attention ?? 0
    }

    private var dueCount: Int {
        relevantSources.filter { $0.sync_plan?.due_now == true }.count
    }

    private var averageCitationCoverage: Double? {
        let sourcesWithMemory = relevantSources.filter { $0.active_memories > 0 }
        guard !sourcesWithMemory.isEmpty else { return nil }
        let cited = sourcesWithMemory.reduce(0.0) { total, source in
            total + (source.citation_coverage * Double(max(source.active_memories, 0)))
        }
        let memories = sourcesWithMemory.reduce(0) { $0 + max($1.active_memories, 0) }
        guard memories > 0 else { return nil }
        return cited / Double(memories)
    }

    private var latestSync: String? {
        let values = relevantSources.compactMap { source in
            source.sync_plan?.last_completed_at ?? source.last_seen_at
        }
        return values.sorted().last
    }

    private var title: String {
        if memoryCount > 0 { return "Memory ready for Ask" }
        if pendingCount > 0 { return "Review memory before Ask" }
        if sourceCount > 0 { return "Source connected" }
        return "No reviewed memory yet"
    }

    private var detail: String {
        if memoryCount > 0 {
            let sourceLabel = "\(sourceCount) source\(sourceCount == 1 ? "" : "s")"
            return "Using \(memoryCount) reviewed memor\(memoryCount == 1 ? "y" : "ies") from \(sourceLabel)."
        }
        if pendingCount > 0 {
            return "\(pendingCount) synced item\(pendingCount == 1 ? "" : "s") waiting in Review before Ask can use them."
        }
        if sourceCount > 0 {
            return "Cortex is connected, but reviewed memory is not ready yet."
        }
        return "Connect notes or a source, then approve useful memory in Review."
    }

    private var statusColor: Color {
        if needsAttentionCount > 0 { return .orange }
        if pendingCount > 0 { return .orange }
        if memoryCount > 0 { return .green }
        return .secondary
    }

    private var statusIcon: String {
        if needsAttentionCount > 0 { return "exclamationmark.circle.fill" }
        if pendingCount > 0 { return "tray.full.fill" }
        if memoryCount > 0 { return "checkmark.seal.fill" }
        return "circle"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: statusIcon)
                    .font(.headline)
                    .foregroundColor(statusColor)
                    .frame(width: 28, height: 28)
                    .background(statusColor.opacity(0.11))
                    .clipShape(RoundedRectangle(cornerRadius: 8))

                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(.headline)
                    Text(detail)
                        .font(.callout)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Spacer(minLength: 0)
            }

            HStack(spacing: 8) {
                AskContextMetric(
                    title: "Freshness",
                    value: freshnessLabel,
                    systemImage: "clock.arrow.circlepath"
                )
                AskContextMetric(
                    title: "Citation confidence",
                    value: citationConfidenceLabel,
                    systemImage: "link.badge.plus"
                )
                AskContextMetric(
                    title: "Source health",
                    value: sourceHealthLabel,
                    systemImage: "waveform.path.ecg"
                )
            }

            if !relevantSources.isEmpty {
                HStack(spacing: 6) {
                    ForEach(Array(relevantSources.prefix(3))) { source in
                        AskSourceConfidenceChip(source: source)
                    }
                    if relevantSources.count > 3 {
                        Text("+\(relevantSources.count - 3) more")
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .padding(.horizontal, 9)
                            .padding(.vertical, 5)
                            .background(Color(nsColor: .controlBackgroundColor))
                            .clipShape(Capsule())
                    }
                    Spacer(minLength: 0)
                }
            }
        }
        .cortexCard(padding: CortexDesign.Space.md, background: CortexDesign.panelBackground)
    }

    private var freshnessLabel: String {
        guard let latestSync else {
            return sourceCount > 0 ? "Waiting for first sync" : "No source"
        }
        return "Synced \(shortDate(latestSync))"
    }

    private var citationConfidenceLabel: String {
        guard let averageCitationCoverage else {
            return memoryCount > 0 ? "Needs citations" : "No memory"
        }
        return "\(Int((averageCitationCoverage * 100).rounded()))% cited"
    }

    private var sourceHealthLabel: String {
        if needsAttentionCount > 0 {
            return "\(needsAttentionCount) needs attention"
        }
        if dueCount > 0 {
            return "\(dueCount) sync due"
        }
        if pendingCount > 0 {
            return "\(pendingCount) in Review"
        }
        if sourceCount > 0 {
            return "Healthy"
        }
        return "Not connected"
    }
}

struct AskContextMetric: View {
    let title: String
    let value: String
    let systemImage: String

    var body: some View {
        HStack(spacing: 7) {
            Image(systemName: systemImage)
                .font(.caption)
                .foregroundColor(.secondary)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                Text(value)
                    .font(.caption)
                    .fontWeight(.semibold)
                    .lineLimit(1)
                    .minimumScaleFactor(0.82)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.55))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct AskSourceConfidenceChip: View {
    let source: SourceReadinessItem

    var body: some View {
        Text(label)
            .font(.caption)
            .foregroundColor(.secondary)
            .lineLimit(1)
            .truncationMode(.tail)
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(Capsule())
            .help(helpText)
    }

    private var label: String {
        let cited = "\(Int((source.citation_coverage * 100).rounded()))% cited"
        if let lastSeen = source.sync_plan?.last_completed_at ?? source.last_seen_at {
            return "\(source.name) · \(cited) · \(shortDate(lastSeen))"
        }
        return "\(source.name) · \(cited)"
    }

    private var helpText: String {
        let memoryLabel = "\(source.active_memories) reviewed memor\(source.active_memories == 1 ? "y" : "ies")"
        if let warning = source.warnings.first, !warning.isEmpty {
            return "\(source.name): \(memoryLabel). \(warning)"
        }
        return "\(source.name): \(memoryLabel). \(source.syncPlanDisplayTitle)."
    }
}

private func shortDate(_ value: String) -> String {
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return "recently" }
    return String(trimmed.prefix(10))
}

struct AskLoadingCard: View {
    var body: some View {
        HStack(spacing: 12) {
            ProgressView()
                .controlSize(.small)
            Text("Searching…")
                .font(.body)
                .foregroundColor(.secondary)
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(18)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct AskErrorCard: View {
    @ObservedObject var state: AppState
    let message: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.headline)
                    .foregroundColor(.orange)
                    .frame(width: 28, height: 28)
                    .background(Color.orange.opacity(0.11))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                VStack(alignment: .leading, spacing: 4) {
                    Text("Ask could not reach your memory")
                        .font(.headline)
                    Text(message)
                        .font(.callout)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }

            HStack(spacing: 10) {
                Spacer(minLength: 0)
                Button {
                    state.runSearch()
                } label: {
                    Label("Retry", systemImage: "arrow.clockwise")
                        .frame(minWidth: 120, minHeight: 44)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(state.isBusy)
            }
        }
        .padding(12)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.orange.opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct AskResponseSection: View {
    @ObservedObject var state: AppState
    @Binding var citedMemoriesExpanded: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !state.askAnswer.isEmpty {
                AskAnswerPanel(
                    answer: state.askAnswer,
                    citations: state.askCitations
                )
            } else if state.searchResults.isEmpty {
                AskEmptyGuidance(
                    state: state,
                    title: "No cited answer found",
                    detail: "I could not find that in reviewed notes yet. Review new synced items or try a more specific question.",
                    showActionsWhenMemoryExists: true
                )
            } else {
                QuietState(
                    title: "Matching memory found",
                    detail: "Cortex found related memory, but no answer was returned. Open sources below."
                )
            }

            if !state.searchResults.isEmpty {
                DisclosureGroup(memoryDisclosureTitle, isExpanded: $citedMemoriesExpanded) {
                    AskResultsSection(state: state)
                        .frame(maxHeight: 280)
                        .padding(.top, 8)
                }
            }
        }
    }

    private var memoryDisclosureTitle: String {
        "Sources (\(state.searchResults.count))"
    }
}

struct AskEmptyGuidance: View {
    @ObservedObject var state: AppState
    let title: String
    let detail: String
    let showActionsWhenMemoryExists: Bool

    private var approvedMemoryCount: Int {
        state.review?.stats.memories ?? state.stats?.memories ?? 0
    }

    private var pendingReviewCount: Int {
        max(state.inbox.count, state.review?.stats.pending_captures ?? 0)
    }

    private var obsidianConnector: SourceConnectorCatalogItem? {
        state.sourceConnectorCatalog.first { $0.id == "obsidian" }
    }

    private func startNotesSync() {
        if let connector = obsidianConnector {
            state.connectLocalNotesFolder(connector, chooseNew: state.notesNeedContent)
        } else {
            state.openConnectionsPrivacy(statusMessage: "Connect notes")
        }
    }

    var body: some View {
        VStack(spacing: 12) {
            QuietState(title: title, detail: detail)

            if approvedMemoryCount == 0 || showActionsWhenMemoryExists {
                HStack(spacing: 10) {
                    if approvedMemoryCount == 0 {
                        if pendingReviewCount > 0 {
                            Button {
                                state.selectedTab = .review
                                state.status = "Review memory"
                            } label: {
                                Label("Review memory", systemImage: "checklist")
                                    .frame(minWidth: 164, minHeight: 46)
                            }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.large)
                        } else {
                            Button {
                                startNotesSync()
                            } label: {
                                Label(primarySourceActionTitle, systemImage: primarySourceActionIcon)
                                    .frame(minWidth: 172, minHeight: 46)
                            }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.large)
                        }
                    } else {
                        Button {
                            state.selectedTab = .review
                            state.status = "Review more memory"
                        } label: {
                            Label("Review memory", systemImage: "checklist")
                                .frame(minWidth: 150, minHeight: 46)
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)

                        Button {
                            startNotesSync()
                        } label: {
                            Label("Sync notes", systemImage: "folder.badge.plus")
                                .frame(minWidth: 168, minHeight: 46)
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.large)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .center)
            }
        }
    }

    private var primarySourceActionTitle: String {
        if state.notesNeedContent { return "Choose notes" }
        if state.hasConnectedObsidianVault || state.hasConnectedSourceAccount { return "Sync notes" }
        return "Connect notes"
    }

    private var primarySourceActionIcon: String {
        if state.notesNeedContent { return "folder.badge.questionmark" }
        if state.hasConnectedObsidianVault || state.hasConnectedSourceAccount { return "arrow.triangle.2.circlepath" }
        return "folder.badge.plus"
    }
}

struct AskResultsSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        Group {
            if state.searchResults.isEmpty && !state.hasSearched {
                QuietState(title: "Ask your notes", detail: "Ask about a project, person, decision, or detail from reviewed notes.")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if state.searchResults.isEmpty {
                QuietState(title: "No cited result matched", detail: "Review new synced items or try a more specific question.")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        ForEach(state.searchResults) { item in
                            AskSourceDetailRow(item: item)
                        }
                    }
                }
            }
        }
    }
}

struct AskSourceDetailRow: View {
    let item: MemoryItem

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center, spacing: 8) {
                Image(systemName: "quote.bubble")
                    .font(.callout)
                    .foregroundColor(.accentColor)
                    .frame(width: 28, height: 28)
                    .background(Color.accentColor.opacity(0.11))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                VStack(alignment: .leading, spacing: 2) {
                    Text(sourceTitle)
                        .font(.callout)
                        .fontWeight(.semibold)
                        .lineLimit(1)
                    Text(detailLine)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
            }
            Text(item.content)
                .font(.body)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            if let citation = citationLabel {
                HStack(spacing: 6) {
                    Image(systemName: "link")
                        .font(.caption2)
                    Text(citation)
                        .font(.caption)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer(minLength: 0)
                }
                .foregroundColor(.secondary)
                .help(item.source_url ?? citation)
            }
        }
        .padding(12)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.32)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var sourceTitle: String {
        item.source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "Cited source" : item.source
    }

    private var detailLine: String {
        let layer = (item.layer ?? item.kind).trimmingCharacters(in: .whitespacesAndNewlines)
        let kind = item.kind.trimmingCharacters(in: .whitespacesAndNewlines)
        if !layer.isEmpty, layer != kind {
            return "\(layer.capitalized) memory"
        }
        return kind.isEmpty ? "Reviewed memory" : "\(kind.capitalized) memory"
    }

    private var citationLabel: String? {
        CitationDisplay.label(sourceURL: item.source_url)
    }
}

struct AskAnswerPanel: View {
    let answer: String
    let citations: [AskCitationItem]

    @State private var showAllCitations = false

    private static let collapsedCitationCount = 3

    private var visibleCitations: [AskCitationItem] {
        showAllCitations ? citations : Array(citations.prefix(Self.collapsedCitationCount))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Label("Answer", systemImage: "quote.bubble")
                    .font(.headline)
                Spacer()
            }
            Text(answer)
                .font(.body)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            if !citations.isEmpty {
                Divider()
                VStack(alignment: .leading, spacing: 6) {
                    Text("Citations")
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(.secondary)
                    ForEach(visibleCitations) { citation in
                        AskCitationRow(citation: citation)
                    }
                    if citations.count > Self.collapsedCitationCount {
                        Button(showAllCitations ? "Show fewer citations" : "Show all \(citations.count) citations") {
                            showAllCitations.toggle()
                        }
                        .buttonStyle(.plain)
                        .font(.caption)
                        .foregroundColor(CortexDesign.accent)
                    }
                }
            }
        }
        .padding(12)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .onChange(of: answer) { _ in
            showAllCitations = false
        }
    }
}

struct AskCitationRow: View {
    let citation: AskCitationItem

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text("[\(citation.index)]")
                .font(.caption)
                .fontWeight(.semibold)
                .foregroundColor(.secondary)
                .monospacedDigit()
            VStack(alignment: .leading, spacing: 2) {
                Text(sourceLabel)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                if let detail = sourceDetail {
                    Text(detail)
                        .font(.caption2)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                if !excerpt.isEmpty {
                    Text(excerpt)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
    }

    private var sourceLabel: String {
        CitationDisplay.label(
            path: citation.citation_path,
            sourceURL: citation.source_url,
            fallback: citation.source,
            lineStart: citation.line_start,
            lineEnd: citation.line_end
        ) ?? citation.source
    }

    private var sourceDetail: String? {
        let pieces = [
            citation.section_title?.trimmingCharacters(in: .whitespacesAndNewlines),
            citation.citation_path == nil ? lineLabel : nil,
            citation.record_scope?.trimmingCharacters(in: .whitespacesAndNewlines)
        ]
        let detail = pieces.compactMap { value -> String? in
            guard let value, !value.isEmpty else { return nil }
            return value
        }.joined(separator: " - ")
        return detail.isEmpty ? nil : detail
    }

    private var lineLabel: String? {
        guard let start = citation.line_start else { return nil }
        if let end = citation.line_end, end > start {
            return "Lines \(start)-\(end)"
        }
        return "Line \(start)"
    }

    private var excerpt: String {
        citation.excerpt.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
