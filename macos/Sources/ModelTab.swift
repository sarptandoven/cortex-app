import SwiftUI

struct ModelTab: View {
    @ObservedObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HomeHeroSection(state: state, review: state.review)
            }
            .padding(16)
        }
        .background(CortexDesign.appBackground)
    }
}

struct HomeHeroSection: View {
    @ObservedObject var state: AppState
    let review: DailyReviewResponse?

    private var activeSources: Int {
        if let connected = state.sourceReadinessReport?.summary.connected {
            return connected
        }
        return state.activeSourceAccounts.filter { account in
            account.status.lowercased() != "empty" && account.auth_state.lowercased() != "needs-content"
        }.count
    }

    private var hasEmptySource: Bool {
        if let report = state.sourceReadinessReport {
            return report.sources.contains { $0.status == "empty" }
        }
        return state.activeSourceAccounts.contains { account in
            account.status.lowercased() == "empty" || account.auth_state.lowercased() == "needs-content"
        }
    }

    private var memoryCount: Int {
        review?.stats.memories ?? state.stats?.memories ?? 0
    }

    private var pendingCount: Int {
        review?.stats.pending_captures ?? state.inbox.count
    }

    private var hasMemory: Bool {
        memoryCount > 0
    }

    private var statusSummary: (label: String, systemImage: String, color: Color) {
        if !state.isLocalServiceReady {
            if CortexRecoveryText.needsAttention(state.displayStatus) {
                return ("Needs attention", "exclamationmark.triangle.fill", .orange)
            }
            return ("Starting", "power", .accentColor)
        }
        if pendingCount > 0 {
            return ("Ready for Review", "tray.full.fill", .orange)
        }
        if hasMemory {
            return ("Ready to ask", "checkmark.seal.fill", .green)
        }
        if activeSources > 0 {
            return ("Syncing notes", "arrow.triangle.2.circlepath", .accentColor)
        }
        if hasEmptySource {
            return ("No notes found", "folder.badge.questionmark", .orange)
        }
        if state.connectedAIIntegrationCount > 0 {
            return ("Waiting for notes", "folder.badge.plus", .accentColor)
        }
        return ("Private on this Mac", "lock.shield", .secondary)
    }

    private var title: String {
        if !state.isLocalServiceReady {
            return "Cortex is starting"
        }
        if pendingCount > 0 {
            return "Review new memory"
        }
        if hasMemory {
            return "Ask about your notes"
        }
        if activeSources > 0 {
            return "Your notes are syncing"
        }
        if hasEmptySource {
            return "Choose notes with content"
        }
        if state.connectedAIIntegrationCount > 0 {
            return "Connect notes to continue"
        }
        return "Connect your notes"
    }

    private var detail: String {
        if !state.isLocalServiceReady {
            if CortexRecoveryText.needsAttention(state.displayStatus) {
                return state.displayStatus
            }
            return "This usually takes a moment."
        }
        if pendingCount > 0 {
            return "Choose what Cortex should remember before it appears in Ask."
        }
        if hasMemory {
            return "Cortex answers from saved memory and shows which notes each answer came from."
        }
        if activeSources > 0 {
            return "New items will appear in Review when sync finishes."
        }
        if hasEmptySource {
            return "Cortex could not find usable notes there. Pick a folder or app that has real notes."
        }
        if state.connectedAIIntegrationCount > 0 {
            return "Your AI tool is connected. Add notes so Ask can answer with sources."
        }
        return "Choose notes once. Cortex keeps them in sync and brings new memory to Review."
    }

    private var actionTitle: String {
        if !state.isLocalServiceReady { return "Start Cortex" }
        if activeSources == 0 { return hasEmptySource ? "Choose notes" : "Connect notes" }
        if pendingCount > 0 { return "Review memory" }
        if hasMemory { return "Ask a question" }
        return "Check notes"
    }

