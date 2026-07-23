import SwiftUI
import AppKit
import Combine

// MARK: - Snapshot

/// The slice of AppState the bottom-of-screen "live activity" surfaces react to. Built by the
/// AppDelegate each sample tick (mirrors the menu-bar snapshot), so the coordinator never touches
/// AppState directly and stays trivially testable. All strings are already display-safe (source
/// names run through SourceDisplayName before they get here).
struct LiveActivitySnapshot: Equatable {
    /// User master switch. When false the coordinator shows nothing (and hides anything up).
    var enabled: Bool
    /// Something is in flight (sync/import/backup/launch/busy). LEVEL signal — true for the whole
    /// operation, so the sampling loop can't miss it.
    var working: Bool
    var done: Int
    var total: Int
    /// A user-facing "what" label (e.g. "Notes", "ChatGPT") or nil for the generic phrasing.
    var detail: String?
    /// Items waiting in Review — drives the idle "N to review" pill.
    var pendingCount: Int
    /// New-stamp signals (timestamp-keyed, deduped) that punctuate learning/capture events.
    var learnedAt: Date?
    var capturedAt: Date?
    var learnedCount: Int
    /// A NEW timestamp means a real sync just finished → the HUD celebrates.
    var syncCompletedAt: Date?
}

// MARK: - Interactive-pill hook

/// The coordinator arbitrates the bottom-center region, but the interactive pill (P5) lives in its
/// own file and needs key focus, so it's injected behind this tiny protocol rather than owned
/// directly. Keeps the ambient surfaces (which never take focus) decoupled from the interactive one.
@MainActor
protocol LiveActivityPillControlling: AnyObject {
    /// Non-pinning: flashes a reminder when the review backlog grows, then auto-dismisses.
    func updatePending(_ count: Int)
    func hidePill()
}

// MARK: - Shared bottom panel

/// A borderless, non-activating panel pinned near the bottom of the active screen. Never becomes key
/// or main (so it can't steal focus from the frontmost app), floats at the status-bar window level,
/// and joins all spaces. The ambient surfaces set `ignoresMouseEvents = true`; the interactive pill
/// (P5) uses its own panel subclass that can become key on demand.
final class BottomAmbientPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }

    convenience init(width: CGFloat, height: CGFloat) {
        self.init(
            contentRect: NSRect(x: 0, y: 0, width: width, height: height),
            styleMask: [.nonactivatingPanel, .borderless],
            backing: .buffered,
            defer: false
        )
        isFloatingPanel = true
        level = .statusBar
        backgroundColor = .clear
        isOpaque = false
        hasShadow = false
        hidesOnDeactivate = false
        isMovableByWindowBackground = false
        ignoresMouseEvents = true
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
    }
}

/// The screen that owns the space under the mouse, falling back to the primary screen. Shared by all
/// live-activity surfaces so they consistently appear on the display the user is looking at.
@MainActor
func liveActivityActiveScreen() -> NSScreen? {
    let mouse = NSEvent.mouseLocation
    return NSScreen.screens.first { NSMouseInRect(mouse, $0.frame, false) } ?? NSScreen.main
}

/// Host a SwiftUI root inside a panel at an explicit fill frame. (Setting
/// translatesAutoresizingMaskIntoConstraints=false with no constraints leaves a zero-size hosting
/// view — the same bug that made the notch pill invisible — so we always give an explicit frame.)
@MainActor
func installHostingView<V: View>(_ view: V, in panel: NSPanel, width: CGFloat, height: CGFloat) {
    let hosting = NSHostingView(rootView: view)
    hosting.frame = NSRect(x: 0, y: 0, width: width, height: height)
    hosting.autoresizingMask = [.width, .height]
    panel.contentView = hosting
}

// MARK: - Coordinator

/// The single owner of the bottom-of-screen live-activity region. A lightweight @MainActor sampling
/// loop (same proven shape as MenuBarAnimator) reads a snapshot each tick and drives:
///   • P1 BottomLearningHUD — live sync/learn progress + a completion flourish.
///   • P3 AmbientEdgeGlow    — a calm bottom glow, only while working.
///   • P4 MemoryFormedRipple — a one-shot ring on each new learned/captured event.
///   • P5 LiveActivityPill   — an idle "N to review" pill (injected; interactive).
/// Arbitration rule: while WORKING the HUD owns bottom-center and the pill is hidden; when idle with
/// items waiting the pill shows; at true idle everything is calm (no motion). This is what keeps the
/// bottom edge from ever showing two competing elements.
@MainActor
final class LiveActivityCenter {
    private let snapshotProvider: @MainActor () -> LiveActivitySnapshot
    weak var pill: LiveActivityPillControlling?

    private let hud = BottomLearningHUD()
    private let glow = AmbientEdgeGlow()
    private let ripple = MemoryFormedRipple()

    private var loopTask: Task<Void, Never>?
    private var cancellables = Set<AnyCancellable>()

    /// Fires whenever the underlying AppState changes (bridged from `objectWillChange` at
    /// construction). The snapshot is derived ENTIRELY from AppState, so this is a complete wake
    /// source: any change that could make us busy (working/learnedAt/capturedAt/pendingCount/enabled)
    /// bumps it. When wired we fully suspend at idle and let this restart the loop; when nil we fall
    /// back to a gentle idle poll so the loop can never get permanently stuck.
    private let changeSignal: AnyPublisher<Void, Never>?

