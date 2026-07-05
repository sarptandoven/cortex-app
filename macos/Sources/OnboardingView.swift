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
                        Text("Getting started")
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
                .accessibilityLabel("Finish setup later")
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

            if state.isLocalServiceReady {
                OnboardingCheckRow(title: "Private memory ready", detail: "Cortex is using the memory folder shown below. You can keep the default or choose another local folder before connecting sources.", systemImage: "checkmark.seal.fill", color: .green)
                OnboardingVaultFolderCard(state: state)
            } else if state.backendNeedsRecovery {
                OnboardingBackendRecoveryCard(state: state)
            } else {
                OnboardingCheckRow(title: "Starting private memory", detail: state.displayBackendStatus, systemImage: "clock", color: .orange)
            }
        }
    }
}

struct OnboardingVaultFolderCard: View {
    @ObservedObject var state: AppState

    private var reportedVaultPath: String? {
        state.diagnostics?.vault?.path
    }

    private var pathMatchesBackend: Bool {
        guard let reportedVaultPath else { return false }
        let configured = URL(fileURLWithPath: state.vaultPath).standardizedFileURL.path
        let reported = URL(fileURLWithPath: reportedVaultPath).standardizedFileURL.path
        return configured == reported
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: pathMatchesBackend ? "folder.fill.badge.checkmark" : "folder.badge.gearshape")
                    .foregroundColor(pathMatchesBackend ? .green : .orange)
                    .frame(width: 22)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Private memory folder")
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(.secondary)
                    Text(state.vaultPath)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundColor(.primary)
                        .lineLimit(2)
                        .truncationMode(.middle)
                    Text(pathMatchesBackend ? "Backend health confirms this path." : "Waiting for backend health to confirm this path.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer(minLength: 0)
            }

            HStack(spacing: 8) {
                Button("Use Default") { state.useDefaultVaultFolder() }
                    .buttonStyle(.bordered)
                Button("Choose Folder") { state.chooseVaultFolder() }
                    .buttonStyle(.bordered)
                Button("Reveal Folder") { state.openVaultFolder() }
                    .buttonStyle(.bordered)
            }
        }
        .padding(12)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.22)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
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
            Text("Connect one memory path first: AI tools through MCP, Obsidian, or a local notes folder. Cortex keeps the data private, sends useful memory to Review, and uses approved memory in Ask with citations.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

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
        return "Connect MCP or notes"
    }

    private var sourceCardDetail: String {
        if state.onboardingHasSource {
            if state.onboardingHasSyncedMemory {
                return "Cortex found fresh, usable memory from the connected path. Review one useful item next."
            }
            return "Connection is ready. Save from an AI tool or sync notes, then useful items will appear in Review."
        }
        if let message = state.onboardingSourceHealthMessage {
            return message
        }
        if state.hasConnectedObsidianVault {
            return "Cortex checks connected notes on launch and every 30 minutes, then sends new memory to Review with citations."
        }
        return "Choose Obsidian or a local notes folder, or connect MCP AI tools to start. Reviewed memory becomes available to Ask with citations."
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
        return "Connect MCP or notes"
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
        return "Use MCP to save a memory or choose notes. Reviewed memory becomes available to Ask with citations."
    }

    private var firstSourceButtonTitle: String {
        if state.onboardingHasConnectedMemoryLayer, !state.hasConnectedObsidianVault {
            return "Open Connections"
        }
        if state.notesNeedContent { return "Choose notes" }
        if state.hasConnectedObsidianVault { return "Sync notes" }
        if obsidianConnector != nil { return "Connect notes" }
        return "Open Connections"
    }

    private var firstSourceButtonIcon: String {
        if state.onboardingHasConnectedMemoryLayer, !state.hasConnectedObsidianVault {
            return "link.circle"
        }
        if state.notesNeedContent { return "folder.badge.questionmark" }
        if state.hasConnectedObsidianVault { return "arrow.triangle.2.circlepath" }
        if obsidianConnector != nil { return "folder.badge.plus" }
        return "link.circle"
    }

    private func runFirstSourceAction() {
        if state.onboardingHasConnectedMemoryLayer, !state.hasConnectedObsidianVault {
            state.openConnectionsPrivacy(statusMessage: "Check source health")
            return
        }
        if let connector = obsidianConnector {
            state.connectLocalNotesFolder(connector, chooseNew: state.notesNeedContent)
        } else {
            state.openConnectionsPrivacy(statusMessage: "Connect MCP or notes")
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
            Text("Local backups let you recover Cortex memory on this Mac. Create one now, or skip and do it later from Connections & Privacy.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

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
                    detail: "You can create a local backup from Connections & Privacy before adding more notes.",
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