    private var actionDetail: String {
        if !state.isLocalServiceReady { return "Start Cortex on this Mac." }
        if activeSources == 0 {
            if hasEmptySource {
                return "Pick a different notes folder or app."
            }
            if state.connectedAIIntegrationCount > 0 {
                return "Notes give Ask something to cite."
            }
            return "Cortex syncs automatically after notes connect."
        }
        if pendingCount > 0 {
            return "\(pendingCount) item\(pendingCount == 1 ? "" : "s") waiting"
        }
        if hasMemory {
            return "\(memoryCount) saved memor\(memoryCount == 1 ? "y" : "ies") ready"
        }
        return "Confirm notes are syncing."
    }

    private var actionIcon: String {
        if !state.isLocalServiceReady { return "power" }
        if activeSources == 0 { return hasEmptySource ? "folder.badge.questionmark" : "folder.badge.plus" }
        if pendingCount > 0 { return "checklist" }
        if hasMemory { return "magnifyingglass" }
        return "arrow.clockwise"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Label(statusSummary.label, systemImage: statusSummary.systemImage)
                .font(.callout)
                .fontWeight(.semibold)
                .foregroundColor(statusSummary.color)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(statusSummary.color.opacity(0.12))
                .clipShape(Capsule())

            VStack(alignment: .leading, spacing: 8) {
                Text(title)
                    .font(.system(size: 34, weight: .semibold))
                Text(detail)
                    .font(.title3)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 680, alignment: .leading)
            }

            HStack(alignment: .center, spacing: 12) {
                Button {
                    runNextAction()
                } label: {
                    Label(actionTitle, systemImage: actionIcon)
                        .frame(minWidth: 150, minHeight: 48)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(state.isBusy)

                Text(actionDetail)
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                Spacer(minLength: 0)
            }

            VStack(alignment: .leading, spacing: 8) {
                HomeStatusRow(
                    title: "Notes",
                    detail: sourceStatus.detail,
                    systemImage: sourceStatus.systemImage,
                    color: sourceStatus.color
                )
                HomeStatusRow(
                    title: "Memory",
                    detail: memoryStatus.detail,
                    systemImage: memoryStatus.systemImage,
                    color: memoryStatus.color
                )
            }
            .frame(maxWidth: 620, alignment: .leading)
        }
        .padding(.vertical, 30)
        .padding(.horizontal, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var sourceStatus: (detail: String, systemImage: String, color: Color) {
        if activeSources > 0 {
            return ("\(activeSources) connected and syncing", "folder.fill.badge.checkmark", .green)
        }
        if hasEmptySource {
            return ("No usable notes found", "folder.badge.questionmark", .orange)
        }
        return ("Not connected yet", "folder.badge.plus", .accentColor)
    }

    private var memoryStatus: (detail: String, systemImage: String, color: Color) {
        if pendingCount > 0 {
            return ("\(pendingCount) waiting for Review", "tray.full.fill", .orange)
        }
        if memoryCount > 0 {
            return ("\(memoryCount) saved for Ask", "brain.head.profile", .green)
        }
        return ("No saved memory yet", "checklist", .secondary)
    }

    private func runNextAction() {
        if !state.isLocalServiceReady {
            Task {
                await state.ensureBackend()
                await state.loadDiagnostics()
                await state.loadReview()
                await state.loadStats()
            }
        } else if activeSources == 0 {
            state.openConnectionsPrivacy(statusMessage: "Connections")
        } else if pendingCount > 0 {
            state.selectedTab = .review
            state.status = "Review memory"
        } else if hasMemory {
            state.selectedTab = .ask
            state.status = "Ask Cortex"
        } else {
            state.openConnectionsPrivacy(statusMessage: "Check notes")
        }
    }
}

struct HomeStatusRow: View {
    let title: String
    let detail: String
    let systemImage: String
    let color: Color

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: systemImage)
                .font(.title3)
                .foregroundColor(color)
                .frame(width: 28, height: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.callout)
                    .fontWeight(.semibold)
                Text(detail)
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 6)
        .frame(minHeight: 44, alignment: .leading)
    }
}

