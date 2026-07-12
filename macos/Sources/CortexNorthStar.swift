import SwiftUI

// MARK: - North-star headline (GET /v1/usage/headline)
//
// The two "felt" surfaces of the core promise — "your memory, actively used across every AI":
//   1. RecallHeadlineCard — the Home headline: "Your memory was read by N AIs this week."
//   2. RecallProofWatcher — the proof moment: a live watcher that flips from "waiting…" to
//      "<app> just read your memory. Continuity, proven." on the first external read.
// Both are pure read-models over the backend's honest headline payload; neither ever fabricates
// a number. Honesty rules inherited from the endpoint: distinct_ais counts only per-app token
// labels — shared/default/untokened traffic surfaces as unattributed_calls, never as an AI.

/// One external AI client (a distinct per-app token label) that read memory in the window.
struct RecallClient: Codable, Hashable, Identifiable {
    let label: String
    let read_calls: Int
    let last_used_at: String?
    var id: String { label }
}

/// One frequently-served memory in the window — id + a short local display title only.
struct RecallTopMemory: Codable, Hashable, Identifiable {
    let memory_id: String
    let times_served: Int
    let title_or_summary: String
    var id: String { memory_id }
}

/// The north-star headline payload (GET /v1/usage/headline?days=7). Decoded as-is; the card and
/// watcher below derive every display string from these fields and never invent their own counts.
struct RecallHeadline: Codable, Hashable {
    let window_days: Int
    let computed_at: String
    let distinct_ais: Int
    let total_recalls: Int
    let clients: [RecallClient]
    let top_memories: [RecallTopMemory]
    let unattributed_calls: Int
    let unattributed_sources: [RecallClient]
    let caveats: [String]?
}

/// Display-string builders for the headline card, kept as pure helpers so every surface that
/// narrates the north-star number says it the same way.
enum RecallHeadlineText {
    /// "this week" for the canonical 7-day window; an honest "in the last N days" otherwise.
    static func windowPhrase(_ days: Int) -> String {
        days == 7 ? "this week" : "in the last \(days) days"
    }

    /// "Your memory was read by 3 AIs this week" (singular-safe).
    static func headline(distinctAIs: Int, windowDays: Int) -> String {
        "Your memory was read by \(distinctAIs) AI\(distinctAIs == 1 ? "" : "s") \(windowPhrase(windowDays))"
    }

    /// The compact per-client stamp: ["18 recalls", "Claude Desktop", "12", "Cursor", "5"].
    /// Rendered by AccessionStamp, so it reads "18 RECALLS · CLAUDE DESKTOP · 12 · CURSOR · 5".
    static func stampSegments(totalRecalls: Int, clients: [RecallClient], limit: Int = 4) -> [String] {
        var segments = ["\(totalRecalls) recall\(totalRecalls == 1 ? "" : "s")"]
        for client in clients.prefix(limit) {
            segments.append(client.label)
            segments.append("\(client.read_calls)")
        }
        return segments
    }

    /// The quiet honesty footnote — nil when there is no unattributed traffic to disclose.
    static func unattributedFootnote(_ calls: Int) -> String? {
        guard calls > 0 else { return nil }
        return "+\(calls) read\(calls == 1 ? "" : "s") from shared connections"
    }
}

// MARK: - Home headline card

/// The north-star headline card at the top of Home. Three honest shapes:
///   - distinct AIs read memory this window → the giant serif count + a "ledger of readers";
///   - only shared/untokened reads happened → the recall count, attributed to shared connections;
///   - nothing read memory → a purposeful nudge into the Connect-an-app wizard, not a sad zero.
/// Hidden entirely until the endpoint has answered once (state.recallHeadline != nil), so a cold
/// engine never renders a fabricated empty state.
struct RecallHeadlineCard: View {
    @ObservedObject var state: AppState

    var body: some View {
        if let headline = state.recallHeadline {
            Group {
                if headline.distinct_ais >= 1 {
                    activeCard(headline)
                } else if headline.total_recalls > 0 {
                    sharedOnlyCard(headline)
                } else {
                    emptyNudgeCard(headline)
                }
            }
            .transition(.opacity)
        }
    }

