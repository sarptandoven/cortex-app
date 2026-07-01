import SwiftUI

struct OnboardingView: View {
    @ObservedObject var state: AppState
    private let steps = OnboardingStep.allCases

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            ScrollView {
                stepContent
                    .padding(24)
                    .frame(maxWidth: 620, alignment: .leading)
                    .frame(maxWidth: .infinity)
            }
            Divider()
            footer
        }
        .background(CortexDesign.appBackground)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 7) {
                        Image(systemName: "brain.head.profile")
                            .foregroundColor(.accentColor)
                        Text("Cortex first run")
                            .font(.caption)
                            .fontWeight(.semibold)
                            .foregroundColor(.secondary)
                    }
                    Text(state.onboardingStep.title)
                        .font(.title2)
                        .fontWeight(.semibold)
                    Text(state.onboardingStep.subtitle)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                Button {
                    state.dismissOnboardingForSession()
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.borderless)
                .help("Finish later")
            }

            HStack(spacing: 6) {
                ForEach(steps) { step in
                    Capsule()
                        .fill(progressColor(for: step))
                        .frame(height: 4)
                }
            }
        }
        .padding(22)
    }

    private func progressColor(for step: OnboardingStep) -> Color {
        if state.onboardingStepIsComplete(step) {
            return .accentColor
        }
        if state.onboardingStep == step {
            return .accentColor.opacity(0.55)
        }
        return Color(nsColor: .separatorColor).opacity(0.55)
    }

    private var footer: some View {
        HStack {
            Button {
                state.previousOnboardingStep()
            } label: {
                Label("Back", systemImage: "chevron.left")
            }
            .disabled(state.onboardingStep == .privateVault)
            .controlSize(.large)

            Spacer()

            Text(state.displayStatus)
                .font(.caption)
                .foregroundColor(.secondary)
                .lineLimit(1)
                .frame(maxWidth: 260)

            Spacer()

            if state.onboardingStep == steps.last {
                Button {
                    state.finishOnboarding()
                } label: {
                    Label(
                        state.canCompleteOnboarding ? "Finish" : "Finish Later",
                        systemImage: state.canCompleteOnboarding ? "checkmark.circle" : "arrow.right.circle"
                    )
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            } else {
                Button {
                    state.nextOnboardingStep()
                } label: {
                    Label(continueButtonTitle, systemImage: state.canAdvanceOnboarding ? "chevron.right" : continueButtonIcon)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(!state.canAdvanceOnboarding)
            }
        }
        .padding(18)
    }

    private var continueButtonTitle: String {
        guard !state.canAdvanceOnboarding else { return "Continue" }
        switch state.onboardingStep {
        case .privateVault:
            return "Waiting for Service"
        case .firstSource:
            return "Choose Notes"
        case .reviewMemory:
            return "Review One Item"
        case .askUse:
            return "Ask with Citations"
        }
    }

    private var continueButtonIcon: String {
        switch state.onboardingStep {
        case .privateVault:
            return "clock"
        case .firstSource:
            return "arrow.triangle.2.circlepath"
        case .reviewMemory:
            return "checklist"
        case .askUse:
            return "quote.bubble"
        }
    }

    @ViewBuilder
    private var stepContent: some View {
        switch state.onboardingStep {
        case .privateVault:
            OnboardingVaultStep(state: state)
        case .firstSource:
            OnboardingFirstSourceStep(state: state)
        case .reviewMemory:
            OnboardingReviewMemoryStep(state: state)
        case .askUse:
            OnboardingAskUseStep(state: state)
        }
    }
}

struct OnboardingStepRow: View {
    let step: OnboardingStep
    let selected: Bool
    let completed: Bool

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: completed ? "checkmark.circle.fill" : step.systemImage)
                .frame(width: 18)
            Text(step.title)
            Spacer()
        }
        .font(.caption)
        .fontWeight(selected ? .semibold : .regular)
        .foregroundColor(selected || completed ? .accentColor : .secondary)
        .padding(.horizontal, 9)
        .padding(.vertical, 7)
        .background((selected ? Color.accentColor.opacity(0.12) : CortexDesign.panelBackground))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct OnboardingVaultStep: View {
    @ObservedObject var state: AppState
    @State private var vaultLocationExpanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Your memory stays on this Mac. Cortex builds a private index, sends new memory to Review, and only uses memory after you approve it.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            if state.diagnostics?.vault != nil {
                OnboardingCheckRow(title: "Private memory ready", detail: "Next, connect notes so Cortex can start the review loop.", systemImage: "checkmark.seal.fill", color: .green)
            } else {
                OnboardingCheckRow(title: "Starting private memory", detail: state.displayBackendStatus, systemImage: "clock", color: .orange)
            }

            DisclosureGroup("Local details", isExpanded: $vaultLocationExpanded) {
                VStack(alignment: .leading, spacing: 10) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Memory folder")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Text(state.vaultPath)
                            .font(.system(.caption, design: .monospaced))
                            .lineLimit(2)
                            .textSelection(.enabled)
                    }
                    HStack {
                        Button {
                            state.useDefaultVaultFolder()
                        } label: {
                            Label("Use Default", systemImage: "house")
                        }
                        Button {
                            state.chooseVaultFolder()
                        } label: {
                            Label("Change", systemImage: "folder")
                        }
                        Button {
                            state.openVaultFolder()
                        } label: {
                            Label("Reveal", systemImage: "arrow.up.right.square")
                        }
                        Spacer()
                    }
                    if let vault = state.diagnostics?.vault {
                        VStack(alignment: .leading, spacing: 8) {
                            OnboardingCheckRow(title: "Local folder", detail: vault.path, systemImage: "externaldrive", color: .secondary)
                            OnboardingCheckRow(title: "Local index", detail: vault.index_path, systemImage: "bolt.horizontal.circle.fill", color: .accentColor)
                            OnboardingCheckRow(title: "Activity log", detail: "\(vault.event_count) events", systemImage: "list.bullet.rectangle", color: .secondary)
                        }
                    }
                }
                .padding(.top, 6)
            }
            .padding(12)
            .background(CortexDesign.panelBackground)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }
}

