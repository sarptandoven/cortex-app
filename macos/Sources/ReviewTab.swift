import AppKit
import Foundation
import SwiftUI

struct ReviewTab: View {
    @ObservedObject var state: AppState
    @State private var initialLoadDone = false
    @State private var isReloading = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: CortexDesign.Space.xl) {
                ReviewHeaderSection(
                    state: state,
                    isReloading: isReloading,
                    onRefresh: { Task { await reload() } }
                )
                if !initialLoadDone && state.inbox.isEmpty {
                    ReviewLoadingCard()
                } else {
                    if shouldShowSourceHealth {
                        ReviewSourceHealthStrip(
                            state: state,
                            onSyncNow: { Task { await reload() } }
                        )
                    }
                    ReviewProactiveAlertsSection(state: state)
                    ReviewTwinGradingSection(state: state)
                    ReviewInboxSection(state: state, captures: state.inbox)
                }
            }
            .frame(maxWidth: 680, alignment: .leading)
            // Center the reading column in the window (matches AskTab); text inside stays leading.
            .frame(maxWidth: .infinity)
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
        // U-REV6: keep the proactive alerts + twin grading queue live while the Review tab is on
        // screen. The app's global live-count refresh only re-pulls the inbox for Review, so without
        // this the alert strip and grading queue silently go stale the moment they've loaded once.
        // Scoped to the visible tab and cancelled automatically when the tab-owning view goes away.
        .task(id: state.selectedTab) {
            guard state.selectedTab == .review else { return }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 8_000_000_000)   // ~8s
                if Task.isCancelled || state.selectedTab != .review { break }
                await state.loadProactiveAlerts()
                await state.loadTwinScorecard()
            }
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
    // The tab drives the reload; the header only surfaces the affordance and its in-flight state.
    var isReloading: Bool = false
    var onRefresh: (() -> Void)? = nil
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Review")
                        .font(CortexDesign.Typography.display(22))
                        .foregroundColor(CortexDesign.ink)
                    Text("Approve what \(DistributionMode.appDisplayName) should remember. Archive the rest.")
                        .font(CortexDesign.Typography.body)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                // Manual refresh: the tab already live-refreshes every few seconds, but a quiet
                // "check now" affordance lets an impatient reviewer pull the latest queue on demand.
                if let onRefresh {
                    CortexIconButton(
                        systemImage: "arrow.clockwise",
                        role: .ghost,
                        help: isReloading ? "Refreshing review queue" : "Refresh the review queue"
                    ) {
                        onRefresh()
                    }
                    .disabled(isReloading)
                    .rotationEffect(.degrees(isReloading && !reduceMotion ? 360 : 0))
                    .animation(
                        isReloading && !reduceMotion
                            ? .linear(duration: 0.9).repeatForever(autoreverses: false)
                            : .default,
                        value: isReloading
                    )
                    .accessibilityLabel("Refresh review queue")
                }
            }
        }
    }
}

struct ReviewSourceHealthStrip: View {
    @ObservedObject var state: AppState
    // U-REV1: the tab owns the reload; the strip only surfaces the "Sync now" affordance.
    var onSyncNow: (() -> Void)? = nil

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

    /// U-REV1: the first source actually flagged as needing attention, used to name the fix button
    /// and to target Connections at that exact source.
    private var firstFailing: SourceReadinessItem? {
        sources.first(where: \.needsAttention)
    }

    /// The fix button's title: a short verb phrase naming the source. The full next_action sentence
    /// already reads out in the line above, so the button stays a button, not a repeated sentence.
    private var fixTitle: String {
        "Fix \(firstFailing?.name ?? "source")"
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
                        // U-REV7: the chip is now a real affordance — a failing source opens
                        // Connections targeted at it; a healthy source triggers a fresh sync pull.
                        ReviewSourceHealthChip(
                            source: source,
                            action: {
                                if source.needsAttention {
                                    state.openConnectionsPrivacy(statusMessage: "Fix \(source.name)")
                                } else {
                                    onSyncNow?()
                                }
                            }
                        )
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

            // U-REV1: the strip is a dead read-out no longer. When a source needs attention we hand
            // the reviewer a direct fix button naming that source; when the queue is
            // simply pending/healthy we offer a "Sync now" pull so an impatient reviewer can refresh.
            HStack(spacing: 10) {
                if needsAttentionCount > 0 {
                    CortexButton(title: fixTitle, systemImage: "wrench.and.screwdriver", role: .primary, size: .small) {
                        let name = firstFailing?.name ?? "source"
                        state.openConnectionsPrivacy(statusMessage: "Fix \(name)")
                    }
                    .help("Open Connections to fix \(firstFailing?.name ?? "the source that needs attention")")
                }
                if let onSyncNow, needsAttentionCount == 0, (pendingCount > 0 || !sources.isEmpty) {
                    CortexButton(title: "Sync now", systemImage: "arrow.triangle.2.circlepath", role: .ghost, size: .small) {
                        onSyncNow()
                    }
                    .help("Pull the latest synced memory into the review queue")
                }
                Spacer(minLength: 0)
            }
        }
        .cortexCard(padding: CortexDesign.Space.md, background: CortexDesign.panelBackground)
    }
}

