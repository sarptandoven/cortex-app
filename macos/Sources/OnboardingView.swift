import SwiftUI
import UniformTypeIdentifiers

/// A fast, delightful first-run walkthrough for Cortex — "The Archive".
///
/// Three calm beats: welcome (value prop with privacy folded in), add memory (every connect path
/// on one screen with a single primary action), and you're set (next-step pointers plus the
/// compact quick-capture, AI-tools, and first-backup decisions). Each beat animates in with a
/// spring + asymmetric slide and carries ambient motion (`TimelineView`).
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

    /// The three narrative beats of the walkthrough. Independent of `OnboardingStep` (the setup
    /// loop). The old privacy / see-yourself / quick-capture / connect-tools beats are merged into
    /// these three, so the flow only ever shows three screens while every capability survives.
    private enum WalkStep: Int, CaseIterable, Identifiable {
        case welcome
        case addMemory
        case finish

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
                CortexButton(title: "Skip", role: .ghost, size: .small) {
                    skipTapped()
                }
                .help("Skip the walkthrough")
                .accessibilityLabel("Skip the walkthrough")
            }

            // Progress dots — the current beat is a longer, wax-red capsule; visited beats stay
            // filled, unvisited stay quiet. A quiet "Step N of 3" for orientation.
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
            CortexButton(title: "Back", systemImage: "chevron.left", role: .ghost, size: .large) {
                back()
            }
            .disabled(step == .welcome)
            .opacity(step == .welcome ? 0 : 1)

            Spacer()

            trailingFooterButton
        }
        .padding(20)
    }

    /// Exactly one `.primary` action per step: Continue on welcome, the connect card's action on
    /// add-memory (Continue stays quiet there until a source is actually live), Finish at the end.
    @ViewBuilder
    private var trailingFooterButton: some View {
        switch step {
        case .welcome:
            CortexButton(title: "Continue", systemImage: "chevron.right", role: .primary, size: .large) {
                advance()
            }
        case .addMemory:
            CortexButton(
                title: "Continue",
                systemImage: "chevron.right",
                role: state.onboardingHasSource ? .primary : .ghost,
                size: .large
            ) {
                advance()
            }
        case .finish:
            CortexButton(title: "Finish", systemImage: "checkmark.circle", role: .primary, size: .large) {
                finishTapped()
            }
            .disabled(celebrating)
        }
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
    ///
    /// The "Your Archive is ready" celebration is gated on `state.canCompleteOnboarding` — the same
    /// flag `finishOnboarding()` uses to decide whether setup actually completes. Without a connected
    /// source setup can't complete, so we must NOT claim success: instead we route the user back to
    /// the Add-Memory step with a short nudge. The "Explore with sample notes" path connects a real
    /// source (satisfying `.firstSource`), so it still completes honestly and does celebrate.
    private func finishTapped() {
        guard !celebrating else { return }
        guard state.canCompleteOnboarding else {
            state.status = "One more step — connect a source to finish."
            withAnimation(.spring(response: 0.42, dampingFraction: 0.82)) { step = .addMemory }
            return
        }
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
        case .addMemory:
            OnboardingAddMemoryStep(state: state, advance: advance)
        case .finish:
            OnboardingFinishStep(state: state)
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

/// The value prop in two short lines with the privacy reassurance folded in as one quiet caption,
/// over a gently breathing hero mark. The old standalone privacy beat lives on as compact rows
/// inside the "How it works" disclosure — same honesty gates, a third of the reading.
private struct OnboardingWelcomeStep: View {
    let markSpace: Namespace.ID
    @State private var howItWorksExpanded = false

    // The required-account build (CortexRequireAccount=true) syncs memory to the user's account, so
    // the "nothing is uploaded / no account needed" copy is only honest for the local-only build.
    // Gate on the same Info.plist flag AppState.accountRequired reads.
    private var accountRequired: Bool {
        (Bundle.main.object(forInfoDictionaryKey: "CortexRequireAccount") as? String)?.lowercased() == "true"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack {
                Spacer()
                OnboardingHeroMark(systemImage: "brain.head.profile", tint: CortexDesign.accent)
                    .matchedGeometryEffect(id: "hero", in: markSpace)
                Spacer()
            }
            .padding(.top, 6)

            VStack(alignment: .leading, spacing: 12) {
                Text("Welcome to \(DistributionMode.appDisplayName)")
                    .font(CortexDesign.Typography.display(30))
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)

                Text("A private archive of what you know — your notes, distilled into memory you can search and let your AI tools cite.")
                    .font(CortexDesign.Typography.prose(16))
                    .lineSpacing(4)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)

                // The privacy beat, folded into one quiet line.
                Text(accountRequired
                     ? "Built and kept on your Mac, synced privately to your account. Your memory is always yours."
                     : "Everything stays on your Mac — nothing is uploaded, and there's no account to create.")
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkFaint)
                    .fixedSize(horizontal: false, vertical: true)
            }

            howItWorks
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// The old privacy + philosophy beats, compressed into a quiet disclosure.
    private var howItWorks: some View {
        DisclosureGroup(isExpanded: $howItWorksExpanded) {
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
                OnboardingCheckRow(
                    title: "A calm, considered space",
                    detail: "No feed, no noise — just your memory, kept like a well-tended library.",
                    systemImage: "books.vertical",
                    color: CortexDesign.gold
                )
            }
            .padding(.top, 10)
        } label: {
            Text("How it works")
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(CortexDesign.inkSecondary)
        }
        .padding(.top, 2)
    }
}

