import Foundation
import SwiftUI
import UniformTypeIdentifiers

struct SourcesTab: View {
    @ObservedObject var state: AppState
    @State private var isSupportedSourcesExpanded = false
    @State private var isSecondaryCaptureExpanded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                SourcesHeroSection(state: state)
                SourcesImportSection(
                    state: state,
                    isSupportedSourcesExpanded: $isSupportedSourcesExpanded,
                    handleDrop: handleDrop
                )
                SourceHealthSummarySection(state: state)
                ImportHistorySection(state: state)
                SecondaryCaptureToolsSection(state: state, isExpanded: $isSecondaryCaptureExpanded)
            }
            .padding(16)
        }
        .task {
            await state.loadTrust()
            await state.loadImportHistory()
        }
    }

    private func handleDrop(_ providers: [NSItemProvider]) -> Bool {
        let group = DispatchGroup()
        let lock = NSLock()
        var urls: [URL] = []
        for provider in providers {
            if provider.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) {
                group.enter()
                provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                    defer { group.leave() }
                    var url: URL?
                    if let itemURL = item as? URL {
                        url = itemURL
                    } else if let data = item as? Data,
                              let string = String(data: data, encoding: .utf8) {
                        url = URL(string: string)
                    } else if let string = item as? String {
                        url = URL(string: string)
                    }
                    if let url {
                        lock.lock()
                        urls.append(url)
                        lock.unlock()
                    }
                }
            }
        }
        group.notify(queue: .main) {
            state.captureDropTargeted = false
            state.captureFiles(urls)
        }
        return !providers.isEmpty
    }
}

struct SecondaryCaptureToolsSection: View {
    @ObservedObject var state: AppState
    @Binding var isExpanded: Bool

    var body: some View {
        DisclosureGroup(isExpanded: $isExpanded) {
            VStack(alignment: .leading, spacing: 14) {
                Text("Use these for one-off notes or links after the main source import. They do not replace source exports for onboarding or model coverage.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                CaptureQuickNoteSection(state: state)
                Divider()
                CaptureWebSection(state: state)
            }
            .padding(.top, 8)
        } label: {
            HStack(spacing: 9) {
                Image(systemName: "plus.square.dashed")
                    .foregroundColor(.secondary)
                    .frame(width: 20)
                VStack(alignment: .leading, spacing: 1) {
                    Text("Secondary capture tools")
                        .font(.callout)
                        .fontWeight(.medium)
                    Text("Quick notes and links for edge cases")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
            }
        }
        .padding(12)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.65))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct SourcesHeroSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Add sources")
                    .font(.title3)
                    .fontWeight(.semibold)
                Text("Import the real conversations, notes, documents, messages, and writing samples that should shape your private model.")
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            Button {
                Task {
                    await state.loadTrust()
                    await state.loadImportHistory()
                }
            } label: {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
        }
    }
}

struct SourcesImportSection: View {
    @ObservedObject var state: AppState
    @Binding var isSupportedSourcesExpanded: Bool
    let handleDrop: ([NSItemProvider]) -> Bool

    private let supportedGroups = [
        SupportedSourceGroup(title: "AI chats", items: ["ChatGPT", "Claude", "Gemini"]),
        SupportedSourceGroup(title: "Workspaces", items: ["Notion", "Google Drive", "Docs", "Slack", "Teams"]),
        SupportedSourceGroup(title: "Messages", items: ["Email", "Messages", "WhatsApp", "Discord", "Telegram"]),
        SupportedSourceGroup(title: "Personal data", items: ["Notes", "Calendar", "Contacts", "Bookmarks"]),
        SupportedSourceGroup(title: "Files", items: ["PDF", "DOCX", "RTF", "Markdown", "CSV", "JSON"])
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(spacing: 8) {
                Image(systemName: state.captureDropTargeted ? "arrow.down.doc.fill" : "arrow.down.doc")
                    .font(.largeTitle)
                    .foregroundColor(state.captureDropTargeted ? .accentColor : .secondary)
                Text(state.captureDropTargeted ? "Drop to import" : "Drop source exports here")
                    .font(.headline)
                Text("Cortex scans locally, previews detected records, then queues reviewable memory signals.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity, minHeight: 132)
            .background(Color(nsColor: .textBackgroundColor))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(state.captureDropTargeted ? Color.accentColor : Color.secondary.opacity(0.22), lineWidth: state.captureDropTargeted ? 2 : 1))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .onDrop(of: [UTType.fileURL.identifier], isTargeted: $state.captureDropTargeted, perform: handleDrop)

            HStack {
                Button {
                    state.chooseFilesForCapture()
                } label: {
                    Label("Choose Sources", systemImage: "doc.badge.plus")
                }
                .buttonStyle(.borderedProminent)

                Button {
                    state.importCaptureInbox()
                } label: {
                    Label("Import Inbox", systemImage: "tray.and.arrow.down")
                }

                Menu {
                    Button {
                        state.openCaptureInbox()
                    } label: {
                        Label("Open Inbox Folder", systemImage: "tray")
                    }
                    Button {
                        state.copyCaptureInboxPath()
                    } label: {
                        Label("Copy Inbox Path", systemImage: "doc.on.doc")
                    }
                } label: {
                    Label("Inbox", systemImage: "tray")
                }
                .menuStyle(.borderlessButton)

                Spacer()
            }

            if !state.lastFileCaptureSummary.isEmpty {
                Text(state.lastFileCaptureSummary)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            DisclosureGroup("Supported source types", isExpanded: $isSupportedSourcesExpanded) {
                SupportedSourceGroupsSection(groups: supportedGroups)
                    .padding(.top, 8)
            }
        }
        .padding(12)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct SupportedSourceGroup {
    let title: String
    let items: [String]
}

struct SupportedSourceGroupsSection: View {
    let groups: [SupportedSourceGroup]

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 8)], alignment: .leading, spacing: 8) {
            ForEach(groups, id: \.title) { group in
                VStack(alignment: .leading, spacing: 6) {
                    Text(group.title)
                        .font(.caption)
                        .fontWeight(.semibold)
                    Text(group.items.joined(separator: " · "))
                        .font(.caption2)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(9)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(nsColor: .textBackgroundColor))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
    }
}
