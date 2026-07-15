import SwiftUI

// The phases 5-6 surfaces, in the Archive's voice:
//  - ReviewProactiveAlertsSection: contradiction interrupts land in Review (budget-gated,
//    always dismissible, every resolution is a training label).
//  - ReviewTwinGradingSection: "Cortex predicted X — how did it go?" grading queue.
//  - AskTwinPredictionCard: cited would-I verdicts alongside the ordinary Ask answer.
//  - TwinScorecardCard: the twin's accuracy record on the Model tab.
//  - ConnectionsToolUsageSection: honest local metrics (tool scorecard, alert precision,
//    prefetch hit rate) plus the proactive-alert daily budget control.

/// ISO-8601 → "6 Jul 2026" for accession stamps. Nil when absent or unparseable — stamps
/// never fabricate a segment.
func phaseStampDate(_ raw: String?) -> String? {
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

// MARK: - Review: proactive alerts (Phase 6.1/6.3)

struct ReviewProactiveAlertsSection: View {
    @ObservedObject var state: AppState

    var body: some View {
        if !state.proactiveAlerts.isEmpty {
            VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
                SectionHeader(
                    title: "\(DistributionMode.appDisplayName) noticed",
                    detail: "New notes that disagree with trusted memory. Dismissals teach \(DistributionMode.appDisplayName) what not to flag."
                )
                ForEach(state.proactiveAlerts) { alert in
                    ReviewProactiveAlertCard(
                        alert: alert,
                        isInFlight: state.inFlightAlertIds.contains(alert.id),
                        resolve: { resolution in
                            state.resolveProactiveAlert(alert, resolution: resolution)
                        }
                    )
                    .transition(.asymmetric(
                        insertion: .opacity,
                        removal: .move(edge: .trailing).combined(with: .opacity)
                    ))
                }
            }
            .animation(.spring(response: 0.35, dampingFraction: 0.8), value: state.proactiveAlerts.map(\.id))
        }
    }
}

struct ReviewProactiveAlertCard: View {
    let alert: ProactiveAlertItem
    var isInFlight: Bool = false
    let resolve: (String) -> Void

    private var field: String? {
        let value = alert.detail?.field?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return value.isEmpty ? nil : value
    }

    private var isContradiction: Bool {
        alert.kind == "contradiction"
    }

    private var title: String {
        if let field {
            return "Your notes disagree on \(field)"
        }
        return alert.title
    }

    /// The explanation line is contradiction-specific; other alert kinds carry their
    /// whole story in the title, and a borrowed subtitle would be false.
    private var subtitle: String? {
        isContradiction
            ? "Only flagged because the existing memory is high-trust. Decide which is true."
            : nil
    }

    private var stampSegments: [String] {
        var segments = [alert.kind]
        if let date = phaseStampDate(alert.created_at) {
            segments.append(date)
        }
        if let trust = alert.detail?.existing?.trust_score {
            segments.append("Trust \(String(format: "%.1f", trust))")
        }
        return segments
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: isContradiction ? "arrow.left.arrow.right" : "bell")
                    .font(.headline)
                    .foregroundColor(CortexDesign.accent)
                    .frame(width: 28, height: 28)
                    .background(CortexDesign.accentSoft)
                    .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                        .font(CortexDesign.Typography.title)
                        .foregroundColor(CortexDesign.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    if let subtitle {
                        Text(subtitle)
                            .font(CortexDesign.Typography.caption)
                            .foregroundColor(CortexDesign.inkSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 0)
            }

            if let newClaim = alert.detail?.new?.claim, let knownClaim = alert.detail?.existing?.claim {
                VStack(alignment: .leading, spacing: 8) {
                    alertClaimRow(label: "New", claim: newClaim)
                    alertClaimRow(label: "Known", claim: knownClaim)
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(CortexDesign.quietBackground)
                .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
            }

            HStack(alignment: .center, spacing: 10) {
                AccessionStamp(segments: stampSegments)
                Spacer()
                if isInFlight {
                    ProgressView()
                        .controlSize(.small)
                }
                Button {
                    resolve("dismissed")
                } label: {
                    Label("Dismiss", systemImage: "xmark")
                }
                .buttonStyle(.bordered)
                .disabled(isInFlight)
                .help("Not worth flagging: \(DistributionMode.appDisplayName) stops raising pairs like this")
                Button {
                    resolve("accepted")
                } label: {
                    Label("Noted", systemImage: "checkmark.seal")
                }
                .buttonStyle(.borderedProminent)
                .disabled(isInFlight)
                .help("A real conflict worth knowing about")
            }
        }
        .padding(.leading, 10)
        .cortexCard(padding: 14, background: CortexDesign.panelBackground)
        .archiveSpine(CortexDesign.gold)
    }

