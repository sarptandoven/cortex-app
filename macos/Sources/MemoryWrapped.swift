import SwiftUI
import AppKit
import UserNotifications

// MARK: - Memory Wrapped — the north-star metric as a recurring, shareable object.
//
// "Spotify Wrapped for your memory": a screenshot-native weekly card that turns the ONE number
// the plan says flips the yes — "your memory answered N recalls across M AIs this week" — into a
// tell-a-friend / come-back object for a product whose ideal usage is ZERO app-opens.
//
// It renders REAL recall stats and never fabricates one: every count, label, and date comes from
// the same honest `RecallHeadline` payload (GET /v1/usage/headline) the Home headline card reads,
// inheriting its honesty rules verbatim — distinct_ais counts only per-app token labels; shared /
// default / untokened traffic is disclosed as unattributed reads, never as an AI. When the numbers
// are low or zero it shows a warm "your first week" state, never a sad empty card.
//
// Everything expensive (the model derivation, layout, and 2× raster) happens only when the share
// sheet opens — the Home entry and the weekly notifier are pure read-models over `RecallHeadline`,
// so the card costs nothing until someone reaches for it. Rendering is deterministic (FNV-seeded
// star-dust, no `.random`) so the same week always produces the same card — the constellation
// contract, applied to the wrapped card too.

/// Everything `MemoryWrappedCard` draws, computed ONCE when the share sheet opens (or when the
/// compact Home entry needs its summary line). Plain data, so the card view itself is trivial and
/// deterministic. Nothing here invents a number: it only reshapes the honest headline payload.
struct MemoryWrappedModel {
    /// One AI client's line on the card: the token label as-is plus its read count.
    struct ClientLine: Identifiable {
        let label: String
        let recalls: Int
        var id: String { label }
    }

    let totalRecalls: Int
    let distinctAIs: Int
    /// Per-client breakdown (Claude Desktop · Cursor · ChatGPT …), most-active first, capped so the
    /// card stays legible. `unattributed_calls` never enters this list — it rides the footnote only.
    let clients: [ClientLine]
    /// The single most-recalled memory's display title, already prose-cleaned; nil when none served.
    let topMemory: String?
    /// The honest disclosure line for shared/untokened reads — nil when there are none.
    let unattributedFootnote: String?
    /// "30 JUN – 7 JUL 2026" — the real window the numbers describe, derived from computed_at and
    /// window_days so the card can never claim a window it didn't measure.
    let windowLine: String
    /// The mono stamp line, e.g. "MEMORY WRAPPED · 7 JUL 2026".
    let stampLine: String
    /// True when nothing meaningful happened yet: no distinct AI AND no attributed recalls. Drives
    /// the warm "your first week" identity instead of a row of zeros.
    let isFirstWeek: Bool

    /// At most this many client rows — a per-app breakdown, not a spreadsheet.
    private static let clientBudget = 5

