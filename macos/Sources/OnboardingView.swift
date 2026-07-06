import SwiftUI

struct OnboardingView: View {
    @ObservedObject var state: AppState
    private let steps = OnboardingStep.allCases
    @State private var celebrating = false

    var body: some View {
        ZStack {
            VStack(spacing: 0) {
                header
                Divider()
                ScrollView {
                    stepContent
                        .padding(24)
                        .frame(maxWidth: 640, alignment: .leading)
                        .frame(maxWidth: .infinity)
                        .id(state.onboardingStep)
                        .transition(.asymmetric(
                            insertion: .move(edge: .trailing).combined(with: .opacity),
                            removal: .move(edge: .leading).combined(with: .opacity)
                        ))
                }
                .animation(.easeInOut(duration: 0.32), value: state.onboardingStep)
                Divider()
                footer
            }

            if celebrating {
                celebrationOverlay
                    .transition(.opacity)
            }
        }
        .background(CortexDesign.appBackground)
    }

    /// A brief full-panel beat acknowledging a completed setup before the sheet closes — and the
    /// one thing worth remembering (the ⌃⌥Space hotkey) gets its moment.
    private var celebrationOverlay: some View {
        VStack(spacing: 14) {
            OnboardingHeroMark(systemImage: "checkmark.seal.fill", tint: .green)
            Text("You're all set")
                .font(.system(size: 24, weight: .bold))
            Text("Ask anytime — press ⌃⌥Space or click the brain in your menu bar.")
                .font(.callout)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(CortexDesign.appBackground.opacity(0.97))
    }

    /// Finish with everything complete earns a short celebration before the sheet closes; the
    /// "Finish Later" path stays instant.
    private func finishTapped() {
        guard !celebrating else { return }
        guard state.canCompleteOnboarding else {
            state.finishOnboarding()
            return
        }
        withAnimation(.spring(response: 0.4, dampingFraction: 0.75)) { celebrating = true }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
            state.finishOnboarding()
        }
    }

    private var stepNumber: Int {
        (steps.firstIndex(of: state.onboardingStep) ?? 0) + 1
    }

    private var heroTint: Color {
        state.onboardingStepIsComplete(state.onboardingStep) ? .green : .accentColor
    }

