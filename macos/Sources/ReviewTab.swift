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
                    Text("Approve what Cortex should remember. Archive the rest.")
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
            return "Approve useful items, archive noise. Approved memory powers Ask and your AI tools."
        }
        if !sources.isEmpty {
            return "New synced memories land here first."
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

/// The in-card wax-seal morph confirm — replaces the native `confirmationDialog`. When a destructive
/// action is armed, the card's action row morphs INTO this panel: a wax seal "sets into the paper" on
/// the confirm press. One line of consequence copy, a quiet Cancel, and a wax-red primary that stamps
/// the decision. No modal, no system sheet — the confirm happens where the eyes already are.
struct ReviewWaxSealConfirm: View {
    let message: String
    let confirmTitle: String
    let onConfirm: () -> Void
    let onCancel: () -> Void

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            // The wax disc: a domed seal surface with an embossed serif mark, the "are you sure" beat.
            ZStack {
                Circle().fill(CortexDesign.accent)
                Circle()
                    .fill(
                        RadialGradient(
                            colors: [Color.white.opacity(0.18), .clear],
                            center: .init(x: 0.3, y: 0.25),
                            startRadius: 0,
                            endRadius: 18
                        )
                    )
                Circle().strokeBorder(CortexDesign.accent.opacity(0.55), lineWidth: 1)
                Image(systemName: "exclamationmark")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundColor(CortexDesign.panelBackground)
            }
            .frame(width: 26, height: 26)
            .accessibilityHidden(true)

            Text(message)
                .font(CortexDesign.Typography.caption)
                .foregroundColor(CortexDesign.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)

            Spacer(minLength: 8)

            CortexButton(title: "Cancel", role: .ghost, size: .small) { onCancel() }
                .keyboardShortcut(.cancelAction)
            CortexButton(title: confirmTitle, systemImage: "seal.fill", role: .primary, size: .small) {
                onConfirm()
            }
        }
        .padding(.horizontal, CortexDesign.Space.md)
        .padding(.vertical, CortexDesign.Space.sm)
        .background(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .fill(CortexDesign.accentSoft)
        )
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .stroke(CortexDesign.accent.opacity(0.3), lineWidth: 1)
        )
        .transition(.opacity.combined(with: .scale(scale: 0.97, anchor: .trailing)))
        .accessibilityElement(children: .combine)
        .accessibilityLabel(message)
    }
}

/// The section board: a large backlog rendered as at most 15 one-decision groups.
/// Each card is a project/folder ("Magic Agent Demo Codex Launch · 1,238 notes") with
/// sample titles for a sniff test, and Approve/Archive act on the WHOLE section server-side.
struct ReviewSectionsBoard: View {
    @ObservedObject var state: AppState

    /// The calm-facing item count: a big backlog collapses to "99+" so the section board reads as
    /// "a few tidy decisions", never a raw scary "1,238 items".
    private var pendingDisplay: String {
        state.reviewSectionsPendingTotal > 99 ? "99+" : "\(state.reviewSectionsPendingTotal)"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Review by section")
                    .font(CortexDesign.Typography.title)
                    .foregroundColor(CortexDesign.ink)
                Text("\(pendingDisplay) items in \(state.reviewSections.count) sections, one decision each.")
                    .font(CortexDesign.Typography.body)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            LazyVStack(alignment: .leading, spacing: 10) {
                ForEach(state.reviewSections) { section in
                    // The archive confirm now morphs IN-CARD (no native confirmationDialog): the card
                    // owns its own armed state and calls archive only on the sealed confirm.
                    ReviewSectionCard(
                        section: section,
                        isInFlight: state.inFlightSectionIds.contains(section.section_id),
                        approve: { state.approveReviewSection(section) },
                        archive: { state.archiveReviewSection(section) }
                    )
                }
            }
        }
    }
}

