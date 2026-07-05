import Foundation

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
