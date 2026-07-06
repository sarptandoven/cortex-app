import AppKit

/// A snapshot of the app state the menu-bar icon reflects. Provided by the AppDelegate each tick.
struct MenuBarSnapshot: Equatable {
    /// Something is in flight (a real source sync OR any busy work) — the icon spins.
    var syncing: Bool
    /// Specifically a real source sync is running. Its active→inactive edge triggers the
    /// "sync complete" checkmark flourish (a plain `busy` blip does not, to avoid flicker).
    var realSyncActive: Bool
    /// How many items are waiting in Review — shown as a count next to the icon.
    var pendingCount: Int
}

/// What the icon is currently rendering.
private enum MenuBarVisual: Equatable {
    case idle
    case syncing
    case attention(Int)
    case success
}

/// Drives the menu-bar `NSStatusItem` icon: a calm brain at rest, a spinner while Cortex syncs, a
/// review count when memory is waiting, and a brief checkmark when a sync finishes.
///
/// A single main-actor `Task` loop polls the snapshot. At rest it wakes only ~twice a second and
/// shows a static glyph (standard menu-bar etiquette — no motion when idle); motion is reserved for
/// the transient sync spinner and the completion checkmark, so idle energy cost stays near zero.
/// macOS 13 compatible (no `symbolEffect`); spinner frames are pre-baked rotated template images so
/// the glyph tints correctly on light/dark menu bars.
@MainActor
final class MenuBarAnimator {
    private weak var statusItem: NSStatusItem?
    private let snapshotProvider: @MainActor () -> MenuBarSnapshot

    private var loopTask: Task<Void, Never>?
    private var currentVisual: MenuBarVisual = .idle
    private var frameIndex = 0

    // Edge detection for the completion flourish.
    private var lastRealSyncActive = false
    private var successFramesRemaining = 0

    // Pre-baked assets, built lazily once.
    private lazy var spinnerFrames: [NSImage] = Self.makeRotationFrames(
        symbol: "arrow.triangle.2.circlepath", frameCount: 24, pointSize: 15
    )
    private lazy var idleImage: NSImage? = Self.makeSymbol("brain.head.profile", pointSize: 15)
    private lazy var attentionImage: NSImage? = Self.makeSymbol("tray.full.fill", pointSize: 14)
    private lazy var successImage: NSImage? = Self.makeSymbol("checkmark.circle.fill", pointSize: 15)

    private let frameInterval: TimeInterval = 0.05   // ~20fps spin (~1.2s / revolution)
    private let idleInterval: TimeInterval = 0.45    // slow heartbeat: notice state changes cheaply
    private let successDuration: TimeInterval = 1.6   // how long the checkmark lingers

    init(statusItem: NSStatusItem, snapshotProvider: @escaping @MainActor () -> MenuBarSnapshot) {
        self.statusItem = statusItem
        self.snapshotProvider = snapshotProvider
    }

    func start() {
        render(.idle)
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
        let snapshot = snapshotProvider()

        // A real sync just finished → play the completion checkmark.
        if lastRealSyncActive && !snapshot.realSyncActive && !snapshot.syncing {
            successFramesRemaining = Int((successDuration / frameInterval).rounded())
        }
        lastRealSyncActive = snapshot.realSyncActive

        // Resolve what to show, in priority order.
        let visual: MenuBarVisual
        if snapshot.syncing {
            successFramesRemaining = 0            // an in-flight sync outranks a stale checkmark
            visual = .syncing
        } else if successFramesRemaining > 0 {
            successFramesRemaining -= 1
            visual = .success
        } else if snapshot.pendingCount > 0 {
            visual = .attention(snapshot.pendingCount)
        } else {
            visual = .idle
        }

        if visual != currentVisual {
            render(visual)
        } else if case .syncing = visual {
            advanceSpinner()
        }

        switch visual {
        case .syncing, .success:
            return frameInterval
        case .idle, .attention:
            return idleInterval
        }
    }

    private func advanceSpinner() {
        guard let button = statusItem?.button, !spinnerFrames.isEmpty else { return }
        frameIndex &+= 1
        button.image = spinnerFrames[frameIndex % spinnerFrames.count]
    }

    private func render(_ visual: MenuBarVisual) {
        currentVisual = visual
        frameIndex = 0
        guard let button = statusItem?.button else { return }
        button.alphaValue = 1.0
        switch visual {
        case .idle:
            button.imagePosition = .imageOnly
            button.title = ""
            button.image = idleImage
            button.toolTip = "Cortex"
        case .syncing:
            button.imagePosition = .imageOnly
            button.title = ""
            button.image = spinnerFrames.first ?? idleImage
            button.toolTip = "Cortex — syncing your memory…"
        case .attention(let count):
            // Icon + a live count of items waiting in Review. Title text tints itself to the menu
            // bar automatically, so this stays legible on light and dark menu bars.
            button.image = attentionImage ?? idleImage
            button.title = " \(count > 99 ? "99+" : String(count))"
            button.imagePosition = .imageLeading
            button.toolTip = "Cortex — \(count) item\(count == 1 ? "" : "s") waiting for review"
        case .success:
            button.imagePosition = .imageOnly
            button.title = ""
            button.image = successImage ?? idleImage
            button.toolTip = "Cortex — sync complete"
        }
        // Safety net: if symbol rendering ever fails, fall back to a visible text title.
        if button.image == nil && button.title.isEmpty {
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
