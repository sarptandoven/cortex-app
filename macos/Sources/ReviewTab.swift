import SwiftUI

struct ReviewTab: View {
    @ObservedObject var state: AppState
    @State private var isContextExpanded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                ReviewHeaderSection(state: state)
                ReviewInboxSection(state: state, captures: state.inbox)
                if let review = state.review {
                    if state.inbox.isEmpty && !hasReviewContext(review) {
                        QuietState(title: "Nothing to review", detail: "New imports, decisions, and open loops will appear here when Cortex finds them.")
                    } else if hasReviewContext(review) {
                        ReviewContextDisclosure(
                            review: review,
                            isExpanded: $isContextExpanded
                        )
                    }
                } else {
                    QuietState(title: "Review is loading", detail: "Cortex is checking pending memories, decisions, and open loops.")
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
                    Text("Review memory")
                        .font(.title3)
                        .fontWeight(.semibold)
                    Text("Approve useful signals and archive noise before agents rely on new memory.")
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
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
            if let review = state.review {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 104), spacing: 8)], spacing: 8) {
                    StatBox(label: "Pending", value: state.inbox.count)
                    StatBox(label: "Approved", value: review.stats.memories)
                    StatBox(label: "Decisions", value: review.recent_decisions.count)
                    StatBox(label: "Open", value: review.open_tasks.count)
                }
            }
        }
    }
}

struct ReviewInboxSection: View {
    @ObservedObject var state: AppState
    let captures: [CaptureItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Memory inbox")
                        .font(.headline)
                    Text(captures.isEmpty ? "Imported source records waiting for approval appear here." : "\(captures.count) item\(captures.count == 1 ? "" : "s") waiting for a decision.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
                if !captures.isEmpty {
                    Button {
                        Task {
                            await state.loadInbox()
                            await state.loadReview()
                        }
                    } label: {
                        Label("Refresh", systemImage: "arrow.clockwise")
                    }
                    .labelStyle(.iconOnly)
                    .help("Refresh memory inbox")
                }
            }

            if captures.isEmpty {
                QuietState(title: "No new signals", detail: "Import a source in Sources. Cortex will place reviewable memory here before it strengthens your model.")
            } else {
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(captures.prefix(10)) { capture in
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
}

struct ReviewCaptureCard: View {
    let capture: CaptureItem
    let approve: () -> Void
    let archive: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "tray.full")
                    .foregroundColor(.accentColor)
                    .frame(width: 22)
                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                        .font(.headline)
                        .lineLimit(2)
                    Text(sourceDetail)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer()
            }

            if let summary = capture.summary, !summary.isEmpty {
                Text(summary)
                    .font(.body)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("No summary available. Approving keeps derived memory from this source; archiving excludes it from the model.")
                    .font(.body)
                    .foregroundColor(.secondary)
            }

            HStack(spacing: 10) {
                ReviewCountPill(label: "Memories", value: capture.memory_count ?? 0, systemImage: "brain.head.profile")
                ReviewCountPill(label: "Tasks", value: capture.task_count ?? 0, systemImage: "circle.dashed")
                Spacer()
                Button {
                    archive()
                } label: {
                    Label("Archive Noise", systemImage: "archivebox")
                }
                Button {
                    approve()
                } label: {
                    Label("Approve Memory", systemImage: "checkmark.seal")
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
        return candidate.isEmpty ? "Untitled source record" : candidate
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
            HStack {
                Label("Decisions, open loops, and review guidance", systemImage: "list.bullet.rectangle")
                    .font(.headline)
                Spacer()
                Text("\(contextCount)")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }
}

struct ReviewGuidanceSection: View {
    let actions: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionHeader(title: "Review guidance", detail: "Suggested cleanup before memory is used in other apps.")
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
            SectionHeader(title: "Open loops", detail: "Unfinished work Cortex should keep visible, but not mix into the approval inbox.")
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
            SectionHeader(title: "Recent decisions", detail: "Approved decisions kept for context while reviewing new source data.")
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
