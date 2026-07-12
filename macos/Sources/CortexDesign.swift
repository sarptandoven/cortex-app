import SwiftUI
import AppKit

/// Resolves to a light or dark value based on the current system appearance. The app is pinned to
/// light (Aqua) today, but the dark values are kept coherent so unpinning later needs no palette work.
private func cortexAdaptiveColor(light: NSColor, dark: NSColor) -> Color {
    Color(nsColor: NSColor(name: nil, dynamicProvider: { appearance in
        appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? dark : light
    }))
}

private func cortexHex(_ hex: UInt32, alpha: CGFloat = 1) -> NSColor {
    NSColor(
        srgbRed: CGFloat((hex >> 16) & 0xFF) / 255.0,
        green: CGFloat((hex >> 8) & 0xFF) / 255.0,
        blue: CGFloat(hex & 0xFF) / 255.0,
        alpha: alpha
    )
}

/// Physical-craft depth is procedural: the wax seal's sheen and rim are derived from the accent by
/// nudging luminance in sRGB, so they stay coherent across a light/dark repalette with no hand-tuned
/// second color. `amount > 0` lightens toward white, `< 0` darkens toward black.
private extension Color {
    func cortexAdjustBrightness(_ amount: CGFloat) -> Color {
        Color(nsColor: NSColor(name: nil, dynamicProvider: { appearance in
            let resolved = NSColor(self).usingColorSpace(.sRGB) ?? .clear
            var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
            _ = appearance // resolution happens via the parent dynamic color; kept for parity.
            resolved.getRed(&r, green: &g, blue: &b, alpha: &a)
            let mix: (CGFloat) -> CGFloat = { channel in
                amount >= 0
                    ? channel + (1 - channel) * amount
                    : channel * (1 + amount)
            }
            return NSColor(srgbRed: mix(r), green: mix(g), blue: mix(b), alpha: a)
        }))
    }

    /// Lighten toward white by `amount` (0…1).
    func cortexLightened(_ amount: CGFloat) -> Color { cortexAdjustBrightness(amount) }
    /// Darken toward black by `amount` (0…1).
    func cortexDarkened(_ amount: CGFloat) -> Color { cortexAdjustBrightness(-amount) }
}

/// "The Archive" — Cortex's visual identity. A personal archive you'd trust with your life's
/// marginalia: warm paper, iron-gall ink, sealing-wax red, index cards with margin rules, and
/// card-catalog metadata in tiny monospaced caps. Three type voices with strict roles (serif =
/// the archive's voice, SF = the app's working voice, mono = the catalog stamp). Guardrails are
/// contractual: restrained physical craft — procedural depth (two-layer shadows, edge-light,
/// seeded ±1.5% grain), letterpress embossed edges, one wax-seal moment per surface; never bitmap
/// textures; never on the live-activity surfaces. Gold is a fill (never small text), destructive
/// actions stay system red, serif/SF/mono typography and destructive-red are untouched, max
/// weight .semibold app-wide.
enum CortexDesign {
    // MARK: Palette — iron-gall ink on warm paper, one decisive wax-red accent.

    /// Warm paper — a desk blotter, not cream parchment (~4% warmer than neutral).
    static let appBackground = cortexAdaptiveColor(
        light: cortexHex(0xF7F4ED),
        dark: cortexHex(0x1C1A17)
    )
    /// Index-card white: one visible step brighter than the paper, SOLID so hairlines and
    /// shadows stay crisp (no alpha).
    static let panelBackground = cortexAdaptiveColor(
        light: cortexHex(0xFEFDFA),
        dark: cortexHex(0x262320)
    )
    static let cardBackground = cortexAdaptiveColor(
        light: cortexHex(0xFEFDFA),
        dark: cortexHex(0x262320)
    )
    /// Recessed wells (footer strip, quiet input grounds).
    static let quietBackground = cortexAdaptiveColor(
        light: cortexHex(0xF1EDE3),
        dark: cortexHex(0x211E1B)
    )

    /// Sealing-wax / library-stamp red — the decisive non-blue ink (6.9:1 on paper).
    static let accent = cortexAdaptiveColor(
        light: cortexHex(0x8C3A2B),
        dark: cortexHex(0xC96B57)
    )
    /// Wax red as a wash, for selected fills and quiet emphasis grounds.
    static let accentSoft = cortexAdaptiveColor(
        light: cortexHex(0x8C3A2B, alpha: 0.09),
        dark: cortexHex(0xC96B57, alpha: 0.18)
    )

