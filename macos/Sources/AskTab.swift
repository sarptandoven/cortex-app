import AppKit
import SwiftUI

struct AskTab: View {
    @ObservedObject var state: AppState
    @State private var citedMemoriesExpanded = false
    @State private var initialLoadDone = false
    @State private var isReloading = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: CortexDesign.Space.xl) {
                if hasReviewedMemory {
                    AskQuerySection(state: state)
                    AskMemoryContextStrip(state: state)

                    if state.isBusy {
                        AskLoadingCard()
                    } else if let askError = state.askError {
                        AskErrorCard(state: state, message: askError)
                    } else if state.hasSearched,
                              !state.searchQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        // Only show an answer that matches the query in the box — a result carried
                        // over from onboarding (shared state) with an empty field here reads as a
                        // stale answer the user never asked for on this screen.
                        // Personal "would I" questions get the twin's cited verdict first,
                        // then the ordinary answer beneath it.
                        if let twinPrediction = state.twinPrediction {
                            AskTwinPredictionCard(prediction: twinPrediction)
                        }
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
                        AskSuggestedQuestions(state: state)
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
            .frame(maxWidth: 680, alignment: .topLeading)
            .padding(CortexDesign.Space.xl)
            .padding(.top, CortexDesign.Space.xl)
            .frame(maxWidth: .infinity)
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
        // Suggested questions derive from recent memory — load it so the chips have data.
        await state.loadRecent()
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

struct AskQuerySection: View {
    @ObservedObject var state: AppState
    @FocusState private var queryFocused: Bool

    private var trimmedQuery: String {
        state.searchQuery.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        HStack(spacing: CortexDesign.Space.sm) {
            Image(systemName: "magnifyingglass")
                .font(.title3)
                .foregroundColor(queryFocused ? CortexDesign.accent : CortexDesign.inkSecondary)
            TextField("Ask about a project, person, or decision", text: $state.searchQuery)
                .textFieldStyle(.plain)
                .font(.title2)
                .focused($queryFocused)
                .onSubmit {
                    guard !state.isBusy, !trimmedQuery.isEmpty else { return }
                    state.runSearch()
                }
                .onChange(of: state.searchQuery) { newValue in
                    // Clearing the box clears the previous answer, so a stale result never lingers
                    // beneath an empty search field.
                    if newValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        state.clearAskResults()
                    }
                }
            if !state.searchQuery.isEmpty {
                Button {
                    state.searchQuery = ""
                    queryFocused = true
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundColor(CortexDesign.inkFaint)
                }
                .buttonStyle(.plain)
                .help("Clear")
            }
            Button {
                state.runSearch()
            } label: {
                Text("Ask")
                    .fontWeight(.semibold)
                    .frame(minWidth: 76, minHeight: 40)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(state.isBusy || trimmedQuery.isEmpty)
        }
        .padding(.horizontal, CortexDesign.Space.md)
        .padding(.vertical, CortexDesign.Space.sm)
        .frame(minHeight: 60)
        .background(CortexDesign.panelBackground)
        // The brand focus cue: a ruled bottom line that turns wax-red when the field is active.
        // Placed before the clip so the rule's ends follow the card's rounded corners.
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(queryFocused ? CortexDesign.accent : CortexDesign.hairline)
                .frame(height: 2)
        }
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .stroke(CortexDesign.hairline, lineWidth: 1)
        )
        .animation(.easeOut(duration: 0.15), value: queryFocused)
        .onAppear {
            // Focus after the field joins the hierarchy — an immediate assignment is
            // silently dropped on macOS 13.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
                queryFocused = true
            }
        }
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
        if needsAttentionCount > 0 { return CortexDesign.accent }
        if pendingCount > 0 { return CortexDesign.gold }
        if memoryCount > 0 { return CortexDesign.sealMoss }
        return CortexDesign.inkFaint
    }

    private var statusIcon: String {
        if needsAttentionCount > 0 { return "exclamationmark.circle.fill" }
        if pendingCount > 0 { return "tray.full.fill" }
        if memoryCount > 0 { return "checkmark.seal.fill" }
        return "circle"
    }

    var body: some View {
        // Healthy = nothing at all — the question field is the star. The diagnostic card only
        // appears when something actually needs the user (a source needs attention or items
        // wait in Review).
        if needsAttentionCount > 0 || (memoryCount == 0 && pendingCount > 0) {
            fullDiagnosticCard
        }
    }

    private var fullDiagnosticCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: statusIcon)
                    .font(.headline)
                    .foregroundColor(statusColor)
                    .frame(width: 28, height: 28)
                    .background(statusColor.opacity(0.11))
                    .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md))

                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(CortexDesign.Typography.title)
                        .foregroundColor(CortexDesign.ink)
                    Text(detail)
                        .font(CortexDesign.Typography.body)
                        .foregroundColor(CortexDesign.inkSecondary)
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
                            .font(CortexDesign.Typography.caption)
                            .foregroundColor(CortexDesign.inkSecondary)
                            .padding(.horizontal, 9)
                            .padding(.vertical, 5)
                            .background(CortexDesign.panelBackground)
                            .clipShape(Capsule())
                            .overlay(Capsule().stroke(CortexDesign.hairline, lineWidth: 1))
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
                .foregroundColor(CortexDesign.inkSecondary)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(.caption2)
                    .foregroundColor(CortexDesign.inkSecondary)
                Text(value)
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(CortexDesign.ink)
                    .lineLimit(1)
                    .minimumScaleFactor(0.82)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
        .background(CortexDesign.quietBackground)
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md))
    }
}

