import Foundation
import SwiftUI

struct SourcesTab: View {
    @ObservedObject var state: AppState
    @State private var isCoverageExpanded = false

    private var supportedGroups: [SupportedSourceGroup] {
        supportedSourceGroups(from: state)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                SourcesInteractionLayerSection(state: state)
                SourceHealthSummarySection(state: state)
                ConnectedSourceAccountsSection(state: state)
                DisclosureGroup(isExpanded: $isCoverageExpanded) {
                    SupportedSourceGroupsSection(groups: supportedGroups, isLoading: state.sourceConnectorCatalog.isEmpty)
                        .padding(.top, 8)
                } label: {
                    SourcesDisclosureLabel(
                        systemImage: "rectangle.connected.to.line.below",
                        title: "Connection coverage",
                        detail: "Supported services and sync readiness"
                    )
                }
                .padding(12)
                .background(Color(nsColor: .windowBackgroundColor))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color(nsColor: .separatorColor).opacity(0.35)))
                .clipShape(RoundedRectangle(cornerRadius: 8))
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

@MainActor private func supportedSourceGroups(from state: AppState) -> [SupportedSourceGroup] {
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

struct SourcesInteractionLayerSection: View {
    @ObservedObject var state: AppState

    private var activeAccounts: [SourceAccountItem] {
        state.sourceAccounts.filter { $0.disconnected_at == nil }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Data layer")
                        .font(.title3)
                        .fontWeight(.semibold)
                    Text("Cortex learns from connected accounts and direct AI tools, then sends useful memory to Review.")
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
                    }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .help("Refresh connection status")
            }

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 8)], spacing: 8) {
                SourceConnectivityMetric(title: "Accounts", value: "\(activeAccounts.count)", systemImage: "person.crop.circle.badge.checkmark", color: activeAccounts.isEmpty ? .secondary : .green)
                SourceConnectivityMetric(title: "Pending", value: "\(state.review?.stats.pending_captures ?? state.inbox.count)", systemImage: "checklist", color: state.inbox.isEmpty ? .secondary : .orange)
                SourceConnectivityMetric(title: "Memory", value: "\(state.stats?.memories ?? 0)", systemImage: "brain.head.profile", color: (state.stats?.memories ?? 0) == 0 ? .secondary : .accentColor)
            }

            HStack(alignment: .center, spacing: 10) {
                Image(systemName: activeAccounts.isEmpty ? "link.badge.plus" : "checkmark.seal.fill")
                    .foregroundColor(activeAccounts.isEmpty ? .accentColor : .green)
                    .frame(width: 24)
                VStack(alignment: .leading, spacing: 2) {
                    Text(activeAccounts.isEmpty ? "Connect once, sync automatically" : "Connected sources sync into Review")
                        .font(.callout)
                        .fontWeight(.semibold)
                    Text(activeAccounts.isEmpty ? "The normal path is account sign-in or direct tool setup. Recovery file handling stays out of the main workflow." : "Cortex keeps source health visible and waits for your approval before memory becomes active.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 12)
                Button {
                    state.selectedTab = .trust
                    state.status = "Open Trust to manage direct tool access"
                } label: {
                    Label("Open Trust", systemImage: "lock.shield")
                }
                .buttonStyle(.bordered)
            }
            .padding(10)
            .background(Color.accentColor.opacity(0.08))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.accentColor.opacity(0.2)))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .padding(14)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct ConnectedSourceAccountsSection: View {
    @ObservedObject var state: AppState

    private var activeAccounts: [SourceAccountItem] {
        state.sourceAccounts.filter { $0.disconnected_at == nil }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title: "Connected accounts", detail: "Sources that can keep memory current without file handling.")
            if activeAccounts.isEmpty {
                QuietState(title: "No accounts connected", detail: "Connect services or direct AI tools once; Cortex will sync useful signals into Review.")
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
    let accessDetail: String?
    let sourceIds: [String]
    let formats: [String]
    let readinessStatus: String

    init(catalog: SourceConnectorCatalogItem, readiness: SourceReadinessItem?) {
        let resolvedReadinessStatus = Self.normalizedReadinessStatus(
            readiness?.readiness_status ?? catalog.readiness_status,
            catalog: catalog,
            readiness: readiness
        )
        let permissionsRequired = Self.coalescedList(readiness?.permissions_required, catalog.permissions_required)
        let liveScopes = Self.coalescedList(readiness?.scopes, catalog.scopes)
        let fallbackStatus = Self.displayStatus(for: resolvedReadinessStatus)
        let fallbackStatusTitle = Self.displayTitle(for: resolvedReadinessStatus)
        let connectionDetail = Self.connectionDetail(
            readinessStatus: resolvedReadinessStatus,
            scopes: liveScopes,
            catalog: catalog
        )

        id = catalog.id
        name = catalog.name
        sourceIds = catalog.source_ids ?? [catalog.id]
        formats = catalog.formats ?? []
        readinessStatus = resolvedReadinessStatus
        accessDetail = Self.accessDetail(
            readinessStatus: resolvedReadinessStatus,
            permissionsRequired: permissionsRequired,
            scopes: liveScopes
        )
        if let readiness, Self.shouldPreserveReadinessStatus(readiness.status) {
            status = readiness.status
            statusTitle = readiness.statusTitle
            detail = Self.sanitizedAction(readiness.next_action, readinessStatus: resolvedReadinessStatus)
        } else if let readiness {
            status = fallbackStatus
            statusTitle = fallbackStatusTitle
            detail = connectionDetail ?? Self.sanitizedAction(readiness.next_action, readinessStatus: resolvedReadinessStatus)
        } else if catalog.isLivePlanned {
            status = fallbackStatus
            statusTitle = fallbackStatusTitle
            detail = connectionDetail ?? "Direct service connection requires account consent."
        } else if catalog.isImportReady {
            status = fallbackStatus
            statusTitle = fallbackStatusTitle
            detail = connectionDetail ?? "Local app connector can run without service sign-in."
        } else {
            status = fallbackStatus
            statusTitle = fallbackStatusTitle
            detail = connectionDetail ?? "Needs a direct connector before becoming a primary source."
        }
    }

    private static func shouldPreserveReadinessStatus(_ status: String) -> Bool {
        ["needs_attention", "needs_review", "synced", "connected"].contains(status)
    }

    private static func normalizedReadinessStatus(_ value: String?, catalog: SourceConnectorCatalogItem, readiness: SourceReadinessItem?) -> String {
        let explicit = (value ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if ["export-only", "import-ready", "live-planned"].contains(explicit) {
            return explicit
        }
        if let readiness {
            let liveStatus = readiness.live_status.lowercased()
            if liveStatus == "planned" {
                return "live-planned"
            }
            if readiness.supports_import == true || ["native", "generic", "import_ready"].contains(readiness.import_status.lowercased()) || !readiness.formats.isEmpty {
                return "import-ready"
            }
        }
        return catalog.connectorReadinessStatus
    }

    private static func displayStatus(for readinessStatus: String) -> String {
        switch readinessStatus {
        case "live-planned": return "planned_connection"
        case "import-ready": return "connector_ready"
        case "export-only": return "connector_needed"
        default: return "available"
        }
    }

    private static func displayTitle(for readinessStatus: String) -> String {
        switch readinessStatus {
        case "live-planned": return "Sign-in planned"
        case "import-ready": return "Local connector"
        case "export-only": return "Connector needed"
        default: return "Available"
        }
    }

    private static func coalescedList(_ preferred: [String]?, _ fallback: [String]?) -> [String] {
        let preferredList = cleanedList(preferred)
        if !preferredList.isEmpty {
            return preferredList
        }
        return cleanedList(fallback)
    }

    private static func cleanedList(_ values: [String]?) -> [String] {
        var seen: Set<String> = []
        var result: [String] = []
        for value in values ?? [] {
            let text = value.trimmingCharacters(in: .whitespacesAndNewlines)
            if text.isEmpty || seen.contains(text) {
                continue
            }
            seen.insert(text)
            result.append(text)
        }
        return result
    }

    private static func compactPermission(_ value: String?) -> String? {
        guard let text = compactCopy(value, removingPrefixes: ["First-100:", "First 100:", "Live-planned:", "Live planned:"]) else {
            return nil
        }
        let lowercased = text.lowercased()
        if lowercased.contains("oauth") || lowercased.contains("api consent") {
            return text.replacingOccurrences(of: "user OAuth/API consent for", with: "account consent:", options: [.caseInsensitive])
        }
        if lowercased.contains("local folder") || lowercased.contains("local file") || lowercased.contains("database copy") {
            return "local app access"
        }
        if lowercased.contains("service export") || lowercased.contains("import data") || lowercased.contains("selected") {
            return nil
        }
        return text
    }

    private static func compactCopy(_ value: String?, removingPrefixes prefixes: [String]) -> String? {
        var text = (value ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        for prefix in prefixes {
            if text.lowercased().hasPrefix(prefix.lowercased()) {
                text = String(text.dropFirst(prefix.count)).trimmingCharacters(in: .whitespacesAndNewlines)
                break
            }
        }
        text = text.trimmingCharacters(in: CharacterSet(charactersIn: ". "))
        return text.isEmpty ? nil : text
    }

    private static func accessDetail(readinessStatus: String, permissionsRequired: [String], scopes: [String]) -> String? {
        switch readinessStatus {
        case "live-planned":
            if !scopes.isEmpty {
                return "Needs account consent: \(scopeSummary(scopes))"
            }
            return permissionsRequired.compactMap(compactPermission).first
        case "import-ready":
            return permissionsRequired.compactMap(compactPermission).first
        default:
            return nil
        }
    }

    private static func connectionDetail(readinessStatus: String, scopes: [String], catalog: SourceConnectorCatalogItem) -> String? {
        switch readinessStatus {
        case "live-planned":
            if scopes.isEmpty {
                return "Direct service connection is the intended path."
            }
            return "Direct service connection requires account consent."
        case "import-ready":
            return "Local app connector can run without cloud sign-in."
        case "export-only":
            return "Needs a direct connector before becoming a primary source."
        default:
            return catalog.notes
        }
    }

    private static func sanitizedAction(_ text: String, readinessStatus: String) -> String {
        let lowercased = text.lowercased()
        if lowercased.contains("import") || lowercased.contains("export") || lowercased.contains("file") || lowercased.contains("folder") {
            switch readinessStatus {
            case "live-planned":
                return "Connect this source through account sign-in."
            case "import-ready":
                return "Connect this source through a local app integration."
            default:
                return "Needs a direct connector before becoming a primary source."
            }
        }
        return text
    }

    private static func scopeSummary(_ scopes: [String]) -> String {
        let prefix = scopes.prefix(3).joined(separator: ", ")
        if scopes.count > 3 {
            return "\(prefix), +\(scopes.count - 3)"
        }
        return prefix
    }

    var statusColor: Color {
        switch status {
        case "needs_attention": return .orange
        case "needs_review": return .yellow
        case "synced", "connected": return .green
        case "planned_connection": return .blue
        case "connector_ready": return .accentColor
        case "connector_needed": return .secondary
        default: return .secondary
        }
    }

    var systemImage: String {
        switch status {
        case "needs_attention": return "exclamationmark.triangle.fill"
        case "needs_review": return "tray.full.fill"
        case "synced": return "checkmark.seal.fill"
        case "connected": return "link.circle.fill"
        case "connector_ready": return "link.badge.plus"
        case "planned_connection": return "person.crop.circle.badge.plus"
        case "connector_needed": return "circle.dashed"
        default: return "circle"
        }
    }
}

struct SupportedSourceGroupsSection: View {
    let groups: [SupportedSourceGroup]
    let isLoading: Bool

    var body: some View {
        if groups.isEmpty {
            QuietState(
                title: isLoading ? "Loading connections" : "Connection coverage unavailable",
                detail: isLoading ? "Cortex is checking service readiness." : "Refresh Sources after the local service is healthy."
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
                    .lineLimit(2)
                if let accessDetail = item.accessDetail {
                    Text(accessDetail)
                        .font(.caption2)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 4)
    }
}
