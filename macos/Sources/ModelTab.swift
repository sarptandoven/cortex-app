import SwiftUI

struct ModelTab: View {
    @ObservedObject var state: AppState
    @State private var modelDetailsExpanded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if let review = state.review {
                    if review.stats.memories == 0 && review.pending.isEmpty {
                        ModelEmptySection(state: state)
                    } else {
                        ModelOverviewSection(state: state, review: review)
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
                                title: "Model details",
                                detail: "Layers, sources, topics, entities, and signal health"
                            )
                        }
                        .padding(12)
                        .background(Color(nsColor: .controlBackgroundColor).opacity(0.65))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                } else {
                    VStack(alignment: .center, spacing: 12) {
                        ProgressView()
                        Text("Loading personal model")
                            .font(.headline)
                        Text("Cortex is starting the local memory engine.")
                            .foregroundColor(.secondary)
                    }
                    .frame(maxWidth: .infinity, minHeight: 420)
                }
            }
            .padding(16)
        }
    }
}

struct ModelEmptySection: View {
    @ObservedObject var state: AppState
    @State private var layersExpanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .top, spacing: 14) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color.accentColor.opacity(0.14))
                    Image(systemName: "brain.head.profile")
                        .font(.system(size: 30, weight: .semibold))
                        .foregroundColor(.accentColor)
                }
                .frame(width: 64, height: 64)

                VStack(alignment: .leading, spacing: 6) {
                    Text("Start your personal model")
                        .font(.title2)
                        .fontWeight(.semibold)
                    Text("Add conversations, email, notes, writing samples, decisions, or messages. Cortex turns them into private memory layers for future agent adaptation.")
                        .font(.callout)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }

            HStack(spacing: 8) {
                Button {
                    state.selectedTab = .sources
                    state.status = "Add your first source"
                } label: {
                    Label("Add First Source", systemImage: "tray.and.arrow.down")
                }
                .buttonStyle(.borderedProminent)

                Button {
                    state.selectedTab = .trust
                    state.status = "Review privacy and AI access"
                } label: {
                    Label("Review Privacy", systemImage: "lock.shield")
                }

                Spacer()
            }

            DisclosureGroup(isExpanded: $layersExpanded) {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 128), spacing: 8)], spacing: 8) {
                    EmptyLayerPill(title: "Facts", detail: "what is true", systemImage: "text.book.closed")
                    EmptyLayerPill(title: "Events", detail: "what happened", systemImage: "calendar")
                    EmptyLayerPill(title: "Style", detail: "how you write", systemImage: "signature")
                    EmptyLayerPill(title: "Decisions", detail: "what you chose", systemImage: "checkmark.seal")
                    EmptyLayerPill(title: "Preferences", detail: "what you like", systemImage: "slider.horizontal.3")
                    EmptyLayerPill(title: "Rejections", detail: "what to avoid", systemImage: "hand.raised")
                }
                .padding(.top, 8)
            } label: {
                ModelDisclosureLabel(
                    systemImage: "square.stack.3d.up",
                    title: "What Cortex learns",
                    detail: "Facts, events, style, decisions, preferences, and rejections"
                )
            }
            .padding(12)
            .background(Color(nsColor: .windowBackgroundColor).opacity(0.54))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .padding(18)
        .frame(maxWidth: .infinity, minHeight: 420, alignment: .topLeading)
        .background(
            LinearGradient(
                colors: [
                    Color(nsColor: .controlBackgroundColor),
                    Color.accentColor.opacity(0.08)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        )
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct EmptyLayerPill: View {
    let title: String
    let detail: String
    let systemImage: String

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: systemImage)
                .foregroundColor(.accentColor)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(.caption)
                    .fontWeight(.semibold)
                Text(detail)
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(9)
        .background(Color(nsColor: .windowBackgroundColor).opacity(0.64))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct ModelOverviewSection: View {
    @ObservedObject var state: AppState
    let review: DailyReviewResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let loop = state.productLoop {
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Personal model")
                            .font(.title3)
                            .fontWeight(.semibold)
                        Text(modelDetail(loop: loop))
                            .font(.callout)
                            .foregroundColor(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer()
                    ModelReadinessRing(value: readinessScore)
                }

                HStack(alignment: .center, spacing: 10) {
                    Button {
                        state.performProductLoopAction(loop.primary_action)
                    } label: {
                        Label(loop.primary_action.label, systemImage: primaryActionIcon(loop.primary_action.action))
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(loop.primary_action.action == "done")

                    Text(loop.primary_action.detail)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer()
                }

                ModelProofPointCard(state: state, memory: review.recent_memories.first, signalCount: review.stats.memories)

            } else {
                HStack {
                    ProgressView()
                    Text("Loading personal model")
                        .foregroundColor(.secondary)
                    Spacer()
                }
            }
        }
        .padding(16)
        .background(
            LinearGradient(
                colors: [
                    Color(nsColor: .controlBackgroundColor),
                    Color.accentColor.opacity(0.10)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        )
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var readinessScore: Int {
        min(100, max(0, review.stats.memories * 4 + review.recent_decisions.count * 6 + review.stats.entities * 2))
    }

    private func modelDetail(loop: ProductLoopResponse) -> String {
        if review.stats.memories == 0 {
            return "Add conversations, email, notes, writing, decisions, and messages so Cortex can build an adaptation layer around how you think, decide, write, and work."
        }
        if review.stats.pending_captures > 0 {
            return "Cortex has new data waiting for review before it becomes part of the personal model."
        }
        return "Cortex is organizing your data into semantic, episodic, style, decision, preference, and negative memory layers for in-house agent adaptation."
    }

    private func primaryActionIcon(_ action: String) -> String {
        switch action {
        case "capture":
            return "tray.and.arrow.down"
        case "review":
            return "checklist"
        case "reuse":
            return "magnifyingglass"
        case "done":
            return "checkmark.seal"
        default:
            return "arrow.right.circle"
        }
    }
}

struct ModelReadinessRing: View {
    let value: Int

    var body: some View {
        ZStack {
            Circle()
                .stroke(Color.secondary.opacity(0.18), lineWidth: 8)
            Circle()
                .trim(from: 0, to: CGFloat(value) / 100)
                .stroke(Color.accentColor, style: StrokeStyle(lineWidth: 8, lineCap: .round))
                .rotationEffect(.degrees(-90))
            VStack(spacing: 0) {
                Text("\(value)")
                    .font(.headline)
                    .fontWeight(.semibold)
                Text("model")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
        }
        .frame(width: 70, height: 70)
        .accessibilityLabel("Model readiness \(value) percent")
    }
}

struct ModelProofPointCard: View {
    @ObservedObject var state: AppState
    let memory: MemoryItem?
    let signalCount: Int

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .foregroundColor(.accentColor)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 5) {
                Text("Proof point")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(.secondary)
                if let memory {
                    Text(memory.content)
                        .font(.callout)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 8) {
                        Text(sourceDetail(for: memory))
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                        Spacer(minLength: 0)
                        Button {
                            state.searchQuery = String(memory.content.prefix(140))
                            state.selectedTab = .ask
                            state.runSearch()
                        } label: {
                            Label("Ask", systemImage: "magnifyingglass")
                        }
                    }
                } else {
                    Text("\(signalCount) approved signals are available for adaptation.")
                        .font(.callout)
                    Text("Add richer sources to show a concrete memory here.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
        }
        .padding(12)
        .background(Color(nsColor: .windowBackgroundColor).opacity(0.64))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var icon: String {
        guard let memory else {
            return "brain.head.profile"
        }
        switch memory.layer ?? memory.kind {
        case "decision":
            return "checkmark.seal"
        case "episodic", "event":
            return "calendar"
        case "style":
            return "signature"
        case "preference":
            return "slider.horizontal.3"
        case "negative":
            return "hand.raised"
        default:
            return "brain.head.profile"
        }
    }

    private func sourceDetail(for memory: MemoryItem) -> String {
        var parts = [memory.source]
        if let date = memory.captured_at {
            parts.append(String(date.prefix(10)))
        }
        if let url = memory.source_url, !url.isEmpty {
            parts.append(url)
        }
        return parts.joined(separator: " · ")
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
            SectionHeader(title: "Memory quality", detail: "Citation, review, and layer health for the current model.")
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
                        TrustNotice(systemImage: "checkmark.seal.fill", title: quality.status.replacingOccurrences(of: "_", with: " ").capitalized, detail: "Imported memory has usable citations, review state, and layer coverage.", color: .green)
                    }
                }
            }
        }
    }

    private func percent(_ value: Double) -> String {
        "\(Int((value * 100).rounded()))%"
    }
}

struct ModelRecentMemorySection: View {
    @ObservedObject var state: AppState
    let memory: MemoryItem

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title: "Recent useful memory", detail: "A concrete signal currently shaping the personal model.")
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: icon)
                    .foregroundColor(.accentColor)
                    .frame(width: 24)
                VStack(alignment: .leading, spacing: 6) {
                    Text(layerTitle)
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(.secondary)
                    Text(memory.content)
                        .font(.body)
                        .lineLimit(4)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 8) {
                        Text(sourceDetail)
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                        Spacer(minLength: 0)
                        Button {
                            state.searchQuery = String(memory.content.prefix(140))
                            state.selectedTab = .ask
                            state.runSearch()
                        } label: {
                            Label("Ask", systemImage: "magnifyingglass")
                        }
                    }
                }
            }
            .padding(12)
            .background(Color(nsColor: .controlBackgroundColor))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }

    private var layerTitle: String {
        (memory.layer ?? memory.kind).replacingOccurrences(of: "_", with: " ").capitalized
    }

    private var icon: String {
        switch memory.layer ?? memory.kind {
        case "decision":
            return "checkmark.seal"
        case "episodic", "event":
            return "calendar"
        case "style":
            return "signature"
        case "preference":
            return "slider.horizontal.3"
        case "negative":
            return "hand.raised"
        default:
            return "brain.head.profile"
        }
    }

    private var sourceDetail: String {
        var parts = [memory.source]
        if let date = memory.captured_at {
            parts.append(String(date.prefix(10)))
        }
        if let url = memory.source_url, !url.isEmpty {
            parts.append(url)
        }
        return parts.joined(separator: " · ")
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
            SectionHeader(title: "Model coverage", detail: "Memory layers Cortex can use for adaptation.")
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
            SectionHeader(title: "Sources", detail: "Chats, notes, files, emails, writing, decisions, and messages become structured memory.")
            if let summary = state.trustSummary, !summary.source_counts.isEmpty {
                HStack(spacing: 8) {
                    ForEach(summary.source_counts.prefix(4)) { source in
                        SourceCoveragePill(source: source)
                    }
                    Spacer(minLength: 0)
                }
            } else if review.stats.memories == 0 {
                QuietState(title: "No personal model yet", detail: "Add conversations, notes, writing samples, files, and decisions to start building your adaptation layer.")
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
            SectionHeader(title: "Current signal", detail: "High-level shape of the model without review clutter.")
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 130), spacing: 8)], spacing: 8) {
                ModelMetricPill(label: "Signals", value: "\(review.stats.memories)", systemImage: "brain.head.profile")
                ModelMetricPill(label: "Review", value: "\(review.stats.pending_captures)", systemImage: "tray.full")
                ModelMetricPill(label: "Topics", value: "\(review.top_topics.count)", systemImage: "number")
                ModelMetricPill(label: "People & projects", value: "\(review.top_entities.count)", systemImage: "person.2")
                ModelMetricPill(label: "Open loops", value: "\(review.open_tasks.count)", systemImage: "circle.dashed")
                ModelMetricPill(label: "Decisions", value: "\(review.recent_decisions.count)", systemImage: "checkmark.seal")
            }
            if review.top_topics.isEmpty && review.top_entities.isEmpty {
                QuietState(title: "Needs more context", detail: "Import a richer source to improve people, project, topic, and style coverage.")
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
                Text(count > 0 ? "\(count) signals" : "Needs data")
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
