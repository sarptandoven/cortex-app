import SwiftUI
import AppKit
import UniformTypeIdentifiers

// MARK: - Wire models (POST /v1/import-diff)
//
// These decode the SHIPPED /v1/import-diff response verbatim. Every field beyond the identity
// of a fact is optional-tolerant so a partial backend response never crashes the compare — the
// view abstains on anything it can't ground rather than fabricating. The honesty rule lives in
// the models too: a `match` is optional, so a "confirmed"/"conflicting" fact WITHOUT a match is
// structurally possible from the server but the view refuses to claim it (see ImportDiffFact.isCited).

/// One memory the backend matched a vendor fact against — the citation. Field shapes mirror the
/// contract exactly (content/source/layer/kind required-ish in practice but decoded tolerant).
struct ImportDiffMatch: Codable, Hashable {
    let memory_id: String?
    let content: String?
    let source: String?
    let source_url: String?
    let layer: String?
    let kind: String?
    let captured_at: String?
}

/// A single fact read out of the user's vendor export, with the verdict against their Mirror.
struct ImportDiffFact: Codable, Hashable, Identifiable {
    let text: String
    let vendor: String?
    let vendor_label: String?
    /// "confirmed" | "conflicting" | "stale" | "missing" (unknown values degrade to .missing bucket-less).
    let status: String
    let score: Double?
    let match: ImportDiffMatch?
    let note: String?
    let captured_at: String?

    // Stable, deterministic identity — never .random. The (status,text) pair is unique enough for a
    // single export, and ForEach needs a stable id so rows don't re-shuffle on re-render.
    var id: String { "\(status)|\(text)" }

    var normalizedStatus: ImportDiffStatus {
        ImportDiffStatus(rawValue: status.lowercased()) ?? .missing
    }

    /// The honesty gate: a "confirmed"/"conflicting"/"stale" claim is only shown as such when it
    /// carries a real citation the user can inspect. Without a match we cannot honestly say Cortex
    /// agrees or disagrees, so such a fact is treated as uncited and rendered without the verdict.
    var isCited: Bool {
        guard let match else { return false }
        let hasContent = !(match.content ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return hasContent || !(match.memory_id ?? "").isEmpty
    }
}

/// A memory Cortex holds that the vendor export did NOT mention — "what your export missed".
struct ImportDiffCortexOnly: Codable, Hashable, Identifiable {
    let memory_id: String?
    let content: String?
    let source: String?
    let source_url: String?
    let layer: String?
    let kind: String?
    let captured_at: String?

    var id: String { memory_id ?? (content ?? UUID().uuidString) }
}

struct ImportDiffSummary: Codable, Hashable {
    let total: Int?
    let confirmed: Int?
    let conflicting: Int?
    let stale: Int?
    let missing: Int?
    let cortex_only: Int?
}

struct ImportDiffParsed: Codable, Hashable {
    let vendor: String?
    let vendor_label: String?
    let count: Int?
    let source: String?
}

/// The whole decoded /v1/import-diff response.
struct ImportDiffResult: Codable, Hashable {
    /// "embedding" | "keyword" — an honest degradation signal. Keyword means the semantic embedder
    /// was unavailable and matches are shallower; the footer says so plainly.
    let method: String?
    let provider: String?
    let vendor: String?
    let vendor_label: String?
    let summary: ImportDiffSummary?
    let facts: [ImportDiffFact]?
    let cortex_only: [ImportDiffCortexOnly]?
    let parsed: ImportDiffParsed?
    let caveats: [String]?

    var isKeywordFallback: Bool {
        (method ?? "").lowercased() == "keyword"
    }

    /// The vendor's user-facing name, preferring the top-level label, then parsed, then a titleized
    /// vendor id. Never leaks a raw lowercase vendor id.
    var displayVendorLabel: String {
        let candidates = [vendor_label, parsed?.vendor_label]
        for candidate in candidates {
            if let value = candidate?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty {
                return value
            }
        }
        let raw = (vendor ?? parsed?.vendor ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return raw.isEmpty ? "your AI" : SourceDisplayName.label(raw)
    }

    /// How many facts the backend actually read from the export — never inflated past what it returned.
    var factsRead: Int {
        if let count = parsed?.count { return count }
        return facts?.count ?? 0
    }
}

// MARK: - Status vocabulary

/// The four verdicts, each with its own tone. Coloring reuses the design system's semantic palette:
/// moss for agreement, gold for tension, and a quiet blue for "Cortex doesn't know this yet".
enum ImportDiffStatus: String, CaseIterable {
    case confirmed
    case conflicting
    case stale
    case missing

    var title: String {
        switch self {
        case .confirmed: return "Confirmed"
        case .conflicting: return "Conflicting"
        case .stale: return "Out of date"
        case .missing: return "\(DistributionMode.appDisplayName) doesn't know this"
        }
    }

    var systemImage: String {
        switch self {
        case .confirmed: return "checkmark.seal"
        case .conflicting: return "exclamationmark.triangle"
        case .stale: return "clock.arrow.circlepath"
        case .missing: return "questionmark.circle"
        }
    }

    var tone: Color {
        switch self {
        case .confirmed: return CortexDesign.sealMoss
        case .conflicting, .stale: return CortexDesign.gold
        case .missing: return Color(red: 0.24, green: 0.44, blue: 0.62) // quiet archival blue
        }
    }

    var blurb: String {
        switch self {
        case .confirmed: return "Your export and \(DistributionMode.appDisplayName) agree."
        case .conflicting: return "Your export says something \(DistributionMode.appDisplayName) remembers differently."
        case .stale: return "\(DistributionMode.appDisplayName) has a newer version of this."
        case .missing: return "This isn't in your \(DistributionMode.appDisplayName) yet."
        }
    }
}

// MARK: - Import-diff view

/// "What the AIs think of you." Paste or drop a vendor memory export (ChatGPT / Claude / Gemini)
/// and see it compared, with citations, against your Cortex Mirror. Every "confirmed" or
/// "conflicting" verdict is grounded in a tap-through citation; missing facts offer a one-tap
/// "Add to Cortex" using the app's existing capture path. Nothing is claimed beyond what the
/// backend returned.
struct ImportDiffView: View {
    @ObservedObject var state: AppState
    var onClose: (() -> Void)? = nil

    /// Auto = let the server sniff the vendor; otherwise a hint is sent.
    enum VendorChoice: String, CaseIterable, Identifiable {
        case auto, chatgpt, claude, gemini
        var id: String { rawValue }
        var label: String {
            switch self {
            case .auto: return "Auto-detect"
            case .chatgpt: return "ChatGPT"
            case .claude: return "Claude"
            case .gemini: return "Gemini"
            }
        }
        /// A vendor glyph for the pill segmented control — the archive's stamp for each source.
        var glyph: String {
            switch self {
            case .auto: return "sparkle.magnifyingglass"
            case .chatgpt: return "bubble.left.and.text.bubble.right"
            case .claude: return "a.square"
            case .gemini: return "diamond"
            }
        }
        /// The wire value; nil for auto (the server sniffs).
        var wireValue: String? { self == .auto ? nil : rawValue }
    }