    /// start() was called and stop() has not — gates whether wakes are allowed to (re)arm the loop.
    private var started = false
    /// The app is frontmost. We pause entirely in the background (didResignActive) and resume on
    /// didBecomeActive, so nothing samples while the user is looking at another app.
    private var appActive = true

    // Deduped event stamps so a burst is neither missed nor replayed.
    private var lastSeenLearned: Date?
    private var lastSeenCaptured: Date?
    private var lastSeenCompletion: Date?

    private let activeInterval: TimeInterval = 0.2
    private let idleInterval: TimeInterval = 0.5

    /// The result of a single tick: either there is live motion to keep sampling at `activeInterval`,
    /// or there is nothing to do and the loop should go quiescent (suspend, or slow-poll if unwired).
    private enum TickOutcome {
        case busy(TimeInterval)
        case idle
    }

    init(snapshotProvider: @escaping @MainActor () -> LiveActivitySnapshot,
         changeSignal: AnyPublisher<Void, Never>? = nil) {
        self.snapshotProvider = snapshotProvider
        self.changeSignal = changeSignal
    }

    func start() {
        started = true
        appActive = NSApplication.shared.isActive
        observeAppActivation()
        observeStateChanges()
        resume()   // reflect current state once, then self-arm only while busy
    }

    func stop() {
        started = false
        loopTask?.cancel()
        loopTask = nil
        cancellables.removeAll()
    }

    /// Restart the sampling loop if it isn't already running. Cheap and idempotent: called from every
    /// state-change signal and from didBecomeActive, so a suspended loop reliably wakes the instant
    /// there is something to do. No-op while stopped, in the background, or already looping.
    func wake() {
        resume()
    }

    private func resume() {
        guard started, appActive, loopTask == nil else { return }
        loopTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                switch self.tick() {
                case .busy(let interval):
                    // Live motion (working / HUD / ripple) → keep the fast cadence smooth.
                    try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
                case .idle:
                    if self.changeSignal == nil {
                        // No event bridge wired → fall back to a gentle idle poll rather than
                        // suspending, so we can never get stuck (still stops in the background,
                        // since the loop is cancelled on didResignActive).
                        try? await Task.sleep(nanoseconds: UInt64(self.idleInterval * 1_000_000_000))
                    } else {
                        // Nothing to do → SUSPEND. A state change (changeSignal) or re-activation
                        // (didBecomeActive) calls wake()/resume() to restart us. Drop the task handle
                        // (no await between here and return, so no wake can be lost in the gap).
                        self.loopTask = nil
                        return
                    }
                }
            }
        }
    }

    /// Pause in the background, resume in the foreground — the sampling loop must not run while the
    /// user is looking at another app.
    private func observeAppActivation() {
        let nc = NotificationCenter.default
        nc.publisher(for: NSApplication.didResignActiveNotification)
            .sink { [weak self] _ in
                guard let self else { return }
                self.appActive = false
                self.loopTask?.cancel()
                self.loopTask = nil
            }
            .store(in: &cancellables)
        nc.publisher(for: NSApplication.didBecomeActiveNotification)
            .sink { [weak self] _ in
                guard let self else { return }
                self.appActive = true
                self.resume()
            }
            .store(in: &cancellables)
    }

    /// Bridge AppState changes to wake(). Delivered async on the main queue so the tick reads the
    /// COMMITTED value (objectWillChange fires in willSet, before the property updates).
    private func observeStateChanges() {
        guard let changeSignal else { return }
        changeSignal
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.wake() }
            .store(in: &cancellables)
    }

    private func tick() -> TickOutcome {
        let snap = snapshotProvider()

        // Master switch: user turned live activity off → hide everything and do no work. Old event
        // stamps go stale (>6s), so nothing replays when re-enabled.
        guard snap.enabled else {
            hud.forceHide()
            glow.setActive(false)
            pill?.hidePill()
            return .idle
        }

        // --- Event-driven punctuation (each fires at most once per new stamp) ---
        if let completed = snap.syncCompletedAt, completed != lastSeenCompletion {
            lastSeenCompletion = completed
            if completed.timeIntervalSinceNow > -6, snap.learnedCount > 0 {
                hud.celebrate(learnedCount: snap.learnedCount)
            }
        }
        if let captured = snap.capturedAt, captured != lastSeenCaptured {
            lastSeenCaptured = captured
            if captured.timeIntervalSinceNow > -6 { ripple.fire(style: .captured) }
        }
        if let learned = snap.learnedAt, learned != lastSeenLearned {
            lastSeenLearned = learned
            // A quick-capture bumps both stamps; the captured ripple already covers that moment.
            if learned.timeIntervalSinceNow > -6, snap.capturedAt != learned { ripple.fire(style: .learned) }
        }

        // --- Continuous surfaces ---
        glow.setActive(snap.working)
        if snap.working {
            hud.updateProgress(done: snap.done, total: snap.total, detail: snap.detail)
            pill?.hidePill()
        } else {
            hud.clearProgress()  // no-op unless a progress HUD is currently up (celebration is left alone)
            // The pill and the HUD share bottom-center, so the pill may only be driven once the HUD
            // is fully idle — never while a completion celebration (or a dismiss) is still on screen.
            // `updatePending` is non-pinning: it flashes a reminder when the backlog GROWS and
            // auto-dismisses, so it never becomes a permanent bar the user can't get rid of.
            if !hud.isPresenting {
                pill?.updatePending(snap.pendingCount)
            } else {
                pill?.hidePill()
            }
        }

        let busy = snap.working || hud.isPresenting || ripple.isAnimating
        return busy ? .busy(activeInterval) : .idle
    }
}

