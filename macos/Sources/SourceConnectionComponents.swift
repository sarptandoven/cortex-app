import SwiftUI

struct SourceConnectorStatusCard: View {
    @ObservedObject var state: AppState
    let connector: SourceConnectorCatalogItem
    let connected: Bool
    var needsContent: Bool = false
    var needsAttention: Bool = false
    var attentionDetail: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: statusIcon)
                    .font(.system(size: 30, weight: .semibold))
                    .foregroundColor(statusColor)
                    .frame(width: 42, height: 42)
                Spacer(minLength: 0)
                SourceStatusChip(
                    title: statusTitle,
                    systemImage: statusChipIcon,
                    color: statusColor
                )
            }
            VStack(alignment: .leading, spacing: 5) {
                Text(connector.id == "obsidian" ? "Primary notes" : "Later")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(.secondary)
                Text(connector.id == "obsidian" ? "Notes folder" : connector.name)
                    .font(.title3)
                    .fontWeight(.semibold)
                Text(statusDetail)
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if connector.id == "obsidian" {
                HStack(spacing: 8) {
                    Button {
                        state.connectLocalNotesFolder(connector)
                    } label: {
                        Label(primaryButtonTitle, systemImage: primaryButtonIcon)
                            .frame(maxWidth: .infinity, minHeight: 46)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                        .disabled(state.isBusy)

                    if state.hasConnectedObsidianVault {
                        Button {
                            state.connectLocalNotesFolder(connector, chooseNew: true)
                        } label: {
                            Label("Change folder", systemImage: "folder")
                                .frame(minHeight: 46)
                        }
                        .controlSize(.large)
                        .disabled(state.isBusy)
                    }
                }
            } else {
                HStack(spacing: 8) {
                    Image(systemName: "clock")
                        .foregroundColor(.secondary)
                    Text("Available later")
                        .font(.callout)
                        .fontWeight(.medium)
                    Spacer()
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 8)
                .background(CortexDesign.cardBackground)
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, minHeight: 210, alignment: .topLeading)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var statusTitle: String {
        if needsAttention { return "Needs attention" }
        if needsContent { return "No notes found" }
        if connector.id != "obsidian" { return "Later" }
        return connected ? "Connected" : "Local"
    }

    private var statusIcon: String {
        if needsAttention { return "exclamationmark.triangle.fill" }
        if needsContent { return "folder.badge.questionmark" }
        if connector.id != "obsidian" { return "clock" }
        return connected ? "checkmark.seal.fill" : "link.badge.plus"
    }

    private var statusChipIcon: String {
        if needsAttention { return "exclamationmark.circle.fill" }
        if needsContent { return "exclamationmark.circle.fill" }
        if connector.id != "obsidian" { return "clock" }
        return connected ? "checkmark.circle.fill" : "folder.badge.plus"
    }

    private var statusColor: Color {
        if needsAttention { return .orange }
        if needsContent { return .orange }
        if connector.id != "obsidian" { return .secondary }
        return connected ? .green : .accentColor
    }

    private var statusDetail: String {
        if connector.id != "obsidian" {
            return "Available from advanced connector settings."
        }
        if needsAttention {
            return attentionDetail ?? "Cortex needs attention before these notes can keep syncing."
        }
        if needsContent {
            return "Cortex could not find usable notes there. Choose a notes library with real content."
        }
        if connected {
            if !state.hasConnectedObsidianVault {
                return "Reconnect the notes folder on this Mac so Cortex can keep syncing."
            }
            return "Cortex keeps these notes synced. Review approves memory before Ask or AI tools use it."
        }
        return "Choose a notes folder once. Cortex syncs locally and keeps citations attached."
    }

    private var primaryButtonTitle: String {
        if needsAttention { return "Fix notes sync" }
        if needsContent { return "Choose notes" }
        if connected {
            return state.hasConnectedObsidianVault ? "Sync notes" : "Reconnect notes"
        }
        return "Start notes sync"
    }

    private var primaryButtonIcon: String {
        if needsAttention { return "exclamationmark.triangle.fill" }
        if needsContent { return "folder.badge.questionmark" }
        if connected {
            return state.hasConnectedObsidianVault ? "arrow.triangle.2.circlepath" : "folder.badge.plus"
        }
        return "folder.badge.plus"
    }
}

struct SourceStatusChip: View {
    let title: String
    let systemImage: String
    let color: Color

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: systemImage)
            Text(title)
        }
        .font(.caption)
        .fontWeight(.semibold)
        .foregroundColor(color)
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .background(color.opacity(0.12))
        .clipShape(Capsule())
    }
}
