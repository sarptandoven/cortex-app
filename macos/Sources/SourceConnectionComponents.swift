import SwiftUI

struct SourceConnectorStatusCard: View {
    @ObservedObject var state: AppState
    let connector: SourceConnectorCatalogItem
    let connected: Bool
    var needsContent: Bool = false
    var needsAttention: Bool = false
    var attentionDetail: String? = nil
    @State private var confirmDisconnect = false

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
                Text(connector.id == "obsidian" ? "Primary notes" : "Direct source")
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
                        state.connectLocalNotesFolder(connector, chooseNew: needsContent)
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

                        Button(role: .destructive) {
                            confirmDisconnect = true
                        } label: {
                            Label("Disconnect", systemImage: "xmark.circle")
                                .frame(minHeight: 46)
                        }
                        .controlSize(.large)
                        .disabled(state.isBusy)
                        .help("Stop syncing this notes folder. Already synced memory is kept.")
                        .confirmationDialog(
                            "Disconnect notes folder?",
                            isPresented: $confirmDisconnect,
                            titleVisibility: .visible
                        ) {
                            Button("Disconnect notes", role: .destructive) {
                                state.disconnectObsidianNotes()
                            }
                            Button("Cancel", role: .cancel) {}
                        } message: {
                            Text("Cortex stops syncing this folder. Memory already synced and reviewed is kept and stays available to Ask. You can reconnect a folder later.")
                        }
                    }
                }
            } else {
                // A real, working control — previously this was a card styled like a button that
                // did nothing when tapped. It now actually opens Connections & Privacy.
                Button {
                    state.openConnectionsPrivacy()
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "lock.shield")
                        Text("Open Connections & Privacy")
                            .font(.callout)
                            .fontWeight(.medium)
                        Spacer()
                        Image(systemName: "chevron.right")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 10)
                    .frame(maxWidth: .infinity)
                    .background(CortexDesign.cardBackground)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .contentShape(RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
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
        if connector.id != "obsidian" { return connector.connectorReadinessStatus == "token-ready" ? "Token sync" : "Ready" }
        return connected ? "Connected" : "Local"
    }

    private var statusIcon: String {
        if needsAttention { return "exclamationmark.triangle.fill" }
        if needsContent { return "folder.badge.questionmark" }
        if connector.id != "obsidian" { return "link.circle.fill" }
        return connected ? "checkmark.seal.fill" : "link.badge.plus"
    }

    private var statusChipIcon: String {
        if needsAttention { return "exclamationmark.circle.fill" }
        if needsContent { return "exclamationmark.circle.fill" }
        if connector.id != "obsidian" { return connector.connectorReadinessStatus == "token-ready" ? "key.fill" : "link.circle" }
        return connected ? "checkmark.circle.fill" : "folder.badge.plus"
    }

    private var statusColor: Color {
        if needsAttention { return .orange }
        if needsContent { return .orange }
        if connector.id != "obsidian" { return connector.connectorReadinessStatus == "token-ready" ? .accentColor : .secondary }
        return connected ? .green : .accentColor
    }

    private var statusDetail: String {
        if connector.id != "obsidian" {
            return connector.connectorReadinessStatus == "token-ready"
                ? "Read-only token sync is available in Connections & Privacy."
                : "This source is managed from Connections & Privacy."
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
            return "Cortex keeps these notes synced. Review approves memory before Ask uses it."
        }
        return "Choose a notes folder once. Cortex syncs locally and keeps citations attached."
    }

    private var primaryButtonTitle: String {
        if needsAttention { return "Fix notes" }
        if needsContent { return "Choose notes" }
        if connected {
            return state.hasConnectedObsidianVault ? "Sync notes" : "Reconnect notes"
        }
        return "Connect notes"
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
