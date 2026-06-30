import SwiftUI

struct OnboardingView: View {
    @ObservedObject var state: AppState
    private let steps = OnboardingStep.allCases

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider()
            VStack(spacing: 0) {
                header
                Divider()
                ScrollView {
                    stepContent
                        .padding(22)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                Divider()
                footer
            }
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

            Spacer()

            Text(state.displayStatus)
                .font(.caption)
                .foregroundColor(.secondary)
                .lineLimit(1)
                .frame(maxWidth: 260)

            Spacer()

            if state.onboardingStep == .trustBackup {
                Button {
                    state.finishOnboarding()
                } label: {
                    Label(
                        state.canCompleteOnboarding ? "Finish Setup" : "Skip Setup for Now",
                        systemImage: state.canCompleteOnboarding ? "checkmark.circle" : "arrow.right.circle"
                    )
                }
                .buttonStyle(.borderedProminent)
            } else {
                Button {
                    state.nextOnboardingStep()
                } label: {
                    Label(continueButtonTitle, systemImage: state.canAdvanceOnboarding ? "chevron.right" : continueButtonIcon)
                }
                .buttonStyle(.borderedProminent)
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
            return "Connect Accounts or Apps"
        case .reviewMemory:
            return "Approve One Memory"
        case .askUse:
            return "Ask a Question"
        case .trustBackup:
            return "Choose Backup"
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
        case .trustBackup:
            return "externaldrive"
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
            OnboardingTrustBackupStep(state: state)
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
            Text("Cortex starts with a private local vault, then builds memory from connected accounts, apps, and direct AI tools. The vault stays backup-friendly while setup focuses on connected context.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 8) {
                Text("Local vault")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Text(state.vaultPath)
                    .font(.system(.caption, design: .monospaced))
                    .lineLimit(2)
                    .textSelection(.enabled)
                DisclosureGroup("Advanced vault location", isExpanded: $vaultLocationExpanded) {
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
                    OnboardingCheckRow(title: "Vault ready", detail: vault.path, systemImage: "checkmark.seal.fill", color: .green)
                    OnboardingCheckRow(title: "Local index", detail: vault.index_path, systemImage: "bolt.horizontal.circle.fill", color: .accentColor)
                    OnboardingCheckRow(title: "Audit log", detail: "\(vault.event_count) events", systemImage: "list.bullet.rectangle", color: .secondary)
                }
            } else {
                OnboardingCheckRow(title: "Starting local backend", detail: state.displayBackendStatus, systemImage: "clock", color: .orange)
            }
        }
    }
}

struct OnboardingFirstSourceStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Connect one account, app integration, or direct AI tool with real context from email, calendar, notes, chat, docs, or direct AI memory saves.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Button {
                        state.selectedTab = .sources
                        state.dismissOnboardingForSession()
                        state.status = "Connect accounts, apps, or direct AI tools to build your model"
                    } label: {
                        Label("Connect Accounts or Apps", systemImage: "link.circle")
                    }
                    .buttonStyle(.borderedProminent)
                    Spacer()
                }
            }

            if !state.onboardingFirstSourceNames.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Connected account, app, or tool")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Text(state.onboardingFirstSourceNames.joined(separator: ", "))
                        .font(.caption)
                        .lineLimit(2)
                        .truncationMode(.middle)
                }
            }

            OnboardingCheckRow(
                title: state.onboardingHasSource ? "First connection ready" : "Waiting for a connection",
                detail: state.onboardingHasSource ? "Next, review what Cortex found before it becomes memory." : "Connect an account, app, or direct AI tool to continue.",
                systemImage: state.onboardingHasSource ? "checkmark.seal.fill" : "link.circle",
                color: state.onboardingHasSource ? .green : .orange
            )
        }
    }
}

struct OnboardingReviewMemoryStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Approve only what Cortex should remember. Archive anything noisy before it can appear in Ask or AI handoffs.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            if state.inbox.isEmpty {
                QuietState(title: "No pending memory", detail: emptyReviewDetail)
            } else {
                ForEach(state.inbox.prefix(2)) { capture in
                    CaptureCard(
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
                title: state.onboardingHasReviewedMemory ? "Memory reviewed" : "Approve one useful memory",
                detail: state.onboardingHasReviewedMemory ? "Cortex has approved memory it can cite." : "Approve one pending item to unlock the first cited Ask.",
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
            return "No reviewable memory is waiting yet. Refresh Review, or connect another account, app, or direct AI tool with more context."
        }
        return "Connect an account, app, or direct AI tool first. Anything useful will appear here before Cortex remembers it."
    }
}

struct OnboardingAskUseStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Ask one question against approved memory. This shows the main loop: connected context becomes reviewed memory, then cited answers.")
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
                title: state.onboardingHasUsedCortex ? "Cortex used once" : "Use Cortex once",
                detail: state.onboardingHasUsedCortex ? "Approved memory was used in a cited answer." : "Ask a question that returns cited memory.",
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
        return "Ask about a connected account, app, or tool. Cortex answers with citations when approved memory matches."
    }
}

struct OnboardingTrustBackupStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Finish with simple defaults: review new memory first, share only approved memory, redact copied memory, and keep backups local.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 10) {
                OnboardingToggleRow(
                    title: "Review new memories first",
                    detail: "New connected context waits for approval before Cortex remembers it.",
                    isOn: $state.appSettings.review_new_captures
                )
                OnboardingToggleRow(
                    title: "Only approved memory leaves Cortex",
                    detail: "Connected tools do not receive pending memory.",
                    isOn: Binding(
                        get: { !state.appSettings.allow_pending_in_context },
                        set: { state.appSettings.allow_pending_in_context = !$0 }
                    )
                )
                OnboardingToggleRow(
                    title: "Redact copied memory",
                    detail: "AI handoffs remove sensitive details where possible.",
                    isOn: $state.appSettings.redact_sensitive_context
                )
                HStack {
                    Button {
                        state.saveMemorySettings()
                    } label: {
                        Label("Save Trust Settings", systemImage: "checkmark.circle")
                    }
                    Button {
                        state.selectedTab = .trust
                        state.dismissOnboardingForSession()
                    } label: {
                        Label("Open Trust", systemImage: "lock.shield")
                    }
                    Spacer()
                }
            }
            .padding(12)
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            VStack(alignment: .leading, spacing: 8) {
                Text("Backup")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Text("Backups stay local in the Cortex vault. Create one now, or explicitly skip it for this setup.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack {
                    Button {
                        state.createBackup()
                    } label: {
                        Label("Back Up Now", systemImage: "archivebox")
                    }
                    Button {
                        state.skipFirstBackup()
                    } label: {
                        Label("Skip for Now", systemImage: "forward")
                    }
                    Spacer()
                }
            }

            if let backup = state.lastBackupPath {
                Text("Latest backup: \(backup)")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
                    .truncationMode(.middle)
            }

            OnboardingCheckRow(
                title: state.onboardingHasBackupDecision ? "Backup decision recorded" : "Back up or skip explicitly",
                detail: state.onboardingHasBackupDecision ? "Setup can finish once the earlier steps are ready." : "Create a first backup, or explicitly skip it for now.",
                systemImage: state.onboardingHasBackupDecision ? "checkmark.seal.fill" : "externaldrive",
                color: state.onboardingHasBackupDecision ? .green : .orange
            )
        }
    }
}

struct OnboardingToggleRow: View {
    let title: String
    let detail: String
    @Binding var isOn: Bool

    var body: some View {
        Toggle(isOn: $isOn) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
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