struct ReviewSourceHealthChip: View {
    let source: SourceReadinessItem
    // U-REV7: the chip is now tappable — a failing source jumps to Connections, a healthy one syncs.
    var action: (() -> Void)? = nil
    @State private var hovering = false

    var body: some View {
        Button {
            action?()
        } label: {
            HStack(spacing: 5) {
                Text(label)
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(hovering ? CortexDesign.ink : CortexDesign.inkSecondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                Image(systemName: source.needsAttention ? "arrow.up.right" : "arrow.triangle.2.circlepath")
                    .font(.system(size: 8, weight: .semibold))
                    .foregroundColor(source.needsAttention ? CortexDesign.accent : CortexDesign.inkFaint)
            }
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .background(CortexDesign.quietBackground)
            .overlay(
                Capsule().stroke(CortexDesign.hairline.opacity(hovering ? 1 : 0), lineWidth: 1)
            )
            .clipShape(Capsule())
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .disabled(action == nil)
        .help(helpText)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(helpText)
    }

    private var label: String {
        if source.pending > 0 {
            let shown = source.pending > 99 ? "99+" : "\(source.pending)"
            return "\(source.name) · \(shown) pending"
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
    // Which in-card wax-seal confirm is showing, if any. Archive always confirms; Approve
    // confirms only past a size threshold (a whole section can carry thousands of items).
    private enum ArmedSectionAction { case archive, approve }
    @State private var armedAction: ArmedSectionAction? = nil

    private var groupedCaptureCount: String {
        AnimatableNumber.groupedInteger(Double(section.capture_count))
    }

    private var countLine: String {
        var parts = ["\(groupedCaptureCount) note\(section.capture_count == 1 ? "" : "s")"]
        if section.memory_count > 0 {
            parts.append("\(AnimatableNumber.groupedInteger(Double(section.memory_count))) memories")
        }
        if section.task_count > 0 {
            parts.append("\(AnimatableNumber.groupedInteger(Double(section.task_count))) tasks")
        }
        return parts.joined(separator: " · ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 14) {
                VStack(alignment: .leading, spacing: 6) {
                    // Title gets the full row; counts move to their own caption line so neither
                    // truncates while the user decides the fate of a thousand-item section.
                    VStack(alignment: .leading, spacing: 2) {
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
                .layoutPriority(1)
                Spacer(minLength: 12)
                if armedAction == nil {
                    if isInFlight {
                        // Ghost skeleton stamp instead of the stock spinner while acting.
                        ReviewGhostStamp()
                    }
                    CortexButton(title: "Archive", systemImage: "archivebox", role: .ghost) {
                        withAnimation(CortexMotion.press) { armedAction = .archive }
                    }
                    .disabled(isInFlight)
                    .help("Archives the \(groupedCaptureCount) items in this section")
                    CortexButton(title: "Approve", systemImage: "checkmark.seal", role: .secondary) {
                        // A big section is thousands of memories in one click; arm the same seal
                        // Archive uses. Small sections keep the frictionless single click.
                        if section.capture_count > 25 {
                            withAnimation(CortexMotion.press) { armedAction = .approve }
                        } else {
                            approve()
                        }
                    }
                    .disabled(isInFlight)
                    .help("Approves the \(groupedCaptureCount) items in this section")
                }
            }

            // Peeking sample mini-cards: a sniff test of what's inside, as tiny stacked index cards.
            if armedAction == nil, !section.sample_titles.isEmpty {
                ReviewSampleMiniCards(titles: section.sample_titles)
            }

            // The in-card wax-seal confirm, morphing in over the action row.
            if let action = armedAction {
                ReviewWaxSealConfirm(
                    message: action == .archive
                        ? "Archive \(groupedCaptureCount) items in “\(section.label)”? Your original notes stay in your source."
                        : "Approve \(groupedCaptureCount) note\(section.capture_count == 1 ? "" : "s") in “\(section.label)”? They become memory \(DistributionMode.appDisplayName) can use in Ask.",
                    confirmTitle: action == .archive ? "Archive section" : "Approve section",
                    onConfirm: {
                        withAnimation(CortexMotion.press) { armedAction = nil }
                        if action == .archive { archive() } else { approve() }
                    },
                    onCancel: {
                        withAnimation(CortexMotion.press) { armedAction = nil }
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
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

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
            .onChange(of: scenePhase) { _ in updatePulse() }
            .onChange(of: reduceMotion) { _ in updatePulse() }
            .onAppear { updatePulse() }
            .accessibilityLabel("Working")
    }

    // Pulse only while active and motion is welcome; rest at full legibility otherwise.
    private func updatePulse() {
        guard scenePhase == .active, !reduceMotion else {
            withAnimation(.easeInOut(duration: 0.2)) { pulse = false }
            return
        }
        withAnimation(.easeInOut(duration: 0.7).repeatForever(autoreverses: true)) { pulse = true }
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
    // The inbox endpoint (GET /v1/inbox) pages by `limit` only — no offset — and the server caps a
    // single fetch at 100. loadInbox() fetches the first 30; when the reviewer reaches the end of that
    // page and a larger backlog exists, "Show more" fetches the NEXT server page by growing the limit,
    // so the backlog past the initial 30 is actually reachable (up to the 100 server ceiling; beyond
    // that the section board / "Approve all N" are the tools for a very large backlog).
    private static let serverFetchStart = 30
    private static let serverFetchStep = 30
    private static let serverFetchCap = 100
    @State private var visibleLimit = ReviewInboxSection.pageSize
    @State private var serverFetchLimit = ReviewInboxSection.serverFetchStart
    @State private var loadingMoreFromServer = false
    @State private var previousCaptureCount = 0
    @State private var confirmApproveAll = false
    @State private var approveAllInFlight = false
    // U-REV5: the in-line wax-seal confirm for "Archive N shown" (destructive, so it's guarded).
    @State private var confirmArchiveShown = false
    // U-REV8: triage the queue low-confidence first, so the items most likely to need a human eye
    // float to the top. Off by default — the server order is the calm default.
    @State private var lowConfidenceFirst = false

    // Sections earn their space only when they actually compress work: a backlog
    // bigger than one page, grouped into more than one section.
    private var showSections: Bool {
        state.reviewSectionsPendingTotal > Self.pageSize && state.reviewSections.count > 1
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            // The calm focus lead: leads with the small set on screen ("A few to look at"), and only
            // whispers the rest of the backlog as a soft secondary line. Never a scary raw number.
            if !captures.isEmpty && !showSections {
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
            } else if confirmArchiveShown {
                // U-REV5: the destructive "Archive N shown" confirm — same wax-seal beat as approve-all.
                let archiveBatch = Array(orderedVisibleCaptures.prefix(10))
                ReviewWaxSealConfirm(
                    message: "Archive \(archiveBatch.count) shown item\(archiveBatch.count == 1 ? "" : "s")? \(DistributionMode.appDisplayName) won't remember them; your original notes stay in your source.",
                    confirmTitle: "Archive \(archiveBatch.count) shown",
                    onConfirm: {
                        withAnimation(CortexMotion.press) { confirmArchiveShown = false }
                        state.archiveCaptures(archiveBatch)
                    },
                    onCancel: {
                        withAnimation(CortexMotion.press) { confirmArchiveShown = false }
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
                    // U-REV8: a quiet "Low confidence first" triage sort. Only offered when the queue
                    // actually carries a confidence signal to sort on, and never fabricates one.
                    if showConfidenceSort {
                        CortexButton(
                            title: lowConfidenceFirst ? "Original order" : "Low confidence first",
                            systemImage: lowConfidenceFirst ? "arrow.up.arrow.down" : "arrow.down.circle",
                            role: .ghost,
                            size: .small
                        ) {
                            withAnimation(CortexMotion.press) { lowConfidenceFirst.toggle() }
                        }
                        .help("Sort the queue so the least-certain items surface first")
                    }
                    Spacer()
                    if showBatchApprove {
                        // approveCaptures/archiveCaptures cap the batch at 10 server-side, so act on
                        // exactly that slice and label the buttons with the true count.
                        let approveBatch = Array(orderedVisibleCaptures.prefix(10))
                        if approveAllInFlight {
                            // Ghost skeleton stamp instead of the stock spinner.
                            ReviewGhostStamp()
                        }
                        // U-REV5: the batch archive counterpart to "Approve N shown" — a quiet ghost so
                        // the wax-red approve stays the one primary. Guarded by the wax-seal confirm.
                        CortexButton(title: "Archive \(approveBatch.count) shown", systemImage: "archivebox", role: .ghost, size: .large) {
                            withAnimation(CortexMotion.press) { confirmArchiveShown = true }
                        }
                        .disabled(approveAllInFlight || !state.inFlightCaptureIds.isEmpty)
                        .help("Archive the \(approveBatch.count) items shown at the top")
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
                    ForEach(orderedVisibleCaptures) { capture in
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
                            // U-REV5: per-source archive-all, same gating and scope as approve-all.
                            archiveAllFromSource: pendingSourceCounts[capture.source, default: 0] > 1
                                ? { archiveAll(source: capture.source) }
                                : nil,
                            // U-REV4: edit-before-approve saves the corrected text and archives the draft.
                            approveEdited: { edited in
                                Task { await state.approveCapture(capture, editedContent: edited) }
                            },
                            isTopItem: capture.id == orderedVisibleCaptures.first?.id
                        )
                        .transition(.asymmetric(
                            insertion: .opacity,
                            removal: .move(edge: .trailing).combined(with: .opacity)
                        ))
                    }

                    if captures.count > visibleCount {
                        // More is already loaded locally: just reveal the next slice, no fetch.
                        CortexButton(title: "Show more (\(captures.count - visibleCount) remaining)", systemImage: "chevron.down", role: .ghost, size: .large, fullWidth: true) {
                            visibleLimit += Self.pageSize
                        }
                    } else if canFetchMoreFromServer {
                        // The loaded page is exhausted but the server holds more (the inbox request
                        // caps at 30). Fetch the NEXT page so the backlog is actually reachable —
                        // without this the "Approve all" path was the only way past item 30.
                        CortexButton(
                            title: loadingMoreFromServer ? "Loading more" : "Show more (\(remainingBeyondLoaded) more waiting)",
                            systemImage: loadingMoreFromServer ? "hourglass" : "chevron.down",
                            role: .ghost,
                            size: .large,
                            fullWidth: true
                        ) {
                            fetchMoreFromServer()
                        }
                        .disabled(loadingMoreFromServer)
                    }

                    // U-REV10: once the reviewer has expanded past the first page, let them re-collapse
                    // the queue back to a calm single page instead of scrolling all the way up.
                    if visibleLimit > Self.pageSize {
                        CortexButton(title: "Show fewer", systemImage: "chevron.up", role: .ghost, size: .large, fullWidth: true) {
                            withAnimation(.spring(response: 0.35, dampingFraction: 0.8)) {
                                visibleLimit = Self.pageSize
                            }
                        }
                    }
                }
                .animation(.spring(response: 0.35, dampingFraction: 0.8), value: captures.map(\.id))
            }
        }
        .onChange(of: captures.count) { newCount in
            // A shrink (approve/archive removed an item) re-collapses the queue to a calm first page
            // and resets the server-page cursor so "Show more" starts paging from scratch again. A
            // grow is left alone here: the "Show more" fetch reveals its own new slice, so we must NOT
            // snap the visible window back to page one and hide what the reviewer just pulled in.
            if newCount < previousCaptureCount {
                visibleLimit = Self.pageSize
                serverFetchLimit = Self.serverFetchStart
            }
            previousCaptureCount = newCount
        }
    }

    /// Whether the batch-approve row (Approve N shown / Approve all N) earns its place. It appears
    /// whenever there is more than one thing to batch — either multiple items on screen, or a larger
    /// backlog behind them — so even a tiny 2-3 item queue gets a one-tap batch approve (and every
    /// card in it lights up its own in-flight ghost). A lone single item stays out of the batch row:
    /// its own card button already covers it, so "Approve 1 shown" would just be noise.
    private var showBatchApprove: Bool {
        visibleCount > 1 || totalPendingCount > visibleCount
    }

    private var visibleCaptures: [CaptureItem] {
        Array(captures.prefix(visibleLimit))
    }

    /// U-REV8: the visible page, optionally reordered low-confidence first. The reorder is a stable
    /// sort over the SAME visible slice — it never fabricates a confidence signal (captures with no
    /// seal keep their relative order) and never changes which items are on the page, only their order.
    private var orderedVisibleCaptures: [CaptureItem] {
        guard lowConfidenceFirst else { return visibleCaptures }
        return visibleCaptures.enumerated().sorted { lhs, rhs in
            let lp = reviewConfidenceSortRank(lhs.element)
            let rp = reviewConfidenceSortRank(rhs.element)
            if lp != rp { return lp < rp }
            return lhs.offset < rhs.offset   // stable within a rank
        }.map(\.element)
    }

    private var visibleCount: Int {
        visibleCaptures.count
    }

    /// U-REV8: only offer the confidence sort when the visible queue actually carries a confidence
    /// seal to sort on (mixed ranks) — never a no-op toggle on an all-equal queue.
    private var showConfidenceSort: Bool {
        guard visibleCount > 1 else { return false }
        let ranks = Set(visibleCaptures.map(reviewConfidenceSortRank))
        return ranks.count > 1
    }

    /// The whole backlog, not the loaded page: the inbox request caps at 30 items, so a 99+ queue
    /// is only visible through the review stats. Never report fewer than what's already on screen.
    private var totalPendingCount: Int {
        max(state.review?.stats.pending_captures ?? 0, captures.count)
    }

    /// How many pending items exist on the server past what's currently loaded into the inbox array.
    private var remainingBeyondLoaded: Int {
        max(totalPendingCount - captures.count, 0)
    }

    /// Whether "Show more" can pull the NEXT server page: there IS a backlog past the loaded set, the
    /// current fetch actually filled its page (so more likely exist), and we haven't hit the server's
    /// per-request ceiling. Past the ceiling a very large backlog is cleared via the section board or
    /// "Approve all N", not by paging one screen at a time.
    private var canFetchMoreFromServer: Bool {
        remainingBeyondLoaded > 0
            && captures.count >= serverFetchLimit
            && serverFetchLimit < Self.serverFetchCap
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

    /// U-REV5: archive EVERY pending item from one source in a single action (the counterpart to
    /// `approveAll(source:)`). Mirrors the same in-flight guard so the source's bulk actions never race.
    private func archiveAll(source: String) {
        guard !approveAllInFlight else { return }
        approveAllInFlight = true
        Task {
            await state.archiveAllCaptures(source: source)
            approveAllInFlight = false
        }
    }

    /// Pull the NEXT server page of the inbox. The endpoint pages by `limit` only (no offset) and caps
    /// a single fetch at 100, so paging = re-fetching with a larger limit and replacing the inbox
    /// array. loadInbox() defaults to 30; each "Show more" past the loaded set grows the limit by a
    /// page (capped at 100) so the backlog beyond the initial 30 becomes reachable in the item view.
    private func fetchMoreFromServer() {
        guard !loadingMoreFromServer else { return }
        let nextLimit = min(serverFetchLimit + Self.serverFetchStep, Self.serverFetchCap)
        guard nextLimit > serverFetchLimit else { return }
        loadingMoreFromServer = true
        Task {
            defer { loadingMoreFromServer = false }
            do {
                let data = try await state.request(path: "/v1/inbox?limit=\(nextLimit)", method: "GET")
                let results = try JSONDecoder().decode(InboxResponse.self, from: data).results
                serverFetchLimit = nextLimit
                withAnimation(.spring(response: 0.35, dampingFraction: 0.8)) {
                    state.inbox = results
                    // Reveal the freshly loaded page (the onChange grow-branch also protects this,
                    // but set it here so the new items show even if the count didn't change).
                    visibleLimit = min(results.count, visibleLimit + Self.pageSize)
                }
            } catch {
                state.status = CortexRecoveryText.failureStatus("Load more review items", error: error)
            }
        }
    }

    private var emptyDetail: String {
        if (state.review?.stats.memories ?? 0) == 0 {
            if state.hasConnectedSourceAccount || state.hasConnectedObsidianVault {
                return "Sync your source. New memories will appear here before \(DistributionMode.appDisplayName) uses them."
            }
            return "Connect notes first. New memories will appear here before \(DistributionMode.appDisplayName) uses them."
        }
        return "All caught up. New synced items will appear here before \(DistributionMode.appDisplayName) uses them."
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
                    // U-REV9: the all-clear state was a dead-end — now the reviewer can jump straight
                    // to the memories they just approved instead of only being pointed at Ask.
                    CortexButton(title: "View recent memories", systemImage: "sparkles", role: .secondary, size: .large) {
                        state.selectedTab = .model
                        state.status = "Recent memories"
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
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

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
        .onChange(of: scenePhase) { _ in updatePulse() }
        .onChange(of: reduceMotion) { _ in updatePulse() }
        .onAppear { updatePulse() }
    }

    // Pulse only while active and motion is welcome; rest at full legibility otherwise.
    private func updatePulse() {
        guard scenePhase == .active, !reduceMotion else {
            withAnimation(.easeInOut(duration: 0.2)) { pulse = false }
            return
        }
        withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) { pulse = true }
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
    /// U-REV5: present only when this capture's source repeats in the queue — archives EVERY pending
    /// item from the same source in one action.
    var archiveAllFromSource: (() -> Void)? = nil
    /// U-REV4: save an edited version of the memory before approving, instead of the raw draft.
    var approveEdited: ((String) -> Void)? = nil
    var isTopItem: Bool = false
    @State private var confirmArchive = false
    @State private var isHovered = false
    // U-REV3: reveals the full, untruncated summary + every proposed memory/task.
    @State private var showDetail = false
    // U-REV4: inline edit-before-approve.
    @State private var isEditing = false
    @State private var editedText = ""
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

            // U-REV3: the full, untruncated detail — the whole summary and every proposed memory and
            // task, not the two-line card preview. Kept inline so Approve/Archive stay reachable.
            if showDetail {
                ReviewCaptureDetail(capture: capture)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }

            ReviewQueueSourceBox(capture: capture)

            // U-REV4: the inline edit-before-approve editor — the reviewer rewrites the memory, then
            // "Save & approve" saves the corrected text (the raw draft is archived, never remembered).
            if isEditing {
                ReviewInlineEditor(
                    text: $editedText,
                    onCancel: {
                        withAnimation(CortexMotion.press) { isEditing = false }
                    },
                    onSave: {
                        let trimmed = editedText.trimmingCharacters(in: .whitespacesAndNewlines)
                        withAnimation(CortexMotion.press) { isEditing = false }
                        guard !trimmed.isEmpty else { return }
                        approveEdited?(trimmed)
                    }
                )
                .transition(.opacity.combined(with: .move(edge: .top)))
            }

            if let actionError, !actionError.isEmpty {
                Label(actionError, systemImage: "exclamationmark.triangle.fill")
                    .font(CortexDesign.Typography.body)
                    .foregroundColor(CortexDesign.accent)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if confirmArchive {
                // The in-card wax-seal archive confirm (replaces the native confirmationDialog).
                ReviewWaxSealConfirm(
                    message: "Archive this review item? \(DistributionMode.appDisplayName) won't remember it; your original note stays in your source. We'll only ask this once.",
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
                    // U-REV3: reveal/hide the full detail — the whole summary and every proposed item,
                    // untruncated. A quiet ghost so it never competes with Approve/Archive.
                    if hasExpandableDetail {
                        CortexButton(
                            title: showDetail ? "Hide details" : "Details",
                            systemImage: showDetail ? "chevron.up" : "text.magnifyingglass",
                            role: .ghost,
                            size: .large
                        ) {
                            withAnimation(CortexMotion.press) { showDetail.toggle() }
                        }
                        .disabled(isInFlight)
                        .help("Show the full memory before deciding")
                    }
                    // U-REV4: rewrite the memory before approving. Seeds the editor with the best
                    // single line of proposed content we have (summary → first proposed memory).
                    if approveEdited != nil {
                        CortexButton(title: "Edit", systemImage: "pencil", role: .ghost, size: .large) {
                            editedText = editableSeed
                            withAnimation(CortexMotion.press) { isEditing = true }
                        }
                        .disabled(isInFlight)
                        .help("Edit this memory, then approve the corrected version")
                    }
                    // Archive: a gold-spined ghost — the "set aside" gesture, quiet.
                    CortexButton(title: "Archive", systemImage: "archivebox", role: .ghost, size: .large) {
                        requestArchive()
                    }
                    .disabled(isInFlight)
                    // Optional-shortcut overload (macOS 12.3+): only the top card answers ⌘⌫.
                    .keyboardShortcut(isTopItem ? KeyboardShortcut(.delete, modifiers: .command) : nil)
                    .help("Archive (⌘⌫ archives the top item)")
                    // Approve: the moss checkmark-seal — the "kept" gesture, the wax-red primary.
                    CortexButton(title: "Approve", systemImage: "checkmark.seal", role: .secondary, size: .large) {
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
            // U-REV2: open the underlying note/URL from the right-click menu too.
            if let openURL = reviewOpenableSourceURL(capture) {
                Button {
                    NSWorkspace.shared.open(openURL)
                } label: {
                    Label("Open source", systemImage: "arrow.up.right.square")
                }
            }
            // U-REV3: reveal the full detail from the menu.
            if hasExpandableDetail {
                Button {
                    withAnimation(CortexMotion.press) { showDetail.toggle() }
                } label: {
                    Label(showDetail ? "Hide details" : "Show details", systemImage: "text.magnifyingglass")
                }
            }
            if approveAllFromSource != nil || archiveAllFromSource != nil {
                Divider()
            }
            if let approveAllFromSource {
                Button {
                    approveAllFromSource()
                } label: {
                    Label("Approve all from \(sourceDisplayName)", systemImage: "checkmark.seal.fill")
                }
                .disabled(isInFlight)
                // Honest scope: this approves EVERY pending item from this source server-side, not just
                // the cards on this page — so the label never over-promises or surprises the user.
                .help("Approves every pending item from \(sourceDisplayName), including any not shown on this page")
            }
            // U-REV5: the per-source archive counterpart — archives EVERY pending item from this
            // source in one action, matching the approve-all scope so triage is symmetric.
            if let archiveAllFromSource {
                Button(role: .destructive) {
                    archiveAllFromSource()
                } label: {
                    Label("Archive pending from \(sourceDisplayName)", systemImage: "archivebox.fill")
                }
                .disabled(isInFlight)
                .help("Archives every pending item from \(sourceDisplayName), including any not shown on this page")
            }
        }
    }

    /// U-REV3: whether there is more to show than the card's two-line preview — a long summary, extra
    /// proposed memories/tasks past the previewed ones, or a source URL. Gates the Details affordance
    /// so it never appears on a card that has nothing more to reveal.
    private var hasExpandableDetail: Bool {
        let memoryTotal = capture.preview_memories?.count ?? 0
        let taskTotal = capture.preview_tasks?.count ?? 0
        if memoryTotal + taskTotal > 1 { return true }
        if let summary = capture.summary?.trimmingCharacters(in: .whitespacesAndNewlines),
           summary.count > 160 {
            return true
        }
        return false
    }

    /// U-REV4: the best single line of proposed content to seed the editor with — the summary if
    /// present, else the first proposed memory, else the first proposed task. Never a raw id/path.
    private var editableSeed: String {
        if let summary = capture.summary?.trimmingCharacters(in: .whitespacesAndNewlines), !summary.isEmpty {
            return MemoryText.normalizedProse(summary)
        }
        if let first = capture.preview_memories?.first?.content {
            return MemoryText.normalizedProse(first)
        }
        if let first = capture.preview_tasks?.first?.content {
            return MemoryText.normalizedProse(first)
        }
        return ""
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

/// U-REV3: the full, untruncated detail for a review card — the whole summary and every proposed
/// memory and task, not the two-line card preview. Rendered inline beneath the card's preview so the
/// reviewer never loses the Approve/Archive row while reading. Only real, backend-supplied content;
/// nothing fabricated.
struct ReviewCaptureDetail: View {
    let capture: CaptureItem

    private var fullSummary: String? {
        guard let summary = capture.summary?.trimmingCharacters(in: .whitespacesAndNewlines),
              !summary.isEmpty else { return nil }
        return MemoryText.normalizedProse(summary)
    }

    private var memories: [MemoryItem] {
        capture.preview_memories ?? []
    }

    private var tasks: [TaskItem] {
        capture.preview_tasks ?? []
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let fullSummary {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Full summary".uppercased())
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.8)
                        .foregroundColor(CortexDesign.inkFaint)
                    Text(fullSummary)
                        .font(CortexDesign.Typography.prose(13.5))
                        .foregroundColor(CortexDesign.ink)
                        .lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            if !memories.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("All proposed memories".uppercased())
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.8)
                        .foregroundColor(CortexDesign.inkFaint)
                    ForEach(memories) { memory in
                        ReviewDetailBullet(text: memory.content, tint: CortexDesign.accent)
                    }
                }
            }

            if !tasks.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("All proposed tasks".uppercased())
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.8)
                        .foregroundColor(CortexDesign.inkFaint)
                    ForEach(tasks) { task in
                        ReviewDetailBullet(text: task.content, tint: CortexDesign.gold)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, CortexDesign.Space.md)
        .padding(.vertical, CortexDesign.Space.sm)
        .background(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .fill(CortexDesign.quietBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .stroke(CortexDesign.hairline, lineWidth: 1)
        )
        .accessibilityElement(children: .contain)
    }
}

/// One untruncated bullet in the review detail — full prose, no line cap, so a reviewer reads the
/// whole proposed memory before deciding.
struct ReviewDetailBullet: View {
    let text: String
    let tint: Color

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 9) {
            Image(systemName: "circle.fill")
                .font(.system(size: 6))
                .foregroundColor(tint)
            Text(MemoryText.normalizedProse(text))
                .font(CortexDesign.Typography.prose(13))
                .foregroundColor(CortexDesign.ink)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
    }
}

/// U-REV4: the inline edit-before-approve editor. A plain multi-line field seeded with the proposed
/// memory, a quiet Cancel, and a wax-red "Save & approve" that hands the corrected text back to the
/// card — the raw draft is archived so only the reviewer's version enters memory.
struct ReviewInlineEditor: View {
    @Binding var text: String
    let onCancel: () -> Void
    let onSave: () -> Void

    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Edit before approving".uppercased())
                .font(CortexDesign.Typography.stamp)
                .kerning(0.8)
                .foregroundColor(CortexDesign.inkFaint)

            TextEditor(text: $text)
                .font(CortexDesign.Typography.prose(13.5))
                .foregroundColor(CortexDesign.ink)
                .scrollContentBackground(.hidden)
                .frame(minHeight: 72, maxHeight: 160)
                .padding(8)
                .background(CortexDesign.panelBackground)
                .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                        .stroke(CortexDesign.hairline, lineWidth: 1)
                )
                .focused($focused)

            HStack(spacing: 10) {
                Spacer(minLength: 0)
                CortexButton(title: "Cancel", role: .ghost, size: .small) { onCancel() }
                CortexButton(title: "Save & approve", systemImage: "checkmark.seal", role: .primary, size: .small) {
                    onSave()
                }
                .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(.horizontal, CortexDesign.Space.md)
        .padding(.vertical, CortexDesign.Space.sm)
        .background(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .fill(CortexDesign.quietBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .stroke(CortexDesign.hairline, lineWidth: 1)
        )
        .onAppear { focused = true }
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
                Text("\(DistributionMode.appDisplayName) is preparing this item.")
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
                // U-REV2: open the underlying source (a note file or the captured URL) so a reviewer
                // can check provenance before approving. Only offered when there is a real, openable
                // destination — never a dead button on a capture with no linkable source.
                if let openURL = reviewOpenableSourceURL(capture) {
                    CortexIconButton(
                        systemImage: "arrow.up.right.square",
                        role: .ghost,
                        size: .small,
                        help: "Open the original source"
                    ) {
                        NSWorkspace.shared.open(openURL)
                    }
                }
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

/// U-REV2: the openable destination for a review card's source, or nil when there's nothing real to
/// open. Prefers an explicit `source_url` (a captured web page), then a path-like `source` string
/// (a note file on disk). Only http/https and local file paths are honored — never a fabricated or
/// non-openable scheme — so the "Open source" affordance is only shown when it actually resolves.
func reviewOpenableSourceURL(_ capture: CaptureItem) -> URL? {
    if let raw = capture.source_url?.trimmingCharacters(in: .whitespacesAndNewlines),
       !raw.isEmpty,
       let url = URL(string: raw),
       let scheme = url.scheme?.lowercased(),
       scheme == "http" || scheme == "https" || scheme == "file" {
        return url
    }
    let source = capture.source.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !source.isEmpty else { return nil }
    if MemoryText.isPathLike(source) {
        let expanded = (source as NSString).expandingTildeInPath
        if FileManager.default.fileExists(atPath: expanded) {
            return URL(fileURLWithPath: expanded)
        }
    }
    // A bare http(s) URL that landed in `source` rather than `source_url`.
    if let url = URL(string: source),
       let scheme = url.scheme?.lowercased(),
       scheme == "http" || scheme == "https" {
        return url
    }
    return nil
}

private func reviewShortDate(_ value: String) -> String {
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return "recently" }
    var parsed: Date?
    let iso = ISO8601DateFormatter()
    iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    parsed = iso.date(from: trimmed)
    if parsed == nil {
        iso.formatOptions = [.withInternetDateTime]
        parsed = iso.date(from: trimmed)
    }
    if parsed == nil {
        let bare = DateFormatter()
        bare.locale = Locale(identifier: "en_US_POSIX")
        bare.dateFormat = "yyyy-MM-dd"
        parsed = bare.date(from: String(trimmed.prefix(10)))
    }
    guard let date = parsed else { return "recently" }
    let out = DateFormatter()
    out.dateFormat = "MMM d"
    return out.string(from: date)
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

/// U-REV8: a triage rank for the "Low confidence first" sort — smaller sorts earlier. Low-confidence
/// captures rank first (they most need a human eye), then unknown/medium, then high-confidence (safest
/// to keep). Derived deterministically from the same preview-memory confidence the seal reads; a
/// capture with no confidence signal lands in the neutral middle rather than being treated as urgent.
func reviewConfidenceSortRank(_ capture: CaptureItem) -> Int {
    let confidences = (capture.preview_memories ?? []).compactMap { $0.confidence?.lowercased() }
    if confidences.contains("low") { return 0 }
    if confidences.isEmpty { return 1 }
    if confidences.contains("high") && !confidences.contains("medium") && !confidences.contains("low") {
        return 2
    }
    // Mixed or medium confidence sits between low and pure-high.
    return 1
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
