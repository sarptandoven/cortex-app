import Foundation
import SwiftUI
import UniformTypeIdentifiers

struct SourcesTab: View {
    @ObservedObject var state: AppState
    @State private var isSupportedSourcesExpanded = false
    @State private var isSecondaryCaptureExpanded = false
    @State private var isImportHistoryExpanded = false
    @State private var isInboxExpanded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                SourcesImportSection(
                    state: state,
                    handleDrop: handleDrop
                )

                VStack(alignment: .leading, spacing: 8) {
                    SourcesInboxImportSection(state: state, isExpanded: $isInboxExpanded)
                    SourceCatalogDisclosureSection(state: state, isExpanded: $isSupportedSourcesExpanded)

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
                    .background(Color(nsColor: .windowBackgroundColor))
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
                    .clipShape(RoundedRectangle(cornerRadius: 8))

                    SecondaryCaptureToolsSection(state: state, isExpanded: $isSecondaryCaptureExpanded)
                }
                .padding(.top, 2)
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
            SourcesDisclosureLabel(
                systemImage: "plus.square.dashed",
                title: "Secondary capture tools",
                detail: "Quick notes and links for edge cases"
            )
        }
        .padding(12)
        .background(Color(nsColor: .windowBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct SourcesInboxImportSection: View {
    @ObservedObject var state: AppState
    @Binding var isExpanded: Bool

    var body: some View {
        DisclosureGroup(isExpanded: $isExpanded) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Use the inbox when another app needs a stable folder to drop exports into.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
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
        } label: {
            SourcesDisclosureLabel(
                systemImage: "tray",
                title: "Inbox import",
                detail: "Stable folder for external exports"
            )
        }
        .padding(12)
        .background(Color(nsColor: .windowBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct SourceCatalogDisclosureSection: View {
    @ObservedObject var state: AppState
    @Binding var isExpanded: Bool

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
        DisclosureGroup(isExpanded: $isExpanded) {
            VStack(alignment: .leading, spacing: 12) {
                SourceHealthSummarySection(state: state)
                Divider()
                SupportedSourceGroupsSection(groups: supportedGroups, isLoading: state.sourceConnectorCatalog.isEmpty)
            }
                .padding(.top, 8)
        } label: {
            SourcesDisclosureLabel(
                systemImage: "list.bullet.rectangle",
                title: "Supported sources",
                detail: "Readiness, coverage, and import options"
            )
        }
        .padding(12)
        .background(Color(nsColor: .windowBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct SourcesImportSection: View {
    @ObservedObject var state: AppState
    let handleDrop: ([NSItemProvider]) -> Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Import sources")
                        .font(.title3)
                        .fontWeight(.semibold)
                    Text("Pick one readable export or file to start. Cortex scans locally and sends detected memory to Review before it affects the model.")
                        .font(.callout)
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
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .help("Refresh source status")
            }

            SourcesDropZone(state: state, handleDrop: handleDrop)

            HStack {
                Button {
                    state.chooseFilesForCapture()
                } label: {
                    Label("Choose File or Export", systemImage: "doc.badge.plus")
                }
                .buttonStyle(.borderedProminent)

                Spacer()
            }

            SourcesLastImportResult(summary: state.lastFileCaptureSummary)
            SourcesReviewNextAction(state: state)
        }
        .padding(14)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct SourcesDropZone: View {
    @ObservedObject var state: AppState
    let handleDrop: ([NSItemProvider]) -> Bool

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: state.captureDropTargeted ? "arrow.down.doc.fill" : "arrow.down.doc")
                .font(.largeTitle)
                .foregroundColor(state.captureDropTargeted ? .accentColor : .secondary)
            Text(state.captureDropTargeted ? "Drop to import" : "Drop exports or files here")
                .font(.headline)
            Text("AI chat exports, notes, docs, messages, and writing samples are good first sources.")
                .font(.caption)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, minHeight: 146)
        .background(Color(nsColor: .textBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(state.captureDropTargeted ? Color.accentColor : Color.secondary.opacity(0.22), lineWidth: state.captureDropTargeted ? 2 : 1))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .onDrop(of: [UTType.fileURL.identifier], isTargeted: $state.captureDropTargeted, perform: handleDrop)
    }
}

struct SourcesLastImportResult: View {
    let summary: String

    var body: some View {
        HStack(alignment: .top, spacing: 9) {
            Image(systemName: summary.isEmpty ? "clock" : "checkmark.circle.fill")
                .foregroundColor(summary.isEmpty ? .secondary : .green)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text("Last import result")
                    .font(.callout)
                    .fontWeight(.medium)
                Text(summary.isEmpty ? "No import yet. Choose or drop one source to preview it." : summary)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(10)
        .background(Color(nsColor: .textBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct SourcesReviewNextAction: View {
    @ObservedObject var state: AppState

    private var pendingCount: Int {
        max(state.inbox.count, state.review?.stats.pending_captures ?? 0)
    }

    private var detail: String {
        if pendingCount > 0 {
            return "\(pendingCount) item\(pendingCount == 1 ? "" : "s") waiting for approval or archive."
        }
        if state.lastFileCaptureSummary.isEmpty {
            return "After import, Review will show anything that needs approval."
        }
        return "No review items yet. Try another source or refresh Review if import just finished."
    }

    private var buttonTitle: String {
        pendingCount > 0 ? "Review Pending" : "Open Review"
    }

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            Image(systemName: "checklist")
                .foregroundColor(.accentColor)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 2) {
                Text("Next: Review")
                    .font(.callout)
                    .fontWeight(.semibold)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 12)
            Button {
                state.selectedTab = .review
                state.status = "Review new signals below"
            } label: {
                Label(buttonTitle, systemImage: "arrow.right")
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(10)
        .background(Color.accentColor.opacity(0.08))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.accentColor.opacity(0.2)))
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
                title: isLoading ? "Loading supported sources" : "Supported sources unavailable",
                detail: isLoading ? "Cortex is checking supported imports and readiness." : "Refresh Sources after the local backend is healthy."
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
