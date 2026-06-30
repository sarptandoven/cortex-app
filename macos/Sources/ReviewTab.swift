import SwiftUI

struct ReviewTab: View {
    @ObservedObject var state: AppState
    @State private var isContextExpanded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                ReviewHeaderSection(state: state)
                ReviewInboxSection(state: state, captures: state.inbox)
                if let review = state.review {
                    if hasReviewContext(review) {
                        ReviewContextDisclosure(
                            review: review,
                            isExpanded: $isContextExpanded
                        )
                    }
                }
            }
            .padding(16)
        }
        .task {
            await state.loadInbox()
            await state.loadReview()
            await state.loadProductLoop()
        }
    }

    private func hasReviewContext(_ review: DailyReviewResponse) -> Bool {
        !review.recommended_actions.isEmpty || !review.open_tasks.isEmpty || !review.recent_decisions.isEmpty
    }
}

struct ReviewHeaderSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Review queue")
                        .font(.title3)
                        .fontWeight(.semibold)
                    Text("Approve what Cortex should remember, and archive noise. Ask and connected AI tools can cite only approved items.")
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                ReviewPendingBadge(count: state.inbox.count)
                Button {
                    Task {
                        await state.loadInbox()
                        await state.loadReview()
                        await state.loadProductLoop()
                    }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
        }
    }
}

struct ReviewPendingBadge: View {
    let count: Int

    var body: some View {
        VStack(alignment: .trailing, spacing: 1) {
            Text("\(count)")
                .font(.title3)
                .fontWeight(.semibold)
            Text("Pending")
                .font(.caption2)
                .foregroundColor(.secondary)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct ReviewInboxSection: View {
    @ObservedObject var state: AppState
    let captures: [CaptureItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Pending items")
                        .font(.headline)
                    Text(queueDetail)
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
                if !captures.isEmpty {
                    Button {
                        state.archiveCaptures(visibleCaptures)
                    } label: {
                        Label("Archive \(visibleCount)", systemImage: "archivebox")
                    }
                    Button {
                        state.approveCaptures(visibleCaptures)
                    } label: {
                        Label("Approve \(visibleCount)", systemImage: "checkmark.seal")
                    }
                    .buttonStyle(.borderedProminent)
                }
            }

            if captures.isEmpty {
                QuietState(title: "Nothing to review", detail: emptyDetail)
            } else {
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(visibleCaptures) { capture in
                        ReviewCaptureCard(
                            capture: capture,
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
            return "\(captures.count) pending. Showing the first \(visibleCount)."
        }
        return "\(captures.count) pending item\(captures.count == 1 ? "" : "s")."
    }

    private var emptyDetail: String {
        if (state.review?.stats.memories ?? 0) == 0 {
            return "Connect a source first. Useful memory lands here before Cortex can use it."
        }
        return "All caught up. New source records land here before Cortex can use them."
    }
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
        .background(Color(nsColor: .controlBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
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
        if let url = capture.source_url, !url.isEmpty {
            parts.append(url)
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
                if let sourceURL = memory.source_url, !sourceURL.isEmpty {
                    Label(sourceURL, systemImage: "quote.bubble")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .help(sourceURL)
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
            .background(Color(nsColor: .textBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct ReviewContextDisclosure: View {
    let review: DailyReviewResponse
    @Binding var isExpanded: Bool

    private var contextCount: Int {
        review.recommended_actions.count + review.open_tasks.count + review.recent_decisions.count
    }

    var body: some View {
        DisclosureGroup(isExpanded: $isExpanded) {
            VStack(alignment: .leading, spacing: 14) {
                if !review.recommended_actions.isEmpty {
                    ReviewGuidanceSection(actions: review.recommended_actions)
                }
                if !review.open_tasks.isEmpty {
                    ReviewOpenLoopsSection(tasks: review.open_tasks)
                }
                if !review.recent_decisions.isEmpty {
                    ReviewDecisionSection(decisions: review.recent_decisions)
                }
            }
            .padding(.top, 8)
        } label: {
            HStack(alignment: .firstTextBaseline) {
                Label("Approved memory", systemImage: "sidebar.right")
                    .font(.subheadline)
                Spacer()
                Text(contextSummary)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }

    private var contextSummary: String {
        var parts: [String] = []
        if !review.recent_decisions.isEmpty {
            parts.append("\(review.recent_decisions.count) decisions")
        }
        if !review.open_tasks.isEmpty {
            parts.append("\(review.open_tasks.count) follow-ups")
        }
        if !review.recommended_actions.isEmpty {
            parts.append("\(review.recommended_actions.count) guidance")
        }
        return parts.isEmpty ? "\(contextCount)" : parts.joined(separator: " · ")
    }
}

struct ReviewGuidanceSection: View {
    let actions: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionHeader(title: "Review guidance", detail: "Suggested cleanup before approved memory is used elsewhere.")
            ForEach(actions.prefix(3), id: \.self) { action in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: "checkmark.circle")
                        .foregroundColor(.accentColor)
                    Text(action)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .font(.body)
            }
        }
    }
}

struct ReviewOpenLoopsSection: View {
    let tasks: [TaskItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionHeader(title: "Follow-ups", detail: "Unfinished work Cortex keeps visible outside the approval queue.")
            ForEach(tasks.prefix(4)) { task in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(task.kind.uppercased())
                        .font(.caption2)
                        .fontWeight(.semibold)
                        .foregroundColor(.orange)
                        .frame(width: 62, alignment: .leading)
                    Text(task.content)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
                .padding(8)
                .background(Color(nsColor: .controlBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
    }
}

struct ReviewDecisionSection: View {
    let decisions: [MemoryItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionHeader(title: "Recent decisions", detail: "Approved decisions available as context while reviewing new source memory.")
            ForEach(decisions.prefix(3)) { decision in
                Text(decision.content)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(nsColor: .controlBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
    }
}
