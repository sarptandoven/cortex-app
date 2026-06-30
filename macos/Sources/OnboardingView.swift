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
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
                Image(systemName: "brain.head.profile")
                    .font(.system(size: 28, weight: .semibold))
                    .foregroundColor(.accentColor)
                Text("Cortex")
                    .font(.title2)
                    .fontWeight(.semibold)
                Text("Private memory on this Mac")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            VStack(alignment: .leading, spacing: 8) {
                ForEach(steps) { step in
                    OnboardingStepRow(
                        step: step,
                        selected: state.onboardingStep == step,
                        completed: state.onboardingStepIsComplete(step)
                    )
                }
            }

            Spacer()

            HStack(spacing: 6) {
                Image(systemName: "lock.shield")
                Text("Local-first")
            }
            .font(.caption)
            .foregroundColor(.secondary)
            .padding(.horizontal, 8)
            .padding(.vertical, 5)
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .padding(20)
        .frame(minWidth: 190, maxWidth: 190, maxHeight: .infinity, alignment: .topLeading)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.55))
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 7) {
                        Image(systemName: "brain.head.profile")
                            .foregroundColor(.accentColor)
                        Text("Cortex setup")
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
                .help("Finish setup later")
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

    private var legacyHeader: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 5) {
                Text("Step \(state.onboardingStepIndex + 1) of \(steps.count)")
                    .font(.caption)
                    .foregroundColor(.secondary)
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
                Label("Skip Setup for Now", systemImage: "xmark")
            }
        }
        .padding(22)
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
                        state.canCompleteOnboarding ? "Start Cortex" : "Finish Later",
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
                return "Connect Cortex"
            case .reviewMemory:
                return "Approve Memory"
            case .askUse:
                return "Ask Cortex"
            }
    }

    private var continueButtonIcon: String {
        switch state.onboardingStep {
        case .privateVault:
            return "clock"
        case .firstSource:
            return "link.circle"
        case .reviewMemory:
            return "checkmark.circle"
        case .askUse:
            return "sparkle.magnifyingglass"
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
        .background((selected ? Color.accentColor.opacity(0.12) : Color(nsColor: .controlBackgroundColor)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct OnboardingVaultStep: View {
    @ObservedObject var state: AppState
    @State private var vaultLocationExpanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Cortex starts with a private memory folder on this Mac, then builds memory from connected notes, accounts, and AI tools. Setup stays focused on getting useful context flowing.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 8) {
                Text("Memory folder")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Text(state.vaultPath)
                    .font(.system(.caption, design: .monospaced))
                    .lineLimit(2)
                    .textSelection(.enabled)
                DisclosureGroup("Advanced memory folder", isExpanded: $vaultLocationExpanded) {
                    HStack {
                        Button {
                            state.useDefaultVaultFolder()
                        } label: {
                            Label("Use Default Location", systemImage: "house")
                        }
                        Button {
                            state.chooseVaultFolder()
                        } label: {
                            Label("Change Location", systemImage: "folder")
                        }
                        Button {
                            state.openVaultFolder()
                        } label: {
                            Label("Reveal Location", systemImage: "arrow.up.right.square")
                        }
                        Spacer()
                    }
                    .padding(.top, 6)
                }
            }
            .padding(12)
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            if let vault = state.diagnostics?.vault {
                VStack(alignment: .leading, spacing: 8) {
                    OnboardingCheckRow(title: "Memory folder ready", detail: vault.path, systemImage: "checkmark.seal.fill", color: .green)
                    OnboardingCheckRow(title: "Memory index", detail: vault.index_path, systemImage: "bolt.horizontal.circle.fill", color: .accentColor)
                    OnboardingCheckRow(title: "Activity log", detail: "\(vault.event_count) events", systemImage: "list.bullet.rectangle", color: .secondary)
                }
            } else {
                OnboardingCheckRow(title: "Starting local backend", detail: state.displayBackendStatus, systemImage: "clock", color: .orange)
            }
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
            Text("Connect one real source. For this beta, the most useful paths are Obsidian notes and local AI tools that send useful memory into Review.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 240), spacing: 12)], spacing: 12) {
                OnboardingConnectionCard(
                    title: "Connect AI tools",
                    detail: "Claude Desktop, Cursor, Windsurf, and other local AI tools can read approved memory and send new memory into Review.",
                    systemImage: "wand.and.stars",
                    isPrimary: true,
                    status: state.connectedAIIntegrationCount > 0 ? "\(state.connectedAIIntegrationCount) connected" : "Ready",
                    buttonTitle: state.connectedAIIntegrationCount > 0 ? "Manage" : "Connect"
                ) {
                    state.openConnectionsPrivacy(statusMessage: "Connect local AI tools")
                    state.dismissOnboardingForSession()
                }

                OnboardingConnectionCard(
                    title: state.hasConnectedObsidianVault ? "Obsidian connected" : "Connect Obsidian",
                    detail: state.hasConnectedObsidianVault ? "Cortex syncs the saved notes automatically on launch and periodically." : "Choose an Obsidian folder once. Cortex reads notes locally, cleans Markdown, and preserves citations.",
                    systemImage: state.hasConnectedObsidianVault ? "checkmark.seal.fill" : "folder.badge.plus",
                    isPrimary: false,
                    status: state.hasConnectedObsidianVault ? "Connected" : "Local",
                    buttonTitle: state.hasConnectedObsidianVault ? "Connected" : "Connect notes"
                ) {
                    if state.hasConnectedObsidianVault {
                        state.status = "Obsidian will sync automatically"
                    } else if let connector = obsidianConnector {
                        state.connectLocalNotesFolder(connector)
                    } else {
                        Task { await state.loadSourceConnectivity() }
                        state.status = "Checking Obsidian connector"
                    }
                }
            }

            if !state.onboardingFirstSourceNames.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Connected path")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Text(state.onboardingFirstSourceNames.joined(separator: ", "))
                        .font(.caption)
                        .lineLimit(2)
                        .truncationMode(.middle)
                }
            }

            OnboardingCheckRow(
                title: state.onboardingHasSource ? "Connection ready" : "Waiting for one connection",
                detail: state.onboardingHasSource ? "Setup can continue. New memory will appear in Review when a connected source or tool saves context." : "Connect Obsidian notes, or use a connected AI tool to send useful memory into Review.",
                systemImage: state.onboardingHasSource ? "checkmark.seal.fill" : "link.circle",
                color: state.onboardingHasSource ? .green : .orange
            )
        }
        .task {
            await state.loadTrust()
            if state.sourceConnectorCatalog.isEmpty {
                await state.loadSourceConnectivity()
            }
        }
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
                        .padding(.horizontal, 9)
                        .padding(.vertical, 5)
                        .background((isPrimary ? Color.white : Color.accentColor).opacity(0.16))
                        .clipShape(Capsule())
                }
            }
            Text(title)
                .font(.headline)
                .fontWeight(.semibold)
            Text(detail)
                .font(.callout)
                .foregroundColor(isPrimary ? .white.opacity(0.86) : .secondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            actionButton
        }
        .padding(16)
        .frame(maxWidth: .infinity, minHeight: 186, alignment: .topLeading)
        .foregroundColor(isPrimary ? .white : .primary)
        .background(isPrimary ? Color.accentColor : Color(nsColor: .controlBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(isPrimary ? Color.clear : Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    @ViewBuilder
    private var actionButton: some View {
        if isPrimary {
            Button(action: action) {
                Label(buttonTitle, systemImage: "link.circle")
                    .frame(maxWidth: .infinity, minHeight: 42)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
        } else {
            Button(action: action) {
                Label(buttonTitle, systemImage: "folder.badge.plus")
                    .frame(maxWidth: .infinity, minHeight: 42)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
        }
    }
}

struct OnboardingReviewMemoryStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Review is the safety layer. When connected sources or tools produce memory candidates, approve only what Cortex should remember.")
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
            return "No reviewable memory is waiting yet. New source or tool memory will land here before Cortex uses it."
        }
        return "Connect Obsidian notes first, or send useful memory from a connected AI tool. Anything useful will appear here before Cortex remembers it."
    }

    private var reviewPathTitle: String {
        if state.onboardingHasReviewedMemory {
            return "Memory reviewed"
        }
        return state.onboardingHasSource ? "Approve one memory" : "Connect Cortex first"
    }

    private var reviewPathDetail: String {
        if state.onboardingHasReviewedMemory {
            return "Cortex has approved memory it can cite."
        }
        if state.onboardingHasSource {
            return "Approve one useful memory to let Cortex cite it in Ask."
        }
        return "Connect notes or send useful memory from a connected AI tool before Review can receive memory."
    }
}

struct OnboardingAskUseStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Ask is where approved memory becomes useful. It answers with citations once Review has accepted memory.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 10) {
                TextField("Ask about an approved memory or exact phrase", text: $state.searchQuery)
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
            .background(Color(nsColor: .controlBackgroundColor))
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
                    MemoryCard(item: item) {
                        state.deleteMemory(item)
                    }
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
        state.hasSearched ? "No cited answer yet" : "Use approved context once"
    }

    private var askEmptyDetail: String {
        if state.hasSearched {
            return "Try an exact phrase from approved memory, or go back to Review and approve one useful item."
        }
        return "Ask about connected memory. Cortex answers with citations when approved memory matches."
    }

    private var askPathTitle: String {
        if state.onboardingHasUsedCortex {
            return "Cortex used once"
        }
        if state.onboardingHasReviewedMemory {
            return "Ask once with citations"
        }
        return state.onboardingHasSource ? "Review memory first" : "Connect Cortex first"
    }

    private var askPathDetail: String {
        if state.onboardingHasUsedCortex {
            return "Approved memory was used in a cited answer."
        }
        if state.onboardingHasReviewedMemory {
            return "Run Ask once. Setup finishes after Cortex returns a cited answer."
        }
        if state.onboardingHasSource {
            return "Ask becomes useful after one memory is approved in Review."
        }
        return "Connect notes or send useful memory from a connected AI tool before Ask can cite memory."
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
