import Foundation
#if canImport(Glibc)
import Glibc
#else
import Darwin
#endif

/// Owns the marked Hyprland terminal tag and the reversible Omarchy terminal preference.
public struct OmarchyTerminalIntegration {
    public enum State: String, Codable {
        case enabled, disabled, incomplete, unavailable
    }

    public struct Status: Codable {
        public let state: State
        public let configPath: String
        public let modulePath: String
        public let detail: String?
        public let terminalSelection: TerminalSelection
        public let warnings: [String]
    }

    public struct TerminalSelection: Codable {
        public enum State: String, Codable {
            case unmanaged, managed, pending, conflict, unavailable
        }

        public let state: State
        public let path: String
        public let firstEntry: String?
        public let detail: String?
    }

    private struct Failure: LocalizedError {
        let message: String
        init(_ message: String) { self.message = message }
        var errorDescription: String? { message }
    }

    private enum EntryPointUnavailable: LocalizedError {
        case missing(String)
        case symlink(String)

        var errorDescription: String? {
            switch self {
            case .missing(let path): return "Hyprland Lua config is missing: \(path)"
            case .symlink(let path): return "Hyprland Lua config is a symbolic link: \(path)"
            }
        }
    }

    private struct File: Equatable {
        let data: Data
        let inode: ino_t
        let mode: mode_t
        let group: gid_t
    }

    private struct RestoreRecord: Codable {
        struct Original: Codable {
            let data: Data
            let mode: mode_t
            let group: gid_t

            init(_ file: File) {
                data = file.data
                mode = file.mode
                group = file.group
            }

            func matches(_ file: File) -> Bool {
                file.data == data && file.mode == mode && file.group == group
            }
        }

        let version: Int
        let original: Original?

        var managedBytes: Data { OmarchyTerminalIntegration.selectionPrefix + (original?.data ?? Data()) }

        func matchesOriginal(_ file: File?) -> Bool {
            guard let file else { return original == nil }
            return original?.matches(file) == true
        }

        func matchesManaged(_ file: File?) -> Bool {
            guard let file, file.data == managedBytes else { return false }
            guard let original else { return file.mode == 0o600 }
            return file.mode == original.mode && file.group == original.group
        }
    }

    private struct TerminalFiles {
        let list: File?
        let backup: File?
        let record: RestoreRecord?

        var isUserSelection: Bool {
            guard record == nil, let list else { return false }
            return list.data.prefix(while: { $0 != 10 && $0 != 13 }) == Data(OmarchyTerminalIntegration.desktopID.utf8)
        }

        var state: TerminalSelection.State {
            // Only an active restore record gives the integration ownership of the list.
            guard let record else { return .unmanaged }
            if record.matchesManaged(list) { return .managed }
            if record.matchesOriginal(list) { return .pending }
            return .conflict
        }
    }

    private typealias TagFiles = (config: File, range: Range<Int>?, module: File?)
    private let environment: IntegrationEnvironment
    private let directory: URL
    private let terminalDirectory: URL

    public init(environment: IntegrationEnvironment = .process()) {
        self.environment = environment
        let configRoot = environment.xdgConfigHome.flatMap { path in
            path.hasPrefix("/") ? URL(fileURLWithPath: path, isDirectory: true) : nil
        } ?? environment.homeDirectory.appendingPathComponent(".config", isDirectory: true)
        terminalDirectory = configRoot
        directory = configRoot.appendingPathComponent("hypr", isDirectory: true)
    }

    public func status() throws -> Status {
        let selection: TerminalSelection
        do {
            selection = try withTerminalDirectory { fd in
                try describeSelection(inspectTerminalFiles(fd))
            }
        } catch {
            selection = TerminalSelection(state: .unavailable, path: terminalListPath, firstEntry: nil,
                                          detail: error.localizedDescription)
        }
        do {
            return try withDirectory { fd in
                let files = try inspect(fd)
                return status(tagState(hasInclude: files.range != nil, hasModule: files.module != nil), selection: selection)
            }
        } catch let error as EntryPointUnavailable {
            return status(.unavailable, selection: selection, detail: error.localizedDescription)
        }
    }