struct ModelMetricPill: View {
    let label: String
    let value: String
    let systemImage: String

    var body: some View {
        HStack(spacing: 7) {
            Image(systemName: systemImage)
                .foregroundColor(.accentColor)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(value)
                    .font(.headline)
                    .fontWeight(.semibold)
                Text(label)
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(CortexDesign.cardBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct ModelQualitySection: View {
    let quality: MemoryQualityResponse

    private var color: Color {
        if quality.score >= 80 { return .green }
        if quality.score >= 55 { return .orange }
        return .red
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title: "Memory quality", detail: "How much approved memory has citations, dates, review state, and useful structure.")
            HStack(alignment: .top, spacing: 12) {
                ZStack {
                    Circle()
                        .stroke(color.opacity(0.18), lineWidth: 8)
                    Circle()
                        .trim(from: 0, to: CGFloat(quality.score) / 100)
                        .stroke(color, style: StrokeStyle(lineWidth: 8, lineCap: .round))
                        .rotationEffect(.degrees(-90))
                    Text("\(quality.score)")
                        .font(.headline)
                        .fontWeight(.semibold)
                }
                .frame(width: 58, height: 58)

                VStack(alignment: .leading, spacing: 8) {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 116), spacing: 8)], spacing: 8) {
                        ModelMetricPill(label: "Cited", value: percent(quality.citation_coverage), systemImage: "quote.bubble")
                        ModelMetricPill(label: "Dated", value: percent(quality.date_coverage ?? 0), systemImage: "calendar")
                        ModelMetricPill(label: "Reviewed", value: percent(quality.review_coverage), systemImage: "checkmark.seal")
                        ModelMetricPill(label: "Layers", value: percent(quality.layer_coverage), systemImage: "square.stack.3d.up")
                    }
                    if let warning = quality.warnings.first {
                        TrustNotice(systemImage: "exclamationmark.triangle.fill", title: "Needs attention", detail: warning, color: .orange)
                    } else {
                        TrustNotice(systemImage: "checkmark.seal.fill", title: quality.status.replacingOccurrences(of: "_", with: " ").capitalized, detail: "Approved memory has usable citations, review state, and coverage.", color: .green)
                    }
                }
            }
        }
    }

    private func percent(_ value: Double) -> String {
        "\(Int((value * 100).rounded()))%"
    }
}

struct ModelCoverageSection: View {
    let review: DailyReviewResponse

    private let layers: [(String, String, String)] = [
        ("Facts", "semantic", "text.book.closed"),
        ("Events", "episodic", "calendar"),
        ("Style", "style", "signature"),
        ("Decisions", "decision", "checkmark.seal"),
        ("Preferences", "preference", "slider.horizontal.3"),
        ("Rejections", "negative", "hand.raised")
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title: "Memory coverage", detail: "Types of approved memory available to Ask and connected AI tools.")
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 128), spacing: 8)], spacing: 8) {
                ForEach(layers, id: \.1) { layer in
                    LayerCoverageTile(title: layer.0, layer: layer.1, systemImage: layer.2, count: count(for: layer.1))
                }
            }
        }
    }

    private func count(for layer: String) -> Int {
        if layer == "decision" {
            return max(review.recent_decisions.count, review.stats.decisions)
        }
        let durableLayerCount = review.stats.by_layer?.first(where: { $0.layer == layer })?.count ?? 0
        if durableLayerCount > 0 {
            return durableLayerCount
        }
        let recentLayerCount = review.recent_memories.filter { $0.layer == layer }.count
        if recentLayerCount > 0 {
            return recentLayerCount
        }
        switch layer {
        case "semantic":
            return review.stats.memories
        case "episodic":
            return review.stats.by_kind.first(where: { $0.kind == "event" })?.count ?? 0
        case "style":
            return review.stats.by_kind.first(where: { $0.kind == "style" })?.count ?? 0
        case "preference":
            return review.stats.by_kind.first(where: { $0.kind == "preference" })?.count ?? 0
        case "negative":
            return review.stats.by_kind.first(where: { $0.kind == "negative" })?.count ?? 0
        default:
            return 0
        }
    }
}

