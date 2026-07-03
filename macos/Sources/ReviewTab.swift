import Foundation
import SwiftUI

struct ReviewTab: View {
    @ObservedObject var state: AppState
    @State private var initialLoadDone = false
    @State private var isReloading = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: CortexDesign.Space.lg) {
                ReviewHeaderSection(state: state)
                if !initialLoadDone && state.inbox.isEmpty {
                    ReviewLoadingCard()
                } else {
                    if shouldShowSourceHealth {
                        ReviewSourceHealthStrip(state: state)
                    }
                    ReviewInboxSection(state: state, captures: state.inbox)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(CortexDesign.Space.lg)
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
    }

    private var shouldShowSourceHealth: Bool {
        state.hasConnectedSourceAccount
            || state.hasConnectedObsidianVault
            || state.onboardingHasReviewedMemory
            || !state.inbox.isEmpty
    }
}

struct ReviewHeaderSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Review")
                        .font(.title2)
                        .fontWeight(.semibold)
                    Text("Approve what Cortex should remember. Archive anything noisy or unclear.")
                        .font(.body)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                if shouldShowPendingBadge {
                    ReviewPendingBadge(count: state.inbox.count)
                }
            }
        }
    }

    private var shouldShowPendingBadge: Bool {
        state.inbox.count > 0
            || state.hasConnectedSourceAccount
            || state.hasConnectedObsidianVault
            || state.onboardingHasReviewedMemory
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
            return "\(needsAttentionCount) source\(needsAttentionCount == 1 ? "" : "s") need attention. Already synced local memory stays available."
        }
        if pendingCount > 0 {
            return "Approve useful items, archive noise, and Cortex will use approved memory in Ask and MCP retrieval."
        }
        if !sources.isEmpty {
            return "Cortex will place new synced memories here before they are used."
        }
        return "Connect notes or a source to start building reviewed memory."
    }

    private var statusColor: Color {
        if needsAttentionCount > 0 { return .orange }
        if pendingCount > 0 { return .orange }
        if !sources.isEmpty { return .green }
        return .secondary
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
                ReviewHealthMetric(
                    title: "Queue",
                    value: pendingCount == 0 ? "Clear" : "\(pendingCount) pending",
                    systemImage: "tray.full"
                )
                ReviewHealthMetric(
                    title: "Freshness",
                    value: freshnessLabel,
                    systemImage: "clock.arrow.circlepath"
                )
                ReviewHealthMetric(
                    title: "Health",
                    value: sourceHealthLabel,
                    systemImage: "waveform.path.ecg"
                )
            }

            if !sources.isEmpty {
                HStack(spacing: 6) {
                    ForEach(Array(sources.prefix(3))) { source in
                        ReviewSourceHealthChip(source: source)
                    }
                    if sources.count > 3 {
                        Text("+\(sources.count - 3) more")
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
            return sources.isEmpty ? "No source" : "Waiting"
        }
        return "Synced \(reviewShortDate(latestSync))"
    }

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

struct ReviewHealthMetric: View {
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

struct ReviewSourceHealthChip: View {
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
        if source.pending > 0 {
            return "\(source.name) · \(source.pending) pending"
        }
        if let lastSeen = source.sync_plan?.last_completed_at ?? source.last_seen_at {
            return "\(source.name) · \(reviewShortDate(lastSeen))"
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

struct ReviewPendingBadge: View {
    let count: Int

    var body: some View {
        VStack(alignment: .trailing, spacing: 2) {
            Text("\(count)")
                .font(.title2)
                .fontWeight(.semibold)
            Text("Pending")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct ReviewInboxSection: View {
    @ObservedObject var state: AppState
    let captures: [CaptureItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .center, spacing: 14) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Pending items")
                        .font(.title3)
                        .fontWeight(.semibold)
                    Text(queueDetail)
                        .font(.callout)
                        .foregroundColor(.secondary)
                }
                Spacer()
            }

            if captures.isEmpty {
                if !state.isLocalServiceReady {
                    ReviewServiceStartingState(state: state)
                } else {
                    ReviewEmptyState(state: state, detail: emptyDetail)
                }
            } else {
                LazyVStack(alignment: .leading, spacing: 14) {
                    ForEach(visibleCaptures) { capture in
                        ReviewQueueCaptureCard(
                            capture: capture,
                            isInFlight: state.inFlightCaptureIds.contains(capture.id),
                            actionError: state.captureActionErrors[capture.id],
                            approve: { state.approveCapture(capture) },
                            archive: { state.archiveCapture(capture) }
                        )
                    }
                }
            }
        }
    }

    private var visibleCaptures: [CaptureItem] {
        Array(captures.prefix(10))
    }

    private var visibleCount: Int {
        visibleCaptures.count
    }

    private var queueDetail: String {
        if captures.isEmpty {
            return "Nothing waiting for review right now."
        }
        if captures.count > visibleCount {
            return "Showing \(visibleCount) of \(captures.count) waiting for review."
        }
        return "\(captures.count) item\(captures.count == 1 ? "" : "s") waiting for review."
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
            QuietState(title: emptyTitle, detail: detail)

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

                if state.hasConnectedSourceAccount || state.hasConnectedObsidianVault || approvedMemoryCount > 0 {
                    Button {
                        Task {
                            await state.loadSourceConnectivity()
                            await state.loadInbox()
                            await state.loadReview()
                            await state.loadStats()
                        }
                    } label: {
                        Label("Refresh", systemImage: "arrow.clockwise")
                            .frame(minWidth: 112, minHeight: 46)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)
                    .disabled(state.isBusy)
                }
            }
            .frame(maxWidth: .infinity, alignment: .center)
        }
    }
}

struct ReviewLoadingCard: View {
    var body: some View {
        HStack(spacing: 12) {
            ProgressView()
                .controlSize(.small)
            Text("Loading review queue…")
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
                        .foregroundColor(.orange)
                } else {
                    ProgressView()
                        .controlSize(.small)
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(.headline)
                    Text(detail)
                        .font(.body)
                        .foregroundColor(.secondary)
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

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top, spacing: 12) {
                    Text(title)
                        .font(.title3)
                        .fontWeight(.semibold)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 12)
                    Text(reviewSizeLabel)
                        .font(.callout)
                        .foregroundColor(.secondary)
                }

                if let summary = cleanedSummary {
                    Text(summary)
                        .font(.body)
                        .lineSpacing(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            ReviewQueuePreviewList(capture: capture)

            Divider()

            ReviewQueueSourceBox(capture: capture)

            if let actionError, !actionError.isEmpty {
                Label(actionError, systemImage: "exclamationmark.triangle.fill")
                    .font(.callout)
                    .foregroundColor(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack(alignment: .center, spacing: 12) {
                if isInFlight {
                    ProgressView()
                        .controlSize(.small)
                }
                Spacer()
                Button {
                    archive()
                } label: {
                    Label("Archive", systemImage: "archivebox")
                        .frame(minWidth: 132, minHeight: 48)
                }
                .controlSize(.large)
                .buttonStyle(.bordered)
                .disabled(isInFlight)
                Button {
                    approve()
                } label: {
                    Label("Approve", systemImage: "checkmark.seal")
                        .frame(minWidth: 150, minHeight: 48)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(isInFlight)
            }
        }
        .cortexCard(padding: CortexDesign.Space.lg, background: CortexDesign.panelBackground)
    }

    private var title: String {
        let candidate = capture.title ?? capture.source
        return candidate.isEmpty ? "Untitled review item" : candidate
    }

    private var cleanedSummary: String? {
        guard let summary = capture.summary?.trimmingCharacters(in: .whitespacesAndNewlines),
              !summary.isEmpty else {
            return nil
        }
        return summary
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
        Array((capture.preview_memories ?? []).prefix(3))
    }

    private var tasks: [TaskItem] {
        Array((capture.preview_tasks ?? []).prefix(2))
    }

    var body: some View {
        if memories.isEmpty && tasks.isEmpty {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: "hourglass")
                    .foregroundColor(.secondary)
                Text("Cortex is preparing this item.")
            }
            .font(.callout)
            .foregroundColor(.secondary)
        } else {
            VStack(alignment: .leading, spacing: 10) {
                Text("Will remember")
                    .font(.callout)
                    .fontWeight(.semibold)
                    .foregroundColor(.secondary)

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
                .foregroundColor(.accentColor)
            Text(text)
                .font(.body)
                .lineSpacing(2)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
    }
}

struct ReviewQueueSourceBox: View {
    let capture: CaptureItem

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: "doc.text.magnifyingglass")
                    .foregroundColor(.secondary)
                Text("From \(sourceName)")
                    .font(.callout)
                    .fontWeight(.semibold)
                Spacer(minLength: 0)
            }

            if let capturedDate = capturedDate {
                Text("Added \(capturedDate)")
                    .font(.callout)
                    .foregroundColor(.secondary)
            }

            if let citation = citation {
                Label(citation, systemImage: "link")
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(capture.source_url ?? citation)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var sourceName: String {
        let trimmed = capture.source.trimmingCharacters(in: .whitespacesAndNewlines)
        if let citation = CitationDisplay.cleanSourceURL(trimmed) {
            return citation
        }
        return trimmed.isEmpty ? "source" : trimmed
    }

    private var capturedDate: String? {
        guard let capturedAt = capture.captured_at?.trimmingCharacters(in: .whitespacesAndNewlines),
              !capturedAt.isEmpty else {
            return nil
        }
        return String(capturedAt.prefix(10))
    }

    private var citation: String? {
        CitationDisplay.label(sourceURL: capture.source_url)
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
                        .font(.headline)
                        .lineLimit(2)
                }
                Spacer()
                ReviewCountPill(label: "Memories", value: capture.memory_count ?? 0, systemImage: "brain.head.profile")
                ReviewCountPill(label: "Tasks", value: capture.task_count ?? 0, systemImage: "circle.dashed")
            }

            if let summary = capture.summary, !summary.isEmpty {
                Text(summary)
                    .font(.body)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("No summary yet.")
                    .font(.body)
                    .foregroundColor(.secondary)
            }

            ReviewPreviewList(capture: capture)

            HStack(alignment: .center, spacing: 10) {
                Label(sourceDetail, systemImage: capture.source_url?.isEmpty == false ? "quote.bubble" : "link")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(sourceDetail)
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
        .padding(12)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.softBorder))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var title: String {
        let candidate = capture.title ?? capture.source
        return candidate.isEmpty ? "Untitled review item" : candidate
    }

    private var sourceDetail: String {
        var parts = [capture.source]
        if let date = capture.captured_at {
            parts.append(String(date.prefix(10)))
        }
        if let citation = CitationDisplay.label(sourceURL: capture.source_url) {
            parts.append(citation)
        }
        return parts.joined(separator: " · ")
    }
}

struct ReviewPreviewList: View {
    let capture: CaptureItem

    private var memories: [MemoryItem] {
        Array((capture.preview_memories ?? []).prefix(5))
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
            .font(.caption)
            .foregroundColor(.secondary)
        } else {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    Image(systemName: "brain.head.profile")
                    Text("What Cortex will remember")
                        .fontWeight(.semibold)
                    Spacer()
                }
                .font(.caption)
                .foregroundColor(.secondary)

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
            ReviewPreviewKindPill(label: memoryLabel, color: color(for: memory.kind))
            VStack(alignment: .leading, spacing: 3) {
                Text(memory.content)
                    .font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
                if let citation = CitationDisplay.label(sourceURL: memory.source_url) {
                    Label(citation, systemImage: "quote.bubble")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .help(memory.source_url ?? citation)
                }
            }
            Spacer(minLength: 0)
        }
    }

    private var memoryLabel: String {
        if let layer = memory.layer, !layer.isEmpty, layer != memory.kind {
            return "\(memory.kind) · \(layer)"
        }
        return memory.kind
    }

    private func color(for kind: String) -> Color {
        switch kind {
        case "decision": return .red
        case "preference": return .purple
        case "style": return .teal
        case "negative": return .orange
        case "procedure": return .indigo
        case "action": return .green
        default: return .accentColor
        }
    }
}

struct ReviewTaskPreviewRow: View {
    let task: TaskItem

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            ReviewPreviewKindPill(label: "task", color: .green)
            Text(task.content)
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
    }
}

struct ReviewPreviewKindPill: View {
    let label: String
    let color: Color

    var body: some View {
        Text(label.uppercased())
            .font(.caption2)
            .fontWeight(.semibold)
            .foregroundColor(color)
            .lineLimit(1)
            .truncationMode(.tail)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(color.opacity(0.10))
            .clipShape(RoundedRectangle(cornerRadius: 6))
            .frame(width: 92, alignment: .leading)
    }
}

struct ReviewCountPill: View {
    let label: String
    let value: Int
    let systemImage: String

    var body: some View {
        Label("\(value) \(label.lowercased())", systemImage: systemImage)
            .font(.caption)
            .foregroundColor(.secondary)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(CortexDesign.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}