    /// Review and Ask are explicitly skippable — mirror the footer's "Skip for now" affordance in
    /// the header so the step count doesn't read as five mandatory gates.
    private var currentStepIsOptional: Bool {
        (state.onboardingStep == .reviewMemory || state.onboardingStep == .askUse)
            && !state.onboardingStepIsComplete(state.onboardingStep)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                HStack(spacing: 7) {
                    Image(systemName: "sparkles")
                        .foregroundColor(.accentColor)
                    Text("Welcome to Cortex")
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(.secondary)
                }
                Spacer()
                Text("Step \(stepNumber) of \(steps.count)")
                    .font(.caption)
                    .foregroundColor(.secondary)
                if currentStepIsOptional {
                    // Label the skippable steps up front so "Skip for now" reads as legitimate,
                    // not like giving up — only three of the five steps are actually required.
                    Text("Optional")
                        .font(.caption2)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.accent)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 2)
                        .background(Capsule().fill(CortexDesign.accentSoft))
                }
                Button {
                    state.dismissOnboardingForSession()
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.borderless)
                .help("Finish later")
                .accessibilityLabel("Finish setup later")
            }

            HStack(alignment: .center, spacing: 18) {
                OnboardingHeroMark(systemImage: state.onboardingStep.systemImage, tint: heroTint)
                VStack(alignment: .leading, spacing: 6) {
                    Text(state.onboardingStep.headline)
                        .font(.system(size: 25, weight: .bold))
                        .fixedSize(horizontal: false, vertical: true)
                        .id("title-\(state.onboardingStep.rawValue)")
                        .transition(.opacity.combined(with: .move(edge: .trailing)))
                    Text(state.onboardingStep.subtitle)
                        .font(.title3)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .id("sub-\(state.onboardingStep.rawValue)")
                        .transition(.opacity)
                }
                Spacer(minLength: 0)
            }

            HStack(spacing: 6) {
                ForEach(steps) { step in
                    Capsule()
                        .fill(progressColor(for: step))
                        .frame(height: state.onboardingStep == step ? 6 : 4)
                }
            }
            .animation(.spring(response: 0.4, dampingFraction: 0.72), value: state.onboardingStep)
        }
        .padding(24)
        .animation(.easeInOut(duration: 0.32), value: state.onboardingStep)
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
                    finishTapped()
                } label: {
                    Label(
                        state.canCompleteOnboarding ? "Finish" : "Finish Later",
                        systemImage: state.canCompleteOnboarding ? "checkmark.circle" : "arrow.right.circle"
                    )
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(celebrating)
            } else {
                Button {
                    state.nextOnboardingStep()
                } label: {
                    Label(footerAdvanceTitle, systemImage: footerAdvanceIcon)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(!state.canAdvanceOnboarding)
            }
        }
        .padding(18)
    }

    /// True when the current step can be advanced but hasn't been completed — i.e. the user is
    /// choosing to move on without finishing an optional step (Review / Ask). Drives the "Skip for
    /// now" affordance so the primary button is always meaningful instead of a dead, disabled state.
    private var footerAdvanceIsSkip: Bool {
        state.canAdvanceOnboarding
            && !state.onboardingStepIsComplete(state.onboardingStep)
            && (state.onboardingStep == .reviewMemory || state.onboardingStep == .askUse)
    }

    private var footerAdvanceTitle: String {
        footerAdvanceIsSkip ? "Skip for now" : continueButtonTitle
    }

    private var footerAdvanceIcon: String {
        if footerAdvanceIsSkip { return "arrow.right" }
        return state.canAdvanceOnboarding ? "chevron.right" : continueButtonIcon
    }

    private var continueButtonTitle: String {
        guard !state.canAdvanceOnboarding else { return "Continue" }
        switch state.onboardingStep {
        case .privateVault:
            return "Starting Cortex"
        case .firstSource:
            return "Choose Source"
        case .reviewMemory:
            return "Review One Item"
        case .askUse:
            return "Ask with Citations"
        case .trustBackup:
            return "Back Up or Skip"
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
        case .trustBackup:
            return "archivebox"
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
        case .trustBackup:
            OnboardingBackupStep(state: state)
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

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Your memory stays on this Mac. Cortex keeps a private index, sends useful items to Review, and only uses memory after you approve it.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            OnboardingHowTo(
                title: OnboardingStep.privateVault.howToTitle,
                steps: OnboardingStep.privateVault.howToSteps,
                initiallyExpanded: true
            )

            if state.isLocalServiceReady {
                OnboardingCheckRow(title: "Private memory ready", detail: "Next, connect a source so Cortex can start finding useful memory.", systemImage: "checkmark.seal.fill", color: .green)
            } else if state.backendNeedsRecovery {
                OnboardingBackendRecoveryCard(state: state)
            } else {
                OnboardingCheckRow(title: "Starting private memory", detail: state.displayBackendStatus, systemImage: "clock", color: .orange)
            }
        }
    }
}

