import AppKit

/// What the menu-bar icon should be conveying right now. Derived from AppState by the
/// AppDelegate; the animator only knows about these three visual modes.
enum MenuBarMode: Equatable {
    /// Nothing in flight — a calm, static brain glyph (standard menu-bar etiquette: no motion at rest).
    case idle
    /// A sync/import/backup is running — the icon spins so the user can see work is happening.
    case syncing
    /// Reviewable memory is waiting — the icon breathes slowly to gently pull the eye.
    case attention
}

/// Drives the menu-bar `NSStatusItem` icon with lightweight, power-friendly animation.
///
/// A single main-actor `Task` loop polls the desired mode. At rest it wakes only a couple of times a
/// second and shows a plain template glyph (standard menu-bar etiquette: no motion when idle). While
/// a sync runs it advances a pre-baked spinner; while review is pending it slowly pulses the glyph.
/// Everything here is macOS 13 compatible (no `symbolEffect`), and frames are pre-rendered as
/// template images so the glyph tints correctly to the menu-bar appearance (light or dark bar).
@MainActor
final class MenuBarAnimator {
    private weak var statusItem: NSStatusItem?
    private let modeProvider: @MainActor () -> MenuBarMode

    private var loopTask: Task<Void, Never>?
    private var mode: MenuBarMode = .idle
    private var frameIndex = 0

    // Pre-baked assets, built lazily once.
    private lazy var spinnerFrames: [NSImage] = Self.makeRotationFrames(
        symbol: "arrow.triangle.2.circlepath", frameCount: 24, pointSize: 15
    )
    private lazy var idleImage: NSImage? = Self.makeSymbol("brain.head.profile", pointSize: 15)
    private lazy var attentionImage: NSImage? = Self.makeSymbol("tray.full.fill", pointSize: 14)

    private let frameInterval: TimeInterval = 0.05   // ~20fps spin (~1.2s / revolution)
    private let idleInterval: TimeInterval = 0.45    // slow heartbeat: notice mode changes cheaply

    init(statusItem: NSStatusItem, modeProvider: @escaping @MainActor () -> MenuBarMode) {
        self.statusItem = statusItem
        self.modeProvider = modeProvider
    }

    func start() {
        applyMode(.idle)
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

    /// Advance one animation step and return how long to sleep before the next one.
    private func tick() -> TimeInterval {
        let desired = modeProvider()
        if desired != mode { applyMode(desired) }
        guard let button = statusItem?.button else { return idleInterval }
        switch mode {
        case .idle:
            return idleInterval
        case .syncing:
            frameIndex &+= 1
            if !spinnerFrames.isEmpty {
                button.image = spinnerFrames[frameIndex % spinnerFrames.count]
            }
            return frameInterval
        case .attention:
            frameIndex &+= 1
            // Slow breathing pulse (~1.8s period) between 0.55 and 1.0 opacity.
            let phase = Double(frameIndex) * frameInterval * (2 * Double.pi / 1.8)
            button.alphaValue = 0.55 + 0.45 * (0.5 + 0.5 * sin(phase))
            return frameInterval
        }
    }

    private func applyMode(_ newMode: MenuBarMode) {
        mode = newMode
        frameIndex = 0
        guard let button = statusItem?.button else { return }
        button.imagePosition = .imageOnly
        button.title = ""
        button.alphaValue = 1.0
        switch newMode {
        case .idle:
            button.image = idleImage
            button.toolTip = "Cortex"
        case .syncing:
            button.image = spinnerFrames.first ?? idleImage
            button.toolTip = "Cortex — syncing your memory…"
        case .attention:
            button.image = attentionImage ?? idleImage
            button.toolTip = "Cortex — memory is waiting for review"
        }
        // Safety net: if symbol rendering ever fails, fall back to a visible text title so the
        // menu-bar item can never become invisible.
        if button.image == nil {
            button.imagePosition = .noImage
            button.title = "Cortex"
        }
    }

    // MARK: - Image baking

    /// A single template symbol image at a given point size, suitable for the menu bar.
    private static func makeSymbol(_ name: String, pointSize: CGFloat) -> NSImage? {
        guard let base = NSImage(systemSymbolName: name, accessibilityDescription: "Cortex") else { return nil }
        let config = NSImage.SymbolConfiguration(pointSize: pointSize, weight: .semibold)
        let image = base.withSymbolConfiguration(config) ?? base
        image.isTemplate = true
        return image
    }

    /// Pre-render `frameCount` rotated copies of a symbol into template images. Rotating a static set
    /// of frames (rather than animating a layer transform) sidesteps NSView anchor-point/flip quirks
    /// and guarantees the glyph stays crisp and correctly tinted in the menu bar.
    private static func makeRotationFrames(symbol: String, frameCount: Int, pointSize: CGFloat) -> [NSImage] {
        guard let base = makeSymbol(symbol, pointSize: pointSize) else { return [] }
        let side = pointSize + 6
        let canvas = NSSize(width: side, height: side)
        var frames: [NSImage] = []
        frames.reserveCapacity(frameCount)
        for i in 0..<frameCount {
            let degrees = CGFloat(i) / CGFloat(frameCount) * 360.0
            let frame = NSImage(size: canvas, flipped: false) { rect in
                guard let ctx = NSGraphicsContext.current?.cgContext else { return false }
                ctx.translateBy(x: rect.midX, y: rect.midY)
                ctx.rotate(by: -degrees * .pi / 180.0)
                ctx.translateBy(x: -rect.midX, y: -rect.midY)
                base.draw(in: rect, from: .zero, operation: .sourceOver, fraction: 1.0)
                return true
            }
            frame.isTemplate = true
            frames.append(frame)
        }
        return frames
    }
}
