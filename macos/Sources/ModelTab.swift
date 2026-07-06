import SwiftUI
import AppKit

struct ModelTab: View {
    @ObservedObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                if let progress = state.syncProgress, progress.active {
                    SyncProgressCard(progress: progress)
                        .transition(.opacity.combined(with: .move(edge: .top)))
                }
                HomeHeroSection(state: state, review: state.review)
                if state.mirrorInsight != nil {
                    MirrorMomentCard(state: state)
                        .transition(.opacity.combined(with: .move(edge: .top)))
                }
                if let profile = state.profile, !profile.sections.isEmpty {
                    VStack(alignment: .leading, spacing: CortexDesign.Space.sm) {
                        SectionHeader(
                            title: "What Cortex has learned",
                            detail: ""
                        )
                        if let segments = profileStampSegments(for: profile) {
                            AccessionStamp(segments: segments)
                                .accessibilityElement(children: .ignore)
                                .accessibilityLabel(segments.joined(separator: ", "))
                        }
                    }
                    .padding(.top, CortexDesign.Space.xl)
                    ForEach(profile.sections) { section in
                        ProfileCard(section: section)
                    }
                    if let footnote = limitationsFootnote(for: profile) {
                        Text(footnote)
                            .font(CortexDesign.Typography.prose(13).italic())
                            .lineSpacing(3)
                            .foregroundColor(CortexDesign.inkFaint)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: 620, alignment: .leading)
                            .accessibilityLabel("Note: \(footnote)")
                    }
                }
            }
            .frame(maxWidth: 760, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.horizontal, 32)
            .padding(.vertical, 28)
            .animation(.easeInOut(duration: 0.25), value: state.mirrorInsight)
            .animation(.easeInOut(duration: 0.25), value: state.syncProgress)
        }
        .background(CortexDesign.appBackground)
    }

    /// The profile's own accession line — "PROFILE READINESS · 62/100 · COMPILED · 6 JUL 2026".
    /// Readiness disappears at 100 (silence = confidence); the compiled date stays as provenance.
    /// Never fabricate a segment; nil hides the stamp entirely.
    private func profileStampSegments(for profile: ProfileResponse) -> [String]? {
        var segments: [String] = []
        if let readiness = profile.readiness, readiness < 100 {
            segments.append("Profile readiness")
            segments.append("\(readiness)/100")
        }
        if let compiled = ModelTab.compiledDateText(profile.generatedAt) {
            segments.append("Compiled")
            segments.append(compiled)
        }
        return segments.isEmpty ? nil : segments
    }

    /// One marginal note at the foot of the profile column: the first couple of backend
    /// limitations, joined. No card, no icon — just faint serif italic in the margin.
    private func limitationsFootnote(for profile: ProfileResponse) -> String? {
        let notes = (profile.limitations ?? [])
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .prefix(2)
        return notes.isEmpty ? nil : notes.joined(separator: " ")
    }

    /// Parses the backend's ISO-8601 `generated_at` into "6 Jul 2026" (AccessionStamp applies
    /// the stamp casing). Returns nil when absent or unparseable.
    static func compiledDateText(_ raw: String?) -> String? {
        guard let value = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else {
            return nil
        }
        let iso = ISO8601DateFormatter()
        var date = iso.date(from: value)
        if date == nil {
            iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            date = iso.date(from: value)
        }
        guard let date else { return nil }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "d MMM yyyy"
        return formatter.string(from: date)
    }
}

/// A real, changing sync-progress bar (determinate, driven by the job queue) shown while Cortex
/// is turning newly-synced content into cited memory. The "learning about you" panel below it
/// refreshes live as the queue drains, so the user watches memory build in real time.
struct SyncProgressCard: View {
    let progress: SyncProgress

    /// "Syncing Notes…" when the queue names what it is working on; the generic label otherwise.
    private var title: String {
        if let detail = progress.detail, !detail.isEmpty {
            return "Syncing \(detail)…"
        }
        return "Syncing your memory…"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: CortexDesign.Space.sm) {
                Text(title)
                    .font(.callout)
                    .fontWeight(.semibold)
                    .foregroundColor(CortexDesign.ink)
                Spacer(minLength: 0)
                if progress.total > 0 {
                    Text("\(progress.done) of \(progress.total)")
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.8)
                        .foregroundColor(CortexDesign.inkFaint)
                        .accessibilityHidden(true)
                }
            }
            ProgressView(value: progress.fraction)
                .progressViewStyle(.linear)
                .tint(CortexDesign.gold)
        }
        .cortexCard(padding: 14, background: CortexDesign.goldSoft)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            progress.total > 0
                ? "\(title.replacingOccurrences(of: "…", with: "")), \(progress.done) of \(progress.total)"
                : title.replacingOccurrences(of: "…", with: "")
        )
    }
}