struct OnboardingBackendRecoveryCard: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            OnboardingCheckRow(
                title: "Memory engine needs a hand",
                detail: state.displayBackendStatus,
                systemImage: "exclamationmark.triangle.fill",
                color: .orange
            )
            Text("Cortex couldn't finish starting its private memory engine. This is usually temporary — try again, and if it keeps happening the log helps us fix it.")
                .font(.caption)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 10) {
                Button {
                    state.retryBackendStart()
                } label: {
                    if state.backendRetryInProgress {
                        Label("Starting…", systemImage: "arrow.triangle.2.circlepath")
                    } else {
                        Label("Try Again", systemImage: "arrow.clockwise")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(state.backendRetryInProgress)

                Button {
                    state.revealBackendLog()
                } label: {
                    Label("Show Log", systemImage: "doc.text.magnifyingglass")
                }
                .buttonStyle(.bordered)
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
            Text("Connect one memory source first. Cortex syncs it privately, sends useful memory to Review, and makes approved memory available to Ask with citations.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            OnboardingHowTo(
                title: OnboardingStep.firstSource.howToTitle,
                steps: OnboardingStep.firstSource.howToSteps
            )

            OnboardingConnectionCard(
                title: sourceCardTitle,
                detail: sourceCardDetail,
                systemImage: sourceCardIcon,
                isPrimary: true,
                status: sourceCardStatus,
                buttonTitle: firstSourceButtonTitle,
                buttonSystemImage: firstSourceButtonIcon
            ) {
                runFirstSourceAction()
            }

            if !state.onboardingFirstSourceNames.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Synced source")
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
                color: sourceCheckColor
            )
        }
        .task {
            await state.loadTrust()
            if state.sourceConnectorCatalog.isEmpty {
                await state.loadSourceConnectivity()
            }
        }
    }

    private var sourceCardTitle: String {
        if state.onboardingHasSource {
            return "Source ready"
        }
        if state.onboardingHasConnectedMemoryLayer {
            return "Source connected"
        }
        return "Connect notes"
    }

    private var sourceCardDetail: String {
        if state.onboardingHasSource {
            return "Cortex found fresh, usable memory from the connected source. Review one useful item next."
        }
        if let message = state.onboardingSourceHealthMessage {
            return message
        }
        if state.hasConnectedObsidianVault {
            return "Cortex checks connected notes on launch and every 30 minutes, then sends new memory to Review with citations."
        }
        return "Choose a local notes folder to start. Cortex will keep it synced and send useful memory to Review."
    }

    private var sourceCardIcon: String {
        if state.onboardingHasSource {
            return "checkmark.seal.fill"
        }
        if state.onboardingHasConnectedMemoryLayer {
            return "exclamationmark.circle"
        }
        return "folder.badge.plus"
    }

    private var sourceCardStatus: String {
        if state.onboardingHasSource {
            return "Fresh"
        }
        if state.onboardingSourceHealthMessage != nil {
            return "Check"
        }
        if state.onboardingHasConnectedMemoryLayer {
            return "Waiting"
        }
        return "Local"
    }

    private var sourceCheckColor: Color {
        if state.onboardingHasSource {
            return .green
        }
        if state.onboardingHasConnectedMemoryLayer {
            return .orange
        }
        return .secondary
    }

    private var connectionCheckTitle: String {
        if state.onboardingHasSource {
            return "Source synced"
        }
        if state.onboardingHasConnectedMemoryLayer {
            return "Source not ready yet"
        }
        return "Connect a source"
    }

    private var connectionCheckDetail: String {
        if state.onboardingHasSource {
            return "Review has fresh, citable memory from your source."
        }
        if let message = state.onboardingSourceHealthMessage {
            return message
        }
        if state.onboardingHasConnectedMemoryLayer {
            return "Cortex is checking source health so useful memory appears in Review."
        }
        return "Choose notes when ready. Reviewed memory becomes available to Ask with citations."
    }

    private var firstSourceButtonTitle: String {
        if state.onboardingHasConnectedMemoryLayer, !state.hasConnectedObsidianVault {
            return "Open Connections"
        }
        if state.notesNeedContent { return "Choose notes" }
        if state.hasConnectedObsidianVault { return "Sync notes" }
        if obsidianConnector != nil { return "Connect notes" }
        return "Refresh"
    }

    private var firstSourceButtonIcon: String {
        if state.onboardingHasConnectedMemoryLayer, !state.hasConnectedObsidianVault {
            return "link.circle"
        }
        if state.notesNeedContent { return "folder.badge.questionmark" }
        if state.hasConnectedObsidianVault { return "arrow.triangle.2.circlepath" }
        if obsidianConnector != nil { return "folder.badge.plus" }
        return "arrow.clockwise"
    }

    private func runFirstSourceAction() {
        if state.onboardingHasConnectedMemoryLayer, !state.hasConnectedObsidianVault {
            state.openConnectionsPrivacy(statusMessage: "Check source health")
            return
        }
        if let connector = obsidianConnector {
            state.connectLocalNotesFolder(connector, chooseNew: state.notesNeedContent)
        } else {
            // No local-notes connector in the catalog yet: don't leave the user with a button that
            // only refreshes. Open Connections so they always have a concrete way to pick a source.
            state.openConnectionsPrivacy(statusMessage: "Choose a source to connect")
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
    let buttonSystemImage: String
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
                Label(buttonTitle, systemImage: buttonSystemImage)
                    .frame(maxWidth: .infinity, minHeight: 46)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
        } else {
            Button(action: action) {
                Label(buttonTitle, systemImage: buttonSystemImage)
                    .frame(maxWidth: .infinity, minHeight: 42)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
        }
    }
}