struct AskSourceConfidenceChip: View {
    let source: SourceReadinessItem

    var body: some View {
        Text(label)
            .font(CortexDesign.Typography.caption)
            .foregroundColor(CortexDesign.inkSecondary)
            .lineLimit(1)
            .truncationMode(.tail)
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .background(CortexDesign.panelBackground)
            .clipShape(Capsule())
            .overlay(Capsule().stroke(CortexDesign.hairline, lineWidth: 1))
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
            Text("Finding a cited answer…")
                .font(CortexDesign.Typography.body)
                .foregroundColor(CortexDesign.ink)
            Spacer(minLength: 0)
        }
        .cortexCard(padding: 18, background: CortexDesign.panelBackground)
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
                    .foregroundColor(CortexDesign.accent)
                    .frame(width: 28, height: 28)
                    .background(CortexDesign.accentSoft)
                    .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md))
                VStack(alignment: .leading, spacing: 4) {
                    Text("Ask could not reach your memory")
                        .font(CortexDesign.Typography.title)
                        .foregroundColor(CortexDesign.ink)
                    Text(message)
                        .font(CortexDesign.Typography.body)
                        .foregroundColor(CortexDesign.inkSecondary)
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
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md)
                .stroke(CortexDesign.accent.opacity(0.35))
        )
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md))
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
                AskQuietState(
                    title: "Matching memory found",
                    detail: "Cortex found related memory, but no answer was returned. Open sources below."
                )
            }

            // Only when the answer lacks its own citation ledger — beneath a cited answer this
            // list merely duplicates the footnotes, so the page ends at them instead.
            if !state.searchResults.isEmpty && (state.askAnswer.isEmpty || state.askCitations.isEmpty) {
                // Quiet secondary label on the toggle only — the rows inside keep full contrast.
                DisclosureGroup(isExpanded: $citedMemoriesExpanded) {
                    AskResultsSection(state: state)
                        .frame(maxHeight: 280)
                        .padding(.top, 8)
                } label: {
                    Text(memoryDisclosureTitle)
                        .font(.callout)
                        .foregroundColor(CortexDesign.inkSecondary)
                }
            }
        }
    }

    private var memoryDisclosureTitle: String {
        "Related memory (\(state.searchResults.count))"
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
            AskQuietState(title: title, detail: detail)

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

/// Ask's empty states in the archive voice: a serif display title over SF body detail, set on an
/// index card. Local to Ask so the shared QuietState's call sites elsewhere stay untouched.
private struct AskQuietState: View {
    let title: String
    let detail: String
    var systemImage: String = "sparkles"

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: systemImage)
                .font(.system(size: 26, weight: .regular))
                .foregroundColor(CortexDesign.accent.opacity(0.55))
            Text(title)
                .font(CortexDesign.Typography.display(20))
                .foregroundColor(CortexDesign.ink)
                .multilineTextAlignment(.center)
            Text(detail)
                .font(CortexDesign.Typography.body)
                .foregroundColor(CortexDesign.inkSecondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: 460)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 26)
        .padding(.horizontal, 18)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .stroke(CortexDesign.hairline, lineWidth: 1)
        )
    }
}

/// Clickable "Try asking" questions drawn from the user's own reviewed memory —
/// one click gets a first cited answer instead of a blank page.
struct AskSuggestedQuestions: View {
    @ObservedObject var state: AppState

    var body: some View {
        let suggestions = state.onboardingAskSuggestions
        if !suggestions.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("Try asking")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(CortexDesign.inkSecondary)
                ForEach(suggestions.prefix(2), id: \.self) { suggestion in
                    AskSuggestionChip(text: suggestion) {
                        state.searchQuery = suggestion
                        state.runSearch()
                    }
                }
            }
        }
    }
}