    /// Iron-gall ink — warm near-black (13.6:1 on paper); pure #000 looks harsh on warm surfaces.
    static let ink = cortexAdaptiveColor(
        light: cortexHex(0x2B2620),
        dark: cortexHex(0xE8E3D9)
    )
    /// Secondary ink for 12–13pt supporting text (6.7:1, AA-verified solid — not an opacity).
    static let inkSecondary = cortexAdaptiveColor(
        light: cortexHex(0x5C554B),
        dark: cortexHex(0xB0A99D)
    )
    /// Faint ink (4.37:1) ONLY for ≥11pt-medium mono stamps, disabled states, and the mono footer.
    static let inkFaint = cortexAdaptiveColor(
        light: cortexHex(0x7A7166),
        dark: cortexHex(0x8C857A)
    )

    /// Marginalia gold — fills and rules ONLY (3.1:1 — never small text): the unreviewed spine,
    /// the review-count badge, the sync progress beam, capture confirmations.
    static let gold = cortexAdaptiveColor(
        light: cortexHex(0xB58121),
        dark: cortexHex(0xD3A04C)
    )
    static let goldSoft = cortexAdaptiveColor(
        light: cortexHex(0xB58121, alpha: 0.14),
        dark: cortexHex(0xD3A04C, alpha: 0.22)
    )

    /// Banker's-lamp moss — the privacy/health mark (LOCAL ONLY, healthy connections). 5.43:1;
    /// restricted to dots, badges, and ≥12pt medium text.
    static let sealMoss = cortexAdaptiveColor(
        light: cortexHex(0x4F6B45),
        dark: cortexHex(0x7E9A72)
    )

    static let softBorder = cortexAdaptiveColor(
        light: cortexHex(0x2B2620, alpha: 0.14),
        dark: cortexHex(0xE8E3D9, alpha: 0.16)
    )
    static let hairline = cortexAdaptiveColor(
        light: cortexHex(0x2B2620, alpha: 0.10),
        dark: cortexHex(0xE8E3D9, alpha: 0.10)
    )

    // MARK: Spacing — generous whitespace, consistent rhythm.
    enum Space {
        static let xs: CGFloat = 6
        static let sm: CGFloat = 10
        static let md: CGFloat = 16
        static let lg: CGFloat = 22
        static let xl: CGFloat = 32
    }

    // MARK: Radii — index cards, not iOS pills.
    enum Radius {
        static let sm: CGFloat = 6
        static let md: CGFloat = 8
        static let lg: CGFloat = 12
    }

    static let controlHeight: CGFloat = 40

    // MARK: Elevation — how far a surface floats off the paper desk.
    //
    // A formal scale so cards, sheets, and hover-lifts share one language of depth instead of
    // ad-hoc `.shadow` calls. Each level is a TWO-layer shadow: a soft ambient (the object's cast
    // shadow across the desk) plus a tight contact shadow (where it actually touches). This is the
    // procedural-depth half of "restrained physical craft" — real light, no bitmap texture.
    enum Elevation {
        case rest      // lying flat on the paper — the default card
        case raised    // lifted a little — hovered/interactive card
        case floating  // a sheet or popover sitting above the surface

        /// Soft ambient shadow (the wide, faint cast).
        var ambient: (color: Color, radius: CGFloat, y: CGFloat) {
            switch self {
            case .rest:     return (CortexDesign.ink.opacity(0.05), 10, 4)
            case .raised:   return (CortexDesign.ink.opacity(0.05), 14, 6)
            case .floating: return (CortexDesign.ink.opacity(0.08), 24, 12)
            }
        }

        /// Tight contact shadow (the crisp line where the card meets the desk).
        var contact: (color: Color, radius: CGFloat, y: CGFloat) {
            switch self {
            case .rest:     return (CortexDesign.ink.opacity(0.08), 2, 1)
            case .raised:   return (CortexDesign.ink.opacity(0.08), 2, 1)
            case .floating: return (CortexDesign.ink.opacity(0.10), 3, 2)
            }
        }
    }

    // MARK: Typography — three voices, strict roles.
    //
    // SERIF (New York) is the archive's voice: display, titles, memory/answer prose. Floor 13pt.
    // DEFAULT (SF) is the app's working voice: every control, label, description.
    // MONO (SF Mono) is the catalog stamp: provenance/telemetry only. Ceiling 12pt, never sentences.
    enum Typography {
        /// Screen/onboarding display titles.
        static func display(_ size: CGFloat = 26) -> Font {
            .system(size: size, weight: .semibold, design: .serif)
        }
        /// Section and card titles.
        static let title = Font.system(size: 16, weight: .semibold, design: .serif)
        /// Memory-card content and Ask answer prose (pair with .lineSpacing(3)).
        static func prose(_ size: CGFloat = 14) -> Font {
            .system(size: size, weight: .regular, design: .serif)
        }
        /// Big statistics — New York numerals are a feature (pair with .monospacedDigit()).
        static let stat = Font.system(size: 22, weight: .semibold, design: .serif)
        /// Working UI text.
        static let body = Font.system(size: 13, weight: .regular)
        static let caption = Font.system(size: 12, weight: .regular)
        /// The catalog stamp (uppercase the string, add .kerning(0.8)).
        static let stamp = Font.system(size: 11, weight: .medium, design: .monospaced)
        /// Keyboard hints and micro-telemetry.
        static let hint = Font.system(size: 10.5, weight: .medium, design: .monospaced)
    }
}