/// The "Mirror Moment" — the "holy-shit, it knows me" beat. Surfaces the single thing
/// Cortex learned about the user, in their words, with its source, and lets them confirm
/// or dismiss in one tap. Only rendered when `state.mirrorInsight != nil`; if the backend
/// abstains, this view is never shown (no empty card).
struct MirrorMomentCard: View {
    @ObservedObject var state: AppState

    private var insight: MirrorInsight? { state.mirrorInsight }

    /// A calm, human caption naming the source and how many times Cortex saw it,
    /// e.g. "From your calendar · seen 6 times". Degrades gracefully when the
    /// backend omits parts of the evidence.
    private var evidenceCaption: String? {
        guard let evidence = insight?.evidence else { return nil }
        var parts: [String] = []
        if let source = evidence.source, !source.isEmpty {
            parts.append("From your \(source)")
        }
        if let count = evidence.count, count > 0 {
            parts.append("seen \(count) time\(count == 1 ? "" : "s")")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    var body: some View {
        if let insight {
            VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
                Text("CORTEX NOTICED")
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.accent)
                    .accessibilityHidden(true)

                Text(insight.headline)
                    .font(CortexDesign.Typography.display(22))
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 620, alignment: .leading)

                if let evidenceCaption {
                    Label(evidenceCaption, systemImage: "doc.text.magnifyingglass")
                        .font(.callout)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                HStack(spacing: CortexDesign.Space.sm) {
                    Button {
                        state.confirmMirrorInsight()
                    } label: {
                        Label("That's right", systemImage: "checkmark")
                            .frame(minHeight: CortexDesign.controlHeight - 8)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .accessibilityLabel("That's right, this is accurate")

                    Button {
                        state.dismissMirrorInsight()
                    } label: {
                        Label("Not quite", systemImage: "xmark")
                            .frame(minHeight: CortexDesign.controlHeight - 8)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)
                    .accessibilityLabel("Not quite, dismiss this")

                    Spacer(minLength: 0)
                }
            }
            .cortexCard(background: CortexDesign.accentSoft)
            .frame(maxWidth: 620, alignment: .leading)
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Cortex noticed: \(insight.headline)")
        }
    }
}

/// One card in the "What Cortex knows about you" Personal Profile stack. Each card is a
/// single section (how you work, preferences, ...): a confident one-line statement plus a
/// few quiet grounding rows. Confidence is a three-band mark: settled (high) facts carry no
/// chip, a pattern still taking shape (medium) is flagged with a secondary "Emerging" pill,
/// and a first hint (low) gets a fainter "Early signal" pill. Rows whose
/// element carries a source_url can be opened. This view assumes the caller only renders it
/// for non-empty profiles; a section with no statement and no elements shows just its title.
struct ProfileCard: View {
    let section: ProfileSection

    @State private var showSources = false

    /// At most three grounding elements, and only those with something to show.
    private var visibleElements: [ProfileElement] {
        Array(section.elements.filter { ($0.text?.isEmpty == false) }.prefix(3))
    }

    /// The lowest confidence band; medium (and unknown) stay "Emerging" as before.
    private var isEarlySignal: Bool {
        (section.confidence ?? "").lowercased() == "low"
    }

    private var confidencePill: some View {
        CortexStatusPill(
            label: isEarlySignal ? "Early signal" : "Emerging",
            systemImage: "sparkles",
            color: isEarlySignal ? CortexDesign.inkFaint : CortexDesign.inkSecondary
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
            HStack(alignment: .firstTextBaseline, spacing: CortexDesign.Space.sm) {
                Text(section.title.uppercased())
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.accent)
                Spacer(minLength: 0)
                if !section.isConfident {
                    confidencePill
                        .accessibilityHidden(true)
                }
            }

            if let statement = section.statement, !statement.isEmpty {
                Text(statement)
                    .font(CortexDesign.Typography.prose(15))
                    .lineSpacing(3)
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 620, alignment: .leading)
            }