struct AskSuggestionChip: View {
    let text: String
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: "sparkle.magnifyingglass")
                    .font(.caption)
                    .foregroundColor(CortexDesign.accent)
                Text(text)
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.ink)
                    .lineLimit(1)
                    .truncationMode(.tail)
                Spacer(minLength: 0)
                Image(systemName: "arrow.right")
                    .font(.caption2)
                    .foregroundColor(CortexDesign.inkFaint)
                    .opacity(hovering ? 1 : 0)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(hovering ? CortexDesign.accentSoft : CortexDesign.panelBackground)
            .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                    .stroke(CortexDesign.hairline, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
    }
}

struct AskResultsSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        Group {
            if state.searchResults.isEmpty && !state.hasSearched {
                AskQuietState(title: "Ask your notes", detail: "Ask about a project, person, decision, or detail from reviewed notes.")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if state.searchResults.isEmpty {
                AskQuietState(title: "No cited result matched", detail: "Review new synced items or try a more specific question.")
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
                    .foregroundColor(CortexDesign.accent)
                    .frame(width: 28, height: 28)
                    .background(CortexDesign.accentSoft)
                    .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md))
                Text(sourceTitle)
                    .font(.callout)
                    .fontWeight(.semibold)
                    .foregroundColor(CortexDesign.ink)
                    .lineLimit(1)
                Spacer(minLength: 0)
            }
            let display = MemoryText.displayContent(item.content)
            Text(display.headline)
                .font(CortexDesign.Typography.body)
                .foregroundColor(CortexDesign.ink)
                .lineLimit(display.path == nil ? 6 : 2)
                .truncationMode(.middle)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            if let path = display.path {
                Text(path)
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundColor(CortexDesign.inkFaint)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(MemoryText.unwrap(item.content))
            }
            if let citation = citationLabel {
                HStack(spacing: 6) {
                    Image(systemName: "link")
                        .font(.caption2)
                    Text(citation)
                        .font(CortexDesign.Typography.caption)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer(minLength: 0)
                }
                .foregroundColor(CortexDesign.inkSecondary)
                .help(item.source_url ?? citation)
            }
        }
        .padding(12)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: CortexDesign.Radius.md).stroke(CortexDesign.hairline, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md))
    }

    private var sourceTitle: String {
        item.source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "Cited source" : item.source
    }

    private var citationLabel: String? {
        CitationDisplay.label(sourceURL: item.source_url)
    }
}

struct AskAnswerPanel: View {
    let answer: String
    let citations: [AskCitationItem]
    /// The question echoed back in italic serif above the answer — an annotated reply, not a
    /// bare result. Defaults nil so existing call sites render unchanged.
    var question: String? = nil

    @State private var showAllCitations = false
    @State private var justCopied = false
    @State private var hovering = false

    private static let collapsedCitationCount = 3

    private var visibleCitations: [AskCitationItem] {
        showAllCitations ? citations : Array(citations.prefix(Self.collapsedCitationCount))
    }

    private var echoedQuestion: String? {
        guard let trimmed = question?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty else { return nil }
        return trimmed
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .firstTextBaseline) {
                Text(verbatim: "ANSWER")
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.inkFaint)
                Spacer()
                // Copy is a post-reading action — revealed only while the pointer is over the
                // panel, so the answer opens as prose instead of chrome.
                Button {
                    copyAnswerWithSources()
                } label: {
                    Label(justCopied ? "Copied" : "Copy",
                          systemImage: justCopied ? "checkmark" : "doc.on.doc")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .disabled(justCopied)
                .help("Copy the answer with its sources")
                .opacity(hovering || justCopied ? 1 : 0)
                .animation(.easeOut(duration: 0.12), value: hovering)
            }
            if let echoedQuestion {
                Text(echoedQuestion)
                    .font(.system(size: 14, design: .serif))
                    .italic()
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 620, alignment: .leading)
            }
            Text(answer)
                .font(CortexDesign.Typography.prose(14.5))
                .lineSpacing(3.5)
                .foregroundColor(CortexDesign.ink)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: 620, alignment: .leading)
            if !citations.isEmpty {
                // The footnote ledger: a short divider rule ends the page the way a book does,
                // then numbered footnotes with dotted leaders out to their line ranges.
                Rectangle()
                    .fill(CortexDesign.hairline)
                    .frame(width: 56, height: 1)
                VStack(alignment: .leading, spacing: 6) {
                    Text(verbatim: "CITATIONS")
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.8)
                        .foregroundColor(CortexDesign.inkFaint)
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
        .padding(CortexDesign.Space.lg)
        .background(CortexDesign.panelBackground)
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md)
                .stroke(CortexDesign.hairline, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md))
        .onHover { hovering = $0 }
        .onChange(of: answer) { _ in
            showAllCitations = false
            justCopied = false
        }
    }

    private func copyAnswerWithSources() {
        var text = answer
        if !citations.isEmpty {
            let lines = citations.map { citation -> String in
                let label = CitationDisplay.label(
                    path: citation.citation_path,
                    sourceURL: citation.source_url,
                    fallback: citation.source
                ) ?? citation.source
                return "[\(citation.index)] \(label)"
            }
            text += "\n\nSources:\n" + lines.joined(separator: "\n")
        }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        justCopied = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
            justCopied = false
        }
    }
}

