import SwiftUI

struct AskTab: View {
    @ObservedObject var state: AppState
    @State private var citedMemoriesExpanded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                AskHeaderSection()
                AskQuerySection(state: state)

                if state.hasSearched {
                    AskResponseSection(
                        state: state,
                        citedMemoriesExpanded: $citedMemoriesExpanded
                    )
                } else {
                    AskEmptyGuidance(
                        state: state,
                        title: "Ask your notes",
                        detail: "Ask about a project, person, decision, or detail from your reviewed notes. Cortex answers with sources.",
                        showActionsWhenMemoryExists: false
                    )
                }
            }
            .frame(maxWidth: .infinity, alignment: .topLeading)
            .padding(16)
        }
        .task {
            await state.loadSourceConnectivity()
            await state.loadReview()
            await state.loadStats()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(CortexDesign.appBackground)
    }
}

struct AskHeaderSection: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Ask Cortex")
                .font(.title3)
                .fontWeight(.semibold)
            Text("Cortex answers from reviewed notes and shows sources.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct AskQuerySection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                TextField("Ask about a project, person, decision, or phrase", text: $state.searchQuery)
                    .textFieldStyle(.roundedBorder)
                    .font(.title3)
                    .frame(minHeight: 52)
                    .onSubmit { state.runSearch() }
                Button {
                    state.runSearch()
                } label: {
                    Label("Ask", systemImage: "magnifyingglass")
                        .frame(minWidth: 104, minHeight: 52)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }
        }
        .padding(16)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.softBorder))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

}

struct AskResponseSection: View {
    @ObservedObject var state: AppState
    @Binding var citedMemoriesExpanded: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !state.askAnswer.isEmpty {
                AskAnswerPanel(
                    answer: state.askAnswer,
                    citations: state.askCitations
                )
            } else if state.searchResults.isEmpty {
                AskEmptyGuidance(
                    state: state,
                    title: "No cited answer found",
                    detail: "I could not find that in reviewed notes yet. Review new synced items or try a more specific question.",
                    showActionsWhenMemoryExists: true
                )
            } else {
                QuietState(
                    title: "Matching memory found",
                    detail: "Cortex found related memory, but no answer was returned. Open sources below."
                )
            }

            if !state.searchResults.isEmpty {
                DisclosureGroup(memoryDisclosureTitle, isExpanded: $citedMemoriesExpanded) {
                    AskResultsSection(state: state)
                        .frame(maxHeight: 280)
                        .padding(.top, 8)
                }
            }
        }
    }

    private var memoryDisclosureTitle: String {
        "Sources (\(state.searchResults.count))"
    }
}

struct AskEmptyGuidance: View {
    @ObservedObject var state: AppState
    let title: String
    let detail: String
    let showActionsWhenMemoryExists: Bool

    private var approvedMemoryCount: Int {
        state.review?.stats.memories ?? state.stats?.memories ?? 0
    }

    private var obsidianConnector: SourceConnectorCatalogItem? {
        state.sourceConnectorCatalog.first { $0.id == "obsidian" }
    }

    private func startNotesSync() {
        if let connector = obsidianConnector {
            state.connectLocalNotesFolder(connector)
        } else {
            state.openConnectionsPrivacy(statusMessage: "Source sync")
        }
    }

    var body: some View {
        VStack(spacing: 12) {
            QuietState(title: title, detail: detail)

            if approvedMemoryCount == 0 || showActionsWhenMemoryExists {
                HStack(spacing: 10) {
                    if approvedMemoryCount == 0 {
                        Button {
                            startNotesSync()
                        } label: {
                            Label("Start source sync", systemImage: "folder.badge.plus")
                                .frame(minWidth: 172, minHeight: 46)
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)

                        Button {
                            state.selectedTab = .review
                            state.status = "Review memory"
                        } label: {
                            Label("Open Review", systemImage: "checklist")
                                .frame(minWidth: 132, minHeight: 46)
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.large)
                    } else {
                        Button {
                            state.selectedTab = .review
                            state.status = "Review more memory"
                        } label: {
                            Label("Review memory", systemImage: "checklist")
                                .frame(minWidth: 150, minHeight: 46)
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)

                        Button {
                            startNotesSync()
                        } label: {
                            Label("Sync another source", systemImage: "folder.badge.plus")
                                .frame(minWidth: 168, minHeight: 46)
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.large)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .center)
            }
        }
    }
}

struct AskResultsSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        Group {
            if state.searchResults.isEmpty && !state.hasSearched {
                QuietState(title: "Ask your notes", detail: "Ask about a project, person, decision, or detail from reviewed notes.")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if state.searchResults.isEmpty {
                QuietState(title: "No cited result matched", detail: "Review new synced items or try a more specific question.")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        ForEach(state.searchResults) { item in
                            AskSourceDetailRow(item: item)
                        }
                    }
                }
            }
        }
    }
}