            if !visibleElements.isEmpty {
                Button {
                    withAnimation(.easeOut(duration: 0.2)) { showSources.toggle() }
                } label: {
                    HStack(spacing: CortexDesign.Space.xs) {
                        Image(systemName: "chevron.right")
                            .font(.caption2.weight(.semibold))
                            .rotationEffect(.degrees(showSources ? 90 : 0))
                        Text(showSources ? "Hide sources" : "Where this comes from")
                    }
                    .font(.caption)
                    .fontWeight(.medium)
                    .foregroundColor(CortexDesign.accent)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(showSources ? "Hide sources" : "Show sources")

                if showSources {
                    VStack(alignment: .leading, spacing: CortexDesign.Space.sm) {
                        ForEach(visibleElements) { element in
                            ProfileElementRow(element: element)
                        }
                    }
                    .transition(.opacity)
                }
            }
        }
        .padding(.leading, 10)
        .cortexCard()
        .archiveSpine(CortexDesign.accent)
        .frame(maxWidth: 620, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(accessibilityLabel)
    }

    private var accessibilityLabel: String {
        let prefix: String
        if section.isConfident {
            prefix = section.title
        } else if isEarlySignal {
            prefix = "\(section.title), Early signal"
        } else {
            prefix = "\(section.title), Emerging"
        }
        if let statement = section.statement, !statement.isEmpty {
            return "\(prefix): \(statement)"
        }
        return prefix
    }
}

/// A quiet grounding row under a profile statement: the observed snippet in quotes and a
/// caption naming the source and how often Cortex saw it. When the element carries a
/// source_url, the whole row becomes a button that opens it.
private struct ProfileElementRow: View {
    let element: ProfileElement

    /// "From your calendar · seen 6 times" — degrades gracefully when parts are missing.
    private var sourceCaption: String? {
        var parts: [String] = []
        if let source = element.source, !source.isEmpty {
            parts.append("From your \(source)")
        }
        if let count = element.count, count > 0 {
            parts.append("seen \(count) time\(count == 1 ? "" : "s")")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private var openableURL: URL? {
        ProfileElementRow.resolveURL(element.sourceURL)
    }

    var body: some View {
        if let url = openableURL {
            Button {
                NSWorkspace.shared.open(url)
            } label: {
                rowContent
            }
            .buttonStyle(.plain)
            .accessibilityLabel(accessibilityLabel)
            .accessibilityHint("Opens the source")
        } else {
            rowContent
                .accessibilityElement(children: .combine)
                .accessibilityLabel(accessibilityLabel)
        }
    }

    private var rowContent: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.xs) {
            if let text = element.text, !text.isEmpty {
                Text("\u{201C}\(text)\u{201D}")
                    .font(.callout)
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            if let sourceCaption {
                Text(sourceCaption)
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.vertical, CortexDesign.Space.xs)
        .padding(.horizontal, CortexDesign.Space.sm)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(CortexDesign.quietBackground)
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous))
        .contentShape(Rectangle())
    }

    private var accessibilityLabel: String {
        var parts: [String] = []
        if let text = element.text, !text.isEmpty { parts.append(text) }
        if let sourceCaption { parts.append(sourceCaption) }
        return parts.isEmpty ? "Profile detail" : parts.joined(separator: ". ")
    }

    /// Turns a backend `source_url` into an openable URL. Handles Cortex's custom
    /// `local-file://` scheme (a local path), plus ordinary `file://` and web URLs.
    /// Returns nil for empty or unusable values so the row stays non-interactive.
    static func resolveURL(_ raw: String?) -> URL? {
        guard let value = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else {
            return nil
        }
        let localPrefix = "local-file://"
        if value.hasPrefix(localPrefix) {
            var path = String(value.dropFirst(localPrefix.count))
            if let hashIndex = path.firstIndex(of: "#") {
                path = String(path[..<hashIndex])
            }
            if let queryIndex = path.firstIndex(of: "?") {
                path = String(path[..<queryIndex])
            }
            let decoded = path.removingPercentEncoding ?? path
            let trimmed = decoded.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : URL(fileURLWithPath: trimmed)
        }
        return URL(string: value)
    }
}

struct HomeHeroSection: View {
    @ObservedObject var state: AppState
    let review: DailyReviewResponse?

    private var activeSources: Int {
        if let connected = state.sourceReadinessReport?.summary.connected {
            return connected
        }
        return state.connectedSourceAccounts.filter { account in
            !account.needsContent
        }.count
    }

    private var hasEmptySource: Bool {
        if let report = state.sourceReadinessReport {
            return report.sources.contains { $0.status == "empty" }
        }
        return state.activeSourceAccounts.contains { account in
            account.needsContent
        }
    }

    private var needsAttentionSources: Int {
        state.sourceReadinessReport?.summary.needs_attention ?? 0
    }

    private var dueSyncSources: Int {
        state.sourceReadinessReport?.sources.filter { $0.sync_plan?.due_now == true }.count ?? 0
    }

