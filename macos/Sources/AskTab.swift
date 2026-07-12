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
                            detail: "Cortex answers from your reviewed notes, with sources.",
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
            return "Approve one useful memory in Review, then Ask can answer with citations."
        }
        if state.hasConnectedSourceAccount || state.hasConnectedObsidianVault {
            return "Sync your source, then approve one useful item in Review."
        }
        return "Connect notes or a source. Cortex answers only from reviewed memory."
    }
}

struct AskQuerySection: View {
    @ObservedObject var state: AppState
    @FocusState private var queryFocused: Bool

    private var trimmedQuery: String {
        state.searchQuery.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.xs) {
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
                CortexIconButton(systemImage: "xmark.circle.fill", role: .ghost, size: .small, help: "Clear") {
                    state.searchQuery = ""
                    queryFocused = true
                }
            }
            // The one primary action on this surface. Its label switches to "Searching…" in
            // flight so the click is acknowledged in place, with the spinner line just below.
            CortexButton(title: state.isBusy ? "Searching…" : "Ask", role: .primary, size: .large) {
                state.runSearch()
            }
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

        // A clear, unmissable in-flight line right under the field: the moment the user asks,
        // a small spinner and "Searching your memory…" confirm the work started.
        if state.isBusy {
            HStack(spacing: CortexDesign.Space.xs) {
                ProgressView()
                    .controlSize(.small)
                    .scaleEffect(0.7)
                    .frame(width: 14, height: 14)
                Text("Searching your memory…")
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
            }
            .padding(.horizontal, CortexDesign.Space.xs)
            .transition(.opacity)
            .accessibilityElement()
            .accessibilityLabel("Searching your memory")
        }
        }
        .animation(.easeOut(duration: 0.15), value: state.isBusy)
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
    @State private var detailsExpanded = false

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
        // Healthy = nothing at all — the question field is the star. When something actually
        // needs the user (a source needs attention or items wait in Review), ONE quiet line
        // appears; the telemetry tucks behind a small "Details" disclosure.
        if needsAttentionCount > 0 || (memoryCount == 0 && pendingCount > 0) {
            compactDiagnosticLine
        }
    }

    private var compactDiagnosticLine: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Image(systemName: statusIcon)
                    .font(.caption)
                    .foregroundColor(statusColor)
                Text(title)
                    .font(CortexDesign.Typography.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(CortexDesign.ink)
                    .lineLimit(1)
                Text(detail)
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                Spacer(minLength: 0)
            }

            // The telemetry tucks behind the custom serif hairline expander (no native disclosure).
            AskHairlineExpander(
                title: "Details",
                collapseTitle: "Hide details",
                isExpanded: $detailsExpanded
            ) {
                VStack(alignment: .leading, spacing: 8) {
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
                .padding(.top, 6)
            }
        }
        .cortexCard(padding: CortexDesign.Space.sm, background: CortexDesign.panelBackground)
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