// MARK: - Motion — one small vocabulary of physically-plausible timings.
//
// Tokenized so every primitive animates on the SAME curves: a control never fights itself with two
// competing durations (the pre-overhaul hover/press stutter). Springs read as weight; the press
// "sets into the paper", the seal cools, numbers roll like a counter wheel.
enum CortexMotion {
    /// Hover in/out and press for buttons — one spring for both layers (kills the stutter).
    static let press = Animation.spring(response: 0.18, dampingFraction: 0.7)
    static let hover = Animation.spring(response: 0.18, dampingFraction: 0.7)
    /// The wax seal "sets into the paper" — sheen/edge-light drop on press.
    static let settle = Animation.easeOut(duration: 0.09)
    /// Card hover-lift — a touch slower so the shadow spread reads.
    static let lift = Animation.easeOut(duration: 0.16)
    /// Stat count-up / roll on value change.
    static let rollNumber = Animation.easeOut(duration: 0.55)
    /// Focus ring bloom on a field.
    static let focus = Animation.easeOut(duration: 0.14)
}

// MARK: - Physical-craft primitives (letterpress edges, seeded grain, wax seal)
//
// The three procedural moves that make warm paper real instead of asserted — all deterministic,
// all zero-bitmap, none permitted on the live-activity surfaces.

extension View {
    /// Letterpress embossed edge: a two-stop gradient stroke — a faint highlight at the top-left,
    /// a slightly heavier ink shadow at the bottom-right — so a rectangle reads as a pressed edge
    /// catching light rather than a default rounded-rect outline. The cheapest move that kills the
    /// "stock control" tell, with no texture. Layer it OVER a fill/clip.
    func embossedBorder(radius: CGFloat = CortexDesign.Radius.md, lineWidth: CGFloat = 1) -> some View {
        overlay(
            RoundedRectangle(cornerRadius: radius, style: .continuous)
                .strokeBorder(
                    LinearGradient(
                        colors: [
                            CortexDesign.ink.opacity(0.06),  // top-left: light catching the raised edge
                            CortexDesign.hairline,           // mid: settles into the plain hairline
                            CortexDesign.ink.opacity(0.10),  // bottom-right: the pressed shadow
                        ],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ),
                    lineWidth: lineWidth
                )
        )
    }

    /// Procedural paper grain: a faint ±1.5% luminance noise drawn in a Canvas, deterministic
    /// (FNV-1a-seeded per cell — the same constellation "no `.random`" contract), so warm paper has
    /// real tooth. PANELS ONLY — never on cards-in-lists (too busy) and never on live-activity
    /// surfaces. Draws behind content; the size is sampled from the Canvas, so it tiles any panel.
    func paperGrain(intensity: Double = 0.015, cell: CGFloat = 3) -> some View {
        background(PaperGrain(intensity: intensity, cell: cell).allowsHitTesting(false))
    }
}

/// The seeded grain field. A grid of `cell`-sized squares, each nudged ± a seeded luminance delta —
/// no bitmap, no randomness (FNV-1a over the cell's grid coordinate, matching MemoryMap/Wrapped).
private struct PaperGrain: View {
    let intensity: Double
    let cell: CGFloat

    var body: some View {
        Canvas { context, size in
            let cols = Int((size.width / cell).rounded(.up))
            let rows = Int((size.height / cell).rounded(.up))
            guard cols > 0, rows > 0 else { return }
            for row in 0..<rows {
                for col in 0..<cols {
                    // FNV-1a over "col,row" → a stable per-cell hash (deterministic; no `.random`).
                    var hash: UInt64 = 0xcbf29ce484222325
                    for byte in "\(col),\(row)".utf8 {
                        hash ^= UInt64(byte)
                        hash = hash &* 0x100000001b3
                    }
                    // Map the hash to a signed delta in [-intensity, +intensity].
                    let unit = Double(hash % 1000) / 999.0        // 0…1, stable
                    let delta = (unit * 2 - 1) * intensity        // ±intensity
                    // Ink darkens, paper-white lightens; alpha carries the tiny luminance change.
                    let color: Color = delta >= 0
                        ? CortexDesign.ink.opacity(abs(delta) * 1.4)
                        : CortexDesign.panelBackground.opacity(abs(delta) * 1.4)
                    let rect = CGRect(x: CGFloat(col) * cell, y: CGFloat(row) * cell, width: cell, height: cell)
                    context.fill(Path(rect), with: .color(color))
                }
            }
        }
    }
}