    @State private var pasted: String = ""
    @State private var vendor: VendorChoice = .auto
    @State private var result: ImportDiffResult?
    @State private var comparing = false
    @State private var errorText: String?
    /// Facts the user has already added this session, so the "Add to Cortex" button can confirm
    /// per-row without re-fetching. Keyed by the fact's stable id.
    @State private var addedFactIDs: Set<String> = []
    /// Highlight state for the drop zone while a file hovers over it.
    @State private var dropTargeted = false
    /// Facts the user chose to keep Cortex's version of (dismissed the vendor's claim). Keyed by the
    /// fact's stable id so a conflicting/stale row can be resolved without re-fetching.
    @State private var keptCortexFactIDs: Set<String> = []
    /// True while a batch "Add all" is running; carries the running progress "Adding 3/25…".
    @State private var batchAddCount: Int = 0
    @State private var batchAddTotal: Int = 0

    /// UserDefaults keys for U-DIFF9: persist the last compare so reopening the surface (or comparing
    /// a second vendor) doesn't silently drop the previous verdict. Stored locally here rather than on
    /// AppState so this surface owns its own resume state.
    private static let lastDiffDefaultsKey = "cortexImportDiff.lastResult.v1"
    private static let lastDiffAddedDefaultsKey = "cortexImportDiff.lastAddedIDs.v1"

    /// U-DIFF3: presents the shareable verdict card sheet.
    @State private var showShareSheet = false
    /// Feedback for the "Copy summary" fallback action.
    @State private var summaryCopied = false

    private var trimmedInput: String {
        pasted.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(CortexDesign.hairline)
            ScrollView {
                VStack(alignment: .leading, spacing: CortexDesign.Space.lg) {
                    inputSection
                    if let result {
                        resultsSection(result)
                    } else if comparing {
                        comparingState
                    } else {
                        introState
                    }
                }
                .frame(maxWidth: 680, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.horizontal, CortexDesign.Space.xl)
                .padding(.vertical, CortexDesign.Space.lg)
            }
        }
        .background(CortexDesign.appBackground)
    }

    // MARK: Header

    private var header: some View {
        HStack(alignment: .center, spacing: CortexDesign.Space.md) {
            VStack(alignment: .leading, spacing: 3) {
                Text("What the AIs think of you")
                    .font(CortexDesign.Typography.title)
                    .foregroundColor(CortexDesign.ink)
                Text("Paste a ChatGPT, Claude, or Gemini memory export and see it checked, with citations, against your \(DistributionMode.appDisplayName).")
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: CortexDesign.Space.md)
            if let onClose {
                CortexIconButton(systemImage: "xmark", role: .ghost, size: .regular, help: "Close") {
                    onClose()
                }
            }
        }
        .padding(.horizontal, CortexDesign.Space.xl)
        .padding(.vertical, CortexDesign.Space.md)
    }

    // MARK: Input

    private var inputSection: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.sm) {
            HStack(spacing: CortexDesign.Space.sm) {
                vendorPicker

                Spacer(minLength: CortexDesign.Space.sm)

                CortexButton(title: "Open export file…", systemImage: "folder", role: .ghost, size: .small) {
                    openExportFile()
                }
                .disabled(comparing)
            }

            pasteWell

            dropZone

            HStack(spacing: CortexDesign.Space.sm) {
                CortexButton(
                    title: comparing ? "Comparing…" : "Compare",
                    systemImage: "arrow.left.arrow.right",
                    role: .primary,
                    size: .regular
                ) {
                    compare()
                }
                .disabled(comparing || trimmedInput.isEmpty)

                if result != nil || !trimmedInput.isEmpty {
                    CortexButton(title: "Clear", role: .ghost, size: .regular) {
                        pasted = ""
                        result = nil
                        errorText = nil
                        addedFactIDs = []
                    }
                    .disabled(comparing)
                }

                Spacer(minLength: 0)
            }

            if let errorText {
                Text(errorText)
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// A pill segmented control carrying each vendor's glyph — replaces the stock menu Picker so the
    /// source choice reads as a stamp shelf, not a System-Settings dropdown. One tap sets the hint.
    private var vendorPicker: some View {
        HStack(spacing: 0) {
            ForEach(VendorChoice.allCases) { choice in
                let selected = vendor == choice
                Button {
                    vendor = choice
                } label: {
                    HStack(spacing: 5) {
                        Image(systemName: choice.glyph)
                            .font(.system(size: 11, weight: .semibold))
                        Text(choice.label)
                            .font(.system(size: 12, weight: selected ? .semibold : .medium))
                    }
                    .foregroundColor(selected ? CortexDesign.panelBackground : CortexDesign.inkSecondary)
                    .padding(.horizontal, 12)
                    .frame(minHeight: 28)
                    .background(
                        RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                            .fill(selected ? CortexDesign.accent : Color.clear)
                    )
                    .contentShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous))
                }
                .buttonStyle(.plain)
                .disabled(comparing)
            }
        }
        .padding(3)
        .background(CortexDesign.quietBackground)
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        .embossedBorder(radius: CortexDesign.Radius.md)
        .animation(CortexMotion.press, value: vendor)
    }

