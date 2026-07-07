import AppKit

/// A snapshot of the app state the menu-bar icon reflects. Provided by the AppDelegate each tick.
struct MenuBarSnapshot: Equatable {
    /// Something is in flight (sync/import/backup/launch or busy work) — the icon spins. This is a
    /// LEVEL signal (true for the whole duration of the operation), so sampling can never miss it.
    var syncing: Bool
    /// When the last real sync/import completed. A NEW timestamp triggers the checkmark flourish —
    /// deterministic, instead of edge-detecting a polled transient that fast syncs slipped past.
    var syncCompletedAt: Date?
    /// When Cortex last learned something new (sync/sample/import/quick-capture added memories). A NEW
    /// timestamp triggers the moss "learned" sparkle flourish. Deterministic like `syncCompletedAt`,
    /// so a fast burst of learning is never missed by the sampling loop.
    /// Fed by AppState's `lastLearnedAt: Date?` (agent 1).
    var learnedAt: Date?
    /// When the last quick-capture (highlight/screenshot → memory) landed. A NEW timestamp triggers the
    /// "captured" flourish, a variant distinct from the plain learned sparkle. Optional — when AppState
    /// has no separate capture signal, leave nil and the learned flourish covers both.
    /// Fed by AppState's `lastCapturedAt: Date?` (agent 1, if exposed).
    var capturedAt: Date?
    /// How many items are waiting in Review — shown as a count next to the icon.
    var pendingCount: Int
}

/// What the icon is currently rendering.
private enum MenuBarVisual: Equatable {
    case idle
    case syncing
    case attention(Int)
    case success
    /// Cortex learned something new — a brief moss-tinted sparkle pulse.
    case learned
    /// A quick-capture landed — a distinct sparkle variant (pin/burst) in the same moss tint.
    case captured
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

    /// While the Cortex Spotlight popover is open it is anchored to the status-item BUTTON. Mutating
    /// that button (image, title/size, alpha) every frame repositions and destabilizes the transient
    /// popover — it flickers and swallows clicks. So the AppDelegate pauses the animator for the whole
    /// time the popover is shown; the icon simply holds its current frame until the popover closes.
    var paused: Bool = false

    private var loopTask: Task<Void, Never>?
    private var currentVisual: MenuBarVisual = .idle
    private var frameIndex = 0
    private var pulseFrame = 0

    // Completion/hold state.
    private var lastSeenCompletion: Date?
    private var pendingSuccess = false
    private var successFramesRemaining = 0
    private var spinHoldRemaining = 0

    // Learned / captured flourish state — timestamp-keyed like the checkmark so fast events aren't
    // missed, and framed with a fixed count so the sparkle always plays its full arc once queued.
    private var lastSeenLearned: Date?
    private var lastSeenCaptured: Date?
    private var pendingLearned = false
    private var pendingCaptured = false
    private var flourishFramesRemaining = 0
    private var flourishFrame = 0

    private let frameInterval: TimeInterval = 0.05   // ~20fps spin
    private let pulseInterval: TimeInterval = 0.08   // ~12fps pulse (attention)
    private let idleInterval: TimeInterval = 0.45    // slow heartbeat when nothing is moving
    private let minSpinFrames = 16                   // ~0.8s minimum visible spin
    private let successFrames = 28                   // ~1.4s checkmark
    private let flourishFrames = 30                  // ~1.5s learned/captured sparkle

    // Pre-baked assets, built lazily once — all at the same canvas size.
    private static let glyphPointSize: CGFloat = 15
    private lazy var spinnerFrames: [NSImage] = (0..<24).compactMap {
        Self.bakeSymbol("arrow.triangle.2.circlepath", pointSize: 14,
                        rotationDegrees: CGFloat($0) / 24.0 * 360.0)
    }
    private lazy var idleImage: NSImage? = Self.bakeSymbol("brain.head.profile", pointSize: Self.glyphPointSize)
    private lazy var attentionImage: NSImage? = Self.bakeSymbol("tray.full.fill", pointSize: 14)
    private lazy var successImage: NSImage? = Self.bakeSymbol("checkmark.circle.fill", pointSize: Self.glyphPointSize)

    /// "The Archive" moss (#4F6B45) — the learned/captured flourishes tint toward it (distinct from the
    /// template checkmark) while staying legible on both light and dark menu bars.
    private static let mossTint = NSColor(calibratedRed: 0x4F/255.0, green: 0x6B/255.0, blue: 0x45/255.0, alpha: 1.0)

    /// A sparkle rising into view: baked once per scale step so the pulse is smooth. Moss-tinted.
    private lazy var learnedFrames: [NSImage] = Self.bakeScalePulse("sparkles", tint: Self.mossTint)
    /// The "captured" variant — a distinct glyph (a filled sparkle / pin burst) in the same tint, so a
    /// quick-capture reads differently from ambient learning. Falls back to the learned sparkle.
    private lazy var capturedFrames: [NSImage] = {
        let frames = Self.bakeScalePulse("sparkle", tint: Self.mossTint)
        return frames.isEmpty ? learnedFrames : frames
    }()

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
        // Frozen while the popover is open so the anchored popover stays stable (no button mutation).
        if paused { return idleInterval }
        let snap = snapshotProvider()

        // A NEW (and recent) completion stamp → queue the checkmark (played after any spin hold).
        // Keyed to the timestamp itself, so it can never be missed or double-played.
        if let completed = snap.syncCompletedAt,
           completed != lastSeenCompletion {
            lastSeenCompletion = completed
            if completed.timeIntervalSinceNow > -5 {
                pendingSuccess = true
            }
        }

