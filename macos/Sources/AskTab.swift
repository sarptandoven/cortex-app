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
                    QuietState(
                        title: "Ask approved memory",
                        detail: "Ask about a source, project, person, decision, or exact phrase. Cortex answers only from approved memory and shows citations."
                    )
                }
            }
            .frame(maxWidth: .infinity, alignment: .topLeading)
            .padding(16)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

struct AskHeaderSection: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Ask Cortex")
                .font(.title3)
                .fontWeight(.semibold)
            Text("Ask about approved memory and get an answer with sources you can inspect.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct AskQuerySection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                TextField("Ask about approved memory, a project, a person, or an exact phrase", text: $state.searchQuery)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { state.runSearch() }
                Button {
                    state.runSearch()
                } label: {
                    Label("Ask", systemImage: "magnifyingglass")
                }
                .buttonStyle(.borderedProminent)
            }

            if state.hasSearched {
                Text(citationSummary)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .padding(12)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var citationSummary: String {
        if state.askCitations.isEmpty {
            return state.searchResults.isEmpty ? "No citations yet" : "\(state.searchResults.count) source match\(state.searchResults.count == 1 ? "" : "es")"
        }
        return "\(state.askCitations.count) citation\(state.askCitations.count == 1 ? "" : "s")"
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
                QuietState(
                    title: "No cited answer found",
                    detail: "Try an exact phrase from approved memory, or connect a source from Connections & Privacy and approve it in Review."
                )
            } else {
                QuietState(
                    title: "Matching source found",
                    detail: "Cortex found related memory, but no answer was returned. Open source details below."
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
        "Source details (\(state.searchResults.count))"
    }
}

struct AskResultsSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        Group {
            if state.searchResults.isEmpty && !state.hasSearched {
                QuietState(title: "Ask approved memory", detail: "Ask about a project, a person, a decision, or an exact phrase from approved memory.")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if state.searchResults.isEmpty {
                QuietState(title: "No cited result matched", detail: "Try an exact phrase from approved memory, or approve more memory in Review.")
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
        .background(Color(nsColor: .textBackgroundColor))
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
        return kind.isEmpty ? "Approved memory" : "\(kind.capitalized) memory"
    }

    private var citationLabel: String? {
        guard let sourceURL = item.source_url?.trimmingCharacters(in: .whitespacesAndNewlines),
              !sourceURL.isEmpty else {
            return nil
        }
        if sourceURL.hasPrefix("file://"), let url = URL(string: sourceURL) {
            return url.lastPathComponent.isEmpty ? url.path : url.lastPathComponent
        }
        return sourceURL
    }
}

struct AskAnswerPanel: View {
    let answer: String
    let citations: [AskCitationItem]

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
                    Text("\(citations.count) citation\(citations.count == 1 ? "" : "s")")
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(.secondary)
                    ForEach(citations) { citation in
                        AskCitationRow(citation: citation)
                    }
                }
            }
        }
        .padding(12)
        .background(Color(nsColor: .textBackgroundColor))
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
        if let path = citation.citation_path?.trimmingCharacters(in: .whitespacesAndNewlines), !path.isEmpty {
            return path
        }
        let sourceURL = citation.source_url?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return sourceURL.isEmpty ? citation.source : sourceURL
    }

    private var sourceDetail: String? {
        let pieces = [
            citation.section_title?.trimmingCharacters(in: .whitespacesAndNewlines),
            lineLabel,
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