struct ReviewSectionCard: View {
    let section: ReviewSection
    let isInFlight: Bool
    let approve: () -> Void
    let archive: () -> Void
    @State private var isHovered = false
    // Armed = the in-card wax-seal archive confirm is showing (replaces the native dialog).
    @State private var armed = false

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
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 14) {
                VStack(alignment: .leading, spacing: 6) {
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
                    // The kind-distribution bar: memories vs tasks as a proportioned rule so the mix
                    // reads at a glance. Only when the section actually carries countable items.
                    if section.memory_count + section.task_count > 0 {
                        ReviewKindDistributionBar(
                            memoryCount: section.memory_count,
                            taskCount: section.task_count
                        )
                        .frame(maxWidth: 220)
                    }
                }
                Spacer(minLength: 12)
                if !armed {
                    if isInFlight {
                        // Ghost skeleton stamp instead of the stock spinner while acting.
                        ReviewGhostStamp()
                    }
                    CortexButton(title: "Archive", systemImage: "archivebox", role: .ghost) {
                        withAnimation(CortexMotion.press) { armed = true }
                    }
                    .disabled(isInFlight)
                    .help("Archives the \(section.capture_count) items in this section")
                    CortexButton(title: "Approve", systemImage: "checkmark.seal", role: .primary) {
                        approve()
                    }
                    .disabled(isInFlight)
                    .help("Approves the \(section.capture_count) items in this section")
                }
            }

            // Peeking sample mini-cards: a sniff test of what's inside, as tiny stacked index cards.
            if !armed, !section.sample_titles.isEmpty {
                ReviewSampleMiniCards(titles: section.sample_titles)
            }

            // The in-card wax-seal archive confirm, morphing in over the action row.
            if armed {
                ReviewWaxSealConfirm(
                    message: "Archive \(section.capture_count) items in “\(section.label)”? Your original notes stay in your source.",
                    confirmTitle: "Archive section",
                    onConfirm: {
                        withAnimation(CortexMotion.press) { armed = false }
                        archive()
                    },
                    onCancel: {
                        withAnimation(CortexMotion.press) { armed = false }
                    }
                )
            }
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

/// A proportioned two-segment rule showing a section's memory-vs-task mix — the archive's
/// kind-distribution bar. Wax red = memories (kept knowledge), gold = tasks (open loops). No labels
/// clutter the rule; a tooltip carries the exact counts.
struct ReviewKindDistributionBar: View {
    let memoryCount: Int
    let taskCount: Int

    private var total: Int { max(memoryCount + taskCount, 1) }

    var body: some View {
        GeometryReader { geo in
            let width = geo.size.width
            let memoryWidth = width * CGFloat(memoryCount) / CGFloat(total)
            HStack(spacing: 0) {
                if memoryCount > 0 {
                    Rectangle()
                        .fill(CortexDesign.accent.opacity(0.8))
                        .frame(width: memoryWidth)
                }
                if taskCount > 0 {
                    Rectangle()
                        .fill(CortexDesign.gold.opacity(0.85))
                }
            }
        }
        .frame(height: 4)
        .clipShape(Capsule())
        .help("\(memoryCount) memor\(memoryCount == 1 ? "y" : "ies") · \(taskCount) task\(taskCount == 1 ? "" : "s")")
        .accessibilityLabel("\(memoryCount) memories, \(taskCount) tasks")
    }
}

/// The peeking sample mini-cards: up to three sample titles rendered as tiny stacked index cards, a
/// physical "riffle the stack" preview of what a section holds. Never fabricated — omitted when the
/// backend supplied no sample titles.
struct ReviewSampleMiniCards: View {
    let titles: [String]

    var body: some View {
        HStack(spacing: 8) {
            ForEach(Array(titles.prefix(3).enumerated()), id: \.offset) { _, title in
                Text(title)
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .frame(maxWidth: 180, alignment: .leading)
                    .background(
                        RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                            .fill(CortexDesign.quietBackground)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                            .stroke(CortexDesign.hairline, lineWidth: 1)
                    )
                    // A gold hairline rail on the leading edge — a pending index card in miniature.
                    .overlay(alignment: .leading) {
                        RoundedRectangle(cornerRadius: 1)
                            .fill(CortexDesign.gold.opacity(0.7))
                            .frame(width: 2)
                            .padding(.vertical, 5)
                    }
                    .help(title)
            }
            if titles.count > 3 {
                Text("+\(titles.count - 3)")
                    .font(CortexDesign.Typography.hint)
                    .foregroundColor(CortexDesign.inkFaint)
            }
            Spacer(minLength: 0)
        }
    }
}

/// A quiet ghost index-card skeleton stamp shown in place of the stock spinner while a section or
/// capture action is in flight — a small pulsing mono "sealing…" mark on a quiet ground.
struct ReviewGhostStamp: View {
    @State private var pulse = false