// MARK: - P1: Bottom Learning HUD

@MainActor
final class BottomLearningHUD {
    private enum Mode { case idle, progress, dismissing, celebration }

    private var panel: BottomAmbientPanel?
    private let model = LearningHUDModel()
    private var mode: Mode = .idle
    private var dismissWork: DispatchWorkItem?

    // Self-limiting so the bar can NEVER become a permanent fixture: an indeterminate bar (no known
    // total) shows only a few seconds, and a determinate bar that stops advancing (stalled/stuck sync)
    // also auto-dismisses — regardless of how long the upstream "working" signal stays raised. Once
    // capped we stay hidden for the rest of the episode, until progress genuinely advances again or
    // work stops (clearProgress resets the trackers for the next episode).
    private var progressStartedAt: Date?
    private var lastAdvanceAt: Date?
    private var lastSeenDone: Int = -1
    private var progressCapped = false
    private static let indeterminateMaxSeconds: TimeInterval = 5
    private static let stallMaxSeconds: TimeInterval = 12

    private static let width: CGFloat = 460
    private static let height: CGFloat = 96
    private static let gap: CGFloat = 18

    /// True while anything is on screen — keeps the coordinator sampling fast for a smooth bar.
    var isPresenting: Bool { mode != .idle }

    /// Live progress while a sync/import runs. Called every working tick. Self-limits: keeps showing
    /// while progress genuinely advances, but auto-dismisses an indeterminate bar after a few seconds
    /// and a stalled determinate bar after the stall window — so it can never become a permanent bar.
    func updateProgress(done: Int, total: Int, detail: String?) {
        // Never hijack an in-flight celebration; it schedules its own dismiss.
        guard mode != .celebration else { return }

        let now = Date()
        // Real advancement resets the stall timer and lifts any cap (work genuinely resumed).
        if done > lastSeenDone {
            lastSeenDone = done
            lastAdvanceAt = now
            progressCapped = false
        }
        // Capped for this episode → stay hidden until progress advances (handled above) or work stops.
        guard !progressCapped else { return }

        // Begin a fresh episode when we weren't already showing progress.
        if mode != .progress {
            mode = .progress
            progressStartedAt = now
            if lastAdvanceAt == nil { lastAdvanceAt = now }
        }

        // Self-limit: an indeterminate bar may only show a few seconds; a determinate bar that stops
        // advancing is treated as stalled. Either way, dismiss and suppress re-showing this episode.
        let shownFor = now.timeIntervalSince(progressStartedAt ?? now)
        let sinceAdvance = now.timeIntervalSince(lastAdvanceAt ?? now)
        let stale = (total <= 0 && shownFor > Self.indeterminateMaxSeconds)
                 || (total > 0 && sinceAdvance > Self.stallMaxSeconds)
        if stale {
            progressCapped = true
            mode = .dismissing
            scheduleDismiss(after: 0.2)
            return
        }

        let panel = ensurePanel()
        position(panel)
        dismissWork?.cancel()
        let title: String
        if let detail, !detail.isEmpty {
            title = "Learning from \(detail)"
        } else {
            title = "Building your memory"
        }
        let count = total > 0 ? "\(done.formatted()) / \(total.formatted())" : ""
        // Determinate fill when we know the total; animated indeterminate sweep until a count arrives.
        model.apply(title: title, subtitle: count, fraction: total > 0 ? Double(done) / Double(total) : 0,
                    showBar: true, indeterminate: total <= 0, celebration: false)
        present(panel)
    }

    /// Work stopped without a celebration → dismiss the progress HUD. Guarded so the coordinator can
    /// call it every idle tick harmlessly, and so it never interrupts a celebration in flight.
    func clearProgress() {
        // Work stopped → reset the episode trackers so the next real work shows fresh.
        resetProgressTracking()
        guard mode == .progress else { return }
        mode = .dismissing
        scheduleDismiss(after: 0.35)
    }

    /// Immediately dismiss regardless of mode (used when the user turns live activity off).
    func forceHide() {
        resetProgressTracking()
        guard mode != .idle else { return }
        mode = .dismissing
        scheduleDismiss(after: 0)
    }

    private func resetProgressTracking() {
        lastSeenDone = -1
        lastAdvanceAt = nil
        progressStartedAt = nil
        progressCapped = false
    }