    /// The paste well in the CortexField idiom: a quiet recessed ground, letterpress edge, and a wax
    /// focus ring — the multiline sibling of the design-system text field (TextEditor has no styled
    /// variant, so the field chrome is applied around it here).
    private var pasteWell: some View {
        ZStack(alignment: .topLeading) {
            if pasted.isEmpty {
                Text("Paste your export here. The raw text or JSON is fine.")
                    .font(CortexDesign.Typography.prose(13))
                    .foregroundColor(CortexDesign.inkFaint)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 12)
                    .allowsHitTesting(false)
            }
            TextEditor(text: $pasted)
                .font(CortexDesign.Typography.prose(13))
                .foregroundColor(CortexDesign.ink)
                .scrollContentBackground(.hidden)
                .padding(.horizontal, 8)
                .padding(.vertical, 6)
                .frame(minHeight: 120, maxHeight: 200)
        }
        .background(CortexDesign.quietBackground)
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        .embossedBorder(radius: CortexDesign.Radius.md)
    }

    /// A dashed drop zone matching the ChatGPT/Claude importer's affordance: drop the export file
    /// (a .zip, .json, .jsonl, or plain text) and its contents load into the paste well. This is the
    /// path a user who "downloaded the .zip" actually takes.
    private var dropZone: some View {
        RoundedRectangle(cornerRadius: 10, style: .continuous)
            .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6]))
            .foregroundColor(dropTargeted ? CortexDesign.accent : CortexDesign.hairline)
            .frame(height: 54)
            .overlay(
                HStack(spacing: 8) {
                    Image(systemName: "arrow.down.doc")
                        .foregroundColor(dropTargeted ? CortexDesign.accent : CortexDesign.inkFaint)
                    Text("Drop your export file here. A .zip is fine")
                        .font(CortexDesign.Typography.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                }
            )
            .animation(CortexMotion.press, value: dropTargeted)
            .onDrop(of: [.fileURL], isTargeted: $dropTargeted) { providers in
                guard let provider = providers.first else { return false }
                provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                    var url: URL?
                    if let data = item as? Data {
                        url = URL(dataRepresentation: data, relativeTo: nil)
                    } else if let dropped = item as? URL {
                        url = dropped
                    }
                    guard let resolved = url?.standardizedFileURL else { return }
                    Task { @MainActor in loadExportFile(at: resolved) }
                }
                return true
            }
    }

    // MARK: Empty / transient states

    private var introState: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
            CortexEmptyState(
                systemImage: "sparkle.magnifyingglass",
                title: "See what an AI remembers about you",
                message: "Export your memory from ChatGPT, Claude, or Gemini, paste it above, and \(DistributionMode.appDisplayName) will tell you what it can confirm, what conflicts, and what it already knew that the export missed."
            )
            .frame(maxWidth: .infinity)

            // U-DIFF9: offer to bring back the last compare rather than starting cold.
            if hasSavedDiff {
                HStack(spacing: CortexDesign.Space.sm) {
                    Image(systemName: "clock.arrow.circlepath")
                        .foregroundColor(CortexDesign.accent)
                    Text("You have a saved result from your last compare.")
                        .font(CortexDesign.Typography.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                    Spacer(minLength: 0)
                    CortexButton(title: "Show it again", role: .ghost, size: .small) {
                        restoreLastDiff()
                    }
                }
                .padding(CortexDesign.Space.md)
                .background(
                    RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                        .fill(CortexDesign.quietBackground)
                )
                .embossedBorder(radius: CortexDesign.Radius.md)
            }

            // U-DIFF8: a quick-win row — where to find the export button in each AI, and a bundled
            // sample so a curious user can see the result before they've exported anything.
            introHelpRow
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, CortexDesign.Space.md)
    }

    /// U-DIFF8: concrete "how to export" deep links per vendor plus a "Try a sample" that loads a
    /// small bundled export so the compare can be seen immediately.
    private var introHelpRow: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.sm) {
            Text("WHERE TO GET YOUR EXPORT")
                .font(CortexDesign.Typography.stamp)
                .kerning(0.8)
                .foregroundColor(CortexDesign.inkFaint)
            HStack(spacing: CortexDesign.Space.sm) {
                CortexButton(title: "ChatGPT", systemImage: "arrow.up.right.square", role: .ghost, size: .small) {
                    openExportHelp(.chatgpt)
                }
                CortexButton(title: "Claude", systemImage: "arrow.up.right.square", role: .ghost, size: .small) {
                    openExportHelp(.claude)
                }
                CortexButton(title: "Gemini", systemImage: "arrow.up.right.square", role: .ghost, size: .small) {
                    openExportHelp(.gemini)
                }
                Spacer(minLength: 0)
                CortexButton(title: "Try a sample", systemImage: "wand.and.stars", role: .secondary, size: .small) {
                    loadSampleExport()
                }
                .disabled(comparing)
            }
            Text("Each AI emails your export as a file. Download it, then drop it above. We never send the export anywhere except your own \(DistributionMode.appDisplayName). For Gemini, open Takeout, then pick \u{201C}My Activity\u{201D} \u{2192} \u{201C}Gemini Apps\u{201D}.")
                .font(CortexDesign.Typography.caption)
                .foregroundColor(CortexDesign.inkFaint)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(CortexDesign.Space.md)
        .background(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .fill(CortexDesign.quietBackground)
        )
        .embossedBorder(radius: CortexDesign.Radius.md)
    }

    private var comparingState: some View {
        HStack(spacing: CortexDesign.Space.sm) {
            ProgressView().scaleEffect(0.8)
            Text("Reading your export and checking it against your \(DistributionMode.appDisplayName)…")
                .font(CortexDesign.Typography.caption)
                .foregroundColor(CortexDesign.inkSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .center)
        .padding(.top, CortexDesign.Space.lg)
    }

    // MARK: Results

    @ViewBuilder
    private func resultsSection(_ result: ImportDiffResult) -> some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.lg) {
            summaryHeader(result)

            shareRow(result)

            let facts = result.facts ?? []
            let confirmed = facts.filter { $0.normalizedStatus == .confirmed }
            let tension = facts.filter { $0.normalizedStatus == .conflicting || $0.normalizedStatus == .stale }
            let missing = facts.filter { $0.normalizedStatus == .missing }

            if !confirmed.isEmpty {
                factGroup(status: .confirmed, facts: confirmed, result: result)
            }
            if !tension.isEmpty {
                // Conflicting and stale share the amber column; each row still shows its own status.
                factGroup(status: .conflicting, facts: tension, result: result, headerOverride: "Conflicting or out of date")
            }
            if !missing.isEmpty {
                factGroup(status: .missing, facts: missing, result: result)
            }

            if facts.isEmpty {
                Text("\(DistributionMode.appDisplayName) couldn't read any facts from that export. Try pasting the raw export text or JSON.")
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            cortexOnlySection(result)

            footnotes(result)
        }
    }

    /// The hero verdict band: two ledgers, side by side. On the left, the export's ink field — the
    /// serif count it confirms and disputes; on the right, the moss field — what Cortex already
    /// remembered that the export forgot. Large New York numerals read as a headline, not a chip row.
    private func summaryHeader(_ result: ImportDiffResult) -> some View {
        let summary = result.summary
        let confirms = summary?.confirmed ?? 0
        let disputes = (summary?.conflicting ?? 0) + (summary?.stale ?? 0)
        let remembers = summary?.cortex_only ?? 0
        let missing = summary?.missing ?? 0
        let n = result.factsRead

        return VStack(alignment: .leading, spacing: 0) {
            Text("We read \(n) fact\(n == 1 ? "" : "s") from your \(result.displayVendorLabel) export.")
                .font(CortexDesign.Typography.prose(15))
                .foregroundColor(CortexDesign.ink)
                .fixedSize(horizontal: false, vertical: true)
                .padding(CortexDesign.Space.lg)

            // Split ink/moss field: what the export got right/wrong vs. what your Cortex already held.
            HStack(spacing: 0) {
                verdictLedger(
                    title: "Your export",
                    stats: [
                        (confirms, "confirms", CortexDesign.sealMoss),
                        (disputes, "disputes", CortexDesign.gold),
                        (missing, "new to \(DistributionMode.appDisplayName)", ImportDiffStatus.missing.tone),
                    ],
                    ground: CortexDesign.quietBackground
                )
                Rectangle()
                    .fill(CortexDesign.hairline)
                    .frame(width: 1)
                verdictLedger(
                    title: "Your \(DistributionMode.appDisplayName)",
                    stats: [
                        (remembers, "it already knew your export forgot", CortexDesign.sealMoss),
                    ],
                    ground: CortexDesign.sealMoss.opacity(0.08)
                )
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(CortexDesign.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        .embossedBorder(radius: CortexDesign.Radius.md)
        .shadow(color: CortexDesign.Elevation.rest.ambient.color, radius: CortexDesign.Elevation.rest.ambient.radius, y: CortexDesign.Elevation.rest.ambient.y)
        .archiveSpine(CortexDesign.accent)
    }

    /// U-DIFF3: the share affordance under the verdict band. "Share result…" opens a night-sky
    /// share card mirroring Memory Wrapped; "Copy summary" is the honest fallback that puts the
    /// plain verdict counts on the clipboard. Both use only numbers the backend actually returned.
    @ViewBuilder
    private func shareRow(_ result: ImportDiffResult) -> some View {
        HStack(spacing: CortexDesign.Space.sm) {
            CortexButton(title: "Share result…", systemImage: "square.and.arrow.up", role: .secondary, size: .small) {
                showShareSheet = true
            }
            CortexButton(title: summaryCopied ? "Copied" : "Copy summary", systemImage: "doc.on.doc", role: .ghost, size: .small) {
                copySummary(result)
            }
            Spacer(minLength: 0)
        }
        .sheet(isPresented: $showShareSheet) {
            ImportDiffShareSheet(model: ImportDiffShareModel.build(from: result))
        }
    }

    /// The honest text fallback for U-DIFF3: verdict counts, vendor, and a made-of-Cortex line.
    private func copySummary(_ result: ImportDiffResult) {
        let model = ImportDiffShareModel.build(from: result)
        let text = model.clipboardSummary
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(text, forType: .string)
        summaryCopied = true
        Task {
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            summaryCopied = false
        }
    }

    /// One column of the verdict band: a mono field title over a stack of large serif numerals.
    private func verdictLedger(title: String, stats: [(count: Int, label: String, tone: Color)], ground: Color) -> some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.sm) {
            Text(title.uppercased())
                .font(CortexDesign.Typography.stamp)
                .kerning(0.8)
                .foregroundColor(CortexDesign.inkFaint)
            ForEach(Array(stats.enumerated()), id: \.offset) { _, stat in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("\(stat.count)")
                        .font(.system(size: 34, weight: .semibold, design: .serif))
                        .monospacedDigit()
                        .foregroundColor(stat.tone)
                    Text(stat.label)
                        .font(CortexDesign.Typography.body)
                        .foregroundColor(CortexDesign.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(CortexDesign.Space.lg)
        .background(ground)
    }

    @ViewBuilder
    private func factGroup(status: ImportDiffStatus, facts: [ImportDiffFact], result: ImportDiffResult, headerOverride: String? = nil) -> some View {
        // How many of these facts still have an "Add to Cortex" action outstanding — the ones that
        // aren't already added. Drives the batch "Add all" button's enabled state and count.
        let addable = facts.filter { !addedFactIDs.contains($0.id) }
        VStack(alignment: .leading, spacing: CortexDesign.Space.sm) {
            HStack(spacing: 8) {
                Image(systemName: status.systemImage)
                    .foregroundColor(status.tone)
                Text(headerOverride ?? status.title)
                    .font(CortexDesign.Typography.title)
                    .foregroundColor(CortexDesign.ink)
                Text("\(facts.count)")
                    .font(CortexDesign.Typography.caption)
                    .monospacedDigit()
                    .foregroundColor(CortexDesign.inkSecondary)
                Spacer(minLength: 0)

                // U-DIFF1: batch "Add all N to Cortex" for the missing group — one tap to import
                // everything the export knew that Cortex didn't. Shows live progress and disables
                // once nothing is left to add.
                if status == .missing {
                    if batchAddTotal > 0 {
                        CortexButton(
                            title: "Adding \(batchAddCount)/\(batchAddTotal)…",
                            systemImage: "plus.circle",
                            role: .secondary,
                            size: .small
                        ) {}
                        .disabled(true)
                    } else if !addable.isEmpty {
                        CortexButton(
                            title: "Add all \(addable.count) to \(DistributionMode.appDisplayName)",
                            systemImage: "plus.circle",
                            role: .secondary,
                            size: .small
                        ) {
                            Task { await addAllMissing(addable, vendorLabel: result.displayVendorLabel) }
                        }
                    } else {
                        Label("All added", systemImage: "checkmark.seal.fill")
                            .font(CortexDesign.Typography.caption)
                            .foregroundColor(CortexDesign.sealMoss)
                    }
                }
            }
            ForEach(facts) { fact in
                ImportDiffFactRow(
                    fact: fact,
                    vendorLabel: result.displayVendorLabel,
                    alreadyAdded: addedFactIDs.contains(fact.id),
                    keptCortex: keptCortexFactIDs.contains(fact.id),
                    onAdd: { await addToCortex(fact) },
                    onKeepCortex: { keptCortexFactIDs.insert(fact.id) },
                    onOpenMatch: { openMatch(fact.match) }
                )
            }
        }
    }

    @ViewBuilder
    private func cortexOnlySection(_ result: ImportDiffResult) -> some View {
        let items = result.cortex_only ?? []
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: CortexDesign.Space.sm) {
                HStack(spacing: 8) {
                    Image(systemName: "tray.full")
                        .foregroundColor(CortexDesign.accent)
                    Text("What your export missed")
                        .font(CortexDesign.Typography.title)
                        .foregroundColor(CortexDesign.ink)
                    Text("\(items.count)")
                        .font(CortexDesign.Typography.caption)
                        .monospacedDigit()
                        .foregroundColor(CortexDesign.inkSecondary)
                    Spacer(minLength: 0)
                }
                Text("\(DistributionMode.appDisplayName) already remembers these; your \(result.displayVendorLabel) export doesn't.")
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(items) { item in
                    ImportDiffCortexOnlyRow(item: item)
                }
            }
        }
    }

    @ViewBuilder
    private func footnotes(_ result: ImportDiffResult) -> some View {
        let caveats = (result.caveats ?? [])
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        if result.isKeywordFallback || !caveats.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                if result.isKeywordFallback {
                    Label(
                        "Matched by keyword, not meaning: the semantic embedder wasn't available, so these matches are rougher than usual.",
                        systemImage: "info.circle"
                    )
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.inkFaint)
                    .fixedSize(horizontal: false, vertical: true)
                }
                ForEach(caveats, id: \.self) { caveat in
                    Text("· \(caveat)")
                        .font(CortexDesign.Typography.caption)
                        .foregroundColor(CortexDesign.inkFaint)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, CortexDesign.Space.sm)
        }
    }

    // MARK: Actions

    private func compare() {
        let input = trimmedInput
        guard !input.isEmpty, !comparing else { return }
        comparing = true
        errorText = nil
        result = nil
        addedFactIDs = []
        Task {
            let diff = await state.importDiff(export: input, vendor: vendor.wireValue)
            comparing = false
            if let diff {
                result = diff
                persistLastDiff()
            } else {
                errorText = "Couldn't compare that export right now. Check that \(DistributionMode.appDisplayName) is running and try again."
            }
        }
    }

    // MARK: Last-compare persistence (U-DIFF9)

    /// Persist the current result plus the set of facts already added, so reopening the surface (or
    /// running a second vendor and coming back) can restore the last verdict instead of starting cold.
    private func persistLastDiff() {
        let defaults = UserDefaults.standard
        if let result, let data = try? JSONEncoder().encode(result) {
            defaults.set(data, forKey: Self.lastDiffDefaultsKey)
            defaults.set(Array(addedFactIDs), forKey: Self.lastDiffAddedDefaultsKey)
        }
    }

    /// True when a previously-saved compare exists that isn't already loaded — drives the "resume"
    /// chip in the intro state.
    private var hasSavedDiff: Bool {
        result == nil && UserDefaults.standard.data(forKey: Self.lastDiffDefaultsKey) != nil
    }

    /// Restore the last saved compare into the view (U-DIFF9). Best-effort: a stale/undecodable blob
    /// is quietly cleared rather than surfaced as an error.
    private func restoreLastDiff() {
        let defaults = UserDefaults.standard
        guard let data = defaults.data(forKey: Self.lastDiffDefaultsKey),
              let saved = try? JSONDecoder().decode(ImportDiffResult.self, from: data) else {
            defaults.removeObject(forKey: Self.lastDiffDefaultsKey)
            defaults.removeObject(forKey: Self.lastDiffAddedDefaultsKey)
            return
        }
        result = saved
        addedFactIDs = Set(defaults.stringArray(forKey: Self.lastDiffAddedDefaultsKey) ?? [])
        errorText = nil
    }

    private func addToCortex(_ fact: ImportDiffFact) async {
        let text = fact.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        let added = await state.addImportDiffFact(text: text, vendorLabel: fact.vendor_label ?? result?.displayVendorLabel ?? "")
        if added {
            addedFactIDs.insert(fact.id)
            persistLastDiff()
        }
    }

    /// U-DIFF1: add every missing fact in one action, one after another, so the user never taps
    /// through 25 rows. Progress is reflected in the header button title ("Adding 3/25…"). Facts
    /// that fail to add stay actionable per-row (their id just never enters `addedFactIDs`).
    @MainActor
    private func addAllMissing(_ facts: [ImportDiffFact], vendorLabel: String) async {
        guard batchAddTotal == 0 else { return }
        batchAddCount = 0
        batchAddTotal = facts.count
        defer {
            batchAddTotal = 0
            batchAddCount = 0
        }
        for fact in facts {
            batchAddCount += 1
            if addedFactIDs.contains(fact.id) { continue }
            let text = fact.text.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { continue }
            let added = await state.addImportDiffFact(text: text, vendorLabel: fact.vendor_label ?? vendorLabel)
            if added {
                addedFactIDs.insert(fact.id)
            }
        }
        persistLastDiff()
    }

    /// U-DIFF2 / U-DIFF5: open the internal Cortex memory a fact was matched against. There's no
    /// dedicated memory-detail-by-id surface, so this reuses the app's established "explore a memory"
    /// path: seed the Ask tab with the memory's own text and run a real retrieval, exactly as the
    /// Constellation node tap does. Never fabricates a link when there's nothing to open.
    private func openMatch(_ match: ImportDiffMatch?) {
        guard let match else { return }
        // Prefer the actual openable source URL if the memory carries one (a file/web citation).
        if let url = CitationDisplay.openableURL(path: nil, sourceURL: match.source_url) {
            NSWorkspace.shared.open(url)
            return
        }
        // Otherwise pivot into Ask on the memory's own content so the user lands on it in context.
        let seed = (match.content ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !seed.isEmpty else { return }
        state.searchQuery = String(seed.prefix(200))
        state.selectedTab = .ask
        state.runSearch()
        onClose?()
    }

    /// U-DIFF8: open the vendor's own data-export page so the user can start the export in one click.
    /// These are the real, current export destinations for each provider.
    private func openExportHelp(_ choice: VendorChoice) {
        let urlString: String
        switch choice {
        case .chatgpt: urlString = "https://chatgpt.com/#settings/DataControls"
        case .claude: urlString = "https://claude.ai/settings/data-privacy-controls"
        // Gemini chat history lives under Takeout's "My Activity" product (the standalone "Gemini"
        // product is Gems, not conversations), so deep-link straight to My Activity pre-selected;
        // the user then narrows it to "Gemini Apps" (see the caption copy).
        case .gemini: urlString = "https://takeout.google.com/settings/takeout/custom/my_activity"
        case .auto: urlString = "https://takeout.google.com/"
        }
        if let url = URL(string: urlString) {
            NSWorkspace.shared.open(url)
        }
    }

    /// U-DIFF8: load a small bundled sample export into the paste well and compare it, so a first-time
    /// visitor can see exactly what the result looks like before exporting their own memory. The sample
    /// is a plainly-labeled illustration, not the user's data; the compare runs against their real
    /// Cortex, so the missing/confirmed split is still honest for whatever they actually hold.
    private func loadSampleExport() {
        vendor = .chatgpt
        pasted = Self.sampleExportText
        errorText = nil
        result = nil
        compare()
    }

    /// A compact, obviously-illustrative ChatGPT-style memory export. Plain text so the backend reads
    /// it the same way it reads a real paste. Kept short so the sample compare returns quickly.
    private static let sampleExportText = """
    Sample memory export (illustration only)

    - The user is a founder building a macOS app for personal memory.
    - Prefers concise, direct answers without filler.
    - Is based in the San Francisco Bay Area.
    - Enjoys long-distance running on weekends.
    - Is learning to play the piano.
    - Cares deeply about user privacy and local-first software.
    """

    private func openExportFile() {
        let panel = NSOpenPanel()
        panel.title = "Open export file"
        panel.message = "Open a ChatGPT, Claude, or Gemini memory export: the .zip, its .json/.jsonl, or a plain-text file."
        panel.prompt = "Open"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [
            .zip,
            .json,
            .plainText,
            .text,
            UTType(filenameExtension: "jsonl") ?? .data,
        ]
        if panel.runModal() == .OK, let url = panel.url {
            loadExportFile(at: url)
        }
    }

    /// Load a picked/dropped export into the paste well. Accepts a plain-text/JSON file directly, and
    /// — this is the fix for the .zip the sibling importer tells users to download — accepts a .zip by
    /// extracting it to a temp dir and pulling the memory/export JSON text out. There is no reachable
    /// Swift-side unzip utility in the app (the chat-import path unzips on the backend, which takes a
    /// path; import-diff takes text), so extraction shells out to `/usr/bin/ditto` (always present on
    /// macOS). REPORTED to the caller: a central `AppState.extractExportText(fromZip:)` would be the
    /// cleaner home for this.
    private func loadExportFile(at url: URL) {
        let started = url.startAccessingSecurityScopedResource()
        defer { if started { url.stopAccessingSecurityScopedResource() } }

        let isZip = url.pathExtension.lowercased() == "zip"
        if isZip {
            switch ImportDiffZipExtractor.extractExportText(fromZip: url) {
            case .success(let text):
                pasted = text
                errorText = nil
            case .failure(let message):
                errorText = message
            }
            return
        }

        if let text = try? String(contentsOf: url, encoding: .utf8) {
            pasted = text
            errorText = nil
        } else {
            errorText = "Couldn't read that file as text. If it's a .zip, drop it here to unzip it, or paste the export contents instead."
        }
    }
}