    private func alertClaimRow(label: String, claim: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(label.uppercased())
                .font(CortexDesign.Typography.stamp)
                .kerning(0.8)
                .foregroundColor(label == "New" ? CortexDesign.accent : CortexDesign.inkFaint)
                .frame(width: 48, alignment: .leading)
            Text(claim)
                .font(CortexDesign.Typography.prose(14))
                .lineSpacing(3)
                .foregroundColor(CortexDesign.ink)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
        }
    }
}

// MARK: - Review: twin grading queue (Phase 5.4)

struct ReviewTwinGradingSection: View {
    @ObservedObject var state: AppState

    private static let visibleCount = 3

    private var ungraded: [TwinUngradedPrediction] {
        state.twinScorecard?.ungraded ?? []
    }

    var body: some View {
        if !ungraded.isEmpty {
            VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
                SectionHeader(
                    title: "Grade your twin",
                    detail: "\(DistributionMode.appDisplayName) made these predictions from your memory. Grading them builds an accuracy record you can check."
                )
                ForEach(ungraded.prefix(Self.visibleCount)) { prediction in
                    ReviewTwinGradingCard(
                        prediction: prediction,
                        isInFlight: state.inFlightTwinGradeIds.contains(prediction.id),
                        grade: { outcome in
                            state.gradeTwinPrediction(prediction, outcome: outcome)
                        }
                    )
                }
                if ungraded.count > Self.visibleCount {
                    Text("\(ungraded.count - Self.visibleCount) more prediction\(ungraded.count - Self.visibleCount == 1 ? "" : "s") waiting")
                        .font(CortexDesign.Typography.caption)
                        .foregroundColor(CortexDesign.inkFaint)
                }
            }
        }
    }
}

struct ReviewTwinGradingCard: View {
    let prediction: TwinUngradedPrediction
    var isInFlight: Bool = false
    let grade: (String) -> Void

    private var stampSegments: [String] {
        var segments = ["Predicted"]
        if let date = phaseStampDate(prediction.predicted_at) {
            segments.append(date)
        }
        return segments
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let question = prediction.question, !question.isEmpty {
                Text(question)
                    .font(.system(size: 14, design: .serif))
                    .italic()
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(prediction.verdictLabel)
                .font(CortexDesign.Typography.title)
                .foregroundColor(CortexDesign.ink)
            HStack(alignment: .center, spacing: 10) {
                AccessionStamp(segments: stampSegments)
                Spacer()
                if isInFlight {
                    ProgressView()
                        .controlSize(.small)
                }
                Button("Wrong") { grade("incorrect") }
                    .buttonStyle(.bordered)
                    .disabled(isInFlight)
                    .help("The real decision went the other way")
                Button("Can't say") { grade("unclear") }
                    .buttonStyle(.bordered)
                    .disabled(isInFlight)
                    .help("No clear outcome to grade against")
                Button("Right") { grade("correct") }
                    .buttonStyle(.borderedProminent)
                    .disabled(isInFlight)
                    .help("The prediction matched what you did")
            }
        }
        .cortexCard(padding: 14, background: CortexDesign.panelBackground)
    }
}

// MARK: - Ask: twin prediction card (Phase 5.1/5.3)

struct AskTwinPredictionCard: View {
    let prediction: TwinPredictionResponse

    private static let evidencePerBucket = 3

    private var isInsufficient: Bool {
        prediction.verdict == "insufficient_evidence"
    }

