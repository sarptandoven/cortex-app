import Foundation
import SwiftUI

struct ReviewTab: View {
    @ObservedObject var state: AppState
    @State private var initialLoadDone = false
    @State private var isReloading = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: CortexDesign.Space.xl) {
                ReviewHeaderSection(state: state)
                if !initialLoadDone && state.inbox.isEmpty {
                    ReviewLoadingCard()
                } else {
                    if shouldShowSourceHealth {
                        ReviewSourceHealthStrip(state: state)
                    }
                    ReviewProactiveAlertsSection(state: state)
                    ReviewTwinGradingSection(state: state)
                    ReviewInboxSection(state: state, captures: state.inbox)
                }
            }
            .frame(maxWidth: 680, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, CortexDesign.Space.xl)
            .padding(.vertical, CortexDesign.Space.xl)
        }
        .task {
            await reload()
            initialLoadDone = true
        }
        .onChange(of: state.selectedTab) { tab in
            guard tab == .review, initialLoadDone else { return }
            Task { await reload() }
        }
        .background(CortexDesign.appBackground)
    }

    private func reload() async {
        guard !isReloading else { return }
        isReloading = true
        defer { isReloading = false }
        await state.loadSourceConnectivity()
        await state.loadInbox()
        await state.loadReview()
        await state.loadProductLoop()
        await state.loadProactiveAlerts()
        await state.loadTwinScorecard()
    }

    private var shouldShowSourceHealth: Bool {
        // The health strip is a diagnostic, not a headline — only surface it when a source actually
        // needs the user. In the healthy case Review goes straight to the pending items, which is
        // what the tab is for.
        (state.sourceReadinessReport?.summary.needs_attention ?? 0) > 0
    }
}

struct ReviewHeaderSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Review")
                        .font(CortexDesign.Typography.display(22))
                        .foregroundColor(CortexDesign.ink)
                    Text("Approve what Cortex should remember. Archive anything noisy or unclear.")
                        .font(CortexDesign.Typography.body)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
            }
        }
    }
}

struct ReviewSourceHealthStrip: View {
    @ObservedObject var state: AppState

    private var sources: [SourceReadinessItem] {
        state.sourceReadinessReport?.sources
            .filter { source in
                source.pending > 0 || source.active_memories > 0 || source.accounts > 0
            }
            .sorted { lhs, rhs in
                if lhs.pending != rhs.pending {
                    return lhs.pending > rhs.pending
                }
                if lhs.active_memories != rhs.active_memories {
                    return lhs.active_memories > rhs.active_memories
                }
                return lhs.name < rhs.name
            } ?? []
    }

    private var pendingCount: Int {
        state.inbox.count
    }

    private var needsAttentionCount: Int {
        state.sourceReadinessReport?.summary.needs_attention ?? 0
    }

    private var dueCount: Int {
        sources.filter { $0.sync_plan?.due_now == true }.count
    }

    // Contract-kept: no longer rendered since the metric tiles were removed, but a contract test
    // asserts this identifier exists in this file.
    private var latestSync: String? {
        sources.compactMap { $0.sync_plan?.last_completed_at ?? $0.last_seen_at }.sorted().last
    }

    private var title: String {
        if needsAttentionCount > 0 { return "Check source health before approving" }
        if pendingCount > 0 { return "Review synced memory with source context" }
        if !sources.isEmpty { return "Sources are ready for new memory" }
        return "No connected source context yet"
    }

    private var detail: String {
        if needsAttentionCount > 0 {
            // Name the source and its fix; "1 source need attention" was both broken
            // grammar and useless (which source? what do I do?).
            let failing = sources.filter(\.needsAttention)
            if let first = failing.first {
                let action = first.next_action.trimmingCharacters(in: .whitespacesAndNewlines)
                let lead = failing.count == 1
                    ? "\(first.name) needs attention."
                    : "\(first.name) and \(failing.count - 1) more need attention."
                return action.isEmpty ? "\(lead) Already synced memory stays available." : "\(lead) \(action)"
            }
            return "A source needs attention. Already synced memory stays available."
        }
        if pendingCount > 0 {
            return "Approve useful items, archive noise, and Cortex will use approved memory in Ask and connected AI tools."
        }
        if !sources.isEmpty {
            return "Cortex will place new synced memories here before they are used."
        }
        return "Connect notes or a source to start building reviewed memory."
    }

