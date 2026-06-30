import SwiftUI

struct SourceConnectorStatusCard: View {
    @ObservedObject var state: AppState
    let connector: SourceConnectorCatalogItem
    let connected: Bool
    var needsContent: Bool = false

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
                            Label("Change", systemImage: "folder")
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
                    Text("Direct connector not active in this beta")
                        .font(.callout)
                        .fontWeight(.medium)
                    Spacer()
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 8)
                .background(Color(nsColor: .windowBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, minHeight: 210, alignment: .topLeading)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var statusTitle: String {
        if needsContent { return "No notes found" }
        return connected ? "Connected" : "Local"
    }

    private var statusIcon: String {
        if needsContent { return "folder.badge.questionmark" }
        return connected ? "checkmark.seal.fill" : "link.badge.plus"
    }

    private var statusChipIcon: String {
        if needsContent { return "exclamationmark.circle.fill" }
        return connected ? "checkmark.circle.fill" : "folder.badge.plus"
    }

    private var statusColor: Color {
        if needsContent { return .orange }
        return connected ? .green : .accentColor
    }

    private var statusDetail: String {
        if needsContent {
            return "The last folder did not produce usable Markdown notes. Choose a notes folder with real content."
        }
        if connected {
            return "Cortex can resync these notes and send new memory to Review."
        }
        return "Choose a notes folder once. Cortex reads Markdown locally and keeps citations attached."
    }

    private var primaryButtonTitle: String {
        if needsContent { return "Choose notes" }
        return connected ? "Sync notes" : "Connect notes"
    }

    private var primaryButtonIcon: String {
        if needsContent { return "folder.badge.questionmark" }
        return connected ? "arrow.clockwise" : "folder.badge.plus"
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
