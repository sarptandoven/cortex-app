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
    case trustBackup

    var id: Int { rawValue }

    var title: String {
        switch self {
        case .privateVault: return "Private"
        case .firstSource: return "Notes"
        case .reviewMemory: return "Review"
        case .askUse: return "Ask"
        case .trustBackup: return "Backup"
        }
    }

    var subtitle: String {
        switch self {
        case .privateVault:
            return "Cortex keeps memory private on this Mac."
        case .firstSource:
            return "Connect notes once. Cortex keeps them synced."
        case .reviewMemory:
            return "Approve one useful item before Cortex uses it."
        case .askUse:
            return "Ask once and check the citations."
        case .trustBackup:
            return "Back up local memory, or choose to do it later."
        }
    }

    var systemImage: String {
        switch self {
        case .privateVault: return "externaldrive.badge.checkmark"
        case .firstSource: return "link.circle"
        case .reviewMemory: return "checklist"
        case .askUse: return "sparkle.magnifyingglass"
        case .trustBackup: return "archivebox"
        }
    }
}