    private var statusColor: Color {
        if needsAttentionCount > 0 { return CortexDesign.accent }
        if pendingCount > 0 { return CortexDesign.gold }
        if !sources.isEmpty { return CortexDesign.sealMoss }
        return CortexDesign.inkSecondary
    }

    private var statusIcon: String {
        if needsAttentionCount > 0 { return "exclamationmark.circle.fill" }
        if pendingCount > 0 { return "tray.full.fill" }
        if !sources.isEmpty { return "checkmark.seal.fill" }
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
                    .accessibilityHidden(true)

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
            .accessibilityElement(children: .combine)

            if !sources.isEmpty {
                HStack(spacing: 6) {
                    ForEach(Array(sources.prefix(3))) { source in
                        ReviewSourceHealthChip(source: source)
                    }
                    if sources.count > 3 {
                        Text("+\(sources.count - 3) more")
                            .font(CortexDesign.Typography.caption)
                            .foregroundColor(CortexDesign.inkSecondary)
                            .padding(.horizontal, 9)
                            .padding(.vertical, 5)
                            .background(CortexDesign.quietBackground)
                            .clipShape(Capsule())
                    }
                    Spacer(minLength: 0)
                }
            }
        }
        .cortexCard(padding: CortexDesign.Space.md, background: CortexDesign.panelBackground)
    }

    // Contract-kept: no longer rendered since the metric tiles were removed, but a contract test
    // asserts this identifier exists in this file.
    private var sourceHealthLabel: String {
        if needsAttentionCount > 0 {
            return "\(needsAttentionCount) needs attention"
        }
        if dueCount > 0 {
            return "\(dueCount) sync due"
        }
        if !sources.isEmpty {
            return "Healthy"
        }
        return "Not connected"
    }
}

struct ReviewSourceHealthChip: View {
    let source: SourceReadinessItem

    var body: some View {
        Text(label)
            .font(CortexDesign.Typography.caption)
            .foregroundColor(CortexDesign.inkSecondary)
            .lineLimit(1)
            .truncationMode(.tail)
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .background(CortexDesign.quietBackground)
            .clipShape(Capsule())
            .help(helpText)
            .accessibilityElement(children: .combine)
            .accessibilityLabel(helpText)
    }

    private var label: String {
        if source.pending > 0 {
            return "\(source.name) · \(source.pending) pending"
        }
        if let lastSeen = source.sync_plan?.last_completed_at ?? source.last_seen_at {
            return "\(source.name) · synced \(reviewShortDate(lastSeen))"
        }
        return source.name
    }

    private var helpText: String {
        if let warning = source.warnings.first, !warning.isEmpty {
            return "\(source.name): \(warning)"
        }
        return "\(source.name): \(source.syncPlanDisplayTitle), \(source.active_memories) reviewed memories."
    }
}