    static func build(from headline: RecallHeadline) -> MemoryWrappedModel {
        // Most-active first, then label for a stable (deterministic) tie-break; drop empty labels
        // so a malformed token never renders a blank row.
        let ranked = headline.clients
            .filter { !$0.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
            .sorted { lhs, rhs in
                if lhs.read_calls != rhs.read_calls { return lhs.read_calls > rhs.read_calls }
                return lhs.label < rhs.label
            }
            .prefix(clientBudget)
            .map { ClientLine(label: $0.label, recalls: $0.read_calls) }

        let top = headline.top_memories.first.map { MemoryText.displayProse($0.title_or_summary, maxLength: 88) }

        // "your first week" = the endpoint has answered but nothing has actually recalled memory
        // through an attributed AI yet. Shared-only reads still count as real activity, so a week
        // with unattributed_calls > 0 is NOT the empty state.
        let isFirstWeek = headline.distinct_ais < 1
            && headline.total_recalls < 1
            && headline.unattributed_calls < 1

        return MemoryWrappedModel(
            totalRecalls: headline.total_recalls,
            distinctAIs: headline.distinct_ais,
            clients: Array(ranked),
            topMemory: top,
            unattributedFootnote: RecallHeadlineText.unattributedFootnote(headline.unattributed_calls),
            windowLine: windowLine(computedAt: headline.computed_at, windowDays: headline.window_days),
            stampLine: stampLine(computedAt: headline.computed_at),
            isFirstWeek: isFirstWeek
        )
    }

    // MARK: Display strings

    /// The hero line. Singular-safe for both counts; the "first week" caller renders its own warm
    /// line instead of this one.
    var headlineText: String {
        let recalls = "\(totalRecalls) recall\(totalRecalls == 1 ? "" : "s")"
        let ais = "\(distinctAIs) AI\(distinctAIs == 1 ? "" : "s")"
        return "Your memory answered \(recalls) across \(ais) this week"
    }

    /// The shared-only variant: reads happened, but only through untokened connections — say exactly
    /// that instead of claiming distinct AIs.
    var sharedOnlyHeadline: String {
        "Your memory was read \(totalRecalls) time\(totalRecalls == 1 ? "" : "s") this week"
    }

    /// The one-line summary for the compact Home entry and the weekly notification body.
    var compactSummary: String {
        if isFirstWeek { return "Your first week is being written." }
        if distinctAIs >= 1 { return headlineText }
        if totalRecalls >= 1 { return sharedOnlyHeadline + ", through shared connections." }
        // No attributed AND no shared reads but not first-week (defensive): stay warm, never sad.
        return "Your first week is being written."
    }

    /// "N recalls across M AIs" — the terse notification-title tail.
    var notificationTail: String {
        "\(totalRecalls) recall\(totalRecalls == 1 ? "" : "s") across \(distinctAIs) AI\(distinctAIs == 1 ? "" : "s")"
    }

    /// Parses the ISO-8601 `computed_at` (now_iso: "2026-07-07T12:00:00+00:00") and renders the
    /// real 7-day window as "30 JUN – 7 JUL 2026". Falls back to just the end date when the window
    /// spans a single month, and to today's date if computed_at is ever unparseable.
    private static func windowLine(computedAt raw: String, windowDays: Int) -> String {
        let end = parseISO(raw) ?? Date()
        let start = Calendar.current.date(byAdding: .day, value: -max(0, windowDays), to: end) ?? end

        let full = DateFormatter()
        full.locale = Locale(identifier: "en_US_POSIX")
        full.dateFormat = "d MMM yyyy"
        let dayMonth = DateFormatter()
        dayMonth.locale = Locale(identifier: "en_US_POSIX")
        dayMonth.dateFormat = "d MMM"

        let cal = Calendar.current
        let sameMonth = cal.isDate(start, equalTo: end, toGranularity: .month)
        let startText = sameMonth ? cal.component(.day, from: start).formatted() : dayMonth.string(from: start)
        return "\(startText) – \(full.string(from: end))".uppercased()
    }

    private static func stampLine(computedAt raw: String) -> String {
        let date = parseISO(raw) ?? Date()
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "d MMM yyyy"
        return "MEMORY WRAPPED · " + formatter.string(from: date).uppercased()
    }

    /// ISO-8601 with and without fractional seconds — mirrors `ModelTab.compiledDateText`.
    private static func parseISO(_ raw: String) -> Date? {
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return nil }
        let iso = ISO8601DateFormatter()
        if let date = iso.date(from: value) { return date }
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return iso.date(from: value)
    }
}

// MARK: - The card

/// The fixed-size (1200×630, standard social-card ratio) Memory Wrapped card. Renders the week's
/// real recall stats in the Archive's DARK identity — the same night-sky palette and quiet Cortex
/// branding as `ConstellationShareCard`, so the two share objects read as one family. Exported at
/// 2× (2400×1260) via ImageRenderer. Never blank: the first-week state is a warm invitation, not a
/// row of zeros.
struct MemoryWrappedCard: View {
    static let size = CGSize(width: 1200, height: 630)

    let model: MemoryWrappedModel

    /// The Archive's dark palette, fixed by VALUE (identical hex to `ConstellationShareCard.Night`):
    /// the app window is pinned light and the adaptive tokens would resolve light here — the card
    /// must always carry the night-sky identity, so it names the dark values directly.
    private enum Night {
        static let paper = Color(red: 0.110, green: 0.102, blue: 0.090)          // #1C1A17
        static let panel = Color(red: 0.149, green: 0.137, blue: 0.125)          // #262320
        static let ink = Color(red: 0.910, green: 0.890, blue: 0.851)            // #E8E3D9
        static let inkSecondary = Color(red: 0.690, green: 0.663, blue: 0.616)   // #B0A99D
        static let inkFaint = Color(red: 0.549, green: 0.522, blue: 0.478)       // #8C857A
        static let accent = Color(red: 0.788, green: 0.420, blue: 0.341)         // #C96B57
        static let gold = Color(red: 0.827, green: 0.627, blue: 0.298)           // #D3A04C
        static let moss = Color(red: 0.494, green: 0.604, blue: 0.447)           // #7E9A72
    }

    var body: some View {
        ZStack {
            Night.paper
            RadialGradient(
                colors: [Night.panel.opacity(0.9), Night.paper],
                center: .init(x: 0.28, y: 0.32),
                startRadius: 40,
                endRadius: 720
            )
            starDust
            chrome
            Rectangle()
                .strokeBorder(Night.ink.opacity(0.12), lineWidth: 1)
                .padding(16)
        }
        .frame(width: Self.size.width, height: Self.size.height)
    }