    var body: some View {
        Text("SEALING")
            .font(CortexDesign.Typography.hint)
            .kerning(0.8)
            .foregroundColor(CortexDesign.inkFaint)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(
                RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                    .fill(CortexDesign.quietBackground)
            )
            .opacity(pulse ? 0.5 : 1)
            .onAppear {
                withAnimation(.easeInOut(duration: 0.7).repeatForever(autoreverses: true)) {
                    pulse = true
                }
            }
            .accessibilityLabel("Working")
    }
}

/// The calm focus lead: the antidote to "a thousand things to review". It leads with the small set the
/// user is actually looking at ("A few to look at today"), and presents any larger backlog only as a
/// soft, no-pressure secondary line ("and plenty more whenever you like"). The exact backlog number is
/// never rendered when it's large: it collapses to "99+" so Review reads as an invitation, not a chore.
struct ReviewFocusLead: View {
    let focusCount: Int
    let backlogBeyondFocus: Int
    let pendingDisplay: String

    private var focusTitle: String {
        // Warm, low-pressure framing scaled to how much is on screen, never a count-driven alarm.
        switch focusCount {
        case 0: return "You're all caught up"
        case 1: return "One to look at"
        case 2...5: return "A few to look at"
        default: return "Today's review"
        }
    }

    private var focusDetail: String {
        // The soft secondary backlog line: gentle, optional, never an exact scary number when large.
        if backlogBeyondFocus <= 0 {
            return "Take a look when you have a moment. No rush."
        }
        return "Start with these. Plenty more whenever you like."
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "cup.and.saucer")
                .font(.headline)
                .foregroundColor(CortexDesign.sealMoss)
                .frame(width: 28, height: 28)
                .background(CortexDesign.sealMoss.opacity(0.11))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 4) {
                Text(focusTitle)
                    .font(CortexDesign.Typography.title)
                    .foregroundColor(CortexDesign.ink)
                Text(focusDetail)
                    .font(CortexDesign.Typography.body)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 0)

            // The backlog whisper: a quiet capped pill, never the raw scary count. Only when there is
            // meaningfully more behind the focus set.
            if backlogBeyondFocus > 0 {
                Text("\(pendingDisplay) waiting")
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .background(CortexDesign.quietBackground)
                    .clipShape(Capsule())
                    .help("There's no deadline. Review at your own pace.")
            }
        }
        .cortexCard(padding: CortexDesign.Space.md, background: CortexDesign.panelBackground)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(focusTitle). \(focusDetail)")
    }
}

struct ReviewInboxSection: View {
    @ObservedObject var state: AppState
    let captures: [CaptureItem]

    private static let pageSize = 10
    @State private var visibleLimit = ReviewInboxSection.pageSize
    @State private var confirmApproveAll = false
    @State private var approveAllInFlight = false