/// A wax-red scan line sweeping a skeleton citation ledger — replaces the stock spinner. No
/// bitmap, no `.random`: the sweep is a repeatForever offset on a single soft wax-red band, and
/// the ledger rows are quiet hairline blocks shaped like the real answer (a title bar, prose
/// lines, then three numbered footnote stubs) so the wait previews the page that's coming.
struct AskLoadingCard: View {
    @State private var sweep: CGFloat = -0.35

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .firstTextBaseline) {
                Text(verbatim: "ANSWER")
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.inkFaint)
                Spacer()
                Text("Finding a cited answer…")
                    .font(CortexDesign.Typography.hint)
                    .foregroundColor(CortexDesign.inkFaint)
            }

            // The prose skeleton: three ruled lines of decreasing width.
            VStack(alignment: .leading, spacing: 10) {
                skeletonBar(width: 0.94, height: 11)
                skeletonBar(width: 0.86, height: 11)
                skeletonBar(width: 0.52, height: 11)
            }

            Rectangle()
                .fill(CortexDesign.hairline)
                .frame(width: 56, height: 1)

            // The citation ledger skeleton: a stamp label + three numbered footnote stubs.
            VStack(alignment: .leading, spacing: 8) {
                Text(verbatim: "CITATIONS")
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.inkFaint)
                ForEach(0..<3, id: \.self) { i in
                    HStack(spacing: 8) {
                        Text("\(i + 1).")
                            .font(CortexDesign.Typography.stamp)
                            .foregroundColor(CortexDesign.accent.opacity(0.5))
                        skeletonBar(width: [0.6, 0.72, 0.48][i], height: 9)
                        Spacer(minLength: 0)
                    }
                }
            }
        }
        .padding(CortexDesign.Space.lg)
        .background(CortexDesign.panelBackground)
        // The wax-red scan line sweeps left→right across the whole ledger, on a soft gradient band.
        .overlay(
            GeometryReader { geo in
                let bandWidth = geo.size.width * 0.28
                LinearGradient(
                    colors: [.clear, CortexDesign.accent.opacity(0.14), .clear],
                    startPoint: .leading,
                    endPoint: .trailing
                )
                .frame(width: bandWidth)
                .offset(x: sweep * geo.size.width)
                .allowsHitTesting(false)
            }
        )
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .stroke(CortexDesign.hairline, lineWidth: 1)
        )
        .onAppear {
            withAnimation(.easeInOut(duration: 1.15).repeatForever(autoreverses: false)) {
                sweep = 1.05
            }
        }
        .accessibilityElement()
        .accessibilityLabel("Finding a cited answer")
    }

    private func skeletonBar(width: CGFloat, height: CGFloat) -> some View {
        GeometryReader { geo in
            RoundedRectangle(cornerRadius: 2, style: .continuous)
                .fill(CortexDesign.ink.opacity(0.06))
                .frame(width: geo.size.width * width, height: height)
        }
        .frame(height: height)
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
                CortexButton(title: "Retry", systemImage: "arrow.clockwise", role: .secondary) {
                    state.runSearch()
                }
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
    /// The question the visible answer actually answered — snapshotted when an answer lands, so
    /// editing the field without re-asking never relabels an old answer.
    @State private var askedQuestion = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !state.askAnswer.isEmpty {
                AskAnswerPanel(
                    answer: state.askAnswer,
                    citations: state.askCitations,
                    question: askedQuestion.isEmpty ? state.searchQuery : askedQuestion
                )
            } else if state.searchResults.isEmpty {
                AskEmptyGuidance(
                    state: state,
                    title: "No cited answer found",
                    detail: "Nothing in reviewed notes matches yet. Try a more specific question.",
                    showActionsWhenMemoryExists: true
                )
            } else {
                // Retrieval found related memory but the model wouldn't commit to a single
                // cited answer. Rather than a dead "no answer" line, name what happened and
                // point straight at the matches, which are auto-opened just below.
                AskQuietState(
                    title: "Related memory, no single answer",
                    detail: "Cortex found memory related to your question but not a confident cited answer. The closest matches are open below - skim them, or ask something more specific.",
                    systemImage: "text.magnifyingglass"
                )
                .onAppear { citedMemoriesExpanded = true }
            }

            // ALWAYS expose the fuller retrieved set when there is one — even beneath a cited
            // answer. The footnote ledger only shows the citations the model chose; the raw matched
            // memories (state.searchResults) are the retrieved context, and hiding them when a
            // cited answer exists silently dropped everything the answer didn't footnote. The custom
            // serif hairline expander replaces the old native DisclosureGroup.
            if !state.searchResults.isEmpty {
                AskHairlineExpander(
                    title: "Show all \(state.searchResults.count) retrieved source\(state.searchResults.count == 1 ? "" : "s")",
                    collapseTitle: "Hide retrieved sources",
                    isExpanded: $citedMemoriesExpanded
                ) {
                    AskResultsSection(state: state)
                        .frame(maxHeight: 320)
                        .padding(.top, 10)
                }
            }
        }
        .onAppear {
            askedQuestion = state.searchQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        .onChange(of: state.askAnswer) { _ in
            askedQuestion = state.searchQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        }
    }
}

