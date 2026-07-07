import Foundation

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
            return "Your note in Cortex"
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
    /// Strip a leading `file '…'` wrapper and surrounding quotes to get the inner path/string.
    static func unwrap(_ raw: String) -> String {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
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
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if isPathLike(trimmed), let name = filename(trimmed) {
            return (name, middleTruncated(unwrap(trimmed)))
        }
        return (trimmed, nil)
    }

    /// A normalized key for collapsing near-identical previews.
    static func dedupeKey(_ raw: String) -> String {
        unwrap(raw).lowercased()
    }
}