    /// A sync just finished and added memories → morph to a check + "Learned N new memories", hold,
    /// then contract out. Supersedes any in-flight progress dismiss.
    func celebrate(learnedCount: Int) {
        let panel = ensurePanel()
        position(panel)
        dismissWork?.cancel()
        mode = .celebration
        resetProgressTracking()   // the episode ended; a post-celebration working tick starts clean
        let noun = learnedCount == 1 ? "memory" : "memories"
        // No side-by-side subtitle here: it shares the title's fixed-width row and would truncate
        // the headline for large counts ("Learned 1284 new me…"). The check-seal icon already says
        // "added to memory".
        model.apply(title: "Learned \(learnedCount) new \(noun)", subtitle: "",
                    fraction: 1, showBar: false, indeterminate: false, celebration: true)
        present(panel)
        scheduleDismiss(after: 2.6)
    }

    // MARK: internals

    private func present(_ panel: BottomAmbientPanel) {
        // A fresh appearance (HUD content was hidden) → bump the epoch so the progress bar gets a new
        // identity and its sweep/shimmer animation restarts cleanly, even though the hosting view is
        // reused across presents.
        if !model.visible { model.barEpoch &+= 1 }
        if !panel.isVisible {
            panel.alphaValue = 1
            panel.orderFrontRegardless()
        }
        withAnimation(.spring(response: 0.42, dampingFraction: 0.78)) {
            model.visible = true
        }
    }

    private func scheduleDismiss(after delay: TimeInterval) {
        dismissWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            withAnimation(.spring(response: 0.34, dampingFraction: 0.9)) {
                self.model.visible = false
            }
            self.mode = .idle
            let orderOut = DispatchWorkItem { [weak self] in
                guard let self, !self.model.visible else { return }
                self.panel?.orderOut(nil)
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.42, execute: orderOut)
        }
        dismissWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    private func ensurePanel() -> BottomAmbientPanel {
        if let panel { return panel }
        let panel = BottomAmbientPanel(width: Self.width, height: Self.height)
        installHostingView(LearningHUDView(model: model), in: panel, width: Self.width, height: Self.height)
        self.panel = panel
        return panel
    }

    private func position(_ panel: BottomAmbientPanel) {
        guard let screen = liveActivityActiveScreen() else { return }
        let x = screen.frame.midX - Self.width / 2
        let y = screen.visibleFrame.minY + Self.gap  // visibleFrame excludes the Dock → sits above it
        panel.setFrame(NSRect(x: x, y: y, width: Self.width, height: Self.height), display: false)
    }
}

/// Observable backing store for the HUD so a single reused hosting view re-renders content + drives
/// the spring in/out without being torn down.
private final class LearningHUDModel: ObservableObject {
    @Published var visible = false
    @Published var title = ""
    @Published var subtitle = ""
    @Published var fraction: Double = 0
    @Published var showBar = true
    /// No known total yet (early sync / count-less work) → the bar sweeps indeterminately instead of
    /// showing a fixed fill, so it still reads as actively working.
    @Published var indeterminate = false
    @Published var celebration = false
    /// Bumped on every fresh HUD appearance. The progress bar is `.id`'d on this so each present gets
    /// a brand-new view identity (fresh @State + onAppear) — the hosting view is reused across
    /// presents, so without this a re-present in the same mode would not restart the sweep animation.
    @Published var barEpoch: Int = 0

    func apply(title: String, subtitle: String, fraction: Double, showBar: Bool, indeterminate: Bool, celebration: Bool) {
        self.title = title
        self.subtitle = subtitle
        self.showBar = showBar
        self.indeterminate = indeterminate
        self.celebration = celebration
        // Ease the determinate fill toward its new value so 4s→~1s poll samples read as a live,
        // growing fill rather than discrete steps.
        withAnimation(.easeInOut(duration: 0.5)) {
            self.fraction = min(1, max(0, fraction))
        }
    }
}

private struct LearningHUDView: View {
    @ObservedObject var model: LearningHUDModel

    private var accent: Color { model.celebration ? CortexDesign.sealMoss : CortexDesign.sealMoss }

    var body: some View {
        VStack {
            Spacer(minLength: 0)
            if model.visible {
                pill
                    .transition(
                        .asymmetric(
                            insertion: .scale(scale: 0.9, anchor: .bottom)
                                .combined(with: .opacity)
                                .combined(with: .move(edge: .bottom)),
                            removal: .scale(scale: 0.94, anchor: .bottom)
                                .combined(with: .opacity)
                        )
                    )
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .allowsHitTesting(false)
    }

    private var pill: some View {
        HStack(spacing: CortexDesign.Space.sm) {
            ZStack {
                Circle().fill(accent.opacity(0.14)).frame(width: 32, height: 32)
                Image(systemName: model.celebration ? "checkmark.seal.fill" : "brain.head.profile")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundColor(accent)
            }
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline) {
                    Text(model.title)
                        .font(CortexDesign.Typography.body.weight(.semibold))
                        .foregroundColor(CortexDesign.ink)
                        .lineLimit(1)
                    Spacer(minLength: CortexDesign.Space.sm)
                    if !model.subtitle.isEmpty {
                        Text(model.subtitle)
                            .font(CortexDesign.Typography.stamp)
                            .foregroundColor(CortexDesign.inkSecondary)
                            .lineLimit(1)
                    }
                }
                if model.showBar {
                    HUDProgressBar(fraction: model.fraction, indeterminate: model.indeterminate, accent: accent)
                        .id(model.barEpoch)   // fresh identity per present → animation restarts cleanly
                }
            }
            .frame(width: 300, alignment: .leading)
        }
        // The pill is a Capsule, so its rounded ends occupy ~half its height (~26pt) on each side.
        // Inset the content past that radius — otherwise the full-width progress track and the
        // right-aligned subtitle spill into the curved ends (the track appears to run past the
        // pill). Leading clears the icon off the left curve; trailing (≥ the corner radius) keeps
        // the track/subtitle inside the flat middle.
        .padding(.leading, CortexDesign.Space.md)
        .padding(.trailing, CortexDesign.Space.xl)
        .padding(.vertical, 10)
        .background(
            Capsule(style: .continuous)
                .fill(CortexDesign.panelBackground)
                .overlay(Capsule(style: .continuous).strokeBorder(accent.opacity(0.22), lineWidth: 1))
                .shadow(color: Color.black.opacity(0.18), radius: 16, x: 0, y: 6)
        )
        .padding(.bottom, 4)
    }
}

