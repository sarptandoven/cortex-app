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
                Text("Private personal model")
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
                Label("Finish Later", systemImage: "xmark")
            }
            Button {
                state.skipOnboarding()
            } label: {
                Label("Skip Setup", systemImage: "forward.end")
            }
            .foregroundColor(.secondary)
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
                        state.canCompleteOnboarding ? "Finish Setup" : "Open App for Now",
                        systemImage: state.canCompleteOnboarding ? "checkmark.circle" : "arrow.right.circle"
                    )
                }
                .buttonStyle(.borderedProminent)
            } else {
                Button {
                    state.nextOnboardingStep()
                } label: {
                    Label("Continue", systemImage: "chevron.right")
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(18)
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

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Cortex stores your memory in a readable folder on this Mac. The search index can be rebuilt from those files, so the vault stays portable and backup-friendly.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 8) {
                Text("Vault folder")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Text(state.vaultPath)
                    .font(.system(.caption, design: .monospaced))
                    .lineLimit(2)
                    .textSelection(.enabled)
                HStack {
                    Button {
                        state.useDefaultVaultFolder()
                    } label: {
                        Label("Use Default", systemImage: "house")
                    }
                    Button {
                        state.chooseVaultFolder()
                    } label: {
                        Label("Choose Folder", systemImage: "folder")
                    }
                    Button {
                        state.openVaultFolder()
                    } label: {
                        Label("Open Folder", systemImage: "arrow.up.right.square")
                    }
                    Spacer()
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
                OnboardingCheckRow(title: "Starting local backend", detail: state.backendStatus, systemImage: "clock", color: .orange)
            }
        }
    }
}

struct OnboardingFirstSourceStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Start with one source that has real context: an AI chat export, notes, docs, email, messages, or project files.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack {
                Button {
                    state.chooseFilesForCapture()
                } label: {
                    Label("Choose Sources", systemImage: "doc.badge.plus")
                }
                .buttonStyle(.borderedProminent)
                Button {
                    state.openCaptureInbox()
                } label: {
                    Label("Open Inbox", systemImage: "tray")
                }
                Spacer()
            }

            if !state.onboardingFirstSourceNames.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("First source")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Text(state.onboardingFirstSourceNames.joined(separator: ", "))
                        .font(.caption)
                        .lineLimit(2)
                        .truncationMode(.middle)
                }
            }

            OnboardingCheckRow(
                title: state.onboardingHasSource ? "First source imported" : "Waiting for an imported source",
                detail: state.onboardingHasSource ? "Continue to review and approve useful memory." : "Choose sources, drop files, or continue and add one from Sources later.",
                systemImage: state.onboardingHasSource ? "checkmark.seal.fill" : "tray.and.arrow.down",
                color: state.onboardingHasSource ? .green : .orange
            )
        }
    }
}

struct OnboardingReviewMemoryStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Cortex works best when the first memories are reviewed. Approve useful signals and archive anything noisy before the model starts adapting around them.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            if state.inbox.isEmpty {
                QuietState(title: "No pending memory", detail: state.onboardingHasReviewedMemory ? "You already reviewed memory from your first source." : "Import a source, then return here to approve useful memory.")
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
                detail: state.onboardingHasReviewedMemory ? "Cortex has at least one approved memory to use." : "Approve a pending item here, or keep moving and finish review from the main app.",
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
}

struct OnboardingAskUseStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Ask Cortex one question against approved memory. This is the core use loop: imported sources become cited answers you can trust.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 10) {
                TextField("Ask about your first source", text: $state.searchQuery)
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
                QuietState(title: "Use approved memory once", detail: "Ask a question about your imported source. Cortex will answer with citations when approved memory matches.")
            }

            OnboardingCheckRow(
                title: state.onboardingHasUsedCortex ? "Cortex used once" : "Use Cortex once",
                detail: state.onboardingHasUsedCortex ? "Approved memory was used in a cited answer." : "Ask a question that returns cited memory.",
                systemImage: state.onboardingHasUsedCortex ? "checkmark.seal.fill" : "sparkle.magnifyingglass",
                color: state.onboardingHasUsedCortex ? .green : .orange
            )
        }
    }
}

struct OnboardingTrustBackupStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Finish with a clear default: review new memory first, share only approved memory, redact copied memory, and keep backups local.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 10) {
                OnboardingToggleRow(
                    title: "Review new memories first",
                    detail: "New imports wait for approval before shaping Cortex.",
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
                    detail: "AI handoffs remove sensitive details when possible.",
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
                Text("Backups are zip files stored inside the local Cortex vault. Create one now, or explicitly skip it for this setup.")
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
                detail: state.onboardingHasBackupDecision ? "Setup can be completed when the earlier steps are ready." : "Create a first backup, or explicitly skip it for now.",
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