struct AskSourceDetailRow: View {
    let item: MemoryItem

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center, spacing: 8) {
                Image(systemName: "quote.bubble")
                    .font(.callout)
                    .foregroundColor(.accentColor)
                    .frame(width: 28, height: 28)
                    .background(Color.accentColor.opacity(0.11))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                VStack(alignment: .leading, spacing: 2) {
                    Text(sourceTitle)
                        .font(.callout)
                        .fontWeight(.semibold)
                        .lineLimit(1)
                    Text(detailLine)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
            }
            Text(item.content)
                .font(.body)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            if let citation = citationLabel {
                HStack(spacing: 6) {
                    Image(systemName: "link")
                        .font(.caption2)
                    Text(citation)
                        .font(.caption)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer(minLength: 0)
                }
                .foregroundColor(.secondary)
                .help(item.source_url ?? citation)
            }
        }
        .padding(12)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.32)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var sourceTitle: String {
        item.source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "Cited source" : item.source
    }

    private var detailLine: String {
        let layer = (item.layer ?? item.kind).trimmingCharacters(in: .whitespacesAndNewlines)
        let kind = item.kind.trimmingCharacters(in: .whitespacesAndNewlines)
        if !layer.isEmpty, layer != kind {
            return "\(layer.capitalized) memory"
        }
        return kind.isEmpty ? "Reviewed memory" : "\(kind.capitalized) memory"
    }

    private var citationLabel: String? {
        CitationDisplay.label(sourceURL: item.source_url)
    }
}

struct AskAnswerPanel: View {
    let answer: String
    let citations: [AskCitationItem]

    @State private var showAllCitations = false

    private static let collapsedCitationCount = 3

    private var visibleCitations: [AskCitationItem] {
        showAllCitations ? citations : Array(citations.prefix(Self.collapsedCitationCount))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Label("Answer", systemImage: "quote.bubble")
                    .font(.headline)
                Spacer()
            }
            Text(answer)
                .font(.body)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            if !citations.isEmpty {
                Divider()
                VStack(alignment: .leading, spacing: 6) {
                    Text("Citations")
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(.secondary)
                    ForEach(visibleCitations) { citation in
                        AskCitationRow(citation: citation)
                    }
                    if citations.count > Self.collapsedCitationCount {
                        Button(showAllCitations ? "Show fewer citations" : "Show all \(citations.count) citations") {
                            showAllCitations.toggle()
                        }
                        .buttonStyle(.plain)
                        .font(.caption)
                        .foregroundColor(CortexDesign.accent)
                    }
                }
            }
        }
        .padding(12)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct AskCitationRow: View {
    let citation: AskCitationItem

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text("[\(citation.index)]")
                .font(.caption)
                .fontWeight(.semibold)
                .foregroundColor(.secondary)
                .monospacedDigit()
            VStack(alignment: .leading, spacing: 2) {
                Text(sourceLabel)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                if let detail = sourceDetail {
                    Text(detail)
                        .font(.caption2)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                if !excerpt.isEmpty {
                    Text(excerpt)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
    }

    private var sourceLabel: String {
        CitationDisplay.label(
            path: citation.citation_path,
            sourceURL: citation.source_url,
            fallback: citation.source,
            lineStart: citation.line_start,
            lineEnd: citation.line_end
        ) ?? citation.source
    }

    private var sourceDetail: String? {
        let pieces = [
            citation.section_title?.trimmingCharacters(in: .whitespacesAndNewlines),
            citation.citation_path == nil ? lineLabel : nil,
            citation.record_scope?.trimmingCharacters(in: .whitespacesAndNewlines)
        ]
        let detail = pieces.compactMap { value -> String? in
            guard let value, !value.isEmpty else { return nil }
            return value
        }.joined(separator: " - ")
        return detail.isEmpty ? nil : detail
    }

    private var lineLabel: String? {
        guard let start = citation.line_start else { return nil }
        if let end = citation.line_end, end > start {
            return "Lines \(start)-\(end)"
        }
        return "Line \(start)"
    }

    private var excerpt: String {
        citation.excerpt.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