    private var stampHeader: some View {
        Text(verbatim: "MEMORY IN USE")
            .font(CortexDesign.Typography.stamp)
            .kerning(0.8)
            .foregroundColor(CortexDesign.accent)
            .accessibilityHidden(true)
    }

    /// The full headline, promoted to the giant New York numeral: N distinct AIs read memory this
    /// window, rendered as a display(64) serif count over a caption, with a horizontal ledger of
    /// readers (per-client name + rolling count) below a hairline rule. The number ROLLS on change
    /// via `AnimatableNumber`, so a live read (RecallProofWatcher polling) spins the wheel up.
    private func activeCard(_ headline: RecallHeadline) -> some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
            stampHeader

            // The giant north-star numeral + its caption. The number is the hero; the words are the
            // gloss beside it, not the headline.
            HStack(alignment: .firstTextBaseline, spacing: CortexDesign.Space.md) {
                AnimatableNumber(
                    value: Double(headline.distinct_ais),
                    font: CortexDesign.Typography.display(64),
                    color: CortexDesign.ink
                )
                .animation(CortexMotion.rollNumber, value: headline.distinct_ais)
                Text("AI\(headline.distinct_ais == 1 ? "" : "s") read your memory\n\(RecallHeadlineText.windowPhrase(headline.window_days))")
                    .font(CortexDesign.Typography.prose(15))
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(RecallHeadlineText.headline(distinctAIs: headline.distinct_ais, windowDays: headline.window_days))

            // The ledger of readers: a hairline rule, then a per-client row (name · rolling count),
            // so the abstract "3 AIs" becomes a legible register of who actually read.
            if !headline.clients.isEmpty {
                Rectangle()
                    .fill(CortexDesign.hairline)
                    .frame(height: 1)
                readersLedger(headline)
            }

            if let top = headline.top_memories.first {
                mostRecalledRow(top)
            }

            if let footnote = RecallHeadlineText.unattributedFootnote(headline.unattributed_calls) {
                Text(footnote)
                    .font(.caption)
                    .foregroundColor(CortexDesign.inkFaint)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .cortexCard(padding: CortexDesign.Space.lg, background: CortexDesign.panelBackground)
        .frame(maxWidth: 620, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(RecallHeadlineText.headline(distinctAIs: headline.distinct_ais, windowDays: headline.window_days))
    }

    /// The horizontal register of who read memory: total recalls, then each client as name over a
    /// rolling count. gold = "live/this week" — the counts sit in the gold register.
    private func readersLedger(_ headline: RecallHeadline) -> some View {
        HStack(alignment: .top, spacing: CortexDesign.Space.lg) {
            ledgerColumn(
                label: "Total recalls",
                value: headline.total_recalls,
                tint: CortexDesign.gold
            )
            ForEach(headline.clients.prefix(4)) { client in
                ledgerColumn(
                    label: client.label,
                    value: client.read_calls,
                    tint: CortexDesign.ink
                )
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(clientAccessibilitySummary(headline))
    }

    /// One reader column: a rolling numeral over a small SF label. `AnimatableNumber` rolls the
    /// count on change (a poll that lands a new read spins the wheel).
    private func ledgerColumn(label: String, value: Int, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            AnimatableNumber(
                value: Double(value),
                font: CortexDesign.Typography.stat,
                color: tint
            )
            .animation(CortexMotion.rollNumber, value: value)
            Text(label)
                .font(CortexDesign.Typography.caption)
                .foregroundColor(CortexDesign.inkSecondary)
                .lineLimit(1)
        }
    }

    /// "Most recalled" — the app's most-served memory this window. FIX: was dead non-interactive
    /// text; now a tappable row that explores that memory in Ask (the same "explore this" affordance
    /// the Constellation node tap uses: seed the query, switch to Ask, run the search — surfacing the
    /// memory's content with citations). No dedicated open-memory-by-id surface exists; this reuses
    /// the shipped search path. accentSoft wash = "Cortex noticed / here is a thread to pull".
    private func mostRecalledRow(_ top: RecallTopMemory) -> some View {
        let title = MemoryText.displayProse(top.title_or_summary, maxLength: 80)
        return Button {
            state.searchQuery = top.title_or_summary
            state.selectedTab = .ask
            state.runSearch()
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: CortexDesign.Space.sm) {
                Text("Most recalled")
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.inkFaint)
                Text(title)
                    .font(.callout)
                    .foregroundColor(CortexDesign.ink)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                Image(systemName: "arrow.up.forward")
                    .font(.caption2.weight(.semibold))
                    .foregroundColor(CortexDesign.accent)
            }
            .padding(.vertical, CortexDesign.Space.xs)
            .padding(.horizontal, CortexDesign.Space.sm)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(CortexDesign.accentSoft)
            .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous))
            .contentShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Most recalled: \(title). Explore in Ask.")
        .accessibilityHint("Opens Ask and searches for this memory")
    }

