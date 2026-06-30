import AppKit
import SwiftUI

private struct AskStarter: Identifiable {
    let label: String
    let systemImage: String
    let query: String

    var id: String { label }
}

struct AskTab: View {
    @ObservedObject var state: AppState
    @State private var exportOptionsExpanded = false
    @State private var citedMemoriesExpanded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                AskHeaderSection()
                AskQuerySection(state: state)

                if state.hasSearched {
                    AskResponseSection(
                        state: state,
                        citedMemoriesExpanded: $citedMemoriesExpanded,
                        copyForAIApp: copyAnswerForAIApp
                    )
                } else {
                    QuietState(
                        title: "Ask for a cited answer",
                        detail: "Try a question about a project, person, decision, preference, or phrase from your imported sources."
                    )
                }

                AskAIHandoffSection(state: state, isExpanded: $exportOptionsExpanded)
            }
            .frame(maxWidth: .infinity, alignment: .topLeading)
            .padding(16)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private func copyAnswerForAIApp() {
        let payload = askAIHandoffPayload(
            question: state.searchQuery,
            answer: state.askAnswer,
            citations: state.askCitations
        )
        guard !payload.isEmpty else { return }

        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(payload, forType: .string)
        if state.askCitations.isEmpty {
            state.status = "Answer copied for AI app"
        } else {
            state.status = "Answer with \(state.askCitations.count) citation\(state.askCitations.count == 1 ? "" : "s") copied for AI app"
        }
    }
}

struct AskHeaderSection: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Ask Cortex")
                .font(.title3)
                .fontWeight(.semibold)
            Text("Get a natural-language answer from approved memory, with citations ready to reuse.")
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
            return state.searchResults.isEmpty ? "No citations found" : "\(state.searchResults.count) source memory match\(state.searchResults.count == 1 ? "" : "es")"
        }
        return "\(state.askCitations.count) citation\(state.askCitations.count == 1 ? "" : "s")"
    }
}

struct AskResponseSection: View {
    @ObservedObject var state: AppState
    @Binding var citedMemoriesExpanded: Bool
    let copyForAIApp: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !state.askAnswer.isEmpty {
                AskAnswerPanel(
                    answer: state.askAnswer,
                    citations: state.askCitations,
                    copyForAIApp: copyForAIApp
                )
            } else if state.searchResults.isEmpty {
                QuietState(
                    title: "No cited answer found",
                    detail: "Try a project, person, decision, or exact phrase from an approved source."
                )
            } else {
                QuietState(
                    title: "Source memory found",
                    detail: "Cortex found matching source memory, but no natural-language answer was returned."
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
        "Source memory details (\(state.searchResults.count))"
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
        DisclosureGroup("More export options", isExpanded: $isExpanded) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Use these copy formats when another AI app needs more than the cited answer.")
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
    var copyForAIApp: (() -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Label("Answer", systemImage: "quote.bubble")
                    .font(.headline)
                Spacer()
                if let copyForAIApp {
                    Button {
                        copyForAIApp()
                    } label: {
                        Label("Copy for AI app", systemImage: "square.and.arrow.up")
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .help("Copies the answer with citation excerpts and source links.")
                }
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
        let sourceURL = citation.source_url?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return sourceURL.isEmpty ? citation.source : sourceURL
    }

    private var excerpt: String {
        citation.excerpt.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

private func askAIHandoffPayload(question: String, answer: String, citations: [AskCitationItem]) -> String {
    var sections: [String] = []

    let trimmedQuestion = question.trimmingCharacters(in: .whitespacesAndNewlines)
    if !trimmedQuestion.isEmpty {
        sections.append("Question:\n\(trimmedQuestion)")
    }

    let trimmedAnswer = answer.trimmingCharacters(in: .whitespacesAndNewlines)
    if !trimmedAnswer.isEmpty {
        sections.append("Answer:\n\(trimmedAnswer)")
    }

    if !citations.isEmpty {
        let citationText = citations.map { citation -> String in
            var lines = ["[\(citation.index)] \(askCitationSourceLabel(citation))"]
            if let date = askCitationDate(citation) {
                lines.append("Date: \(date)")
            }
            let excerpt = citation.excerpt.trimmingCharacters(in: .whitespacesAndNewlines)
            if !excerpt.isEmpty {
                lines.append("Excerpt: \(excerpt)")
            }
            return lines.joined(separator: "\n")
        }
        .joined(separator: "\n\n")
        sections.append("Citations:\n\(citationText)")
    }

    return sections.joined(separator: "\n\n")
}

private func askCitationSourceLabel(_ citation: AskCitationItem) -> String {
    let sourceURL = citation.source_url?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    return sourceURL.isEmpty ? citation.source : sourceURL
}

private func askCitationDate(_ citation: AskCitationItem) -> String? {
    let rawDate = (citation.occurred_at ?? citation.captured_at)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    guard !rawDate.isEmpty else { return nil }
    return String(rawDate.prefix(10))
}