struct AskCitationRow: View {
    let citation: AskCitationItem
    @State private var hovering = false

    /// The user-openable source for this citation, if any. Internal-only provenance
    /// (e.g. cortex-capture://) returns nil and the row stays plain text.
    private var openableURL: URL? {
        CitationDisplay.openableURL(path: citation.citation_path, sourceURL: citation.source_url)
    }

    var body: some View {
        if let url = openableURL {
            Button {
                NSWorkspace.shared.open(url)
            } label: {
                rowContent(openable: true)
            }
            .buttonStyle(.plain)
            .help("Open source")
            .accessibilityAddTraits(.isLink)
        } else {
            rowContent(openable: false)
        }
    }

    private func rowContent(openable: Bool) -> some View {
        // A footnote ledger entry: mono wax-red numeral, SF title in ink, then a dotted leader
        // running out to a right-aligned mono line-range/date — a table-of-contents line.
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("\(citation.index).")
                .font(CortexDesign.Typography.stamp)
                .foregroundColor(CortexDesign.accent)
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(sourceLabel)
                        .font(.system(size: 12))
                        .foregroundColor(CortexDesign.ink)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    if openable {
                        Image(systemName: "arrow.up.right.square")
                            .font(.caption2)
                            .foregroundColor(CortexDesign.accent)
                    }
                    if let trailing = leaderLabel {
                        AskDottedLeader()
                            .stroke(CortexDesign.hairline, style: StrokeStyle(lineWidth: 1, dash: [1, 3]))
                            .frame(height: 3)
                            .frame(minWidth: 12)
                        Text(trailing.uppercased())
                            .font(CortexDesign.Typography.stamp)
                            .kerning(0.8)
                            .foregroundColor(CortexDesign.inkFaint)
                            .lineLimit(1)
                            .layoutPriority(1)
                    } else {
                        Spacer(minLength: 0)
                    }
                }
                if let detail = sourceDetail {
                    Text(detail)
                        .font(.caption2)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                if !excerpt.isEmpty {
                    Text(excerpt)
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 6)
        .padding(.vertical, 4)
        .background(
            RoundedRectangle(cornerRadius: 6, style: .continuous)
                .fill(hovering && openable ? CortexDesign.accentSoft : Color.clear)
        )
        .contentShape(Rectangle())
        .onHover { inside in
            hovering = inside
        }
    }

    private var sourceLabel: String {
        // Line numbers are NOT passed here — sourceDetail owns the line label in all cases, so the
        // range can never print twice ("file.md - line 12" + "Line 12").
        CitationDisplay.label(
            path: citation.citation_path,
            sourceURL: citation.source_url,
            fallback: citation.source
        ) ?? citation.source
    }

    /// The right end of the dotted leader: the line range where available, else the memory's
    /// date — never fabricated, omitted entirely when neither is known.
    private var leaderLabel: String? {
        if let lineLabel { return lineLabel }
        let date = (citation.occurred_at ?? citation.captured_at)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard let date, !date.isEmpty else { return nil }
        return shortDate(date)
    }

    private var sourceDetail: String? {
        // The dotted leader owns the line label (leaderLabel above), so the range can never
        // print twice ("file.md - line 12" + a "LINE 12" leader).
        let pieces = [
            citation.section_title?.trimmingCharacters(in: .whitespacesAndNewlines),
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
        let raw = citation.excerpt.trimmingCharacters(in: .whitespacesAndNewlines)
        // A bare file path adds nothing under a row that already names the source — suppress it.
        if MemoryText.isPathLike(raw) { return "" }
        return raw
    }
}

/// The dotted leader of a footnote ledger row — a hairline of 1pt dots stretching between the
/// source title and its right-aligned line range, like a table-of-contents line.
private struct AskDottedLeader: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: 0, y: rect.midY))
        path.addLine(to: CGPoint(x: rect.width, y: rect.midY))
        return path
    }
}