/// The Learning HUD's animated progress bar. Two modes:
///  • determinate (total known) — a moss fill (eased by the model) with a soft gloss sweeping across
///    it so it reads as actively working even between the ~1.2s poll samples;
///  • indeterminate (total unknown) — a short segment sweeping left→right on a repeat.
/// The repeating animations are safe to leave as `repeatForever`: this view only exists inside the
/// HUD's `if model.visible { pill }`, so it (and its animations) are torn down the moment the HUD
/// dismisses — unlike the edge-glow, which lives for the app's lifetime and had to gate on state.
private struct HUDProgressBar: View {
    var fraction: Double
    var indeterminate: Bool
    var accent: Color

    @State private var sweep: CGFloat = 0     // indeterminate segment position (0…1)
    @State private var shimmer: CGFloat = 0   // determinate gloss position (0…1)

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            ZStack(alignment: .leading) {
                Capsule().fill(CortexDesign.ink.opacity(0.10))   // track

                if indeterminate {
                    let segW = max(24, w * 0.34)
                    Capsule()
                        .fill(accent.opacity(0.85))
                        .frame(width: segW)
                        .offset(x: -segW + sweep * (w + segW))    // enter left, exit right
                } else {
                    // Clamp defensively: GeometryReader does not clip, so a fraction > 1 (stale
                    // done/total) would let the fill escape the track. The model clamps too.
                    let fillW = min(w, max(3, w * min(1, max(0, fraction))))
                    Capsule()
                        .fill(accent)
                        .frame(width: fillW)
                        .overlay(
                            LinearGradient(
                                colors: [.white.opacity(0), .white.opacity(0.35), .white.opacity(0)],
                                startPoint: .leading, endPoint: .trailing
                            )
                            .frame(width: max(20, fillW * 0.45))
                            .offset(x: -fillW * 0.45 + shimmer * (fillW + fillW * 0.45))
                        )
                        .clipShape(Capsule())   // keep both fill and gloss inside the capsule
                }
            }
            // Clip the WHOLE bar (track + determinate fill + the indeterminate sweep) to the track
            // shape. The indeterminate segment is offset beyond both ends to "enter left / exit
            // right", and GeometryReader does not clip its children — without this, the moving
            // segment spills past the pill's rounded end (the reported "progress goes beyond the bar").
            .clipShape(Capsule())
        }
        .frame(height: 5)
        .onAppear { restart() }
        .onChange(of: indeterminate) { _ in restart() }
        .onDisappear { hardStop() }
    }

    /// Snap both phases to 0 with animations DISABLED — this reliably terminates any in-flight
    /// `repeatForever` (re-animating a property with a finite animation does not dependably replace a
    /// repeating one on the AppKit hosting path).
    private func hardStop() {
        var t = Transaction()
        t.disablesAnimations = true
        withTransaction(t) {
            sweep = 0
            shimmer = 0
        }
    }

    private func restart() {
        hardStop()   // kill any prior loop before starting the new one
        if indeterminate {
            withAnimation(.linear(duration: 1.15).repeatForever(autoreverses: false)) { sweep = 1 }
        } else {
            withAnimation(.easeInOut(duration: 1.4).repeatForever(autoreverses: false)) { shimmer = 1 }
        }
    }
}

// MARK: - P3: Ambient bottom edge-glow

@MainActor
final class AmbientEdgeGlow {
    private var panel: BottomAmbientPanel?
    private let model = EdgeGlowModel()
    private var active = false
    private var orderOutWork: DispatchWorkItem?

    private static let height: CGFloat = 90

