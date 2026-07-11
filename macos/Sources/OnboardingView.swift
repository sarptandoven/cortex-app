import SwiftUI
import UniformTypeIdentifiers

/// A deep, guided, animated first-run walkthrough for Cortex — "The Archive".
///
/// The flow is a narrative in six calm beats: welcome, privacy, add memory, see yourself, an
/// optional quick-capture opt-in, and finally connecting AI tools. Each beat animates in with a
/// spring + asymmetric slide, carries ambient motion (`TimelineView`), and offers a clear
/// Back / Continue with a Skip escape hatch.
///
/// The walkthrough keeps its OWN step cursor (`WalkStep`) so the storytelling order is independent
/// of the practical setup-loop enum that `AppState` tracks. Real actions still route through
/// `AppState` (loadSampleNotes / connectLocalNotesFolder / openConnectionsPrivacy / finishOnboarding),
/// so nothing about the underlying setup gates changes.
///
/// macOS 13 safe: pure `withAnimation`, `.transition`, `TimelineView`, `Canvas`,
/// `matchedGeometryEffect`, and `repeatForever` — no `symbolEffect` / `phaseAnimator` /
/// `contentTransition` / `Observable`.
struct OnboardingView: View {
    @ObservedObject var state: AppState

    /// The six narrative beats of the walkthrough. Independent of `OnboardingStep` (the setup loop).
    private enum WalkStep: Int, CaseIterable, Identifiable {
        case welcome
        case privacy
        case addMemory
        case seeYourself
        case quickCapture
        case connectTools

        var id: Int { rawValue }
    }

    @State private var step: WalkStep = .welcome
    @State private var celebrating = false
    /// Drives the continuity mark that slides between the header and step content.
    @Namespace private var markSpace

    private var steps: [WalkStep] { WalkStep.allCases }