    // MARK: Deterministic star field

    /// Faint FNV-seeded star-dust (no randomness — the constellation contract) so the card always
    /// reads as a living night sky, even in the first week with no numbers to draw.
    private var starDust: some View {
        Canvas { context, _ in
            for index in 0..<120 {
                var hash: UInt64 = 0xcbf29ce484222325
                for byte in "wrapped-dust-\(index)".utf8 {
                    hash ^= UInt64(byte)
                    hash = hash &* 0x100000001b3
                }
                let x = CGFloat(hash % UInt64(Self.size.width))
                let y = CGFloat((hash >> 16) % UInt64(Self.size.height))
                let alpha = 0.03 + Double((hash >> 32) % 90) / 1_600
                let radius: CGFloat = (hash >> 44) % 6 == 0 ? 1.6 : 0.9
                context.fill(
                    Path(ellipseIn: CGRect(x: x - radius, y: y - radius, width: radius * 2, height: radius * 2)),
                    with: .color(Night.ink.opacity(alpha))
                )
            }
        }
        .frame(width: Self.size.width, height: Self.size.height)
    }

    // MARK: Chrome — the honest text layout

    private var chrome: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(model.stampLine)
                .font(.system(size: 13, weight: .medium, design: .monospaced))
                .kerning(2.4)
                .foregroundColor(Night.inkFaint)

            Text(model.windowLine)
                .font(.system(size: 12, weight: .medium, design: .monospaced))
                .kerning(1.8)
                .foregroundColor(Night.inkFaint.opacity(0.85))
                .padding(.top, 6)

            if model.isFirstWeek {
                firstWeekHero
            } else {
                headlineHero
            }

            Spacer(minLength: 0)

            if model.isFirstWeek {
                firstWeekFoot
            } else {
                statsFoot
            }
        }
        .padding(.horizontal, 52)
        .padding(.top, 46)
        .padding(.bottom, 44)
    }

    /// The number — the whole point of the card. Rendered big, in the serif archive voice, with the
    /// distinct-AIs line first and shared-only reads narrated honestly when that's all there was.
    private var headlineHero: some View {
        VStack(alignment: .leading, spacing: 14) {
            if model.distinctAIs >= 1 {
                (Text("Your memory answered ")
                    + Text("\(model.totalRecalls)").foregroundColor(Night.accent)
                    + Text(" recall\(model.totalRecalls == 1 ? "" : "s") across ")
                    + Text("\(model.distinctAIs)").foregroundColor(Night.gold)
                    + Text(" AI\(model.distinctAIs == 1 ? "" : "s") this week"))
                    .font(.system(size: 46, weight: .semibold, design: .serif))
                    .foregroundColor(Night.ink)
                    .lineLimit(3)
                    .minimumScaleFactor(0.7)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text(model.sharedOnlyHeadline)
                    .font(.system(size: 46, weight: .semibold, design: .serif))
                    .foregroundColor(Night.ink)
                    .lineLimit(3)
                    .minimumScaleFactor(0.7)
                    .fixedSize(horizontal: false, vertical: true)
                Text("All through shared connections. Connect apps individually to see which AI is reading.")
                    .font(.system(size: 17, weight: .regular))
                    .foregroundColor(Night.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let top = model.topMemory {
                (Text("Most recalled  ").foregroundColor(Night.inkFaint)
                    + Text(top).foregroundColor(Night.ink))
                    .font(.system(size: 18, weight: .regular))
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 6)
            }
        }
        .padding(.top, 22)
    }

    /// The warm first-week identity: an invitation, never a sad zero.
    private var firstWeekHero: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Your first week of memory,\nabout to be written.")
                .font(.system(size: 46, weight: .semibold, design: .serif))
                .foregroundColor(Night.ink)
                .fixedSize(horizontal: false, vertical: true)
            Text("Connect Claude, Cursor, or ChatGPT and every recall lands here. Your memory, working across every AI, in one number.")
                .font(.system(size: 18, weight: .regular))
                .foregroundColor(Night.inkSecondary)
                .frame(maxWidth: 720, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 2)
        }
        .padding(.top, 22)
    }

    /// The per-client breakdown (Claude Desktop · Cursor · ChatGPT …), each a big recall count over
    /// its label. Falls back to the shared-connection footnote when there are no attributed clients.
    private var statsFoot: some View {
        HStack(alignment: .lastTextBaseline, spacing: 40) {
            if model.clients.isEmpty {
                if let footnote = model.unattributedFootnote {
                    Text(footnote)
                        .font(.system(size: 16, weight: .regular))
                        .foregroundColor(Night.inkSecondary)
                }
            } else {
                ForEach(model.clients) { client in
                    VStack(alignment: .leading, spacing: 6) {
                        Text("\(client.recalls)")
                            .font(.system(size: 40, weight: .semibold, design: .serif))
                            .monospacedDigit()
                            .foregroundColor(Night.ink)
                        Text(client.label.uppercased())
                            .font(.system(size: 12, weight: .medium, design: .monospaced))
                            .kerning(1.4)
                            .foregroundColor(Night.inkFaint)
                            .lineLimit(1)
                    }
                    .frame(maxWidth: 220, alignment: .leading)
                }
            }
            Spacer(minLength: 0)
            branding
        }
        .overlay(alignment: .bottomLeading) {
            if !model.clients.isEmpty, let footnote = model.unattributedFootnote {
                Text(footnote)
                    .font(.system(size: 13, weight: .regular, design: .monospaced))
                    .foregroundColor(Night.inkFaint)
                    .offset(y: 26)
            }
        }
    }

    private var firstWeekFoot: some View {
        HStack(alignment: .lastTextBaseline) {
            HStack(spacing: 10) {
                Circle().fill(Night.gold).frame(width: 8, height: 8)
                Text("Check back after a week of use")
                    .font(.system(size: 16, weight: .regular))
                    .foregroundColor(Night.inkSecondary)
            }
            Spacer(minLength: 0)
            branding
        }
    }

    /// The branding: a wax-red seal dot and the wordmark — identical to ConstellationShareCard,
    /// nothing louder, so the two cards are unmistakably the same product.
    private var branding: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Circle()
                .fill(Night.accent)
                .frame(width: 7, height: 7)
            Text("Measured by")
                .font(.system(size: 15, weight: .regular))
                .foregroundColor(Night.inkSecondary)
            Text(DistributionMode.appDisplayName)
                .font(.system(size: 22, weight: .semibold, design: .serif))
                .foregroundColor(Night.ink)
        }
    }
}

