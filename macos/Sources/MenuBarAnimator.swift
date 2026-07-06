import AppKit

/// A snapshot of the app state the menu-bar icon reflects. Provided by the AppDelegate each tick.
struct MenuBarSnapshot: Equatable {
    /// Something is in flight (a real source sync OR any busy work) — the icon spins.
    var syncing: Bool
    /// Specifically a real source sync is running. Its active→inactive edge triggers the
    /// "sync complete" checkmark flourish (a plain busy blip does not, to avoid flicker).
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
/// gently pulsing review count when memory is waiting, and a brief checkmark when a sync finishes.
///
/// Design notes:
/// - A single main-actor `Task` loop polls the snapshot. Idle wakes only ~twice a second and shows a
///   static glyph; the fast frame cadence only runs while there is motion (spin / pulse / checkmark),
///   so idle energy cost stays near zero.
/// - Every glyph is baked at its NATURAL size, centered in one consistent canvas, as a template
///   image — so nothing stretches, clips, or jumps size between states, and it tints correctly on
///   light/dark menu bars.
/// - A minimum spin duration keeps very short syncs visible (otherwise the spinner would flash for a
///   single frame and the user would never see it).
/// macOS 13 compatible (no symbolEffect / phaseAnimator).
@MainActor
final class MenuBarAnimator {
    private weak var statusItem: NSStatusItem?
    private let snapshotProvider: @MainActor () -> MenuBarSnapshot

    private var loopTask: Task<Void, Never>?
    private var currentVisual: MenuBarVisual = .idle
    private var frameIndex = 0
    private var pulseFrame = 0

    // Edge/hold state.
    private var lastRealSyncActive = false
    private var pendingSuccess = false
    private var successFramesRemaining = 0
    private var spinHoldRemaining = 0

    private let frameInterval: TimeInterval = 0.05   // ~20fps spin
    private let pulseInterval: TimeInterval = 0.08   // ~12fps pulse (attention)
    private let idleInterval: TimeInterval = 0.45    // slow heartbeat when nothing is moving
    private let minSpinFrames = 16                   // ~0.8s minimum visible spin
    private let successFrames = 28                   // ~1.4s checkmark

    // Pre-baked assets, built lazily once — all at the same canvas size.
    private static let glyphPointSize: CGFloat = 15
    private lazy var spinnerFrames: [NSImage] = (0..<24).compactMap {
        Self.bakeSymbol("arrow.triangle.2.circlepath", pointSize: 14,
                        rotationDegrees: CGFloat($0) / 24.0 * 360.0)
    }
    private lazy var idleImage: NSImage? = Self.bakeSymbol("brain.head.profile", pointSize: Self.glyphPointSize)
    private lazy var attentionImage: NSImage? = Self.bakeSymbol("tray.full.fill", pointSize: 14)
    private lazy var successImage: NSImage? = Self.bakeSymbol("checkmark.circle.fill", pointSize: Self.glyphPointSize)

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
        let snap = snapshotProvider()

        // Real-sync completion edge → queue the checkmark (played after any minimum-spin hold).
        if lastRealSyncActive && !snap.realSyncActive {
            pendingSuccess = true
        }
        lastRealSyncActive = snap.realSyncActive

        // Minimum spin: once spinning, keep spinning for at least minSpinFrames so brief work is seen.
        if snap.syncing {
            spinHoldRemaining = minSpinFrames
        } else if spinHoldRemaining > 0 {
            spinHoldRemaining -= 1
        }
        let showSpin = snap.syncing || spinHoldRemaining > 0

        let visual: MenuBarVisual
        if showSpin {
            visual = .syncing
        } else if pendingSuccess || successFramesRemaining > 0 {
            if pendingSuccess {
                pendingSuccess = false
                successFramesRemaining = successFrames
            }
            successFramesRemaining -= 1
            visual = .success
        } else if snap.pendingCount > 0 {
            visual = .attention(snap.pendingCount)
        } else {
            visual = .idle
        }

        if visual != currentVisual {
            render(visual)
        } else {
            switch visual {
            case .syncing: advanceSpinner()
            case .attention: advancePulse()
            default: break
            }
        }

        switch visual {
        case .syncing, .success: return frameInterval
        case .attention: return pulseInterval
        case .idle: return idleInterval
        }
    }

    private func advanceSpinner() {
        guard let button = statusItem?.button, !spinnerFrames.isEmpty else { return }
        frameIndex &+= 1
        button.image = spinnerFrames[frameIndex % spinnerFrames.count]
    }

    private func advancePulse() {
        guard let button = statusItem?.button else { return }
        pulseFrame &+= 1
        let t = Double(pulseFrame) * pulseInterval * (2 * Double.pi / 1.9)  // ~1.9s period
        button.alphaValue = 0.68 + 0.32 * (0.5 + 0.5 * sin(t))
    }

    private func render(_ visual: MenuBarVisual) {
        currentVisual = visual
        frameIndex = 0
        pulseFrame = 0
        guard let button = statusItem?.button else { return }
        button.alphaValue = 1.0
        switch visual {
        case .idle:
            button.imagePosition = .imageOnly
            button.title = ""
            button.image = idleImage
            button.toolTip = "Cortex — click to ask your memory (⌃⌥Space)"
        case .syncing:
            button.imagePosition = .imageOnly
            button.title = ""
            button.image = spinnerFrames.first ?? idleImage
            button.toolTip = "Cortex — syncing your memory…"
        case .attention(let count):
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

    /// Bake an SF Symbol into a template image at a CONSISTENT canvas size, drawn at its natural size
    /// (crisp, never stretched) and centered (never clipped), optionally rotated. Marking it a
    /// template makes the menu bar tint it correctly on light/dark bars.
    private static func bakeSymbol(_ name: String, pointSize: CGFloat, rotationDegrees: CGFloat = 0) -> NSImage? {
        guard let base = NSImage(systemSymbolName: name, accessibilityDescription: "Cortex") else { return nil }
        let config = NSImage.SymbolConfiguration(pointSize: pointSize, weight: .semibold)
        let symbol = base.withSymbolConfiguration(config) ?? base
        let symbolSize = symbol.size
        // One canvas big enough that a rotated glyph never clips at the corners.
        let side = ceil(max(symbolSize.width, symbolSize.height) * 1.5)
        let canvas = NSSize(width: side, height: side)
        let image = NSImage(size: canvas, flipped: false) { rect in
            guard let ctx = NSGraphicsContext.current?.cgContext else { return false }
            if rotationDegrees != 0 {
                ctx.translateBy(x: rect.midX, y: rect.midY)
                ctx.rotate(by: -rotationDegrees * .pi / 180.0)
                ctx.translateBy(x: -rect.midX, y: -rect.midY)
            }
            let drawRect = NSRect(
                x: rect.midX - symbolSize.width / 2,
                y: rect.midY - symbolSize.height / 2,
                width: symbolSize.width,
                height: symbolSize.height
            )
            symbol.draw(in: drawRect, from: .zero, operation: .sourceOver, fraction: 1.0)
            return true
        }
        image.isTemplate = true
        return image
    }
}
