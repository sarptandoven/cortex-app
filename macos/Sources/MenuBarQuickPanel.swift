import SwiftUI

extension Notification.Name {
    /// Posted by the AppDelegate right after the quick panel is shown, so the panel can grab
    /// keyboard focus (NSPopover doesn't reliably give a TextField first-responder on open).
    static let cortexFocusQuickPanel = Notification.Name("CortexFocusQuickPanel")
}

/// The "Cortex Spotlight" menu-bar panel: ask your memory and get a cited answer, or capture a
/// thought — one keystroke away (click the menu-bar icon or press the global hotkey). Reuses the
/// same AppState + AskAnswerPanel as the main window, so answers render identically and stay in one
/// backend. Everything here is macOS 13 compatible (no symbolEffect / phaseAnimator).
struct MenuBarQuickPanel: View {
    @ObservedObject var state: AppState
    var onOpenApp: () -> Void
    var onOpenReview: () -> Void
    var onClose: () -> Void

    private enum Mode: Hashable { case ask, capture }

    @State private var mode: Mode = .ask
    @State private var query = ""
    @State private var draft = ""
    @State private var asking = false
    @State private var answer: AskResponse?
    @State private var askError: String?
    @State private var savingCapture = false
    @State private var captureSaved = false
    @State private var captureFailed = false
    @FocusState private var fieldFocused: Bool
    @Namespace private var segment