// MARK: - Step 2: Add your memory

/// Every first-source path on one screen: connect a notes folder (the ONE primary action), the
/// one-click app grid, the ChatGPT / Claude export drop target + file picker, and the
/// "Explore with sample notes" escape hatch. Preserves the original first-source actions:
/// `connectLocalNotesFolder` and `loadSampleNotes`, and honors the "drag in a ChatGPT / Claude
/// export" promise with a real drop target + file picker.
private struct OnboardingAddMemoryStep: View {
    @ObservedObject var state: AppState
    /// Called after sample notes load so the walkthrough moves forward to the closing beat.
    let advance: () -> Void

    @State private var loadingSamples = false
    @State private var dropTargeted = false

    private var obsidianConnector: SourceConnectorCatalogItem? {
        state.sourceConnectorCatalog.first { $0.id == "obsidian" }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .top, spacing: 22) {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Add your first memory")
                        .font(CortexDesign.Typography.display(26))
                        .foregroundColor(CortexDesign.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("Point Cortex at your notes — it distills the useful parts into memory. Add more later.")
                        .font(CortexDesign.Typography.prose(15))
                        .lineSpacing(3)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                OnboardingDistillMark()
                    .frame(width: 96)
                    .padding(.top, 2)
            }

            // The card carries the step's `.primary` while no source is live; once one is, the
            // card relaxes to `.secondary` ("Change source") and the footer Continue takes over.
            OnboardingConnectionCard(
                title: connectTitle,
                detail: connectDetail,
                systemImage: connectIcon,
                isPrimary: !state.onboardingHasSource,
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

    /// The lighter, ghost-role path: bundled sample notes so a brand-new user (or a reviewer with
    /// no files of their own) can see the full memory picture instantly, then advance.
    @ViewBuilder
    private var sampleNotesOption: some View {
        VStack(alignment: .leading, spacing: 4) {
            CortexButton(
                title: loadingSamples ? "Loading sample notes…" : "Explore with sample notes",
                systemImage: "sparkles",
                role: .ghost
            ) {
                exploreWithSampleNotes()
            }
            .disabled(loadingSamples || state.isBusy)

            Text("No files of your own yet? Try Cortex on a small set of example notes.")
                .font(.caption)
                .foregroundColor(CortexDesign.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
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
                        CortexButton(title: "Choose export file…", systemImage: "folder.badge.plus", role: .ghost, size: .small) {
                            state.importAIChatExport()
                        }
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
            return "Cortex found usable memory from your connected source. Continue when ready."
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

// MARK: - Step 3: You're set

/// The closing beat: the Constellation preview and the review → ask → cited-answers loop as
/// next-step pointers, plus the compact quick-capture opt-in, the AI-tools launch point, and the
/// first-backup decision (the last setup-loop gate). Merged from the old see-yourself,
/// quick-capture, and connect-tools beats so the walkthrough closes on one screen.
private struct OnboardingFinishStep: View {
    @ObservedObject var state: AppState

    private var memoryCount: Int {
        state.stats?.memories ?? state.graphNodes.count
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            VStack(alignment: .leading, spacing: 10) {
                Text("You're set")
                    .font(CortexDesign.Typography.display(26))
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text("As memory accumulates, Cortex draws Your Constellation — review it, ask it questions, get cited answers.")
                    .font(CortexDesign.Typography.prose(15))
                    .lineSpacing(3)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // The user's REAL graph, laid out by the same engine as the full map — never a
            // decorative fake. With no data yet it says so honestly ("your map builds as you
            // import") instead of pretending.
            ConstellationMiniPreview(nodes: state.graphNodes, edges: state.graphEdges)
                .frame(height: 140)
                .frame(maxWidth: .infinity)
                .background(CortexDesign.panelBackground)
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(CortexDesign.softBorder, lineWidth: 1))
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .overlay(alignment: .bottomLeading) {
                    Text(memoryCount > 0 ? "Your Constellation · \(memoryCount) memories" : "Your Constellation")
                        .font(.system(size: 11, weight: .medium, design: .monospaced))
                        .foregroundColor(CortexDesign.inkFaint)
                        .padding(10)
                }

            // The proof moment: waits for the first external AI read and flips to
            // "<app> just read your memory. Continuity, proven." Purely observational —
            // it polls only while this step is on screen and never blocks Finish.
            RecallProofWatcher(state: state, waitingLine: "Waiting for your first external read…")

            VStack(alignment: .leading, spacing: 10) {
                OnboardingFlowRow(index: 1, title: "Review", detail: "Approve the memory worth keeping.", systemImage: "checklist")
                OnboardingFlowRow(index: 2, title: "Ask", detail: "Question your memory in plain language.", systemImage: "sparkle.magnifyingglass")
                OnboardingFlowRow(index: 3, title: "Cited answers", detail: "Every answer links back to the source.", systemImage: "quote.bubble")
            }

            OnboardingQuickCaptureRow(state: state)

            settingsCard
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .task {
            await state.loadStats()
            await state.loadProfile()
            // Fresh graph data for the real-data Constellation preview above.
            await state.loadGraph()
        }
    }

    /// AI tools + the first-backup decision, as two compact rows with sensible defaults —
    /// everything here can also be revisited later from Connections / Settings.
    private var settingsCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            aiToolsRow
            Divider().opacity(0.5)
            backupRow
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(CortexDesign.softBorder, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    /// The old connect-tools beat as one row: nothing is required to finish.
    private var aiToolsRow: some View {
        HStack(alignment: .center, spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Connect your AI tools")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(CortexDesign.ink)
                Text("Let agents like Claude read and cite your approved memory — set up anytime.")
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            CortexButton(title: "Open Connections", systemImage: "link.circle", role: .secondary, size: .small) {
                state.openConnectionsPrivacy(statusMessage: "Connect your AI tools")
            }
        }
    }

    /// The setup loop's last gate (`OnboardingStep.trustBackup`) needs an explicit first-backup
    /// decision. Offer it here — back up now, or skip and decide later from Settings — so the
    /// walkthrough can genuinely complete onboarding instead of only dismissing for the session.
    @ViewBuilder
    private var backupRow: some View {
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
            HStack(alignment: .center, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Back up your memory")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(CortexDesign.ink)
                    Text("Save a restorable snapshot now, or decide later in Settings.")
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                CortexButton(title: "Back Up Now", systemImage: "archivebox", role: .secondary, size: .small) {
                    state.createBackup()
                }
                CortexButton(title: "Skip for now", role: .ghost, size: .small) {
                    state.skipFirstBackup()
                }
            }
        }
    }
}

/// The quick-capture opt-in, compressed to one compact card. In App Store builds screen and
/// keyboard capture are sandbox-incompatible, so this renders as a positive reassurance row
/// instead — no dead toggle, and no reference to any other place to get the app (steering users
/// off the App Store is a Guideline 4 / 2.3.2 issue).
private struct OnboardingQuickCaptureRow: View {
    @ObservedObject var state: AppState

    private var isMAS: Bool { DistributionMode.isAppStore }

    /// Flipping the toggle on with no shortcut yet recorded would register no hotkey — the feature
    /// would read as "on" while doing nothing. Seed the shared ⌥⌘C default (`KeyCombo.defaultCapture`,
    /// the same combo the celebration copy and Settings use) the moment it turns on so quick capture
    /// is immediately functional; the recorder below still lets the user rebind it.
    private var enabledBinding: Binding<Bool> {
        Binding(
            get: { state.quickCaptureEnabled },
            set: { newValue in
                if newValue, state.quickCaptureKeybind == nil {
                    state.quickCaptureKeybind = KeyCombo.defaultCapture
                }
                state.quickCaptureEnabled = newValue
            }
        )
    }

    var body: some View {
        Group {
            if isMAS {
                OnboardingCheckRow(
                    title: "Everything becomes memory, automatically",
                    detail: "Add notes or import your chats any time and \(DistributionMode.appDisplayName) distills them into cited memory on your Mac — no extra setup.",
                    systemImage: "checkmark.seal",
                    color: CortexDesign.gold
                )
            } else {
                enableRows
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(CortexDesign.softBorder, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    /// Direct-download build: a live opt-in toggle + a keybind recorder, bound to AppState.
    private var enableRows: some View {
        VStack(alignment: .leading, spacing: 12) {
            Toggle(isOn: enabledBinding) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Quick capture")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(CortexDesign.ink)
                    Text("Optional — save anything with a global shortcut. Change it anytime in Settings.")
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .toggleStyle(.switch)

            if state.quickCaptureEnabled {
                Divider().opacity(0.5)
                HStack(alignment: .center, spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
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

                // Pure-visual preview of the capture pill so the user experiences the notch during
                // onboarding (no backend needed). Uses the same .captured style that a real quick
                // capture surfaces. Direct-download only — deliberately absent from the MAS row.
                CortexButton(title: "Show me the notch", systemImage: "bell.badge", role: .ghost, size: .small) {
                    NotchNotifier.shared.show(
                        title: "Saved to Cortex",
                        subtitle: "This is what a quick capture looks like.",
                        style: .captured
                    )
                }
            }
        }
        .animation(.easeInOut(duration: 0.22), value: state.quickCaptureEnabled)
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

// (The old OnboardingConstellationPreview — a hardcoded 8-node decoration — was replaced by
// ConstellationMiniPreview in CortexNorthStar.swift, which draws the user's REAL graph.)

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
            CortexButton(
                title: buttonTitle,
                systemImage: buttonSystemImage,
                role: isPrimary ? .primary : .secondary,
                size: .large,
                fullWidth: true,
                action: action
            )
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .background(isPrimary ? CortexDesign.panelBackground : CortexDesign.cardBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke((isPrimary ? Color.accentColor : Color(nsColor: .separatorColor)).opacity(isPrimary ? 0.32 : 0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
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

/// One compact "what's next" pointer row (Review → Ask → Cited answers).
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
