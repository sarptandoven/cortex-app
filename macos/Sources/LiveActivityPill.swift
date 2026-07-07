import SwiftUI
import AppKit

/// P5 — the interactive bottom-center "live activity" pill. Unlike the ambient surfaces it accepts
/// clicks and can take key focus, so it lives in its own file with its own panel subclass. The
/// coordinator only ever shows it when IDLE with items waiting (never while the Learning HUD is up),
/// so the two never fight for bottom-center. It is a second, reachable surface for the core loop
/// (see what's waiting → clear the Review queue → open Cortex) for people who live at the bottom of
/// the screen; it duplicates the menu-bar icon's job on purpose, for reachability.
@MainActor
final class LiveActivityPill: LiveActivityPillControlling {
    struct Actions {
        let openReview: () -> Void
        let openApp: () -> Void
    }

    private let actions: Actions
    private var panel: InteractivePillPanel?
    private let model = PillModel()
    private var visible = false
    private var orderOutWork: DispatchWorkItem?
    private var autoDismissWork: DispatchWorkItem?
    /// The highest pending count we've already surfaced. The pill only re-appears when the backlog
    /// GROWS beyond this, so it's a gentle reminder rather than a bar that pins forever.
    private var lastSurfacedCount = 0

    private static let width: CGFloat = 320
    private static let height: CGFloat = 92
    private static let gap: CGFloat = 18
    private static let autoDismissAfter: TimeInterval = 8

    init(actions: Actions) {
        self.actions = actions
    }

    /// Non-pinning update. Shows a reminder ONLY when the pending backlog grows beyond what we last
    /// surfaced, then auto-dismisses after a few seconds. A steady backlog never re-pins the pill;
    /// when the backlog drops (user reviewed items) we lower the watermark so a later increase can
    /// remind again.
    func updatePending(_ count: Int) {
        if count <= 0 {
            lastSurfacedCount = 0
            hidePill()
            return
        }
        if count < lastSurfacedCount {
            lastSurfacedCount = count  // backlog shrank; re-arm for the next growth
        }
        guard count > lastSurfacedCount else { return }  // no NEW items since we last reminded
        lastSurfacedCount = count
        present(count: count)
    }

    private func present(count: Int) {
        let panel = ensurePanel()
        position(panel)
        model.pendingCount = count
        orderOutWork?.cancel()
        if !visible {
            visible = true
            panel.alphaValue = 1
            panel.orderFrontRegardless()
            withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) { model.visible = true }
        }
        // Auto-dismiss so it never becomes a permanent fixture; hovering keeps it (see PillView).
        autoDismissWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self, !self.model.hovering else { return }
            self.hidePill()
        }
        autoDismissWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.autoDismissAfter, execute: work)
    }

    func hidePill() {
        autoDismissWork?.cancel()
        guard visible else { return }
        visible = false
        withAnimation(.spring(response: 0.34, dampingFraction: 0.9)) { model.visible = false }
        let work = DispatchWorkItem { [weak self] in
            guard let self, !self.model.visible else { return }
            self.panel?.orderOut(nil)
        }
        orderOutWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.42, execute: work)
    }

    private func ensurePanel() -> InteractivePillPanel {
        if let panel { return panel }
        let panel = InteractivePillPanel(width: Self.width, height: Self.height)
        let view = PillView(
            model: model,
            onReview: { [weak self] in self?.actions.openReview() },
            onOpen: { [weak self] in self?.actions.openApp() }
        )
        // Use a first-mouse-accepting host (NOT the shared installHostingView) so a single click
        // fires the buttons even when Cortex isn't the active app — which is the pill's whole point.
        let hosting = FirstMouseHostingView(rootView: view)
        hosting.frame = NSRect(x: 0, y: 0, width: Self.width, height: Self.height)
        hosting.autoresizingMask = [.width, .height]
        panel.contentView = hosting
        self.panel = panel
        return panel
    }

    private func position(_ panel: InteractivePillPanel) {
        guard let screen = liveActivityActiveScreen() else { return }
        let x = screen.frame.midX - Self.width / 2
        let y = screen.visibleFrame.minY + Self.gap
        panel.setFrame(NSRect(x: x, y: y, width: Self.width, height: Self.height), display: false)
    }
}