/// A custom serif hairline expander — the archive's "Show all N sources" affordance, replacing the
/// native `DisclosureGroup`. A serif label opens a hairline rule running to the trailing edge, with a
/// wax-red chevron that rotates on toggle. The disclosed content springs open beneath the rule.
struct AskHairlineExpander<Content: View>: View {
    let title: String
    let collapseTitle: String
    @Binding var isExpanded: Bool
    @ViewBuilder var content: () -> Content

    @State private var hovering = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.spring(response: 0.3, dampingFraction: 0.82)) { isExpanded.toggle() }
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(CortexDesign.accent)
                        .rotationEffect(.degrees(isExpanded ? 90 : 0))
                    Text(isExpanded ? collapseTitle : title)
                        .font(CortexDesign.Typography.prose(13))
                        .foregroundColor(hovering ? CortexDesign.ink : CortexDesign.inkSecondary)
                        .lineLimit(1)
                        .fixedSize()
                    Rectangle()
                        .fill(
                            LinearGradient(
                                colors: [CortexDesign.hairline, CortexDesign.hairline.opacity(0)],
                                startPoint: .leading,
                                endPoint: .trailing
                            )
                        )
                        .frame(height: 1)
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .onHover { hovering = $0 }

            if isExpanded {
                content()
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
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
                    if approvedMemoryCount == 0 && pendingReviewCount == 0 {
                        CortexButton(
                            title: primarySourceActionTitle,
                            systemImage: primarySourceActionIcon,
                            role: .secondary,
                            size: .large
                        ) {
                            startNotesSync()
                        }
                    } else {
                        CortexButton(
                            title: "Review memory",
                            systemImage: "checklist",
                            role: .secondary,
                            size: .large
                        ) {
                            state.selectedTab = .review
                            state.status = approvedMemoryCount == 0 ? "Review memory" : "Review more memory"
                        }
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
                    CortexButton(
                        title: suggestion,
                        systemImage: "sparkle.magnifyingglass",
                        role: .secondary,
                        fullWidth: true
                    ) {
                        state.searchQuery = suggestion
                        state.runSearch()
                    }
                }
            }
        }
    }
}

struct AskResultsSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        Group {
            if state.searchResults.isEmpty && !state.hasSearched {
                AskQuietState(title: "Ask your notes", detail: "Cortex answers from your reviewed notes, with sources.")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if state.searchResults.isEmpty {
                AskQuietState(title: "No cited result matched", detail: "Try a more specific question, or review new synced items.")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        ForEach(state.searchResults) { item in
                            AskSourceDetailRow(state: state, item: item)
                        }
                    }
                }
            }
        }
    }
}

struct AskSourceDetailRow: View {
    @ObservedObject var state: AppState
    let item: MemoryItem

    private var openableURL: URL? {
        CitationDisplay.openableURL(sourceURL: item.source_url)
    }

    private var isForgetting: Bool {
        state.inFlightMemoryIds.contains(item.id)
    }

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

            // Lightweight row actions: open the underlying source when it's reachable, and
            // let the reader forget a match that isn't helpful (it drops out of future answers).
            HStack(spacing: CortexDesign.Space.xs) {
                if let url = openableURL {
                    CortexButton(title: "Open source", systemImage: "arrow.up.right.square", role: .ghost, size: .small) {
                        NSWorkspace.shared.open(url)
                    }
                    .help("Open this source (\(url.absoluteString))")
                }
                Spacer(minLength: 0)
                CortexButton(
                    title: isForgetting ? "Forgetting…" : "Not helpful",
                    systemImage: "hand.thumbsdown",
                    role: .ghost,
                    size: .small
                ) {
                    state.deleteMemory(item)
                }
                .disabled(isForgetting)
                .help("Forget this memory so it stops appearing in answers")
            }
            .padding(.top, 2)
        }
        .padding(12)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: CortexDesign.Radius.md).stroke(CortexDesign.hairline, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md))
        .opacity(isForgetting ? 0.55 : 1)
    }

    private var sourceTitle: String {
        item.source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "Cited source" : item.source
    }

    private var citationLabel: String? {
        CitationDisplay.label(sourceURL: item.source_url)
    }
}

