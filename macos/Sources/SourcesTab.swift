import Foundation
import SwiftUI
import UniformTypeIdentifiers

struct SourcesTab: View {
    @ObservedObject var state: AppState
    @State private var isSupportedSourcesExpanded = false
    @State private var isSecondaryCaptureExpanded = false
    @State private var isSourceHealthExpanded = false
    @State private var isImportHistoryExpanded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                SourcesHeroSection(state: state)
                SourcesImportSection(
                    state: state,
                    isSupportedSourcesExpanded: $isSupportedSourcesExpanded,
                    handleDrop: handleDrop
                )
                DisclosureGroup(isExpanded: $isSourceHealthExpanded) {
                    SourceHealthSummarySection(state: state)
                        .padding(.top, 8)
                } label: {
                    SourcesDisclosureLabel(
                        systemImage: "checkmark.seal",
                        title: "Source health",
                        detail: "Import readiness and source coverage"
                    )
                }
                .padding(12)
                .background(Color(nsColor: .controlBackgroundColor).opacity(0.65))
                .clipShape(RoundedRectangle(cornerRadius: 8))

                DisclosureGroup(isExpanded: $isImportHistoryExpanded) {
                    ImportHistorySection(state: state)
                        .padding(.top, 8)
                } label: {
                    SourcesDisclosureLabel(
                        systemImage: "clock.arrow.circlepath",
                        title: "Import history",
                        detail: "Recent imports and undo controls"
                    )
                }
                .padding(12)
                .background(Color(nsColor: .controlBackgroundColor).opacity(0.65))
                .clipShape(RoundedRectangle(cornerRadius: 8))

                SecondaryCaptureToolsSection(state: state, isExpanded: $isSecondaryCaptureExpanded)
            }
            .padding(16)
        }
        .task {
            await state.loadTrust()
            if state.sourceConnectorCatalog.isEmpty {
                await state.loadSourceConnectivity()
            }
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

struct SourcesDisclosureLabel: View {
    let systemImage: String
    let title: String
    let detail: String

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: systemImage)
                .foregroundColor(.secondary)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(.callout)
                    .fontWeight(.medium)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            Spacer()
        }
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
                    if state.sourceConnectorCatalog.isEmpty {
                        await state.loadSourceConnectivity()
                    }
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
    @State private var isInboxExpanded = false

    private var supportedGroups: [SupportedSourceGroup] {
        var readinessBySource: [String: SourceReadinessItem] = [:]
        for source in state.sourceReadinessReport?.sources ?? [] {
            readinessBySource[source.source] = source
        }
        let grouped = Dictionary(grouping: state.sourceConnectorCatalog) { item in
            item.category ?? "Other"
        }
        return grouped.map { title, items in
            SupportedSourceGroup(
                title: title,
                items: items
                    .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
                    .map { SupportedSourceDisplayItem(catalog: $0, readiness: readinessBySource[$0.id]) }
            )
        }
        .sorted { lhs, rhs in
            sourceCategoryRank(lhs.title) < sourceCategoryRank(rhs.title)
        }
    }

    private func sourceCategoryRank(_ title: String) -> String {
        let ranks = [
            "AI chats": "00",
            "Email": "01",
            "Docs": "02",
            "Notes": "03",
            "Work chat": "04",
            "Messages": "05",
            "Calendar": "06",
            "People": "07",
            "Work tools": "08",
            "Research": "09",
        ]
        return "\(ranks[title] ?? "99")-\(title)"
    }

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

                Spacer()
            }

            if !state.lastFileCaptureSummary.isEmpty {
                Text(state.lastFileCaptureSummary)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            DisclosureGroup("Inbox import", isExpanded: $isInboxExpanded) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Use the inbox when another app needs a stable folder to drop exports into.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    HStack {
                        Button {
                            state.importCaptureInbox()
                        } label: {
                            Label("Import Inbox", systemImage: "tray.and.arrow.down")
                        }
                        Button {
                            state.openCaptureInbox()
                        } label: {
                            Label("Open Folder", systemImage: "tray")
                        }
                        Button {
                            state.copyCaptureInboxPath()
                        } label: {
                            Label("Copy Path", systemImage: "doc.on.doc")
                        }
                        Spacer()
                    }
                }
                .padding(.top, 8)
            }

            DisclosureGroup("Supported source types", isExpanded: $isSupportedSourcesExpanded) {
                SupportedSourceGroupsSection(groups: supportedGroups, isLoading: state.sourceConnectorCatalog.isEmpty)
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
    let items: [SupportedSourceDisplayItem]
}