    // Sections earn their space only when they actually compress work: a backlog
    // bigger than one page, grouped into more than one section.
    private var showSections: Bool {
        state.reviewSectionsPendingTotal > Self.pageSize && state.reviewSections.count > 1
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            // The calm focus lead: leads with the small set on screen ("A few to look at"), and only
            // whispers the rest of the backlog as a soft secondary line. Never a scary raw number.
            if !captures.isEmpty {
                ReviewFocusLead(
                    focusCount: focusCount,
                    backlogBeyondFocus: backlogBeyondFocus,
                    pendingDisplay: pendingDisplay
                )
            }
            if showSections {
                ReviewSectionsBoard(state: state)
            }
            // The approve-all confirm now morphs in-line here (no native confirmationDialog): a
            // wax-seal panel replaces the action row while armed.
            if confirmApproveAll {
                ReviewWaxSealConfirm(
                    message: "Approve all \(totalPendingCount) items? Everything waiting becomes usable memory. You can still archive or forget individual memories later.",
                    confirmTitle: "Approve all",
                    onConfirm: {
                        withAnimation(CortexMotion.press) { confirmApproveAll = false }
                        approveAll(source: nil)
                    },
                    onCancel: {
                        withAnimation(CortexMotion.press) { confirmApproveAll = false }
                    }
                )
            } else {
                HStack(alignment: .center, spacing: 14) {
                    if showSections {
                        Text("Or review one at a time".uppercased())
                            .font(CortexDesign.Typography.stamp)
                            .kerning(0.8)
                            .foregroundColor(CortexDesign.inkFaint)
                    }
                    Spacer()
                    if visibleCount > 3 {
                        // approveCaptures caps the batch at 10 server-side, so approve exactly that
                        // slice and label the button with the true count — no promising more than we act on.
                        let approveBatch = Array(visibleCaptures.prefix(10))
                        if approveAllInFlight {
                            // Ghost skeleton stamp instead of the stock spinner.
                            ReviewGhostStamp()
                        }
                        if totalPendingCount > approveBatch.count {
                            // The power action for the 99+ backlog the 10-at-a-time batch can't clear.
                            // Secondary on purpose: "Approve N shown" keeps the wax-red primary because
                            // it only approves what the user has actually looked at.
                            CortexButton(title: "Approve all \(totalPendingCount)", systemImage: "checkmark.seal.fill", role: .secondary, size: .large) {
                                withAnimation(CortexMotion.press) { confirmApproveAll = true }
                            }
                            .disabled(approveAllInFlight || !state.inFlightCaptureIds.isEmpty)
                            .help("Approve every pending item, including \(totalPendingCount - approveBatch.count) not shown here")
                        }
                        CortexButton(title: "Approve \(approveBatch.count) shown", systemImage: "checkmark.seal", role: .primary, size: .large) {
                            state.approveCaptures(approveBatch)
                        }
                        .disabled(approveAllInFlight || !state.inFlightCaptureIds.isEmpty)
                        .help("Approve the \(approveBatch.count) items shown at the top")
                    }
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
                            // The card's raw source string is the one value the backend matches
                            // exactly, so per-source approve-all hangs off the card — offered only
                            // when the source actually repeats in the queue.
                            approveAllFromSource: pendingSourceCounts[capture.source, default: 0] > 1
                                ? { approveAll(source: capture.source) }
                                : nil,
                            isTopItem: capture.id == visibleCaptures.first?.id
                        )
                        .transition(.asymmetric(
                            insertion: .opacity,
                            removal: .move(edge: .trailing).combined(with: .opacity)
                        ))
                    }

                    if captures.count > visibleCount {
                        CortexButton(title: "Show more (\(captures.count - visibleCount) remaining)", systemImage: "chevron.down", role: .ghost, size: .large, fullWidth: true) {
                            visibleLimit += Self.pageSize
                        }
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

    /// The whole backlog, not the loaded page: the inbox request caps at 30 items, so a 99+ queue
    /// is only visible through the review stats. Never report fewer than what's already on screen.
    private var totalPendingCount: Int {
        max(state.review?.stats.pending_captures ?? 0, captures.count)
    }

    /// The calm-facing count: a big backlog is never rendered as a scary raw "1,238". Anything past
    /// 99 collapses to "99+" so Review reads as an invitation, not an overwhelming chore. Used for the
    /// header lead and the soft secondary backlog line; the pinned "Approve all N" button keeps the
    /// exact count so a user who opts into the bulk clear sees precisely what it acts on.
    private var pendingDisplay: String {
        totalPendingCount > 99 ? "99+" : "\(totalPendingCount)"
    }

    /// The small curated focus set the user is actually looking at right now, framed calmly.
    private var focusCount: Int {
        visibleCount
    }

    /// How much backlog sits behind the focus set, if any: the soft secondary line, never a scary
    /// exact number when it's large.
    private var backlogBeyondFocus: Int {
        max(totalPendingCount - focusCount, 0)
    }

    /// How many loaded captures share each raw source string — gates the per-source approve-all so
    /// it only appears where it approves more than the card it's invoked from.
    private var pendingSourceCounts: [String: Int] {
        captures.reduce(into: [:]) { counts, capture in
            guard !capture.source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
            counts[capture.source, default: 0] += 1
        }
    }

    private func approveAll(source: String?) {
        guard !approveAllInFlight else { return }
        approveAllInFlight = true
        Task {
            await state.approveAllCaptures(source: source)
            approveAllInFlight = false
        }
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
                    CortexButton(title: "Ask a question", systemImage: "magnifyingglass", role: .primary, size: .large) {
                        state.selectedTab = .ask
                        state.status = "Ask \(DistributionMode.appDisplayName)"
                    }
                } else if state.hasConnectedObsidianVault, let connector = obsidianConnector {
                    CortexButton(
                        title: state.notesNeedContent ? "Choose notes" : "Sync notes",
                        systemImage: state.notesNeedContent ? "folder.badge.questionmark" : "arrow.triangle.2.circlepath",
                        role: .primary,
                        size: .large
                    ) {
                        state.connectLocalNotesFolder(connector, chooseNew: state.notesNeedContent)
                    }
                    .disabled(state.isBusy)
                } else {
                    CortexButton(title: "Connect notes", systemImage: "folder.badge.plus", role: .primary, size: .large) {
                        if let connector = obsidianConnector {
                            state.connectLocalNotesFolder(connector, chooseNew: state.notesNeedContent)
                        } else {
                            state.openConnectionsPrivacy(statusMessage: "Connect notes")
                        }
                    }
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

/// A ghost index-card skeleton in place of the stock spinner: two stacked review-card silhouettes
/// (gold pending rail, a title bar, two preview lines, an action stub) breathing on a slow pulse, so
/// the wait previews the queue that's coming. Deterministic; no `.random`.
struct ReviewLoadingCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            ReviewGhostCard()
            ReviewGhostCard()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement()
        .accessibilityLabel("Loading review queue")
    }
}

/// One ghost review card — the silhouette a real `ReviewQueueCaptureCard` casts while loading.
struct ReviewGhostCard: View {
    @State private var pulse = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            skeletonBar(width: 0.6, height: 14)   // title
            VStack(alignment: .leading, spacing: 8) {
                skeletonBar(width: 0.92, height: 10)
                skeletonBar(width: 0.78, height: 10)
            }
            HStack {
                Spacer()
                skeletonBar(width: 0.18, height: 24)   // action stub
                    .frame(width: 76)
                skeletonBar(width: 0.22, height: 24)   // action stub
                    .frame(width: 92)
            }
        }
        .padding(.leading, 10)
        .cortexCard(padding: CortexDesign.Space.lg, background: CortexDesign.panelBackground)
        .archiveSpine(CortexDesign.gold.opacity(0.5))
        .opacity(pulse ? 0.65 : 1)
        .onAppear {
            withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) {
                pulse = true
            }
        }
    }

    private func skeletonBar(width: CGFloat, height: CGFloat) -> some View {
        GeometryReader { geo in
            RoundedRectangle(cornerRadius: 3, style: .continuous)
                .fill(CortexDesign.ink.opacity(0.06))
                .frame(width: geo.size.width * width, height: height)
        }
        .frame(height: height)
    }
}