struct OnboardingFirstSourceStep: View {
    @ObservedObject var state: AppState

    private var obsidianConnector: SourceConnectorCatalogItem? {
        state.sourceConnectorCatalog.first { $0.id == "obsidian" }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Choose a notes folder once. Cortex syncs Markdown locally, sends useful memory to Review, and keeps syncing after first run.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            OnboardingConnectionCard(
                title: state.hasConnectedObsidianVault ? "Notes connected" : "Connect notes",
                detail: state.hasConnectedObsidianVault ? "Cortex syncs saved notes on launch and every 30 minutes, then sends new memory to Review with citations." : "Pick an Obsidian or Markdown folder. Cortex handles parsing, citations, and repeat sync automatically.",
                systemImage: state.onboardingHasSource ? "checkmark.seal.fill" : "folder.badge.plus",
                isPrimary: true,
                status: state.onboardingHasSource ? "Synced" : (state.hasConnectedObsidianVault ? "Connected" : "Local"),
                buttonTitle: state.hasConnectedObsidianVault ? "Check status" : "Connect notes"
            ) {
                if state.hasConnectedObsidianVault {
                    Task { await state.loadSourceConnectivity() }
                    state.status = "Checking notes"
                } else if let connector = obsidianConnector {
                    state.connectLocalNotesFolder(connector)
                } else {
                    Task { await state.loadSourceConnectivity() }
                    state.status = "Checking notes connector"
                }
            }

            if !state.onboardingFirstSourceNames.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Connected notes")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Text(state.onboardingFirstSourceNames.joined(separator: ", "))
                        .font(.caption)
                        .lineLimit(2)
                        .truncationMode(.middle)
                }
            }

            OnboardingCheckRow(
                title: connectionCheckTitle,
                detail: connectionCheckDetail,
                systemImage: state.onboardingHasSource ? "checkmark.seal.fill" : "link.circle",
                color: state.onboardingHasSource ? .green : (state.onboardingHasConnectedMemoryLayer ? .orange : .secondary)
            )
        }
        .task {
            await state.loadTrust()
            if state.sourceConnectorCatalog.isEmpty {
                await state.loadSourceConnectivity()
            }
        }
    }

    private var connectionCheckTitle: String {
        if state.onboardingHasSource {
            return "Memory layer synced"
        }
        if state.onboardingHasConnectedMemoryLayer {
            return "Waiting for synced memory"
        }
        return "Connect notes"
    }

    private var connectionCheckDetail: String {
        if state.onboardingHasSource {
            return "Review has memory from connected notes."
        }
        if state.onboardingHasConnectedMemoryLayer {
            return "Cortex is checking notes so useful memory appears in Review."
        }
        return "Connect notes from Home when ready."
    }
}