    var body: some View {
        ZStack {
            VStack(spacing: 0) {
                header
                Divider().opacity(0.5)
                ScrollView {
                    stepContent
                        .padding(.horizontal, 44)
                        .padding(.vertical, 34)
                        .frame(maxWidth: 600, alignment: .leading)
                        .frame(maxWidth: .infinity)
                        .id(step)
                        .transition(.asymmetric(
                            insertion: .move(edge: .trailing).combined(with: .opacity),
                            removal: .move(edge: .leading).combined(with: .opacity)
                        ))
                }
                .animation(.spring(response: 0.42, dampingFraction: 0.82), value: step)
                Divider().opacity(0.5)
                footer
            }

            if celebrating {
                celebrationOverlay
                    .transition(.opacity)
            }
        }
        .background(OnboardingAmbientBackground())
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 10) {
                Text(DistributionMode.appDisplayName)
                    .font(.system(size: 15, weight: .semibold, design: .serif))
                    .foregroundColor(CortexDesign.ink)
                Text("The Archive")
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.inkFaint)
                Spacer()
                Button {
                    skipTapped()
                } label: {
                    Text("Skip")
                        .font(.system(size: 12, weight: .medium))
                }
                .buttonStyle(.borderless)
                .help("Skip the walkthrough")
                .accessibilityLabel("Skip the walkthrough")
            }

            // Progress dots — the current beat is a longer, wax-red capsule; visited beats stay
            // filled, unvisited stay quiet. A quiet "Step N of 6" for orientation.
            HStack(spacing: 8) {
                ForEach(steps) { s in
                    Capsule()
                        .fill(dotColor(for: s))
                        .frame(width: step == s ? 22 : 7, height: 7)
                }
                Spacer()
                Text("Step \(step.rawValue + 1) of \(steps.count)")
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundColor(CortexDesign.inkFaint)
            }
            .animation(.spring(response: 0.4, dampingFraction: 0.72), value: step)
        }
        .padding(.horizontal, 44)
        .padding(.top, 26)
        .padding(.bottom, 18)
    }

    private func dotColor(for s: WalkStep) -> Color {
        if s.rawValue < step.rawValue { return CortexDesign.accent.opacity(0.55) }
        if s == step { return CortexDesign.accent }
        return CortexDesign.softBorder
    }

    // MARK: - Footer

    private var footer: some View {
        HStack {
            Button {
                back()
            } label: {
                Label("Back", systemImage: "chevron.left")
            }
            .buttonStyle(.borderless)
            .controlSize(.large)
            .disabled(step == .welcome)
            .opacity(step == .welcome ? 0 : 1)

            Spacer()

            if step == steps.last {
                Button {
                    finishTapped()
                } label: {
                    Label("Finish", systemImage: "checkmark.circle")
                        .frame(minWidth: 120)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(celebrating)
            } else {
                Button {
                    advance()
                } label: {
                    Label(step == .quickCapture ? "Almost there" : "Continue",
                          systemImage: "chevron.right")
                        .frame(minWidth: 120)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }
        }
        .padding(20)
    }

    // MARK: - Navigation

    private func advance() {
        guard let next = WalkStep(rawValue: step.rawValue + 1) else { return }
        withAnimation(.spring(response: 0.42, dampingFraction: 0.82)) { step = next }
    }

    private func back() {
        guard let prev = WalkStep(rawValue: step.rawValue - 1) else { return }
        withAnimation(.spring(response: 0.42, dampingFraction: 0.82)) { step = prev }
    }

    /// Skip leaves the walkthrough for this session without asserting setup is finished — the
    /// existing session-dismiss path keeps the setup gates honest.
    private func skipTapped() {
        state.dismissOnboardingForSession()
    }

    /// Finishing runs a brief celebration, then hands off to `AppState.finishOnboarding()`, which
    /// posts `.cortexOnboardingCompleted` when the setup loop is genuinely complete and otherwise
    /// closes the walkthrough gracefully for the session.
    private func finishTapped() {
        guard !celebrating else { return }
        withAnimation(.spring(response: 0.4, dampingFraction: 0.75)) { celebrating = true }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.15) {
            state.finishOnboarding()
        }
    }

    // MARK: - Step content

    @ViewBuilder
    private var stepContent: some View {
        switch step {
        case .welcome:
            OnboardingWelcomeStep(markSpace: markSpace)
        case .privacy:
            OnboardingPrivacyStep()
        case .addMemory:
            OnboardingAddMemoryStep(state: state, advance: advance)
        case .seeYourself:
            OnboardingSeeYourselfStep(state: state)
        case .quickCapture:
            OnboardingQuickCaptureStep(state: state)
        case .connectTools:
            OnboardingConnectToolsStep(state: state)
        }
    }

    // MARK: - Celebration

    /// A short full-panel beat before the sheet closes — the one thing worth remembering (the
    /// ⌃⌥Space hotkey) gets its moment.
    private var celebrationOverlay: some View {
        VStack(spacing: 16) {
            OnboardingHeroMark(systemImage: "checkmark.seal.fill", tint: CortexDesign.sealMoss)
            Text("Your Archive is ready")
                .font(CortexDesign.Typography.display(26))
                .foregroundColor(CortexDesign.ink)
            Text("Ask anytime — press ⌃⌥Space, or click the mark in your menu bar.")
                .font(.callout)
                .foregroundColor(CortexDesign.inkSecondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(36)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(CortexDesign.appBackground.opacity(0.98))
    }
}

// MARK: - Step 1: Welcome

/// Warm one-sentence welcome + the Archive identity, over a gently breathing hero mark.
private struct OnboardingWelcomeStep: View {
    let markSpace: Namespace.ID

    var body: some View {
        VStack(alignment: .leading, spacing: 26) {
            HStack {
                Spacer()
                OnboardingHeroMark(systemImage: "brain.head.profile", tint: CortexDesign.accent)
                    .matchedGeometryEffect(id: "hero", in: markSpace)
                Spacer()
            }
            .padding(.top, 6)

            VStack(alignment: .leading, spacing: 14) {
                Text("Welcome to \(DistributionMode.appDisplayName)")
                    .font(CortexDesign.Typography.display(30))
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)

                Text("Cortex is a private archive of what you know — it quietly distills your notes into memory you can search, review, and let your AI tools cite.")
                    .font(CortexDesign.Typography.prose(16))
                    .lineSpacing(4)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            OnboardingCheckRow(
                title: "A calm, considered space",
                detail: "No feed, no noise — just your memory, kept like a well-tended library.",
                systemImage: "books.vertical",
                color: CortexDesign.gold
            )
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - Step 2: Privacy first

/// "Everything stays on your Mac", over a calm animated lock cradling a leaf.
private struct OnboardingPrivacyStep: View {
    // The required-account build (CortexRequireAccount=true) syncs memory to the user's account, so
    // the "nothing is uploaded / no account needed" copy is only honest for the local-only build.
    // Gate on the same Info.plist flag AppState.accountRequired reads.
    private var accountRequired: Bool {
        (Bundle.main.object(forInfoDictionaryKey: "CortexRequireAccount") as? String)?.lowercased() == "true"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 26) {
            HStack {
                Spacer()
                OnboardingPrivacyMark()
                Spacer()
            }
            .padding(.top, 6)

            VStack(alignment: .leading, spacing: 14) {
                Text("Private, by design")
                    .font(CortexDesign.Typography.display(28))
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)

                Text(accountRequired
                     ? "Cortex builds and keeps your memory on your Mac, and syncs it to your account so it stays safe and reachable across your devices. Your memory is always yours."
                     : "Everything stays on your Mac. Cortex builds and keeps your memory index locally — nothing is uploaded, and there is no account to create.")
                    .font(CortexDesign.Typography.prose(16))
                    .lineSpacing(4)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            VStack(alignment: .leading, spacing: 12) {
                OnboardingCheckRow(
                    title: accountRequired ? "Built on your Mac" : "On this Mac only",
                    detail: accountRequired
                        ? "Your memory is created and kept on your device."
                        : "Your notes and memory never leave your device.",
                    systemImage: "lock.shield",
                    color: CortexDesign.sealMoss
                )
                OnboardingCheckRow(
                    title: accountRequired ? "Synced to your account" : "No account needed",
                    detail: accountRequired
                        ? "Signed in, your memory is backed up and reachable across your devices."
                        : "Nothing to sign up for. You are in control the whole way.",
                    systemImage: "person.crop.circle.badge.checkmark",
                    color: CortexDesign.sealMoss
                )
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - Step 3: Add your memory

/// The two clear first-source paths (connect notes / explore with sample notes), beside an
/// animated illustration of notes distilling into a single memory. Preserves the original
/// first-source actions: `connectLocalNotesFolder` and `loadSampleNotes`, and honors the
/// "drag in a ChatGPT / Claude export" promise with a real drop target + file picker.
private struct OnboardingAddMemoryStep: View {
    @ObservedObject var state: AppState
    /// Called after sample notes load so the walkthrough moves forward to "See yourself".
    let advance: () -> Void

    @State private var loadingSamples = false
    @State private var dropTargeted = false

    private var obsidianConnector: SourceConnectorCatalogItem? {
        state.sourceConnectorCatalog.first { $0.id == "obsidian" }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            HStack(alignment: .top, spacing: 22) {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Add your first memory")
                        .font(CortexDesign.Typography.display(26))
                        .foregroundColor(CortexDesign.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("Point Cortex at your notes and it distills the useful parts into memory. Pick a path — you can add more later.")
                        .font(CortexDesign.Typography.prose(15))
                        .lineSpacing(3)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                OnboardingDistillMark()
                    .frame(width: 96)
                    .padding(.top, 2)
            }

            OnboardingConnectionCard(
                title: connectTitle,
                detail: connectDetail,
                systemImage: connectIcon,
                isPrimary: true,
                status: connectStatus,
                buttonTitle: connectButtonTitle,
                buttonSystemImage: connectButtonIcon
            ) {
                runConnectAction()
            }

            appConnectGrid

            aiExportOption

            sampleNotesOption
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .task {
            if state.sourceConnectorCatalog.isEmpty {
                await state.loadSourceConnectivity()
            }
        }
    }

    /// The lighter, link-style path: bundled sample notes so a brand-new user (or a reviewer with
    /// no files of their own) can see the full memory picture instantly, then advance.
    @ViewBuilder
    private var sampleNotesOption: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button {
                exploreWithSampleNotes()
            } label: {
                HStack(spacing: 7) {
                    if loadingSamples {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "sparkles")
                    }
                    Text("Explore with sample notes")
                }
            }
            .buttonStyle(.link)
            .disabled(loadingSamples || state.isBusy)

            Text("No files of your own yet? Try Cortex on a small set of example notes.")
                .font(.caption)
                .foregroundColor(CortexDesign.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 2)
    }

    /// The priority "log in and it pulls your data" sources, surfaced IN onboarding instead of
    /// buried in Connections. Each tile reuses the exact connect dispatch the Connections library
    /// uses (GitHub device flow / managed-OAuth start), so there is one code path. Managed-OAuth
    /// connectors whose provider credentials aren't configured on the server render honestly as
    /// "Available soon" rather than a dead button — matching the Connections sheet.
    private static let onboardingSourceIDs = ["notion", "gmail", "google-drive", "github"]

    private var onboardingSources: [SourceConnectorCatalogItem] {
        OnboardingAddMemoryStep.onboardingSourceIDs.compactMap { id in
            state.sourceConnectorCatalog.first { $0.id == id }
        }
        // App Store builds are local-first: outbound HTTPS is stripped, so these OAuth/token
        // connectors literally cannot sync — never show a dead "Connect" tile for them (mirrors
        // ConnectionsPrivacySheet's wiredConnectors filter). In MAS this leaves the grid empty, so
        // the "connect an app" section simply doesn't render; the notes-folder + export + sample
        // paths (which DO work locally) remain.
        .filter { !DistributionMode.isAppStore || $0.connectionSetup?.mode == "native-local-connector" }
    }

    private func sourceIsConnectable(_ connector: SourceConnectorCatalogItem) -> Bool {
        if connector.connectionSetup?.supportsDeviceFlow == true { return true }
        if connector.connectionSetup?.supportsManagedOAuth == true {
            return state.managedOAuthIsConfigured(connector)
        }
        return false
    }

    private func connectOnboardingSource(_ connector: SourceConnectorCatalogItem) {
        // Same dispatch as ConnectionsPrivacySheet.libraryAction — one connect path, no drift.
        if connector.connectionSetup?.supportsDeviceFlow == true {
            state.startGitHubDeviceFlow(connector)
        } else if connector.connectionSetup?.supportsManagedOAuth == true,
                  state.managedOAuthIsConfigured(connector) {
            state.startManagedOAuthConnector(connector)
        }
    }

    private func sourceIcon(_ id: String) -> String {
        switch id {
        case "notion": return "doc.richtext"
        case "gmail": return "envelope"
        case "google-drive": return "externaldrive"
        case "github": return "chevron.left.forwardslash.chevron.right"
        default: return "app.connected.to.app.below.fill"
        }
    }

    @ViewBuilder
    private var appConnectGrid: some View {
        let sources = onboardingSources
        if !sources.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("…or connect an app — sign in once and Cortex pulls your data")
                    .font(.subheadline)
                    .fontWeight(.semibold)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                LazyVGrid(
                    columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)],
                    spacing: 10
                ) {
                    ForEach(sources) { connector in
                        OnboardingSourceTile(
                            name: connector.name,
                            systemImage: sourceIcon(connector.id),
                            connectable: sourceIsConnectable(connector),
                            starting: state.connectorOAuthStartingIDs.contains(connector.id),
                            connected: state.connectorSyncingIDs.contains(connector.id)
                        ) {
                            connectOnboardingSource(connector)
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// The promised ChatGPT / Claude export path: a real drop target that routes straight into
    /// `AppState.importFromPath`, plus the same file picker Connections uses
    /// (`importAIChatExport`). Without this, the step's copy said "drag in an export" while only
    /// offering the notes-folder flow.
    @ViewBuilder
    private var aiExportOption: some View {
        RoundedRectangle(cornerRadius: 10)
            .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6]))
            .foregroundColor(dropTargeted ? CortexDesign.accent : CortexDesign.softBorder)
            .frame(height: 58)
            .overlay(
                HStack(spacing: 8) {
                    if state.importInFlight { ProgressView().controlSize(.small) }
                    Text(state.importInFlight ? "Importing your chats…" : "Drag a ChatGPT / Claude export here, or")
                        .font(.callout)
                        .foregroundColor(CortexDesign.inkSecondary)
                    if !state.importInFlight {
                        Button {
                            state.importAIChatExport()
                        } label: {
                            Label("Choose export file…", systemImage: "folder.badge.plus")
                        }
                        .buttonStyle(.link)
                    }
                }
            )
            .onDrop(of: [.fileURL], isTargeted: $dropTargeted) { providers in
                guard let provider = providers.first else { return false }
                provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                    var resolved: String?
                    if let data = item as? Data, let url = URL(dataRepresentation: data, relativeTo: nil) {
                        resolved = url.standardizedFileURL.path
                    } else if let url = item as? URL {
                        resolved = url.standardizedFileURL.path
                    }
                    guard let path = resolved else { return }
                    Task { @MainActor in await state.importFromPath(path) }
                }
                return true
            }
    }

    private func exploreWithSampleNotes() {
        guard !loadingSamples else { return }
        loadingSamples = true
        Task {
            await state.loadSampleNotes()
            loadingSamples = false
            advance()
        }
    }

    private var connectTitle: String {
        state.onboardingHasSource ? "Source connected" : "Connect your notes"
    }

    private var connectDetail: String {
        if state.onboardingHasSource {
            return "Cortex found usable memory from your connected source. See yourself next."
        }
        if state.hasConnectedObsidianVault {
            return "Cortex checks connected notes on launch and every 30 minutes, then distills new memory with citations."
        }
        return "Choose a local notes folder — or drag in a ChatGPT / Claude export. Everything stays on your Mac."
    }

    private var connectIcon: String {
        state.onboardingHasSource ? "checkmark.seal.fill" : "folder.badge.plus"
    }

    private var connectStatus: String {
        state.onboardingHasSource ? "Ready" : "Local"
    }

    private var connectButtonTitle: String {
        if state.onboardingHasSource { return "Change source" }
        if state.notesNeedContent { return "Choose notes" }
        if state.hasConnectedObsidianVault { return "Sync notes" }
        if obsidianConnector != nil { return "Connect notes" }
        return "Open Connections"
    }

    private var connectButtonIcon: String {
        if state.notesNeedContent { return "folder.badge.questionmark" }
        if state.hasConnectedObsidianVault { return "arrow.triangle.2.circlepath" }
        if obsidianConnector != nil { return "folder.badge.plus" }
        return "link.circle"
    }

    private func runConnectAction() {
        if let connector = obsidianConnector {
            state.connectLocalNotesFolder(connector, chooseNew: state.notesNeedContent)
        } else {
            state.openConnectionsPrivacy(statusMessage: "Choose a source to connect")
        }
    }
}

// MARK: - Step 4: See yourself

/// A preview of the profile and "Your Constellation" graph forming, with the review → ask → cited
/// answers loop explained in three calm rows.
private struct OnboardingSeeYourselfStep: View {
    @ObservedObject var state: AppState

    private var memoryCount: Int {
        state.stats?.memories ?? state.graphNodes.count
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            VStack(alignment: .leading, spacing: 10) {
                Text("See yourself take shape")
                    .font(CortexDesign.Typography.display(26))
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text("As memory accumulates, Cortex draws Your Constellation — the shape of what you know — and builds a profile you can browse.")
                    .font(CortexDesign.Typography.prose(15))
                    .lineSpacing(3)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            OnboardingConstellationPreview()
                .frame(height: 190)
                .frame(maxWidth: .infinity)
                .background(CortexDesign.panelBackground)
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(CortexDesign.softBorder, lineWidth: 1))
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .overlay(alignment: .bottomLeading) {
                    Text(memoryCount > 0 ? "Your Constellation · \(memoryCount) memories" : "Your Constellation")
                        .font(.system(size: 11, weight: .medium, design: .monospaced))
                        .foregroundColor(CortexDesign.inkFaint)
                        .padding(12)
                }

            VStack(alignment: .leading, spacing: 12) {
                OnboardingFlowRow(index: 1, title: "Review", detail: "Approve the memory worth keeping.", systemImage: "checklist")
                OnboardingFlowRow(index: 2, title: "Ask", detail: "Question your memory in plain language.", systemImage: "sparkle.magnifyingglass")
                OnboardingFlowRow(index: 3, title: "Cited answers", detail: "Every answer links back to the source.", systemImage: "quote.bubble")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .task {
            await state.loadStats()
            await state.loadProfile()
        }
    }
}

private struct OnboardingFlowRow: View {
    let index: Int
    let title: String
    let detail: String
    let systemImage: String

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            ZStack {
                Circle().fill(CortexDesign.accentSoft)
                Image(systemName: systemImage)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(CortexDesign.accent)
            }
            .frame(width: 34, height: 34)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(CortexDesign.ink)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            if index < 3 {
                Image(systemName: "arrow.down")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(CortexDesign.inkFaint)
            }
        }
    }
}

// MARK: - Step 5: Quick capture (optional opt-in)

/// An opt-in card for saving anything to Cortex with a keyboard shortcut. In App Store builds this
/// is shown as "Available in the direct-download version" with the controls disabled — screen and
/// keyboard capture are sandbox-incompatible and must never be offered in MAS.
private struct OnboardingQuickCaptureStep: View {
    @ObservedObject var state: AppState

    private var isMAS: Bool { DistributionMode.isAppStore }

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 8) {
                    Text("Quick capture")
                        .font(CortexDesign.Typography.display(26))
                        .foregroundColor(CortexDesign.ink)
                    Text("Optional")
                        .font(.caption2)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.ink)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 2)
                        .background(Capsule().fill(CortexDesign.goldSoft))
                }
                Text("Save anything to Cortex with a shortcut — highlighted text or what's on screen goes straight into your Archive.")
                    .font(CortexDesign.Typography.prose(15))
                    .lineSpacing(3)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if isMAS {
                masCard
            } else {
                enableCard
            }

            Text("You can change this anytime in Settings.")
                .font(.caption)
                .foregroundColor(CortexDesign.inkFaint)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Direct-download build: a live opt-in toggle + a keybind recorder, bound to AppState.
    private var enableCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            Toggle(isOn: $state.quickCaptureEnabled) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Enable quick capture")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(CortexDesign.ink)
                    Text("Turn on to capture with a global shortcut.")
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                }
            }
            .toggleStyle(.switch)

            if state.quickCaptureEnabled {
                Divider().opacity(0.5)
                HStack(alignment: .center, spacing: 12) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Shortcut")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundColor(CortexDesign.ink)
                        Text("Press the keys you'd like to use.")
                            .font(.caption)
                            .foregroundColor(CortexDesign.inkSecondary)
                    }
                    Spacer()
                    // KeybindRecorderView is provided by QuickCapture.swift and records one
                    // keystroke into a KeyCombo bound to AppState.quickCaptureKeybind.
                    KeybindRecorderView(combo: $state.quickCaptureKeybind)
                }
                .transition(.opacity.combined(with: .move(edge: .top)))
            }

            // Pure-visual preview of the capture pill so the user experiences the notch during
            // onboarding (no backend needed). Uses the same .captured style that a real quick
            // capture surfaces. Direct-download only — deliberately absent from masCard.
            Button {
                NotchNotifier.shared.show(
                    title: "Saved to Cortex",
                    subtitle: "This is what a quick capture looks like.",
                    style: .captured
                )
            } label: {
                Label("Show me the notch", systemImage: "bell.badge")
            }
            .buttonStyle(.bordered)
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(CortexDesign.softBorder, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .animation(.easeInOut(duration: 0.22), value: state.quickCaptureEnabled)
    }

    /// Mac App Store build: this beat just reassures — no dead toggle, and no reference to any
    /// other place to get the app (steering users off the App Store is a Guideline 4 / 2.3.2 issue).
    /// A positive statement of what the sandboxed build does.
    private var masCard: some View {
        OnboardingCheckRow(
            title: "Everything becomes memory, automatically",
            detail: "Add notes or import your chats any time and \(DistributionMode.appDisplayName) distills them into cited memory on your Mac — no extra setup.",
            systemImage: "checkmark.seal",
            color: CortexDesign.gold
        )
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(CortexDesign.softBorder, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

// MARK: - Step 6: Connect your AI tools

/// Brief close: point to Connections for wiring up AI tools, record the first backup decision
/// (the last setup-loop gate), and finish.
private struct OnboardingConnectToolsStep: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            HStack {
                Spacer()
                OnboardingHeroMark(systemImage: "point.3.connected.trianglepath.dotted", tint: CortexDesign.accent)
                Spacer()
            }
            .padding(.top, 6)

            VStack(alignment: .leading, spacing: 12) {
                Text("Connect your AI tools")
                    .font(CortexDesign.Typography.display(26))
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text("Let agents like Claude read and cite your approved memory. Set this up in Connections whenever you're ready — nothing is required to finish.")
                    .font(CortexDesign.Typography.prose(15))
                    .lineSpacing(3)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Button {
                state.openConnectionsPrivacy(statusMessage: "Connect your AI tools")
            } label: {
                Label("Open Connections", systemImage: "link.circle")
                    .frame(minWidth: 180, minHeight: 44)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)

            backupCard

            OnboardingCheckRow(
                title: "You're set up",
                detail: "Press Finish to enter your Archive. You can revisit any of this later.",
                systemImage: "checkmark.seal.fill",
                color: CortexDesign.sealMoss
            )
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// The setup loop's last gate (`OnboardingStep.trustBackup`) needs an explicit first-backup
    /// decision. Offer it here — back up now, or skip and decide later from Settings — so the
    /// walkthrough can genuinely complete onboarding instead of only dismissing for the session.
    @ViewBuilder
    private var backupCard: some View {
        if state.onboardingHasBackupDecision {
            OnboardingCheckRow(
                title: state.onboardingBackupDecision == "skipped" ? "Backup skipped for now" : "First backup saved",
                detail: state.onboardingBackupDecision == "skipped"
                    ? "You can back up anytime from Settings → Data & Recovery."
                    : "Your local memory has a restorable snapshot on this Mac.",
                systemImage: "archivebox",
                color: CortexDesign.sealMoss
            )
        } else {
            VStack(alignment: .leading, spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Back up your memory")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(CortexDesign.ink)
                    Text("Save a restorable snapshot of your local memory, or decide later in Settings.")
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                HStack(spacing: 10) {
                    Button {
                        state.createBackup()
                    } label: {
                        Label("Back Up Now", systemImage: "archivebox")
                    }
                    .buttonStyle(.bordered)
                    Button("Skip for now") {
                        state.skipFirstBackup()
                    }
                    .buttonStyle(.borderless)
                }
            }
            .padding(18)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(CortexDesign.panelBackground)
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(CortexDesign.softBorder, lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: 10))
        }
    }
}

// MARK: - Animated components

/// An animated hero glyph: a symbol resting inside softly-expanding concentric rings, breathing
/// gently. macOS 13 compatible (pure `withAnimation` / `repeatForever`, no `symbolEffect`).
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

/// A calm lock cradling a leaf — the privacy mark. The leaf drifts and the lock ring breathes.
private struct OnboardingPrivacyMark: View {
    @State private var animate = false

    var body: some View {
        ZStack {
            Circle()
                .fill(CortexDesign.sealMoss.opacity(0.12))
                .frame(width: 78, height: 78)
                .scaleEffect(animate ? 1.04 : 0.96)
                .animation(.easeInOut(duration: 2.4).repeatForever(autoreverses: true), value: animate)
            Image(systemName: "lock.shield")
                .font(.system(size: 34, weight: .semibold))
                .foregroundColor(CortexDesign.sealMoss)
            Image(systemName: "leaf.fill")
                .font(.system(size: 15, weight: .semibold))
                .foregroundColor(CortexDesign.sealMoss.opacity(0.85))
                .offset(x: 22, y: animate ? -20 : -14)
                .rotationEffect(.degrees(animate ? 6 : -6))
                .animation(.easeInOut(duration: 2.8).repeatForever(autoreverses: true), value: animate)
        }
        .frame(width: 96, height: 96)
        .onAppear { animate = true }
        .accessibilityHidden(true)
    }
}

/// Three note cards drifting into a single distilled memory dot — the "notes → memory" idea.
private struct OnboardingDistillMark: View {
    var body: some View {
        TimelineView(.animation) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            Canvas { ctx, size in
                let center = CGPoint(x: size.width / 2, y: size.height * 0.62)
                // Three source "note" marks orbiting slightly, feeding the center.
                for i in 0..<3 {
                    let phase = t * 0.6 + Double(i) * (.pi * 2 / 3)
                    let radius = 26.0 + sin(t * 0.9 + Double(i)) * 3
                    let p = CGPoint(x: center.x + CGFloat(cos(phase)) * radius,
                                    y: center.y - 34 + CGFloat(sin(phase)) * radius * 0.4)
                    let rect = CGRect(x: p.x - 7, y: p.y - 9, width: 14, height: 18)
                    let path = Path(roundedRect: rect, cornerRadius: 2)
                    ctx.fill(path, with: .color(CortexDesign.gold.opacity(0.55)))
                    // Faint line drawing each note toward the distilled memory.
                    var line = Path()
                    line.move(to: p)
                    line.addLine(to: center)
                    ctx.stroke(line, with: .color(CortexDesign.accent.opacity(0.18)), lineWidth: 1)
                }
                // The distilled memory: a steady wax-red dot with a soft breathing halo.
                let pulse = 1 + sin(t * 1.4) * 0.12
                let halo = CGRect(x: center.x - 13 * pulse, y: center.y - 13 * pulse,
                                  width: 26 * pulse, height: 26 * pulse)
                ctx.fill(Path(ellipseIn: halo), with: .color(CortexDesign.accent.opacity(0.15)))
                let dot = CGRect(x: center.x - 7, y: center.y - 7, width: 14, height: 14)
                ctx.fill(Path(ellipseIn: dot), with: .color(CortexDesign.accent))
            }
        }
        .frame(height: 96)
        .accessibilityHidden(true)
    }
}