    // Actively syncing/processing right now — distinct from "connected and waiting", so the UI
    // never claims a sync is running when nothing is.
    private var syncingSources: Int {
        guard let summary = state.sourceReadinessReport?.summary else { return 0 }
        return (summary.syncing ?? 0) + ((summary.processing ?? 0) > 0 ? 1 : 0)
    }

    private var memoryCount: Int {
        review?.stats.memories ?? state.stats?.memories ?? 0
    }

    private var pendingCount: Int {
        review?.stats.pending_captures ?? state.inbox.count
    }

    private var hasMemory: Bool {
        memoryCount > 0
    }

    private var obsidianConnector: SourceConnectorCatalogItem? {
        state.sourceConnectorCatalog.first { $0.id == "obsidian" }
    }

    private var canSyncSource: Bool {
        state.hasConnectedObsidianVault && obsidianConnector != nil
    }

    private var title: String {
        if !state.isLocalServiceReady {
            return "Cortex is starting"
        }
        if needsAttentionSources > 0 {
            return "Check your source connection"
        }
        if pendingCount > 0 {
            return "Review new memory"
        }
        if dueSyncSources > 0 {
            return "Refresh connected memory"
        }
        if hasMemory {
            return "Ask about your memory"
        }
        if syncingSources > 0 {
            return "Your source is syncing"
        }
        if activeSources > 0 {
            return "Your source is connected"
        }
        if hasEmptySource {
            return "Choose a source with content"
        }
        if state.connectedAIIntegrationCount > 0 {
            return "Connect your notes"
        }
        return "Connect your notes"
    }

    // Only problem and first-run states carry an explanation line; healthy states
    // let the display title speak alone.
    private var detail: String? {
        if !state.isLocalServiceReady {
            if CortexRecoveryText.needsAttention(state.displayStatus) {
                return state.displayStatus
            }
            return "This usually takes a moment."
        }
        if needsAttentionSources > 0 {
            return "Cortex keeps already synced memory local, but one or more sources need attention before fresh items arrive."
        }
        if pendingCount > 0 {
            return nil
        }
        if dueSyncSources > 0 {
            return nil
        }
        if hasMemory {
            return nil
        }
        if syncingSources > 0 {
            return nil
        }
        if activeSources > 0 {
            return nil
        }
        if hasEmptySource {
            return "Cortex could not find usable content there. Pick a source with real notes or records."
        }
        if state.connectedAIIntegrationCount > 0 {
            return "Connect notes so Ask can answer with citations."
        }
        return "Connect your notes once. Cortex keeps them synced and brings new memory to Review."
    }

    private var actionTitle: String {
        if !state.isLocalServiceReady { return "Start Cortex" }
        if needsAttentionSources > 0 { return "Open Connections" }
        if dueSyncSources > 0 { return "Sync now" }
        if activeSources == 0 { return hasEmptySource ? "Choose notes" : "Connect notes" }
        if pendingCount > 0 { return "Review memory" }
        if hasMemory { return "Ask a question" }
        return canSyncSource ? "Sync notes" : "View notes"
    }

    private var actionIcon: String {
        if !state.isLocalServiceReady { return "power" }
        if needsAttentionSources > 0 { return "exclamationmark.circle" }
        if dueSyncSources > 0 { return "arrow.triangle.2.circlepath" }
        if activeSources == 0 { return hasEmptySource ? "folder.badge.questionmark" : "folder.badge.plus" }
        if pendingCount > 0 { return "checklist" }
        if hasMemory { return "magnifyingglass" }
        return canSyncSource ? "arrow.triangle.2.circlepath" : "info.circle"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.lg) {
            VStack(alignment: .leading, spacing: CortexDesign.Space.sm) {
                Text(title)
                    .font(CortexDesign.Typography.display(30))
                    .foregroundColor(CortexDesign.ink)
                if let detail {
                    Text(detail)
                        .font(CortexDesign.Typography.body)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: 680, alignment: .leading)
                }
            }

