import SwiftUI

enum AppTab: Hashable {
    case model
    case review
    case ask
}

enum OnboardingStep: Int, CaseIterable, Identifiable {
    case privateVault
    case firstSource
    case reviewMemory
    case askUse

    var id: Int { rawValue }

    var title: String {
        switch self {
        case .privateVault: return "Memory"
        case .firstSource: return "Source"
        case .reviewMemory: return "Review"
        case .askUse: return "Ask"
        }
    }

    var subtitle: String {
        switch self {
        case .privateVault:
            return "Confirm the local memory engine is ready on this Mac."
        case .firstSource:
            return "Connect Obsidian or a local notes folder once."
        case .reviewMemory:
            return "Useful memory waits for approval before Cortex uses it."
        case .askUse:
            return "Ask approved memory with citations."
        }
    }

    var systemImage: String {
        switch self {
        case .privateVault: return "externaldrive.badge.checkmark"
        case .firstSource: return "link.circle"
        case .reviewMemory: return "checklist"
        case .askUse: return "sparkle.magnifyingglass"
        }
    }
}