// MARK: - Zip export extraction

/// Pulls the readable export text out of a ChatGPT/Claude/Gemini memory-export .zip. These exports
/// bundle the actual data as JSON (conversations.json, chat.json, memory.json, user.json…) inside a
/// zip; import-diff wants that text, not a path. There is no reachable in-app unzip helper (the chat
/// importer hands the .zip path to the backend, which unzips server-side), so this extracts with
/// `/usr/bin/ditto -x -k` (bundled on every macOS) into a scratch dir, then joins the export-ish
/// text files it finds. Deterministic file ordering (sorted by relevance then path — no reliance on
/// filesystem order). Best-effort and self-cleaning; never crashes the surface.
enum ImportDiffZipExtractor {
    enum Result {
        case success(String)
        case failure(String)
    }

    /// Names (case-insensitive substrings) that mark a file as the export's memory/chat payload —
    /// preferred first so the compare reads the richest file even when a zip carries several JSONs.
    private static let preferredNameHints = ["memory", "conversation", "chat", "message", "user", "export"]
    private static let textExtensions: Set<String> = ["json", "jsonl", "txt", "md", "ndjson"]
    /// Guardrail: don't try to load an unreasonably large blob into the paste well / request body.
    private static let maxTotalBytes = 12 * 1024 * 1024

