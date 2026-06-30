import SwiftUI

struct SourceConnectorStatusCard: View {
    @ObservedObject var state: AppState
    let connector: SourceConnectorCatalogItem
    let connected: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: connected ? "checkmark.seal.fill" : "link.badge.plus")
                    .font(.system(size: 30, weight: .semibold))
                    .foregroundColor(connected ? .green : .accentColor)
                    .frame(width: 42, height: 42)
                Spacer(minLength: 0)
                SourceStatusChip(
                    title: connected ? "Connected" : "Local",
                    systemImage: connected ? "checkmark.circle.fill" : "folder.badge.plus",
                    color: connected ? .green : .accentColor
                )
            }
            VStack(alignment: .leading, spacing: 5) {
                Text(connector.name)
                    .font(.title3)
                    .fontWeight(.semibold)
                Text(connected ? "Cortex can resync this vault and send new notes to Review." : "Choose an Obsidian vault once. Cortex reads Markdown notes locally and keeps citations attached.")
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if connector.id == "obsidian" {
                HStack(spacing: 8) {
                    Button {
                        state.connectLocalNotesFolder(connector)
                    } label: {
                        Label(connected ? "Sync vault" : "Connect vault", systemImage: connected ? "arrow.clockwise" : "folder.badge.plus")
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