/// A wax-red domed seal — the one physical "moment" per surface. Built from four procedural layers,
/// no bitmap: a base `accent` fill, a top-left radial sheen (accent lightened ~12% at ~0.18 alpha),
/// a 1px inner rim (accent darkened ~18%), and a hairline white ~0.10 top edge-light. `pressed`
/// drives the "sets into the paper" state: the sheen and edge-light fade so the dome flattens.
///
/// Used as the fill for `role == .primary` buttons; also a standalone surface (recovery envelope,
/// hero disc) via `CortexSealSurface(cornerRadius:pressed:)`.
struct CortexSealSurface: View {
    var cornerRadius: CGFloat = CortexDesign.Radius.md
    var pressed: Bool = false

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        ZStack {
            // 1. Base wax fill.
            shape.fill(CortexDesign.accent)

            // 2. Top-left radial sheen — where light domes off the wax. Drops on press.
            shape.fill(
                RadialGradient(
                    colors: [
                        CortexDesign.accent.cortexLightened(0.12).opacity(pressed ? 0 : 0.18),
                        Color.clear,
                    ],
                    center: .init(x: 0.3, y: 0.25),
                    startRadius: 0,
                    endRadius: 120
                )
            )

            // 3. Inner rim — the darkened lip of the seal (always present, reads as thickness).
            shape.strokeBorder(CortexDesign.accent.cortexDarkened(0.18), lineWidth: 1)

            // 4. Hairline top edge-light — the crisp catch along the upper edge. Drops on press.
            shape
                .strokeBorder(
                    LinearGradient(
                        colors: [Color.white.opacity(pressed ? 0 : 0.10), Color.clear],
                        startPoint: .top,
                        endPoint: .center
                    ),
                    lineWidth: 1
                )
        }
    }
}

// MARK: - Buttons
//
// The app-wide button language. Every interactive button routes through these four roles so
// controls stop rendering as stock system-blue AppKit buttons (the pre-overhaul state: ~115 raw
// .buttonStyle sites with a dozen different heights). Wax-red primary carries THE one main action
// of a surface; paper secondary is the workhorse; ghost is for quiet inline actions; destructive
// stays system red per the design guardrails.

enum CortexButtonRole {
    case primary      // wax-red fill, paper text — exactly one per surface
    case secondary    // index-card fill, hairline border, ink text
    case ghost        // no fill until hover — quiet inline actions
    case destructive  // system red, bordered — delete/purge only

    var background: Color {
        switch self {
        case .primary: return CortexDesign.accent
        case .secondary: return CortexDesign.cardBackground
        case .ghost: return .clear
        case .destructive: return Color.red.opacity(0.08)
        }
    }

    var hoverBackground: Color {
        switch self {
        case .primary: return CortexDesign.accent.opacity(0.88)
        case .secondary: return CortexDesign.quietBackground
        case .ghost: return CortexDesign.ink.opacity(0.06)
        case .destructive: return Color.red.opacity(0.14)
        }
    }

    var foreground: Color {
        switch self {
        case .primary: return CortexDesign.panelBackground
        case .secondary: return CortexDesign.ink
        case .ghost: return CortexDesign.inkSecondary
        case .destructive: return .red
        }
    }

    var border: Color {
        switch self {
        case .primary: return .clear
        case .secondary: return CortexDesign.softBorder
        case .ghost: return .clear
        case .destructive: return Color.red.opacity(0.35)
        }
    }
}

enum CortexButtonSize {
    case small    // inline row actions
    case regular  // standard controls
    case large    // heroes and empty states

    var height: CGFloat {
        switch self {
        case .small: return 28
        case .regular: return 36
        case .large: return 44
        }
    }

    var font: Font {
        switch self {
        case .small: return .system(size: 12, weight: .medium)
        case .regular: return .system(size: 13, weight: .medium)
        case .large: return .system(size: 14, weight: .semibold)
        }
    }

    var horizontalPadding: CGFloat {
        switch self {
        case .small: return 10
        case .regular: return 14
        case .large: return 18
        }
    }
}

/// The one true button. Hover, press, and disabled states are built in, so every call site gets
/// the same physics: quick fade on hover, a subtle press scale, 40% opacity when disabled.
struct CortexButton: View {
    let title: String
    var systemImage: String? = nil
    var role: CortexButtonRole = .secondary
    var size: CortexButtonSize = .regular
    var fullWidth: Bool = false
    let action: () -> Void

