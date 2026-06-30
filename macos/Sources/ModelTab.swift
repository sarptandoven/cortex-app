import SwiftUI

struct ModelTab: View {
    @ObservedObject var state: AppState
    @State private var modelDetailsExpanded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HomeHeroSection(state: state, review: state.review)
                HomeActionSection(state: state, review: state.review)

                if let review = state.review, review.stats.memories > 0 || !review.pending.isEmpty {
                    DisclosureGroup(isExpanded: $modelDetailsExpanded) {
                        VStack(alignment: .leading, spacing: 14) {
                            if let quality = state.memoryQuality {
                                ModelQualitySection(quality: quality)
                            }
                            ModelCoverageSection(review: review)
                            ModelSourceCoverageSection(state: state, review: review)
                            ModelSignalSummarySection(review: review)
                        }
                        .padding(.top, 8)
                    } label: {
                        ModelDisclosureLabel(
                            systemImage: "square.stack.3d.up",
                            title: "Memory details",
                            detail: "Coverage, citations, topics, people, and quality"
                        )
                    }
                    .padding(12)
                    .background(Color(nsColor: .controlBackgroundColor).opacity(0.65))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                } else {
                    HomeFirstRunNotes(state: state)
                }
            }
            .padding(16)
        }
    }

    private var loadingDetail: String {
        CortexRecoveryText.needsAttention(state.displayStatus)
            ? state.displayStatus
            : "Cortex is starting the local memory engine on this Mac."
    }
}

struct HomeHeroSection: View {
    @ObservedObject var state: AppState
    let review: DailyReviewResponse?

    private var activeSources: Int {
        state.activeSourceAccounts.count
    }

    private var memoryCount: Int {
        review?.stats.memories ?? state.stats?.memories ?? 0
    }

    private var pendingCount: Int {
        review?.stats.pending_captures ?? state.inbox.count
    }

    private var title: String {
        if memoryCount > 0 {
            return "Cortex is ready"
        }
        if pendingCount > 0 {
            return "Review new memory"
        }
        if activeSources > 0 {
            return "Cortex is syncing"
        }
        if state.connectedAIIntegrationCount > 0 {
            return "Connect a memory source"
        }
        return "Cortex is running locally"
    }

