import Foundation
import NaturalLanguage

/// The single source of truth for turning an internal source id (e.g. "obsidian", "chatgpt") into a
/// user-facing name. Product rule: a raw connector id must NEVER be shown to a user. Two forms exist
/// because grammar differs — `bareNoun` reads correctly right after "From your …" (a lowercase noun,
/// or a proper noun where appropriate), while `label` is the standalone, capitalized chip form.
/// Unknown ids are titleized (never returned as a raw lowercase id) so a new connector can't leak.
enum SourceDisplayName {
    static func bareNoun(_ id: String) -> String {
        switch canonical(id) {
        case "obsidian", "local", "file", "files", "notes", "vault", "markdown": return "notes"
        case "calendar", "gcal", "google-calendar", "google_calendar": return "calendar"
        case "gmail", "email", "mail", "outlook": return "email"
        case "imessage", "messages", "sms": return "messages"
        case "slack": return "Slack"
        case "github": return "GitHub"
        case "notion": return "Notion"
        case "linear": return "Linear"
        case "jira": return "Jira"
        case "apple-notes", "apple_notes": return "Apple Notes"
        case "chatgpt", "openai": return "ChatGPT"
        case "claude", "anthropic": return "Claude"
        default: return titleized(id)
        }
    }

    static func label(_ id: String) -> String {
        switch canonical(id) {
        case "obsidian", "local", "file", "files", "notes", "vault", "markdown": return "Notes"
        case "calendar", "gcal", "google-calendar", "google_calendar": return "Calendar"
        case "gmail", "email", "mail", "outlook": return "Email"
        case "imessage", "messages", "sms": return "Messages"
        case "slack": return "Slack"
        case "github": return "GitHub"
        case "notion": return "Notion"
        case "linear": return "Linear"
        case "jira": return "Jira"
        case "apple-notes", "apple_notes": return "Apple Notes"
        case "chatgpt", "openai": return "ChatGPT"
        case "claude", "anthropic": return "Claude"
        default: return titleized(id)
        }
    }

    private static func canonical(_ id: String) -> String {
        id.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    private static func titleized(_ id: String) -> String {
        let cleaned = id
            .replacingOccurrences(of: "_", with: " ")
            .replacingOccurrences(of: "-", with: " ")
            .trimmingCharacters(in: .whitespaces)
        guard !cleaned.isEmpty else { return "notes" }
        return cleaned
            .split(separator: " ")
            .map { $0.prefix(1).uppercased() + $0.dropFirst() }
            .joined(separator: " ")
    }
}

enum CitationDisplay {
    static func label(
        path: String? = nil,
        sourceURL: String? = nil,
        fallback: String? = nil,
        lineStart: Int? = nil,
        lineEnd: Int? = nil
    ) -> String? {
        guard let base = cleanPath(path) ?? cleanSourceURL(sourceURL) ?? cleanPlain(fallback) else {
            return nil
        }
        guard let line = lineLabel(start: lineStart, end: lineEnd) else {
            return base
        }
        return "\(base) - \(line)"
    }

    /// The user-openable URL for a citation, or nil when there is nothing to open
    /// (internal-only provenance such as `cortex-capture://…`). Prefers an explicit
    /// source URL (http/https/file/local-file), then an absolute on-disk path.
    static func openableURL(path: String? = nil, sourceURL: String? = nil) -> URL? {
        if let raw = sourceURL?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty {
            let lower = raw.lowercased()
            if lower.hasPrefix("http://") || lower.hasPrefix("https://") || lower.hasPrefix("file://") {
                return URL(string: raw)
            }
            // Internal/sanitized schemes are NOT user-openable, so the row stays plain
            // text: `cortex-capture://` is internal provenance, and `local-file://` is
            // reduced by the backend to a privacy-safe basename (real path stripped), so
            // building a file URL from it would only produce a dead link. Fall through.
        }
        if let filePath = path?.trimmingCharacters(in: .whitespacesAndNewlines), filePath.hasPrefix("/") {
            return URL(fileURLWithPath: filePath)
        }
        return nil
    }

    static func cleanSourceURL(_ value: String?) -> String? {
        guard let value = cleanPlain(value) else { return nil }
        // A memory the user captured directly in Cortex; its provenance is the capture itself.
        if value.hasPrefix("cortex-capture://") {
            return "Your note in \(DistributionMode.appDisplayName)"
        }
        if value.hasPrefix("local-file://") {
            return cleanLocalFile(value)
        }
        if value.hasPrefix("file://"), let url = URL(string: value) {
            let last = url.lastPathComponent.trimmingCharacters(in: .whitespacesAndNewlines)
            return last.isEmpty ? nil : last
        }
        if let url = URL(string: value), let host = url.host, !host.isEmpty {
            let path = url.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            let display = path.isEmpty ? host : "\(host)/\(path)"
            return display.removingPercentEncoding ?? display
        }
        return value.removingPercentEncoding ?? value
    }