/// A small constellation forming: nodes fade/settle into place with gently pulsing links —
/// a preview of "Your Constellation". Pure `TimelineView` + `Canvas`, macOS-13 safe.
private struct OnboardingConstellationPreview: View {
    // Fixed layout so the preview reads as a coherent shape rather than random noise.
    private let nodes: [CGPoint] = [
        CGPoint(x: 0.20, y: 0.34), CGPoint(x: 0.38, y: 0.66), CGPoint(x: 0.50, y: 0.30),
        CGPoint(x: 0.64, y: 0.58), CGPoint(x: 0.78, y: 0.38), CGPoint(x: 0.86, y: 0.68),
        CGPoint(x: 0.30, y: 0.52), CGPoint(x: 0.58, y: 0.74),
    ]
    private let edges: [(Int, Int)] = [(0, 2), (0, 6), (6, 1), (1, 7), (2, 3), (3, 4), (4, 5), (3, 7)]

    var body: some View {
        TimelineView(.animation) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            Canvas { ctx, size in
                func point(_ p: CGPoint) -> CGPoint {
                    CGPoint(x: 18 + p.x * (size.width - 36), y: 18 + p.y * (size.height - 36))
                }
                // Links first, pulsing softly.
                for (a, b) in edges {
                    let pa = point(nodes[a]); let pb = point(nodes[b])
                    var path = Path(); path.move(to: pa); path.addLine(to: pb)
                    let flicker = 0.14 + (sin(t * 0.8 + Double(a + b)) + 1) * 0.06
                    ctx.stroke(path, with: .color(CortexDesign.accent.opacity(flicker)), lineWidth: 1)
                }
                // Nodes settling in with a gentle breathing scale.
                for (i, n) in nodes.enumerated() {
                    let p = point(n)
                    let pulse = 1 + sin(t * 1.1 + Double(i) * 0.7) * 0.18
                    let r = (i == 2 || i == 3 ? 5.5 : 4.0) * pulse
                    let halo = CGRect(x: p.x - r * 2, y: p.y - r * 2, width: r * 4, height: r * 4)
                    ctx.fill(Path(ellipseIn: halo), with: .color(CortexDesign.accent.opacity(0.08)))
                    let dot = CGRect(x: p.x - r, y: p.y - r, width: r * 2, height: r * 2)
                    ctx.fill(Path(ellipseIn: dot), with: .color(CortexDesign.accent.opacity(0.85)))
                }
            }
        }
        .accessibilityHidden(true)
    }
}

