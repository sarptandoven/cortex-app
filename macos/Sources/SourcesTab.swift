import Foundation
import SwiftUI

struct SourcesTab: View {
    @ObservedObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                SourcesDemoHeader(state: state)
                IntegrationCenterView(state: state, compact: true)
                SourceQuickConnectSection(state: state)
                ConnectedSourceAccountsSection(state: state)
            }
            .padding(16)
        }
        .task {
            await state.loadTrust()
            if state.sourceConnectorCatalog.isEmpty {
                await state.loadSourceConnectivity()
            }
        }
    }
}

struct SourcesDemoHeader: View {
    @ObservedObject var state: AppState

    private var activeAccounts: Int {
        state.activeSourceAccounts.count
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: "link.circle.fill")
                    .font(.system(size: 38, weight: .semibold))
                    .foregroundColor(.accentColor)
                    .frame(width: 52, height: 52)
                VStack(alignment: .leading, spacing: 5) {
                    Text(activeAccounts > 0 ? "Sources are connected" : "Connect Cortex through your tools")
                        .font(.system(size: 25, weight: .semibold))
                    Text("Connect MCP and Obsidian once. Cortex keeps memory current, asks for review, and cites what it uses.")
                        .font(.callout)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }

            HStack(spacing: 8) {
                SourceStatusChip(
                    title: "Review first",
                    systemImage: "checklist",
                    color: .green
                )
                SourceStatusChip(
                    title: "Cited retrieval",
                    systemImage: "quote.bubble",
                    color: .accentColor
                )
                Spacer(minLength: 0)
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct SourceQuickConnectSection: View {
    @ObservedObject var state: AppState

    private var connectorsByID: [String: SourceConnectorCatalogItem] {
        Dictionary(uniqueKeysWithValues: state.sourceConnectorCatalog.map { ($0.id, $0) })
    }

    private var obsidianConnector: SourceConnectorCatalogItem? {
        connectorsByID["obsidian"]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionHeader(title: "Obsidian vault", detail: "Connect once. Cortex syncs useful notes into Review.")
            if state.sourceConnectorCatalog.isEmpty {
                QuietState(title: "Checking connectors", detail: "Cortex is reading the local source registry.")
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 290), spacing: 12)], spacing: 12) {
                    if let connector = obsidianConnector {
                        SourceConnectorStatusCard(
                            state: state,
                            connector: connector,
                            connected: isConnected(connector)
                        )
                    } else {
                        QuietState(title: "Obsidian connector unavailable", detail: "Restart Cortex after the local backend is healthy.")
                    }
                }
            }
        }
        .padding(14)
        .background(Color(nsColor: .windowBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func isConnected(_ connector: SourceConnectorCatalogItem) -> Bool {
        if connector.id == "obsidian", state.hasConnectedObsidianVault {
            return true
        }
        return state.activeSourceAccounts.contains { account in
            account.source == connector.id || (connector.source_ids ?? []).contains(account.source)
        }
    }
}

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

            if canConnectNow {
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
                    Text("Native connector not active in this beta")
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

    private var canConnectNow: Bool {
        connector.id == "obsidian"
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

struct ConnectedSourceAccountsSection: View {
    @ObservedObject var state: AppState

    private var activeAccounts: [SourceAccountItem] {
        state.sourceAccounts.filter { $0.disconnected_at == nil }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title: "Connected accounts", detail: "Live local connections that keep memory current.")
            if activeAccounts.isEmpty {
                QuietState(title: "No accounts connected", detail: "Connect an MCP client or Obsidian vault once; Cortex syncs useful signals into Review.")
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(activeAccounts.prefix(6)) { account in
                        SourceAccountHealthRow(
                            account: account,
                            cursor: state.syncCursors.first(where: { $0.source_account_id == account.id })
                        )
                    }
                }
            }
        }
        .padding(14)
        .background(Color(nsColor: .controlBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}