/// The section board: a large backlog rendered as at most 15 one-decision groups.
/// Each card is a project/folder ("Magic Agent Demo Codex Launch · 1,238 notes") with
/// sample titles for a sniff test, and Approve/Archive act on the WHOLE section server-side.
struct ReviewSectionsBoard: View {
    @ObservedObject var state: AppState
    @State private var confirmArchiveSection: ReviewSection?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Review by section")
                    .font(CortexDesign.Typography.title)
                    .foregroundColor(CortexDesign.ink)
                Text("\(state.reviewSectionsPendingTotal) items grouped into \(state.reviewSections.count) sections — approve or archive each in one decision.")
                    .font(CortexDesign.Typography.body)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            LazyVStack(alignment: .leading, spacing: 10) {
                ForEach(state.reviewSections) { section in
                    ReviewSectionCard(
                        section: section,
                        isInFlight: state.inFlightSectionIds.contains(section.section_id),
                        approve: { state.approveReviewSection(section) },
                        archive: { confirmArchiveSection = section }
                    )
                }
            }
        }
        .confirmationDialog(
            "Archive \(confirmArchiveSection?.capture_count ?? 0) items in “\(confirmArchiveSection?.label ?? "")”?",
            isPresented: Binding(
                get: { confirmArchiveSection != nil },
                set: { if !$0 { confirmArchiveSection = nil } }
            ),
            titleVisibility: .visible
        ) {
            Button("Archive section", role: .destructive) {
                if let section = confirmArchiveSection {
                    state.archiveReviewSection(section)
                }
                confirmArchiveSection = nil
            }
            Button("Cancel", role: .cancel) { confirmArchiveSection = nil }
        } message: {
            Text("Cortex won't remember archived items. Your original notes stay in your source.")
        }
    }
}

struct ReviewSectionCard: View {
    let section: ReviewSection
    let isInFlight: Bool
    let approve: () -> Void
    let archive: () -> Void
    @State private var isHovered = false

    private var countLine: String {
        var parts = ["\(section.capture_count) note\(section.capture_count == 1 ? "" : "s")"]
        if section.memory_count > 0 {
            parts.append("\(section.memory_count) memories")
        }
        if section.task_count > 0 {
            parts.append("\(section.task_count) tasks")
        }
        return parts.joined(separator: " · ")
    }

    var body: some View {
        HStack(alignment: .center, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(section.label)
                        .font(CortexDesign.Typography.title)
                        .foregroundColor(CortexDesign.ink)
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Text(countLine)
                        .font(CortexDesign.Typography.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .lineLimit(1)
                }
                if !section.sample_titles.isEmpty {
                    Text(section.sample_titles.joined(separator: "  ·  "))
                        .font(CortexDesign.Typography.caption)
                        .foregroundColor(CortexDesign.inkFaint)
                        .lineLimit(1)
                        .truncationMode(.tail)
                        .help(section.sample_titles.joined(separator: "\n"))
                }
            }
            Spacer(minLength: 12)
            if isInFlight {
                ProgressView().controlSize(.small)
            }
            Button {
                archive()
            } label: {
                Label("Archive", systemImage: "archivebox")
                    .frame(minHeight: 34)
            }
            .buttonStyle(.bordered)
            .disabled(isInFlight)
            .help("Archives the \(section.capture_count) items in this section")
            Button {
                approve()
            } label: {
                Label("Approve", systemImage: "checkmark.seal")
                    .frame(minHeight: 34)
            }
            .buttonStyle(.borderedProminent)
            .disabled(isInFlight)
            .help("Approves the \(section.capture_count) items in this section")
        }
        .padding(.horizontal, CortexDesign.Space.md)
        .padding(.vertical, CortexDesign.Space.sm)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous).stroke(CortexDesign.hairline))
        .shadow(color: CortexDesign.ink.opacity(isHovered ? 0.06 : 0), radius: 8, y: 2)
        .onHover { isHovered = $0 }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(section.label): \(countLine)")
    }
}

struct ReviewInboxSection: View {
    @ObservedObject var state: AppState
    let captures: [CaptureItem]

    private static let pageSize = 10
    @State private var visibleLimit = ReviewInboxSection.pageSize

    // Sections earn their space only when they actually compress work: a backlog
    // bigger than one page, grouped into more than one section.
    private var showSections: Bool {
        state.reviewSectionsPendingTotal > Self.pageSize && state.reviewSections.count > 1
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            if showSections {
                ReviewSectionsBoard(state: state)
            }
            HStack(alignment: .center, spacing: 14) {
                if showSections {
                    Text("Or review one at a time".uppercased())
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.8)
                        .foregroundColor(CortexDesign.inkFaint)
                }
                Spacer()
                if visibleCount > 3 {
                    Button {
                        state.approveCaptures(visibleCaptures)
                    } label: {
                        Label("Approve \(visibleCount) shown", systemImage: "checkmark.seal")
                            .frame(minHeight: 40)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)
                    .disabled(!state.inFlightCaptureIds.isEmpty)
                    .help("Approve every item shown below")
                }
            }

