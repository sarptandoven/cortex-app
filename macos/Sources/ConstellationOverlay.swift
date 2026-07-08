import SwiftUI
import AppKit

/// P2 — "Your Constellation," summoned as a focused full-screen overlay that slides up from the
/// bottom. Reuses the exact interactive map from Home (`MemoryMapView`) at a larger size, so nodes
/// are tappable and each selection can drill into the cited memories via "Explore in Ask".
///
/// Focus discipline: it is summoned by an explicit user action (a Home button / menu / hotkey) while
/// the app is already active, so it makes ITS OWN window key with `makeKeyAndOrderFront` — it never
/// calls `NSApp.activate(ignoringOtherApps:)`, which is what caused the menu-bar popover focus fight.
/// Dismisses on Esc, on a click in the dimmed backdrop, or via the close button.
@MainActor
final class ConstellationOverlay {
    static let shared = ConstellationOverlay()

    private var panel: ConstellationPanel?
    private let model = ConstellationOverlayModel()
    private var orderOutWork: DispatchWorkItem?
    private weak var state: AppState?
    private var onExplore: ((GraphNode) -> Void)?

    private init() {}

    var isPresented: Bool { model.visible }

    /// Show the overlay for `state`. `onExplore` runs when the user taps "Explore in Ask" on a node
    /// (the overlay dismisses itself first, then calls it).
    func present(state: AppState, onExplore: @escaping (GraphNode) -> Void) {
        // Required-account gate (defense in depth): this overlay is a sibling panel NOT covered by
        // the main-window sign-in wall, so refuse to present it while the user must sign in.
        guard !state.requiresSignIn else { return }
        self.state = state
        self.onExplore = onExplore
        let panel = ensurePanel(state: state)
        position(panel)
        orderOutWork?.cancel()
        panel.alphaValue = 1
        panel.makeKeyAndOrderFront(nil)
        withAnimation(.spring(response: 0.42, dampingFraction: 0.85)) {
            model.visible = true
        }
    }

    func dismiss() {
        guard model.visible else { return }
        withAnimation(.spring(response: 0.34, dampingFraction: 0.9)) {
            model.visible = false
        }
        let work = DispatchWorkItem { [weak self] in
            guard let self, !self.model.visible else { return }
            self.panel?.orderOut(nil)
        }
        orderOutWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.44, execute: work)
    }

    func toggle(state: AppState, onExplore: @escaping (GraphNode) -> Void) {
        if model.visible { dismiss() } else { present(state: state, onExplore: onExplore) }
    }

    private func ensurePanel(state: AppState) -> ConstellationPanel {
        if let panel { return panel }
        let panel = ConstellationPanel()
        panel.onCancel = { [weak self] in self?.dismiss() }
        let view = ConstellationOverlayView(
            model: model,
            state: state,
            onClose: { [weak self] in self?.dismiss() },
            onExplore: { [weak self] node in
                guard let self else { return }
                self.dismiss()
                self.onExplore?(node)
            }
        )
        let size = liveActivityActiveScreen()?.frame.size ?? NSSize(width: 1440, height: 900)
        installHostingView(view, in: panel, width: size.width, height: size.height)
        self.panel = panel
        return panel
    }

    private func position(_ panel: ConstellationPanel) {
        guard let screen = liveActivityActiveScreen() else { return }
        panel.setFrame(screen.frame, display: false)
    }
}

/// Full-screen, borderless, becomes key on demand (for Esc handling + button clicks) but never
/// activates the whole app. Sits just above the other status-bar-level surfaces so it's clearly on
/// top when summoned.
final class ConstellationPanel: NSPanel {
    var onCancel: (() -> Void)?

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    init() {
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
            styleMask: [.nonactivatingPanel, .borderless],
            backing: .buffered,
            defer: false
        )
        isFloatingPanel = true
        level = NSWindow.Level(rawValue: NSWindow.Level.statusBar.rawValue + 1)
        backgroundColor = .clear
        isOpaque = false
        hasShadow = false
        hidesOnDeactivate = false
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
    }

    // Esc → dismiss.
    override func cancelOperation(_ sender: Any?) {
        onCancel?()
    }
}

private final class ConstellationOverlayModel: ObservableObject {
    @Published var visible = false
}

private struct ConstellationOverlayView: View {
    @ObservedObject var model: ConstellationOverlayModel
    @ObservedObject var state: AppState
    let onClose: () -> Void
    let onExplore: (GraphNode) -> Void

    var body: some View {
        ZStack {
            // Dimmed backdrop — tap anywhere outside the card to dismiss.
            Color.black
                .opacity(model.visible ? 0.30 : 0)
                .ignoresSafeArea()
                .contentShape(Rectangle())
                .onTapGesture { onClose() }

            if model.visible {
                card
                    .frame(maxWidth: 900, maxHeight: 720)
                    .padding(40)
                    .transition(
                        .asymmetric(
                            insertion: .move(edge: .bottom).combined(with: .opacity),
                            removal: .move(edge: .bottom).combined(with: .opacity)
                        )
                    )
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .animation(.spring(response: 0.42, dampingFraction: 0.85), value: model.visible)
    }

    private var card: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Your Constellation")
                        .font(CortexDesign.Typography.title)
                        .foregroundColor(CortexDesign.ink)
                    Text("The people, projects and topics Cortex has connected — tap a point to explore.")
                        .font(CortexDesign.Typography.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                }
                Spacer(minLength: CortexDesign.Space.md)
                Button {
                    onClose()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 22))
                        .foregroundColor(CortexDesign.inkFaint)
                }
                .buttonStyle(.plain)
                .keyboardShortcut(.cancelAction)
                .help("Close (Esc)")
            }

            // The same interactive map as Home, shown large, with node drill wired to Ask.
            MemoryMapView(state: state, canvasHeight: 520, onExplore: onExplore)
        }
        .padding(CortexDesign.Space.lg)
        .background(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.lg, style: .continuous)
                .fill(CortexDesign.appBackground)
                .shadow(color: Color.black.opacity(0.28), radius: 40, x: 0, y: 18)
        )
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.lg, style: .continuous)
                .stroke(CortexDesign.hairline, lineWidth: 1)
        )
    }
}