    @State private var hovering = false
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let systemImage {
                    Image(systemName: systemImage)
                        .font(size.font.weight(.medium))
                }
                Text(title)
                    .font(size.font)
                    .lineLimit(1)
            }
            .padding(.horizontal, size.horizontalPadding)
            .frame(maxWidth: fullWidth ? .infinity : nil, minHeight: size.height)
            .contentShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        }
        .buttonStyle(CortexPressStyle(
            background: hovering ? role.hoverBackground : role.background,
            foreground: role.foreground,
            border: role.border,
            role: role,
            hovering: hovering
        ))
        .onHover { hovering = $0 }
        .opacity(isEnabled ? 1 : 0.4)
        // Hover is animated once inside the press style (single spring) — no second hover animation
        // here, which is what caused the press-during-hover stutter.
    }
}

/// Icon-only sibling (toolbar actions, row affordances). Same states, square hit target.
struct CortexIconButton: View {
    let systemImage: String
    var role: CortexButtonRole = .ghost
    var size: CortexButtonSize = .regular
    var help: String = ""
    let action: () -> Void

    @State private var hovering = false
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(size.font.weight(.medium))
                .frame(width: size.height, height: size.height)
                .contentShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        }
        .buttonStyle(CortexPressStyle(
            background: hovering ? role.hoverBackground : role.background,
            foreground: role.foreground,
            border: role.border,
            role: role,
            hovering: hovering
        ))
        .onHover { hovering = $0 }
        .opacity(isEnabled ? 1 : 0.4)
        // Single hover/press spring lives in the press style (no stutter).
        .help(help)
    }
}

/// Press physics shared by both button components. ONE spring (`CortexMotion.press`) drives hover
/// AND press so a press-during-hover no longer double-animates (the old split `.easeOut(0.12)` on
/// hover + `.easeOut(0.1)` on press stuttered). Rendering is role-aware:
///   • `.primary` fills with a `CortexSealSurface` that "sets into the paper" on press — scale 0.97
///     and its sheen/edge-light drop over `CortexMotion.settle` (~0.09s).
///   • `.secondary`/`.ghost` gain an `embossedBorder` (letterpress edge) instead of a flat stroke.
///   • `.destructive` keeps the system-red bordered shape untouched (guardrail).
/// The legacy `init(background:foreground:border:)` remains for any plain call site; the primary
/// path uses `init(role:hovering:)`.
struct CortexPressStyle: ButtonStyle {
    let background: Color
    let foreground: Color
    let border: Color
    /// When set, rendering follows the role's physical treatment (seal / emboss). When nil, the
    /// legacy flat fill+stroke is used (source-compatible with the old three-arg init).
    var role: CortexButtonRole? = nil
    /// Hover state, so the fill can be resolved once and animated on the single press spring.
    var hovering: Bool = false

    private var radius: CGFloat { CortexDesign.Radius.md }

    func makeBody(configuration: Configuration) -> some View {
        let pressed = configuration.isPressed
        return configuration.label
            .foregroundColor(foreground)
            .background(fill(pressed: pressed))
            .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(edge)
            .scaleEffect(pressScale(pressed: pressed))
            .animation(CortexMotion.press, value: pressed)
            .animation(CortexMotion.press, value: hovering)
    }

    /// Primary presses a touch deeper into the paper (0.97); everything else 0.98.
    private func pressScale(pressed: Bool) -> CGFloat {
        guard pressed else { return 1 }
        return role == .primary ? 0.97 : 0.98
    }

    @ViewBuilder
    private func fill(pressed: Bool) -> some View {
        if role == .primary {
            // The wax seal is the primary fill; press drops the sheen/edge-light (settle curve).
            CortexSealSurface(cornerRadius: radius, pressed: pressed)
                .animation(CortexMotion.settle, value: pressed)
        } else {
            RoundedRectangle(cornerRadius: radius, style: .continuous)
                .fill(background)
        }
    }

    @ViewBuilder
    private var edge: some View {
        switch role {
        case .secondary, .ghost:
            // Letterpress edge — kills the flat rounded-rect tell. Ghost's is barely-there until
            // its hover fill lifts it; both read as a pressed paper edge, not a stock outline.
            RoundedRectangle(cornerRadius: radius, style: .continuous)
                .strokeBorder(
                    LinearGradient(
                        colors: [
                            CortexDesign.ink.opacity(0.06),
                            CortexDesign.hairline,
                            CortexDesign.ink.opacity(0.10),
                        ],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ),
                    lineWidth: 1
                )
        case .primary:
            EmptyView() // the seal draws its own rim + edge-light.
        default:
            // Legacy / destructive: the plain stroke.
            RoundedRectangle(cornerRadius: radius, style: .continuous)
                .stroke(border, lineWidth: 1)
        }
    }
}

// MARK: - Animatable number