    /// Negative-layer evidence appears in `opposing` AND `hard_constraints` by construction.
    /// Show each memory once: constraints already rendered under AGAINST are not repeated,
    /// but the veto note still marks them below.
    private var visibleOpposing: [TwinEvidenceItem] {
        Array(prediction.opposing.prefix(Self.evidencePerBucket))
    }

    private var unseenConstraints: [TwinEvidenceItem] {
        let shown = Set(visibleOpposing.map(\.memory_id))
        return prediction.hard_constraints.filter { !shown.contains($0.memory_id) }
    }

    private var opposingContainsConstraint: Bool {
        let constraintIds = Set(prediction.hard_constraints.map(\.memory_id))
        return visibleOpposing.contains { constraintIds.contains($0.memory_id) }
    }

    private var footerSegments: [String] {
        var segments = ["Evidence \(prediction.evidence_count)"]
        if let date = phaseStampDate(prediction.generated_at) {
            segments.append(date)
        }
        return segments
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Text(verbatim: "YOUR TWIN")
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.accent)
                Spacer()
                AccessionStamp(segments: footerSegments)
            }

            Text(prediction.verdictLabel)
                .font(CortexDesign.Typography.display(20))
                .foregroundColor(CortexDesign.ink)

            Text(isInsufficient
                 ? "\(DistributionMode.appDisplayName) only predicts from cited memory, and there isn't enough on this yet. It won't invent a preference."
                 : prediction.rationale)
                .font(CortexDesign.Typography.body)
                .foregroundColor(CortexDesign.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)

            if !prediction.supporting.isEmpty {
                evidenceBucket(label: "For", items: prediction.supporting)
            }
            if !prediction.opposing.isEmpty {
                evidenceBucket(label: "Against", items: prediction.opposing)
            }
            if !prediction.hard_constraints.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    if !unseenConstraints.isEmpty {
                        Text(verbatim: "HARD LINES")
                            .font(CortexDesign.Typography.stamp)
                            .kerning(0.8)
                            .foregroundColor(CortexDesign.accent)
                        ForEach(unseenConstraints.prefix(Self.evidencePerBucket)) { item in
                            TwinEvidenceRow(item: item)
                        }
                    }
                    if opposingContainsConstraint || !unseenConstraints.isEmpty {
                        Text("Hard lines are written by you. The twin never crosses these.")
                            .font(CortexDesign.Typography.caption)
                            .foregroundColor(CortexDesign.inkFaint)
                    }
                }
            }
        }
        .padding(.leading, 10)
        .cortexCard(padding: CortexDesign.Space.lg, background: CortexDesign.panelBackground)
        .archiveSpine(isInsufficient ? CortexDesign.ink.opacity(0.2) : CortexDesign.accent)
    }

    private func evidenceBucket(label: String, items: [TwinEvidenceItem]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label.uppercased())
                .font(CortexDesign.Typography.stamp)
                .kerning(0.8)
                .foregroundColor(CortexDesign.inkFaint)
            ForEach(items.prefix(Self.evidencePerBucket)) { item in
                TwinEvidenceRow(item: item)
            }
            if items.count > Self.evidencePerBucket {
                Text("and \(items.count - Self.evidencePerBucket) more")
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.inkFaint)
            }
        }
    }
}

struct TwinEvidenceRow: View {
    let item: TwinEvidenceItem

    private var stampSegments: [String] {
        var segments: [String] = []
        if !item.layer.isEmpty {
            segments.append(item.layer)
        }
        if let source = item.source?.trimmingCharacters(in: .whitespacesAndNewlines), !source.isEmpty {
            segments.append(SourceDisplayName.label(source))
        }
        if let date = phaseStampDate(item.occurred_at) {
            segments.append(date)
        }
        return segments
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(item.content)
                .font(CortexDesign.Typography.prose(13.5))
                .lineSpacing(3)
                .foregroundColor(CortexDesign.ink)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            if !stampSegments.isEmpty {
                AccessionStamp(segments: stampSegments)
            }
        }
        .padding(.vertical, 2)
    }
}

// MARK: - Model tab: twin scorecard (Phase 5.4)