    private static func cleanPath(_ value: String?) -> String? {
        guard let value = cleanPlain(value) else { return nil }
        let stripped = stripLocatorSuffix(value)
        return stripped.removingPercentEncoding ?? stripped
    }

    private static func cleanPlain(_ value: String?) -> String? {
        guard let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines), !trimmed.isEmpty else {
            return nil
        }
        return trimmed
    }

    private static func cleanLocalFile(_ value: String) -> String? {
        let prefix = "local-file://"
        guard value.hasPrefix(prefix) else { return nil }
        let stripped = String(value.dropFirst(prefix.count))
        let cleaned = stripLocatorSuffix(stripped)
        return cleaned.isEmpty ? nil : (cleaned.removingPercentEncoding ?? cleaned)
    }

    private static func stripLocatorSuffix(_ value: String) -> String {
        let delimiters = ["?", "#"]
        var result = value
        for delimiter in delimiters {
            if let index = result.firstIndex(of: Character(delimiter)) {
                result = String(result[..<index])
            }
        }
        return result.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }

    private static func lineLabel(start: Int?, end: Int?) -> String? {
        guard let start else { return nil }
        if let end, end > start {
            return "lines \(start)-\(end)"
        }
        return "line \(start)"
    }
}

/// Makes raw memory content read cleanly for a normal user. Synced content is often a bare file
/// path (e.g. `file '/Users/.../scene_2.mp4'`); showing the filename as the headline and the path as
/// a quiet secondary line is far more legible than dumping the whole string.
enum MemoryText {
    private static let uuidPattern = #"(?i)\b[0-9a-f]{8}(?:[-\s]+[0-9a-f]{4}){3}[-\s]+[0-9a-f]{12}\b"#

