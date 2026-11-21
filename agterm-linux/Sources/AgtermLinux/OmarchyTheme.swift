import Foundation

/// Omarchy publishes here even when XDG_STATE_HOME is set.
struct OmarchyTheme {
    let current: URL
    var directory: URL { current.appendingPathComponent("theme", isDirectory: true) }
    var config: URL { directory.appendingPathComponent("ghostty.conf") }

    init(home: URL = Self.homeDirectory()) {
        current = home.appendingPathComponent(".local/state/omarchy/current", isDirectory: true)
    }

    private static func homeDirectory() -> URL {
        if let home = ProcessInfo.processInfo.environment["HOME"], home.hasPrefix("/") {
            return URL(fileURLWithPath: home, isDirectory: true)
        }
        return FileManager.default.homeDirectoryForCurrentUser
    }

    var isInstalled: Bool {
        var isDirectory: ObjCBool = false
        return FileManager.default.fileExists(atPath: directory.path, isDirectory: &isDirectory) && isDirectory.boolValue
    }

    func colorLines() -> [String] {
        let config = config.resolvingSymlinksInPath()
        guard (try? config.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true,
              let contents = try? String(contentsOf: config, encoding: .utf8) else { return [] }
        return Self.colorLines(in: contents)
    }

    private static let colorKeys: Set<String> = [
        "background", "foreground", "cursor-color", "cursor-text",
        "selection-background", "selection-foreground", "palette",
    ]

    static func isColorLine(_ line: String) -> Bool {
        assignment(line).map { colorKeys.contains($0.key) } ?? false
    }

    /// Reconstruct only the color arguments written by Omarchy. Never pass its file to Ghostty's loader.
    static func colorLines(in contents: String) -> [String] {
        contents.split(whereSeparator: \.isNewline).compactMap { line in
            guard let (key, value) = assignment(String(line)), colorKeys.contains(key) else { return nil }
            if key == "palette" {
                guard let (index, color) = assignment(value),
                      index.utf8.allSatisfy({ (48...57).contains($0) }),
                      let index = UInt8(index), isHexColor(color) else { return nil }
                return "palette = \(index)=\(color)"
            }
            let usesCellColor = key != "background" && key != "foreground"
            guard isHexColor(value) || (usesCellColor && ["cell-foreground", "cell-background"].contains(value))
            else { return nil }
            return "\(key) = \(value)"
        }
    }

    private static func assignment(_ line: String) -> (key: String, value: String)? {
        guard let equals = line.firstIndex(of: "=") else { return nil }
        return (line[..<equals].trimmingCharacters(in: .whitespaces),
                line[line.index(after: equals)...].trimmingCharacters(in: .whitespaces))
    }

    private static func isHexColor(_ value: String) -> Bool {
        let hex = value.hasPrefix("#") ? value.utf8.dropFirst() : value.utf8[...]
        return hex.count == 6 && hex.allSatisfy {
            (48...57).contains($0) || (65...70).contains($0) || (97...102).contains($0)
        }
    }
}

/// An explicit preference lives with Linux app state. Older profiles derive the default from Omarchy.
struct OmarchyThemePreference {
    let file: URL

    init(stateDirectory: URL = linuxStateDirectory()) {
        file = stateDirectory.appendingPathComponent("omarchy-theme.json")
    }

    func load() -> Bool? {
        guard let data = try? Data(contentsOf: file) else { return nil }
        return try? JSONDecoder().decode(Bool.self, from: data)
    }

    func follows(_ theme: OmarchyTheme) -> Bool { load() ?? theme.isInstalled }

    func save(_ follows: Bool) throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(follows).write(to: file, options: .atomic)
    }
}