/// A serif numeral that ROLLS to a new value on change (a counter-wheel count-up), instead of
/// snapping. `SwiftUI`'s `animatableData` interpolates the underlying `Double` on the
/// `CortexMotion.rollNumber` curve; a formatter turns each intermediate frame back into a string so
/// prefixes/suffixes/grouping ("1,204", "3.2k", "87%") are preserved. Purely a render — the wheel
/// spins toward the passed value and stops there.
struct AnimatableNumber: View, Animatable {
    /// The current (animating) value.
    var value: Double
    var font: Font = CortexDesign.Typography.stat
    var color: Color = CortexDesign.ink
    /// Turns a frame's Double into the shown string. Defaults to a grouped integer.
    var format: (Double) -> String = { AnimatableNumber.groupedInteger($0) }

    var animatableData: Double {
        get { value }
        set { value = newValue }
    }

    var body: some View {
        Text(format(value))
            .font(font)
            .monospacedDigit()
            .foregroundColor(color)
    }

    /// Default formatter: a grouped integer ("1,204"). Rounds the animating frame.
    static func groupedInteger(_ n: Double) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.maximumFractionDigits = 0
        return formatter.string(from: NSNumber(value: n.rounded())) ?? "\(Int(n.rounded()))"
    }
}

// MARK: - Stat

/// One consistent treatment for headline numbers (Home stats, counts): serif numerals over a small
/// SF label — replaces the mixed .title3/.stat ad-hoc shapes. If the value's leading run is numeric
/// it ROLLS on change (count-up wheel, `AnimatableNumber`), preserving any suffix ("k", "%", " days")
/// — otherwise it renders the string verbatim. The `value: String` API is unchanged, so all call
/// sites keep working; the roll is automatic on whatever they pass.
struct CortexStatView: View {
    let value: String
    let label: String

    /// The rolling target parsed from `value`; nil when `value` has no leading number.
    @State private var animatedNumber: Double = 0

    /// Splits "3.2k" → (3.2, "k"), "1,204" → (1204, ""), "—" → nil.
    private var parsed: (number: Double, suffix: String)? {
        let trimmed = value.trimmingCharacters(in: .whitespaces)
        guard let first = trimmed.first, first.isNumber || first == "-" || first == "." else { return nil }
        var numberPart = ""
        var suffixPart = ""
        var inNumber = true
        for char in trimmed {
            if inNumber, char.isNumber || char == "." || char == "," || char == "-" {
                if char != "," { numberPart.append(char) }
            } else {
                inNumber = false
                suffixPart.append(char)
            }
        }
        guard let number = Double(numberPart) else { return nil }
        return (number, suffixPart)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            if let parsed {
                HStack(alignment: .firstTextBaseline, spacing: 0) {
                    AnimatableNumber(
                        value: animatedNumber,
                        format: { Self.format($0, like: parsed.number) }
                    )
                    if !parsed.suffix.isEmpty {
                        Text(parsed.suffix)
                            .font(CortexDesign.Typography.stat)
                            .monospacedDigit()
                            .foregroundColor(CortexDesign.ink)
                    }
                }
                .onAppear { animatedNumber = parsed.number }
                .onChange(of: value) { _ in
                    withAnimation(CortexMotion.rollNumber) { animatedNumber = parsed.number }
                }
            } else {
                Text(value)
                    .font(CortexDesign.Typography.stat)
                    .monospacedDigit()
                    .foregroundColor(CortexDesign.ink)
            }
            Text(label)
                .font(CortexDesign.Typography.caption)
                .foregroundColor(CortexDesign.inkSecondary)
        }
    }

    /// Format a rolling frame to match the target's precision (keep one decimal if the target had
    /// one, else grouped integer) so "3.2k" rolls through "1.4"→"3.2", not "3".
    private static func format(_ n: Double, like target: Double) -> String {
        if target != target.rounded() {
            return String(format: "%.1f", n)
        }
        return AnimatableNumber.groupedInteger(n)
    }
}

// MARK: - Index card

/// The index-card recipe: solid card surface, crisp 8pt corners, a letterpress edge, and TWO-layer
/// paper depth (a soft ambient cast + a tight contact shadow) plus a top-40% edge-light — cards
/// read as physical cards lying on the paper desk, catching light along their top edge. When
/// `interactive` is set, the card lifts on hover (ambient spreads, it rises 1pt) — the affordance
/// for a whole-card tap target.
struct CortexCard: ViewModifier {
    var padding: CGFloat = CortexDesign.Space.lg
    var background: Color = CortexDesign.cardBackground
    var interactive: Bool = false

    @State private var hovering = false