            if captures.isEmpty {
                if !state.isLocalServiceReady {
                    ReviewServiceStartingState(state: state)
                } else {
                    ReviewEmptyState(state: state, detail: emptyDetail)
                }
            } else {
                LazyVStack(alignment: .leading, spacing: 20) {
                    ForEach(visibleCaptures) { capture in
                        ReviewQueueCaptureCard(
                            capture: capture,
                            isInFlight: state.inFlightCaptureIds.contains(capture.id),
                            actionError: state.captureActionErrors[capture.id],
                            approve: { state.approveCapture(capture) },
                            archive: { state.archiveCapture(capture) },
                            isTopItem: capture.id == visibleCaptures.first?.id
                        )
                        .transition(.asymmetric(
                            insertion: .opacity,
                            removal: .move(edge: .trailing).combined(with: .opacity)
                        ))
                    }

                    if captures.count > visibleCount {
                        Button {
                            visibleLimit += Self.pageSize
                        } label: {
                            Label("Show more (\(captures.count - visibleCount) remaining)", systemImage: "chevron.down")
                                .frame(maxWidth: .infinity, minHeight: 46)
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.large)
                    }
                }
                .animation(.spring(response: 0.35, dampingFraction: 0.8), value: captures.map(\.id))
            }
        }
        .onChange(of: captures.count) { _ in
            // Reset pagination when the list changes (after approve/archive/sync) so the
            // "Show more" state tracks the current list.
            visibleLimit = Self.pageSize
        }
    }

    private var visibleCaptures: [CaptureItem] {
        Array(captures.prefix(visibleLimit))
    }

    private var visibleCount: Int {
        visibleCaptures.count
    }

    private var emptyDetail: String {
        if (state.review?.stats.memories ?? 0) == 0 {
            if state.hasConnectedSourceAccount || state.hasConnectedObsidianVault {
                return "Sync your source. New memories will appear here before Cortex uses them."
            }
            return "Connect notes first. New memories will appear here before Cortex uses them."
        }
        return "All caught up. New synced items will appear here before Cortex uses them."
    }
}

struct ReviewEmptyState: View {
    @ObservedObject var state: AppState
    let detail: String

    private var approvedMemoryCount: Int {
        state.review?.stats.memories ?? state.stats?.memories ?? 0
    }

    private var obsidianConnector: SourceConnectorCatalogItem? {
        state.sourceConnectorCatalog.first { $0.id == "obsidian" }
    }

    private var emptyTitle: String {
        if approvedMemoryCount == 0, !(state.hasConnectedSourceAccount || state.hasConnectedObsidianVault) {
            return "Connect notes to start review"
        }
        return "Nothing to review"
    }

