import SwiftUI

enum AppTab: Hashable {
    case model
    case sources
    case review
    case ask
    case trust
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
        case .privateVault: return "Private Vault"
        case .firstSource: return "Connect First Source"
        case .reviewMemory: return "Review Memory"
        case .askUse: return "Ask Cortex"
        case .trustBackup: return "Trust & Backup"
        }
    }

    var subtitle: String {
        switch self {
        case .privateVault:
            return "Confirm the local memory engine and readable vault on this Mac."
        case .firstSource:
            return "Connect one service or app integration that carries real work context."
        case .reviewMemory:
            return "Approve the first useful memory before it becomes part of your model."
        case .askUse:
            return "Ask one cited question so you can see what Cortex actually knows."
        case .trustBackup:
            return "Confirm privacy defaults and decide how this vault is backed up."
        }
    }

    var systemImage: String {
        switch self {
        case .privateVault: return "externaldrive.badge.checkmark"
        case .firstSource: return "link.circle"
        case .reviewMemory: return "checklist"
        case .askUse: return "sparkle.magnifyingglass"
        case .trustBackup: return "lock.shield"
        }
    }
}
