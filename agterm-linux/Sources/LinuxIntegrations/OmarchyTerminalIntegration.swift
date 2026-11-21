import Foundation
#if canImport(Glibc)
import Glibc
#else
import Darwin
#endif

/// Owns a small Lua module and the exact include block inserted into the user's Hyprland entrypoint.
public struct OmarchyTerminalIntegration {
    public enum State: String, Codable {
        case enabled, disabled, incomplete
    }

    public struct Status: Codable {
        public let state: State
        public let configPath: String
        public let modulePath: String
    }

    private struct Failure: LocalizedError {
        let message: String
        init(_ message: String) { self.message = message }
        var errorDescription: String? { message }
    }

    private struct File: Equatable {
        let data: Data
        let inode: ino_t
        let mode: mode_t
        let group: gid_t
    }

    private let directory: URL

    public init(environment: IntegrationEnvironment = .process()) {
        let configRoot = environment.xdgConfigHome.flatMap { path in
            path.hasPrefix("/") ? URL(fileURLWithPath: path, isDirectory: true) : nil
        } ?? environment.homeDirectory.appendingPathComponent(".config", isDirectory: true)
        directory = configRoot.appendingPathComponent("hypr", isDirectory: true)
    }

    public func status() throws -> Status {
        try withDirectory { fd in
            let files = try inspect(fd)
            return status(hasInclude: files.range != nil, hasModule: files.module != nil)
        }
    }

    public func setEnabled(_ enabled: Bool) throws -> Status {
        try withDirectory { fd in
            let (config, range, module) = try inspect(fd)
            if enabled {
                if module == nil {
                    try write(Self.module, named: Self.moduleName, replacing: nil, in: fd)
                }
                if range == nil {
                    var updated = config.data
                    let index = Self.insertionPoint(in: updated)
                    updated.insert(contentsOf: Self.include, at: index)
                    do {
                        try write(updated, named: Self.configName, replacing: config, in: fd)
                    } catch {
                        if module == nil { try removeModule(in: fd) }
                        throw error
                    }
                }
            } else {
                if let range {
                    var updated = config.data
                    updated.removeSubrange(range)
                    try write(updated, named: Self.configName, replacing: config, in: fd)
                }
                if module != nil { try removeModule(in: fd) }
            }
            return status(hasInclude: enabled, hasModule: enabled)
        }
    }

    private static let configName = "hyprland.lua"
    private static let moduleName = "agterm-omarchy.lua"
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
    private static let module = Data((#"""
        -- Managed by agtermctl integration omarchy.
        hl.window_rule({
          name = "agterm-omarchy-terminal",
          match = { class = [[^io\.github\.melonamin\.agterm$]] },
          tag = "+terminal",
        })
        """# + "\n").utf8)

    private func status(hasInclude: Bool, hasModule: Bool) -> Status {
        let state: State = hasInclude == hasModule ? (hasInclude ? .enabled : .disabled) : .incomplete
        return Status(state: state, configPath: directory.appendingPathComponent(Self.configName).path,
                      modulePath: directory.appendingPathComponent(Self.moduleName).path)
    }

    private func inspect(_ fd: Int32) throws -> (config: File, range: Range<Int>?, module: File?) {
        guard let config = try read(Self.configName, in: fd) else {
            throw Failure("Hyprland Lua config is missing: \(directory.appendingPathComponent(Self.configName).path)")
        }
        var unmanaged = config.data
        let range = unmanaged.range(of: Self.include)
        if let range { unmanaged.removeSubrange(range) }
        guard let source = String(data: unmanaged, encoding: .utf8),
              !source.contains(Self.marker), !source.contains("hypr.agterm-omarchy"), !source.contains(Self.moduleName) else {
            throw Failure("The Hyprland entrypoint contains an invalid or conflicting agterm include.")
        }
        let module = try read(Self.moduleName, in: fd)
        if let module, module.data != Self.module {
            throw Failure("The existing \(Self.moduleName) is different from the agterm-managed module.")
        }
        return (config, range, module)
    }

    private static func insertionPoint(in config: Data) -> Int {
        let beginning = config.starts(with: [0xef, 0xbb, 0xbf]) ? 3 : 0
        // Keep a Lua hashbang at the beginning. The managed rule runs before Omarchy's tag-based rules
        // and before any top-level return in a customized entrypoint.
        if config.dropFirst(beginning).starts(with: [35, 33]),
           let newline = config[beginning...].firstIndex(of: 10) {
            return newline + 1
        }
        return beginning
    }

    private func withDirectory<T>(_ action: (Int32) throws -> T) throws -> T {
        let fd = open(directory.path, O_RDONLY | O_DIRECTORY | O_CLOEXEC)
        guard fd >= 0 else { throw failure("open \(directory.path)") }
        defer { _ = close(fd) }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else { throw failure("lock \(directory.path)") }
        defer { _ = flock(fd, LOCK_UN) }
        return try action(fd)
    }

    private func read(_ name: String, in directory: Int32) throws -> File? {
        let fd = openat(directory, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK)
        guard fd >= 0 else {
            if errno == ENOENT { return nil }
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
        let data = try handle.readToEnd() ?? Data()
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

    private func failure(_ operation: String) -> Failure {
        let code = errno
        return Failure("Could not \(operation): \(String(cString: strerror(code)))")
    }
}