// MARK: - Inline [n] citation parsing

/// One run of the answer prose: either plain text or a citation marker `[n]`. The backend embeds
/// literal `[1]`, `[2]` … tokens in the answer (storage.py:11885); this splits the string into an
/// ordered run list so the prose can render each marker as a tappable wax-red mono superscript
/// while the surrounding text stays serif. Deterministic, no regex, no `.random`.
enum AskAnswerRun: Hashable {
    case text(String)
    case marker(Int)
}

enum AskAnswerParser {
    /// Split `answer` into text/marker runs. A marker is a `[` immediately followed by one or more
    /// digits and a `]` (e.g. `[12]`); anything else — including `[note]` or a lone `[` — stays as
    /// literal text so we never eat non-citation brackets. `validIndices` gates which numbers count
    /// as real citations; an out-of-range `[9]` with no citation 9 renders as plain text.
    static func runs(_ answer: String, validIndices: Set<Int>) -> [AskAnswerRun] {
        var runs: [AskAnswerRun] = []
        var pending = ""
        let chars = Array(answer)
        var i = 0
        while i < chars.count {
            if chars[i] == "[" {
                var j = i + 1
                var digits = ""
                while j < chars.count, chars[j].isNumber {
                    digits.append(chars[j])
                    j += 1
                }
                if !digits.isEmpty, j < chars.count, chars[j] == "]",
                   let n = Int(digits), validIndices.contains(n) {
                    if !pending.isEmpty { runs.append(.text(pending)); pending = "" }
                    runs.append(.marker(n))
                    i = j + 1
                    continue
                }
            }
            pending.append(chars[i])
            i += 1
        }
        if !pending.isEmpty { runs.append(.text(pending)) }
        return runs
    }
}

/// A source-type glyph derived from a citation's `kind`/`source_type` — the same friendly-kind
/// vocabulary the review cards use, mapped to an SF Symbol. Never fabricated; falls back to a neutral
/// document mark.
func askSourceGlyph(kind: String, sourceType: String?) -> String {
    switch kind.lowercased() {
    case "decision": return "signpost.right"
    case "preference": return "heart.text.square"
    case "style": return "paintbrush.pointed"
    case "negative": return "hand.thumbsdown"
    case "procedure", "procedural": return "list.number"
    case "action", "task": return "checklist"
    case "event", "episodic": return "calendar"
    case "semantic", "fact": return "text.quote"
    case "question": return "questionmark.circle"
    case "source": return "doc.text"
    default:
        // Fall back on the source medium when the kind is unknown.
        switch (sourceType ?? "").lowercased() {
        case "web", "url", "http", "https": return "link"
        case "file", "note", "markdown", "obsidian": return "doc.text"
        default: return "quote.bubble"
        }
    }
}

/// The cited answer as an annotated ARCHIVE PAGE. Left column is the prose with real tappable
/// wax-red mono superscript `[n]` markers parsed out of the answer text; the right column is a live
/// "source margin" where each citation shows its ACTUAL excerpt, a source-type glyph, and a
/// line-range / date leader. A shared `selectedCitation` binds claim ↔ receipt: hovering or tapping
/// a superscript highlights its margin card and vice-versa. An always-on provenance stamp sits above
/// the answer.
struct AskAnswerPanel: View {
    let answer: String
    let citations: [AskCitationItem]
    /// The question echoed back in italic serif above the answer — an annotated reply, not a
    /// bare result. Defaults nil so existing call sites render unchanged.
    var question: String? = nil

    /// The claim↔receipt link. Set from either side (a superscript in the prose or a margin card);
    /// the other side highlights to match.
    @State private var selectedCitation: Int? = nil
    @State private var justCopied = false

    private var citationsByIndex: [Int: AskCitationItem] {
        Dictionary(citations.map { ($0.index, $0) }, uniquingKeysWith: { first, _ in first })
    }

    private var validIndices: Set<Int> {
        Set(citations.map(\.index))
    }

    private var runs: [AskAnswerRun] {
        AskAnswerParser.runs(answer, validIndices: validIndices)
    }