    /// Reads happened, but only through shared/untokened connections — say exactly that instead
    /// of claiming distinct AIs.
    private func sharedOnlyCard(_ headline: RecallHeadline) -> some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
            stampHeader

            Text("Your memory was read \(headline.total_recalls) time\(headline.total_recalls == 1 ? "" : "s") \(RecallHeadlineText.windowPhrase(headline.window_days))")
                .font(CortexDesign.Typography.display(22))
                .foregroundColor(CortexDesign.ink)
                .fixedSize(horizontal: false, vertical: true)

            Text("All of it came through shared connections. Connect apps individually to see which AI is reading.")
                .font(.caption)
                .foregroundColor(CortexDesign.inkFaint)
                .fixedSize(horizontal: false, vertical: true)
        }
        .cortexCard(padding: CortexDesign.Space.lg, background: CortexDesign.panelBackground)
        .frame(maxWidth: 620, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Your memory was read \(headline.total_recalls) times \(RecallHeadlineText.windowPhrase(headline.window_days)), all through shared connections")
    }

    /// The purposeful zero: no external AI has read memory yet → one quiet nudge into the
    /// existing Connect-an-app wizard (via the same Connections presentation every tab uses).
    private func emptyNudgeCard(_ headline: RecallHeadline) -> some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
            stampHeader

            Text("No AI has read your memory yet \(RecallHeadlineText.windowPhrase(headline.window_days))")
                .font(CortexDesign.Typography.display(20))
                .foregroundColor(CortexDesign.ink)
                .fixedSize(horizontal: false, vertical: true)

            Text("Connect an app and it can read your approved memory, with citations.")
                .font(.callout)
                .foregroundColor(CortexDesign.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)

            CortexButton(title: "Connect an app", systemImage: "wand.and.stars", role: .secondary, size: .small) {
                state.openConnectionsPrivacy(statusMessage: "Connect an app")
            }
            .help("Opens Connections: the step-by-step wizard connects Claude, Cursor, and other AI apps to your memory.")
        }
        .cortexCard(padding: CortexDesign.Space.lg, background: CortexDesign.panelBackground)
        .frame(maxWidth: 620, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("No AI has read your memory yet \(RecallHeadlineText.windowPhrase(headline.window_days)). Connect an app to change that.")
    }

    private func clientAccessibilitySummary(_ headline: RecallHeadline) -> String {
        let clients = headline.clients.prefix(4)
            .map { "\($0.label) \($0.read_calls)" }
            .joined(separator: ", ")
        return "\(headline.total_recalls) recalls. \(clients)"
    }
}

// MARK: - The proof moment

