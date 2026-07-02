import SwiftUI

enum CortexDesign {
    // Palette — a calm, light, warm surface with a single confident accent.
    static let accent = Color(red: 0.13, green: 0.32, blue: 0.72)
    static let accentSoft = Color(red: 0.13, green: 0.32, blue: 0.72).opacity(0.10)
    static let appBackground = Color(red: 0.985, green: 0.980, blue: 0.955)
    static let panelBackground = Color.white.opacity(0.92)
    static let cardBackground = Color.white.opacity(0.86)
    static let quietBackground = Color(red: 0.950, green: 0.955, blue: 0.940)
    static let softBorder = Color.black.opacity(0.08)
    static let hairline = Color.black.opacity(0.055)

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