    var body: some View {
        VStack(spacing: 12) {
            if approvedMemoryCount > 0 {
                ReviewAllClearState()
            } else {
                QuietState(title: emptyTitle, detail: detail)
            }

            HStack(spacing: 10) {
                if approvedMemoryCount > 0 {
                    Button {
                        state.selectedTab = .ask
                        state.status = "Ask Cortex"
                    } label: {
                        Label("Ask a question", systemImage: "magnifyingglass")
                            .frame(minWidth: 150, minHeight: 46)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                } else if state.hasConnectedObsidianVault, let connector = obsidianConnector {
                    Button {
                        state.connectLocalNotesFolder(connector, chooseNew: state.notesNeedContent)
                    } label: {
                        Label(state.notesNeedContent ? "Choose notes" : "Sync notes", systemImage: state.notesNeedContent ? "folder.badge.questionmark" : "arrow.triangle.2.circlepath")
                            .frame(minWidth: 148, minHeight: 46)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(state.isBusy)
                } else {
                    Button {
                        if let connector = obsidianConnector {
                            state.connectLocalNotesFolder(connector, chooseNew: state.notesNeedContent)
                        } else {
                            state.openConnectionsPrivacy(statusMessage: "Connect notes")
                        }
                    } label: {
                        Label("Connect notes", systemImage: "folder.badge.plus")
                            .frame(minWidth: 172, minHeight: 46)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                }
            }
            .frame(maxWidth: .infinity, alignment: .center)
        }
    }
}

struct ReviewAllClearState: View {
    @State private var appeared = false

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: "checkmark.seal.fill")
                .font(.system(size: 34))
                .foregroundColor(CortexDesign.sealMoss)
                .scaleEffect(appeared ? 1 : 0.5)
                .opacity(appeared ? 1 : 0)
            Text("All caught up")
                .font(CortexDesign.Typography.display(20))
                .foregroundColor(CortexDesign.ink)
            Text("Everything you approved is ready to use in Ask.")
                .font(CortexDesign.Typography.body)
                .foregroundColor(CortexDesign.inkSecondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 460)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 26)
        .padding(.horizontal, 18)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .onAppear {
            withAnimation(.spring(response: 0.45, dampingFraction: 0.6)) { appeared = true }
        }
    }
}

