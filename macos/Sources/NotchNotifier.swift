import SwiftUI
import AppKit

/// Visual voice of a notch notification. Each style carries its own accent + glyph so a glance
/// tells you what happened without reading: moss + sparkle for a new memory, wax-red + a saved
/// check for a capture, ink for neutral info.
enum NotchStyle {
    case learned
    case captured
    case info
}

/// A Dynamic-Island-style floating notification pinned top-center of the active screen, just below
/// the notch / menu bar. A single reused borderless, non-activating `NSPanel` hosts a SwiftUI pill
/// that springs in, holds ~2.2s, then contracts and fades out. Re-showing while visible replaces
/// the content smoothly rather than stacking panels.
///
/// All API is `@MainActor`; callers must invoke on the main actor.
@MainActor
final class NotchNotifier {
    static let shared = NotchNotifier()

    /// How long the pill stays fully visible before it begins to retract.
    private let holdDuration: TimeInterval = 2.2

    private var panel: NotchPanel?
    private let model = NotchContentModel()
    private var dismissWorkItem: DispatchWorkItem?

    private init() {}

    /// Present (or replace) a notch notification. Safe to call repeatedly; a single panel is reused
    /// and content is swapped with animation when a new call arrives while one is on screen.
    func show(title: String, subtitle: String?, style: NotchStyle) {
        let panel = ensurePanel()
        position(panel)

        // Swap content first so an in-flight pill morphs to the new message rather than flickering.
        model.apply(title: title, subtitle: subtitle, style: style)

        if !panel.isVisible {
            panel.alphaValue = 1
            panel.orderFrontRegardless()
        }

        withAnimation(.spring(response: 0.42, dampingFraction: 0.72)) {
            model.visible = true
        }

        scheduleDismiss()
    }

    // MARK: - Lifecycle

    private func scheduleDismiss() {
        dismissWorkItem?.cancel()
        let work = DispatchWorkItem { [weak self] in
            self?.dismiss()
        }
        dismissWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + holdDuration, execute: work)
    }

    private func dismiss() {
        withAnimation(.spring(response: 0.34, dampingFraction: 0.9)) {
            model.visible = false
        }
        // Order the panel out only after the contract/fade transition has finished so it doesn't
        // vanish mid-animation.
        let fadeOut = DispatchWorkItem { [weak self] in
            guard let self, !self.model.visible else { return }
            self.panel?.orderOut(nil)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4, execute: fadeOut)
    }

    // MARK: - Panel construction

    private func ensurePanel() -> NotchPanel {
        if let panel { return panel }
        let hosting = NSHostingView(rootView: NotchPillView(model: model))
        // Fill the panel with an explicit frame + autoresizing. (Setting
        // translatesAutoresizingMaskIntoConstraints=false with NO constraints left the hosting
        // view at a zero-size frame, so the panel appeared but rendered nothing — the notch pill
        // was invisible.)
        hosting.frame = NSRect(x: 0, y: 0, width: Self.panelWidth, height: Self.panelHeight)
        hosting.autoresizingMask = [.width, .height]

        let panel = NotchPanel(
            contentRect: NSRect(x: 0, y: 0, width: Self.panelWidth, height: Self.panelHeight),
            styleMask: [.nonactivatingPanel, .borderless],
            backing: .buffered,
            defer: false
        )
        panel.isFloatingPanel = true
        panel.level = .statusBar
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = false // the SwiftUI pill draws its own soft shadow
        panel.hidesOnDeactivate = false
        panel.isMovableByWindowBackground = false
        panel.ignoresMouseEvents = true // info/learned/captured are non-interactive
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        panel.contentView = hosting

        self.panel = panel
        return panel
    }

    /// Fixed panel canvas the pill floats within. Kept generous so the widest subtitle + the spring
    /// overshoot never clip; the pill itself sizes to content and is centered inside this canvas.
    private static let panelWidth: CGFloat = 460
    private static let panelHeight: CGFloat = 96

    /// Center the panel horizontally on the active screen and pin it just under the notch/menu bar.
    private func position(_ panel: NotchPanel) {
        guard let screen = activeScreen() else { return }
        let visible = screen.visibleFrame
        let full = screen.frame

        let x = full.midX - Self.panelWidth / 2

        // Distance from the top of the physical screen down to where usable content starts. On
        // notched Macs `safeAreaInsets.top` covers the notch; otherwise it's the menu-bar height
        // implied by the gap between the full frame and the visible frame.
        let menuBarGap = full.maxY - visible.maxY
        var topInset = menuBarGap
        if #available(macOS 12.0, *) {
            topInset = max(topInset, screen.safeAreaInsets.top)
        }

        // Sit the panel's top edge a hair below that inset so the pill tucks right under the notch.
        let gap: CGFloat = 4
        let y = full.maxY - topInset - Self.panelHeight - gap

        panel.setFrame(NSRect(x: x, y: y, width: Self.panelWidth, height: Self.panelHeight), display: false)
    }

    /// The screen that owns the menu bar / notch under the mouse-focused space, falling back to the
    /// primary screen.
    private func activeScreen() -> NSScreen? {
        let mouse = NSEvent.mouseLocation
        return NSScreen.screens.first { NSMouseInRect(mouse, $0.frame, false) } ?? NSScreen.main
    }
}