    /// Fade the glow in while working, out otherwise. Idempotent — safe to call every tick.
    func setActive(_ working: Bool) {
        if working {
            let panel = ensurePanel()
            // Reposition EVERY working tick (not just on the transition) so the glow follows the
            // active screen in lockstep with the mouse-following HUD/ripple/pill on multi-monitor.
            position(panel)
            guard !active else { return }
            active = true
            orderOutWork?.cancel()
            if !panel.isVisible { panel.alphaValue = 1; panel.orderFrontRegardless() }
            withAnimation(.easeInOut(duration: 0.6)) { model.active = true }
        } else {
            guard active else { return }
            active = false
            withAnimation(.easeInOut(duration: 0.8)) { model.active = false }
            let work = DispatchWorkItem { [weak self] in
                guard let self, !self.model.active else { return }
                self.panel?.orderOut(nil)
            }
            orderOutWork = work
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.9, execute: work)
        }
    }

    private func ensurePanel() -> BottomAmbientPanel {
        if let panel { return panel }
        // Sized to the active screen width at creation; repositioned (and resized) on each activate.
        let width = liveActivityActiveScreen()?.frame.width ?? 1440
        let panel = BottomAmbientPanel(width: width, height: Self.height)
        installHostingView(EdgeGlowView(model: model), in: panel, width: width, height: Self.height)
        self.panel = panel
        return panel
    }

    private func position(_ panel: BottomAmbientPanel) {
        guard let screen = liveActivityActiveScreen() else { return }
        let vf = screen.visibleFrame  // above the Dock, so the glow hugs the content-area bottom edge
        panel.setFrame(NSRect(x: vf.minX, y: vf.minY, width: vf.width, height: Self.height), display: false)
    }
}

private final class EdgeGlowModel: ObservableObject {
    @Published var active = false
}

private struct EdgeGlowView: View {
    @ObservedObject var model: EdgeGlowModel
    @State private var breathe = false

    var body: some View {
        LinearGradient(
            colors: [CortexDesign.sealMoss.opacity(0.0), CortexDesign.sealMoss.opacity(model.active ? (breathe ? 0.20 : 0.10) : 0.0)],
            startPoint: .top,
            endPoint: .bottom
        )
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
        .allowsHitTesting(false)
        .opacity(model.active ? 1 : 0)
        .onChange(of: model.active) { isActive in
            // Slow breathe (~2.2s) ONLY while working. Starting it from onAppear left the
            // repeatForever animation interpolating perpetually even at idle; gating it on
            // `active` starts it when the glow appears and cancels it (finite animation to a
            // resting value) when work stops.
            if isActive {
                withAnimation(.easeInOut(duration: 2.2).repeatForever(autoreverses: true)) {
                    breathe = true
                }
            } else {
                // Deterministically terminate the repeatForever. A finite animation does not
                // dependably replace a repeating one on the AppKit hosting path (see
                // HUDProgressBar.hardStop); snap with animations disabled instead. The glow's own
                // panel-alpha fade-out masks the instant opacity change when work stops.
                var t = Transaction()
                t.disablesAnimations = true
                withTransaction(t) { breathe = false }
            }
        }
    }
}

// MARK: - P4: "Memory formed" ripple

@MainActor
final class MemoryFormedRipple {
    enum Style { case learned, captured }

    private var panel: BottomAmbientPanel?
    private let model = RippleModel()
    private var hideWork: DispatchWorkItem?
    private var lastFired: Date?

    private static let side: CGFloat = 260

    var isAnimating: Bool { model.animating }

    /// One-shot ring bloom from bottom-center. Coalesced: a burst of events within ~0.4s shows a
    /// single ripple rather than strobing.
    func fire(style: Style) {
        let now = Date()
        if let last = lastFired, now.timeIntervalSince(last) < 0.4 { return }
        lastFired = now

        let panel = ensurePanel()
        position(panel)
        hideWork?.cancel()
        model.tint = (style == .captured) ? CortexDesign.accent : CortexDesign.sealMoss
        model.animating = true
        model.progress = 0
        if !panel.isVisible { panel.alphaValue = 1; panel.orderFrontRegardless() }
        withAnimation(.easeOut(duration: 0.7)) { model.progress = 1 }

        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.model.animating = false
            self.panel?.orderOut(nil)
        }
        hideWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.78, execute: work)
    }

    private func ensurePanel() -> BottomAmbientPanel {
        if let panel { return panel }
        let panel = BottomAmbientPanel(width: Self.side, height: Self.side)
        installHostingView(RippleView(model: model), in: panel, width: Self.side, height: Self.side)
        self.panel = panel
        return panel
    }

    private func position(_ panel: BottomAmbientPanel) {
        guard let screen = liveActivityActiveScreen() else { return }
        // Centered horizontally, sitting low so the ring blooms from behind/around the HUD anchor.
        let x = screen.frame.midX - Self.side / 2
        // Clamp so the panel never drops below the screen — otherwise the ring's lower third is
        // hidden behind the Dock (or off the physical bottom when the Dock is hidden).
        let y = max(screen.frame.minY, screen.visibleFrame.minY - Self.side * 0.35)
        panel.setFrame(NSRect(x: x, y: y, width: Self.side, height: Self.side), display: false)
    }
}

private final class RippleModel: ObservableObject {
    @Published var animating = false
    @Published var progress: Double = 0
    @Published var tint: Color = CortexDesign.sealMoss
}

private struct RippleView: View {
    @ObservedObject var model: RippleModel

