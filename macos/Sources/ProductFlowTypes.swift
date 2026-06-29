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
        case .firstSource: return "Add First Source"
        case .reviewMemory: return "Review Memory"
        case .askUse: return "Ask / Use Cortex"
        case .trustBackup: return "Trust & Backup"
        }
    }

    var subtitle: String {
        switch self {
        case .privateVault:
            return "Confirm the local memory engine and readable vault on this Mac."
        case .firstSource:
            return "Import real context from the tools and files that describe your life and work."
        case .reviewMemory:
            return "Approve the first useful memory before it becomes part of your model."
        case .askUse:
            return "Use Cortex once through Ask or an AI handoff so the loop is real."
        case .trustBackup:
            return "Choose your privacy posture and decide how this vault is backed up."
        }
    }

    var systemImage: String {
        switch self {
        case .privateVault: return "externaldrive.badge.checkmark"
        case .firstSource: return "tray.and.arrow.down"
        case .reviewMemory: return "checklist"
        case .askUse: return "sparkle.magnifyingglass"
        case .trustBackup: return "lock.shield"
        }
    }
}