    private var pendingCount: Int {
        state.review?.stats.pending_captures ?? state.inbox.count
    }
    private var isSyncing: Bool { state.syncProgress?.active == true }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(CortexDesign.hairline)
            modeSwitcher
                .padding(.horizontal, 18)
                .padding(.top, 12)
            content
                .padding(.horizontal, 18)
                .padding(.top, 16)
                .padding(.bottom, 18)
            Divider().overlay(CortexDesign.hairline)
            footer
        }
        .frame(width: 384)
        .background(CortexDesign.appBackground)
        .onAppear { focusFieldSoon() }
        .onReceive(NotificationCenter.default.publisher(for: .cortexFocusQuickPanel)) { _ in
            withAnimation(.easeInOut(duration: 0.2)) { mode = .ask }
            focusFieldSoon()
        }
    }

    // MARK: Header

    private var header: some View {
        HStack(spacing: 10) {
            ZStack {
                Circle().fill(CortexDesign.accentSoft).frame(width: 30, height: 30)
                Image(systemName: "brain.head.profile")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundColor(CortexDesign.accent)
            }
            Text("Cortex")
                .font(.system(size: 15, weight: .semibold, design: .serif))
                .foregroundColor(CortexDesign.ink)
            Spacer()
            if isSyncing {
                ProgressView().controlSize(.small)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
    }

    // MARK: Mode switcher (animated segmented control)

    private var modeSwitcher: some View {
        HStack(spacing: 4) {
            segmentButton("Ask", icon: "sparkle.magnifyingglass", value: .ask)
            segmentButton("Note", icon: "square.and.pencil", value: .capture)
        }
        .padding(3)
        .background(CortexDesign.quietBackground)
        .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
    }

    private func segmentButton(_ title: String, icon: String, value: Mode) -> some View {
        let selected = mode == value
        return Button {
            withAnimation(.spring(response: 0.32, dampingFraction: 0.8)) { mode = value }
            focusFieldSoon()
        } label: {
            HStack(spacing: 6) {
                Image(systemName: icon)
                Text(title)
            }
            .font(.system(size: 12.5, weight: .semibold))
            .frame(maxWidth: .infinity)
            .frame(height: 26)
            .foregroundColor(selected ? CortexDesign.ink : CortexDesign.inkSecondary)
            .background(
                ZStack {
                    if selected {
                        RoundedRectangle(cornerRadius: 7, style: .continuous)
                            .fill(CortexDesign.panelBackground)
                            .overlay(
                                RoundedRectangle(cornerRadius: 7, style: .continuous)
                                    .stroke(CortexDesign.hairline, lineWidth: 1)
                            )
                            .shadow(color: CortexDesign.ink.opacity(0.06), radius: 2, y: 1)
                            .matchedGeometryEffect(id: "segbg", in: segment)
                    }
                }
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    // MARK: Content

    @ViewBuilder
    private var content: some View {
        switch mode {
        case .ask: askContent.transition(.opacity)
        case .capture: captureContent.transition(.opacity)
        }
    }

    private var askContent: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundColor(.secondary)
                TextField("Ask your memory…", text: $query)
                    .textFieldStyle(.plain)
                    .font(.system(size: 14))
                    .focused($fieldFocused)
                    .onSubmit(runAsk)
                if !query.isEmpty {
                    Button { query = ""; withAnimation { answer = nil; askError = nil } } label: {
                        Image(systemName: "xmark.circle.fill").foregroundColor(.secondary.opacity(0.6))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 11)
            .padding(.vertical, 9)
            .background(CortexDesign.panelBackground)
            .overlay(
                RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .stroke(fieldFocused ? CortexDesign.accent.opacity(0.5) : CortexDesign.softBorder, lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))

            askResultArea
        }
        .task {
            // The suggestion chips derive from `state.recent`; the panel can open before the main
            // window ever loaded it, so fetch once here to keep the idle state alive, not blank.
            if state.recent.isEmpty { await state.loadRecent() }
        }
    }

    @ViewBuilder
    private var askResultArea: some View {
        if asking {
            QuickPanelSkeleton()
                .transition(.opacity)
        } else if let answer, !answer.answer.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                ScrollView {
                    AskAnswerPanel(answer: answer.answer, citations: answer.citations)
                }
                .frame(maxHeight: 280)
                .overlay(alignment: .bottom) {
                    // Long answers hard-clip at the panel height; a soft fade signals "there's more —
                    // scroll" without stealing clicks from the content beneath it.
                    LinearGradient(
                        colors: [CortexDesign.appBackground.opacity(0), CortexDesign.appBackground],
                        startPoint: .top, endPoint: .bottom
                    )
                    .frame(height: 16)
                    .allowsHitTesting(false)
                }
                HStack {
                    Spacer()
                    Button {
                        // Hand the same question to the full Ask tab so follow-ups don't dead-end
                        // in the 384pt panel.
                        state.searchQuery = query
                        state.selectedTab = .ask
                        state.runSearch()
                        onOpenApp()
                    } label: {
                        Label("Continue in Cortex", systemImage: "arrow.up.forward.app")
                            .font(.caption)
                            .fontWeight(.medium)
                    }
                    .buttonStyle(.plain)
                    .foregroundColor(CortexDesign.accent)
                }
            }
            .transition(.opacity.combined(with: .move(edge: .top)))
        } else if let answer, answer.answer.isEmpty {
            quietRow(icon: "sparkles", text: "No cited memory found. Try a different question, or connect more sources.")
                .transition(.opacity)
        } else if let askError {
            quietRow(icon: "exclamationmark.triangle", text: askError)
                .transition(.opacity)
        } else {
            idleContent
                .transition(.opacity)
        }
    }

    /// The idle state is alive, not a placeholder: tappable suggested questions from the user's own
    /// memory, plus the top item waiting for review with one-tap Approve/Archive.
    private var idleContent: some View {
        VStack(alignment: .leading, spacing: 8) {
            let suggestions = Array(state.onboardingAskSuggestions.prefix(2))
            if suggestions.isEmpty {
                quietRow(icon: "quote.bubble", text: "Ask a question and Cortex answers from your own memory — with citations.")
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Try asking")
                        .font(.caption2)
                        .fontWeight(.semibold)
                        .foregroundColor(.secondary)
                    ForEach(suggestions, id: \.self) { suggestion in
                        QuickSuggestionChip(text: suggestion) {
                            query = suggestion
                            runAsk()
                        }
                    }
                }
            }
            if let pending = state.inbox.first {
                pendingReviewCard(pending)
            }
        }
    }

    private func pendingReviewCard(_ capture: CaptureItem) -> some View {
        let inFlight = state.inFlightCaptureIds.contains(capture.id)
        return VStack(alignment: .leading, spacing: 6) {
            // The catalog-stamp eyebrow; the gold spine on the card edge marks "unreviewed".
            Text("WAITING FOR REVIEW")
                .font(CortexDesign.Typography.stamp)
                .kerning(0.8)
                .foregroundColor(CortexDesign.inkFaint)
            Text(cortexCaptureTitle(capture))
                .font(.system(size: 13, weight: .semibold))
                .foregroundColor(CortexDesign.ink)
                .lineLimit(1)
                .truncationMode(.middle)
            if let summary = capture.summary, !summary.isEmpty {
                Text(summary)
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .lineLimit(2)
            }
            HStack(spacing: 8) {
                Button("Approve") { state.approveCapture(capture) }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .disabled(inFlight)
                Button("Archive") { state.archiveCapture(capture) }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(inFlight)
                if inFlight {
                    ProgressView().controlSize(.small)
                }
                Spacer()
            }
        }
        .padding(10)
        .padding(.leading, 10)
        .background(CortexDesign.quietBackground)
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        .archiveSpine(CortexDesign.gold)
    }

    private var captureContent: some View {
        VStack(alignment: .leading, spacing: 10) {
            ZStack(alignment: .topLeading) {
                if draft.isEmpty {
                    Text("Jot a thought to remember…")
                        .font(.system(size: 14))
                        .foregroundColor(.secondary.opacity(0.7))
                        .padding(.horizontal, 12)
                        .padding(.vertical, 10)
                        .allowsHitTesting(false)
                }
                TextField("", text: $draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(.system(size: 14))
                    .lineLimit(3...7)
                    .focused($fieldFocused)
                    .padding(.horizontal, 11)
                    .padding(.vertical, 9)
            }
            .background(CortexDesign.panelBackground)
            .overlay(
                RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .stroke(fieldFocused ? CortexDesign.accent.opacity(0.5) : CortexDesign.softBorder, lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))

            HStack(spacing: 10) {
                // Honest states: green only after a verified save, and a visible (retry-able)
                // failure instead of a silent swallow.
                if captureSaved {
                    Label("Saved — Ask can use it now", systemImage: "checkmark.seal.fill")
                        .font(.caption).fontWeight(.semibold)
                        .foregroundColor(CortexDesign.sealMoss)
                        .transition(.scale(scale: 0.8).combined(with: .opacity))
                } else if captureFailed {
                    Label("Couldn't save — try again", systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundColor(CortexDesign.accent)
                        .transition(.opacity)
                }
                Spacer()
                Button(action: saveCapture) {
                    HStack(spacing: 6) {
                        if savingCapture { ProgressView().controlSize(.small) }
                        Text(savingCapture ? "Saving…" : "Save")
                    }
                    .frame(minWidth: 64)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.regular)
                .keyboardShortcut(.return, modifiers: .command)
                .disabled(savingCapture || draft.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
    }

    private func quietRow(icon: String, text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: icon).foregroundColor(.secondary)
            Text(text).font(.caption).foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(CortexDesign.quietBackground)
        .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
    }

    // MARK: Footer

    private var footer: some View {
        HStack(spacing: 6) {
            footerAction("Open Cortex", icon: "macwindow", action: onOpenApp)
            footerBadgeAction(
                "Review", icon: "checklist", badge: pendingCount, action: onOpenReview
            )
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 10)
        .background(CortexDesign.appBackground)
    }

    private func footerAction(_ title: String, icon: String, action: @escaping () -> Void, disabled: Bool = false) -> some View {
        QuickFooterButton(title: title, icon: icon, disabled: disabled, action: action)
    }

    private func footerBadgeAction(_ title: String, icon: String, badge: Int, action: @escaping () -> Void) -> some View {
        QuickFooterBadgeButton(title: title, icon: icon, badge: badge, action: action)
    }

    // MARK: Actions

    private func focusFieldSoon() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.06) { fieldFocused = true }
    }

    private func runAsk() {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty, !asking else { return }
        withAnimation(.easeInOut(duration: 0.2)) {
            asking = true
            answer = nil
            askError = nil
        }
        Task {
            let result = await state.askOnce(q)
            await MainActor.run {
                withAnimation(.easeOut(duration: 0.28)) {
                    asking = false
                    if let result {
                        answer = result
                    } else {
                        askError = "Couldn't reach your memory. Is the engine running?"
                    }
                }
            }
        }
    }

    private func saveCapture() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !savingCapture else { return }
        savingCapture = true
        captureFailed = false
        Task {
            let ok = await state.captureFromPanel(text: text)
            await MainActor.run {
                savingCapture = false
                guard ok else {
                    // The draft is kept so the user's thought never silently vanishes.
                    withAnimation(.easeOut(duration: 0.2)) { captureFailed = true }
                    return
                }
                withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
                    captureSaved = true
                    draft = ""
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.8) {
                    withAnimation(.easeOut(duration: 0.3)) { captureSaved = false }
                }
            }
        }
    }
}