struct SupportedSourceDisplayItem: Identifiable {
    let id: String
    let name: String
    let status: String
    let statusTitle: String
    let detail: String
    let sourceIds: [String]
    let formats: [String]

    init(catalog: SourceConnectorCatalogItem, readiness: SourceReadinessItem?) {
        id = catalog.id
        name = catalog.name
        sourceIds = catalog.source_ids ?? [catalog.id]
        formats = catalog.formats ?? []
        if let readiness {
            status = readiness.status
            statusTitle = readiness.statusTitle
            detail = readiness.next_action
        } else if catalog.isImportReady && catalog.isLivePlanned {
            status = "planned_import"
            statusTitle = "Export now"
            detail = "\(catalog.import_label ?? "Import exported files today"); live sync is planned."
        } else if catalog.isImportReady {
            status = "import_ready"
            statusTitle = "Import ready"
            detail = catalog.import_label ?? "Import exported files or folders."
        } else if catalog.isLivePlanned {
            status = "planned"
            statusTitle = "Live planned"
            detail = catalog.notes ?? "Live connection is planned."
        } else if (catalog.live_status ?? "").lowercased() == "export_only" {
            status = "export_only"
            statusTitle = "Export"
            detail = catalog.import_label ?? catalog.notes ?? "Use an exported file."
        } else {
            status = catalog.live_status ?? "available"
            statusTitle = "Available"
            detail = catalog.notes ?? "Add this source when it contains useful memory."
        }
    }

    var statusColor: Color {
        switch status {
        case "needs_attention": return .orange
        case "needs_review": return .yellow
        case "synced", "connected": return .green
        case "imported", "import_ready", "planned_import", "export_only": return .accentColor
        case "planned": return .secondary
        default: return .secondary
        }
    }

    var systemImage: String {
        switch status {
        case "needs_attention": return "exclamationmark.triangle.fill"
        case "needs_review": return "tray.full.fill"
        case "synced": return "checkmark.seal.fill"
        case "connected": return "link.circle.fill"
        case "imported": return "tray.and.arrow.down.fill"
        case "import_ready", "planned_import", "export_only": return "square.and.arrow.down.fill"
        case "planned": return "calendar.badge.clock"
        default: return "circle"
        }
    }

    var sourceSummary: String {
        let ids = sourceIds.prefix(3).joined(separator: ", ")
        if sourceIds.count > 3 {
            return "\(ids), +\(sourceIds.count - 3)"
        }
        return ids
    }
}

struct SupportedSourceGroupsSection: View {
    let groups: [SupportedSourceGroup]
    let isLoading: Bool

    var body: some View {
        if groups.isEmpty {
            QuietState(
                title: isLoading ? "Loading source catalog" : "Source catalog unavailable",
                detail: isLoading ? "Cortex is checking supported imports." : "Refresh Sources after the local backend is healthy."
            )
        } else {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(groups, id: \.title) { group in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(group.title)
                            .font(.caption)
                            .fontWeight(.semibold)
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 240), spacing: 8)], alignment: .leading, spacing: 8) {
                            ForEach(group.items) { item in
                                SupportedSourceCatalogRow(item: item)
                            }
                        }
                    }
                }
            }
        }
    }
}

struct SupportedSourceCatalogRow: View {
    let item: SupportedSourceDisplayItem

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: item.systemImage)
                .foregroundColor(item.statusColor)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(item.name)
                        .font(.callout)
                        .fontWeight(.medium)
                        .lineLimit(1)
                    Text(item.statusTitle)
                        .font(.caption2)
                        .foregroundColor(item.statusColor)
                        .lineLimit(1)
                    Spacer(minLength: 0)
                }
                Text(item.detail)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 4)
    }
}