struct ReviewLoadingCard: View {
    var body: some View {
        HStack(spacing: 12) {
            ProgressView()
                .controlSize(.small)
            Text("Loading review queue…")
                .font(CortexDesign.Typography.body)
                .foregroundColor(CortexDesign.inkSecondary)
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(18)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct ReviewServiceStartingState: View {
    @ObservedObject var state: AppState

    private var needsAttention: Bool {
        CortexRecoveryText.needsAttention(state.displayStatus)
    }

    private var title: String {
        needsAttention ? "Cortex needs attention" : "Cortex is starting"
    }

    private var detail: String {
        if needsAttention {
            return state.displayStatus
        }
        return "Reconnecting to your local memory engine. This usually takes a moment."
    }

    var body: some View {
        VStack(spacing: 12) {
            HStack(spacing: 10) {
                if needsAttention {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.headline)
                        .foregroundColor(CortexDesign.accent)
                } else {
                    ProgressView()
                        .controlSize(.small)
                }
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

            Button {
                Task {
                    await state.ensureBackend()
                    await state.loadDiagnostics()
                    await state.loadInbox()
                    await state.loadReview()
                    await state.loadStats()
                }
            } label: {
                Label(needsAttention ? "Try again" : "Reconnect", systemImage: "arrow.clockwise")
                    .frame(minWidth: 140, minHeight: 44)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .disabled(state.backendRetryInProgress)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(18)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct ReviewQueueCaptureCard: View {
    let capture: CaptureItem
    var isInFlight: Bool = false
    var actionError: String? = nil
    let approve: () -> Void
    let archive: () -> Void
    var isTopItem: Bool = false
    @State private var confirmArchive = false
    @State private var isHovered = false
    // Flips on the first confirmed archive so triage is one click after one informed consent.
    @AppStorage("cortex.review.archiveConfirmedOnce") private var archiveConfirmedOnce = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top, spacing: 12) {
                    Text(title)
                        .font(CortexDesign.Typography.title)
                        .foregroundColor(CortexDesign.ink)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 12)
                    Text(reviewSizeLabel.uppercased())
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.8)
                        .foregroundColor(CortexDesign.inkFaint)
                        .lineLimit(1)
                }

                if let summary = cleanedSummary {
                    Text(summary)
                        .font(CortexDesign.Typography.prose(14))
                        .foregroundColor(CortexDesign.ink)
                        .lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            ReviewQueuePreviewList(capture: capture)

            ReviewQueueSourceBox(capture: capture)

            if let actionError, !actionError.isEmpty {
                Label(actionError, systemImage: "exclamationmark.triangle.fill")
                    .font(CortexDesign.Typography.body)
                    .foregroundColor(CortexDesign.accent)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack(alignment: .center, spacing: 12) {
                if isInFlight {
                    ProgressView()
                        .controlSize(.small)
                }
                Spacer()
                Button {
                    if archiveConfirmedOnce {
                        archive()
                    } else {
                        confirmArchive = true
                    }
                } label: {
                    Label("Archive", systemImage: "archivebox")
                        .frame(minWidth: 132, minHeight: 48)
                }
                .controlSize(.large)
                .buttonStyle(.bordered)
                .disabled(isInFlight)
                // Optional-shortcut overload (macOS 12.3+): only the top card answers ⌘⌫.
                .keyboardShortcut(isTopItem ? KeyboardShortcut(.delete, modifiers: .command) : nil)
                .help("Archive (⌘⌫ archives the top item)")
                .confirmationDialog(
                    "Archive this review item?",
                    isPresented: $confirmArchive,
                    titleVisibility: .visible
                ) {
                    Button("Archive", role: .destructive) {
                        archiveConfirmedOnce = true
                        archive()
                    }
                    Button("Cancel", role: .cancel) {}
                } message: {
                    Text("Cortex won't remember archived items. Your original note stays in your source. We'll only ask this once.")
                }
                Button {
                    approve()
                } label: {
                    Label("Approve", systemImage: "checkmark.seal")
                        .frame(minWidth: 150, minHeight: 48)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(isInFlight)
                .keyboardShortcut(isTopItem ? KeyboardShortcut(.return, modifiers: .command) : nil)
                .help("Approve (⌘↩ approves the top item)")
            }
        }
        // The unreviewed index card: content clears the gold margin rule by 10pt.
        .padding(.leading, 10)
        .cortexCard(padding: CortexDesign.Space.lg, background: CortexDesign.panelBackground)
        .archiveSpine(CortexDesign.gold)
        .shadow(color: CortexDesign.ink.opacity(isHovered ? 0.07 : 0), radius: 10, y: 3)
        .onHover { hovering in
            withAnimation(.easeOut(duration: 0.15)) { isHovered = hovering }
        }
    }

    private var title: String {
        cortexCaptureTitle(capture)
    }

    private var cleanedSummary: String? {
        guard let summary = capture.summary?.trimmingCharacters(in: .whitespacesAndNewlines),
              !summary.isEmpty else {
            return nil
        }
        return MemoryText.displayProse(summary, maxLength: 360)
    }

    private var reviewSizeLabel: String {
        let total = (capture.memory_count ?? 0) + (capture.task_count ?? 0)
        if total <= 0 {
            return "Ready to review"
        }
        return total == 1 ? "1 item" : "\(total) items"
    }
}

struct ReviewQueuePreviewList: View {
    let capture: CaptureItem

    private var memories: [MemoryItem] {
        cortexDedupedMemories(capture.preview_memories ?? [], limit: 3)
    }

    private var tasks: [TaskItem] {
        Array((capture.preview_tasks ?? []).prefix(2))
    }

    var body: some View {
        if memories.isEmpty && tasks.isEmpty {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: "hourglass")
                    .foregroundColor(CortexDesign.inkSecondary)
                Text("Cortex is preparing this item.")
            }
            .font(CortexDesign.Typography.body)
            .foregroundColor(CortexDesign.inkSecondary)
        } else {
            VStack(alignment: .leading, spacing: 10) {
                Text("Will remember".uppercased())
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.inkFaint)

                ForEach(memories) { memory in
                    ReviewQueuePlainPreviewRow(text: memory.content)
                }

                ForEach(tasks) { task in
                    ReviewQueuePlainPreviewRow(text: task.content)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct ReviewQueuePlainPreviewRow: View {
    let text: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 9) {
            Image(systemName: "circle.fill")
                .font(.system(size: 6))
                .foregroundColor(CortexDesign.accent)
            Text(MemoryText.displayProse(text, maxLength: 280))
                .font(CortexDesign.Typography.prose(13.5))
                .foregroundColor(CortexDesign.ink)
                .lineSpacing(3)
                .lineLimit(5)
                .help(MemoryText.normalizedProse(text))
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
    }
}

struct ReviewQueueSourceBox: View {
    let capture: CaptureItem

    var body: some View {
        // The card-catalog accession line: known provenance only, never a fabricated segment.
        if !segments.isEmpty {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                AccessionStamp(segments: segments)
                    .truncationMode(.middle)
                    .help(capture.source_url ?? capture.source)
                Spacer(minLength: 0)
            }
        }
    }

    private var segments: [String] {
        var parts: [String] = []
        // Skip the source segment when it just repeats the card title derived from the same string.
        if let sourceName, sourceName != cortexCaptureTitle(capture) {
            parts.append(sourceName)
        }
        if let capturedDate {
            parts.append(capturedDate)
        }
        return parts
    }

    private var sourceName: String? {
        let trimmed = capture.source.trimmingCharacters(in: .whitespacesAndNewlines)
        if let citation = CitationDisplay.cleanSourceURL(trimmed) {
            return citation
        }
        return trimmed.isEmpty ? nil : SourceDisplayName.label(trimmed)
    }

    private var capturedDate: String? {
        guard let capturedAt = capture.captured_at?.trimmingCharacters(in: .whitespacesAndNewlines),
              !capturedAt.isEmpty else {
            return nil
        }
        return String(capturedAt.prefix(10))
    }
}

private func reviewShortDate(_ value: String) -> String {
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return "recently" }
    return String(trimmed.prefix(10))
}

struct ReviewCaptureCard: View {
    let capture: CaptureItem
    let approve: () -> Void
    let archive: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                        .font(CortexDesign.Typography.title)
                        .foregroundColor(CortexDesign.ink)
                        .lineLimit(2)
                }
                Spacer()
            }

            if let summary = capture.summary, !summary.isEmpty {
                Text(MemoryText.displayProse(summary, maxLength: 360))
                    .font(CortexDesign.Typography.prose(14))
                    .foregroundColor(CortexDesign.ink)
                    .lineSpacing(3)
                    .lineLimit(4)
                    .truncationMode(.tail)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .help(MemoryText.normalizedProse(summary))
            } else {
                Text("No summary yet.")
                    .font(CortexDesign.Typography.body)
                    .foregroundColor(CortexDesign.inkSecondary)
            }

            ReviewPreviewList(capture: capture)

            HStack(alignment: .center, spacing: 10) {
                if !sourceSegments.isEmpty {
                    AccessionStamp(segments: sourceSegments)
                        .truncationMode(.middle)
                        .help(sourceDetail)
                }
                Spacer()
                Button {
                    archive()
                } label: {
                    Label("Archive", systemImage: "archivebox")
                }
                Button {
                    approve()
                } label: {
                    Label("Approve", systemImage: "checkmark.seal")
                }
                .buttonStyle(.borderedProminent)
            }
        }
        // A pending index card: content clears the gold (unreviewed) margin rule.
        .padding(.leading, 10)
        .cortexCard(padding: 12, background: CortexDesign.panelBackground)
        .archiveSpine(CortexDesign.gold)
    }