            HStack(alignment: .center, spacing: CortexDesign.Space.md) {
                Button {
                    runNextAction()
                } label: {
                    Label(actionTitle, systemImage: actionIcon)
                        .frame(minWidth: 150, minHeight: 48)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(state.isBusy)

                Spacer(minLength: 0)
            }

            if state.isLocalServiceReady && activeSources == 0 && !hasMemory && !hasEmptySource && pendingCount == 0 {
                // Brand-new users get the product model in one glance instead of
                // two gray "not connected / no memory" rows.
                HStack(spacing: CortexDesign.Space.lg) {
                    HomeStep(number: 1, text: "Connect notes")
                    HomeStep(number: 2, text: "Review what it learns")
                    HomeStep(number: 3, text: "Ask, with sources")
                }
                .cortexCard(padding: CortexDesign.Space.md)
                .frame(maxWidth: 620, alignment: .leading)
            } else {
                HomeStatusRow(
                    title: "Source",
                    detail: sourceStatus.detail,
                    dotColor: sourceStatus.color,
                    action: { state.openConnectionsPrivacy(statusMessage: "Source status") }
                )
                .cortexCard()
                .frame(maxWidth: 620, alignment: .leading)
            }
        }
        .padding(.vertical, CortexDesign.Space.xl)
        .padding(.horizontal, CortexDesign.Space.xs)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // Ledger-row states are a small ink dot, not a colored icon: moss = healthy,
    // gold = pending/due, wax = needs attention, secondary ink = neutral.
    private var sourceStatus: (detail: String, color: Color) {
        if needsAttentionSources > 0 {
            return (needsAttentionSources == 1 ? "Source needs attention" : "\(needsAttentionSources) sources need attention", CortexDesign.accent)
        }
        if dueSyncSources > 0 {
            return (dueSyncSources == 1 ? "Sync due" : "Sync due for \(dueSyncSources) sources", CortexDesign.gold)
        }
        if syncingSources > 0 {
            return ("\(activeSources) connected, sync in progress", CortexDesign.gold)
        }
        if activeSources > 0 {
            return ("\(activeSources) connected", CortexDesign.sealMoss)
        }
        if hasEmptySource {
            return ("No usable content found", CortexDesign.accent)
        }
        return ("Notes not connected", CortexDesign.accent)
    }

    private func runNextAction() {
        if !state.isLocalServiceReady {
            Task {
                await state.ensureBackend()
                await state.loadDiagnostics()
                await state.loadReview()
                await state.loadStats()
            }
        } else if needsAttentionSources > 0 {
            state.openConnectionsPrivacy(statusMessage: "Check source connection")
        } else if dueSyncSources > 0 {
            state.openConnectionsPrivacy(statusMessage: "Sync connected sources")
        } else if activeSources == 0 {
            if let connector = obsidianConnector {
                state.connectLocalNotesFolder(connector, chooseNew: hasEmptySource)
            } else {
                state.openConnectionsPrivacy(statusMessage: "Connect notes")
            }
        } else if pendingCount > 0 {
            state.selectedTab = .review
            state.status = "Review memory"
        } else if hasMemory {
            state.selectedTab = .ask
            state.status = "Ask Cortex"
        } else {
            if canSyncSource, let connector = obsidianConnector {
                state.connectLocalNotesFolder(connector)
            } else {
                state.openConnectionsPrivacy(statusMessage: "Notes status")
            }
        }
    }
}

/// One numbered step in the first-run "connect -> review -> ask" map shown to
/// brand-new users in place of the Source/Memory status rows.
private struct HomeStep: View {
    let number: Int
    let text: String

    var body: some View {
        HStack(spacing: CortexDesign.Space.xs) {
            Text("\(number)")
                .font(.system(size: 13, weight: .semibold, design: .serif))
                .foregroundColor(CortexDesign.accent)
                .frame(width: 22, height: 22)
                .background(CortexDesign.accentSoft)
                .clipShape(Circle())
            Text(text)
                .font(.callout)
                .foregroundColor(CortexDesign.inkSecondary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Step \(number): \(text)")
    }
}

struct HomeStatusRow: View {
    let title: String
    let detail: String
    let dotColor: Color
    var action: (() -> Void)? = nil

    @State private var hovering = false

    var body: some View {
        if let action {
            Button(action: action) {
                row
            }
            .buttonStyle(.plain)
            .onHover { hovering = $0 }
            .background(
                RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                    .fill(hovering ? CortexDesign.quietBackground : Color.clear)
            )
            .accessibilityHint("Opens \(title.lowercased()) details")
        } else {
            row
        }
    }

    private var row: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(dotColor)
                .frame(width: 7, height: 7)
                .frame(width: 16, height: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.callout)
                    .fontWeight(.semibold)
                    .foregroundColor(CortexDesign.ink)
                Text(detail)
                    .font(.callout)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
            if action != nil {
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.semibold))
                    .foregroundColor(CortexDesign.inkSecondary)
                    .opacity(hovering ? 1 : 0.45)
            }
        }
        .padding(.vertical, 6)
        .frame(minHeight: 44, alignment: .leading)
        .contentShape(Rectangle())
    }
}
