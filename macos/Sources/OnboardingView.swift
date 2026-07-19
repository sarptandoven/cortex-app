import SwiftUI
import AppKit
import UniformTypeIdentifiers

/// A fast, delightful first-run walkthrough for Cortex — "The Archive".
///
/// Four calm beats: welcome (value prop with privacy folded in), add memory (every connect path
/// on one screen with a single primary action), use it (connect an AI tool so your memory travels
/// into Claude/ChatGPT/Cursor, the payoff), and you're set (next-step pointers plus the compact
/// quick-capture and first-backup decisions). Each beat animates in with a spring + asymmetric
/// slide and carries ambient motion (`TimelineView`).
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

    /// The four narrative beats of the walkthrough. Independent of `OnboardingStep` (the setup
    /// loop). The arc is deliberate: welcome (the promise), addMemory (capture), useIt (the PAYOFF:
    /// use your memory inside Claude, ChatGPT, Cursor), and finish (review it, then you're set). The
    /// old privacy / see-yourself / quick-capture beats are folded into these, and "connect an AI
    /// tool" is promoted from a finish-step footnote to its own emphasized beat, because using your
    /// memory where you work IS the point of Cortex.
    private enum WalkStep: Int, CaseIterable, Identifiable {
        case welcome
        case addMemory
        case useIt
        case finish

        var id: Int { rawValue }
    }

    @State private var step: WalkStep = .welcome
    @State private var celebrating = false
    /// Drives the continuity mark that slides between the header and step content.
    @Namespace private var markSpace

    /// An inline notice rendered at the top of the step area — the ONLY place the walkthrough speaks
    /// back to the user about why an action didn't complete (e.g. Finish needs a source first). This
    /// replaces the old silent `state.status` write that OnboardingView never rendered, so tapping
    /// Finish without a connected source no longer bounces the user back with no explanation.
    @State private var notice: OnboardingNotice?

    /// The "Restore from your account" branch (the "1Password moment"). When non-nil it takes over
    /// the step area with the restore sub-flow (sign in → restoring → welcome back), independent of
    /// the four-beat setup walkthrough so the main "Step N of 4" flow and its contract are untouched.
    /// `.signIn` reuses the existing cloud sign-in; once signed in it advances to `.restoring` (live
    /// pull progress), then `.welcomeBack` (the confirmation).
    @State private var restoreStage: OnboardingRestoreStageProxy?

    private var steps: [WalkStep] { WalkStep.allCases }

    var body: some View {
        ZStack {
            VStack(spacing: 0) {
                header
                Divider().opacity(0.5)
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        // The one inline voice of the walkthrough — why an action didn't complete.
                        // Rendered here so it can never be silent again (the old status bounce bug).
                        if let notice {
                            OnboardingNoticeBanner(notice: notice) {
                                withAnimation(.spring(response: 0.42, dampingFraction: 0.82)) { self.notice = nil }
                            }
                            .transition(.asymmetric(
                                insertion: .move(edge: .top).combined(with: .opacity),
                                removal: .opacity
                            ))
                        }

                        Group {
                            if let restoreStage {
                                OnboardingRestoreFlow(
                                    state: state,
                                    stage: restoreStage,
                                    markSpace: markSpace,
                                    advanceToRestoring: { withAnimation(.spring(response: 0.42, dampingFraction: 0.82)) { self.restoreStage = .restoring } },
                                    advanceToWelcomeBack: { withAnimation(.spring(response: 0.42, dampingFraction: 0.82)) { self.restoreStage = .welcomeBack } },
                                    exitRestore: { exitRestoreFlow() },
                                    finishRestore: { finishRestoreFlow() }
                                )
                                .id(restoreStage)
                            } else {
                                stepContent
                                    .id(step)
                            }
                        }
                        .transition(.asymmetric(
                            insertion: .move(edge: .trailing).combined(with: .opacity),
                            removal: .move(edge: .leading).combined(with: .opacity)
                        ))
                    }
                    .padding(.horizontal, 44)
                    .padding(.vertical, 34)
                    .frame(maxWidth: 600, alignment: .leading)
                    .frame(maxWidth: .infinity)
                }
                .animation(.spring(response: 0.42, dampingFraction: 0.82), value: step)
                .animation(.spring(response: 0.42, dampingFraction: 0.82), value: restoreStage)
                .animation(.spring(response: 0.42, dampingFraction: 0.82), value: notice)
                Divider().opacity(0.5)
                footer
            }

            if celebrating {
                celebrationOverlay
                    .transition(.opacity)
            }
        }
        .background(OnboardingAmbientBackground())
        // The GitHub tile on the Add-Memory step starts the device flow, and the browser then asks
        // for a user code — so onboarding must present the code sheet itself. The only other hosts
        // of GitHubDeviceCodeView live inside ConnectionsPrivacySheet, which is never in the
        // hierarchy while the onboarding sheet is up (sibling sheets on the same presenter).
        // Dismissing sets `githubDeviceFlow` back to nil via the binding, so the poll loop exits —
        // matching the Connections sheet behavior.
        .sheet(item: Binding(get: { state.githubDeviceFlow }, set: { state.githubDeviceFlow = $0 })) { prompt in
            GitHubDeviceCodeView(state: state, prompt: prompt)
        }
    }

    /// Enter the restore branch from the welcome beat. Always land on the sign-in stage — even when a
    /// session already exists (rare on a truly fresh Mac, but possible: a leftover, or a DIFFERENT/empty
    /// account). `OnboardingRestoreSignInStep` detects the live session in its own `.onAppear` and shows
    /// the "restore this account?" confirmation guard, so we never silently pull down the wrong account.
    /// (Jumping straight to `.restoring` here would bypass that guard — a confirmed regression.)
    private func enterRestoreFlow() {
        state.restoreProgress = .idle
        withAnimation(.spring(response: 0.42, dampingFraction: 0.82)) {
            restoreStage = .signIn
        }
    }

    /// Leave the restore branch back to the normal welcome beat (e.g. the user changes their mind or
    /// their account was empty and they'd rather start fresh). Never bricks onboarding.
    private func exitRestoreFlow() {
        withAnimation(.spring(response: 0.42, dampingFraction: 0.82)) {
            restoreStage = nil
            step = .welcome
        }
    }

    /// Restore is complete — hand off to the normal finish so the setup gates stay honest. A restored
    /// account has a connected source (its pulled captures), so this genuinely completes onboarding.
    private func finishRestoreFlow() {
        restoreStage = nil
        finishTapped()
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
            // filled, unvisited stay quiet. A quiet "Step N of 4" for orientation.
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
            if restoreStage != nil {
                // The restore branch carries its own actions in-content; the footer only offers a
                // way back out so a returning user is never trapped mid-restore.
                CortexButton(title: "Back", systemImage: "chevron.left", role: .ghost, size: .large) {
                    exitRestoreFlow()
                }
                Spacer()
            } else {
                CortexButton(title: "Back", systemImage: "chevron.left", role: .ghost, size: .large) {
                    back()
                }
                // Block navigation while a source is loading (e.g. "Explore with sample notes" runs
                // state.isBusy for its whole duration). Without this gate the footer Back/Continue/
                // Finish stayed live mid-load, letting a user advance PAST the onboarding gates before
                // the source finished — arriving at a step whose gate was not yet met.
                .disabled(step == .welcome || state.isBusy)
                .opacity(step == .welcome ? 0 : 1)

                Spacer()

                trailingFooterButton
                    // The trailing action is gated on isBusy too so Continue / Finish can't fire
                    // while a source load is still in flight (the mid-load advance-past-gate bug).
                    .disabled(state.isBusy)
            }
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
            // When a source is connected but its first sync hasn't produced citable memory yet,
            // "Continue" would advance to a step whose gate isn't met and Finish would bounce the
            // user straight back here. Reflect that honestly: relabel to "Finishing sync…" and
            // disable, so Continue only advances once the source is genuinely ready.
            CortexButton(
                title: addMemorySyncing ? "Finishing sync…" : "Continue",
                systemImage: addMemorySyncing ? "arrow.triangle.2.circlepath" : "chevron.right",
                role: state.onboardingHasSource ? .primary : .ghost,
                size: .large
            ) {
                advance()
            }
            .disabled(addMemorySyncing)
        case .useIt:
            // The connect-a-tool card carries this beat's emphasized (wax) call to action in-content;
            // the footer keeps a quiet forward path so a user who wants to wire tools later isn't
            // trapped on the payoff screen.
            //
            // U-ONB4: the old "Do this later" primary was self-defeating — it framed the payoff beat's
            // forward path as skipping the payoff. The neutral "Continue" simply advances the
            // walkthrough (the in-content hero card is the connect affordance), so moving on never
            // reads as opting out.
            CortexButton(
                title: "Continue",
                systemImage: "chevron.right",
                role: state.connectedAIIntegrationCount > 0 ? .primary : .secondary,
                size: .large
            ) {
                advance()
            }
        case .finish:
            // Single-source the gate on `canCompleteOnboarding` — the SAME flag `finishOnboarding()`
            // uses. Finish only appears when tapping it will genuinely complete setup; until then the
            // action is an honest "Add a source" that routes back to the Add-Memory step with the
            // inline notice, so the primary never silently bounces.
            if state.canCompleteOnboarding {
                CortexButton(title: "Finish", systemImage: "checkmark.circle", role: .primary, size: .large) {
                    finishTapped()
                }
                .disabled(celebrating)
            } else {
                CortexButton(title: "Add a source", systemImage: "arrow.left", role: .primary, size: .large) {
                    finishTapped()
                }
                .disabled(celebrating)
                .help("Connect a source to finish setup")
            }
        }
    }

    /// True while the add-memory beat has a source connected but not yet ready to continue on: a
    /// connector sync is actively running, OR a memory layer is wired but its first sync hasn't yet
    /// produced citable memory (`onboardingHasSyncedMemory` still false). In that window advancing
    /// would land on a step whose gate isn't met and Finish would bounce the user back — so the
    /// footer's Continue reflects the syncing state instead of pretending the gate is satisfied.
    private var addMemorySyncing: Bool {
        if !state.connectorSyncingIDs.isEmpty { return true }
        return state.onboardingHasConnectedMemoryLayer && !state.onboardingHasSyncedMemory
    }

    // MARK: - Navigation

    private func advance() {
        guard let next = WalkStep(rawValue: step.rawValue + 1) else { return }
        withAnimation(.spring(response: 0.42, dampingFraction: 0.82)) {
            notice = nil
            step = next
        }
    }

    private func back() {
        guard let prev = WalkStep(rawValue: step.rawValue - 1) else { return }
        withAnimation(.spring(response: 0.42, dampingFraction: 0.82)) {
            notice = nil
            step = prev
        }
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
    /// the Add-Memory step AND surface a visible inline notice (the reason is never silent). The
    /// "Explore with sample notes" path connects a real source (satisfying `.firstSource`), so it
    /// still completes honestly and does celebrate.
    ///
    /// The trailing footer already single-sources its gate on `canCompleteOnboarding` (Finish only
    /// renders when it will actually complete), so this guard is a belt-and-braces backstop: if it
    /// ever fires, it must be legible, not a silent bounce.
    private func finishTapped() {
        guard !celebrating else { return }
        guard state.canCompleteOnboarding else {
            withAnimation(.spring(response: 0.42, dampingFraction: 0.82)) {
                notice = OnboardingNotice(
                    severity: .info,
                    title: "One more step to finish",
                    message: needsSourceMessage
                )
                step = .addMemory
            }
            return
        }
        notice = nil
        withAnimation(.spring(response: 0.4, dampingFraction: 0.75)) { celebrating = true }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.15) {
            state.finishOnboarding()
        }
    }

    /// The honest, specific reason setup can't complete yet, drawn from the SAME step gates
    /// `finishOnboarding()` enforces — so the notice names what's actually missing (usually a source)
    /// instead of a generic nudge.
    private var needsSourceMessage: String {
        if !state.hasAtLeastOneConnectedSource {
            return "Connect a source above: your notes folder, an app, or a ChatGPT / Claude export, then Finish. Or explore with sample notes."
        }
        let remaining = state.incompleteOnboardingStepTitles.prefix(2).joined(separator: " · ")
        return remaining.isEmpty
            ? "Complete the first memory loop, then Finish."
            : "Still to do: \(remaining)."
    }

    // MARK: - Step content

    @ViewBuilder
    private var stepContent: some View {
        switch step {
        case .welcome:
            OnboardingWelcomeStep(
                markSpace: markSpace,
                // The restore branch signs into a Cortex account and pulls memory down. On builds
                // where cloud auth is unavailable that flow dead-ends on an empty provider list, so
                // gate the ENTRY on `isCloudAuthAvailable` — not merely on the closure being non-nil.
                cloudAuthAvailable: state.isCloudAuthAvailable,
                onRestore: { enterRestoreFlow() }
            )
        case .addMemory:
            OnboardingAddMemoryStep(
                state: state,
                advance: advance,
                showNotice: { incoming in
                    withAnimation(.spring(response: 0.42, dampingFraction: 0.82)) { notice = incoming }
                }
            )
        case .useIt:
            OnboardingUseItStep(state: state)
        case .finish:
            OnboardingFinishStep(state: state)
        }
    }

    // MARK: - Celebration

    /// A short full-panel beat before the sheet closes — the one thing worth remembering (the
    /// ⌃⌥Space hotkey) gets its moment.
    private var celebrationOverlay: some View {
        VStack(spacing: 16) {
            CortexWaxSeal(size: 74)
            Text("Your Archive is ready")
                .font(CortexDesign.Typography.display(26))
                .foregroundColor(CortexDesign.ink)
            Text("Now use it where you work. Ask in \(DistributionMode.appDisplayName) with ⌃⌥Space, or let Claude, ChatGPT, and Cursor cite your memory.")
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

// MARK: - Restore branch (the "1Password moment")

/// The returning-user restore sub-flow: sign in → live "Restoring your memory…" → "Welcome back".
/// It reuses the EXISTING cloud sign-in (AppState.signInToCloud* / provider discovery) and the
/// EXISTING pull-sync (AppState.restoreFromAccount, which drives CortexPullSync); it never does its
/// own networking. Honesty is load-bearing: it never claims memory is restored until the pull has
/// genuinely applied ≥1 item (state.restoreProgress.isConfirmedRestored), and it reports an empty
/// account plainly. The confirmation reuses the REAL ConstellationMiniPreview + RecallHeadlineCard
/// so the memory truly feels like it came back.
private struct OnboardingRestoreFlow: View {
    @ObservedObject var state: AppState
    let stage: OnboardingRestoreStageProxy
    let markSpace: Namespace.ID
    let advanceToRestoring: () -> Void
    let advanceToWelcomeBack: () -> Void
    let exitRestore: () -> Void
    let finishRestore: () -> Void

    var body: some View {
        switch stage {
        case .signIn:
            OnboardingRestoreSignInStep(state: state, markSpace: markSpace, onSignedIn: advanceToRestoring)
        case .restoring:
            OnboardingRestoringStep(
                state: state,
                onRestored: advanceToWelcomeBack,
                onEmpty: exitRestore
            )
        case .welcomeBack:
            OnboardingWelcomeBackStep(state: state, markSpace: markSpace, onFinish: finishRestore)
        }
    }
}

/// The three stages of the restore branch, shared by `OnboardingView` (which drives the cursor) and
/// the extracted `OnboardingRestoreFlow` sub-view (which renders each stage).
enum OnboardingRestoreStageProxy: Equatable {
    case signIn
    case restoring
    case welcomeBack
}

/// Step 1 of restore — sign in. Reuses the exact cloud sign-in entry points the Settings surface
/// uses (browser-provider handoff + email/password), so there is one auth code path and no drift.
///
/// A session landing does NOT silently push the user into a live restore: a leftover session for a
/// different (or empty) account would then start pulling someone else's — or nothing — with only the
/// footer Back to recover. Instead, when `isSignedIn` is true we present an explicit "Signed in as
/// {email} — not you?" confirmation, and the user must choose to restore THIS account or sign out and
/// pick another.
private struct OnboardingRestoreSignInStep: View {
    @ObservedObject var state: AppState
    let markSpace: Namespace.ID
    let onSignedIn: () -> Void

    @State private var email: String = ""
    @State private var password: String = ""
    @State private var hostedURL: String = ""
    /// True once a session is detected — flips the surface to the account-confirmation guard instead
    /// of auto-advancing into the live restore.
    @State private var confirmingAccount = false

    private var resolvedHostedURL: String {
        let trimmed = hostedURL.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? AppState.defaultHostedURL : trimmed
    }

    /// Social providers reached via the browser handoff (Apple has its own native flow elsewhere;
    /// here we keep the returning-user path simple and lean on the universal browser + email paths).
    private var browserProviders: [CloudAuthProvider] {
        state.cloudAuthProviders.filter { $0.provider.lowercased() != "apple" }
    }

    private var credentialsIncomplete: Bool {
        state.cloudAuthBusy
            || email.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || password.isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack {
                Spacer()
                CortexWaxSeal(size: 66)
                    .matchedGeometryEffect(id: "hero", in: markSpace)
                Spacer()
            }
            .padding(.top, 6)

            if confirmingAccount {
                accountConfirmation
            } else {
                signInForm
            }

            Text("Offline? You can Skip and start fresh. Signing in later will still restore your memory.")
                .font(.caption)
                .foregroundColor(CortexDesign.inkFaint)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .animation(.spring(response: 0.4, dampingFraction: 0.82), value: confirmingAccount)
        .onAppear {
            if email.isEmpty { email = state.cloudAccountEmail }
            if state.isCloudAuthAvailable {
                state.loadCloudAuthProviders(hostedURL: resolvedHostedURL)
            }
            // A session may already exist (e.g. a leftover from a prior sign-in, possibly a DIFFERENT
            // or empty account). Never auto-restore it — ask which account first.
            if state.isSignedIn { confirmingAccount = true }
        }
        // The single source of truth for "signed in" is a stored refresh token. The moment one lands,
        // surface the confirmation guard rather than advancing straight into the live restore.
        .onChange(of: state.isSignedIn) { signedIn in
            confirmingAccount = signedIn
        }
    }

    // MARK: Sign-in form

    private var signInForm: some View {
        VStack(alignment: .leading, spacing: 20) {
            VStack(alignment: .leading, spacing: 10) {
                Text("Restore from your account")
                    .font(CortexDesign.Typography.display(26))
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text("Sign in to the account you used before. Your memory rebuilds itself on this Mac, nothing is retyped or re-imported.")
                    .font(CortexDesign.Typography.prose(15))
                    .lineSpacing(3)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            VStack(alignment: .leading, spacing: 12) {
                ForEach(browserProviders) { provider in
                    CortexButton(
                        title: providerButtonLabel(provider),
                        systemImage: providerButtonIcon(provider),
                        role: .secondary,
                        size: .large,
                        fullWidth: true
                    ) {
                        state.signInToCloudWithBrowser(hostedURL: resolvedHostedURL, provider: provider.provider)
                    }
                    .disabled(state.cloudAuthBusy)
                }

                if browserProviders.isEmpty {
                    CortexButton(title: "Continue in browser", systemImage: "globe", role: .secondary, size: .large, fullWidth: true) {
                        state.signInToCloudWithBrowser(hostedURL: resolvedHostedURL)
                    }
                    .disabled(state.cloudAuthBusy)
                }

                OnboardingOrDivider()

                CortexField(
                    placeholder: "Email",
                    text: $email,
                    textContentType: .username,
                    disableAutocorrection: true
                )
                CortexField(
                    placeholder: "Password",
                    text: $password,
                    secure: true,
                    textContentType: .password
                )
                HStack {
                    CortexButton(title: "Sign in", systemImage: "person.crop.circle.badge.checkmark", role: .primary) {
                        state.signInToCloud(hostedURL: resolvedHostedURL, email: email, password: password)
                    }
                    .disabled(credentialsIncomplete)
                    if state.cloudAuthBusy {
                        ProgressView().controlSize(.small)
                        CortexButton(title: "Cancel", role: .ghost, size: .small) {
                            state.cancelCloudBrowserSignIn()
                        }
                    }
                    Spacer()
                }
            }

            if !state.cloudAuthMessage.isEmpty {
                OnboardingNoticeBanner(
                    notice: OnboardingNotice(severity: .warning, title: nil, message: state.cloudAuthMessage),
                    onDismiss: nil
                )
            }
        }
    }

    // MARK: Account confirmation guard ("not you?")

    /// Confirm WHICH account is about to be restored before pulling anything down. A leftover session
    /// for a different/empty account must not silently start restoring — the user explicitly confirms
    /// this is theirs, or signs out and picks another.
    private var accountConfirmation: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 10) {
                Text("Signed in. Restore this account?")
                    .font(CortexDesign.Typography.display(24))
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text("We'll pull this account's memory down onto this Mac. Make sure it's yours before we begin.")
                    .font(CortexDesign.Typography.prose(15))
                    .lineSpacing(3)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // The identity card — who is actually signed in right now.
            HStack(alignment: .center, spacing: 12) {
                Image(systemName: "person.crop.circle.badge.checkmark")
                    .font(.title2)
                    .foregroundColor(CortexDesign.sealMoss)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Signed in as")
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.8)
                        .foregroundColor(CortexDesign.inkFaint)
                    Text(signedInIdentity)
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(CortexDesign.ink)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(CortexDesign.panelBackground)
            .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
            .embossedBorder()

            HStack(spacing: 10) {
                CortexButton(title: "Restore this account", systemImage: "arrow.down.circle", role: .primary, size: .large) {
                    onSignedIn()
                }
                CortexButton(title: "Not you? Sign in as someone else", role: .ghost, size: .large) {
                    state.signOutOfCloud()
                    // signOutOfCloud clears the refresh token → isSignedIn flips false → onChange
                    // sets confirmingAccount = false. Set it here too so the form returns immediately.
                    confirmingAccount = false
                    password = ""
                    email = state.cloudAccountEmail
                }
                Spacer(minLength: 0)
            }
        }
    }

    /// The best available label for the signed-in account. Falls back to a plain descriptor rather
    /// than an empty string when the email isn't recorded (e.g. a browser/social session).
    private var signedInIdentity: String {
        let recorded = state.cloudAccountEmail.trimmingCharacters(in: .whitespacesAndNewlines)
        return recorded.isEmpty ? "your \(DistributionMode.appDisplayName) account" : recorded
    }

    private func providerButtonLabel(_ provider: CloudAuthProvider) -> String {
        switch provider.provider.lowercased() {
        case "github": return "Sign in with GitHub"
        case "google": return "Continue with Google"
        default: return "Continue with \(provider.display_name)"
        }
    }

    private func providerButtonIcon(_ provider: CloudAuthProvider) -> String {
        switch provider.provider.lowercased() {
        case "github": return "chevron.left.forwardslash.chevron.right"
        case "google": return "globe"
        default: return "arrow.up.forward.app"
        }
    }
}

/// Step 2 of restore — the live pull. Kicks AppState.restoreFromAccount() (which runs the real
/// pull-sync) and narrates its honest progress: "Restoring your memory… N memories recovered".
/// It advances to the confirmation ONLY once the pull genuinely applied ≥1 item; an empty account
/// bows out cleanly instead of pretending.
private struct OnboardingRestoringStep: View {
    @ObservedObject var state: AppState
    let onRestored: () -> Void
    let onEmpty: () -> Void

    @State private var started = false

    private var recovered: Int { state.restoreProgress.recovered }

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack {
                Spacer()
                OnboardingHeroMark(systemImage: "arrow.triangle.2.circlepath", tint: CortexDesign.accent)
                Spacer()
            }
            .padding(.top, 6)

            VStack(alignment: .leading, spacing: 12) {
                Text(headline)
                    .font(CortexDesign.Typography.display(26))
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text(detail)
                    .font(CortexDesign.Typography.prose(15))
                    .lineSpacing(3)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // The live count — honest: it only ever reflects captures the pull has actually applied.
            HStack(spacing: 10) {
                if isActive { ProgressView().controlSize(.small) }
                Text(recovered > 0 ? "\(recovered) memories recovered" : "Reaching your account…")
                    .font(.system(size: 13, weight: .medium, design: .monospaced))
                    .foregroundColor(CortexDesign.inkSecondary)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(CortexDesign.panelBackground)
            .onboardingPanel(radius: 10)

            if case .failed(let message) = state.restoreProgress {
                VStack(alignment: .leading, spacing: 8) {
                    Text(message)
                        .font(.caption)
                        .foregroundColor(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 8) {
                        CortexButton(title: "Try again", systemImage: "arrow.clockwise", role: .secondary, size: .small) {
                            started = false
                            beginRestoreIfNeeded()
                        }
                        CortexButton(title: "Skip for now", role: .ghost, size: .small) {
                            onEmpty()
                        }
                    }
                }
            }

            if case .empty = state.restoreProgress {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Nothing to restore yet: this account has no memory to pull down. You can start fresh and it will sync from here.")
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    CortexButton(title: "Continue setup", systemImage: "chevron.right", role: .secondary, size: .small) {
                        onEmpty()
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .task { beginRestoreIfNeeded() }
        .onChange(of: state.restoreProgress) { progress in
            if progress.isConfirmedRestored { onRestored() }
        }
    }

    private var isActive: Bool {
        if case .restoring = state.restoreProgress { return true }
        return false
    }

    private var headline: String {
        if case .failed = state.restoreProgress { return "Restore paused" }
        if case .empty = state.restoreProgress { return "Your account is empty" }
        return "Restoring your memory…"
    }

    private var detail: String {
        if case .failed = state.restoreProgress {
            return "Your memory is safe in your account, nothing was lost. It will keep restoring in the background, or try again."
        }
        if case .empty = state.restoreProgress {
            return "There's nothing here to bring back yet."
        }
        return "Pulling your captures down from your account into this Mac. This can take a moment for a large archive."
    }

    private func beginRestoreIfNeeded() {
        guard !started else { return }
        started = true
        Task { await state.restoreFromAccount() }
    }
}

/// Step 3 of restore — the confirmation. Reuses the REAL ConstellationMiniPreview (drawn from the
/// pulled graph) and RecallHeadlineCard so the memory genuinely feels like it came back, then guides
/// the user to reconnect their AI tools — honestly: tool wiring is per-device and is NOT restored by
/// sync, so we route into the existing Connect-an-app wizard rather than fabricate an auto-restore.
private struct OnboardingWelcomeBackStep: View {
    @ObservedObject var state: AppState
    let markSpace: Namespace.ID
    let onFinish: () -> Void

    private var recovered: Int { state.restoreProgress.recovered }
    private var memoryCount: Int { state.stats?.memories ?? state.graphNodes.count }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack {
                Spacer()
                CortexWaxSeal(size: 66)
                    .matchedGeometryEffect(id: "hero", in: markSpace)
                Spacer()
            }
            .padding(.top, 6)

            VStack(alignment: .leading, spacing: 10) {
                Text("Welcome back. Your memory is restored")
                    .font(CortexDesign.Typography.display(26))
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text(recovered > 0
                     ? "\(recovered) memories are back on this Mac, exactly as you left them. It keeps syncing from here."
                     : "Your account is connected and syncing to this Mac.")
                    .font(CortexDesign.Typography.prose(15))
                    .lineSpacing(3)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // The user's REAL restored graph, drawn by the same engine as the full map.
            ConstellationMiniPreview(nodes: state.graphNodes, edges: state.graphEdges)
                .frame(height: 140)
                .frame(maxWidth: .infinity)
                .background(CortexDesign.panelBackground)
                .onboardingPanel(radius: 12)
                .overlay(alignment: .bottomLeading) {
                    Text(memoryCount > 0 ? "Your Constellation · \(memoryCount) memories" : "Your Constellation")
                        .font(.system(size: 11, weight: .medium, design: .monospaced))
                        .foregroundColor(CortexDesign.inkFaint)
                        .padding(10)
                }

            // The real north-star headline (hidden until the endpoint answers), so the restored
            // account's recall history surfaces here too.
            RecallHeadlineCard(state: state)

            // Honest reconnect guidance: MCP/tool configs are per-device and are NOT synced, so we do
            // NOT claim they auto-restored. Route into the existing Connect-an-app wizard.
            reconnectToolsCard
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .task {
            await state.loadStats()
            await state.loadGraph()
            await state.loadRecallHeadline()
        }
        // U-ONB3: keep the restored-graph preview live. A large restore applies captures over time,
        // and the pull-sync surfaces through the same connector sync set; re-pull the graph + stats as
        // that transitions so the "your memory is restored" preview grows instead of freezing on the
        // first snapshot.
        .onChange(of: state.connectorSyncingIDs) { _ in
            Task {
                await state.loadStats()
                await state.loadGraph()
            }
        }
    }

    private var reconnectToolsCard: some View {
        HStack(alignment: .center, spacing: 10) {
            Image(systemName: "link.circle")
                .font(.title3)
                .foregroundColor(CortexDesign.accent)
            VStack(alignment: .leading, spacing: 2) {
                Text("Reconnect your AI tools")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(CortexDesign.ink)
                Text("Your memory is back. AI-tool wiring is per-device and isn't synced, so reconnect Claude, ChatGPT, or others to use it here.")
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            CortexButton(title: "Connect", systemImage: "link", role: .secondary, size: .small) {
                state.presentConnectToolsWizard(statusMessage: "Reconnect your AI tools")
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(CortexDesign.panelBackground)
        .onboardingPanel(radius: 10)
    }
}

// MARK: - Step 1: Welcome

/// The value prop in two short lines with the privacy reassurance folded in as one quiet caption,
/// over a gently breathing hero mark. The old standalone privacy beat lives on as compact rows
/// inside the "How it works" disclosure — same honesty gates, a third of the reading.
private struct OnboardingWelcomeStep: View {
    let markSpace: Namespace.ID
    /// Whether a Cortex account (and thus a restore) is actually reachable in this build. Mirrors
    /// `AppState.isCloudAuthAvailable`. When false the restore entry is hidden so it can't dead-end.
    var cloudAuthAvailable: Bool = true
    /// Enter the "Restore from your account" branch. Nil hides the entry (e.g. no cloud auth).
    var onRestore: (() -> Void)? = nil
    @State private var howItWorksExpanded = false

    // The required-account build (CortexRequireAccount=true) syncs memory to the user's account, so
    // the "nothing is uploaded / no account needed" copy is only honest for the local-only build.
    // Gate on the same Info.plist flag AppState.accountRequired reads.
    private var accountRequired: Bool {
        (Bundle.main.object(forInfoDictionaryKey: "CortexRequireAccount") as? String)?.lowercased() == "true"
    }

    /// Whether a Cortex account (and thus a restore) is even possible in this build. The restore
    /// branch signs in and pulls memory down; on a build where cloud auth is unavailable that flow
    /// dead-ends on an empty provider list, so require BOTH a real entry closure AND cloud auth being
    /// available (mirrors AppState.isCloudAuthAvailable) — never just the closure being non-nil.
    private var restoreAvailable: Bool {
        onRestore != nil && cloudAuthAvailable
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack {
                Spacer()
                CortexWaxSeal(size: 72)
                    .matchedGeometryEffect(id: "hero", in: markSpace)
                Spacer()
            }
            .padding(.top, 6)

            VStack(alignment: .leading, spacing: 12) {
                Text("Welcome to \(DistributionMode.appDisplayName)")
                    .font(CortexDesign.Typography.display(30))
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)

                Text("A private archive of what you know, distilled from your notes, so Claude, ChatGPT, and Cursor can cite the memory you already have.")
                    .font(CortexDesign.Typography.prose(16))
                    .lineSpacing(4)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)

                // The privacy beat, folded into one quiet line.
                Text(accountRequired
                     ? "Built and kept on your Mac, synced privately to your account. Your memory is always yours."
                     : "Everything stays on your Mac. Nothing is uploaded, and there's no account to create.")
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkFaint)
                    .fixedSize(horizontal: false, vertical: true)
            }

            howItWorks

            if restoreAvailable {
                restoreEntry
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// The returning-user path: "Already have Cortex? Restore from your account." A quiet divider +
    /// ghost action so it never competes with the primary "start fresh / add memory" flow, but is
    /// unmistakable for someone setting up a new Mac. Routes into the restore branch (sign in → pull).
    @ViewBuilder
    private var restoreEntry: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                VStack { Divider() }
                Text("or")
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkFaint)
                VStack { Divider() }
            }
            HStack(alignment: .center, spacing: 10) {
                Image(systemName: "clock.arrow.circlepath")
                    .font(.title3)
                    .foregroundColor(CortexDesign.accent)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Already have \(DistributionMode.appDisplayName)?")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(CortexDesign.ink)
                    Text("Sign in and your memory rebuilds itself on this Mac from your account.")
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                CortexButton(title: "Restore", systemImage: "arrow.down.circle", role: .secondary, size: .small) {
                    onRestore?()
                }
                .accessibilityLabel("Restore from your account")
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(CortexDesign.panelBackground)
            .onboardingPanel(radius: 10)
        }
        .padding(.top, 4)
    }

    /// The old privacy + philosophy beats, compressed into a quiet disclosure — a design-system
    /// disclosure (ghost header + chevron) rather than the stock macOS `DisclosureGroup` triangle.
    private var howItWorks: some View {
        OnboardingDisclosure(title: "How it works", isExpanded: $howItWorksExpanded) {
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
                    detail: "No feed, no noise, just your memory, kept like a well-tended library.",
                    systemImage: "books.vertical",
                    color: CortexDesign.gold
                )
            }
            .padding(.top, 10)
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
    /// Routes a failure up to the walkthrough's inline notice banner — the sheet's ONLY visible
    /// voice — so sample-notes and export-import failures never report solely via the
    /// `state.status` line that the onboarding sheet occludes.
    let showNotice: (OnboardingNotice) -> Void

    @State private var loadingSamples = false
    @State private var dropTargeted = false
    /// Inline feedback for the export drop target: set when a drop can't be used (a non-file drop, or
    /// a file whose type we don't import). Replaces the old silent failure where an unrecognized drop
    /// did nothing at all. Cleared on the next successful drop / file-picker use.
    @State private var dropFeedback: String?

    private var obsidianConnector: SourceConnectorCatalogItem? {
        state.sourceConnectorCatalog.first { $0.id == "obsidian" }
    }

    /// U-ONB6: the notes-folder path must always connect IN onboarding, never bounce OUT to
    /// Connections. The real folder-connect (`connectLocalNotesFolder`) keys only off `id == "obsidian"`
    /// and runs its own folder picker + sync, so when the catalog hasn't surfaced an obsidian entry we
    /// hand it a minimal synthesized one rather than routing away. All catalog fields but id/name are
    /// optional, so this is a faithful stand-in for the local notes-folder connector.
    private var resolvedNotesConnector: SourceConnectorCatalogItem {
        obsidianConnector ?? SourceConnectorCatalogItem(
            id: "obsidian",
            name: "Notes folder",
            category: nil, auth: nil, live_status: nil, readiness_status: nil,
            scopes: nil, permissions_required: nil, first_100_note: nil, notes: nil,
            import_status: nil, export_status: nil, source_ids: nil, source_aliases: nil,
            import_label: nil, supports_import: nil, formats: nil, primary_beta: nil,
            beta_status: nil, primary_beta_path: nil, show_in_primary_ui: nil,
            baseline_10k: nil, service_baseline: nil, connection_setup: nil
        )
    }

    /// True while the source catalog hasn't loaded yet AND we have no obsidian entry — the window where
    /// the notes card should show a resolving spinner rather than act on a not-yet-known catalog.
    private var notesCatalogResolving: Bool {
        obsidianConnector == nil && state.sourceConnectorCatalog.isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .top, spacing: 22) {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Bring your memory in")
                        .font(CortexDesign.Typography.display(26))
                        .foregroundColor(CortexDesign.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("Pick the path that fits you. \(DistributionMode.appDisplayName) distills whatever you bring into cited memory. You can add more any time.")
                        .font(CortexDesign.Typography.prose(15))
                        .lineSpacing(3)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                OnboardingDistillMark()
                    .frame(width: 96)
                    .padding(.top, 2)
            }

            // Lead with the fastest path for the biggest first-run cohort: people arriving from
            // ChatGPT / Claude / Gemini. One tap opens their export page; the file drops in here.
            OnboardingLaneLabel(
                systemImage: "bubble.left.and.text.bubble.right",
                title: "Coming from ChatGPT, Claude, or Gemini?",
                detail: "Bring that whole history in. It's the quickest way to get real memory in fast."
            )
            aiExportOption

            // Then the local notes folder (the primary source path) and the one-tap sign-in apps.
            OnboardingLaneLabel(
                systemImage: "folder.badge.plus",
                title: "Or point \(DistributionMode.appDisplayName) at your notes",
                detail: "Choose a local notes folder and \(DistributionMode.appDisplayName) keeps it synced on this Mac."
            )
            // The card carries the step's `.primary` while no source is live; once one is, the
            // card relaxes to `.secondary` ("Change source") and the footer Continue takes over.
            OnboardingConnectionCard(
                title: connectTitle,
                detail: connectDetail,
                systemImage: connectIcon,
                isPrimary: !state.onboardingHasSource,
                status: connectStatus,
                buttonTitle: connectButtonTitle,
                buttonSystemImage: connectButtonIcon,
                // In flight while a folder connect / sync this card kicked off is running
                // (state.isBusy) or any connector sync is active — so the primary can't be
                // double-fired mid-connect. Also held while the source catalog is still resolving
                // (U-ONB6) so we never act on a not-yet-known catalog or bounce out of onboarding.
                isBusy: state.isBusy || !state.connectorSyncingIDs.isEmpty || notesCatalogResolving
            ) {
                runConnectAction()
            }

            // Inline outcome for the connect-notes card: syncLocalNotesFolder writes its guidance
            // (empty folder, moved folder, …) to `connectorLastMessages`, which otherwise lands on
            // a Connections surface the user can't see mid-onboarding. Echo it here so a connect
            // that found no usable notes never resolves silently. Hidden while a connect is in
            // flight and once a source is live (success is the card's own "Source connected" state).
            if !state.onboardingHasSource,
               !state.isBusy,
               state.connectorSyncingIDs.isEmpty,
               let connectMessage = state.connectorLastMessages[resolvedNotesConnector.id] {
                OnboardingNoticeBanner(
                    notice: OnboardingNotice(severity: .warning, title: nil, message: connectMessage)
                )
                .transition(.opacity)
            }

            appConnectGrid

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

            Text("No files of your own yet? Try \(DistributionMode.appDisplayName) on a small set of example notes.")
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
                OnboardingLaneLabel(
                    systemImage: "person.crop.circle.badge.checkmark",
                    title: "Or sign in to a source",
                    detail: "Sign in once and \(DistributionMode.appDisplayName) imports your data with your consent."
                )
                LazyVGrid(
                    columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)],
                    spacing: 10
                ) {
                    ForEach(sources) { connector in
                        OnboardingSourceTile(
                            name: connector.name,
                            systemImage: sourceIcon(connector.id),
                            connectable: sourceIsConnectable(connector),
                            // Mirror ConnectionsPrivacySheet.SignInSourceTile: "starting" covers the
                            // whole in-flight window (OAuth opening + post-OAuth sync), and "connected"
                            // reads the DURABLE persisted source account — so a genuinely-connected tile
                            // STAYS connected instead of showing a premature/reverting checkmark.
                            starting: state.connectorOAuthStartingIDs.contains(connector.id)
                                || state.connectorSyncingIDs.contains(connector.id),
                            connected: state.sourceAccount(connector) != nil
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
    /// File types the export drop target accepts: a ChatGPT / Claude export (.zip or its
    /// conversations.json / .jsonl), a plain .txt transcript, or an unzipped folder. Anything else
    /// gets clear feedback instead of a silent no-op.
    private static let acceptedExportExtensions: Set<String> = ["zip", "json", "jsonl", "txt"]

    @ViewBuilder
    private var aiExportOption: some View {
        VStack(alignment: .leading, spacing: 6) {
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6]))
                .foregroundColor(dropTargeted ? CortexDesign.accent : (dropFeedback != nil ? CortexDesign.gold : CortexDesign.softBorder))
                .frame(height: 58)
                .overlay(
                    HStack(spacing: 8) {
                        if state.importInFlight { ProgressView().controlSize(.small) }
                        Text(state.importInFlight ? "Importing your chats…" : "Drag a ChatGPT / Claude export (.zip, .json, .txt) here, or")
                            .font(.callout)
                            .foregroundColor(CortexDesign.inkSecondary)
                        if !state.importInFlight {
                            CortexButton(title: "Choose export file…", systemImage: "folder.badge.plus", role: .ghost, size: .small) {
                                dropFeedback = nil
                                chooseExportFile()
                            }
                        }
                    }
                )
                .onDrop(of: [.fileURL], isTargeted: $dropTargeted) { providers in
                    handleExportDrop(providers)
                }

            Text("Or just download it — the moment the export lands in Downloads or on your Desktop, \(DistributionMode.appDisplayName) imports it for you.")
                .font(.caption)
                .foregroundColor(CortexDesign.inkFaint)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 2)

            if let dropFeedback {
                Label(dropFeedback, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundColor(CortexDesign.gold)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 2)
                    .transition(.opacity)
            }
        }
        .animation(.easeInOut(duration: 0.2), value: dropFeedback)
    }

    /// Resolve a dropped file URL and route it to `importFromPath`, or surface clear feedback. A
    /// non-file drop, or a file whose type we don't import, now says so plainly instead of failing
    /// silently. A folder (unzipped export) is always accepted; a plain file must carry a recognized
    /// extension.
    private func handleExportDrop(_ providers: [NSItemProvider]) -> Bool {
        guard let provider = providers.first,
              provider.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) else {
            dropFeedback = "That doesn't look like a file. Drag your ChatGPT or Claude export file (.zip, .json, or .txt) here, or use Choose export file."
            return false
        }
        provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
            var resolvedURL: URL?
            if let data = item as? Data, let url = URL(dataRepresentation: data, relativeTo: nil) {
                resolvedURL = url.standardizedFileURL
            } else if let url = item as? URL {
                resolvedURL = url.standardizedFileURL
            }
            Task { @MainActor in
                guard let url = resolvedURL else {
                    dropFeedback = "Could not read that dropped item. Try Choose export file instead."
                    return
                }
                let isDirectory = (try? url.resourceValues(forKeys: [.isDirectoryKey]))?.isDirectory ?? false
                let ext = url.pathExtension.lowercased()
                if !isDirectory && !ext.isEmpty
                    && !OnboardingAddMemoryStep.acceptedExportExtensions.contains(ext) {
                    dropFeedback = "\(DistributionMode.appDisplayName) can import a ChatGPT or Claude export: a .zip, its conversations.json, a .jsonl, a .txt transcript, or the unzipped folder. \(ext.uppercased()) files aren't supported here."
                    return
                }
                dropFeedback = nil
                let path = url.path
                let imported = await state.importFromPath(path)
                if !imported {
                    showNotice(importFailureNotice())
                }
            }
        }
        return true
    }

    /// Onboarding-aware variant of `AppState.importAIChatExport`: the identical picker, but it
    /// checks the `importFromPath` result so a failed or empty import surfaces in the walkthrough's
    /// notice banner instead of only writing the `state.status` line the onboarding sheet occludes.
    private func chooseExportFile() {
        let panel = NSOpenPanel()
        panel.title = "Choose export file"
        panel.message = "Select your ChatGPT or Claude export: a .zip, its conversations.json, or the unzipped folder."
        panel.prompt = "Import"
        panel.canChooseFiles = true
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [
            .zip,
            .json,
            UTType(filenameExtension: "jsonl") ?? .data,
        ]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        Task {
            let imported = await state.importFromPath(url.standardizedFileURL.path)
            if !imported {
                showNotice(importFailureNotice())
            }
        }
    }

    /// The import's honest outcome for the notice banner: `importFromPath` writes a specific
    /// plain-language reason to `state.status` on every non-imported path ("No conversations
    /// found…", "Already imported, nothing new to add.", or the recovery text for a request
    /// failure), so the banner names what actually happened.
    private func importFailureNotice() -> OnboardingNotice {
        OnboardingNotice(severity: .warning, title: "Nothing was imported", message: state.status)
    }

    private func exploreWithSampleNotes() {
        guard !loadingSamples else { return }
        loadingSamples = true
        Task {
            let loaded = await state.loadSampleNotes()
            loadingSamples = false
            guard loaded else {
                // Every loadSampleNotes failure path writes a plain-language `status`; surface it
                // in the walkthrough's notice banner instead of advancing to the payoff beat as if
                // the samples had loaded.
                showNotice(OnboardingNotice(
                    severity: .warning,
                    title: "Sample notes didn't load",
                    message: state.status
                ))
                return
            }
            advance()
        }
    }

    private var connectTitle: String {
        state.onboardingHasSource ? "Source connected" : "Connect your notes"
    }

    private var connectDetail: String {
        if state.onboardingHasSource {
            return "\(DistributionMode.appDisplayName) found usable memory from your connected source. Continue when ready."
        }
        if state.hasConnectedObsidianVault {
            return "\(DistributionMode.appDisplayName) checks connected notes on launch and every 30 minutes, then distills new memory with citations."
        }
        return "Choose a local notes folder, or drag in a ChatGPT / Claude export. Everything stays on your Mac."
    }

    private var connectIcon: String {
        state.onboardingHasSource ? "checkmark.seal.fill" : "folder.badge.plus"
    }

    private var connectStatus: String {
        state.onboardingHasSource ? "Ready" : "Local"
    }

    private var connectButtonTitle: String {
        if state.onboardingHasSource { return "Change source" }
        if notesCatalogResolving { return "Preparing…" }
        if state.notesNeedContent { return "Choose notes" }
        if state.hasConnectedObsidianVault { return "Sync notes" }
        // Even with no catalog entry we now connect a local notes folder in-flow (U-ONB6), so this
        // always reads as a real connect action, never "Open Connections".
        return "Connect notes"
    }

    private var connectButtonIcon: String {
        if notesCatalogResolving { return "hourglass" }
        if state.notesNeedContent { return "folder.badge.questionmark" }
        if state.hasConnectedObsidianVault { return "arrow.triangle.2.circlepath" }
        return "folder.badge.plus"
    }

    private func runConnectAction() {
        // U-ONB6: always connect the notes folder in-flow. If the catalog is still loading we hold off
        // (the card is disabled + shows "Preparing…") and refresh connectivity; otherwise we use the
        // real obsidian connector when present, else a synthesized local one, so this never routes the
        // user OUT to Connections mid-onboarding.
        guard !notesCatalogResolving else {
            Task { await state.loadSourceConnectivity() }
            return
        }
        state.connectLocalNotesFolder(resolvedNotesConnector, chooseNew: state.notesNeedContent)
    }
}