    private var echoedQuestion: String? {
        guard let trimmed = question?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty else { return nil }
        return trimmed
    }

    /// One always-on provenance line above the answer: how many reviewed sources back it, in the
    /// mono catalog voice. Never fabricated — omitted when there are no citations.
    private var provenanceSegments: [String]? {
        guard !citations.isEmpty else { return nil }
        var segs = ["CITED ANSWER", "\(citations.count) SOURCE\(citations.count == 1 ? "" : "S")"]
        let openable = citations.filter {
            CitationDisplay.openableURL(path: $0.citation_path, sourceURL: $0.source_url) != nil
        }.count
        if openable > 0 { segs.append("\(openable) OPENABLE") }
        return segs
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header
            // The always-on provenance stamp — the catalog line that says this is a cited answer.
            if let provenanceSegments {
                AccessionStamp(segments: provenanceSegments, emphasisIndex: 0)
                    .help("This answer is drawn only from reviewed memory, cited below.")
            }
            if let echoedQuestion {
                Text(echoedQuestion)
                    .font(.system(size: 14, design: .serif))
                    .italic()
                    .foregroundColor(CortexDesign.inkSecondary)
                    .lineLimit(2)
                    .truncationMode(.tail)
                    .help(echoedQuestion)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            if citations.isEmpty {
                // No citations to annotate — render the answer as plain prose (still selectable).
                Text(answer)
                    .font(CortexDesign.Typography.prose(14.5))
                    .lineSpacing(3.5)
                    .foregroundColor(CortexDesign.ink)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                annotatedPage
            }
        }
        .padding(CortexDesign.Space.lg)
        .background(CortexDesign.panelBackground)
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md)
                .stroke(CortexDesign.hairline, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md))
        // A brief, unmissable confirmation pill after Copy — the small button-label flip alone was
        // easy to miss, so a wax-red toast slides in at the top of the answer and fades out.
        .overlay(alignment: .top) {
            if justCopied {
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 11, weight: .semibold))
                    Text(citations.isEmpty ? "Answer copied" : "Answer and sources copied")
                        .font(CortexDesign.Typography.caption)
                        .fontWeight(.semibold)
                }
                .foregroundColor(.white)
                .padding(.horizontal, 12)
                .padding(.vertical, 7)
                .background(Capsule().fill(CortexDesign.accent))
                .shadow(color: CortexDesign.accent.opacity(0.25), radius: 8, y: 2)
                .padding(.top, 10)
                .transition(.move(edge: .top).combined(with: .opacity))
                .accessibilityElement()
                .accessibilityLabel(citations.isEmpty ? "Answer copied" : "Answer and sources copied")
            }
        }
        .animation(.spring(response: 0.32, dampingFraction: 0.8), value: justCopied)
        .onChange(of: answer) { _ in
            justCopied = false
            selectedCitation = nil
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(verbatim: "ANSWER")
                .font(CortexDesign.Typography.stamp)
                .kerning(0.8)
                .foregroundColor(CortexDesign.inkFaint)
            Spacer()
            // Copy stays reachable at all times (no hover gate) and answers ⌘⇧C — so keyboard and
            // VoiceOver users can reach it too. A ghost button keeps it quiet until used.
            CortexButton(
                title: justCopied ? "Copied" : "Copy",
                systemImage: justCopied ? "checkmark" : "doc.on.doc",
                role: .ghost,
                size: .small
            ) {
                copyAnswerWithSources()
            }
            .disabled(justCopied)
            .keyboardShortcut("c", modifiers: [.command, .shift])
            .help("Copy the answer with its sources (⌘⇧C)")
        }
    }

    /// The two-column archive page: prose left, source margin right. On a narrow width the margin
    /// stacks beneath the prose so it never squeezes the reading column.
    private var annotatedPage: some View {
        HStack(alignment: .top, spacing: CortexDesign.Space.lg) {
            AskAnnotatedProse(
                runs: runs,
                selectedCitation: $selectedCitation
            )
            .frame(maxWidth: .infinity, alignment: .leading)
            .layoutPriority(1)

            AskSourceMargin(
                citations: citations,
                citationsByIndex: citationsByIndex,
                selectedCitation: $selectedCitation
            )
            .frame(width: 260)
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

/// The left column: the answer prose with real inline `[n]` markers rendered as tappable wax-red
/// mono superscripts. Text runs use SwiftUI concatenation so the markers flow inline with the serif
/// prose (a true footnote superscript), and a transparent overlay of tap targets sits over the
/// markers so a click/hover updates `selectedCitation` — SwiftUI `Text` can't carry per-run gestures,
/// so the tap layer is separate but positionally faithful via a wrapping flow of the same runs.
struct AskAnnotatedProse: View {
    let runs: [AskAnswerRun]
    @Binding var selectedCitation: Int?

    var body: some View {
        // The runs are laid out as a wrapping paragraph: plain runs are serif prose, marker runs are
        // small interactive superscript chips. `AskFlowLayout` wraps them like text so the markers sit
        // inline where the [n] token appeared.
        AskFlowLayout(spacing: 0, lineSpacing: 6) {
            ForEach(Array(runs.enumerated()), id: \.offset) { _, run in
                switch run {
                case .text(let string):
                    // Break plain text into word chunks so the flow layout can wrap on spaces.
                    ForEach(Array(wordChunks(string).enumerated()), id: \.offset) { _, chunk in
                        Text(chunk)
                            .font(CortexDesign.Typography.prose(14.5))
                            .foregroundColor(CortexDesign.ink)
                            .textSelection(.enabled)
                    }
                case .marker(let n):
                    AskCitationMarker(
                        index: n,
                        isSelected: selectedCitation == n
                    ) {
                        selectedCitation = (selectedCitation == n) ? nil : n
                    } onHover: { inside in
                        if inside { selectedCitation = n }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Split a text run into wrappable chunks, keeping trailing spaces attached so word spacing is
    /// preserved in the flow layout.
    private func wordChunks(_ s: String) -> [String] {
        guard !s.isEmpty else { return [] }
        var chunks: [String] = []
        var current = ""
        for ch in s {
            current.append(ch)
            if ch == " " || ch == "\n" {
                chunks.append(current)
                current = ""
            }
        }
        if !current.isEmpty { chunks.append(current) }
        return chunks
    }
}

/// One inline citation superscript: a small wax-red mono numeral raised like a footnote marker. Taps
/// and hovers drive the shared `selectedCitation`; when selected it fills with the wax wash so the
/// reader sees which claim they've pinned.
struct AskCitationMarker: View {
    let index: Int
    let isSelected: Bool
    let onTap: () -> Void
    let onHover: (Bool) -> Void

    var body: some View {
        Button(action: onTap) {
            Text("\(index)")
                .font(.system(size: 8.5, weight: .semibold, design: .monospaced))
                .foregroundColor(CortexDesign.accent)
                .padding(.horizontal, 3)
                .padding(.vertical, 0.5)
                .background(
                    RoundedRectangle(cornerRadius: 2, style: .continuous)
                        .fill(isSelected ? CortexDesign.accentSoft : Color.clear)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 2, style: .continuous)
                        .stroke(CortexDesign.accent.opacity(isSelected ? 0.5 : 0.2), lineWidth: 0.75)
                )
                .baselineOffset(5)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover(perform: onHover)
        .help("Citation \(index)")
        .accessibilityLabel("Citation \(index)")
        .accessibilityAddTraits(.isButton)
    }
}

/// The right column: a live source margin. One card per citation showing the ACTUAL excerpt, a
/// source-type glyph, its source label, and a line-range / date leader. The card matching
/// `selectedCitation` lifts to wax emphasis so hovering/tapping a prose marker reveals its receipt.
struct AskSourceMargin: View {
    let citations: [AskCitationItem]
    let citationsByIndex: [Int: AskCitationItem]
    @Binding var selectedCitation: Int?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(verbatim: "SOURCE MARGIN")
                .font(CortexDesign.Typography.stamp)
                .kerning(0.8)
                .foregroundColor(CortexDesign.inkFaint)
            ForEach(citations) { citation in
                AskMarginCitationCard(
                    citation: citation,
                    isSelected: selectedCitation == citation.index
                ) {
                    selectedCitation = (selectedCitation == citation.index) ? nil : citation.index
                } onHover: { inside in
                    if inside { selectedCitation = citation.index }
                    else if selectedCitation == citation.index { selectedCitation = nil }
                }
            }
        }
    }
}

/// A single margin card — the "receipt" for one claim. Real excerpt, source-type glyph, source
/// label, and a dotted leader out to the line range / date. Tapping opens the source when openable.
struct AskMarginCitationCard: View {
    let citation: AskCitationItem
    let isSelected: Bool
    let onTap: () -> Void
    let onHover: (Bool) -> Void

    private var openableURL: URL? {
        // A file URL whose target has been deleted/moved is not really openable — treat it as
        // unavailable so the card shows a note instead of a link that opens to nothing.
        guard let url = CitationDisplay.openableURL(path: citation.citation_path, sourceURL: citation.source_url) else {
            return nil
        }
        if url.isFileURL, !FileManager.default.fileExists(atPath: url.path) {
            return nil
        }
        return url
    }

    /// True when this citation clearly pointed at an on-disk file (an absolute path or a file
    /// source URL) but that file can no longer be opened — deleted, moved, or a volume unmounted.
    /// A web/http citation or an internal capture never triggers this note.
    private var sourceUnavailable: Bool {
        guard openableURL == nil else { return false }
        if let path = citation.citation_path?.trimmingCharacters(in: .whitespacesAndNewlines),
           path.hasPrefix("/") {
            return true
        }
        let lower = (citation.source_url ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return lower.hasPrefix("file://") || lower.hasPrefix("local-file://")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text("\(citation.index)")
                    .font(.system(size: 10, weight: .semibold, design: .monospaced))
                    .foregroundColor(CortexDesign.accent)
                    .frame(minWidth: 12, alignment: .leading)
                Image(systemName: askSourceGlyph(kind: citation.kind, sourceType: citation.source_type))
                    .font(.system(size: 10))
                    .foregroundColor(CortexDesign.inkSecondary)
                Text(sourceLabel)
                    .font(.system(size: 11.5, weight: .medium))
                    .foregroundColor(CortexDesign.ink)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: 2)
                if openableURL != nil {
                    Image(systemName: "arrow.up.right.square")
                        .font(.system(size: 9))
                        .foregroundColor(CortexDesign.accent)
                }
            }

            if !excerpt.isEmpty {
                // The actual excerpt — the receipt text the tooltip used to hide.
                Text(excerpt)
                    .font(CortexDesign.Typography.prose(11.5))
                    .foregroundColor(CortexDesign.inkSecondary)
                    .lineSpacing(2)
                    .lineLimit(4)
                    .truncationMode(.tail)
                    .fixedSize(horizontal: false, vertical: true)
                    .help(excerpt)
            }

            if let leader = leaderLabel {
                // Dotted leader → line range / date, like a footnote's rule out to its locator.
                HStack(spacing: 5) {
                    Line()
                        .stroke(style: StrokeStyle(lineWidth: 1, dash: [1.5, 2.5]))
                        .foregroundColor(CortexDesign.hairline)
                        .frame(height: 1)
                    Text(leader.uppercased())
                        .font(CortexDesign.Typography.hint)
                        .kerning(0.5)
                        .foregroundColor(CortexDesign.inkFaint)
                        .lineLimit(1)
                        .layoutPriority(1)
                }
            }

            if sourceUnavailable {
                // The citation still stands (its excerpt is real), but the file behind it can no
                // longer be opened. Say so plainly rather than leaving a dead, unclickable card.
                HStack(spacing: 5) {
                    Image(systemName: "exclamationmark.triangle")
                        .font(.system(size: 9))
                    Text("Source no longer available")
                        .font(CortexDesign.Typography.hint)
                        .lineLimit(1)
                }
                .foregroundColor(CortexDesign.inkFaint)
                .help("The original file for this citation was moved or deleted. The quoted excerpt above is still what Cortex used.")
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                .fill(isSelected ? CortexDesign.accentSoft : CortexDesign.quietBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                .stroke(isSelected ? CortexDesign.accent.opacity(0.45) : CortexDesign.hairline, lineWidth: 1)
        )
        // A wax-red edge rail on the selected card — the receipt "lights up" for the pinned claim.
        .overlay(alignment: .leading) {
            if isSelected {
                RoundedRectangle(cornerRadius: 1)
                    .fill(CortexDesign.accent)
                    .frame(width: 2)
                    .padding(.vertical, 6)
            }
        }
        .contentShape(Rectangle())
        .onTapGesture {
            if let url = openableURL {
                NSWorkspace.shared.open(url)
            } else {
                onTap()
            }
        }
        .onHover(perform: onHover)
        .animation(CortexMotion.hover, value: isSelected)
        .help(helpText)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Citation \(citation.index): \(sourceLabel). \(excerpt.isEmpty ? "" : excerpt)")
    }

    private var sourceLabel: String {
        // Line numbers are owned by the leader, so the range never prints twice.
        CitationDisplay.label(
            path: citation.citation_path,
            sourceURL: citation.source_url,
            fallback: citation.source
        ) ?? citation.source
    }

    private var helpText: String {
        let base: String
        if openableURL != nil {
            base = "Click to open source"
        } else if sourceUnavailable {
            base = "\(sourceLabel) (source no longer available)"
        } else {
            base = sourceLabel
        }
        return excerpt.isEmpty ? base : "\(excerpt)\n\n\(base)"
    }

    private var leaderLabel: String? {
        if let lineLabel { return lineLabel }
        let date = (citation.occurred_at ?? citation.captured_at)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard let date, !date.isEmpty else {
            // Fall back on the section/scope so the leader isn't empty when there's no locator.
            return sectionDetail
        }
        return shortDate(date)
    }

    private var sectionDetail: String? {
        let pieces = [
            citation.section_title?.trimmingCharacters(in: .whitespacesAndNewlines),
            citation.record_scope?.trimmingCharacters(in: .whitespacesAndNewlines)
        ]
        let detail = pieces.compactMap { value -> String? in
            guard let value, !value.isEmpty else { return nil }
            return value
        }.joined(separator: " · ")
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
        // A bare file path adds nothing under a card that already names the source — suppress it.
        if MemoryText.isPathLike(raw) { return "" }
        return raw
    }
}

/// A one-segment horizontal line, used for the dotted leader.
private struct Line: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.midY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        return path
    }
}

/// A minimal flow (wrapping) layout so inline `[n]` markers sit within the serif prose paragraph and
/// wrap like text. Uses SwiftUI's `Layout` (macOS 13+) — places each subview left-to-right, wrapping
/// to the next line when it would overflow the proposed width. Deterministic; no `.random`.
struct AskFlowLayout: Layout {
    var spacing: CGFloat = 0
    var lineSpacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout Void) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var lineHeight: CGFloat = 0
        var maxLineWidth: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > 0, x + size.width > maxWidth {
                maxLineWidth = max(maxLineWidth, x - spacing)
                x = 0
                y += lineHeight + lineSpacing
                lineHeight = 0
            }
            x += size.width + spacing
            lineHeight = max(lineHeight, size.height)
        }
        maxLineWidth = max(maxLineWidth, x - spacing)
        let totalWidth = proposal.width ?? max(maxLineWidth, 0)
        return CGSize(width: totalWidth, height: y + lineHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout Void) {
        let maxWidth = bounds.width
        var x: CGFloat = 0
        var y: CGFloat = 0
        var lineHeight: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > 0, x + size.width > maxWidth {
                x = 0
                y += lineHeight + lineSpacing
                lineHeight = 0
            }
            subview.place(
                at: CGPoint(x: bounds.minX + x, y: bounds.minY + y),
                anchor: .topLeading,
                proposal: ProposedViewSize(size)
            )
            x += size.width + spacing
            lineHeight = max(lineHeight, size.height)
        }
    }
}