/// A whisper-quiet drifting field behind the whole walkthrough — a few faint gold motes moving
/// slowly across the paper. Never busy; opacity stays very low.
private struct OnboardingAmbientBackground: View {
    var body: some View {
        ZStack {
            CortexDesign.appBackground
            TimelineView(.animation) { context in
                let t = context.date.timeIntervalSinceReferenceDate
                Canvas { ctx, size in
                    for i in 0..<9 {
                        let seed = Double(i) * 1.7
                        let x = (sin(t * 0.05 + seed) * 0.5 + 0.5) * size.width
                        let y = (cos(t * 0.04 + seed * 1.3) * 0.5 + 0.5) * size.height
                        let r = 1.5 + (sin(seed) + 1) * 1.2
                        let rect = CGRect(x: x - r, y: y - r, width: r * 2, height: r * 2)
                        ctx.fill(Path(ellipseIn: rect), with: .color(CortexDesign.gold.opacity(0.05)))
                    }
                }
            }
            .allowsHitTesting(false)
        }
        .ignoresSafeArea()
    }
}

// MARK: - Shared small components

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
                    .foregroundColor(isPrimary ? CortexDesign.accent : CortexDesign.inkSecondary)
                Spacer()
                if let status {
                    Text(status)
                        .font(.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.accent)
                        .padding(.horizontal, 9)
                        .padding(.vertical, 5)
                        .background(CortexDesign.accent.opacity(0.10))
                        .clipShape(Capsule())
                }
            }
            Text(title)
                .font(.headline)
                .fontWeight(.semibold)
                .foregroundColor(CortexDesign.ink)
            Text(detail)
                .font(.callout)
                .foregroundColor(CortexDesign.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            actionButton
        }
        .padding(16)
        .frame(maxWidth: .infinity, minHeight: 176, alignment: .topLeading)
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