// MARK: - Step 3: Use it (the payoff)

/// The beat that carries the whole point of Cortex: use the memory you already have INSIDE the AI
/// tools you already use (Claude Desktop, ChatGPT, Cursor). It is deliberately emphasized, not a
/// footnote: a single wax primary that opens the shared Connect-an-AI-tool wizard
/// (`state.presentConnectToolsWizard()`), the one hero action the whole app funnels into.
///
/// It reads live state (no local Timer): `connectedAIIntegrationCount` flips the headline to a
/// proven "N tool(s) can now cite your memory" once wiring lands, and `detectedAIIntegrationCount`
/// surfaces an honest "we found Claude / ChatGPT on this Mac" nudge when apps are installed but not
/// yet configured. Both refresh via the app-wide live refresh, so this screen updates itself if the
/// user connects a tool in the wizard and returns.
private struct OnboardingUseItStep: View {
    @ObservedObject var state: AppState

    private var connected: Int { state.connectedAIIntegrationCount }
    private var detected: Int { state.detectedAIIntegrationCount }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .top, spacing: 22) {
                VStack(alignment: .leading, spacing: 10) {
                    Text(connected > 0 ? "Your memory is where you work" : "Now use it where you work")
                        .font(CortexDesign.Typography.display(26))
                        .foregroundColor(CortexDesign.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("This is the point of \(DistributionMode.appDisplayName): the memory you just built can be read and cited right inside Claude Desktop, ChatGPT, and Cursor. No copy-paste, no re-explaining yourself.")
                        .font(CortexDesign.Typography.prose(15))
                        .lineSpacing(3)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                OnboardingHeroMark(systemImage: "sparkles.rectangle.stack", tint: CortexDesign.accent)
                    .frame(width: 96)
                    .padding(.top, 2)
            }

            // The proven state: once at least one tool is wired, celebrate it plainly and honestly.
            if connected > 0 {
                OnboardingCheckRow(
                    title: connected == 1
                        ? "1 AI tool can cite your memory"
                        : "\(connected) AI tools can cite your memory",
                    detail: "Ask your questions in Claude, ChatGPT, or Cursor and they'll pull from your approved memory, with sources.",
                    systemImage: "checkmark.seal.fill",
                    color: CortexDesign.sealMoss
                )
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(CortexDesign.panelBackground)
                .onboardingPanel(radius: 10)
            }

            // THE hero action. A full-width wax primary that can't be missed: it opens the shared
            // Connect-an-AI-tool wizard (sheet-over-sheet handoff, then returns to onboarding).
            heroConnectCard

            // The three tools named as concrete, familiar rows so the promise is legible.
            toolsRow
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .onAppear {
            // Refresh detection once on entry; the app-wide live refresh keeps it current after.
            state.refreshIntegrationStates()
        }
    }

    /// The can't-miss primary. Wax button, full width, the single emphasized action of this beat.
    /// The supporting line adapts to what we detected on this Mac so the nudge is honest and specific.
    private var heroConnectCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Image(systemName: "link.badge.plus")
                    .font(.title2)
                    .foregroundColor(CortexDesign.accent)
                VStack(alignment: .leading, spacing: 2) {
                    Text(connected > 0 ? "Connect another AI tool" : "Connect an AI tool")
                        .font(.headline)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.ink)
                    Text(heroDetail)
                        .font(.callout)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }

            CortexButton(
                title: connected > 0 ? "Connect another tool" : "Connect an AI tool",
                systemImage: "link",
                role: .primary,
                size: .large,
                fullWidth: true
            ) {
                state.presentConnectToolsWizard(statusMessage: "Connect your AI tools")
            }
            .accessibilityLabel("Connect an AI tool to use your memory")
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .stroke(CortexDesign.accent.opacity(0.32), lineWidth: 1)
        )
        .embossedBorder()
        .shadow(color: CortexDesign.Elevation.rest.ambient.color, radius: CortexDesign.Elevation.rest.ambient.radius, y: CortexDesign.Elevation.rest.ambient.y)
        .shadow(color: CortexDesign.Elevation.rest.contact.color, radius: CortexDesign.Elevation.rest.contact.radius, y: CortexDesign.Elevation.rest.contact.y)
    }

    /// Honest, specific supporting copy: name that we found apps on this Mac when we did, otherwise a
    /// plain invitation. Never claims a tool is present that isn't.
    private var heroDetail: String {
        if connected == 0, detected > 0 {
            return detected == 1
                ? "We found an AI app on this Mac. Wire it up in one step so it can read your memory."
                : "We found \(detected) AI apps on this Mac. Wire them up in one step so they can read your memory."
        }
        if connected > 0 {
            return "Add ChatGPT, Cursor, or another tool so your memory follows you everywhere you work."
        }
        return "Set up Claude Desktop, ChatGPT, or Cursor in one step so they can cite your approved memory."
    }

    /// The three canonical tools as calm rows, so "use it where you work" is concrete, not abstract.
    private var toolsRow: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Works with the tools you already use")
                .font(.subheadline)
                .fontWeight(.semibold)
                .foregroundColor(CortexDesign.inkSecondary)
            HStack(spacing: 10) {
                OnboardingToolChip(name: "Claude", systemImage: "sparkle") {
                    state.presentConnectToolsWizard(statusMessage: "Connect Claude")
                }
                OnboardingToolChip(name: "ChatGPT", systemImage: "bubble.left.and.bubble.right") {
                    state.presentConnectToolsWizard(statusMessage: "Connect ChatGPT")
                }
                OnboardingToolChip(name: "Cursor", systemImage: "cursorarrow.rays") {
                    state.presentConnectToolsWizard(statusMessage: "Connect Cursor")
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A compact chip naming a supported AI tool. It reads as a concrete tool rather than an abstraction,
/// and is now a live affordance: tapping it opens the shared Connect-an-AI-tool wizard pre-scoped to
/// that tool (the same hero action the primary button drives), so the named tools aren't a dead
/// read-out. It never claims a per-chip "connected" state it can't verify.
private struct OnboardingToolChip: View {
    let name: String
    let systemImage: String
    /// Optional connect action. When nil the chip stays purely illustrative (back-compat).
    var action: (() -> Void)? = nil

    @State private var hovering = false

    var body: some View {
        Button {
            action?()
        } label: {
            HStack(spacing: 6) {
                Image(systemName: systemImage)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(CortexDesign.accent)
                Text(name)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(CortexDesign.ink)
                if action != nil {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundColor(CortexDesign.inkFaint)
                        .opacity(hovering ? 1 : 0.5)
                }
            }
            .padding(.horizontal, 11)
            .padding(.vertical, 7)
            .frame(maxWidth: .infinity)
            .background(CortexDesign.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                    .stroke(CortexDesign.accent.opacity(hovering && action != nil ? 0.4 : 0), lineWidth: 1)
            )
            .embossedBorder()
        }
        .buttonStyle(.plain)
        .disabled(action == nil)
        .onHover { hovering = $0 }
        .help(action != nil ? "Connect \(name)" : "Works with \(name)")
        .accessibilityElement(children: .combine)
        .accessibilityLabel(action != nil ? "Connect \(name)" : "Works with \(name)")
        .accessibilityAddTraits(action != nil ? .isButton : [])
    }
}

// MARK: - Step 4: You're set

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
                Text("As memory accumulates, \(DistributionMode.appDisplayName) draws Your Constellation. Review it, then it's ready wherever you work: in \(DistributionMode.appDisplayName), and in the AI tools you connected.")
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
                .onboardingPanel(radius: 12)
                .overlay(alignment: .bottomLeading) {
                    Text(memoryCount > 0 ? "Your Constellation · \(memoryCount) memories" : "Your Constellation")
                        .font(.system(size: 11, weight: .medium, design: .monospaced))
                        .foregroundColor(CortexDesign.inkFaint)
                        .padding(10)
                }

            // The proof moment: waits for the first external AI read and flips to
            // "<app> just read your memory. Continuity, proven." Purely observational —
            // it polls only while this step is on screen and never blocks Finish.
            //
            // U-ONB7: with no AI tool connected, nothing external will ever read, so the watcher would
            // sit on "Waiting…" forever. In that case show an honest connect affordance instead of a
            // dead spinner; once a tool is wired the real proof watcher takes over.
            if state.connectedAIIntegrationCount > 0 {
                RecallProofWatcher(state: state, waitingLine: "Waiting for your first external read…")
            } else {
                recallProofConnectFallback
            }

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
        // U-ONB3: the Constellation preview was a one-shot snapshot — if a connector's first sync
        // landed AFTER this step appeared, the map stayed empty/stale. A connector finishing its sync
        // removes its id from connectorSyncingIDs; observe that transition and re-pull the real graph
        // + stats so the preview fills in live as the first memory arrives.
        .onChange(of: state.connectorSyncingIDs) { _ in
            Task {
                await state.loadStats()
                await state.loadGraph()
            }
        }
    }

    /// U-ONB7: the stand-in for RecallProofWatcher when no AI tool is connected. Rather than a
    /// forever-"Waiting…" spinner, it names why there's nothing to prove yet and routes into the same
    /// hero connect wizard, so the proof moment becomes reachable instead of a dead-end.
    private var recallProofConnectFallback: some View {
        HStack(alignment: .center, spacing: 10) {
            Image(systemName: "sparkles.rectangle.stack")
                .font(.title3)
                .foregroundColor(CortexDesign.accent)
            VStack(alignment: .leading, spacing: 2) {
                Text("See your memory get used")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(CortexDesign.ink)
                Text("Connect a tool to see this: the moment Claude, ChatGPT, or Cursor reads your memory, it shows up here as proof.")
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            CortexButton(title: "Connect a tool", systemImage: "link", role: .secondary, size: .small) {
                state.presentConnectToolsWizard(statusMessage: "Connect your AI tools")
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(CortexDesign.softBorder, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 10))
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
        .onboardingPanel(radius: 10)
    }

    /// The connect-tools status row. Connecting an AI tool is now its own emphasized beat (the
    /// `.useIt` step), so here it reflects state honestly: a green "N connected" check once tools are
    /// wired, or a quiet second chance to open the same hero wizard. Nothing here is required to
    /// finish. Routes through `presentConnectToolsWizard()` (THE hero action) so there is one path.
    @ViewBuilder
    private var aiToolsRow: some View {
        if state.connectedAIIntegrationCount > 0 {
            OnboardingCheckRow(
                title: state.connectedAIIntegrationCount == 1
                    ? "1 AI tool can read your memory"
                    : "\(state.connectedAIIntegrationCount) AI tools can read your memory",
                detail: "Claude, ChatGPT, and Cursor can cite your approved memory. Add more anytime from Connections.",
                systemImage: "checkmark.seal.fill",
                color: CortexDesign.sealMoss
            )
        } else {
            HStack(alignment: .center, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Use your memory in your AI tools")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(CortexDesign.ink)
                    Text("Let Claude, ChatGPT, and Cursor read and cite your approved memory. You can still set this up anytime.")
                        .font(.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                CortexButton(title: "Connect a tool", systemImage: "link.circle", role: .secondary, size: .small) {
                    state.presentConnectToolsWizard(statusMessage: "Connect your AI tools")
                }
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
            VStack(alignment: .leading, spacing: 10) {
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
                    // While a backup is in flight the button reflects it honestly (spinner + "Backing
                    // up…") and disables, so an impatient second tap can't kick a duplicate backup.
                    if state.backupInFlight { ProgressView().controlSize(.small) }
                    CortexButton(
                        title: state.backupInFlight ? "Backing up…" : "Back Up Now",
                        systemImage: "archivebox",
                        role: .secondary,
                        size: .small
                    ) {
                        state.createBackup()
                    }
                    .disabled(state.backupInFlight)
                    CortexButton(title: "Skip for now", role: .ghost, size: .small) {
                        state.skipFirstBackup()
                    }
                    .disabled(state.backupInFlight)
                }

                // On failure the backup is NOT silent: surface the exact error with a "Try again"
                // affordance, mirroring OnboardingRestoringStep.failed. lastBackupError is cleared at
                // the start of the next createBackup(), so a successful retry clears this banner.
                if let backupError = state.lastBackupError, !state.backupInFlight {
                    OnboardingNoticeBanner(
                        notice: OnboardingNotice(
                            severity: .warning,
                            title: "Backup didn't finish",
                            message: backupError
                        ),
                        onDismiss: nil
                    )
                    CortexButton(title: "Try again", systemImage: "arrow.clockwise", role: .secondary, size: .small) {
                        state.createBackup()
                    }
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
                    detail: "Add notes or import your chats any time and \(DistributionMode.appDisplayName) distills them into cited memory on your Mac, no extra setup.",
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
        .onboardingPanel(radius: 10)
    }

    /// Direct-download build: a live opt-in toggle + a keybind recorder, bound to AppState.
    private var enableRows: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                CortexToggle(title: "Quick capture", isOn: enabledBinding)
                Text("Optional: save anything with a global shortcut. Change it anytime in Settings.")
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

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
            }

            // U-ONB5: the notch preview used to be buried inside the enabled block, so a user deciding
            // WHETHER to turn quick capture on never got to see what it does. Surface it unconditionally
            // (direct-download only — deliberately absent from the MAS row) so the notch demo helps that
            // decision. Pure-visual: uses the same .captured style a real quick capture surfaces, no
            // backend needed.
            Divider().opacity(0.5)
            HStack(alignment: .center, spacing: 8) {
                CortexButton(title: "Show me the notch", systemImage: "bell.badge", role: .ghost, size: .small) {
                    NotchNotifier.shared.show(
                        title: "Saved to \(DistributionMode.appDisplayName)",
                        subtitle: "This is what a quick capture looks like.",
                        style: .captured
                    )
                }
                Text("A quick preview of the capture pill.")
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                Spacer(minLength: 0)
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
    /// True while a connect / sync kicked off by this card is still in flight. Disables the button
    /// and swaps in a "Connecting…" label so a slow connect can't be double-fired by an impatient
    /// second tap (which would kick a duplicate folder-picker / connect request).
    var isBusy: Bool = false
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
            HStack(spacing: 10) {
                CortexButton(
                    title: isBusy ? "Connecting…" : buttonTitle,
                    systemImage: isBusy ? "arrow.triangle.2.circlepath" : buttonSystemImage,
                    role: isPrimary ? .primary : .secondary,
                    size: .large,
                    fullWidth: true,
                    action: action
                )
                .disabled(isBusy)
                if isBusy {
                    ProgressView().controlSize(.small)
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .background(isPrimary ? CortexDesign.panelBackground : CortexDesign.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        // Primary gets a wax-red hairline (never the system-blue accentColor the pre-overhaul card
        // used) plus a rest shadow so it lifts off the paper; secondary settles into a plain
        // letterpress edge.
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .stroke(CortexDesign.accent.opacity(isPrimary ? 0.32 : 0), lineWidth: 1)
        )
        .embossedBorder()
        .shadow(color: CortexDesign.Elevation.rest.ambient.color, radius: CortexDesign.Elevation.rest.ambient.radius, y: CortexDesign.Elevation.rest.ambient.y)
        .shadow(color: CortexDesign.Elevation.rest.contact.color, radius: CortexDesign.Elevation.rest.contact.radius, y: CortexDesign.Elevation.rest.contact.y)
    }
}

/// A calm lane label for the add-memory beat, so the "pick the easiest path" story reads at a glance:
/// a wax glyph, a short serif title, and one honest supporting line. Purely a heading, no action.
private struct OnboardingLaneLabel: View {
    let systemImage: String
    let title: String
    let detail: String

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: systemImage)
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(CortexDesign.accent)
                .frame(width: 20)
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
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

    // Hover/press state for a tactile, alive feel. macOS 13-safe (plain withAnimation + onHover).
    @State private var hovering = false
    @State private var pressing = false

    private var subtitle: String {
        if connected { return "Connected" }
        if starting { return "Opening sign-in…" }
        return connectable ? "Connect" : "Available soon"
    }

    private var iconTint: Color {
        if connected { return CortexDesign.sealMoss }
        return connectable ? CortexDesign.accent : CortexDesign.inkFaint
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                ZStack {
                    // Soft halo that blooms on hover, so the eye is drawn to the tappable thing.
                    Circle()
                        .fill((connected ? CortexDesign.sealMoss : CortexDesign.accent).opacity(hovering && connectable ? 0.14 : 0))
                        .frame(width: 34, height: 34)
                    Image(systemName: connected ? "checkmark.seal.fill" : systemImage)
                        .font(.title3)
                        .foregroundColor(iconTint)
                        .scaleEffect(connected ? 1.06 : 1)
                }
                .frame(width: 26)
                VStack(alignment: .leading, spacing: 1) {
                    Text(name)
                        .font(.callout)
                        .fontWeight(.semibold)
                        .foregroundColor(CortexDesign.ink)
                    Text(subtitle)
                        .font(.caption2)
                        .foregroundColor(connected ? CortexDesign.sealMoss : CortexDesign.inkSecondary)
                }
                Spacer(minLength: 4)
                if starting {
                    ProgressView().controlSize(.small)
                } else if connected {
                    Image(systemName: "checkmark")
                        .font(.caption.weight(.bold))
                        .foregroundColor(CortexDesign.sealMoss)
                        .transition(.scale.combined(with: .opacity))
                } else if connectable {
                    Image(systemName: "arrow.right.circle.fill")
                        .foregroundColor(CortexDesign.accent)
                        // Nudge the arrow on hover to say "go".
                        .offset(x: hovering ? 2 : 0)
                }
            }
            .padding(11)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(hovering && connectable ? CortexDesign.panelBackground : CortexDesign.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                    .strokeBorder((connected ? CortexDesign.sealMoss : CortexDesign.accent).opacity(hovering && connectable ? 0.45 : 0), lineWidth: 1)
            )
            .embossedBorder()
            .shadow(
                color: CortexDesign.Elevation.rest.contact.color,
                radius: hovering && connectable ? CortexDesign.Elevation.rest.contact.radius + 3 : CortexDesign.Elevation.rest.contact.radius,
                y: hovering && connectable ? CortexDesign.Elevation.rest.contact.y + 2 : CortexDesign.Elevation.rest.contact.y
            )
            .opacity(connectable ? 1 : 0.7)
            .scaleEffect(pressing ? 0.97 : (hovering && connectable ? 1.02 : 1))
            .offset(y: hovering && connectable ? -2 : 0)
        }
        .buttonStyle(.plain)
        .disabled(!connectable || starting)
        .onHover { h in withAnimation(CortexMotion.hover) { hovering = h } }
        .simultaneousGesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in if !pressing { withAnimation(CortexMotion.press) { pressing = true } } }
                .onEnded { _ in withAnimation(CortexMotion.press) { pressing = false } }
        )
        .animation(CortexMotion.settle, value: connected)
        .animation(CortexMotion.settle, value: starting)
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

private extension View {
    /// The walkthrough's panel recipe: clip to a continuous rounded rect, a letterpress embossed
    /// edge (kills the flat rounded-rect tell), and a soft REST-elevation two-layer shadow so the
    /// card floats a hair off the paper desk. Replaces the old flat `softBorder` stroke overlays.
    /// Apply AFTER the panel's `.background(...)`.
    func onboardingPanel(radius: CGFloat = CortexDesign.Radius.md) -> some View {
        let ambient = CortexDesign.Elevation.rest.ambient
        let contact = CortexDesign.Elevation.rest.contact
        return self
            .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
            .embossedBorder(radius: radius)
            .shadow(color: ambient.color, radius: ambient.radius, y: ambient.y)
            .shadow(color: contact.color, radius: contact.radius, y: contact.y)
    }
}

// MARK: - Onboarding design primitives (local to the walkthrough)
//
// The shared design system (CortexDesign.swift) ships the button/card/seal-surface language but
// does NOT yet expose a wax-seal MARK, a form field, a toggle, or a severity banner as reusable
// primitives. Rather than reach across into CortexDesign.swift (owned centrally), the walkthrough
// carries its OWN small set here, built entirely from the public design tokens (CortexSealSurface,
// embossedBorder, Elevation, CortexMotion, the palette + typography), so it speaks the same visual
// language — restrained physical craft, no bitmap texture — without duplicating contracts.

/// The walkthrough's inline voice — a severity banner. The ONLY place the flow speaks back about why
/// an action didn't complete (e.g. Finish needs a source). Built from the palette: a tinted wash, a
/// letterpress edge, a severity glyph, and an optional dismiss — never a stock alert.
private struct OnboardingNotice: Equatable {
    enum Severity: Equatable {
        case info
        case warning
        case success

        var tint: Color {
            switch self {
            case .info: return CortexDesign.accent
            case .warning: return CortexDesign.gold
            case .success: return CortexDesign.sealMoss
            }
        }

        var systemImage: String {
            switch self {
            case .info: return "info.circle.fill"
            case .warning: return "exclamationmark.triangle.fill"
            case .success: return "checkmark.seal.fill"
            }
        }
    }

    var severity: Severity
    /// Optional bold lead line; nil renders message-only.
    var title: String?
    var message: String
}

private struct OnboardingNoticeBanner: View {
    let notice: OnboardingNotice
    /// Nil hides the dismiss affordance (for persistent, system-owned messages).
    var onDismiss: (() -> Void)? = nil

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: notice.severity.systemImage)
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(notice.severity.tint)
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 2) {
                if let title = notice.title {
                    Text(title)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(CortexDesign.ink)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text(notice.message)
                    .font(.system(size: 12))
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 4)
            if let onDismiss {
                CortexIconButton(systemImage: "xmark", role: .ghost, size: .small, help: "Dismiss") {
                    onDismiss()
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(notice.severity.tint.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .stroke(notice.severity.tint.opacity(0.28), lineWidth: 1)
        )
        .accessibilityElement(children: .combine)
    }
}

/// The "or" rule between the provider buttons and the email field — a hairline with a mono "or"
/// stamp, in the design voice (no stock `Divider`-with-label).
private struct OnboardingOrDivider: View {
    var body: some View {
        HStack(spacing: 10) {
            rule
            Text(verbatim: "OR")
                .font(CortexDesign.Typography.stamp)
                .kerning(0.8)
                .foregroundColor(CortexDesign.inkFaint)
            rule
        }
        .padding(.vertical, 2)
    }

    private var rule: some View {
        Rectangle()
            .fill(CortexDesign.hairline)
            .frame(height: 1)
    }
}

/// A quiet disclosure in the design voice — a ghost header row with a rotating chevron — replacing
/// the stock `DisclosureGroup` triangle. Drives the passed `Binding<Bool>` and reveals its content
/// with the same spring the rest of the walkthrough uses.
private struct OnboardingDisclosure<Content: View>: View {
    let title: String
    @Binding var isExpanded: Bool
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.spring(response: 0.36, dampingFraction: 0.82)) { isExpanded.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Text(title)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(CortexDesign.inkSecondary)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(CortexDesign.inkFaint)
                        .rotationEffect(.degrees(isExpanded ? 90 : 0))
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if isExpanded {
                content()
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
    }
}