// MARK: - Share sheet (built only when opened)

/// A weak handle to the AppKit view planted under the Share button, so the sharing picker's popover
/// can anchor to the button that summoned it. (Named to avoid colliding with MemoryMapView's own.)
private final class WrappedShareAnchor {
    weak var view: NSView?
}

private struct WrappedShareAnchorView: NSViewRepresentable {
    let anchor: WrappedShareAnchor

    func makeNSView(context: Context) -> NSView {
        let view = NSView(frame: .zero)
        anchor.view = view
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        anchor.view = nsView
    }
}

/// The preview sheet behind the Home "Memory Wrapped" entry: renders the card ONCE (2× via
/// ImageRenderer), shows exactly the pixels that would leave the machine, and offers the three
/// exits — the system share picker (the one primary action), copy, and save-as-PNG. Mirrors
/// `ConstellationShareSheet` exactly, so the two share flows behave identically.
struct MemoryWrappedShareSheet: View {
    @ObservedObject var state: AppState
    /// The headline snapshot at the moment the sheet was opened, used only until (and if) a live
    /// refresh lands. The live `state.recallHeadline` is always preferred when present.
    let fallbackHeadline: RecallHeadline

    /// U-DIFF7: always render the freshest headline. Prefer the live published value; fall back to
    /// the snapshot the caller handed in so the card never renders empty during a refresh.
    private var headline: RecallHeadline { state.recallHeadline ?? fallbackHeadline }

    @Environment(\.dismiss) private var dismiss

    @State private var cardImage: NSImage?
    @State private var cardPNG: Data?
    @State private var copied = false
    /// True once a render attempt produced no bitmap — drives the retry state instead of an
    /// infinite spinner with Copy/Save/Share stuck disabled forever.
    @State private var renderFailed = false
    /// The weekly-notification opt-in, mirrored from `MemoryWrappedNotifier` so the toggle actually
    /// flips the persisted flag (the flag was never being set, so the weekly nudge could never fire).
    @State private var notifyWeekly = MemoryWrappedNotifier.isOptedIn
    /// U-DIFF11: true when the user opted in but macOS notification permission is denied — so the
    /// toggle can honestly say the weekly nudge can't fire yet and point at System Settings, rather
    /// than silently flipping a flag that will never deliver.
    @State private var notificationsDenied = false
    /// Held so the picker isn't deallocated out from under its own popover.
    @State private var activePicker: NSSharingServicePicker?
    @State private var shareAnchor = WrappedShareAnchor()

