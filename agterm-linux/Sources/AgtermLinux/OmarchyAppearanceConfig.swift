import Foundation
import agtermCore

struct OmarchyAppearanceConfig: Equatable {
    private enum Key: String {
        case fontFamily = "font-family"
        case fontSize = "font-size"
        case paddingX = "window-padding-x"
        case paddingY = "window-padding-y"
        case cursorBlink = "cursor-style-blink"
    }

    var families: [String]?
    var size: Double?
    var paddingX: String?
    var paddingY: String?
    var cursorBlink: Bool?

    static func parse(_ contents: String) -> Self? {
        var config = Self()
        for raw in contents.split(whereSeparator: { $0 == "\n" || $0 == "\r\n" }) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard let equals = line.firstIndex(of: "=") else {
                if let key = line.split(whereSeparator: \.isWhitespace).first, Key(rawValue: String(key)) != nil { return nil }
                continue
            }
            guard let key = Key(rawValue: line[..<equals].trimmingCharacters(in: .whitespaces)) else { continue }
            let rawValue = line[line.index(after: equals)...].trimmingCharacters(in: .whitespaces)
            let value = GhosttyConfigText.unquoted(rawValue)
            var invalidCharacters = CharacterSet.controlCharacters.union(.newlines)
            if key == .paddingX || key == .paddingY { invalidCharacters.remove(charactersIn: "\t") }
            guard !value.contains("\""),
                  value.rangeOfCharacter(from: invalidCharacters) == nil else { return nil }
            switch key {
            case .fontFamily:
                if value.isEmpty {
                    config.families = []
                } else {
                    if config.families == nil { config.families = [] }
                    config.families?.append(value)
                }
            case .fontSize:
                // Bound the input before Ghostty scales it for the display and allocates glyphs.
                let size = Double(value).flatMap { (1...128).contains($0) ? $0 : nil }
                guard value.isEmpty || size != nil else { return nil }
                config.size = size
            case .paddingX, .paddingY:
                let padding = parsePadding(value)
                guard value.isEmpty || padding != nil else { return nil }
                if key == .paddingX { config.paddingX = padding } else { config.paddingY = padding }
            case .cursorBlink:
                let blink = Bool(value)
                guard value.isEmpty || blink != nil else { return nil }
                config.cursorBlink = blink
            }
        }
        return config
    }

    private static func parsePadding(_ value: String) -> String? {
        // Ghostty accepts one point value or a pair. Bound each edge on the automatic path.
        let edges = value.split(separator: ",", omittingEmptySubsequences: false)
        guard (1...2).contains(edges.count) else { return nil }
        let points = edges.compactMap { UInt8($0.trimmingCharacters(in: .whitespaces)) }
        guard points.count == edges.count, points.allSatisfy({ $0 <= 128 }) else { return nil }
        return points.map(String.init).joined(separator: ",")
    }

    func lines(overriddenBy keys: Set<String> = []) -> [String] {
        var lines: [String] = []
        if let families, !keys.contains("font-family") {
            lines += families.isEmpty ? ["font-family ="] : families.map { "font-family = \"\($0)\"" }
        }
        if let size, !keys.contains("font-size") { lines.append("font-size = \(size)") }
        if let paddingX, !keys.contains("window-padding-x") { lines.append("window-padding-x = \(paddingX)") }
        if let paddingY, !keys.contains("window-padding-y") { lines.append("window-padding-y = \(paddingY)") }
        if let cursorBlink, !keys.contains("cursor-style-blink") { lines.append("cursor-style-blink = \(cursorBlink)") }
        return lines
    }

    func fallbackLines(overriddenBy lines: [String], scopedPath: String?, home: String) -> [String] {
        guard self != Self() else { return [] }
        var keys = Set(GhosttyConfigText.keyValues(in: lines.joined(separator: "\n")).map(\.key))
        // Scalars already follow Ghostty's layer order. Repeatable families would append behind
        // the Omarchy primary, so suppress our whole list when the scoped config owns that key.
        if families?.isEmpty == false, !keys.contains("font-family"), let scopedPath {
            var remainingReads = 64
            var unreadableFile = false
            let scoped = GhosttyConfigText.keyValues(loading: [scopedPath], home: home) { path in
                guard remainingReads > 0 else { unreadableFile = true; return nil }
                remainingReads -= 1
                let text = OmarchyTheme.readConfig(at: URL(fileURLWithPath: path))
                if text == nil, FileManager.default.fileExists(atPath: path) { unreadableFile = true }
                return text
            }
            if unreadableFile || scoped.contains(where: { $0.key == "font-family" }) {
                keys.insert("font-family")
            }
        }
        return self.lines(overriddenBy: keys)
    }
}

struct OmarchyAppearanceSource {
    private(set) var config = OmarchyAppearanceConfig()

    mutating func refresh(from url: URL) -> Bool {
        guard let contents = OmarchyTheme.readConfig(at: url, missingContents: ""),
              let next = OmarchyAppearanceConfig.parse(contents), next != config else { return false }
        config = next
        return true
    }
}