/// One tappable suggested question in the idle state, built from the user's own memories. Hover
/// brightens the chip and reveals a return-arrow so "click to ask" is legible before committing.
private struct QuickSuggestionChip: View {
    let text: String
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: "sparkle.magnifyingglass")
                    .font(.caption)
                    .foregroundColor(CortexDesign.accent)
                Text(text)
                    .font(.caption)
                    .foregroundColor(CortexDesign.ink)
                    .lineLimit(1)
                    .truncationMode(.tail)
                Spacer(minLength: 0)
                Image(systemName: "arrow.turn.down.left")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundColor(CortexDesign.inkSecondary)
                    .opacity(hovering ? 1 : 0)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .background(hovering ? CortexDesign.accentSoft : CortexDesign.panelBackground)
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(CortexDesign.hairline, lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .contentShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
        .buttonStyle(.plain)
        .onHover { h in withAnimation(.easeOut(duration: 0.12)) { hovering = h } }
    }
}

/// A footer action with a hover affordance: soft rounded highlight + accent icon on hover, so the
/// row reads as clickable at a glance instead of identical gray stacks.
private struct QuickFooterButton: View {
    let title: String
    let icon: String
    var disabled = false
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            VStack(spacing: 3) {
                Image(systemName: icon).font(.system(size: 14, weight: .medium))
                Text(title).font(.system(size: 9.5, weight: .medium))
            }
            .frame(width: 58, height: 40)
            .foregroundColor(hovering && !disabled ? CortexDesign.accent : .secondary)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(hovering && !disabled ? CortexDesign.quietBackground : Color.clear)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .help(title)
        .onHover { h in withAnimation(.easeOut(duration: 0.12)) { hovering = h } }
    }
}