/// Watches for the first external AI read and flips from a calm waiting line to
/// "<app> just read your memory. Continuity, proven."
///
/// Mechanics: polls `AppState.loadRecallHeadline()` every ~3 seconds while visible — the `.task`
/// modifier cancels the loop automatically the moment the view disappears — and also listens to
/// the live activity stream (kind == "reach" recall receipts) so the flip lands within a
/// heartbeat of the actual read. Baselines are captured on appear so PRE-EXISTING reads never
/// masquerade as the proof: only a read that happens while the watcher is on screen counts.
/// Purely observational — it never gates or blocks whatever flow hosts it.
struct RecallProofWatcher: View {
    @ObservedObject var state: AppState
    /// When set, only reads attributed to this token label count (the connect wizard scopes the
    /// watcher to the just-connected tool); nil accepts any distinctly-attributed AI.
    var toolLabel: String? = nil
    var waitingLine: String

    /// read_calls per client label (lowercased) at watch start — nil until the first successful
    /// headline load establishes it.
    @State private var baselineReads: [String: Int]?
    /// The newest activity seq at watch start; only reach events after it count as fresh.
    @State private var baselineActivitySeq: Int = 0
    @State private var activityBaselineSeeded = false
    /// The label that delivered the proof; non-nil flips the view into its proven state.
    @State private var provenLabel: String?

