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

    static func cleanSourceURL(_ value: String?) -> String? {
        guard let value = cleanPlain(value) else { return nil }
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