    /// Normalize backend/import text before it reaches a user-facing view. Some source exports
    /// contain escaped control sequences (the two visible characters `\n`) instead of actual
    /// whitespace. Rendering those verbatim made otherwise polished cards look corrupted.
    static func normalizedProse(_ raw: String) -> String {
        raw
            .replacingOccurrences(of: "\\r\\n", with: " ")
            .replacingOccurrences(of: "\\n", with: " ")
            .replacingOccurrences(of: "\\r", with: " ")
            .replacingOccurrences(of: "\\t", with: " ")
            .components(separatedBy: .whitespacesAndNewlines)
            .filter { !$0.isEmpty }
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// A compact preview for imported content. Repeated quoted file paths are summarized by
    /// filename so Review never turns into a wall of private absolute paths. Ordinary prose is
    /// whitespace-normalized and capped for preview surfaces; the original remains available to
    /// detailed/citation views and accessibility help text.
    static func displayProse(_ raw: String, maxLength: Int = 320) -> String {
        let normalized = normalizedProse(raw)
        let names = quotedFileNames(in: normalized)
        if !names.isEmpty {
            let visible = names.prefix(3).joined(separator: ", ")
            let remainder = names.count > 3 ? " +\(names.count - 3) more" : ""
            return names.count == 1 ? "File: \(visible)" : "Files: \(visible)\(remainder)"
        }
        guard normalized.count > maxLength else { return normalized }
        return String(normalized.prefix(max(1, maxLength - 1))).trimmingCharacters(in: .whitespaces) + "…"
    }

    /// Build a useful Ask subject without leaking UUIDs, absolute paths, or import field names.
    /// Returning nil lets the caller fall back to a clean source-level suggestion.
    ///
    /// The subject is the first CLAUSE of the first sentence, kept intact — never a bag of
    /// stripped words. The old word-mash produced suggestions like "What should I remember
    /// about Cost analyzing 100k lines code with Sonnet Overview The?", which read as broken.
    static func suggestionSubject(_ raw: String) -> String? {
        let normalized = normalizedProse(raw)
        guard !normalized.isEmpty,
              !isPathLike(normalized),
              !normalized.contains("/Users/"),
              !normalized.contains("\\Users\\"),
              !normalized.contains("{"),
              normalized.range(of: uuidPattern, options: .regularExpression) == nil else {
            return nil
        }

        // First sentence, then first clause of it (commas/semicolons/dashes end a clause).
        var subject = normalized
        if let sentenceEnd = subject.rangeOfCharacter(from: CharacterSet(charactersIn: ".!?\n")) {
            subject = String(subject[..<sentenceEnd.lowerBound])
        }
        if let clauseEnd = subject.rangeOfCharacter(from: CharacterSet(charactersIn: ",;:—(")) {
            subject = String(subject[..<clauseEnd.lowerBound])
        }
        subject = subject.trimmingCharacters(in: .whitespacesAndNewlines)

        // Cut long clauses at a word boundary, never mid-word.
        if subject.count > 64 {
            let head = String(subject.prefix(64))
            subject = head.contains(" ") ? String(head[..<head.range(of: " ", options: .backwards)!.lowerBound]) : head
        }
        // A clause that ENDS in a dangling function word ("...with", "...the") reads broken.
        let dangling = Set(["the", "a", "an", "and", "or", "but", "with", "for", "of", "to", "in", "on", "at", "by", "is", "are", "was"])
        var words = subject.split(separator: " ").map(String.init)
        while let last = words.last, dangling.contains(last.lowercased()) {
            words.removeLast()
        }
        let clause = words.joined(separator: " ")

        // The clause is often a full statement ("Claude walked through token estimation"); jammed
        // into an "about X?" template that reads as broken grammar. Keep only the leading noun
        // phrase by truncating before the first verb, then re-strip any newly dangling tail.
        var truncated = false
        if !clause.isEmpty {
            let tagger = NLTagger(tagSchemes: [.lexicalClass])
            tagger.string = clause
            var verbStart: String.Index? = nil
            tagger.enumerateTags(in: clause.startIndex..<clause.endIndex, unit: .word, scheme: .lexicalClass) { tag, range in
                if tag == .verb {
                    verbStart = range.lowerBound
                    return false
                }
                return true
            }
            if let cut = verbStart, cut > clause.startIndex {
                let head = String(clause[..<cut]).trimmingCharacters(in: .whitespacesAndNewlines)
                words = head.split(separator: " ").map(String.init)
                while let last = words.last, dangling.contains(last.lowercased()) {
                    words.removeLast()
                }
                truncated = true
            }
        }

        if truncated {
            // A short noun phrase ("Claude", "Costs for Sonnet 5") is a fine topic on its own.
            guard words.contains(where: { $0.count > 2 }) else {
                return clause.isEmpty ? nil : "\u{201C}\(clause)\u{201D}"
            }
            return words.joined(separator: " ")
        }
        // Meaningful subjects have a few real words; metadata fragments do not.
        let meaningful = words.filter { $0.count > 2 }
        guard words.count >= 3, meaningful.count >= 3 else { return nil }
        return words.joined(separator: " ")
    }

    /// Strip a leading `file '…'` wrapper and surrounding quotes to get the inner path/string.
    static func unwrap(_ raw: String) -> String {
        var s = normalizedProse(raw)
        if s.lowercased().hasPrefix("file ") {
            s = String(s.dropFirst(5)).trimmingCharacters(in: .whitespaces)
        }
        if s.count >= 2,
           (s.hasPrefix("'") && s.hasSuffix("'")) || (s.hasPrefix("\"") && s.hasSuffix("\"")) {
            s = String(s.dropFirst().dropLast())
        }
        return s
    }

    /// True when the content is essentially a single filesystem path (optionally `file '…'`-wrapped).
    static func isPathLike(_ raw: String) -> Bool {
        let s = unwrap(raw)
        guard !s.isEmpty, !s.contains("\n") else { return false }
        if s.hasPrefix("/") || s.hasPrefix("~/") || s.hasPrefix("file://") { return true }
        return s.range(of: "/[^/ ]+\\.[A-Za-z0-9]{1,8}$", options: .regularExpression) != nil
    }

    /// The trailing filename of a path-like string, else nil.
    static func filename(_ raw: String) -> String? {
        let s = unwrap(raw).replacingOccurrences(of: "file://", with: "")
        guard let last = s.split(separator: "/").last, !last.isEmpty else { return nil }
        return String(last)
    }

    /// Middle-truncate so both the leading context and the trailing filename survive.
    static func middleTruncated(_ s: String, max: Int = 72) -> String {
        guard s.count > max else { return s }
        let keep = max - 1
        let head = keep / 2
        let tail = keep - head
        return String(s.prefix(head)) + "…" + String(s.suffix(tail))
    }

    /// A clean (headline, secondaryPath?) for display: for a path-like value the filename is the
    /// headline and the middle-truncated path is secondary; otherwise the raw text is the headline.
    static func displayContent(_ raw: String) -> (headline: String, path: String?) {
        let trimmed = normalizedProse(raw)
        if isPathLike(trimmed), let name = filename(trimmed) {
            return (name, middleTruncated(unwrap(trimmed)))
        }
        return (displayProse(trimmed), nil)
    }

    /// A normalized key for collapsing near-identical previews.
    static func dedupeKey(_ raw: String) -> String {
        unwrap(raw).lowercased()
    }

    private static func quotedFileNames(in raw: String) -> [String] {
        guard raw.contains("/") || raw.contains("\\") else { return [] }
        var names: [String] = []
        let pattern = #"['\"]([^'\"]*(?:/|\\)[^'\"]+)['\"]"#
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
        let range = NSRange(raw.startIndex..<raw.endIndex, in: raw)
        for match in regex.matches(in: raw, range: range) {
            guard match.numberOfRanges > 1,
                  let captureRange = Range(match.range(at: 1), in: raw) else { continue }
            let candidate = String(raw[captureRange])
            guard let name = filename(candidate), name.contains(".") else { continue }
            if !names.contains(name) { names.append(name) }
        }
        return names
    }
}