/// Accepts clicks (so its buttons work) and can take key focus for a control WITHOUT activating the
/// whole app — `becomesKeyOnlyIfNeeded` + `.nonactivatingPanel` is the utility-panel recipe that
/// avoids the focus fight that plagued the menu-bar popover.
final class InteractivePillPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    convenience init(width: CGFloat, height: CGFloat) {
        self.init(
            contentRect: NSRect(x: 0, y: 0, width: width, height: height),
            styleMask: [.nonactivatingPanel, .borderless],
            backing: .buffered,
            defer: false
        )
        isFloatingPanel = true
        becomesKeyOnlyIfNeeded = true
        level = .statusBar
        backgroundColor = .clear
        isOpaque = false
        hasShadow = false
        hidesOnDeactivate = false
        isMovableByWindowBackground = false
        ignoresMouseEvents = false
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
    }
}

/// Delivers the FIRST click even when Cortex isn't the active app. The idle pill is shown while the
/// user works in another app, so with a plain NSHostingView the first click would be consumed just
/// to bring the window forward (needing a second click to actually hit the button). Accepting first
/// mouse makes a single click fire Review / Open.
final class FirstMouseHostingView<V: View>: NSHostingView<V> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

private final class PillModel: ObservableObject {
    @Published var visible = false
    @Published var pendingCount = 0
    /// True while the pointer is over the pill — suppresses the auto-dismiss so it doesn't vanish
    /// out from under the user mid-interaction.
    @Published var hovering = false
}

private struct PillView: View {
    @ObservedObject var model: PillModel
    let onReview: () -> Void
    let onOpen: () -> Void

    @State private var expanded = false

    var body: some View {
        VStack {
            Spacer(minLength: 0)
            if model.visible {
                content
                    .transition(
                        .asymmetric(
                            insertion: .scale(scale: 0.9, anchor: .bottom).combined(with: .opacity).combined(with: .move(edge: .bottom)),
                            removal: .scale(scale: 0.94, anchor: .bottom).combined(with: .opacity)
                        )
                    )
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var content: some View {
        VStack(spacing: expanded ? 10 : 0) {
            HStack(spacing: CortexDesign.Space.sm) {
                ZStack {
                    Circle().fill(CortexDesign.accent.opacity(0.14)).frame(width: 28, height: 28)
                    Image(systemName: "tray.full.fill")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(CortexDesign.accent)
                }
                Text("\(model.pendingCount) to review")
                    .font(CortexDesign.Typography.body.weight(.semibold))
                    .foregroundColor(CortexDesign.ink)
                    .lineLimit(1)
            }
            if expanded {
                HStack(spacing: CortexDesign.Space.sm) {
                    Button(action: onReview) {
                        Text("Review")
                            .font(CortexDesign.Typography.caption.weight(.semibold))
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(CortexDesign.accent)
                    Button(action: onOpen) {
                        Text("Open Cortex")
                            .font(CortexDesign.Typography.caption)
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                }
            }
        }
        .padding(.horizontal, CortexDesign.Space.md)
        .padding(.vertical, expanded ? CortexDesign.Space.md : CortexDesign.Space.sm)
        .frame(width: expanded ? 300 : nil)
        .background(
            RoundedRectangle(cornerRadius: expanded ? 14 : 22, style: .continuous)
                .fill(CortexDesign.panelBackground)
                .overlay(
                    RoundedRectangle(cornerRadius: expanded ? 14 : 22, style: .continuous)
                        .strokeBorder(CortexDesign.accent.opacity(0.22), lineWidth: 1)
                )
                .shadow(color: Color.black.opacity(0.18), radius: 16, x: 0, y: 6)
        )
        .onHover { hovering in
            model.hovering = hovering
            withAnimation(.spring(response: 0.32, dampingFraction: 0.82)) { expanded = hovering }
        }
        .padding(.bottom, 4)
    }
}