    private var elevation: CortexDesign.Elevation {
        interactive && hovering ? .raised : .rest
    }

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
        let ambient = elevation.ambient
        let contact = elevation.contact
        return content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(background)
            .clipShape(shape)
            .overlay(
                // Top-40% edge-light: a hairline highlight fading down the upper part of the card,
                // so light reads as coming from above the desk.
                shape
                    .strokeBorder(
                        LinearGradient(
                            colors: [
                                CortexDesign.panelBackground.cortexLightened(0.6).opacity(0.35),
                                Color.clear,
                            ],
                            startPoint: .top,
                            endPoint: UnitPoint(x: 0.5, y: 0.4)
                        ),
                        lineWidth: 1
                    )
            )
            .overlay(shape.stroke(CortexDesign.hairline, lineWidth: 1))
            // Two-layer shadow: ambient cast + tight contact line.
            .shadow(color: ambient.color, radius: ambient.radius, y: ambient.y)
            .shadow(color: contact.color, radius: contact.radius, y: contact.y)
            .offset(y: interactive && hovering ? -1 : 0)
            .animation(CortexMotion.lift, value: hovering)
            .onHover { if interactive { hovering = $0 } }
    }
}

extension View {
    /// Wrap a view in the index-card recipe. `interactive` is additive (defaults off) so all 37
    /// existing `cortexCard(...)` call sites stay source-compatible while new tappable cards opt in.
    func cortexCard(
        padding: CGFloat = CortexDesign.Space.lg,
        background: Color = CortexDesign.cardBackground,
        interactive: Bool = false
    ) -> some View {
        modifier(CortexCard(padding: padding, background: background, interactive: interactive))
    }

