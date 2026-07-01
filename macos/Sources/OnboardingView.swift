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
            return "Choose Notes"
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
    @State private var vaultLocationExpanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Your memory stays on this Mac. Cortex keeps a private index, sends useful items to Review, and only uses memory after you approve it.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            if state.diagnostics?.vault != nil {
                OnboardingCheckRow(title: "Private memory ready", detail: "Next, connect notes so Cortex can start finding useful memory.", systemImage: "checkmark.seal.fill", color: .green)
            } else {
                OnboardingCheckRow(title: "Starting private memory", detail: state.displayBackendStatus, systemImage: "clock", color: .orange)
            }

            DisclosureGroup("Memory folder", isExpanded: $vaultLocationExpanded) {
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
            Text("Choose a local notes folder first. Cortex syncs it privately, sends useful memory to Review, and makes approved memory available to connected AI tools.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            OnboardingConnectionCard(
                title: state.hasConnectedObsidianVault ? "Notes sync connected" : "Start notes sync",
                detail: state.hasConnectedObsidianVault ? "Cortex checks saved notes on launch and every 30 minutes, then sends new memory to Review with citations." : "Choose an Obsidian or local notes folder. Cortex handles parsing, citations, and repeat sync automatically.",
                systemImage: state.onboardingHasSource ? "checkmark.seal.fill" : "folder.badge.plus",
                isPrimary: true,
                status: state.onboardingHasSource ? "Synced" : (state.hasConnectedObsidianVault ? "Connected" : "Local"),
                buttonTitle: firstSourceButtonTitle,
                buttonSystemImage: firstSourceButtonIcon
            ) {
                runFirstSourceAction()
            }

            if !state.onboardingFirstSourceNames.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Synced notes")
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
            return "Notes synced"
        }
        if state.onboardingHasConnectedMemoryLayer {
            return "Waiting for synced memory"
        }
        return "Start notes sync"
    }

    private var connectionCheckDetail: String {
        if state.onboardingHasSource {
            return "Review has memory from synced notes."
        }
        if state.onboardingHasConnectedMemoryLayer {
            return "Cortex is checking notes so useful memory appears in Review."
        }
        return "Choose a local notes folder when ready. AI tool connections can use approved memory after setup."
    }

    private var firstSourceButtonTitle: String {
        if state.hasConnectedObsidianVault { return "Sync notes" }
        if obsidianConnector != nil { return "Start notes sync" }
        return "Refresh"
    }

    private var firstSourceButtonIcon: String {
        if state.hasConnectedObsidianVault { return "arrow.triangle.2.circlepath" }
        if obsidianConnector != nil { return "folder.badge.plus" }
        return "arrow.clockwise"
    }

    private func runFirstSourceAction() {
        if let connector = obsidianConnector {
            state.connectLocalNotesFolder(connector)
        } else {
            Task { await state.loadSourceConnectivity() }
            state.status = "Checking notes connector"
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
                    Label("Start notes sync", systemImage: "folder.badge.plus")
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
        if state.onboardingHasSource {
            return "No reviewable memory is waiting yet. Let notes sync finish, then approve one useful item."
        }
        return "Start notes sync first; synced memory appears here before Cortex uses it."
    }

    private var reviewPathTitle: String {
        if state.onboardingHasReviewedMemory {
            return "Memory reviewed"
        }
        return state.onboardingHasSource ? "Approve one memory" : "Sync memory first"
    }

    private var reviewPathDetail: String {
        if state.onboardingHasReviewedMemory {
            return "Cortex has reviewed notes it can cite."
        }
        if state.onboardingHasSource {
            return "Approve one useful memory to let Cortex cite it in Ask."
        }
        return "Review unlocks after a connected source syncs memory."
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
