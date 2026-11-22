import Foundation

/// Static freedesktop launcher actions declared by the Linux `.desktop` entry.
///
/// Desktop Entry Actions cannot carry dynamic session identities, so the recent and attention entries open
/// their live GTK palettes after the invocation reaches the primary `GApplication` instance.
enum LinuxDesktopAction: String, CaseIterable, Equatable, Sendable {
    case newSession = "new-session"
    case newWindow = "new-window"
    case quickTerminal = "quick-terminal"
    case dashboard
    case recentSessions = "recent-sessions"
    case attention

    /// Whether this action can run against a window in its current modal state. New Window is app-scoped;
    /// Dashboard is the one window action whose open grid doubles as its own escape hatch.
    func isEnabled(in context: LinuxDesktopActionContext) -> Bool {
        switch self {
        case .newWindow:
            return true
        case .dashboard:
            return !context.terminalZoomActive && !context.pickerActive
        default:
            return !context.modalActive
        }
    }
}

/// The three window-local covers desktop launcher actions must recheck at invocation time.
struct LinuxDesktopActionContext: Equatable, Sendable {
    let terminalZoomActive: Bool
    let dashboardOpen: Bool
    let pickerActive: Bool

    var modalActive: Bool { terminalZoomActive || dashboardOpen || pickerActive }
}

/// Pure command-line classification for the `GApplication::command-line` adapter.
///
/// `G_APPLICATION_HANDLES_COMMAND_LINE` forwards the complete invocation to the primary process. Keeping the
/// parser host-free makes cold/warm launcher behavior testable without a display or a session bus.
enum LinuxApplicationInvocation: Equatable, Sendable {
    case activate
    case open([String])
    case desktopAction(LinuxDesktopAction)
    case terminal(LinuxTerminalLaunch)
    case invalid(String)

    static let desktopActionPrefix = "--desktop-action="

    static func parse(arguments: [String]) -> Self {
        guard !arguments.isEmpty else { return .activate }
        if arguments.first == "--" {
            let paths = Array(arguments.dropFirst())
            return paths.isEmpty ? .activate : .open(paths)
        }
        if let first = arguments.first, LinuxTerminalLaunch.begins(with: first) {
            return LinuxTerminalLaunch.parse(arguments)
        }
        if arguments.count == 1, let first = arguments.first,
           first.hasPrefix(desktopActionPrefix) {
            let value = String(first.dropFirst(desktopActionPrefix.count))
            guard let action = LinuxDesktopAction(rawValue: value) else {
                return .invalid("unknown desktop action: \(value)")
            }
            return .desktopAction(action)
        }
        if arguments.contains(where: { $0.hasPrefix(desktopActionPrefix) }) {
            return .invalid("--desktop-action must be used alone")
        }
        if let option = arguments.first(where: { $0.hasPrefix("-") }) {
            return .invalid("unknown option: \(option)")
        }
        return .open(arguments)
    }
}

struct LinuxTerminalLaunch: Equatable, Sendable {
    var directory: String?
    var title: String?
    var appID: String?
    var command: [String]?

    var isEphemeral: Bool { command != nil || appID != nil }

    private static let directoryPrefix = "--working-directory="
    private static let titlePrefix = "--title="
    private static let appIDPrefix = "--app-id="

    static func begins(with argument: String) -> Bool {
        argument == "-e" || argument.hasPrefix(directoryPrefix)
            || argument.hasPrefix(titlePrefix) || argument.hasPrefix(appIDPrefix)
    }

    static func parse(_ arguments: [String]) -> LinuxApplicationInvocation {
        var launch = Self()
        for (index, argument) in arguments.enumerated() {
            if argument == "-e" {
                let command = Array(arguments.dropFirst(index + 1))
                guard let program = command.first, !program.isEmpty else { return .invalid("-e requires a program") }
                launch.command = command
                return .terminal(launch)
            }
            if argument.hasPrefix(directoryPrefix) {
                launch.directory = String(argument.dropFirst(directoryPrefix.count))
            } else if argument.hasPrefix(titlePrefix) {
                launch.title = String(argument.dropFirst(titlePrefix.count))
            } else if argument.hasPrefix(appIDPrefix) {
                launch.appID = normalizedAppID(argument.dropFirst(appIDPrefix.count))
            } else {
                return .invalid("unexpected terminal launch argument: \(argument)")
            }
        }
        return .terminal(launch)
    }

    private static func normalizedAppID(_ value: Substring) -> String {
        func alphanumeric(_ byte: UInt8) -> Bool {
            (48...57).contains(byte) || (65...90).contains(byte) || (97...122).contains(byte)
        }
        let bytes = value.utf8.prefix(255)
        guard bytes.contains(where: alphanumeric) else { return "" }
        // Preserve Omarchy's class prefix so unusual executable names still match its floating rule.
        return String(decoding: bytes.map { alphanumeric($0) || [45, 46, 95].contains($0) ? $0 : 95 }, as: UTF8.self)
    }

    func workingDirectory(callerCwd: String?, fallback: String,
                          isDirectory: (String) -> Bool = Self.directoryExists) -> String {
        let callerDirectory = callerCwd.flatMap { $0.hasPrefix("/") && isDirectory($0) ? $0 : nil }
        guard let directory, !directory.isEmpty else { return callerDirectory ?? fallback }
        let candidate = directory.hasPrefix("/") ? directory : callerDirectory.map {
            ($0 as NSString).appendingPathComponent(directory)
        }
        if let candidate {
            let normalized = URL(fileURLWithPath: candidate).standardizedFileURL.path
            if isDirectory(normalized) { return normalized }
        }
        return callerDirectory ?? fallback
    }

    private static func directoryExists(_ path: String) -> Bool {
        var isDirectory: ObjCBool = false
        return FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory) && isDirectory.boolValue
    }
}