struct OnboardingReviewMemoryStep: View {
    @ObservedObject var state: AppState

    private var obsidianConnector: SourceConnectorCatalogItem? {
        state.sourceConnectorCatalog.first { $0.id == "obsidian" }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Review one synced item before Cortex can use it. Approve only memory with a clear citation.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            OnboardingHowTo(
                title: OnboardingStep.reviewMemory.howToTitle,
                steps: OnboardingStep.reviewMemory.howToSteps
            )

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

            reviewActions

            OnboardingCheckRow(
                title: reviewPathTitle,
                detail: reviewPathDetail,
                systemImage: state.onboardingHasReviewedMemory ? "checkmark.seal.fill" : "tray.full",
                color: state.onboardingHasReviewedMemory ? .green : .orange
            )
        }
        .task {
            await state.loadSourceConnectivity()
            await state.loadInbox()
            await state.loadReview()
            await state.loadStats()
        }
    }

    @ViewBuilder
    private var reviewActions: some View {
        HStack {
            if state.inbox.isEmpty, state.hasConnectedObsidianVault, let connector = obsidianConnector {
                Button {
                    state.connectLocalNotesFolder(connector)
                } label: {
                    Label("Sync notes", systemImage: "arrow.triangle.2.circlepath")
                        .frame(minWidth: 158, minHeight: 42)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(state.isBusy)
            } else if state.inbox.isEmpty, !state.onboardingHasSource {
                Button {
                    state.previousOnboardingStep()
                } label: {
                    Label("Connect notes", systemImage: "folder.badge.plus")
                        .frame(minWidth: 146, minHeight: 42)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            } else {
                Button {
                    state.selectedTab = .review
                    state.dismissOnboardingForSession()
                } label: {
                    Label("Open Review", systemImage: "checklist")
                        .frame(minWidth: 132, minHeight: 42)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }

            Button {
                Task {
                    await state.loadSourceConnectivity()
                    await state.loadInbox()
                    await state.loadReview()
                    await state.loadStats()
                }
            } label: {
                Label("Check again", systemImage: "arrow.clockwise")
                    .frame(minWidth: 124, minHeight: 42)
            }
            .controlSize(.large)
            .disabled(state.isBusy)
            Spacer()
        }
    }

    private var emptyReviewDetail: String {
        if state.onboardingHasReviewedMemory {
            return "You already reviewed memory from your first connection."
        }
        if let message = state.onboardingSourceHealthMessage {
            return message
        }
        if state.onboardingHasSource {
            return "No reviewable memory is waiting yet. Let notes finish syncing, then approve one useful item."
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
            return "Cortex has reviewed memory it can cite."
        }
        if let message = state.onboardingSourceHealthMessage {
            return message
        }
        if state.onboardingHasSource {
            return "Approve one useful memory to let Cortex cite it in Ask."
        }
        return "Review unlocks after connected notes sync memory."
    }
}

struct OnboardingAskUseStep: View {
    @ObservedObject var state: AppState

    private var askStepReady: Bool {
        state.onboardingStepIsComplete(.askUse)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Cortex can now answer from reviewed notes with citations. Try a question now, or continue setup.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            OnboardingHowTo(
                title: OnboardingStep.askUse.howToTitle,
                steps: OnboardingStep.askUse.howToSteps
            )

            VStack(alignment: .leading, spacing: 10) {
                TextField("Ask about your reviewed notes", text: $state.searchQuery)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { state.runSearch() }
                HStack {
                    Button {
                        state.runSearch()
                    } label: {
                        Label("Ask Cortex", systemImage: "magnifyingglass")
                            .frame(minWidth: 150, minHeight: 44)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
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

            if let askError = state.askError {
                // A real engine/network failure must not masquerade as "no answer" — surface it with
                // a retry so the user doesn't conclude Ask is broken and abandon setup.
                VStack(alignment: .leading, spacing: 8) {
                    OnboardingCheckRow(
                        title: "Ask hit a problem",
                        detail: askError,
                        systemImage: "exclamationmark.triangle.fill",
                        color: .orange
                    )
                    Button {
                        state.runSearch()
                    } label: {
                        Label("Try again", systemImage: "arrow.clockwise")
                            .frame(minWidth: 120, minHeight: 40)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(state.isBusy)
                }
            } else if !state.searchResults.isEmpty {
                ForEach(state.searchResults.prefix(2)) { item in
                    MemoryCard(item: item)
                }
            } else {
                QuietState(title: askEmptyTitle, detail: askEmptyDetail)
            }

            OnboardingCheckRow(
                title: askPathTitle,
                detail: askPathDetail,
                systemImage: askStepReady ? "checkmark.seal.fill" : "sparkle.magnifyingglass",
                color: askStepReady ? .green : .orange
            )
        }
    }

    private var askEmptyTitle: String {
        state.hasSearched ? "No cited answer yet" : "Ask your notes"
    }

    private var askEmptyDetail: String {
        if state.hasSearched {
            return "Review new synced items or try a more specific question."
        }
        if state.onboardingHasReviewedMemory {
            return "This step is optional now that reviewed notes exist. Ask once to see citations before you continue."
        }
        if let message = state.onboardingSourceHealthMessage {
            return message
        }
        return "Ask becomes useful after reviewed notes exist."
    }

    private var askPathTitle: String {
        if state.onboardingHasUsedCortex {
            return "Cortex used once"
        }
        if state.onboardingHasReviewedMemory {
            return "Ready to ask"
        }
        return state.onboardingHasSource ? "Review memory first" : "Ask later"
    }

    private var askPathDetail: String {
        if state.onboardingHasUsedCortex {
            return "Reviewed notes were used in a cited answer."
        }
        if state.onboardingHasReviewedMemory {
            return "Ask is available now. Continue setup when ready."
        }
        if let message = state.onboardingSourceHealthMessage {
            return message
        }
        if state.onboardingHasSource {
            return "Ask becomes useful after one memory is approved in Review."
        }
        return "Ask becomes useful after reviewed notes exist."
    }
}

struct OnboardingBackupStep: View {
    @ObservedObject var state: AppState

    private var backupCount: Int {
        state.dataLifecycleReport?.backups.count ?? 0
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Local backups let you recover Cortex memory on this Mac. Create one now, or skip and do it later from Advanced settings.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            OnboardingHowTo(
                title: OnboardingStep.trustBackup.howToTitle,
                steps: OnboardingStep.trustBackup.howToSteps
            )

            HStack(alignment: .center, spacing: 12) {
                Button {
                    state.createBackup()
                } label: {
                    Label("Back Up Now", systemImage: "archivebox")
                        .frame(minWidth: 150, minHeight: 46)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(state.isBusy)

                Button {
                    state.skipFirstBackup()
                } label: {
                    Label("Skip for Now", systemImage: "clock")
                        .frame(minWidth: 132, minHeight: 46)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
                .disabled(state.isBusy)

                Spacer(minLength: 0)
            }

            if let backup = state.lastBackupPath {
                OnboardingCheckRow(
                    title: "Backup saved",
                    detail: backup,
                    systemImage: "checkmark.seal.fill",
                    color: .green
                )
            } else if state.onboardingBackupDecision == "skipped" {
                OnboardingCheckRow(
                    title: "Backup skipped for now",
                    detail: "You can create a local backup from Advanced settings before adding more notes.",
                    systemImage: "clock.fill",
                    color: .orange
                )
            } else if backupCount > 0 {
                OnboardingCheckRow(
                    title: "Backup already exists",
                    detail: "\(backupCount) local backup\(backupCount == 1 ? "" : "s") available.",
                    systemImage: "checkmark.seal.fill",
                    color: .green
                )
            } else {
                OnboardingCheckRow(
                    title: "Choose backup option",
                    detail: "Create a backup now, or explicitly skip this first backup.",
                    systemImage: "externaldrive.badge.exclamationmark",
                    color: .orange
                )
            }
        }
        .task {
            await state.loadReliability()
            await state.loadTrust()
        }
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

// MARK: - Animated intro components

/// An animated hero glyph for the onboarding header: a symbol resting inside softly-expanding
/// concentric rings, breathing gently. macOS 13 compatible (pure `withAnimation`/`repeatForever`,
/// no `symbolEffect`).
struct OnboardingHeroMark: View {
    let systemImage: String
    let tint: Color
    @State private var animate = false

    var body: some View {
        ZStack {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .stroke(tint.opacity(0.35), lineWidth: 1.5)
                    .frame(width: 66, height: 66)
                    .scaleEffect(animate ? 1.55 : 0.85)
                    .opacity(animate ? 0 : 0.6)
                    .animation(
                        .easeOut(duration: 2.6)
                            .repeatForever(autoreverses: false)
                            .delay(Double(index) * 0.85),
                        value: animate
                    )
            }
            Circle()
                .fill(tint.opacity(0.14))
                .frame(width: 66, height: 66)
            Image(systemName: systemImage)
                .font(.system(size: 27, weight: .semibold))
                .foregroundColor(tint)
                .scaleEffect(animate ? 1.05 : 0.95)
                .animation(.easeInOut(duration: 2).repeatForever(autoreverses: true), value: animate)
        }
        .frame(width: 96, height: 96)
        .onAppear { animate = true }
        .accessibilityHidden(true)
    }
}

/// A compact, numbered "here's exactly how" panel used in every onboarding step. Collapsible so
/// the help stays one click away without turning steps into walls of text — only the first step
/// starts expanded (there it IS the content).
struct OnboardingHowTo: View {
    let title: String
    let steps: [String]
    @State private var expanded: Bool

    init(title: String, steps: [String], initiallyExpanded: Bool = false) {
        self.title = title
        self.steps = steps
        _expanded = State(initialValue: initiallyExpanded)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Button {
                withAnimation(.easeInOut(duration: 0.22)) { expanded.toggle() }
            } label: {
                HStack {
                    Label(title, systemImage: "list.number")
                        .font(.subheadline)
                        .fontWeight(.semibold)
                        .foregroundColor(.primary)
                    Spacer()
                    Image(systemName: "chevron.down")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(.secondary)
                        .rotationEffect(.degrees(expanded ? 0 : -90))
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(expanded ? "Collapse: \(title)" : "Expand: \(title)")

            if expanded {
                ForEach(Array(steps.enumerated()), id: \.offset) { index, text in
                    HStack(alignment: .top, spacing: 10) {
                        Text("\(index + 1)")
                            .font(.caption)
                            .fontWeight(.bold)
                            .foregroundColor(.white)
                            .frame(width: 20, height: 20)
                            .background(Circle().fill(Color.accentColor))
                        Text(text)
                            .font(.callout)
                            .foregroundColor(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                    }
                    .transition(.opacity)
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(CortexDesign.softBorder, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

extension OnboardingStep {
    /// A friendly, full-sentence headline for the animated intro (the `title` stays a one-word chip).
    var headline: String {
        switch self {
        case .privateVault: return "Your memory, private on this Mac"
        case .firstSource: return "Connect your first source"
        case .reviewMemory: return "Review what Cortex saved"
        case .askUse: return "Ask, and get cited answers"
        case .trustBackup: return "Keep a safe backup"
        }
    }

    /// A short "here's exactly how" title + numbered steps shown in each onboarding step.
    var howToTitle: String {
        switch self {
        case .privateVault: return "How Cortex works"
        case .firstSource: return "How to connect a source"
        case .reviewMemory: return "How review works"
        case .askUse: return "How to ask"
        case .trustBackup: return "How backups work"
        }
    }

    var howToSteps: [String] {
        switch self {
        case .privateVault:
            return [
                "Cortex keeps a private memory index on this Mac — nothing is uploaded.",
                "It watches the sources you connect and saves useful memory for you.",
                "Approved memory becomes searchable, and agents like Claude can cite it.",
            ]
        case .firstSource:
            return [
                "Click the button below, then pick a notes folder — or drag in a ChatGPT / Claude export.",
                "For more services, open Connections and choose one; Cortex shows exactly how to connect it.",
                "Cortex keeps it synced privately and sends new memory to Review with citations.",
            ]
        case .reviewMemory:
            return [
                "Open an item Cortex saved from your connected source.",
                "Approve it if it's useful and clearly cited — otherwise archive it.",
                "Approved memory becomes available to Ask and to your agents.",
            ]
        case .askUse:
            return [
                "Type a question about your reviewed notes.",
                "Cortex answers using only memory you approved.",
                "Every answer shows citations you can click to open the source.",
            ]
        case .trustBackup:
            return [
                "Create a local backup so you can recover memory on this Mac.",
                "Back up or restore anytime from Advanced settings.",
                "Backups stay on your Mac — they are never uploaded.",
            ]
        }
    }
}
