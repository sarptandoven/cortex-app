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

/// "The Archive" — Cortex's visual identity. A personal archive you'd trust with your life's
/// marginalia: warm paper, iron-gall ink, sealing-wax red, index cards with margin rules, and
/// card-catalog metadata in tiny monospaced caps. Three type voices with strict roles (serif =
/// the archive's voice, SF = the app's working voice, mono = the catalog stamp). Guardrails are
/// contractual: no textures or skeuomorphism, gold is a fill (never small text), destructive
/// actions stay system red, max weight .semibold app-wide.
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
            border: role.border
        ))
        .onHover { hovering = $0 }
        .opacity(isEnabled ? 1 : 0.4)
        .animation(.easeOut(duration: 0.12), value: hovering)
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
            border: role.border
        ))
        .onHover { hovering = $0 }
        .opacity(isEnabled ? 1 : 0.4)
        .animation(.easeOut(duration: 0.12), value: hovering)
        .help(help)
    }
}

/// Press physics shared by both button components: fill + hairline + a 0.98 press scale.
struct CortexPressStyle: ButtonStyle {
    let background: Color
    let foreground: Color
    let border: Color

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundColor(foreground)
            .background(background)
            .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                    .stroke(border, lineWidth: 1)
            )
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.easeOut(duration: 0.1), value: configuration.isPressed)
    }
}

// MARK: - Stat

/// One consistent treatment for headline numbers (Home stats, counts): serif numerals over a
/// small SF label — replaces the mixed .title3/.stat ad-hoc shapes.
struct CortexStatView: View {
    let value: String
    let label: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value)
                .font(CortexDesign.Typography.stat)
                .monospacedDigit()
                .foregroundColor(CortexDesign.ink)
            Text(label)
                .font(CortexDesign.Typography.caption)
                .foregroundColor(CortexDesign.inkSecondary)
        }
    }
}

// MARK: - Index card

/// The index-card recipe: solid card surface, crisp 8pt corners, hairline ink border, and a
/// whisper of contact shadow — cards read as physical cards lying on the paper desk.
struct CortexCard: ViewModifier {
    var padding: CGFloat = CortexDesign.Space.lg
    var background: Color = CortexDesign.cardBackground

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(background)
            .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                    .stroke(CortexDesign.hairline, lineWidth: 1)
            )
            .shadow(color: CortexDesign.ink.opacity(0.06), radius: 3, y: 1)
    }
}

extension View {
    func cortexCard(
        padding: CGFloat = CortexDesign.Space.lg,
        background: Color = CortexDesign.cardBackground
    ) -> some View {
        modifier(CortexCard(padding: padding, background: background))
    }

    /// The margin spine rule — the app's most recognizable mark. A 3pt vertical rule inset in the
    /// leading margin of a card, like the red margin line of an index card. Semantic colors:
    /// gold = unreviewed, wax red = kept/cited, ink 20% = raw source.
    func archiveSpine(_ color: Color) -> some View {
        overlay(alignment: .leading) {
            RoundedRectangle(cornerRadius: 1)
                .fill(color)
                .frame(width: 3)
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
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(title)
                    .font(CortexDesign.Typography.title)
                    .foregroundColor(CortexDesign.ink)
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