    /// The margin spine rule — the app's most recognizable mark. A 3pt vertical rule inset in the
    /// leading margin of a card, like the red margin line of an index card. Now a vertical gradient
    /// with a ~0.5px feathered shadow so the rule reads as INK BLED into the paper, not a flat bar.
    /// Semantic colors: gold = unreviewed, wax red = kept/cited, ink 20% = raw source.
    func archiveSpine(_ color: Color) -> some View {
        overlay(alignment: .leading) {
            RoundedRectangle(cornerRadius: 1)
                .fill(
                    LinearGradient(
                        colors: [color.cortexLightened(0.08), color, color.cortexDarkened(0.10)],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .frame(width: 3)
                .shadow(color: color.opacity(0.35), radius: 0.5, x: 0.5) // ink bleeding into paper
                .padding(.vertical, 10)
                .padding(.leading, 12)
        }
    }
}

// MARK: - Ruled section header

/// Section headers sit on a notebook rule: serif title, then a hairline running to the trailing
/// edge, opening with a short wax tick. The detail line stays in the SF working voice.
struct SectionHeader: View {
    let title: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(title)
                    .font(CortexDesign.Typography.title)
                    .foregroundColor(CortexDesign.ink)
                // The wax tick — a small 6×3 sealing-wax rect that opens the rule (the doc-promised
                // mark that was missing). Reads as a stamp pressed at the head of the line.
                RoundedRectangle(cornerRadius: 0.5)
                    .fill(CortexDesign.accent)
                    .frame(width: 6, height: 3)
                    .offset(y: -3)
                Rectangle()
                    .fill(
                        LinearGradient(
                            colors: [CortexDesign.accent.opacity(0.55), CortexDesign.hairline],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .frame(height: 1)
                    .offset(y: -4)
            }
            if !detail.isEmpty {
                Text(detail)
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

// MARK: - Accession stamp

/// A card-catalog accession line in tiny monospaced caps — "NOTES · 12 JUN 2026". Replaces
/// colored capsule pills as the metadata language: state is words and ink, not bubbles. Never
/// fabricate a segment; omit what you don't know.
struct AccessionStamp: View {
    let segments: [String]
    /// Index of one segment to tint wax-red (e.g. "KEPT"); nil for all-faint.
    var emphasisIndex: Int? = nil

    var body: some View {
        HStack(spacing: 0) {
            ForEach(Array(segments.enumerated()), id: \.offset) { index, segment in
                if index > 0 {
                    Text(" · ")
                        .font(CortexDesign.Typography.stamp)
                        .foregroundColor(CortexDesign.inkFaint)
                }
                Text(segment.uppercased())
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(index == emphasisIndex ? CortexDesign.accent : CortexDesign.inkFaint)
            }
        }
        .lineLimit(1)
    }
}

// MARK: - Status pill (quiet, archive-voiced)

/// A compact status mark: icon + word in the working voice, tinted, on a quiet wash. Kept for
/// call-site compatibility; new metadata should prefer AccessionStamp.
struct CortexStatusPill: View {
    let label: String
    let systemImage: String
    var color: Color = CortexDesign.accent

    var body: some View {
        Label(label, systemImage: systemImage)
            .font(.system(size: 12, weight: .medium))
            .foregroundColor(color)
            .padding(.horizontal, CortexDesign.Space.sm)
            .padding(.vertical, 4)
            .background(color.opacity(0.10))
            .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous))
    }
}

/// A calm, centered empty state: icon, serif title, message, optional action.
struct CortexEmptyState: View {
    let systemImage: String
    let title: String
    let message: String
    var actionTitle: String? = nil
    var action: (() -> Void)? = nil

    var body: some View {
        VStack(spacing: CortexDesign.Space.md) {
            Image(systemName: systemImage)
                .font(.system(size: 38, weight: .regular))
                .foregroundColor(CortexDesign.accent.opacity(0.55))
            VStack(spacing: CortexDesign.Space.xs) {
                Text(title)
                    .font(CortexDesign.Typography.display(20))
                    .foregroundColor(CortexDesign.ink)
                    .multilineTextAlignment(.center)
                Text(message)
                    .font(CortexDesign.Typography.body)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 420)
            }
            if let actionTitle, let action {
                CortexButton(title: actionTitle, role: .primary, size: .large, action: action)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, CortexDesign.Space.xl)
        .padding(.horizontal, CortexDesign.Space.lg)
    }
}

// MARK: - Wax-seal mark + form primitives (canonical; workstream #1 completion)
//
// These three finish the Wave-1 backbone (the earlier pass ran out before adding them).
// Every surface uses these — DO NOT redefine locally.

/// The wax-seal mark — a domed sealing-wax disc with an embossed serif glyph. Replaces every stock
/// SF-Symbol hero (sign-in, onboarding, the sealed recovery-code envelope).
struct CortexWaxSeal: View {
    var size: CGFloat = 64
    var glyph: String = "C"

    var body: some View {
        ZStack {
            CortexSealSurface(cornerRadius: size / 2)
                .clipShape(Circle())
            Circle()
                .strokeBorder(Color.white.opacity(0.08), lineWidth: 1)
                .padding(size * 0.14)
            ZStack {
                Text(glyph)
                    .font(.system(size: size * 0.46, weight: .semibold, design: .serif))
                    .foregroundColor(CortexDesign.accent.opacity(0.65))
                    .offset(y: 0.7)
                Text(glyph)
                    .font(.system(size: size * 0.46, weight: .semibold, design: .serif))
                    .foregroundColor(CortexDesign.panelBackground.opacity(0.92))
            }
        }
        .frame(width: size, height: size)
        .shadow(color: CortexDesign.accent.opacity(0.30), radius: 10, y: 4)
        .shadow(color: Color.black.opacity(0.18), radius: 3, y: 1)
    }
}

/// A design-system text field — a quiet recessed well, letterpress embossed edge, and a wax focus
/// ring. `secure` swaps in a SecureField; `mono` renders the value in the catalog-stamp voice.
/// Replaces every `.textFieldStyle(.roundedBorder)`.
struct CortexField: View {
    let placeholder: String
    @Binding var text: String
    var secure: Bool = false
    var mono: Bool = false
    var textContentType: NSTextContentType? = nil
    var disableAutocorrection: Bool = false

    @FocusState private var focused: Bool

    private var valueFont: Font {
        mono ? .system(.body, design: .monospaced) : CortexDesign.Typography.body
    }

    var body: some View {
        Group {
            if secure {
                SecureField(placeholder, text: $text)
            } else {
                TextField(placeholder, text: $text)
            }
        }
        .textFieldStyle(.plain)
        .font(valueFont)
        .foregroundColor(CortexDesign.ink)
        .textContentType(textContentType)
        .disableAutocorrection(disableAutocorrection)
        .focused($focused)
        .padding(.horizontal, 12)
        .frame(minHeight: CortexDesign.controlHeight)
        .background(CortexDesign.quietBackground)
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        .embossedBorder(radius: CortexDesign.Radius.md)
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .strokeBorder(CortexDesign.accent.opacity(focused ? 0.55 : 0), lineWidth: 1.5)
        )
        .animation(CortexMotion.focus, value: focused)
    }
}

/// A design-system toggle: an SF-voiced label beside a compact wax-red switch. Replaces the stock
/// blue `Toggle` so a confirm speaks the archive's language.
struct CortexToggle: View {
    let title: String
    @Binding var isOn: Bool

    var body: some View {
        Button {
            isOn.toggle()
        } label: {
            HStack(spacing: 8) {
                ZStack(alignment: isOn ? .trailing : .leading) {
                    Capsule()
                        .fill(isOn ? CortexDesign.accent : CortexDesign.ink.opacity(0.18))
                    Circle()
                        .fill(CortexDesign.panelBackground)
                        .padding(2)
                        .shadow(color: Color.black.opacity(0.2), radius: 1, y: 0.5)
                }
                .frame(width: 34, height: 20)
                .animation(CortexMotion.press, value: isOn)
                Text(title)
                    .font(CortexDesign.Typography.body)
                    .foregroundColor(CortexDesign.ink)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