    var body: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
            HStack(alignment: .firstTextBaseline, spacing: CortexDesign.Space.sm) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Your Memory Wrapped")
                        .font(CortexDesign.Typography.title)
                        .foregroundColor(CortexDesign.ink)
                    Text("Your week's recall stats, ready to share. Real numbers only. Post it on purpose.")
                        .font(CortexDesign.Typography.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                }
                Spacer(minLength: 0)
                CortexIconButton(systemImage: "xmark", role: .ghost, size: .small, help: "Close") {
                    dismiss()
                }
                .accessibilityLabel("Close Memory Wrapped preview")
            }

            preview

            HStack(spacing: CortexDesign.Space.sm) {
                CortexButton(title: copied ? "Copied" : "Copy", systemImage: "doc.on.doc", role: .secondary) {
                    copyPNG()
                }
                .disabled(cardPNG == nil)
                CortexButton(title: "Save PNG", systemImage: "square.and.arrow.down", role: .secondary) {
                    savePNG()
                }
                .disabled(cardPNG == nil)
                Spacer(minLength: 0)
                CortexButton(title: "Share…", systemImage: "square.and.arrow.up", role: .primary) {
                    presentSharePicker()
                }
                .disabled(cardImage == nil)
                .background(WrappedShareAnchorView(anchor: shareAnchor))
            }

            weeklyNotifyRow
        }
        .padding(CortexDesign.Space.lg)
        // Taller than the bare thumbnail: the trophy adds a stamp above and a seal below, and the
        // opt-in row sits beneath the exits.
        .frame(minWidth: 684, minHeight: 600)
        .background(CortexDesign.appBackground)
        // U-DIFF7: render immediately from whatever headline we have, then pull the freshest one so
        // the shared card reflects the latest recalls rather than a snapshot from when it was opened.
        .task {
            renderCard()
            await state.loadRecallHeadline()
        }
        // When the live headline lands (or changes), re-rasterize the card so the shared image is
        // never stale. `rerenderCard()` forces a fresh raster (the initial `renderCard` no-ops once a
        // card exists).
        .onChange(of: state.recallHeadline) { _ in
            rerenderCard()
        }
    }

    /// The opt-in the weekly notification was ALWAYS missing: `MemoryWrappedNotifier.setOptedIn` had
    /// no caller anywhere, so `isOptedIn` was permanently false and `maybeNotify` returned at its
    /// opt-in guard every time — the headline weekly nudge could never fire. This design-system
    /// `CortexToggle` is the caller: flipping it writes the persisted flag (via `.onChange`), so the
    /// next headline refresh can actually deliver the "your Memory Wrapped is ready" notification.
    private var weeklyNotifyRow: some View {
        VStack(alignment: .leading, spacing: 3) {
            CortexToggle(title: "Notify me weekly", isOn: $notifyWeekly)
            Text(notifyWeekly
                 ? (notificationsDenied
                    ? "Notifications are off for \(DistributionMode.appDisplayName). Turn them on in System Settings to get your weekly nudge when a new card is ready."
                    : "On. You'll get one nudge a week when a new card is ready, and only on weeks with real recalls.")
                 : "Off. Turn this on for one nudge a week when a new card is ready, only on weeks with real recalls.")
                .font(CortexDesign.Typography.caption)
                .foregroundColor(CortexDesign.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.leading, 42) // aligns under the toggle title, past the switch

            // U-DIFF11: don't quietly flip a flag that can't deliver. When the user opts in but macOS
            // notification permission is denied, say so and offer a jump to System Settings.
            if notifyWeekly && notificationsDenied {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Image(systemName: "exclamationmark.triangle")
                        .foregroundColor(CortexDesign.gold)
                    Text("Notifications are off for \(DistributionMode.appDisplayName), so this nudge can't be delivered yet.")
                        .font(CortexDesign.Typography.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                    CortexButton(title: "Open Settings", role: .ghost, size: .small) {
                        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.notifications") {
                            NSWorkspace.shared.open(url)
                        }
                    }
                }
                .padding(.leading, 42)
                .padding(.top, 2)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(CortexDesign.Space.sm)
        .background(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .fill(CortexDesign.quietBackground)
        )
        .embossedBorder()
        // U-DIFF11 (returning user): the on-open honesty check. `notifyWeekly` initializes from the
        // persisted opt-in, so a returning user who opted in lands here already `true`. Without this,
        // `notificationsDenied` stays false and the row would claim "On. You'll get one nudge a week…"
        // even when macOS permission is actually denied. Read the current authorization WITHOUT ever
        // requesting it (mere appearance must never prompt) and reflect it honestly.
        .task {
            if notifyWeekly {
                checkNotificationPermissionOnAppear()
            }
        }
        // The single place the persisted opt-in flag is written — reads `isOptedIn` on open (State
        // initializer above), writes on every change here.
        .onChange(of: notifyWeekly) { value in
            MemoryWrappedNotifier.setOptedIn(value)
            if value {
                requestNotificationPermissionFeedback()
            } else {
                notificationsDenied = false
            }
        }
        .accessibilityHint("Turns on a once-a-week notification when your Memory Wrapped card is ready.")
    }

    /// U-DIFF11: on opt-in, check macOS notification permission. If it's never been asked, request it
    /// now (so the weekly nudge can fire); if it's denied, surface the honest "can't deliver yet"
    /// notice with a jump to System Settings. All state updates hop back to the main actor.
    private func requestNotificationPermissionFeedback() {
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            switch settings.authorizationStatus {
            case .denied:
                DispatchQueue.main.async { notificationsDenied = true }
            case .notDetermined:
                center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
                    DispatchQueue.main.async { notificationsDenied = !granted }
                }
            default:
                DispatchQueue.main.async { notificationsDenied = false }
            }
        }
    }

    /// U-DIFF11 (returning user): a CHECK-ONLY companion to `requestNotificationPermissionFeedback`.
    /// Runs on sheet appear for the already-opted-in case. It reads the current authorization status
    /// and NEVER calls `requestAuthorization` — mere appearance must not throw a system prompt. Both
    /// `.denied` and `.notDetermined` mean the weekly nudge can't be delivered right now, so the row
    /// flips to the honest "notifications are off" copy with the jump to System Settings.
    private func checkNotificationPermissionOnAppear() {
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            let denied = settings.authorizationStatus == .denied
                || settings.authorizationStatus == .notDetermined
            DispatchQueue.main.async { notificationsDenied = denied }
        }
    }

    @ViewBuilder
    private var preview: some View {
        ConstellationTrophy(
            image: cardImage,
            failed: renderFailed,
            stampText: "MEMORY WRAPPED",
            sealText: DistributionMode.appDisplayName,
            onRetry: { Task { @MainActor in renderCard() } }
        )
    }

    /// Build the model and rasterize at 2× — all of it deferred to sheet-open, so nothing on Home
    /// pays a cost for the card's existence.
    ///
    /// Render can fail (ImageRenderer returns no bitmap under memory pressure): instead of returning
    /// silently and leaving Copy/Save/Share disabled forever behind an endless spinner, it flips
    /// `renderFailed` so the trophy preview offers a Retry.
    @MainActor
    private func renderCard() {
        guard cardImage == nil else { return }
        renderFailed = false
        let model = MemoryWrappedModel.build(from: headline)
        let renderer = ImageRenderer(content: MemoryWrappedCard(model: model))
        renderer.scale = 2
        guard let cgImage = renderer.cgImage else {
            renderFailed = true
            return
        }
        let rep = NSBitmapImageRep(cgImage: cgImage)
        // Point size stays 1200×630 while the pixel grid is 2400×1260 — crisp on retina, correct
        // dimensions everywhere else.
        rep.size = NSSize(width: MemoryWrappedCard.size.width, height: MemoryWrappedCard.size.height)
        cardPNG = rep.representation(using: .png, properties: [:])
        let image = NSImage(size: rep.size)
        image.addRepresentation(rep)
        cardImage = image
    }

    /// U-DIFF7: force a fresh raster when the live headline changes. `renderCard` deliberately no-ops
    /// once a card exists (so the initial `.task` render isn't redone); this clears that guard first
    /// so the newest recall numbers actually make it onto the shared image.
    @MainActor
    private func rerenderCard() {
        cardImage = nil
        cardPNG = nil
        renderCard()
    }

    /// PNG straight onto the general pasteboard (plus TIFF for older paste targets).
    private func copyPNG() {
        guard let data = cardPNG else { return }
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.declareTypes([.png, .tiff], owner: nil)
        pasteboard.setData(data, forType: .png)
        if let tiff = cardImage?.tiffRepresentation {
            pasteboard.setData(tiff, forType: .tiff)
        }
        copied = true
        Task {
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            copied = false
        }
    }

    private func savePNG() {
        guard let data = cardPNG else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.png]
        panel.nameFieldStringValue = "my-memory-wrapped.png"
        panel.canCreateDirectories = true
        panel.isExtensionHidden = false
        if panel.runModal() == .OK, let url = panel.url {
            try? data.write(to: url)
        }
    }

    /// NSSharingServicePicker needs a real AppKit anchor; `WrappedShareAnchorView` plants one under
    /// the Share button so the popover points at the control that summoned it.
    private func presentSharePicker() {
        guard let image = cardImage, let anchorView = shareAnchor.view else { return }
        let picker = NSSharingServicePicker(items: [image])
        activePicker = picker
        picker.show(relativeTo: anchorView.bounds, of: anchorView, preferredEdge: .minY)
    }
}