    private var title: String {
        cortexCaptureTitle(capture)
    }

    private var sourceSegments: [String] {
        var parts: [String] = []
        let source = capture.source.trimmingCharacters(in: .whitespacesAndNewlines)
        if !source.isEmpty {
            parts.append(SourceDisplayName.label(source))
        }
        if let date = capture.captured_at?.trimmingCharacters(in: .whitespacesAndNewlines), !date.isEmpty {
            parts.append(String(date.prefix(10)))
        }
        if let citation = CitationDisplay.label(sourceURL: capture.source_url) {
            parts.append(citation)
        }
        return parts
    }

    private var sourceDetail: String {
        sourceSegments.joined(separator: " · ")
    }
}

struct ReviewPreviewList: View {
    let capture: CaptureItem

    private var memories: [MemoryItem] {
        cortexDedupedMemories(capture.preview_memories ?? [], limit: 5)
    }

    private var tasks: [TaskItem] {
        Array((capture.preview_tasks ?? []).prefix(3))
    }

    var body: some View {
        if memories.isEmpty && tasks.isEmpty {
            HStack(spacing: 6) {
                Image(systemName: "hourglass")
                Text("Cortex is still preparing proposed memory for this item.")
            }
            .font(CortexDesign.Typography.caption)
            .foregroundColor(CortexDesign.inkSecondary)
        } else {
            VStack(alignment: .leading, spacing: 8) {
                Text("What Cortex will remember".uppercased())
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.inkFaint)

                ForEach(memories) { memory in
                    ReviewMemoryPreviewRow(memory: memory)
                }

                if !tasks.isEmpty {
                    Divider()
                    ForEach(tasks) { task in
                        ReviewTaskPreviewRow(task: task)
                    }
                }
            }
            .padding(.vertical, 2)
        }
    }
}

