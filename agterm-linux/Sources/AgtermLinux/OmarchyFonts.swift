import Foundation
import agtermCore

struct OmarchyFonts: Equatable {
    var families: [String]?
    var size: Double?

    static func parse(_ contents: String) -> Self? {
        var fonts = Self()
        for raw in contents.split(whereSeparator: { $0 == "\n" || $0 == "\r\n" }) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard let equals = line.firstIndex(of: "=") else {
                let key = line.split(whereSeparator: \.isWhitespace).first
                if key == "font-family" || key == "font-size" { return nil }
                continue
            }
            let key = line[..<equals].trimmingCharacters(in: .whitespaces)
            guard key == "font-family" || key == "font-size" else { continue }
            let rawValue = line[line.index(after: equals)...].trimmingCharacters(in: .whitespaces)
            let value = GhosttyConfigText.unquoted(rawValue)
            guard !value.contains("\""),
                  value.rangeOfCharacter(from: .controlCharacters.union(.newlines)) == nil else { return nil }
            switch key {
            case "font-family":
                if value.isEmpty {
                    fonts.families = []
                } else {
                    if fonts.families == nil { fonts.families = [] }
                    fonts.families?.append(value)
                }
            default:
                if value.isEmpty {
                    fonts.size = nil
                } else {
                    // Accept fractional points and Omarchy's small sizes. Bound the input before
                    // Ghostty converts it to Float, scales it for the display and allocates glyphs.
                    guard let size = Double(value), (1...128).contains(size) else { return nil }
                    fonts.size = size
                }
            }
        }
        return fonts
    }

    func lines(overriddenBy keys: Set<String> = []) -> [String] {
        var lines: [String] = []
        if let families, !keys.contains("font-family") {
            lines += families.isEmpty ? ["font-family ="] : families.map { "font-family = \"\($0)\"" }
        }
        if let size, !keys.contains("font-size") { lines.append("font-size = \(size)") }
        return lines
    }

    func fallbackLines(overriddenBy lines: [String], scopedPath: String?, home: String) -> [String] {
        guard families != nil || size != nil else { return [] }
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

struct OmarchyFontSource {
    private(set) var fonts = OmarchyFonts()

    mutating func refresh(from url: URL) -> Bool {
        guard let contents = OmarchyTheme.readConfig(at: url),
              let next = OmarchyFonts.parse(contents), next != fonts else { return false }
        fonts = next
        return true
    }
}