    var body: some View {
        ZStack {
            ForEach(0..<2, id: \.self) { i in
                let delay = Double(i) * 0.12
                let p = max(0, min(1, model.progress - delay))
                Circle()
                    .stroke(model.tint.opacity((1 - p) * 0.5), lineWidth: 2)
                    // Peak scale 0.9 (< 1.0) keeps the fully-expanded ring inside the panel; at 1.1
                    // the outermost, most-visible frames were clipped by the hosting view bounds.
                    .scaleEffect(0.2 + p * 0.7)
                    .opacity(model.animating ? 1 : 0)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .allowsHitTesting(false)
    }
}

// MARK: - P6: Live activity ticker

/// A small, floating "what's happening right now" card. Unlike the coordinator-driven ambient
/// surfaces above (P1–P5, which the AppDelegate samples via a snapshot), the ticker binds directly
/// to `AppState` and reacts to its published activity feed — it's a pure SwiftUI overlay the parent
/// mounts with `.allowsHitTesting(false)`, so it stays declarative and needs no panel/coordinator.
///
/// Idle-hiding: it renders only while the backend is actively working (`state.activityBusy`) OR an
/// event landed within the last few seconds. Recency is gauged locally — we stamp a `Date` whenever
/// the tail event's `seq` changes — so it never depends on parsing `ActivityEvent.ts`'s wire format.
/// At true idle it collapses to `EmptyView()` and disappears cleanly. Each new event swaps the
/// headline via a keyed transition so you literally SEE memories streaming past, one replacing the
/// last, with the prior one or two lingering faintly behind it.
struct LiveActivityTicker: View {
    @ObservedObject var state: AppState

    /// When the tail event last changed (drives recency-based visibility, independent of `ts`).
    @State private var lastEventAt: Date?
    /// The last tail `seq` we reacted to — so `.onChange` fires exactly once per genuinely new event.
    @State private var lastSeq: Int?
    /// Local ticking clock: re-evaluates recency so the card fades out on its own a few seconds after
    /// the final event, even if no further `@Published` change arrives to re-render.
    @State private var now = Date()

    /// How long the card lingers after the most recent event once the backend is no longer busy.
    private static let lingerWindow: TimeInterval = 3.5
    private static let cardWidth: CGFloat = 320

    init(state: AppState) { self.state = state }

    // The most-recent event (feed is most-recent LAST); nil when nothing has happened yet.
    private var latest: ActivityEvent? { state.recentActivity.last }

    /// The 1–2 events immediately before the headline, most-recent first — the faint fading stack.
    private var trailing: [ActivityEvent] {
        let feed = state.recentActivity
        guard feed.count > 1 else { return [] }
        // Take up to two events before the last, then reverse so index 0 is the nearest-to-headline.
        return Array(feed.dropLast().suffix(2).reversed())
    }

    /// Recent event still within the linger window (computed against the local `now` clock).
    private var withinLinger: Bool {
        guard let at = lastEventAt else { return false }
        return now.timeIntervalSince(at) < Self.lingerWindow
    }

    /// Show while the backend is working or an event is still recent — and only if we actually have
    /// something to show. Everything else collapses to nothing.
    private var isShowing: Bool {
        latest != nil && (state.activityBusy || withinLinger)
    }

    /// Drives `.task(id:)` for the linger heartbeat: it re-arms when a card appears/disappears
    /// (`isShowing`) or a new event lands (`seq`), and — crucially — the task cancels when it goes
    /// false, so at idle the heartbeat exits and stops re-rendering entirely.
    private var heartbeatKey: String {
        "\(isShowing)#\(latest?.seq ?? -1)"
    }

    var body: some View {
        ZStack {
            if isShowing, let latest {
                card(latest)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
        // U-LIVE4: hit-testing is no longer blanket-disabled here so the card itself can be tapped
        // (the card applies its own contentShape + onTapGesture below). The surrounding empty area of
        // this full-bleed frame stays transparent to clicks because nothing else has a gesture or an
        // opaque background — only the bounded card rect responds. NOTE: the parent mount in
        // CortexApp.swift currently wraps this view in `.allowsHitTesting(false)`; that outer modifier
        // must be removed there for taps to reach the card (owner of CortexApp.swift).
        .animation(.easeInOut(duration: 0.28), value: isShowing)
        // Swap the whole card identity on each new event → the keyed transition below plays and the
        // previous headline visibly gives way to the new one.
        .animation(.easeInOut(duration: 0.28), value: latest?.seq)
        // Stamp recency whenever a genuinely new tail event arrives (also seeds on first appearance).
        .onChange(of: latest?.seq) { seq in
            guard seq != lastSeq else { return }
            lastSeq = seq
            if seq != nil { lastEventAt = Date() }
        }
        .onAppear {
            lastSeq = latest?.seq
            if latest != nil { lastEventAt = Date() }
        }
        // A slow local heartbeat so the linger window can expire and fade the card without any further
        // upstream change. Gated on `heartbeatKey` (isShowing + latest seq): it only runs WHILE a card
        // is on screen/lingering. When nothing is showing the guard returns immediately, so idle costs
        // zero ticks and zero re-renders; the moment the linger window lapses, `isShowing` flips false,
        // the key changes, this task is cancelled, and the app goes fully quiescent.
        .task(id: heartbeatKey) {
            guard isShowing else { return }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 500_000_000)
                now = Date()
            }
        }
    }

    // MARK: card

    private func card(_ latest: ActivityEvent) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            // The faint fading stack: the previous event(s), dimmer the further back they are, so you
            // can see memories streaming by without ever exceeding ~3 lines total.
            ForEach(Array(trailing.enumerated()), id: \.element.seq) { index, event in
                Text(event.title)
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.inkFaint)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .opacity(index == 0 ? 0.5 : 0.28)
                    .transition(.opacity)
            }

            headline(latest)
                // Keyed on seq so each new event gets a fresh identity → the swap transition plays,
                // gently replacing the prior headline rather than mutating it in place.
                .id(latest.seq)
                .transition(
                    .asymmetric(
                        insertion: .move(edge: .bottom).combined(with: .opacity),
                        removal: .opacity
                    )
                )
        }
        .frame(width: Self.cardWidth, alignment: .leading)
        .padding(.horizontal, CortexDesign.Space.md)
        .padding(.vertical, CortexDesign.Space.sm + 1)
        .background(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.lg, style: .continuous)
                .fill(CortexDesign.panelBackground)
                .overlay(
                    RoundedRectangle(cornerRadius: CortexDesign.Radius.lg, style: .continuous)
                        .strokeBorder(CortexDesign.softBorder, lineWidth: 1)
                )
                .shadow(color: CortexDesign.ink.opacity(0.16), radius: 14, x: 0, y: 5)
        )
        // U-LIVE4: the whole card is now a tap target that jumps to the tab most relevant to the event
        // (e.g. a review event → Review, an import/memory event → Home) and brings Cortex forward.
        // contentShape makes the padded card fully hittable; only this bounded rect responds to clicks.
        .contentShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.lg, style: .continuous))
        .onTapGesture { openTarget(for: latest) }
        .help("Open \(targetTab(for: latest.kind).label)")
        .padding(.bottom, 14)
    }

    /// U-LIVE4: map an activity event to the most relevant destination tab and route there, bringing
    /// Cortex forward. The mapping mirrors the ticker's own icon vocabulary (`iconName(for:)`).
    private func openTarget(for event: ActivityEvent) {
        state.selectedTab = targetTab(for: event.kind)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// The tab an event kind should open. Review/graded work lands in Review; imports, new memories,
    /// graph and reach updates land on Home (the memory overview); anything unknown defaults to Home.
    private func targetTab(for kind: String) -> AppTab {
        switch kind {
        case "review": return .review
        default:       return .model
        }
    }

    private func headline(_ event: ActivityEvent) -> some View {
        HStack(alignment: .center, spacing: CortexDesign.Space.sm) {
            ZStack {
                Circle()
                    .fill(CortexDesign.goldSoft)
                    .frame(width: 30, height: 30)
                Image(systemName: iconName(for: event.kind))
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(CortexDesign.gold)
            }

            VStack(alignment: .leading, spacing: 1) {
                Text(event.title)
                    .font(CortexDesign.Typography.body.weight(.semibold))
                    .foregroundColor(CortexDesign.ink)
                    .lineLimit(1)
                    .truncationMode(.tail)
                if let secondary = secondaryLine(for: event) {
                    Text(secondary)
                        .font(CortexDesign.Typography.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }

            Spacer(minLength: CortexDesign.Space.xs)

            LivePulse()
        }
    }

    /// A subtle secondary line from `detail` and/or `source` — omitted entirely when both are empty
    /// (never fabricate a line). `detail` leads; `source` is appended as a faint catalog-style tail.
    private func secondaryLine(for event: ActivityEvent) -> String? {
        let detail = event.detail.trimmingCharacters(in: .whitespacesAndNewlines)
        let source = event.source.trimmingCharacters(in: .whitespacesAndNewlines)
        switch (detail.isEmpty, source.isEmpty) {
        case (false, false): return "\(detail) · \(source)"
        case (false, true):  return detail
        case (true, false):  return source
        case (true, true):   return nil
        }
    }

    /// Leading icon by event `kind`. Falls back to the memory glyph for unknown kinds so a new
    /// backend event type never renders a blank/`questionmark` slot.
    private func iconName(for kind: String) -> String {
        switch kind {
        case "memory":  return "sparkles"
        case "import":  return "arrow.down.doc"
        case "source":  return "trash"
        case "graph":   return "circle.hexagongrid.fill"
        case "review":  return "checkmark.seal.fill"
        case "reach":   return "antenna.radiowaves.left.and.right"
        default:        return "brain.head.profile"
        }
    }
}

/// A tiny "live" indicator — a soft gold dot that breathes while on screen. Its `repeatForever`
/// animation is safe to leave running: this view only exists inside the ticker's `if isShowing`
/// branch, so it (and the animation) are torn down the instant the card hides.
private struct LivePulse: View {
    @State private var pulsing = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Circle()
            .fill(CortexDesign.gold)
            .frame(width: 6, height: 6)
            .scaleEffect(pulsing ? 1.0 : 0.6)
            .opacity(reduceMotion ? 0.8 : (pulsing ? 1.0 : 0.4))
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.easeInOut(duration: 0.85).repeatForever(autoreverses: true)) {
                    pulsing = true
                }
            }
    }
}