// MARK: - Home entry

/// The compact "Memory Wrapped" surface on Home, sitting just under the north-star headline card.
/// It only ever renders once the endpoint has answered (`state.recallHeadline != nil`), mirroring
/// `RecallHeadlineCard`'s "never fabricate a cold state" rule. Two honest shapes:
///   - there's a week of real activity → an invitation to open the shareable card;
///   - it's genuinely the first week (no attributed AND no shared reads) → a gentle "check back
///     after a week of use", never a share button that would export an empty card.
struct MemoryWrappedEntry: View {
    @ObservedObject var state: AppState
    @State private var showSheet = false

    var body: some View {
        if let headline = state.recallHeadline {
            let model = MemoryWrappedModel.build(from: headline)
            card(model: model, headline: headline)
                .transition(.opacity)
        }
    }

    @ViewBuilder
    private func card(model: MemoryWrappedModel, headline: RecallHeadline) -> some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.sm) {
            HStack(alignment: .firstTextBaseline, spacing: CortexDesign.Space.sm) {
                Text("MEMORY WRAPPED")
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.gold)
                    .accessibilityHidden(true)
                Spacer(minLength: 0)
                Text(model.windowLine)
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.6)
                    .foregroundColor(CortexDesign.inkFaint)
                    .accessibilityHidden(true)
            }

            Text(model.compactSummary)
                .font(CortexDesign.Typography.display(19))
                .foregroundColor(CortexDesign.ink)
                .fixedSize(horizontal: false, vertical: true)

            if model.isFirstWeek {
                Text("Your shareable weekly card appears here once a week of recalls has passed. Connect an AI to start filling it in.")
                    .font(.callout)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                // U-DIFF4: the first-week card is no longer a dead-end at activation. The primary
                // action gets the user to the one thing that starts filling the card in — connecting
                // an AI — and a quiet secondary lets a curious user preview the (empty) card so the
                // firstWeek card states are reachable rather than dead code (U-DIFF10).
                HStack(spacing: CortexDesign.Space.sm) {
                    CortexButton(title: "Connect an AI", systemImage: "link", role: .primary, size: .small) {
                        state.showConnectionsPrivacy = true
                    }
                    CortexButton(title: "Preview my card", systemImage: "sparkles.rectangle.stack", role: .ghost, size: .small) {
                        showSheet = true
                    }
                    .help("Preview the shareable card. It fills in with real recalls as your AIs read your memory.")
                }
            } else {
                CortexButton(title: "Open your card", systemImage: "sparkles.rectangle.stack", role: .secondary, size: .small) {
                    showSheet = true
                }
                .help("Opens a shareable, screenshot-native card of this week's recall stats: Share, Copy, or Save PNG.")
            }
        }
        // U-DIFF6: the whole card is tappable to open the share sheet (the button stays the visible
        // affordance). Kept off the first-week card so a tap doesn't skip past the "Connect an AI"
        // primary — first-week users open the preview via the explicit ghost button above.
        .contentShape(Rectangle())
        .onTapGesture {
            if !model.isFirstWeek { showSheet = true }
        }
        // Behind the entry, a LIVE downscaled peek of the ACTUAL Wrapped card (only once there's a
        // real card to show) — the exact night-sky object the "Open your card" button reveals,
        // tucked into the trailing edge with a fade so it teases without fighting the ink text. It's
        // the real card view (not a stock graphic), rendered live and deterministically.
        .background(alignment: .trailing) {
            if !model.isFirstWeek {
                cardThumbnail(model: model)
            }
        }
        .cortexCard(padding: CortexDesign.Space.lg, background: CortexDesign.goldSoft)
        .frame(maxWidth: 620, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Memory Wrapped. \(model.compactSummary)")
        .sheet(isPresented: $showSheet) {
            // U-DIFF7: hand the sheet the live AppState (plus the headline snapshot as a fallback) so
            // it re-reads the freshest recall headline and refreshes on open instead of freezing a
            // stale number at the moment the card was tapped.
            MemoryWrappedShareSheet(state: state, fallbackHeadline: headline)
        }
    }

    /// A live, downscaled peek of the REAL `MemoryWrappedCard` — the actual view the share sheet
    /// exports, drawn small behind the entry. Because it's the live SwiftUI card (not a raster or a
    /// stand-in graphic) it stays deterministic (FNV star-dust) and always matches what would leave
    /// the machine. Faded left→right and tucked past the trailing edge so it reads as the card
    /// tucked behind the entry, never as clutter under the text.
    @ViewBuilder
    private func cardThumbnail(model: MemoryWrappedModel) -> some View {
        // The thumbnail's on-screen box; the 1200×630 card is scaled to fit it exactly.
        let thumbW: CGFloat = 208
        let thumbH = thumbW * (MemoryWrappedCard.size.height / MemoryWrappedCard.size.width)
        MemoryWrappedCard(model: model)
            .frame(width: MemoryWrappedCard.size.width, height: MemoryWrappedCard.size.height)
            .scaleEffect(thumbW / MemoryWrappedCard.size.width, anchor: .topLeading)
            .frame(width: thumbW, height: thumbH, alignment: .topLeading)
            .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                    .stroke(CortexDesign.ink.opacity(0.12), lineWidth: 1)
            )
            .rotationEffect(.degrees(-3))
            .shadow(color: CortexDesign.ink.opacity(0.14), radius: 6, y: 3)
            .padding(.trailing, -34)
            // Fade the left edge into the gold ground so it never collides with the ink text.
            .mask(
                LinearGradient(
                    colors: [Color.clear, Color.black.opacity(0.55), Color.black],
                    startPoint: .leading,
                    endPoint: .trailing
                )
            )
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }
}