    private var detail: String {
        if pendingCount > 0 {
            return "New memories are waiting for review before they shape answers."
        }
        if memoryCount > 0 {
            return "Ask questions, inspect citations, and let connected AI tools use approved memory."
        }
        if activeSources > 0 {
            return "Cortex is syncing connected notes. Useful memory will appear in Review."
        }
        if state.connectedAIIntegrationCount > 0 {
            return "Your AI tool is connected. Add Obsidian or local notes so Cortex has memory to use."
        }
        return "Connect notes once. Cortex syncs quietly, sends useful memory to Review, then answers with citations."
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .top, spacing: 14) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color.accentColor.opacity(0.14))
                    Image(systemName: "circle.grid.cross.fill")
                        .font(.system(size: 30, weight: .semibold))
                        .foregroundColor(.accentColor)
                }
                .frame(width: 64, height: 64)

                VStack(alignment: .leading, spacing: 7) {
                    Text(title)
                        .font(.system(size: 28, weight: .semibold))
                    Text(detail)
                        .font(.title3)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }

            VStack(spacing: 10) {
                HomeStatusRow(
                    title: activeSources > 0 ? "Memory source connected" : "No memory source connected",
                    detail: sourceDetail(activeSources: activeSources),
                    systemImage: activeSources > 0 ? "checkmark.seal.fill" : "folder.badge.plus",
                    color: activeSources > 0 ? .green : .accentColor
                )
                HomeStatusRow(
                    title: pendingCount > 0 ? "Memory waiting for review" : (memoryCount > 0 ? "Approved memory ready" : "Memory will appear after sync"),
                    detail: memoryDetail(memoryCount: memoryCount, pendingCount: pendingCount),
                    systemImage: pendingCount > 0 ? "tray.full.fill" : (memoryCount > 0 ? "brain.head.profile.fill" : "brain.head.profile"),
                    color: pendingCount > 0 ? .orange : (memoryCount > 0 ? .accentColor : .secondary)
                )
                if state.connectedAIIntegrationCount > 0 || state.detectedAIIntegrationCount > 0 {
                    HomeStatusRow(
                        title: state.connectedAIIntegrationCount > 0 ? "AI tool connected" : "AI tool detected",
                        detail: aiToolDetail,
                        systemImage: state.connectedAIIntegrationCount > 0 ? "checkmark.circle.fill" : "app.badge.checkmark",
                        color: state.connectedAIIntegrationCount > 0 ? .green : .accentColor
                    )
                }
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func sourceDetail(activeSources: Int) -> String {
        if activeSources > 0 {
            return "\(activeSources) source\(activeSources == 1 ? "" : "s") syncing automatically"
        }
        return "Connect Obsidian or a local notes folder from Connections & Privacy"
    }

    private var aiToolDetail: String {
        if state.connectedAIIntegrationCount > 0 {
            return "\(state.connectedAIIntegrationCount) tool\(state.connectedAIIntegrationCount == 1 ? "" : "s") can use approved memory after Review"
        }
        return "Detected tools can be connected after a memory source is ready"
    }

    private func memoryDetail(memoryCount: Int, pendingCount: Int) -> String {
        if pendingCount > 0 {
            return "\(pendingCount) item\(pendingCount == 1 ? "" : "s") need approval before Ask uses them"
        }
        if memoryCount > 0 {
            return "\(memoryCount) approved memor\(memoryCount == 1 ? "y" : "ies") available with citations"
        }
        return "Cortex keeps new signals in Review before they shape answers"
    }
}

struct HomeStatusRow: View {
    let title: String
    let detail: String
    let systemImage: String
    let color: Color

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: systemImage)
                .font(.title3)
                .foregroundColor(color)
                .frame(width: 32, height: 32)
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(.callout)
                    .fontWeight(.semibold)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
        }
        .padding(12)
        .background(Color(nsColor: .windowBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct HomeActionSection: View {
    @ObservedObject var state: AppState
    let review: DailyReviewResponse?

    private var pendingCount: Int {
        review?.stats.pending_captures ?? state.inbox.count
    }

    private var hasMemory: Bool {
        (review?.stats.memories ?? state.stats?.memories ?? 0) > 0
    }

    var body: some View {
        HomePrimaryActionButton(
            title: actionTitle,
            detail: actionDetail,
            systemImage: actionIcon,
            isDisabled: actionDisabled
        ) {
            runNextAction()
        }
    }

    private var actionTitle: String {
        if !state.isLocalServiceReady { return "Start private vault" }
        if state.activeSourceAccounts.isEmpty { return "Connect notes" }
        if pendingCount > 0 { return "Review memory" }
        if hasMemory { return "Ask Cortex" }
        return "Check connections"
    }

    private var actionDetail: String {
        if !state.isLocalServiceReady { return state.displayBackendStatus }
        if state.activeSourceAccounts.isEmpty {
            if state.connectedAIIntegrationCount > 0 {
                return "Your AI tool is ready. Add notes so memory can sync automatically."
            }
            return "Connect Obsidian or local notes once. Cortex keeps sync automatic after that."
        }
        if pendingCount > 0 { return "\(pendingCount) new item\(pendingCount == 1 ? "" : "s") waiting for approval" }
        if hasMemory { return "Search approved memory with citations" }
        return "Confirm source health and privacy controls"
    }

    private var actionIcon: String {
        if !state.isLocalServiceReady { return "externaldrive.badge.checkmark" }
        if state.activeSourceAccounts.isEmpty { return "folder.badge.plus" }
        if pendingCount > 0 { return "checklist" }
        if hasMemory { return "magnifyingglass" }
        return "lock.shield"
    }

    private var actionDisabled: Bool {
        state.isBusy
    }

    private func runNextAction() {
        if !state.isLocalServiceReady {
            Task {
                await state.ensureBackend()
                await state.loadDiagnostics()
                await state.loadReview()
                await state.loadStats()
            }
        } else if state.activeSourceAccounts.isEmpty {
            state.openConnectionsPrivacy(statusMessage: "Connect notes")
        } else if pendingCount > 0 {
            state.selectedTab = .review
            state.status = "Review memory"
        } else if hasMemory {
            state.selectedTab = .ask
            state.status = "Ask Cortex"
        } else {
            state.openConnectionsPrivacy(statusMessage: "Check source health and privacy")
        }
    }
}

struct HomePrimaryActionButton: View {
    let title: String
    let detail: String
    let systemImage: String
    let isDisabled: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: systemImage)
                    .font(.title2)
                    .frame(width: 34, height: 34)
                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                        .font(.title3)
                        .fontWeight(.semibold)
                    Text(detail)
                        .font(.callout)
                        .foregroundColor(.white.opacity(0.86))
                        .lineLimit(2)
                }
                Spacer(minLength: 0)
            }
            .padding(18)
            .frame(maxWidth: .infinity, minHeight: 96, alignment: .leading)
        }
        .buttonStyle(.plain)
        .foregroundColor(.white)
        .background(Color.accentColor)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .disabled(isDisabled)
        .opacity(isDisabled ? 0.55 : 1)
    }
}