/// The Review footer action: same hover treatment as QuickFooterButton, plus the marginalia-gold
/// waiting-count badge (ink numerals on a gold fill — gold is never text).
private struct QuickFooterBadgeButton: View {
    let title: String
    let icon: String
    let badge: Int
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            VStack(spacing: 3) {
                ZStack(alignment: .topTrailing) {
                    Image(systemName: icon).font(.system(size: 14, weight: .medium))
                    if badge > 0 {
                        Text(badge > 99 ? "99+" : String(badge))
                            .font(.system(size: 8, weight: .semibold, design: .monospaced))
                            .foregroundColor(CortexDesign.ink)
                            .padding(.horizontal, 4).padding(.vertical, 1)
                            .background(
                                RoundedRectangle(cornerRadius: 3, style: .continuous)
                                    .fill(CortexDesign.gold.opacity(0.85))
                            )
                            .offset(x: 11, y: -7)
                            .transition(.scale.combined(with: .opacity))
                    }
                }
                Text(title).font(.system(size: 9.5, weight: .medium))
            }
            .frame(width: 58, height: 40)
            .foregroundColor(badge > 0 || hovering ? CortexDesign.accent : CortexDesign.inkSecondary)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(hovering ? CortexDesign.accentSoft : Color.clear)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(badge > 0 ? "\(badge) waiting for review" : title)
        .animation(.spring(response: 0.35, dampingFraction: 0.7), value: badge)
        .onHover { h in withAnimation(.easeOut(duration: 0.12)) { hovering = h } }
    }
}

/// A shimmering skeleton shown while an answer is being retrieved — the premium "loading" feel.
/// A gradient sweeps across placeholder lines; pure SwiftUI, macOS 13 compatible.
private struct QuickPanelSkeleton: View {
    @State private var phase: CGFloat = -1

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 6) {
                Image(systemName: "sparkles").font(.caption).foregroundColor(CortexDesign.accent)
                Text("Searching your memory…").font(.caption).foregroundColor(.secondary)
            }
            skeletonLine(width: 1.0)
            skeletonLine(width: 0.92)
            skeletonLine(width: 0.7)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
        .onAppear {
            withAnimation(.linear(duration: 1.15).repeatForever(autoreverses: false)) { phase = 2 }
        }
    }

    private func skeletonLine(width: CGFloat) -> some View {
        GeometryReader { geo in
            RoundedRectangle(cornerRadius: 4)
                .fill(Color.secondary.opacity(0.14))
                .overlay(
                    LinearGradient(
                        colors: [.clear, Color.white.opacity(0.6), .clear],
                        startPoint: .leading, endPoint: .trailing
                    )
                    .frame(width: geo.size.width * 0.4)
                    .offset(x: (phase - 1) * geo.size.width)
                )
                .clipShape(RoundedRectangle(cornerRadius: 4))
        }
        .frame(height: 9)
        .frame(width: nil)
        .scaleEffect(x: width, anchor: .leading)
    }
}
