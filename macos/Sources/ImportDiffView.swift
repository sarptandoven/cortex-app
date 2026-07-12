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
        case .missing: return "Cortex doesn't know this"
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
        case .confirmed: return "Your export and Cortex agree."
        case .conflicting: return "Your export says something Cortex remembers differently."
        case .stale: return "Cortex has a newer version of this."
        case .missing: return "This isn't in your Cortex yet."
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
                Text("Paste a ChatGPT, Claude, or Gemini memory export and see it checked, with citations, against your Cortex.")
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
        CortexEmptyState(
            systemImage: "sparkle.magnifyingglass",
            title: "See what an AI remembers about you",
            message: "Export your memory from ChatGPT, Claude, or Gemini, paste it above, and Cortex will tell you what it can confirm, what conflicts, and what it already knew that the export missed."
        )
        .frame(maxWidth: .infinity)
        .padding(.top, CortexDesign.Space.md)
    }

    private var comparingState: some View {
        HStack(spacing: CortexDesign.Space.sm) {
            ProgressView().scaleEffect(0.8)
            Text("Reading your export and checking it against your Cortex…")
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
                Text("Cortex couldn't read any facts from that export. Try pasting the raw export text or JSON.")
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
                        (missing, "new to Cortex", ImportDiffStatus.missing.tone),
                    ],
                    ground: CortexDesign.quietBackground
                )
                Rectangle()
                    .fill(CortexDesign.hairline)
                    .frame(width: 1)
                verdictLedger(
                    title: "Your Cortex",
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
            }
            ForEach(facts) { fact in
                ImportDiffFactRow(
                    fact: fact,
                    vendorLabel: result.displayVendorLabel,
                    alreadyAdded: addedFactIDs.contains(fact.id),
                    onAdd: { await addToCortex(fact) }
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
                Text("Cortex already remembers these; your \(result.displayVendorLabel) export doesn't.")
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
            } else {
                errorText = "Couldn't compare that export right now. Check that Cortex is running and try again."
            }
        }
    }

    private func addToCortex(_ fact: ImportDiffFact) async {
        let text = fact.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        let added = await state.addImportDiffFact(text: text, vendorLabel: fact.vendor_label ?? result?.displayVendorLabel ?? "")
        if added {
            addedFactIDs.insert(fact.id)
        }
    }

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
    let onAdd: () async -> Void

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
            }
        }
        .cortexCard(padding: CortexDesign.Space.md)
        .archiveSpine(effectiveStatus.tone)
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
                Text("Cortex remembers: \(MemoryText.displayProse(content, maxLength: 320))")
                    .font(CortexDesign.Typography.prose(13))
                    .lineSpacing(3)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
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
                Label("Added to Cortex", systemImage: "checkmark.seal.fill")
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.sealMoss)
            } else {
                CortexButton(
                    title: adding ? "Adding…" : "Add to Cortex",
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