// MARK: - Weekly local notification (opt-in, isolated)

/// The once-a-week "your Memory Wrapped is ready" nudge — the north-star metric reaching back OUT
/// to the user for a product whose ideal usage is zero app-opens. Strictly opt-in, strictly at most
/// weekly, and never fired for an empty week: a notification with N=0 would be spam, not delight.
///
/// This is a pure read-model over `RecallHeadline` plus two UserDefaults keys, with no dependency on
/// AppState internals — so it can be invoked from the same place the app already refreshes the
/// headline (`bootstrap()` / `loadRecallHeadline()`) without entangling anything. If the opt-in flag
/// is off (the default), `maybeNotify` is a no-op that never touches the notification center.
enum MemoryWrappedNotifier {
    /// UserDefaults opt-in flag — OFF by default. The user turns weekly Wrapped notifications on.
    static let optInDefaultsKey = "memoryWrapped.weeklyNotification.optIn.v1"
    /// The timestamp of the last delivered Wrapped notification, so we never fire more than weekly.
    static let lastDeliveredDefaultsKey = "memoryWrapped.weeklyNotification.lastDelivered.v1"

    /// Roughly a week between nudges; a touch under 7 days so a genuine weekly-open cadence isn't
    /// skipped by a few minutes of clock drift.
    private static let minInterval: TimeInterval = 6.5 * 24 * 60 * 60