    static func extractExportText(fromZip zipURL: URL) -> Result {
        let fm = FileManager.default
        let scratch = fm.temporaryDirectory
            .appendingPathComponent("cortex-import-diff", isDirectory: true)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? fm.removeItem(at: scratch) }
        do {
            try fm.createDirectory(at: scratch, withIntermediateDirectories: true)
        } catch {
            return .failure("Couldn't open that .zip: no room to unzip it. Paste the export contents instead.")
        }

        // /usr/bin/ditto -x -k <zip> <dest> extracts a PKZip archive. It ships with macOS, so no
        // third-party dependency is added.
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
        process.arguments = ["-x", "-k", zipURL.path, scratch.path]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            process.waitUntilExit()
        } catch {
            return .failure("Couldn't unzip that export. Paste the export contents instead.")
        }
        guard process.terminationStatus == 0 else {
            return .failure("That .zip couldn't be read. Try re-downloading the export, or paste its contents instead.")
        }

        // Collect candidate text files.
        var candidates: [(url: URL, size: Int)] = []
        if let walker = fm.enumerator(at: scratch, includingPropertiesForKeys: [.isRegularFileKey, .fileSizeKey]) {
            for case let fileURL as URL in walker {
                guard textExtensions.contains(fileURL.pathExtension.lowercased()) else { continue }
                let values = try? fileURL.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
                guard values?.isRegularFile == true else { continue }
                candidates.append((fileURL, values?.fileSize ?? 0))
            }
        }
        guard !candidates.isEmpty else {
            return .failure("No readable export was found in that .zip. Open the export's conversations.json or memory file instead.")
        }

        // Deterministic priority: preferred-name files first (in hint order), then everything else,
        // each group ordered by descending size then path so the ordering never depends on the
        // filesystem's enumeration order.
        func priority(_ url: URL) -> Int {
            let name = url.lastPathComponent.lowercased()
            for (index, hint) in preferredNameHints.enumerated() where name.contains(hint) {
                return index
            }
            return preferredNameHints.count
        }
        let ordered = candidates.sorted { lhs, rhs in
            let pl = priority(lhs.url), pr = priority(rhs.url)
            if pl != pr { return pl < pr }
            if lhs.size != rhs.size { return lhs.size > rhs.size }
            return lhs.url.path < rhs.url.path
        }

        var pieces: [String] = []
        var total = 0
        for candidate in ordered {
            guard total < maxTotalBytes else { break }
            guard let text = try? String(contentsOf: candidate.url, encoding: .utf8) else { continue }
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else { continue }
            pieces.append(trimmed)
            total += trimmed.utf8.count
        }

        let joined = pieces.joined(separator: "\n\n")
        guard !joined.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return .failure("That .zip had no readable text export inside. Open the export's conversations.json or memory file instead.")
        }
        return .success(joined)
    }
}

