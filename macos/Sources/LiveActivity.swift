import SwiftUI
import AppKit

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

    // Deduped event stamps so a burst is neither missed nor replayed.
    private var lastSeenLearned: Date?
    private var lastSeenCaptured: Date?
    private var lastSeenCompletion: Date?

    private let activeInterval: TimeInterval = 0.2
    private let idleInterval: TimeInterval = 0.5

    init(snapshotProvider: @escaping @MainActor () -> LiveActivitySnapshot) {
        self.snapshotProvider = snapshotProvider
    }

    func start() {
        loopTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                let interval = self.tick()
                try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
            }
        }
    }

    func stop() {
        loopTask?.cancel()
        loopTask = nil
    }

    private func tick() -> TimeInterval {
        let snap = snapshotProvider()

        // Master switch: user turned live activity off → hide everything and do no work. Old event
        // stamps go stale (>6s), so nothing replays when re-enabled.
        guard snap.enabled else {
            hud.forceHide()
            glow.setActive(false)
            pill?.hidePill()
            return idleInterval
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
        return busy ? activeInterval : idleInterval
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

    private static let width: CGFloat = 460
    private static let height: CGFloat = 96
    private static let gap: CGFloat = 18

    /// True while anything is on screen — keeps the coordinator sampling fast for a smooth bar.
    var isPresenting: Bool { mode != .idle }

    /// Live progress while a sync/import runs. Called every working tick; cancels any pending dismiss.
    func updateProgress(done: Int, total: Int, detail: String?) {
        let panel = ensurePanel()
        position(panel)
        dismissWork?.cancel()
        mode = .progress
        let title: String
        if let detail, !detail.isEmpty {
            title = "Learning from \(detail)"
        } else {
            title = "Building your memory"
        }
        let count = total > 0 ? "\(done.formatted()) / \(total.formatted())" : ""
        // Always show a bar while working: a determinate fill when we know the total, an animated
        // indeterminate sweep while the count is still unknown (early sync / count-less work).
        model.apply(title: title, subtitle: count, fraction: total > 0 ? Double(done) / Double(total) : 0,
                    showBar: true, indeterminate: total <= 0, celebration: false)
        present(panel)
    }

    /// Work stopped without a celebration → dismiss the progress HUD. Guarded so the coordinator can
    /// call it every idle tick harmlessly, and so it never interrupts a celebration in flight.
    func clearProgress() {
        guard mode == .progress else { return }
        mode = .dismissing
        scheduleDismiss(after: 0.35)
    }

    /// Immediately dismiss regardless of mode (used when the user turns live activity off).
    func forceHide() {
        guard mode != .idle else { return }
        mode = .dismissing
        scheduleDismiss(after: 0)
    }

    /// A sync just finished and added memories → morph to a check + "Learned N new memories", hold,
    /// then contract out. Supersedes any in-flight progress dismiss.
    func celebrate(learnedCount: Int) {
        let panel = ensurePanel()
        position(panel)
        dismissWork?.cancel()
        mode = .celebration
        let noun = learnedCount == 1 ? "memory" : "memories"
        model.apply(title: "Learned \(learnedCount) new \(noun)", subtitle: "Added to your memory",
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
        .padding(.leading, CortexDesign.Space.sm)
        .padding(.trailing, CortexDesign.Space.md)
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
                    let fillW = max(3, w * fraction)
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
                withAnimation(.easeInOut(duration: 0.4)) {
                    breathe = false
                }
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
        let y = screen.visibleFrame.minY - Self.side * 0.35
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
                    .scaleEffect(0.2 + p * 0.9)
                    .opacity(model.animating ? 1 : 0)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .allowsHitTesting(false)
    }
}