struct HomeFirstRunNotes: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center, spacing: 12) {
                Image(systemName: "link.circle")
                    .font(.title2)
                    .foregroundColor(.accentColor)
                    .frame(width: 36, height: 36)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Connect once")
                        .font(.headline)
                    Text("Connect Obsidian or local notes once. Cortex syncs quietly, sends useful memory to Review, then answers with citations.")
                        .font(.callout)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
        }
        .padding(16)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.72))
        .clipShape(RoundedRectangle(cornerRadius: 8))
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
        .background(Color(nsColor: .windowBackgroundColor).opacity(0.65))
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
            SectionHeader(title: "Sources", detail: "Where approved memory is coming from.")
            if let summary = state.trustSummary, !summary.source_counts.isEmpty {
                HStack(spacing: 8) {
                    ForEach(summary.source_counts.prefix(4)) { source in
                        SourceCoveragePill(source: source)
                    }
                    Spacer(minLength: 0)
                }
            } else if review.stats.memories == 0 {
                QuietState(title: "No approved memory yet", detail: "Connect one source, then approve useful memory in Review before expecting Ask to answer.")
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
            SectionHeader(title: "Current snapshot", detail: "A quick read on approved memory, pending review, topics, people, and decisions.")
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 130), spacing: 8)], spacing: 8) {
                ModelMetricPill(label: "Memories", value: "\(review.stats.memories)", systemImage: "brain.head.profile")
                ModelMetricPill(label: "Pending review", value: "\(review.stats.pending_captures)", systemImage: "tray.full")
                ModelMetricPill(label: "Topics", value: "\(review.top_topics.count)", systemImage: "number")
                ModelMetricPill(label: "People & projects", value: "\(review.top_entities.count)", systemImage: "person.2")
                ModelMetricPill(label: "Open loops", value: "\(review.open_tasks.count)", systemImage: "circle.dashed")
                ModelMetricPill(label: "Decisions", value: "\(review.recent_decisions.count)", systemImage: "checkmark.seal")
            }
            if review.top_topics.isEmpty && review.top_entities.isEmpty {
                QuietState(title: "Needs more approved memory", detail: "Approve more memory or connect a richer notes source to improve people, project, topic, and style coverage.")
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
        .background(Color(nsColor: .controlBackgroundColor).opacity(count > 0 ? 0.78 : 0.45))
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
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.72))
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