struct ReviewMemoryPreviewRow: View {
    let memory: MemoryItem

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            VStack(alignment: .leading, spacing: 3) {
                Text(display.headline)
                    .font(CortexDesign.Typography.prose(13.5))
                    .foregroundColor(CortexDesign.ink)
                    .lineLimit(display.path == nil ? 4 : 2)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                if let path = display.path {
                    Text(path)
                        .font(.system(size: 11, weight: .medium, design: .monospaced))
                        .foregroundColor(CortexDesign.inkFaint)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .help(MemoryText.unwrap(memory.content))
                }
                if let citation = CitationDisplay.label(sourceURL: memory.source_url) {
                    Label(citation, systemImage: "quote.bubble")
                        .font(.caption2)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .help(memory.source_url ?? citation)
                }
            }
            Spacer(minLength: 0)
        }
    }

    private var display: (headline: String, path: String?) {
        MemoryText.displayContent(memory.content)
    }
}

struct ReviewTaskPreviewRow: View {
    let task: TaskItem

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text(task.content)
                .font(CortexDesign.Typography.prose(13.5))
                .foregroundColor(CortexDesign.ink)
                .lineLimit(3)
                .truncationMode(.tail)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
    }
}

/// A short, human-friendly name for a memory kind — shown in the review pills instead of the raw
/// backend kind/layer (e.g. "event · episodic" → "Event"). Users shouldn't see internal jargon.
func cortexFriendlyMemoryKind(_ kind: String) -> String {
    switch kind.lowercased() {
    case "decision": return "Decision"
    case "preference": return "Preference"
    case "style": return "Style"
    case "negative": return "Dislike"
    case "procedure", "procedural": return "How-to"
    case "action": return "To-do"
    case "event", "episodic": return "Event"
    case "semantic", "fact": return "Fact"
    case "question": return "Question"
    case "source": return "Source"
    case "task": return "Task"
    case "": return "Memory"
    default: return kind.prefix(1).uppercased() + kind.dropFirst()
    }
}

/// Collapse near-identical preview memories (e.g. a folder of near-identical file paths) so a
/// review card doesn't show the same line five times, then cap to `limit`.
func cortexDedupedMemories(_ items: [MemoryItem], limit: Int) -> [MemoryItem] {
    var seen = Set<String>()
    var out: [MemoryItem] = []
    for memory in items {
        let key = MemoryText.dedupeKey(memory.content)
        if seen.contains(key) { continue }
        seen.insert(key)
        out.append(memory)
        if out.count == limit { break }
    }
    return out
}

/// A human-friendly title for a review card: the capture's title if present, otherwise a cleaned
/// filename/source rather than a raw path or bare id.
func cortexCaptureTitle(_ capture: CaptureItem) -> String {
    if let title = capture.title?.trimmingCharacters(in: .whitespacesAndNewlines), !title.isEmpty {
        return title
    }
    let source = capture.source.trimmingCharacters(in: .whitespacesAndNewlines)
    if source.isEmpty { return "Untitled review item" }
    if MemoryText.isPathLike(source), let name = MemoryText.filename(source) { return name }
    return CitationDisplay.cleanSourceURL(source) ?? source
}