struct ReviewServiceStartingState: View {
    @ObservedObject var state: AppState

    private var needsAttention: Bool {
        CortexRecoveryText.needsAttention(state.displayStatus)
    }

    private var title: String {
        needsAttention ? "\(DistributionMode.appDisplayName) needs attention" : "\(DistributionMode.appDisplayName) is starting"
    }

    private var detail: String {
        if needsAttention {
            return state.displayStatus
        }
        return "Reconnecting to your local memory engine."
    }

    var body: some View {
        VStack(spacing: 12) {
            HStack(spacing: 10) {
                if needsAttention {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.headline)
                        .foregroundColor(CortexDesign.accent)
                } else {
                    // Ghost stamp instead of the stock spinner while the engine reconnects.
                    ReviewGhostStamp()
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

            CortexButton(title: needsAttention ? "Try again" : "Reconnect", systemImage: "arrow.clockwise", role: .secondary, size: .large) {
                Task {
                    await state.ensureBackend()
                    await state.loadDiagnostics()
                    await state.loadInbox()
                    await state.loadReview()
                    await state.loadStats()
                }
            }
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
    /// Present only when this capture's source repeats in the queue: approves EVERY pending item
    /// from the same source, not just this card.
    var approveAllFromSource: (() -> Void)? = nil
    var isTopItem: Bool = false
    @State private var confirmArchive = false
    @State private var isHovered = false
    // Flips on the first confirmed archive so triage is one click after one informed consent.
    @AppStorage("cortex.review.archiveConfirmedOnce") private var archiveConfirmedOnce = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top, spacing: 12) {
                    // The memory-kind glyph chip — a colored type mark echoing the left rail.
                    Image(systemName: cortexMemoryKindGlyph(dominantKind))
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(railColor)
                        .frame(width: 24, height: 24)
                        .background(
                            RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                                .fill(railColor.opacity(0.12))
                        )
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(title)
                            .font(CortexDesign.Typography.title)
                            .foregroundColor(CortexDesign.ink)
                            .lineLimit(3)
                            .fixedSize(horizontal: false, vertical: true)
                        // The type name in the archive stamp voice.
                        if !dominantKind.isEmpty {
                            Text(cortexFriendlyMemoryKind(dominantKind).uppercased())
                                .font(CortexDesign.Typography.hint)
                                .kerning(0.8)
                                .foregroundColor(railColor)
                        }
                    }
                    Spacer(minLength: 12)
                    VStack(alignment: .trailing, spacing: 6) {
                        Text(reviewSizeLabel.uppercased())
                            .font(CortexDesign.Typography.stamp)
                            .kerning(0.8)
                            .foregroundColor(CortexDesign.inkFaint)
                            .lineLimit(1)
                        // The novelty/confidence seal — a small tinted stamp, never fabricated.
                        if let seal {
                            HStack(spacing: 4) {
                                Image(systemName: seal.systemImage)
                                    .font(.system(size: 9, weight: .medium))
                                Text(seal.label.uppercased())
                                    .font(CortexDesign.Typography.hint)
                                    .kerning(0.6)
                            }
                            .foregroundColor(seal.color)
                            .padding(.horizontal, 7)
                            .padding(.vertical, 3)
                            .background(
                                Capsule().fill(seal.color.opacity(0.12))
                            )
                            .help("Confidence and novelty are derived from the proposed memory, not fabricated.")
                        }
                    }
                }

                if let summary = cleanedSummary {
                    Text(summary)
                        .font(CortexDesign.Typography.prose(14))
                        .foregroundColor(CortexDesign.ink)
                        .lineSpacing(3)
                        .lineLimit(2)
                        .truncationMode(.tail)
                        .fixedSize(horizontal: false, vertical: true)
                        .help(MemoryText.normalizedProse(capture.summary ?? summary))
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

            if confirmArchive {
                // The in-card wax-seal archive confirm (replaces the native confirmationDialog).
                ReviewWaxSealConfirm(
                    message: "Archive this review item? Cortex won't remember it; your original note stays in your source. We'll only ask this once.",
                    confirmTitle: "Archive",
                    onConfirm: {
                        archiveConfirmedOnce = true
                        withAnimation(CortexMotion.press) { confirmArchive = false }
                        archive()
                    },
                    onCancel: {
                        withAnimation(CortexMotion.press) { confirmArchive = false }
                    }
                )
            } else {
                HStack(alignment: .center, spacing: 12) {
                    if isInFlight {
                        // Ghost stamp instead of the stock spinner.
                        ReviewGhostStamp()
                    }
                    // The active top card advertises its physical keyboard triage.
                    if isTopItem, !isInFlight {
                        HStack(spacing: 6) {
                            Image(systemName: "return")
                                .font(.system(size: 9, weight: .semibold))
                            Text("⌘↩ approve · ⌘⌫ archive")
                                .font(CortexDesign.Typography.hint)
                        }
                        .foregroundColor(CortexDesign.inkFaint)
                    }
                    Spacer()
                    // Archive: a gold-spined ghost — the "set aside" gesture, quiet.
                    CortexButton(title: "Archive", systemImage: "archivebox", role: .ghost, size: .large) {
                        requestArchive()
                    }
                    .disabled(isInFlight)
                    // Optional-shortcut overload (macOS 12.3+): only the top card answers ⌘⌫.
                    .keyboardShortcut(isTopItem ? KeyboardShortcut(.delete, modifiers: .command) : nil)
                    .help("Archive (⌘⌫ archives the top item)")
                    // Approve: the moss checkmark-seal — the "kept" gesture, the wax-red primary.
                    CortexButton(title: "Approve", systemImage: "checkmark.seal", role: .primary, size: .large) {
                        approve()
                    }
                    .disabled(isInFlight)
                    .keyboardShortcut(isTopItem ? KeyboardShortcut(.return, modifiers: .command) : nil)
                    .help("Approve (⌘↩ approves the top item)")
                }
            }
        }
        // The unreviewed index card: content clears the kind-colored margin rule by 10pt. The top
        // card is "active" — a hairline wax top edge-light lifts it above the queue.
        .padding(.leading, 10)
        .cortexCard(padding: CortexDesign.Space.lg, background: CortexDesign.panelBackground)
        .archiveSpine(railColor)
        .overlay(alignment: .top) {
            if isTopItem {
                Rectangle()
                    .fill(
                        LinearGradient(
                            colors: [CortexDesign.accent.opacity(0.45), CortexDesign.accent.opacity(0)],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .frame(height: 2)
                    .clipShape(RoundedRectangle(cornerRadius: 1))
                    .padding(.horizontal, 2)
            }
        }
        .shadow(color: CortexDesign.ink.opacity((isHovered || isTopItem) ? 0.07 : 0), radius: 10, y: 3)
        .onHover { hovering in
            withAnimation(.easeOut(duration: 0.15)) { isHovered = hovering }
        }
        // Right-click mirrors the two card actions (menu items are system-styled by design),
        // plus the per-source bulk approve when the source repeats in the queue.
        .contextMenu {
            Button {
                approve()
            } label: {
                Label("Approve", systemImage: "checkmark.seal")
            }
            Button {
                requestArchive()
            } label: {
                Label("Archive", systemImage: "archivebox")
            }
            if let approveAllFromSource {
                Divider()
                Button {
                    approveAllFromSource()
                } label: {
                    Label("Approve all from \(sourceDisplayName)", systemImage: "checkmark.seal.fill")
                }
                .disabled(isInFlight)
            }
        }
    }

    private func requestArchive() {
        if archiveConfirmedOnce {
            archive()
        } else {
            confirmArchive = true
        }
    }

    private var title: String {
        cortexCaptureTitle(capture)
    }

    /// Menu-friendly name for the capture's source: a cleaned filename for path-like sources,
    /// otherwise the catalog display name — never a raw path or URL in a menu item.
    private var sourceDisplayName: String {
        let source = capture.source.trimmingCharacters(in: .whitespacesAndNewlines)
        if MemoryText.isPathLike(source), let name = MemoryText.filename(source) { return name }
        return SourceDisplayName.label(source)
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

    /// The dominant memory kind driving the left rail + glyph, derived from the proposed memories.
    private var dominantKind: String {
        cortexCaptureDominantKind(capture)
    }

    /// The left-edge rail color: the kind's tint when known, otherwise the unreviewed-gold default —
    /// so a pending card without previews still reads as "not yet reviewed".
    private var railColor: Color {
        dominantKind.isEmpty ? CortexDesign.gold : cortexMemoryKindColor(dominantKind)
    }

    /// The novelty/confidence seal — nil when the previews carry no signal.
    private var seal: CortexReviewSeal? {
        cortexCaptureSeal(capture)
    }
}

struct ReviewQueuePreviewList: View {
    let capture: CaptureItem
    @State private var showRest = false

    private var memories: [MemoryItem] {
        cortexDedupedMemories(capture.preview_memories ?? [], limit: 3)
    }

    private var tasks: [TaskItem] {
        Array((capture.preview_tasks ?? []).prefix(2))
    }

    /// The card shows ONE preview line at a glance; the rest tuck behind a disclosure.
    private var previewTexts: [String] {
        memories.map(\.content) + tasks.map(\.content)
    }

    var body: some View {
        if previewTexts.isEmpty {
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

                if let first = previewTexts.first {
                    ReviewQueuePlainPreviewRow(text: first)
                }

                if previewTexts.count > 1 {
                    // Custom serif hairline expander (no native DisclosureGroup).
                    ReviewHairlineExpander(
                        title: "\(previewTexts.count - 1) more",
                        collapseTitle: "Show fewer",
                        isExpanded: $showRest
                    ) {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(Array(previewTexts.dropFirst().enumerated()), id: \.offset) { _, text in
                                ReviewQueuePlainPreviewRow(text: text)
                            }
                        }
                        .padding(.top, 6)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// A custom serif hairline expander for the review surfaces — the archive's disclosure affordance,
/// replacing the native `DisclosureGroup`. A serif label with a wax-red chevron that rotates on
/// toggle; the disclosed content springs open beneath it.
struct ReviewHairlineExpander<Content: View>: View {
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
                HStack(spacing: 6) {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundColor(CortexDesign.accent)
                        .rotationEffect(.degrees(isExpanded ? 90 : 0))
                    Text(isExpanded ? collapseTitle : title)
                        .font(CortexDesign.Typography.caption)
                        .foregroundColor(hovering ? CortexDesign.ink : CortexDesign.inkSecondary)
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
                .lineLimit(2)
                .truncationMode(.tail)
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

/// The memory-kind color rail token: each friendly kind gets one coherent ink from the palette so
/// the left edge of a review card reads its type at a glance (the archive's colored spine). Kept
/// inside the existing three-color guardrail — wax red, gold, moss, and quiet inks only, never new
/// hues. Deterministic; no `.random`.
func cortexMemoryKindColor(_ kind: String) -> Color {
    switch kind.lowercased() {
    case "decision":                 return CortexDesign.accent
    case "preference", "style":      return CortexDesign.gold
    case "negative":                 return CortexDesign.accent
    case "procedure", "procedural",
         "action", "task":           return CortexDesign.sealMoss
    case "event", "episodic":        return CortexDesign.gold
    case "semantic", "fact",
         "question":                 return CortexDesign.inkSecondary
    case "source":                   return CortexDesign.inkFaint
    default:                          return CortexDesign.inkSecondary
    }
}

/// The memory-kind glyph shown on the rail — the same friendly-kind vocabulary as the Ask source
/// margin, so a type reads identically across surfaces.
func cortexMemoryKindGlyph(_ kind: String) -> String {
    switch kind.lowercased() {
    case "decision":                 return "signpost.right"
    case "preference":               return "heart.text.square"
    case "style":                    return "paintbrush.pointed"
    case "negative":                 return "hand.thumbsdown"
    case "procedure", "procedural":  return "list.number"
    case "action", "task":           return "checklist"
    case "event", "episodic":        return "calendar"
    case "semantic", "fact":         return "text.quote"
    case "question":                 return "questionmark.circle"
    case "source":                   return "doc.text"
    default:                         return "square.text.square"
    }
}

/// The dominant memory kind of a capture, derived from its preview memories (never fabricated). Picks
/// the most frequent `kind` among previews; ties break on the kind that sorts first so the rail is
/// deterministic across renders. Returns "" (→ neutral) when there are no previews yet.
func cortexCaptureDominantKind(_ capture: CaptureItem) -> String {
    let kinds = (capture.preview_memories ?? []).map { $0.kind.lowercased() }
        .filter { !$0.isEmpty }
    guard !kinds.isEmpty else {
        // A tasks-only capture still has a kind.
        return (capture.task_count ?? 0) > 0 && (capture.memory_count ?? 0) == 0 ? "task" : ""
    }
    var counts: [String: Int] = [:]
    for k in kinds { counts[k, default: 0] += 1 }
    return counts.sorted { lhs, rhs in
        lhs.value != rhs.value ? lhs.value > rhs.value : lhs.key < rhs.key
    }.first?.key ?? ""
}

/// A capture's novelty/confidence seal: a short mono word + tint derived deterministically from the
/// preview memories' string `confidence` ("high"/"medium"/"low") and importance. Novelty is inferred
/// from importance (the backend's per-memory priority) — never a random value. Returns nil when there
/// is no signal to show, so the seal is never fabricated.
struct CortexReviewSeal {
    let label: String
    let color: Color
    let systemImage: String
}

func cortexCaptureSeal(_ capture: CaptureItem) -> CortexReviewSeal? {
    let memories = capture.preview_memories ?? []
    guard !memories.isEmpty else { return nil }

    // Confidence: the strongest signal the previews carry.
    let confidences = memories.compactMap { $0.confidence?.lowercased() }
    if confidences.contains("high") {
        return CortexReviewSeal(label: "High confidence", color: CortexDesign.sealMoss, systemImage: "checkmark.seal")
    }
    if confidences.contains("low") {
        return CortexReviewSeal(label: "Low confidence", color: CortexDesign.gold, systemImage: "exclamationmark.circle")
    }

    // Novelty proxy: a high-importance memory is a notable, worth-keeping item.
    let importances = memories.compactMap { $0.importance }
    if let peak = importances.max(), peak >= 4 {
        return CortexReviewSeal(label: "Notable", color: CortexDesign.accent, systemImage: "sparkle")
    }
    if !confidences.isEmpty {
        return CortexReviewSeal(label: "Medium confidence", color: CortexDesign.inkSecondary, systemImage: "seal")
    }
    return nil
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
