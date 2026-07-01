import Foundation
import SwiftUI

struct ReviewTab: View {
    @ObservedObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                ReviewHeaderSection(state: state)
                ReviewInboxSection(state: state, captures: state.inbox)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(20)
        }
        .task {
            await state.loadInbox()
            await state.loadReview()
            await state.loadProductLoop()
        }
        .background(CortexDesign.appBackground)
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
                ReviewPendingBadge(count: state.inbox.count)
            }
        }
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
                QuietState(title: "Nothing to review", detail: emptyDetail)
            } else {
                LazyVStack(alignment: .leading, spacing: 14) {
                    ForEach(visibleCaptures) { capture in
                        ReviewQueueCaptureCard(
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
            return "Showing \(visibleCount) of \(captures.count) waiting for review."
        }
        return "\(captures.count) item\(captures.count == 1 ? "" : "s") waiting for review."
    }

    private var emptyDetail: String {
        if (state.review?.stats.memories ?? 0) == 0 {
            return "Connect notes first. New memories will appear here before Cortex uses them."
        }
        return "All caught up. New synced items will appear here before Cortex uses them."
    }
}

struct ReviewQueueCaptureCard: View {
    let capture: CaptureItem
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

            ReviewQueueSourceBox(capture: capture)

            HStack(alignment: .center, spacing: 12) {
                Spacer()
                Button {
                    archive()
                } label: {
                    Label("Archive", systemImage: "archivebox")
                }
                .controlSize(.large)
                Button {
                    approve()
                } label: {
                    Label("Approve", systemImage: "checkmark.seal")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.softBorder))
        .clipShape(RoundedRectangle(cornerRadius: 8))
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
                Text("Cortex would remember")
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
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(CortexDesign.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: 8))
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
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(CortexDesign.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
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