/// A compact connect tile for the onboarding source grid. Shows a one-click "Connect" affordance for
/// wired providers, an in-flight spinner while OAuth is starting, and an honest "Available soon" for
/// managed-OAuth providers not yet configured on the server (never a dead button).
struct OnboardingSourceTile: View {
    let name: String
    let systemImage: String
    let connectable: Bool
    let starting: Bool
    let connected: Bool
    let action: () -> Void

    private var subtitle: String {
        if connected { return "Syncing…" }
        if starting { return "Opening sign-in…" }
        return connectable ? "Connect" : "Available soon"
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: systemImage)
                    .font(.title3)
                    .foregroundColor(connectable ? CortexDesign.accent : CortexDesign.inkFaint)
                    .frame(width: 22)
                VStack(alignment: .leading, spacing: 1) {
                    Text(name)
                        .font(.callout)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.ink)
                    Text(subtitle)
                        .font(.caption2)
                        .foregroundColor(CortexDesign.inkSecondary)
                }
                Spacer(minLength: 4)
                if starting {
                    ProgressView().controlSize(.small)
                } else if connectable {
                    Image(systemName: "arrow.right.circle.fill")
                        .foregroundColor(CortexDesign.accent)
                }
            }
            .padding(11)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(CortexDesign.cardBackground)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.softBorder.opacity(0.45)))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .opacity(connectable ? 1 : 0.7)
        }
        .buttonStyle(.plain)
        .disabled(!connectable || starting)
        .help(connectable ? "Sign in to \(name) and import your data" : "\(name) sign-in is coming soon")
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
                    .foregroundColor(CortexDesign.ink)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            Spacer()
        }
    }
}