struct TwinScorecardCard: View {
    let scorecard: TwinScorecardResponse
    @ObservedObject var state: AppState

    private var accuracyText: String {
        guard let accuracy = scorecard.accuracy else { return "–" }
        return "\(Int((accuracy * 100).rounded()))%"
    }

    private var ungradedCount: Int {
        scorecard.ungraded.count
    }

    /// U-TWIN4: a compact one-line breakdown of the verdicts the twin has produced, so the
    /// predictions count is legible ("Likely yes 8 · Likely no 3 · Mixed 2") rather than opaque.
    private var verdictMixLine: String? {
        var parts: [String] = []
        if let yes = scorecard.verdict_mix["likely_yes"], yes > 0 {
            parts.append("Likely yes \(yes)")
        }
        if let no = scorecard.verdict_mix["likely_no"], no > 0 {
            parts.append("Likely no \(no)")
        }
        if let mixed = scorecard.verdict_mix["mixed"], mixed > 0 {
            parts.append("Mixed \(mixed)")
        }
        if let insufficient = scorecard.verdict_mix["insufficient_evidence"], insufficient > 0 {
            parts.append("Not enough \(insufficient)")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// U-TWIN1: switch to the Ask tab and seed a real "Would I " prefix the user completes.
    /// This is an invitation to compose, not an auto-fired search: we deliberately do NOT
    /// call runSearch() here, so an empty/no-evidence Ask never round-trips the twin or
    /// pollutes the persisted recent-queries list. AppState exposes no Ask-field focus
    /// request, so we only switch the tab and seed the text.
    private func askWouldI() {
        state.selectedTab = .ask
        state.searchQuery = "Would I "
    }

    /// U-TWIN2 / U-TWIN3: route to the Review tab's grading queue.
    private func openGradingQueue() {
        state.selectedTab = .review
        state.status = "Grade twin predictions"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
            HStack(alignment: .firstTextBaseline, spacing: CortexDesign.Space.sm) {
                Text(verbatim: "YOUR TWIN")
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.accent)
                Spacer(minLength: 0)
            }
            Text("When asked \u{201C}would I\u{2026}\u{201D}, \(DistributionMode.appDisplayName) predicts from your memory and keeps score.")
                .font(CortexDesign.Typography.body)
                .foregroundColor(CortexDesign.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(alignment: .top, spacing: CortexDesign.Space.xl) {
                twinStat(value: "\(scorecard.predictions)", label: "Predictions")
                twinStat(value: "\(scorecard.graded)", label: "Graded")
                twinStat(value: accuracyText, label: "Accuracy")
            }
            if let verdictMixLine {
                Text(verdictMixLine)
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.6)
                    .foregroundColor(CortexDesign.inkFaint)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(scorecard.graded == 0
                 ? "No grades yet. When a prediction's real outcome lands, grade it in Review."
                 : "Accuracy covers only the predictions you graded.")
                .font(CortexDesign.Typography.prose(13).italic())
                .lineSpacing(3)
                .foregroundColor(CortexDesign.inkFaint)
                .fixedSize(horizontal: false, vertical: true)

            // U-TWIN4: surface the twin's own caveat (same faint-italic footnote treatment as
            // ConnectionsToolUsageSection), so honesty notes on the scorecard aren't dropped.
            if let caveat = scorecard.caveats.first, !caveat.isEmpty {
                Text(caveat)
                    .font(CortexDesign.Typography.prose(13).italic())
                    .lineSpacing(3)
                    .foregroundColor(CortexDesign.inkFaint)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // U-TWIN1 / U-TWIN2: primary "Ask would I…" plus the grading action when there are
            // ungraded predictions waiting (replacing the passive footnote's dead-end).
            HStack(spacing: CortexDesign.Space.sm) {
                CortexButton(title: "Ask would I\u{2026}", systemImage: "questionmark.circle", role: .primary) {
                    askWouldI()
                }
                if ungradedCount > 0 {
                    CortexButton(
                        title: "Grade \(ungradedCount) prediction\(ungradedCount == 1 ? "" : "s")",
                        systemImage: "checkmark.seal",
                        role: .secondary
                    ) {
                        openGradingQueue()
                    }
                }
                Spacer(minLength: 0)
            }
        }
        .cortexCard(padding: CortexDesign.Space.lg, background: CortexDesign.panelBackground)
        .frame(maxWidth: 620, alignment: .leading)
    }

    // U-TWIN3: the stat cells route into the Review grading queue (mirrors ModelTab's
    // LedgerColumn press/hover language) so Predictions / Graded / Accuracy are live.
    // Honesty: the grading queue only renders when there are ungraded predictions, so with
    // everything graded the cells fall back to static stats — no button promising a
    // destination that doesn't exist (mirrors the ungradedCount gate on "Grade N predictions").
    private func twinStat(value: String, label: String) -> some View {
        TwinStatCell(value: value, label: label, action: ungradedCount > 0 ? openGradingQueue : nil)
    }
}

/// A Twin stat cell in the LedgerColumn press/hover language (U-TWIN3). Tappable only when an
/// action is provided (there's a grading queue to open); otherwise it renders as a static stat.
private struct TwinStatCell: View {
    let value: String
    let label: String
    let action: (() -> Void)?

    @State private var hovering = false

    var body: some View {
        if let action {
            Button(action: action) {
                cellContent
                    .background(
                        RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                            .fill(hovering ? CortexDesign.quietBackground : Color.clear)
                    )
                    .contentShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous))
            }
            .buttonStyle(.plain)
            .onHover { hovering = $0 }
            .animation(CortexMotion.press, value: hovering)
            .help("Grade twin predictions in Review")
            .accessibilityElement(children: .combine)
            .accessibilityHint("Opens the twin grading queue in Review")
        } else {
            cellContent
                .help("All predictions graded")
                .accessibilityElement(children: .combine)
        }
    }