    var body: some View {
        Group {
            if let provenLabel {
                provenRow(provenLabel)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            } else {
                waitingRow
                    .transition(.opacity)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(CortexDesign.panelBackground)
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(CortexDesign.softBorder, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .onAppear {
            // Seed the activity baseline synchronously, before any onChange can fire, so replayed
            // ticker history from earlier in the session can never count as the proof.
            guard !activityBaselineSeeded else { return }
            activityBaselineSeeded = true
            baselineActivitySeq = state.recentActivity.last?.seq ?? 0
        }
        .task {
            // First load establishes the honest baseline; subsequent ~3s polls look for growth.
            await state.loadRecallHeadline()
            checkHeadlineForProof()
            while !Task.isCancelled && provenLabel == nil {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                if Task.isCancelled { return }
                await state.loadRecallHeadline()
                checkHeadlineForProof()
            }
        }
        .onChange(of: state.recentActivity) { _ in
            checkActivityForProof()
        }
    }

    // MARK: States

    private var waitingRow: some View {
        HStack(alignment: .center, spacing: 10) {
            ProofWaitingPulse()
            Text(waitingLine)
                .font(.callout)
                .foregroundColor(CortexDesign.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(waitingLine)
    }

    private func provenRow(_ label: String) -> some View {
        HStack(alignment: .center, spacing: 12) {
            ProofBurstMark(tint: CortexDesign.sealMoss)
            (Text("\(label) just read your memory. ").fontWeight(.semibold)
                + Text("Continuity, proven."))
                .font(.callout)
                .foregroundColor(CortexDesign.ink)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(label) just read your memory. Continuity, proven.")
    }

    // MARK: Proof detection

    private func matchesTool(_ label: String) -> Bool {
        guard let toolLabel else { return true }
        return label.compare(toolLabel, options: [.caseInsensitive]) == .orderedSame
    }

    /// Headline path: proof = a matching client whose read_calls grew past its baseline (a client
    /// absent at baseline counts from zero). The FIRST successful load only seeds the baseline —
    /// stale weekly totals must never claim a "just now" read.
    private func checkHeadlineForProof() {
        guard provenLabel == nil, let headline = state.recallHeadline else { return }
        guard let baseline = baselineReads else {
            baselineReads = Dictionary(
                headline.clients.map { ($0.label.lowercased(), $0.read_calls) },
                uniquingKeysWith: max
            )
            return
        }
        for client in headline.clients where matchesTool(client.label) {
            if client.read_calls > (baseline[client.label.lowercased()] ?? 0) {
                prove(client.label)
                return
            }
        }
    }

    /// Activity path: a fresh "reach" recall receipt (seq past the on-appear baseline) from a
    /// matching source is a live, distinctly-attributed read — flip immediately.
    private func checkActivityForProof() {
        guard provenLabel == nil else { return }
        for event in state.recentActivity where event.seq > baselineActivitySeq {
            if event.kind == "reach", event.action == "recall",
               !event.source.isEmpty, matchesTool(event.source) {
                prove(event.source)
                return
            }
        }
    }

    private func prove(_ label: String) {
        guard provenLabel == nil else { return }
        withAnimation(.spring(response: 0.42, dampingFraction: 0.75)) {
            provenLabel = label
        }
    }
}

/// The waiting-state mark: a small gold dot breathing gently — the same "live" language as the
/// activity ticker's pulse.
private struct ProofWaitingPulse: View {
    @State private var pulsing = false

    var body: some View {
        Circle()
            .fill(CortexDesign.gold)
            .frame(width: 7, height: 7)
            .scaleEffect(pulsing ? 1.0 : 0.6)
            .opacity(pulsing ? 1.0 : 0.4)
            .onAppear {
                withAnimation(.easeInOut(duration: 0.85).repeatForever(autoreverses: true)) {
                    pulsing = true
                }
            }
            .accessibilityHidden(true)
    }
}

/// The proof flourish: a one-shot ring burst around the reach glyph, in the app's ring-pulse
/// animation language (see OnboardingHeroMark) but played once — a moment, not a loop.
private struct ProofBurstMark: View {
    let tint: Color
    @State private var burst = false

    var body: some View {
        ZStack {
            ForEach(0..<2, id: \.self) { index in
                Circle()
                    .stroke(tint.opacity(0.4), lineWidth: 1.5)
                    .frame(width: 30, height: 30)
                    .scaleEffect(burst ? 1.9 : 0.8)
                    .opacity(burst ? 0 : 0.7)
                    .animation(.easeOut(duration: 1.1).delay(Double(index) * 0.22), value: burst)
            }
            Circle()
                .fill(tint.opacity(0.14))
                .frame(width: 30, height: 30)
            Image(systemName: "antenna.radiowaves.left.and.right")
                .font(.system(size: 13, weight: .semibold))
                .foregroundColor(tint)
                .scaleEffect(burst ? 1 : 0.6)
                .animation(.spring(response: 0.4, dampingFraction: 0.6), value: burst)
        }
        .frame(width: 38, height: 38)
        .onAppear { burst = true }
        .accessibilityHidden(true)
    }
}

// MARK: - Real-data mini constellation (onboarding closing beat)

/// A REAL-data miniature of "Your Constellation" for onboarding's closing beat: the user's actual
/// graph nodes laid out by the same deterministic engine the full map uses (MemoryMapLayout),
/// rendered as a calm breathing miniature. With no data yet it shows an honest placeholder —
/// never a fake graph pretending to be the user's memory.
struct ConstellationMiniPreview: View {
    let nodes: [GraphNode]
    let edges: [GraphEdge]

    /// Layout is computed once at this nominal size and scaled to whatever frame the caller gives
    /// the preview, so the (memoized) force sim never depends on live view geometry.
    private static let nominalSize = CGSize(width: 520, height: 150)
    private static let maxNodes = 14

    /// The most prominent real nodes (centrality first, importance as fallback), capped so the
    /// short strip reads as a constellation rather than a smear.
    private var previewNodes: [GraphNode] {
        let ranked = nodes.sorted { a, b in
            let pa = a.centrality ?? Double(a.importance ?? 1) / 100.0
            let pb = b.centrality ?? Double(b.importance ?? 1) / 100.0
            if pa != pb { return pa > pb }
            return a.id < b.id
        }
        return Array(ranked.prefix(Self.maxNodes))
    }

    /// Only real edges whose endpoints both made the cut.
    private var previewEdges: [GraphEdge] {
        let ids = Set(previewNodes.map(\.id))
        return edges.filter { ids.contains($0.source_id) && ids.contains($0.target_id) }
    }

    var body: some View {
        if nodes.isEmpty {
            emptyPlaceholder
        } else {
            liveMiniature
        }
    }

    // MARK: Rotating framings

    /// One held framing of the SAME real graph. The rotation only re-EMPHASIZES the constellation
    /// (which register is warm, whether halos bloom, how brightly links flicker); it never re-lays
    /// out nodes or invents data, so the constellation stays the user's real map throughout. All
    /// values are deterministic — driven off the timeline clock and the memoized layout, never
    /// `.random`.
    private enum Framing: CaseIterable {
        case constellation   // the calm baseline: even accent register, soft halos
        case emphasis        // the hubs warm to gold, their halos bloom; links stay quiet
        case links           // the connective tissue lights up: brighter links, cooler nodes

        /// Node dot tint for this framing.
        var nodeTint: Color {
            switch self {
            case .constellation: return CortexDesign.accent
            case .emphasis: return CortexDesign.accent
            case .links: return CortexDesign.accent
            }
        }
        /// Extra tint mixed into hub nodes (nil = no special hub emphasis).
        var hubAccent: Color? {
            switch self {
            case .emphasis: return CortexDesign.gold
            case .constellation, .links: return nil
            }
        }
        /// Base halo opacity for a hub node.
        var hubHalo: Double {
            switch self {
            case .constellation: return 0.14
            case .emphasis: return 0.22
            case .links: return 0.10
            }
        }
        /// Base halo opacity for a non-hub node.
        var nodeHalo: Double {
            switch self {
            case .constellation: return 0.08
            case .emphasis: return 0.10
            case .links: return 0.06
            }
        }
        /// Link flicker floor + swing — the "links" framing brings the connective tissue forward.
        var linkFloor: Double {
            switch self {
            case .constellation: return 0.12
            case .emphasis: return 0.10
            case .links: return 0.24
            }
        }
        var linkSwing: Double {
            switch self {
            case .constellation: return 0.06
            case .emphasis: return 0.05
            case .links: return 0.10
            }
        }
    }

    /// Each framing is held ~10s; the cross-fade between neighbours runs the last ~1.8s of that
    /// window. Both are inside the 8-12s hold / 1.5-2s fade ask, and both are deterministic.
    private static let framingHold: Double = 10
    private static let framingFade: Double = 1.8

    /// Deterministic rotation state at time `t`: the framing currently on screen, the framing being
    /// crossed to, and a 0…1 fade progress. Outside the fade window the two framings are equal and
    /// `fade` is 0, so the crossfade layer contributes nothing.
    private func rotation(at t: Double) -> (current: Framing, next: Framing, fade: Double) {
        let all = Framing.allCases
        let period = Self.framingHold
        let phase = t / period
        let index = Int(phase.rounded(.down))
        let current = all[((index % all.count) + all.count) % all.count]
        let next = all[(((index + 1) % all.count) + all.count) % all.count]

        // Time elapsed inside the current hold, in seconds.
        let intoPhase = (phase - phase.rounded(.down)) * period
        let fadeStart = period - Self.framingFade
        guard intoPhase >= fadeStart else {
            return (current, current, 0)
        }
        // Raw linear progress across the fade window, eased so the cross-fade is a slow swell and
        // settle rather than a linear ramp — never a hard jump.
        let raw = min(1, max(0, (intoPhase - fadeStart) / Self.framingFade))
        let eased = raw * raw * (3 - 2 * raw) // smoothstep = the easeInOut curve, done in-canvas
        return (current, next, eased)
    }

    private var liveMiniature: some View {
        let drawNodes = previewNodes
        let drawEdges = previewEdges
        return TimelineView(.animation) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            let rot = rotation(at: t)
            Canvas { ctx, size in
                let layout = MemoryMapLayout.layout(nodes: drawNodes, edges: drawEdges, size: Self.nominalSize)
                let sx = size.width / Self.nominalSize.width
                let sy = size.height / Self.nominalSize.height
                func scaled(_ p: CGPoint) -> CGPoint { CGPoint(x: p.x * sx, y: p.y * sy) }

                // Draw the constellation once per framing and cross-fade the two by whole-layer
                // opacity — a slow easeInOut swell, so a framing never hard-cuts to the next. The
                // outgoing framing fades from 1 -> (1 - fade); the incoming fades 0 -> fade. Away
                // from the fade window `fade` is 0, so only the current framing renders.
                drawFraming(rot.current, layerOpacity: 1 - rot.fade,
                            in: &ctx, layout: layout, drawNodes: drawNodes, drawEdges: drawEdges,
                            scaled: scaled, t: t)
                if rot.fade > 0 {
                    drawFraming(rot.next, layerOpacity: rot.fade,
                                in: &ctx, layout: layout, drawNodes: drawNodes, drawEdges: drawEdges,
                                scaled: scaled, t: t)
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("A preview of your Constellation, drawn from your real memory graph")
    }

    /// Render ONE framing of the real graph at a given whole-layer opacity. The breathing (node
    /// pulse) and link flicker are unchanged in spirit — the same deterministic sin() language as
    /// before — with per-framing emphasis dialled in via the Framing tokens. `layerOpacity`
    /// multiplies every element's alpha so the caller can cross-fade two framings.
    private func drawFraming(
        _ framing: Framing,
        layerOpacity: Double,
        in ctx: inout GraphicsContext,
        layout: MemoryMapLayout,
        drawNodes: [GraphNode],
        drawEdges: [GraphEdge],
        scaled: (CGPoint) -> CGPoint,
        t: Double
    ) {
        guard layerOpacity > 0.001 else { return }

        // Links first, pulsing softly — the same flicker language as the full map, with the
        // per-framing floor/swing dialled in so the "links" framing brings connective tissue forward.
        for edge in drawEdges {
            guard let pa = layout.position(of: edge.source_id),
                  let pb = layout.position(of: edge.target_id) else { continue }
            var path = Path()
            path.move(to: scaled(pa))
            path.addLine(to: scaled(pb))
            let flicker = framing.linkFloor + (sin(t * 0.8 + Double(edge.id.hashValue % 7)) + 1) * framing.linkSwing
            ctx.stroke(path, with: .color(CortexDesign.accent.opacity(flicker * layerOpacity)), lineWidth: 1)
        }

        // Nodes breathing gently; size comes from the real layout's prominence radii. Hubs may warm
        // toward gold in the emphasis framing.
        for (index, node) in drawNodes.enumerated() {
            guard let position = layout.position(of: node.id) else { continue }
            let p = scaled(position)
            let pulse = 1 + sin(t * 1.1 + Double(index) * 0.7) * 0.15
            let r = min(7, max(3, layout.radius(of: node) * 0.7)) * pulse
            let isHub = node.is_hub == true

            let dotTint = (isHub ? framing.hubAccent : nil) ?? framing.nodeTint
            let haloBase = isHub ? framing.hubHalo : framing.nodeHalo
            let haloTint = (isHub ? framing.hubAccent : nil) ?? CortexDesign.accent

            let halo = CGRect(x: p.x - r * 2, y: p.y - r * 2, width: r * 4, height: r * 4)
            ctx.fill(Path(ellipseIn: halo), with: .color(haloTint.opacity(haloBase * layerOpacity)))
            let dot = CGRect(x: p.x - r, y: p.y - r, width: r * 2, height: r * 2)
            ctx.fill(Path(ellipseIn: dot), with: .color(dotTint.opacity(0.85 * layerOpacity)))
        }
    }

    /// Honest empty state: a few clearly-decorative drifting motes (not a fake graph) under a
    /// plain statement of what will happen.
    private var emptyPlaceholder: some View {
        ZStack {
            TimelineView(.animation) { context in
                let t = context.date.timeIntervalSinceReferenceDate
                Canvas { ctx, size in
                    for i in 0..<7 {
                        let seed = Double(i) * 1.9
                        let x = (sin(t * 0.05 + seed) * 0.5 + 0.5) * size.width
                        let y = (cos(t * 0.04 + seed * 1.3) * 0.5 + 0.5) * size.height
                        let r = 1.5 + (sin(seed) + 1) * 1.1
                        let rect = CGRect(x: x - r, y: y - r, width: r * 2, height: r * 2)
                        ctx.fill(Path(ellipseIn: rect), with: .color(CortexDesign.gold.opacity(0.10)))
                    }
                }
            }
            Text("Your map builds as you import")
                .font(.callout)
                .foregroundColor(CortexDesign.inkSecondary)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Your map builds as you import")
    }
}