// MARK: - Fact row

/// One vendor fact and its verdict. A cited verdict (confirmed/conflicting/stale WITH a match)
/// shows the Cortex memory it was checked against and a tap-through to its source. A "missing"
/// fact offers a single "Add to Cortex" action. An uncited confirmed/conflicting is NEVER shown
/// as agreeing or disagreeing — it drops to a plain "read from your export" line.
struct ImportDiffFactRow: View {
    let fact: ImportDiffFact
    let vendorLabel: String
    let alreadyAdded: Bool
    /// True once the user chose to keep Cortex's version of a conflicting/stale fact (U-DIFF2).
    var keptCortex: Bool = false
    let onAdd: () async -> Void
    /// U-DIFF2: dismiss the vendor's conflicting/stale claim, keeping what Cortex already holds.
    var onKeepCortex: () -> Void = {}
    /// U-DIFF2 / U-DIFF5: open the internal Cortex memory this fact was matched against.
    var onOpenMatch: () -> Void = {}

    @State private var adding = false

    private var status: ImportDiffStatus { fact.normalizedStatus }

    /// The honest verdict tone: only claim agree/disagree when there's a citation to back it.
    private var effectiveStatus: ImportDiffStatus {
        if (status == .confirmed || status == .conflicting || status == .stale) && !fact.isCited {
            return .missing // uncited → treated as "not grounded", shown plainly, no verdict claim
        }
        return status
    }