    public func setEnabled(_ enabled: Bool) throws -> Status {
        try withDirectory { hyprFD in
            let tag = try inspect(hyprFD)
            return try withTerminalDirectory(requirePresent: enabled) { terminalFD in
                let files = try inspectTerminalFiles(terminalFD)
                if enabled {
                    guard let terminalFD else { throw Failure("The Omarchy config directory is unavailable.") }
                    let record = try prepareTerminalSelection(files, in: terminalFD)
                    try setTagEnabled(true, files: tag, in: hyprFD)
                    if let record {
                        try write(record.managedBytes, named: Self.terminalListName, replacing: files.list, in: terminalFD)
                    }
                    return status(.enabled, selection: try describeSelection(inspectTerminalFiles(terminalFD)))
                }
                try setTagEnabled(false, files: tag, in: hyprFD)
                let archive = try terminalFD.flatMap { try disableTerminalSelection(files, in: $0) }
                let selection = try describeSelection(inspectTerminalFiles(terminalFD), archivedRecord: archive)
                return status(.disabled, selection: selection)
            }
        }
    }

    private func setTagEnabled(_ enabled: Bool, files: TagFiles, in fd: Int32) throws {
        let (config, range, module) = files
        if enabled {
            if module == nil { try write(Self.module, named: Self.moduleName, replacing: nil, in: fd) }
            if range == nil {
                var updated = config.data
                let (index, include) = Self.insertion(in: updated)
                updated.insert(contentsOf: include, at: index)
                do {
                    try write(updated, named: Self.configName, replacing: config, in: fd)
                } catch {
                    if module == nil { try removeModule(in: fd) }
                    throw error
                }
            }
            if let module, module.data == Self.tagOnlyModule {
                try write(Self.module, named: Self.moduleName, replacing: module, in: fd)
            }
        } else {
            if let range {
                var updated = config.data
                var start = range.lowerBound
                // Preserve the separator if the user appended code after an unterminated hash header.
                if range.upperBound < updated.endIndex && updated[start] == 10 { start += 1 }
                updated.removeSubrange(start..<range.upperBound)
                try write(updated, named: Self.configName, replacing: config, in: fd)
            }
            if let module { try remove(Self.moduleName, matching: module, in: fd) }
        }
    }