        // A NEW (and recent) capture stamp → queue the "captured" sparkle. Checked before "learned"
        // so a quick-capture (which usually also bumps learnedAt) shows its distinct variant. Both are
        // timestamp-keyed, so a rapid burst of events is never missed or replayed.
        if let captured = snap.capturedAt,
           captured != lastSeenCaptured {
            lastSeenCaptured = captured
            if captured.timeIntervalSinceNow > -5 {
                pendingCaptured = true
                pendingLearned = false   // capture supersedes the plain learned flourish for this event
            }
        }
        // A NEW (and recent) learned stamp → queue the "learned" sparkle, unless a capture for the same
        // moment already claimed the flourish.
        if let learned = snap.learnedAt,
           learned != lastSeenLearned {
            lastSeenLearned = learned
            if learned.timeIntervalSinceNow > -5 && !pendingCaptured {
                pendingLearned = true
            }
        }

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
        } else if pendingCaptured || pendingLearned || flourishFramesRemaining > 0 {
            // Learned / captured sparkle. If both are queued the capture variant wins (queued first).
            if pendingCaptured || pendingLearned {
                let asCaptured = pendingCaptured
                pendingCaptured = false
                pendingLearned = false
                flourishFramesRemaining = flourishFrames
                // Choose the variant now; the render switch reads `currentVisual` to pick the frames.
                render(asCaptured ? .captured : .learned)
            }
            flourishFramesRemaining -= 1
            // Keep whichever variant we started rendering — don't let a stray learned stamp mid-flight
            // swap the glyph set under us.
            visual = (currentVisual == .captured) ? .captured : .learned
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
            case .learned, .captured: advanceFlourish()
            default: break
            }
        }

        switch visual {
        case .syncing, .success, .learned, .captured: return frameInterval
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

    /// Advance the learned/captured sparkle: step through the baked scale-pulse frames and fade the
    /// tail so it settles gently rather than snapping off.
    private func advanceFlourish() {
        guard let button = statusItem?.button else { return }
        let frames = (currentVisual == .captured) ? capturedFrames : learnedFrames
        guard !frames.isEmpty else { return }
        flourishFrame &+= 1
        button.image = frames[flourishFrame % frames.count]
        // Gentle fade over the last third of the flourish.
        let tailStart = flourishFrames / 3
        if flourishFramesRemaining < tailStart, tailStart > 0 {
            button.alphaValue = max(0.35, Double(flourishFramesRemaining) / Double(tailStart))
        }
    }

    private func render(_ visual: MenuBarVisual) {
        currentVisual = visual
        frameIndex = 0
        pulseFrame = 0
        flourishFrame = 0
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
        case .learned:
            button.imagePosition = .imageOnly
            button.title = ""
            button.image = learnedFrames.first ?? idleImage
            button.toolTip = "Cortex — learned something new"
        case .captured:
            button.imagePosition = .imageOnly
            button.title = ""
            button.image = capturedFrames.first ?? idleImage
            button.toolTip = "Cortex — captured to memory"
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

    /// Bake a short "pop + settle" scale pulse of a symbol, tinted (moss) so the learned/captured
    /// flourish reads as its own moment — distinct from the template checkmark — and stays visible on
    /// both light and dark menu bars. Frames share ONE canvas size (the largest scale) so the glyph
    /// never changes footprint or clips as it grows. Returns [] if the symbol can't be loaded, so the
    /// caller can fall back gracefully.
    private static func bakeScalePulse(_ name: String, tint: NSColor) -> [NSImage] {
        // Scale envelope: rise past 1.0 for a little pop, then ease back to a steady display size.
        let scales: [CGFloat] = [0.55, 0.78, 1.0, 1.14, 1.06, 1.0, 1.0, 1.0]
        let maxScale = scales.max() ?? 1.0
        return scales.compactMap { scale in
            bakeTintedSymbol(name, pointSize: glyphPointSize, tint: tint,
                             scale: scale, canvasScale: maxScale)
        }
    }

    /// Bake one tinted (non-template) frame of a symbol at `scale`, centered in a canvas sized for
    /// `canvasScale` so every frame in a pulse shares a footprint. The symbol's alpha is used as a mask
    /// through which the tint is painted, matching the crisp, centered, never-stretched look of the
    /// template glyphs.
    private static func bakeTintedSymbol(_ name: String, pointSize: CGFloat, tint: NSColor,
                                         scale: CGFloat, canvasScale: CGFloat) -> NSImage? {
        guard let base = NSImage(systemSymbolName: name, accessibilityDescription: "Cortex") else { return nil }
        let config = NSImage.SymbolConfiguration(pointSize: pointSize, weight: .semibold)
        let symbol = base.withSymbolConfiguration(config) ?? base
        let natural = symbol.size
        // Canvas holds the largest frame with headroom so nothing clips at any scale.
        let side = ceil(max(natural.width, natural.height) * canvasScale * 1.2)
        let canvas = NSSize(width: side, height: side)
        let drawSize = NSSize(width: natural.width * scale, height: natural.height * scale)
        let image = NSImage(size: canvas, flipped: false) { rect in
            let drawRect = NSRect(
                x: rect.midX - drawSize.width / 2,
                y: rect.midY - drawSize.height / 2,
                width: drawSize.width,
                height: drawSize.height
            )
            // Paint the symbol, then tint it via sourceAtop so only the glyph's pixels take the color.
            symbol.draw(in: drawRect, from: .zero, operation: .sourceOver, fraction: 1.0)
            tint.setFill()
            drawRect.fill(using: .sourceAtop)
            return true
        }
        // Tinted, not a template — the menu bar must show the moss color, not re-tint it monochrome.
        image.isTemplate = false
        return image
    }
}