    private var cellContent: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value)
                .font(CortexDesign.Typography.stat)
                .monospacedDigit()
                .foregroundColor(CortexDesign.ink)
            Text(label.uppercased())
                .font(CortexDesign.Typography.stamp)
                .kerning(0.8)
                .foregroundColor(CortexDesign.inkFaint)
        }
        .padding(.vertical, CortexDesign.Space.xs)
        .padding(.horizontal, CortexDesign.Space.sm)
    }
}

// MARK: - Connections: tool usage metrics + annoyance budget (Phases 4 and 6)

struct ConnectionsToolUsageSection: View {
    @ObservedObject var state: AppState

    private var hosts: [ToolScorecardHost] {
        state.toolScorecard?.hosts ?? []
    }

    private var budgetBinding: Binding<Int> {
        Binding(
            get: { state.appSettings.proactive_alerts_daily_budget ?? 3 },
            set: { newValue in
                state.appSettings.proactive_alerts_daily_budget = min(20, max(0, newValue))
                // The budget lives server-side (PUT /v1/settings), not in local defaults —
                // persistSettings() would only flash "Settings saved" while the backend kept
                // enforcing the old budget. scheduleSettingsAutosave debounces the real save.
                state.scheduleSettingsAutosave()
            }
        )
    }

    private var budgetValue: Int {
        state.appSettings.proactive_alerts_daily_budget ?? 3
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 6) {
                SectionHeader(
                    title: "How tools use your memory",
                    detail: "Measured locally from tool activity over the last 7 days."
                )
                // The moss mark — counts only, never content.
                HStack(spacing: 7) {
                    Circle()
                        .fill(CortexDesign.sealMoss)
                        .frame(width: 7, height: 7)
                    Text("Counts only. Memory content is never shown here.")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(CortexDesign.sealMoss)
                }
            }

