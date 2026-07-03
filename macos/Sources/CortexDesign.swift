import SwiftUI
import AppKit

/// Resolves to a light or dark value based on the current system appearance, so the whole app
/// adapts to Dark Mode from a single palette definition (no call-site changes needed). The light
/// values are unchanged from the original palette, so light mode looks identical.
private func cortexAdaptiveColor(light: NSColor, dark: NSColor) -> Color {
    Color(nsColor: NSColor(name: nil, dynamicProvider: { appearance in
        appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? dark : light
    }))
}

enum CortexDesign {
    // Palette — a calm surface with a single confident accent, adaptive to light/dark.
    static let accent = cortexAdaptiveColor(
        light: NSColor(srgbRed: 0.13, green: 0.32, blue: 0.72, alpha: 1),
        dark: NSColor(srgbRed: 0.46, green: 0.63, blue: 0.99, alpha: 1)
    )
    static let accentSoft = cortexAdaptiveColor(
        light: NSColor(srgbRed: 0.13, green: 0.32, blue: 0.72, alpha: 0.10),
        dark: NSColor(srgbRed: 0.46, green: 0.63, blue: 0.99, alpha: 0.22)
    )
    static let appBackground = cortexAdaptiveColor(
        light: NSColor(srgbRed: 0.985, green: 0.980, blue: 0.955, alpha: 1),
        dark: NSColor(srgbRed: 0.11, green: 0.11, blue: 0.12, alpha: 1)
    )
    static let panelBackground = cortexAdaptiveColor(
        light: NSColor(white: 1.0, alpha: 0.92),
        dark: NSColor(srgbRed: 0.20, green: 0.20, blue: 0.22, alpha: 0.92)
    )
    static let cardBackground = cortexAdaptiveColor(
        light: NSColor(white: 1.0, alpha: 0.86),
        dark: NSColor(srgbRed: 0.23, green: 0.23, blue: 0.25, alpha: 0.90)
    )
    static let quietBackground = cortexAdaptiveColor(
        light: NSColor(srgbRed: 0.950, green: 0.955, blue: 0.940, alpha: 1),
        dark: NSColor(srgbRed: 0.15, green: 0.15, blue: 0.16, alpha: 1)
    )
    static let softBorder = cortexAdaptiveColor(
        light: NSColor(white: 0.0, alpha: 0.08),
        dark: NSColor(white: 1.0, alpha: 0.14)
    )
    static let hairline = cortexAdaptiveColor(
        light: NSColor(white: 0.0, alpha: 0.055),
        dark: NSColor(white: 1.0, alpha: 0.09)
    )

    // Spacing scale — generous whitespace, used everywhere for consistent rhythm.
    enum Space {
        static let xs: CGFloat = 6
        static let sm: CGFloat = 10
        static let md: CGFloat = 16
        static let lg: CGFloat = 22
        static let xl: CGFloat = 32
    }

    // Corner radii.
    enum Radius {
        static let sm: CGFloat = 8
        static let md: CGFloat = 14
        static let lg: CGFloat = 20
    }

    // Minimum comfortable control height (no tiny buttons).
    static let controlHeight: CGFloat = 40
}

/// Card container: a soft rounded panel with a hairline border. The primary way
/// to group related content so surfaces read as calm, structured sections.
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
                    .stroke(CortexDesign.softBorder, lineWidth: 1)
            )
    }
}

extension View {
    func cortexCard(
        padding: CGFloat = CortexDesign.Space.lg,
        background: Color = CortexDesign.cardBackground
    ) -> some View {
        modifier(CortexCard(padding: padding, background: background))
    }
}

struct SectionHeader: View {
    let title: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title)
                .font(.headline)
            Text(detail)
                .font(.caption)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// A compact colored status capsule (icon + label). Reused wherever a surface
/// summarizes its current state.
struct CortexStatusPill: View {
    let label: String
    let systemImage: String
    var color: Color = CortexDesign.accent

    var body: some View {
        Label(label, systemImage: systemImage)
            .font(.callout)
            .fontWeight(.semibold)
            .foregroundColor(color)
            .padding(.horizontal, CortexDesign.Space.sm)
            .padding(.vertical, CortexDesign.Space.xs)
            .background(color.opacity(0.12))
            .clipShape(Capsule())
    }
}

/// A calm, centered empty state: icon, title, message, and an optional action.
struct CortexEmptyState: View {
    let systemImage: String
    let title: String
    let message: String
    var actionTitle: String? = nil
    var action: (() -> Void)? = nil

    var body: some View {
        VStack(spacing: CortexDesign.Space.md) {
            Image(systemName: systemImage)
                .font(.system(size: 40, weight: .regular))
                .foregroundColor(CortexDesign.accent.opacity(0.65))
            VStack(spacing: CortexDesign.Space.xs) {
                Text(title)
                    .font(.title3)
                    .fontWeight(.semibold)
                    .multilineTextAlignment(.center)
                Text(message)
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 420)
            }
            if let actionTitle, let action {
                Button(action: action) {
                    Text(actionTitle).frame(minHeight: CortexDesign.controlHeight - 8)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, CortexDesign.Space.xl)
        .padding(.horizontal, CortexDesign.Space.lg)
    }
}