struct ModelSourceCoverageSection: View {
    @ObservedObject var state: AppState
    let review: DailyReviewResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title: "Connected memory", detail: "Where approved memory is coming from.")
            if let summary = state.trustSummary, !summary.source_counts.isEmpty {
                HStack(spacing: 8) {
                    ForEach(summary.source_counts.prefix(4)) { source in
                        SourceCoveragePill(source: source)
                    }
                    Spacer(minLength: 0)
                }
            } else if review.stats.memories == 0 {
                QuietState(title: "No approved memory yet", detail: "Connect notes, then approve useful memory in Review before expecting Ask to answer.")
            } else {
                HStack(spacing: 8) {
                    ModelMetricPill(label: "Topics", value: "\(review.top_topics.count)", systemImage: "number")
                    ModelMetricPill(label: "People & projects", value: "\(review.top_entities.count)", systemImage: "person.2")
                    Spacer(minLength: 0)
                }
            }
        }
    }
}

struct ModelSignalSummarySection: View {
    let review: DailyReviewResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title: "Current snapshot", detail: "Approved memory, pending review, topics, people, and decisions.")
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 130), spacing: 8)], spacing: 8) {
                ModelMetricPill(label: "Memories", value: "\(review.stats.memories)", systemImage: "brain.head.profile")
                ModelMetricPill(label: "Pending review", value: "\(review.stats.pending_captures)", systemImage: "tray.full")
                ModelMetricPill(label: "Topics", value: "\(review.top_topics.count)", systemImage: "number")
                ModelMetricPill(label: "People & projects", value: "\(review.top_entities.count)", systemImage: "person.2")
                ModelMetricPill(label: "Open tasks", value: "\(review.open_tasks.count)", systemImage: "circle.dashed")
                ModelMetricPill(label: "Decisions", value: "\(review.recent_decisions.count)", systemImage: "checkmark.seal")
            }
            if review.top_topics.isEmpty && review.top_entities.isEmpty {
                QuietState(title: "Needs more approved memory", detail: "Approve more memory or connect richer notes to improve people, project, topic, and style coverage.")
            } else {
                ModelTopicSection(topics: review.top_topics, entities: review.top_entities)
            }
        }
    }
}

struct ModelDisclosureLabel: View {
    let systemImage: String
    let title: String
    let detail: String

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: systemImage)
                .foregroundColor(.accentColor)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.headline)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

struct SectionHeader: View {
    let title: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.headline)
            Text(detail)
                .font(.caption)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct LayerCoverageTile: View {
    let title: String
    let layer: String
    let systemImage: String
    let count: Int

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: systemImage)
                .foregroundColor(count > 0 ? .accentColor : .secondary)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(.callout)
                    .fontWeight(.medium)
                Text(count > 0 ? "\(count) memories" : "Needs data")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(10)
        .background(CortexDesign.cardBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct SourceCoveragePill: View {
    let source: SourceTrustSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(source.source)
                .font(.caption)
                .fontWeight(.medium)
                .lineLimit(1)
            Text("\(source.approved) approved · \(source.pending) review")
                .font(.caption2)
                .foregroundColor(.secondary)
                .lineLimit(1)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(minWidth: 112, alignment: .leading)
        .background(CortexDesign.cardBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct ModelTopicSection: View {
    let topics: [TopicSummary]
    let entities: [EntitySummary]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Active memory")
                .font(.headline)
            if topics.isEmpty && entities.isEmpty {
                Text("Topics and entities appear after you save more memory.")
                    .foregroundColor(.secondary)
            } else {
                if !topics.isEmpty {
                    Text(topics.prefix(8).map { "#\($0.topic)" }.joined(separator: " "))
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !entities.isEmpty {
                    Text(entities.prefix(6).map { "\($0.name) (\($0.kind))" }.joined(separator: " · "))
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}
