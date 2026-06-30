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
        case .firstSource: return "Notes"
        case .reviewMemory: return "Review"
        case .askUse: return "Ask"
        }
    }

    var subtitle: String {
        switch self {
        case .privateVault:
            return "Cortex keeps your memory private on this Mac."
        case .firstSource:
            return "Connect notes once, then let Cortex sync memory into Review."
        case .reviewMemory:
            return "Approve one synced item before Cortex uses it."
        case .askUse:
            return "Ask once and confirm Cortex cites memory."
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