// MARK: - Panel subclass

/// A borderless panel must opt into key/main status explicitly, but we want the opposite: it should
/// never steal focus. Overriding these to `false` keeps the frontmost app active while the pill shows.
private final class NotchPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

// MARK: - SwiftUI content

/// Observable backing store for the reused pill so a single `NSHostingView` can re-render new
/// content and drive the show/hide animation without being torn down.
private final class NotchContentModel: ObservableObject {
    @Published var title: String = ""
    @Published var subtitle: String?
    @Published var style: NotchStyle = .info
    @Published var visible: Bool = false

    func apply(title: String, subtitle: String?, style: NotchStyle) {
        self.title = title
        self.subtitle = subtitle
        self.style = style
    }
}

private struct NotchPillView: View {
    @ObservedObject var model: NotchContentModel

    private var accent: Color {
        switch model.style {
        case .learned: return CortexDesign.sealMoss
        case .captured: return CortexDesign.accent
        case .info: return CortexDesign.ink
        }
    }

    private var glyph: String {
        switch model.style {
        case .learned: return "leaf.fill"
        case .captured: return "checkmark.seal.fill"
        case .info: return "info.circle.fill"
        }
    }

    var body: some View {
        VStack {
            if model.visible {
                pill
                    .transition(
                        .asymmetric(
                            insertion: .scale(scale: 0.86, anchor: .top)
                                .combined(with: .opacity)
                                .combined(with: .move(edge: .top)),
                            removal: .scale(scale: 0.92, anchor: .top)
                                .combined(with: .opacity)
                        )
                    )
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .allowsHitTesting(false)
    }

    private var pill: some View {
        HStack(spacing: CortexDesign.Space.sm) {
            ZStack {
                Circle()
                    .fill(accent.opacity(0.14))
                    .frame(width: 30, height: 30)
                Image(systemName: glyph)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(accent)
            }

            VStack(alignment: .leading, spacing: 1) {
                Text(model.title)
                    .font(CortexDesign.Typography.body.weight(.semibold))
                    .foregroundColor(CortexDesign.ink)
                    .lineLimit(1)
                if let subtitle = model.subtitle, !subtitle.isEmpty {
                    Text(subtitle)
                        .font(CortexDesign.Typography.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .lineLimit(1)
                }
            }
            .fixedSize(horizontal: true, vertical: false)
        }
        // Inset content past the Capsule's end radius (~23pt for this ~46pt-tall pill) so the icon
        // and title never spill into the rounded ends (same class of fix as the bottom Learning HUD).
        .padding(.leading, CortexDesign.Space.md)
        .padding(.trailing, CortexDesign.Space.lg)
        .padding(.vertical, 8)
        .background(
            Capsule(style: .continuous)
                .fill(CortexDesign.panelBackground)
                .overlay(
                    Capsule(style: .continuous)
                        .strokeBorder(accent.opacity(0.22), lineWidth: 1)
                )
                .shadow(color: Color.black.opacity(0.18), radius: 16, x: 0, y: 6)
        )
    }
}