            if hosts.isEmpty {
                Text("No tool activity recorded in this window yet.")
                    .font(CortexDesign.Typography.body)
                    .foregroundColor(CortexDesign.inkSecondary)
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(hosts) { host in
                        ConnectionsToolUsageRow(host: host)
                    }
                }
            }

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 190), spacing: 10)], spacing: 10) {
                ConnectionsTelemetryTile(
                    title: "Alert precision",
                    value: precisionValue,
                    detail: precisionDetail
                )
                ConnectionsTelemetryTile(
                    title: "Context prefetch",
                    value: prefetchValue,
                    detail: prefetchDetail
                )
            }

            VStack(alignment: .leading, spacing: 6) {
                Stepper(value: budgetBinding, in: 0...20) {
                    HStack(spacing: 8) {
                        Text("Proactive alerts per day")
                            .font(.callout)
                            .fontWeight(.medium)
                            .foregroundColor(CortexDesign.ink)
                        Text(budgetValue == 0 ? "OFF" : "\(budgetValue)")
                            .font(CortexDesign.Typography.stamp)
                            .kerning(0.8)
                            .foregroundColor(budgetValue == 0 ? CortexDesign.inkFaint : CortexDesign.accent)
                    }
                }
                Text("\(DistributionMode.appDisplayName) flags at most this many conflicts a day. Zero turns alerts off. Dismissing an alert teaches \(DistributionMode.appDisplayName) not to raise pairs like it.")
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(12)
            .background(CortexDesign.cardBackground)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            if let caveat = state.toolScorecard?.caveats.first {
                Text(caveat)
                    .font(CortexDesign.Typography.prose(13).italic())
                    .lineSpacing(3)
                    .foregroundColor(CortexDesign.inkFaint)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(14)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .onAppear {
            Task {
                await state.loadToolScorecard()
                await state.loadAlertPrecision()
                await state.loadPrefetchHitRate()
            }
        }
    }

    private var precisionValue: String {
        guard let precision = state.alertPrecision?.precision else { return "–" }
        return "\(Int((precision * 100).rounded()))%"
    }

    private var precisionDetail: String {
        guard let report = state.alertPrecision else { return "No alerts resolved yet" }
        if report.resolved == 0 { return "No alerts resolved yet" }
        var text = "\(report.accepted) of \(report.resolved) resolved alerts kept"
        if report.suppressed_by_budget > 0 {
            text += " · \(report.suppressed_by_budget) held by budget"
        }
        return text
    }

    private var prefetchValue: String {
        guard let rate = state.prefetchHitRate?.hit_rate else { return "–" }
        return "\(Int((rate * 100).rounded()))%"
    }

    private var prefetchDetail: String {
        guard let report = state.prefetchHitRate, report.trials > 0 else {
            return "No prefetch trials yet"
        }
        return "Guessed the next context right \(report.hits) of \(report.trials) times"
    }
}

struct ConnectionsToolUsageRow: View {
    let host: ToolScorecardHost

    private var stampSegments: [String] {
        var segments = ["\(host.calls) call\(host.calls == 1 ? "" : "s")"]
        segments.append("Memory-first \(Int((host.memory_usage_rate * 100).rounded()))%")
        if let faithfulness = host.grading.faithfulness_rate {
            segments.append("Faithful \(Int((faithfulness * 100).rounded()))%")
        }
        if host.conflict_packs_served > 0 {
            segments.append("\(host.conflict_packs_served) conflict pack\(host.conflict_packs_served == 1 ? "" : "s")")
        }
        return segments
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(host.displayName)
                .font(.callout)
                .fontWeight(.medium)
                .foregroundColor(CortexDesign.ink)
                .lineLimit(1)
            Spacer(minLength: 8)
            AccessionStamp(segments: stampSegments)
                .lineLimit(1)
                .truncationMode(.tail)
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 12)
        .background(CortexDesign.cardBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct ConnectionsTelemetryTile: View {
    let title: String
    let value: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title.uppercased())
                .font(CortexDesign.Typography.stamp)
                .kerning(0.8)
                .foregroundColor(CortexDesign.inkFaint)
            Text(value)
                .font(CortexDesign.Typography.stat)
                .monospacedDigit()
                .foregroundColor(CortexDesign.ink)
            Text(detail)
                .font(CortexDesign.Typography.caption)
                .foregroundColor(CortexDesign.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .frame(maxWidth: .infinity, minHeight: 84, alignment: .topLeading)
        .background(CortexDesign.cardBackground)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(CortexDesign.hairline, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}