struct OnboardingConnectionCard: View {
    let title: String
    let detail: String
    let systemImage: String
    let isPrimary: Bool
    let status: String?
    let buttonTitle: String
    let action: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Image(systemName: systemImage)
                    .font(.title2)
                Spacer()
                if let status {
                    Text(status)
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(.accentColor)
                        .padding(.horizontal, 9)
                        .padding(.vertical, 5)
                        .background(Color.accentColor.opacity(0.10))
                        .clipShape(Capsule())
                }
            }
            Text(title)
                .font(.headline)
                .fontWeight(.semibold)
            Text(detail)
                .font(.callout)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            actionButton
        }
        .padding(16)
        .frame(maxWidth: .infinity, minHeight: 186, alignment: .topLeading)
        .foregroundColor(.primary)
        .background(isPrimary ? CortexDesign.panelBackground : CortexDesign.cardBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke((isPrimary ? Color.accentColor : Color(nsColor: .separatorColor)).opacity(isPrimary ? 0.32 : 0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    @ViewBuilder
    private var actionButton: some View {
        if isPrimary {
            Button(action: action) {
                Label(buttonTitle, systemImage: "link.circle")
                    .frame(maxWidth: .infinity, minHeight: 46)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
        } else {
            Button(action: action) {
                Label(buttonTitle, systemImage: "ellipsis.circle")
                    .frame(maxWidth: .infinity, minHeight: 42)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
        }
    }
}

struct OnboardingReviewMemoryStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Review one synced item before Cortex can use it. Approve only memory with enough context to cite later.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            if state.inbox.isEmpty {
                QuietState(title: "No pending memory", detail: emptyReviewDetail)
            } else {
                ForEach(state.inbox.prefix(2)) { capture in
                    ReviewCaptureCard(
                        capture: capture,
                        approve: { state.approveCapture(capture) },
                        archive: { state.archiveCapture(capture) }
                    )
                }
            }

            HStack {
                Button {
                    Task {
                        await state.loadInbox()
                        await state.loadReview()
                        await state.loadStats()
                    }
                } label: {
                    Label("Refresh Review", systemImage: "arrow.clockwise")
                }
                Button {
                    state.selectedTab = .review
                    state.dismissOnboardingForSession()
                } label: {
                    Label("Open Full Review", systemImage: "checklist")
                }
                Spacer()
            }

            OnboardingCheckRow(
                title: reviewPathTitle,
                detail: reviewPathDetail,
                systemImage: state.onboardingHasReviewedMemory ? "checkmark.seal.fill" : "tray.full",
                color: state.onboardingHasReviewedMemory ? .green : .orange
            )
        }
        .task {
            await state.loadInbox()
            await state.loadReview()
            await state.loadStats()
        }
    }

    private var emptyReviewDetail: String {
        if state.onboardingHasReviewedMemory {
            return "You already reviewed memory from your first connection."
        }
        if state.onboardingHasSource {
            return "No reviewable memory is waiting yet. Let notes sync finish, then approve one useful item."
        }
        return "Connect notes first; synced memory appears here before Cortex uses it."
    }

    private var reviewPathTitle: String {
        if state.onboardingHasReviewedMemory {
            return "Memory reviewed"
        }
        return state.onboardingHasSource ? "Approve one memory" : "Sync memory first"
    }

    private var reviewPathDetail: String {
        if state.onboardingHasReviewedMemory {
            return "Cortex has approved memory it can cite."
        }
        if state.onboardingHasSource {
            return "Approve one useful memory to let Cortex cite it in Ask."
        }
        return "Review unlocks after connected notes sync memory."
    }
}

struct OnboardingAskUseStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Ask is the proof loop: approved memory should produce an answer with citations.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 10) {
                TextField("Ask about an approved memory", text: $state.searchQuery)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { state.runSearch() }
                HStack {
                    Button {
                        state.runSearch()
                    } label: {
                        Label("Ask Cortex", systemImage: "magnifyingglass")
                    }
                    .buttonStyle(.borderedProminent)
                    Spacer()
                }
            }
            .padding(12)
            .background(CortexDesign.panelBackground)
            .clipShape(RoundedRectangle(cornerRadius: 8))

            if !state.onboardingAskSuggestions.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Try a question")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    ForEach(state.onboardingAskSuggestions, id: \.self) { suggestion in
                        Button {
                            state.searchQuery = suggestion
                            state.runSearch()
                        } label: {
                            Label(suggestion, systemImage: "sparkle.magnifyingglass")
                                .lineLimit(2)
                                .truncationMode(.tail)
                        }
                        .buttonStyle(.bordered)
                    }
                }
            }

            if state.hasSearched && !state.askAnswer.isEmpty {
                AskAnswerPanel(answer: state.askAnswer, citations: state.askCitations)
            }

            if !state.searchResults.isEmpty {
                ForEach(state.searchResults.prefix(2)) { item in
                    MemoryCard(item: item)
                }
            } else {
                QuietState(title: askEmptyTitle, detail: askEmptyDetail)
            }

            OnboardingCheckRow(
                title: askPathTitle,
                detail: askPathDetail,
                systemImage: state.onboardingHasUsedCortex ? "checkmark.seal.fill" : "sparkle.magnifyingglass",
                color: state.onboardingHasUsedCortex ? .green : .orange
            )
        }
    }

    private var askEmptyTitle: String {
        state.hasSearched ? "No cited answer yet" : "Ask approved memory once"
    }

    private var askEmptyDetail: String {
        if state.hasSearched {
            return "Try an exact phrase from approved memory, or go back to Review and approve one useful item."
        }
        return "Ask about approved memory from notes. First run finishes after Cortex returns a cited answer."
    }

    private var askPathTitle: String {
        if state.onboardingHasUsedCortex {
            return "Cortex used once"
        }
        if state.onboardingHasReviewedMemory {
            return "Ask once with citations"
        }
        return state.onboardingHasSource ? "Review memory first" : "Ask later"
    }

    private var askPathDetail: String {
        if state.onboardingHasUsedCortex {
            return "Approved memory was used in a cited answer."
        }
        if state.onboardingHasReviewedMemory {
            return "Run Ask once. First run finishes after Cortex returns a cited answer."
        }
        if state.onboardingHasSource {
            return "Ask becomes useful after one memory is approved in Review."
        }
        return "Ask becomes useful after approved memory exists."
    }
}

struct OnboardingCheckRow: View {
    let title: String
    let detail: String
    let systemImage: String
    let color: Color

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: systemImage)
                .foregroundColor(color)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .fontWeight(.medium)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
            }
            Spacer()
        }
    }
}