    var body: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.sm) {
            // The vendor's claim.
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: effectiveStatus.systemImage)
                    .font(.system(size: 12))
                    .foregroundColor(effectiveStatus.tone)
                    .padding(.top, 2)
                VStack(alignment: .leading, spacing: 3) {
                    Text(MemoryText.displayProse(fact.text, maxLength: 400))
                        .font(CortexDesign.Typography.prose(14))
                        .lineSpacing(3)
                        .foregroundColor(CortexDesign.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("From your \(vendorLabel) export")
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.6)
                        .foregroundColor(CortexDesign.inkFaint)
                }
                Spacer(minLength: 0)
            }

            // The grounding: only rendered when there's a real citation.
            if fact.isCited, let match = fact.match {
                matchBlock(match)
            }

            // Missing facts get one action: add the vendor's fact to Cortex.
            if effectiveStatus == .missing {
                addRow
            } else if effectiveStatus == .conflicting || effectiveStatus == .stale {
                // U-DIFF2: conflicting/stale facts are no longer a dead-end — resolve them in place.
                tensionActionRow
            }
        }
        .cortexCard(padding: CortexDesign.Space.md)
        .archiveSpine(effectiveStatus.tone)
    }

    /// U-DIFF2: the resolve row for a conflicting or stale fact. "Update Cortex with this" writes the
    /// vendor's version in; "Keep Cortex's" dismisses the vendor claim; when the match carries an id,
    /// "Open in Cortex" pivots to the memory it disagreed with so the user can inspect it.
    @ViewBuilder
    private var tensionActionRow: some View {
        HStack(spacing: CortexDesign.Space.sm) {
            if alreadyAdded {
                Label("Updated in \(DistributionMode.appDisplayName)", systemImage: "checkmark.seal.fill")
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.sealMoss)
            } else if keptCortex {
                Label("Kept \(DistributionMode.appDisplayName)'s version", systemImage: "checkmark.circle")
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
            } else {
                CortexButton(
                    title: adding ? "Updating…" : "Update \(DistributionMode.appDisplayName) with this",
                    systemImage: "arrow.triangle.2.circlepath",
                    role: .secondary,
                    size: .small
                ) {
                    guard !adding else { return }
                    adding = true
                    Task {
                        await onAdd()
                        adding = false
                    }
                }
                .disabled(adding)
                CortexButton(title: "Keep \(DistributionMode.appDisplayName)'s", role: .ghost, size: .small) {
                    onKeepCortex()
                }
                .disabled(adding)
            }
            if !(fact.match?.memory_id ?? "").isEmpty || !((fact.match?.content ?? "").isEmpty) {
                CortexButton(title: "Open in \(DistributionMode.appDisplayName)", systemImage: "arrow.up.right.square", role: .ghost, size: .small) {
                    onOpenMatch()
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.leading, 20)
    }

    @ViewBuilder
    private func matchBlock(_ match: ImportDiffMatch) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            if let note = fact.note?.trimmingCharacters(in: .whitespacesAndNewlines), !note.isEmpty {
                Text(note)
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(status == .confirmed ? CortexDesign.sealMoss : CortexDesign.gold)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let content = match.content?.trimmingCharacters(in: .whitespacesAndNewlines), !content.isEmpty {
                // U-DIFF5: when the matched memory is a real, openable Cortex memory, the "Cortex
                // remembers" line becomes a tap-through into it; otherwise it stays plain text so we
                // never offer a dead link.
                let canOpen = !(match.memory_id ?? "").isEmpty
                let remembers = Text("\(DistributionMode.appDisplayName) remembers: \(MemoryText.displayProse(content, maxLength: 320))")
                    .font(CortexDesign.Typography.prose(13))
                    .lineSpacing(3)
                    .foregroundColor(CortexDesign.inkSecondary)
                if canOpen {
                    Button {
                        onOpenMatch()
                    } label: {
                        remembers
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .help("Open this memory in \(DistributionMode.appDisplayName)")
                    .accessibilityAddTraits(.isButton)
                } else {
                    remembers
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            ImportDiffCitation(
                source: match.source,
                sourceURL: match.source_url,
                capturedAt: match.captured_at,
                layer: match.layer
            )
        }
        .padding(.leading, 20)
    }

    private var addRow: some View {
        HStack(spacing: CortexDesign.Space.sm) {
            if alreadyAdded {
                Label("Added to \(DistributionMode.appDisplayName)", systemImage: "checkmark.seal.fill")
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.sealMoss)
            } else {
                CortexButton(
                    title: adding ? "Adding…" : "Add to \(DistributionMode.appDisplayName)",
                    systemImage: "plus",
                    role: .secondary,
                    size: .small
                ) {
                    guard !adding else { return }
                    adding = true
                    Task {
                        await onAdd()
                        adding = false
                    }
                }
                .disabled(adding)
            }
            Spacer(minLength: 0)
        }
        .padding(.leading, 20)
    }
}

// MARK: - Cortex-only row ("what your export missed")

struct ImportDiffCortexOnlyRow: View {
    let item: ImportDiffCortexOnly

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            if let content = item.content?.trimmingCharacters(in: .whitespacesAndNewlines), !content.isEmpty {
                Text(MemoryText.displayProse(content, maxLength: 320))
                    .font(CortexDesign.Typography.prose(14))
                    .lineSpacing(3)
                    .foregroundColor(CortexDesign.ink)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ImportDiffCitation(
                source: item.source,
                sourceURL: item.source_url,
                capturedAt: item.captured_at,
                layer: item.layer
            )
        }
        .cortexCard(padding: CortexDesign.Space.md)
        .archiveSpine(CortexDesign.gold)
    }
}

// MARK: - Citation (reuses the app's clickable-source machinery)

/// A single clickable citation line, built on the SAME CitationDisplay helpers + NSWorkspace.open
/// affordance the Ask tab uses. Opens the source only when it is genuinely user-openable; internal
/// provenance stays plain text (never a dead link). Never fabricates a segment — an unknown source
/// simply renders nothing.
struct ImportDiffCitation: View {
    let source: String?
    let sourceURL: String?
    let capturedAt: String?
    let layer: String?

    @State private var hovering = false

    private var openableURL: URL? {
        CitationDisplay.openableURL(path: nil, sourceURL: sourceURL)
    }

    private var label: String? {
        CitationDisplay.label(sourceURL: sourceURL, fallback: source)
    }

    private var dateStamp: String? {
        guard let raw = capturedAt?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty else { return nil }
        return String(raw.prefix(10))
    }

    var body: some View {
        if let label {
            if let url = openableURL {
                Button {
                    NSWorkspace.shared.open(url)
                } label: {
                    content(openable: true)
                }
                .buttonStyle(.plain)
                .help("Open source: \(label)")
                .accessibilityAddTraits(.isLink)
                .onHover { hovering = $0 }
            } else {
                content(openable: false)
            }
        }
    }

    @ViewBuilder
    private func content(openable: Bool) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "doc.text")
                .font(.caption2)
                .foregroundColor(CortexDesign.accent)
            Text(label ?? "")
                .font(CortexDesign.Typography.stamp)
                .kerning(0.6)
                .foregroundColor(openable ? CortexDesign.accent : CortexDesign.inkFaint)
                .lineLimit(1)
                .truncationMode(.middle)
            if openable {
                Image(systemName: "arrow.up.right.square")
                    .font(.caption2)
                    .foregroundColor(CortexDesign.accent)
            }
            if let dateStamp {
                Text(dateStamp)
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.6)
                    .foregroundColor(CortexDesign.inkFaint)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 6)
        .padding(.vertical, 4)
        .background(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                .fill(hovering && openable ? CortexDesign.accentSoft : Color.clear)
        )
        .contentShape(Rectangle())
    }
}

// MARK: - Shareable verdict card (U-DIFF3)

/// Everything the `ImportDiffShareCard` draws, computed once from an `ImportDiffResult`. Plain data
/// so the card view is trivial and deterministic. Never invents a number: every count comes straight
/// off the summary the backend returned, and the "facts read" figure is capped at what it actually
/// parsed.
struct ImportDiffShareModel {
    let vendorLabel: String
    let factsRead: Int
    let confirms: Int
    let disputes: Int
    let missing: Int
    let remembers: Int
    /// "MEMORY DIFF · 12 JUL 2026" — the mono stamp line.
    let stampLine: String

    static func build(from result: ImportDiffResult) -> ImportDiffShareModel {
        let summary = result.summary
        let confirms = summary?.confirmed ?? 0
        let disputes = (summary?.conflicting ?? 0) + (summary?.stale ?? 0)
        let missing = summary?.missing ?? 0
        let remembers = summary?.cortex_only ?? 0

        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "d MMM yyyy"
        let stamp = "MEMORY DIFF · " + formatter.string(from: Date()).uppercased()

        return ImportDiffShareModel(
            vendorLabel: result.displayVendorLabel,
            factsRead: result.factsRead,
            confirms: confirms,
            disputes: disputes,
            missing: missing,
            remembers: remembers,
            stampLine: stamp
        )
    }

    /// The hero line on the card and in the copy-summary fallback.
    var headlineText: String {
        "What \(vendorLabel) thinks it knows about me, checked against my \(DistributionMode.appDisplayName)."
    }

    /// The honest plain-text fallback for "Copy summary".
    var clipboardSummary: String {
        var lines: [String] = []
        lines.append("What \(vendorLabel) thinks it knows about me, checked against my \(DistributionMode.appDisplayName):")
        lines.append("· \(factsRead) fact\(factsRead == 1 ? "" : "s") read from the export")
        lines.append("· \(confirms) confirmed")
        lines.append("· \(disputes) disputed")
        lines.append("· \(missing) new to \(DistributionMode.appDisplayName)")
        lines.append("· \(remembers) my \(DistributionMode.appDisplayName) already knew that the export forgot")
        lines.append("Measured by \(DistributionMode.appDisplayName).")
        return lines.joined(separator: "\n")
    }
}

