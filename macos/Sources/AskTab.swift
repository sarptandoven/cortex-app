import SwiftUI

private struct AskStarter: Identifiable {
    let label: String
    let systemImage: String
    let query: String

    var id: String { label }
}

struct AskTab: View {
    @ObservedObject var state: AppState
    @State private var useElsewhereExpanded = false
    @State private var citedMemoriesExpanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            AskHeaderSection()
            AskQuerySection(state: state)

            if state.hasSearched && !state.askAnswer.isEmpty {
                AskAnswerPanel(answer: state.askAnswer, citations: state.askCitations)
                DisclosureGroup("Cited memories", isExpanded: $citedMemoriesExpanded) {
                    AskResultsSection(state: state)
                        .padding(.top, 8)
                }
            } else {
                AskResultsSection(state: state)
            }

            AskAIHandoffSection(state: state, isExpanded: $useElsewhereExpanded)
        }
        .padding(16)
    }
}

struct AskHeaderSection: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Ask Cortex")
                .font(.title3)
                .fontWeight(.semibold)
            Text("Search approved memory and inspect citations before using the model anywhere else.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct AskQuerySection: View {
    @ObservedObject var state: AppState
    private let starters: [AskStarter] = [
        AskStarter(label: "Decisions", systemImage: "checkmark.seal", query: "What decisions should I remember?"),
        AskStarter(label: "Preferences", systemImage: "slider.horizontal.3", query: "What preferences have I stated?"),
        AskStarter(label: "Style", systemImage: "signature", query: "How do I usually write?"),
        AskStarter(label: "Recent", systemImage: "clock.arrow.circlepath", query: "What changed recently?")
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                TextField("Ask about a person, project, decision, preference, or writing pattern", text: $state.searchQuery)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { state.runSearch() }
                Button {
                    state.runSearch()
                } label: {
                    Label("Ask", systemImage: "magnifyingglass")
                }
                .buttonStyle(.borderedProminent)
            }

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 122), spacing: 8)], spacing: 8) {
                ForEach(starters) { starter in
                    Button {
                        state.searchQuery = starter.query
                        state.runSearch()
                    } label: {
                        Label(starter.label, systemImage: starter.systemImage)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .buttonStyle(.bordered)
                }
            }

            if state.hasSearched {
                Text("\(state.searchResults.count) cited result\(state.searchResults.count == 1 ? "" : "s")")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .padding(12)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct AskResultsSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        Group {
            if state.searchResults.isEmpty && !state.hasSearched {
                QuietState(title: "Ask your model", detail: "Try a question about a project, person, decision, preference, or phrase from your imported sources.")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if state.searchResults.isEmpty {
                QuietState(title: "No cited memory matched that", detail: "Try a project, person, decision, or exact phrase from an approved source.")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        ForEach(state.searchResults) { item in
                            MemoryCard(item: item) {
                                state.deleteMemory(item)
                            }
                        }
                    }
                }
            }
        }
    }
}

struct AskAIHandoffSection: View {
    @ObservedObject var state: AppState
    @Binding var isExpanded: Bool

    var body: some View {
        DisclosureGroup("Use in another AI app", isExpanded: $isExpanded) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Use these only when another AI app cannot connect to Cortex directly.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                HStack {
                    Button {
                        state.copyAgentAdaptation()
                    } label: {
                        Label("Copy Instructions", systemImage: "wand.and.stars")
                    }
                    Button {
                        state.contextQuery = state.searchQuery
                        state.copyContextPack()
                    } label: {
                        Label("Copy Matching Memory", systemImage: "text.quote")
                    }
                    Button {
                        state.copyDailyContextPack()
                    } label: {
                        Label("Copy My Profile", systemImage: "brain.head.profile")
                    }
                    Spacer()
                }
            }
            .padding(.top, 4)
        }
    }
}

struct AskAnswerPanel: View {
    let answer: String
    let citations: [AskCitationItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label("Answer", systemImage: "quote.bubble")
                    .font(.headline)
                Spacer()
                if !citations.isEmpty {
                    Text("\(citations.count) citation\(citations.count == 1 ? "" : "s")")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
            Text(answer)
                .font(.body)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            if !citations.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(citations.prefix(4)) { citation in
                        HStack(spacing: 6) {
                            Text("[\(citation.index)]")
                                .font(.caption)
                                .fontWeight(.semibold)
                                .foregroundColor(.secondary)
                            Text(citation.source_url ?? citation.source)
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                                .truncationMode(.middle)
                            Spacer(minLength: 0)
                        }
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