    private static let configName = "hyprland.lua"
    private static let moduleName = "agterm-omarchy.lua"
    private static let desktopID = "io.github.melonamin.agterm.desktop"
    private static let terminalListName = "xdg-terminals.list"
    private static let restoreName = ".agterm-omarchy-terminal-list-v1.json"
    private static let selectionPrefix = Data("\(desktopID)\n# Managed by agtermctl integration omarchy\n".utf8)
    private static let terminalListLimit = 1024 * 1024
    private static let marker = "agterm Omarchy terminal tag"
    private static let include = Data(("""
        -- >>> agterm Omarchy terminal tag >>>
        do
          local root = os.getenv("XDG_CONFIG_HOME")
          if not root or root:sub(1, 1) ~= "/" then root = os.getenv("HOME") .. "/.config" end
          local tag_rule = loadfile(root .. "/hypr/agterm-omarchy.lua")
          if tag_rule then tag_rule() end
        end
        -- <<< agterm Omarchy terminal tag <<<
        """ + "\n").utf8)
    // Own the inserted line ending when the original hash line ended at EOF.
    private static let includeAfterUnterminatedHeader = Data("\n-- agterm Omarchy terminal tag separator\n".utf8) + include
    private static let tagOnlyModule = Data((#"""
        -- Managed by agtermctl integration omarchy.
        hl.window_rule({
          name = "agterm-omarchy-terminal",
          match = { class = [[^io\.github\.melonamin\.agterm$]] },
          tag = "+terminal",
        })
        """# + "\n").utf8)
    // Register named defaults early so later named and anonymous personal rules can override them.
    private static let module = tagOnlyModule + Data((#"""
        hl.window_rule({
          name = "agterm-omarchy-scroll",
          match = { class = [[^io\.github\.melonamin\.agterm$]] },
          scroll_touchpad = 0.2,
        })
        hl.window_rule({
          name = "agterm-omarchy-tui-scroll",
          match = { class = [[^(org\.omarchy\..*|TUI\..*)$]], initial_title = [[^agterm$]] },
          scroll_touchpad = 0.2,
        })
        """# + "\n").utf8)

    private func tagState(hasInclude: Bool, hasModule: Bool) -> State {
        hasInclude == hasModule ? (hasInclude ? .enabled : .disabled) : .incomplete
    }

    private var configPath: String { directory.appendingPathComponent(Self.configName).path }

    private func status(_ state: State, selection: TerminalSelection, detail: String? = nil) -> Status {
        Status(state: state, configPath: configPath,
               modulePath: directory.appendingPathComponent(Self.moduleName).path, detail: detail,
               terminalSelection: selection, warnings: shadowingWarnings())
    }

    private var terminalListPath: String { terminalDirectory.appendingPathComponent(Self.terminalListName).path }

    private func describeSelection(_ files: TerminalFiles, archivedRecord: String? = nil) -> TerminalSelection {
        let firstEntry = files.list?.data.split(separator: 10).lazy.compactMap {
            String(data: $0, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
        }.first { entry in
            !entry.isEmpty && !entry.hasPrefix("#") && !entry.hasPrefix("-") && !entry.hasPrefix("+") && !entry.hasPrefix("/")
        }
        if let archivedRecord {
            return TerminalSelection(state: .conflict, path: terminalListPath, firstEntry: firstEntry,
                                     detail: "Kept the edited terminal list and moved the original restore record to \(archivedRecord).")
        }
        let detail: String?
        switch files.state {
        case .conflict:
            let restorePath = terminalDirectory.appendingPathComponent(Self.restoreName).path
            detail = "The terminal list changed while enabled. Disable to keep the edits and archive the restore record at \(restorePath)."
        case .pending:
            detail = "A restore record exists and the original terminal list is in place. Retry enable or disable."
        default:
            detail = nil
        }
        return TerminalSelection(state: files.state, path: terminalListPath, firstEntry: firstEntry, detail: detail)
    }

    private func inspectTerminalFiles(_ fd: Int32?) throws -> TerminalFiles {
        guard let fd else { return TerminalFiles(list: nil, backup: nil, record: nil) }
        let list = try read(Self.terminalListName, in: fd)
        let backup = try read(Self.restoreName, in: fd)
        let record = try backup.map { file in
            guard let record = try? JSONDecoder().decode(RestoreRecord.self, from: file.data),
                  record.version == 1, record.managedBytes.count <= Self.terminalListLimit else {
                throw Failure("The agterm terminal-list restore record is invalid: \(Self.restoreName)")
            }
            return record
        }
        return TerminalFiles(list: list, backup: backup, record: record)
    }

    private func prepareTerminalSelection(_ files: TerminalFiles, in fd: Int32) throws -> RestoreRecord? {
        if files.state == .conflict {
            throw Failure(describeSelection(files).detail ?? "Conflicting Omarchy terminal list.")
        }
        // A selection that the user already made belongs to the user.
        guard files.state != .managed, !files.isUserSelection else { return nil }
        if let record = files.record { return record }
        let record = RestoreRecord(version: 1, original: files.list.map(RestoreRecord.Original.init))
        guard record.managedBytes.count <= Self.terminalListLimit else { throw Failure("The terminal list is too large to manage.") }
        // Publish the original before any Lua or terminal-list edits. If a later write fails,
        // the pending record lets the next enable or disable resume safely.
        try write(JSONEncoder().encode(record), named: Self.restoreName, replacing: nil, in: fd)
        return record
    }

    private func disableTerminalSelection(_ files: TerminalFiles, in fd: Int32) throws -> String? {
        guard let backup = files.backup else { return nil }
        if files.state == .conflict {
            let name = "\(Self.restoreName).conflict-\(UUID().uuidString)"
            // Publish without replacement before removing the active record, so a failed archive write
            // leaves the original in place and a later disable can retry.
            try write(backup.data, named: name, replacing: nil, in: fd)
            try remove(Self.restoreName, matching: backup, in: fd)
            return terminalDirectory.appendingPathComponent(name).path
        }
        if files.state == .managed, let list = files.list {
            if let original = files.record?.original {
                try write(original.data, named: Self.terminalListName, replacing: list, in: fd)
            } else {
                try remove(Self.terminalListName, matching: list, in: fd)
            }
        }
        try remove(Self.restoreName, matching: backup, in: fd)
        return nil
    }

    private func shadowingWarnings() -> [String] {
        let packageCLI = environment.packageCLI()
        let systemCLI = packageCLI ?? URL(fileURLWithPath: "/usr/bin/agtermctl")
        let packageDesktop = systemCLI.deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("share/applications/\(Self.desktopID)")
        var warnings: [String] = []
        if let desktop = desktopOnXDGPath(), Self.fileInfo(packageDesktop) != nil, !Self.sameFile(desktop, packageDesktop) {
            warnings.append("Desktop entry \(desktop.path) shadows \(packageDesktop.path).")
        }
        if let cli = environment.pathCLI() {
            let intended = packageCLI ?? environment.executableURL
            let launcher = (packageCLI ?? cli).resolvingSymlinksInPath()
            let wrapperBinary = launcher.appendingPathExtension("bin")
            // The system package's shell launcher execs its sibling agtermctl.bin.
            let matchesWrapper = packageCLI == nil
                ? Self.sameFile(wrapperBinary, intended)
                : Self.sameFile(wrapperBinary, cli)
            if !Self.sameFile(cli, intended), !matchesWrapper {
                warnings.append("PATH entry \(cli.path) shadows \(intended.path). Run \(intended.path) explicitly.")
            }
        }
        return warnings
    }

    private func desktopOnXDGPath() -> URL? {
        let variables = environment.probeEnvironment
        let userData = variables["XDG_DATA_HOME"].flatMap { $0.hasPrefix("/") ? URL(fileURLWithPath: $0) : nil }
            ?? environment.homeDirectory.appendingPathComponent(".local/share", isDirectory: true)
        let systemData = variables["XDG_DATA_DIRS"].flatMap { $0.isEmpty ? nil : $0 } ?? "/usr/local/share:/usr/share"
        let directories = [userData] + systemData.split(separator: ":").filter { $0.hasPrefix("/") }.map {
            URL(fileURLWithPath: String($0), isDirectory: true)
        }
        return directories.lazy.map { $0.appendingPathComponent("applications/\(Self.desktopID)") }
            .first { Self.fileInfo($0) != nil }
    }

    private static func fileInfo(_ url: URL) -> stat? {
        var info = stat()
        guard fstatat(AT_FDCWD, url.path, &info, 0) == 0, info.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG) else { return nil }
        return info
    }

    private static func sameFile(_ first: URL, _ second: URL) -> Bool {
        guard let left = fileInfo(first), let right = fileInfo(second) else { return false }
        return left.st_dev == right.st_dev && left.st_ino == right.st_ino
    }

    private func inspect(_ fd: Int32) throws -> TagFiles {
        guard let config = try read(Self.configName, in: fd) else {
            throw EntryPointUnavailable.missing(configPath)
        }
        var unmanaged = config.data
        let range = unmanaged.range(of: Self.includeAfterUnterminatedHeader) ?? unmanaged.range(of: Self.include)
        if let range { unmanaged.removeSubrange(range) }
        guard let source = String(data: unmanaged, encoding: .utf8),
              !source.contains(Self.marker), !source.contains("hypr.agterm-omarchy"), !source.contains(Self.moduleName) else {
            throw Failure("The Hyprland entrypoint contains an invalid or conflicting agterm include.")
        }
        let module = try read(Self.moduleName, in: fd)
        if let module, module.data != Self.module, module.data != Self.tagOnlyModule {
            throw Failure("The existing \(Self.moduleName) is different from the agterm-managed module.")
        }
        return (config, range, module)
    }

    private static func insertion(in config: Data) -> (Int, Data) {
        let beginning = config.starts(with: [0xef, 0xbb, 0xbf]) ? 3 : 0
        // Lua ignores the first line when it starts with #. Keep it ahead of the managed rule,
        // which must run before Omarchy's tag-based rules or a top-level return.
        guard config.dropFirst(beginning).first == 35 else { return (beginning, include) }
        guard let newline = config[beginning...].firstIndex(of: 10) else {
            return (config.count, includeAfterUnterminatedHeader)
        }
        return (newline + 1, include)
    }

    private func withDirectory<T>(_ action: (Int32) throws -> T) throws -> T {
        try withLockedDirectory(directory, whenMissing: { throw EntryPointUnavailable.missing(configPath) }, action)
    }

    private func withTerminalDirectory<T>(requirePresent: Bool = false, _ action: (Int32?) throws -> T) throws -> T {
        return try withLockedDirectory(terminalDirectory, whenMissing: {
            if requirePresent { throw failure("open \(terminalDirectory.path)") }
            return try action(nil)
        }, { try action($0) })
    }

    private func withLockedDirectory<T>(_ directory: URL, whenMissing: () throws -> T, _ action: (Int32) throws -> T) throws -> T {
        let fd = open(directory.path, O_RDONLY | O_DIRECTORY | O_CLOEXEC)
        guard fd >= 0 else {
            if errno == ENOENT { return try whenMissing() }
            throw failure("open \(directory.path)")
        }
        defer { _ = close(fd) }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else { throw failure("lock \(directory.path)") }
        defer { _ = flock(fd, LOCK_UN) }
        return try action(fd)
    }

    private func read(_ name: String, in directory: Int32) throws -> File? {
        let fd = openat(directory, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK)
        guard fd >= 0 else {
            if errno == ENOENT { return nil }
            if errno == ELOOP && name == Self.configName { throw EntryPointUnavailable.symlink(configPath) }
            throw failure("open \(name)")
        }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        defer { try? handle.close() }
        var entry = stat()
        guard fstat(fd, &entry) == 0 else { throw failure("stat \(name)") }
        guard entry.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG),
              entry.st_uid == geteuid(), entry.st_nlink == 1 else {
            throw Failure("Expected a regular file owned by this user with a single link: \(name)")
        }
        let data: Data
        if name == Self.terminalListName || name == Self.restoreName {
            let limit = name == Self.terminalListName ? Self.terminalListLimit : Self.terminalListLimit * 2
            guard entry.st_size <= limit else { throw Failure("The file is too large to manage: \(name)") }
            data = try handle.read(upToCount: limit + 1) ?? Data()
            guard data.count <= limit else { throw Failure("The file is too large to manage: \(name)") }
        } else {
            data = try handle.readToEnd() ?? Data()
        }
        return File(data: data, inode: entry.st_ino, mode: entry.st_mode & 0o7777, group: entry.st_gid)
    }

    private func write(_ data: Data, named name: String, replacing original: File?, in directory: Int32) throws {
        let temporary = ".agterm-omarchy-\(UUID().uuidString)"
        let fd = openat(directory, temporary, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0o600)
        guard fd >= 0 else { throw failure("stage \(name)") }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        defer {
            try? handle.close()
            _ = unlinkat(directory, temporary, 0)
        }
        try handle.write(contentsOf: data)
        if let original {
            guard fchown(fd, uid_t.max, original.group) == 0, fchmod(fd, original.mode) == 0 else {
                throw failure("preserve permissions for \(name)")
            }
        } else {
            guard fchmod(fd, 0o600) == 0 else { throw failure("set private permissions for \(name)") }
        }
        try handle.synchronize()
        guard try read(name, in: directory) == original else {
            throw Failure("\(name) changed during the integration update. Retry with the current files.")
        }
        if original == nil {
            guard linkat(directory, temporary, directory, name, 0) == 0 else { throw failure("create \(name)") }
        } else {
            guard renameat(directory, temporary, directory, name) == 0 else { throw failure("update \(name)") }
        }
    }

    private func removeModule(in directory: Int32) throws {
        guard let module = try read(Self.moduleName, in: directory) else { return }
        guard module.data == Self.module else { throw Failure("The agterm module changed during the integration update.") }
        guard unlinkat(directory, Self.moduleName, 0) == 0 else { throw failure("remove \(Self.moduleName)") }
    }

    private func remove(_ name: String, matching expected: File, in directory: Int32) throws {
        guard try read(name, in: directory) == expected else { throw Failure("\(name) changed during the integration update.") }
        guard unlinkat(directory, name, 0) == 0 else { throw failure("remove \(name)") }
    }

    private func failure(_ operation: String) -> Failure {
        let code = errno
        return Failure("Could not \(operation): \(String(cString: strerror(code)))")
    }
}