/// The fixed-size (1200×630) shareable verdict card. Same night-sky Archive identity as
/// `MemoryWrappedCard` so the two share objects read as one family; rendered at 2× via ImageRenderer.
struct ImportDiffShareCard: View {
    static let size = CGSize(width: 1200, height: 630)

    let model: ImportDiffShareModel

    /// The Archive's dark palette, fixed by value (identical hex to `MemoryWrappedCard.Night`).
    private enum Night {
        static let paper = Color(red: 0.110, green: 0.102, blue: 0.090)
        static let panel = Color(red: 0.149, green: 0.137, blue: 0.125)
        static let ink = Color(red: 0.910, green: 0.890, blue: 0.851)
        static let inkSecondary = Color(red: 0.690, green: 0.663, blue: 0.616)
        static let inkFaint = Color(red: 0.549, green: 0.522, blue: 0.478)
        static let accent = Color(red: 0.788, green: 0.420, blue: 0.341)
        static let gold = Color(red: 0.827, green: 0.627, blue: 0.298)
        static let moss = Color(red: 0.494, green: 0.604, blue: 0.447)
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

    /// Faint FNV-seeded star-dust (no randomness) — the constellation contract, matched to
    /// `MemoryWrappedCard` so the two cards share a night sky.
    private var starDust: some View {
        Canvas { context, _ in
            for index in 0..<120 {
                var hash: UInt64 = 0xcbf29ce484222325
                for byte in "diff-dust-\(index)".utf8 {
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

    private var chrome: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(model.stampLine)
                .font(.system(size: 13, weight: .medium, design: .monospaced))
                .kerning(2.4)
                .foregroundColor(Night.inkFaint)

            Text("\(model.factsRead) FACT\(model.factsRead == 1 ? "" : "S") READ FROM \(model.vendorLabel.uppercased())")
                .font(.system(size: 12, weight: .medium, design: .monospaced))
                .kerning(1.8)
                .foregroundColor(Night.inkFaint.opacity(0.85))
                .padding(.top, 6)

            Text(model.headlineText)
                .font(.system(size: 42, weight: .semibold, design: .serif))
                .foregroundColor(Night.ink)
                .lineLimit(3)
                .minimumScaleFactor(0.7)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 24)

            Spacer(minLength: 0)

            statsFoot
        }
        .padding(.horizontal, 52)
        .padding(.top, 46)
        .padding(.bottom, 44)
    }

    private var statsFoot: some View {
        HStack(alignment: .lastTextBaseline, spacing: 40) {
            stat(model.confirms, "CONFIRMED", Night.moss)
            stat(model.disputes, "DISPUTED", Night.gold)
            stat(model.missing, "NEW TO \(DistributionMode.appDisplayName.uppercased())", Night.accent)
            stat(model.remembers, "\(DistributionMode.appDisplayName.uppercased()) KNEW", Night.ink)
            Spacer(minLength: 0)
            branding
        }
    }

    private func stat(_ count: Int, _ label: String, _ tone: Color) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("\(count)")
                .font(.system(size: 40, weight: .semibold, design: .serif))
                .monospacedDigit()
                .foregroundColor(tone)
            Text(label)
                .font(.system(size: 12, weight: .medium, design: .monospaced))
                .kerning(1.4)
                .foregroundColor(Night.inkFaint)
                .lineLimit(1)
        }
        .frame(maxWidth: 200, alignment: .leading)
    }

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

// MARK: - Verdict share sheet (built only when opened)

/// A weak handle to the AppKit view planted under the Share button so the sharing picker's popover
/// can anchor to it. (Named to avoid colliding with the other share anchors.)
private final class ImportDiffShareAnchor {
    weak var view: NSView?
}

private struct ImportDiffShareAnchorView: NSViewRepresentable {
    let anchor: ImportDiffShareAnchor

    func makeNSView(context: Context) -> NSView {
        let view = NSView(frame: .zero)
        anchor.view = view
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        anchor.view = nsView
    }
}

/// The preview + share sheet behind "Share result…". Renders the verdict card once (2× via
/// ImageRenderer), shows exactly the pixels that would leave the machine, and offers Share, Copy,
/// and Save PNG. Mirrors `MemoryWrappedShareSheet` so the two share flows behave identically.
struct ImportDiffShareSheet: View {
    let model: ImportDiffShareModel

    @Environment(\.dismiss) private var dismiss

    @State private var cardImage: NSImage?
    @State private var cardPNG: Data?
    @State private var copied = false
    @State private var renderFailed = false
    @State private var activePicker: NSSharingServicePicker?
    @State private var shareAnchor = ImportDiffShareAnchor()

    var body: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
            HStack(alignment: .firstTextBaseline, spacing: CortexDesign.Space.sm) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Share what the AI thinks of you")
                        .font(CortexDesign.Typography.title)
                        .foregroundColor(CortexDesign.ink)
                    Text("Your verdict card, ready to share. Real counts only, straight from your compare.")
                        .font(CortexDesign.Typography.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                }
                Spacer(minLength: 0)
                CortexIconButton(systemImage: "xmark", role: .ghost, size: .small, help: "Close") {
                    dismiss()
                }
                .accessibilityLabel("Close share preview")
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
                .background(ImportDiffShareAnchorView(anchor: shareAnchor))
            }
        }
        .padding(CortexDesign.Space.lg)
        .frame(minWidth: 684, minHeight: 520)
        .background(CortexDesign.appBackground)
        .task { renderCard() }
    }

    @ViewBuilder
    private var preview: some View {
        ConstellationTrophy(
            image: cardImage,
            failed: renderFailed,
            stampText: "MEMORY DIFF",
            sealText: DistributionMode.appDisplayName,
            onRetry: { Task { @MainActor in renderCard() } }
        )
    }

    @MainActor
    private func renderCard() {
        guard cardImage == nil else { return }
        renderFailed = false
        let renderer = ImageRenderer(content: ImportDiffShareCard(model: model))
        renderer.scale = 2
        guard let cgImage = renderer.cgImage else {
            renderFailed = true
            return
        }
        let rep = NSBitmapImageRep(cgImage: cgImage)
        rep.size = NSSize(width: ImportDiffShareCard.size.width, height: ImportDiffShareCard.size.height)
        cardPNG = rep.representation(using: .png, properties: [:])
        let image = NSImage(size: rep.size)
        image.addRepresentation(rep)
        cardImage = image
    }

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
        panel.nameFieldStringValue = "what-the-ai-thinks-of-me.png"
        panel.canCreateDirectories = true
        panel.isExtensionHidden = false
        if panel.runModal() == .OK, let url = panel.url {
            try? data.write(to: url)
        }
    }

    private func presentSharePicker() {
        guard let image = cardImage, let anchorView = shareAnchor.view else { return }
        let picker = NSSharingServicePicker(items: [image])
        activePicker = picker
        picker.show(relativeTo: anchorView.bounds, of: anchorView, preferredEdge: .minY)
    }
}
