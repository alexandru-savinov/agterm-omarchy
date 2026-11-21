import Foundation
import agtermCore
#if canImport(Glibc)
import Glibc
#else
import Darwin
#endif

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

    static let configReadLimit = 64 * 1024

    func colorLines() -> [String] {
        // Inspect and read the same open file. Nonblocking open lets fstat reject a FIFO even
        // when no writer exists. Reject a full buffer because the final assignment may be cut off.
        let fd = open(config.path, O_RDONLY | O_CLOEXEC | O_NONBLOCK | O_NOCTTY)
        guard fd >= 0 else { return [] }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        defer { try? handle.close() }
        var entry = stat()
        guard fstat(fd, &entry) == 0,
              entry.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG),
              entry.st_size < Self.configReadLimit,
              let bytes = try? handle.read(upToCount: Self.configReadLimit),
              bytes.count < Self.configReadLimit,
              let contents = String(data: bytes, encoding: .utf8) else { return [] }
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

/// The opt-in and any explicit selection of a default theme live with Linux app state.
struct OmarchyThemePreference {
    private struct Choices: Codable {
        var follow: Bool?
        var explicitAgtermTheme: Bool?
    }

    let file: URL

    init(stateDirectory: URL = linuxStateDirectory()) {
        file = stateDirectory.appendingPathComponent("omarchy-theme.json")
    }

    func load() -> Bool? { loadChoices().follow }

    func follows(_ theme: OmarchyTheme) -> Bool { load() ?? theme.isInstalled }

    func usesAgtermTheme(_ settings: AppSettings, previewing: Bool = false) -> Bool {
        if previewing || settings.followSystemAppearance == true { return true }
        // Named themes in existing settings are already explicit. The seeded name and Ghostty's
        // built-in default need a marker to distinguish a deliberate choice from an old profile.
        if let name = settings.theme ?? settings.darkTheme, !name.isEmpty, name != AppSettings.defaultTheme {
            return true
        }
        return loadChoices().explicitAgtermTheme == true
    }

    func save(_ follows: Bool) throws {
        var choices = loadChoices()
        choices.follow = follows
        try saveChoices(choices)
    }

    func saveAgtermThemeChoice(explicit: Bool) throws {
        var choices = loadChoices()
        guard (choices.explicitAgtermTheme ?? false) != explicit else { return }
        choices.explicitAgtermTheme = explicit ? true : nil
        try saveChoices(choices)
    }

    private func loadChoices() -> Choices {
        guard let data = try? Data(contentsOf: file) else { return Choices() }
        let decoder = JSONDecoder()
        return (try? decoder.decode(Choices.self, from: data))
            ?? Choices(follow: try? decoder.decode(Bool.self, from: data))
    }

    private func saveChoices(_ choices: Choices) throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(choices).write(to: file, options: .atomic)
    }
}