    static var isOptedIn: Bool {
        UserDefaults.standard.bool(forKey: optInDefaultsKey)
    }

    static func setOptedIn(_ value: Bool) {
        UserDefaults.standard.set(value, forKey: optInDefaultsKey)
    }

    /// Consider firing the weekly notification for this headline. No-op unless: the user opted in,
    /// there was real attributed activity this week (distinct AIs AND recalls), and at least ~a week
    /// has passed since the last one. Requests authorization only when there's a reason to notify,
    /// and never delivers an empty-week nudge.
    static func maybeNotify(for headline: RecallHeadline, now: Date = Date()) {
        guard isOptedIn else { return }
        // Never spam an empty week: the whole appeal is a real number to celebrate/share.
        guard headline.distinct_ais >= 1, headline.total_recalls >= 1 else { return }

        let defaults = UserDefaults.standard
        if let last = defaults.object(forKey: lastDeliveredDefaultsKey) as? Date,
           now.timeIntervalSince(last) < minInterval {
            return
        }

        let model = MemoryWrappedModel.build(from: headline)
        let title = "Your Memory Wrapped is ready"
        let body = "\(model.notificationTail) this week. Open \(DistributionMode.appDisplayName) to see and share your card."

        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            let deliver = {
                // Stamp the delivery time on the MAIN actor before scheduling so two near-
                // simultaneous callers (bootstrap + a tab refresh) can't both fire.
                DispatchQueue.main.async {
                    if let last = defaults.object(forKey: lastDeliveredDefaultsKey) as? Date,
                       now.timeIntervalSince(last) < minInterval {
                        return
                    }
                    defaults.set(now, forKey: lastDeliveredDefaultsKey)
                    let content = UNMutableNotificationContent()
                    content.title = title
                    content.body = body
                    center.add(UNNotificationRequest(identifier: "memory-wrapped-weekly", content: content, trigger: nil))
                }
            }
            switch settings.authorizationStatus {
            case .authorized, .provisional, .ephemeral:
                deliver()
            case .notDetermined:
                // Ask once, and only because the user opted in and there's a real number to show.
                center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
                    if granted { deliver() }
                }
            default:
                break
            }
        }
    }
}

