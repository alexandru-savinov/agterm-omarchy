import Foundation
#if canImport(Glibc)
import Glibc
#else
import Darwin
#endif

/// Keeps one immutable theme pair for the app lifetime. Ordinary config arguments are copied by
/// libghostty during load, so their files live only through the consumer call, not every later replay.
/// The desktop runtime root is an input only: never replace XDG_RUNTIME_DIR in the process environment.
final class GhosttyGeneratedConfigStorage {
    enum Kind: String, CaseIterable {
        case defaults, runtime, title
    }

    struct AppearanceThemes: Equatable {
        let light: String
        let dark: String
    }

    enum Failure: Error, CustomStringConvertible {
        case invalidRoot, closed, directoryReplaced
        case systemCall(String, Int32)

        var description: String {
            switch self {
            case .invalidRoot: return "generated Ghostty config root must be an absolute path without NUL bytes"
            case .closed: return "generated Ghostty config storage is closed"
            case .directoryReplaced: return "generated Ghostty config directory was moved or replaced"
            case .systemCall(let operation, let code):
                return "generated Ghostty config \(operation): \(String(cString: strerror(code)))"
            }
        }
    }

    let directoryPath: String
    private let directoryFD: Int32
    private let identity: stat
    private let lock = NSLock()
    private var names: Set<String> = []
    private var themes: AppearanceThemes?
    private var nextFile = 0
    private var closed = false

    init(runtimeRoot: String? = ProcessInfo.processInfo.environment["XDG_RUNTIME_DIR"],
         temporaryRoot: String = NSTemporaryDirectory()) throws {
        let root = runtimeRoot.flatMap { $0.isEmpty ? nil : $0 } ?? temporaryRoot
        guard root.hasPrefix("/"), !root.utf8.contains(0) else { throw Failure.invalidRoot }
        var template = Array(((root as NSString).appendingPathComponent("agterm-ghostty-XXXXXX")).utf8CString)
        guard let path = template.withUnsafeMutableBufferPointer({ buffer in
            mkdtemp(buffer.baseAddress!).map { String(cString: $0) }
        }) else { throw Failure.systemCall("create directory", errno) }
        directoryPath = path
        let descriptor = path.withCString { open($0, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW) }
        guard descriptor >= 0 else {
            let failure = Failure.systemCall("open directory", errno)
            path.withCString { _ = rmdir($0) }
            throw failure
        }
        var info = stat()
        guard fstat(descriptor, &info) == 0 else {
            let failure = Failure.systemCall("stat directory", errno)
            _ = close(descriptor)
            path.withCString { _ = rmdir($0) }
            throw failure
        }
        directoryFD = descriptor
        identity = info
    }

    deinit { try? cleanup() }

    /// The pinned Config.loadFile copies ordinary arguments into replay state. Release its input after
    /// the consumer returns, including failed consumers, so repeated OSC/config updates stay bounded.
    func withConfigFile<T>(_ contents: String, as kind: Kind, consume: (String) throws -> T) throws -> T {
        lock.lock()
        let path: String
        do { path = try writeLocked(contents, prefix: kind.rawValue) } catch { lock.unlock(); throw error }
        lock.unlock()
        let result = Result { try consume(path) }
        lock.lock()
        defer { lock.unlock() }
        try removeLocked((path as NSString).lastPathComponent)
        return try result.get()
    }

    /// Unlike ordinary arguments, the selected conditional theme path can be reopened during finalize.
    func appearanceThemes() throws -> AppearanceThemes {
        lock.lock()
        defer { lock.unlock() }
        try requireOpenDirectory()
        if let themes {
            for path in [themes.light, themes.dark] {
                var info = stat()
                guard path.withCString({ lstat($0, &info) }) == 0 else { throw Failure.systemCall("stat theme", errno) }
            }
            return themes
        }
        let light = try writeLocked("", prefix: "lightTheme")
        let dark: String
        do { dark = try writeLocked("", prefix: "darkTheme") } catch {
            try removeLocked((light as NSString).lastPathComponent)
            throw error
        }
        let pair = AppearanceThemes(light: light, dark: dark)
        themes = pair
        return pair
    }

    var retainedFileCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return names.count
    }

    private func requireOpenDirectory() throws {
        guard !closed else { throw Failure.closed }
        guard ownsDirectoryPath else { throw Failure.directoryReplaced }
    }

    /// Exclusive creation never overwrites an earlier input or returns a stale path after failure.
    private func writeLocked(_ contents: String, prefix: String) throws -> String {
        try requireOpenDirectory()
        nextFile += 1
        let name = "\(prefix)-\(nextFile).conf"
        let descriptor = name.withCString {
            openat(directoryFD, $0, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, mode_t(S_IRUSR | S_IWUSR))
        }
        guard descriptor >= 0 else { throw Failure.systemCall("create \(prefix)", errno) }
        var needsClose = true
        var published = false
        defer {
            if needsClose { _ = close(descriptor) }
            if !published { name.withCString { _ = unlinkat(directoryFD, $0, 0) } }
        }
        try Data(contents.utf8).withUnsafeBytes { bytes in
            var offset = 0
            while offset < bytes.count {
                #if canImport(Glibc)
                let count = Glibc.write(descriptor, bytes.baseAddress!.advanced(by: offset), bytes.count - offset)
                #else
                let count = Darwin.write(descriptor, bytes.baseAddress!.advanced(by: offset), bytes.count - offset)
                #endif
                if count < 0 && errno == EINTR { continue }
                guard count > 0 else { throw Failure.systemCall("write \(prefix)", count == 0 ? EIO : errno) }
                offset += count
            }
        }
        let result = close(descriptor)
        needsClose = false
        guard result == 0 else { throw Failure.systemCall("close \(prefix)", errno) }
        guard ownsDirectoryPath else { throw Failure.directoryReplaced }
        names.insert(name)
        published = true
        return (directoryPath as NSString).appendingPathComponent(name)
    }

    private func removeLocked(_ name: String) throws {
        guard !closed else { throw Failure.closed }
        guard names.contains(name) else { return }
        guard name.withCString({ unlinkat(directoryFD, $0, 0) }) == 0 || errno == ENOENT else {
            throw Failure.systemCall("remove file", errno)
        }
        names.remove(name)
    }

    /// Call only after libghostty has released every config owner. Never recursively remove a directory:
    /// unrelated entries and any replacement at the original pathname must survive cleanup.
    func cleanup() throws {
        lock.lock()
        defer { lock.unlock() }
        guard !closed else { return }
        closed = true
        defer { _ = close(directoryFD) }
        var failure: Failure?
        for name in names {
            if name.withCString({ unlinkat(directoryFD, $0, 0) }) != 0 && errno != ENOENT && failure == nil {
                failure = .systemCall("remove file", errno)
            }
        }
        names.removeAll()
        themes = nil
        if ownsDirectoryPath {
            if directoryPath.withCString({ rmdir($0) }) != 0 && errno != ENOENT && failure == nil {
                failure = .systemCall("remove directory", errno)
            }
        } else if failure == nil {
            failure = .directoryReplaced
        }
        if let failure { throw failure }
    }

    private var ownsDirectoryPath: Bool {
        var current = stat()
        return directoryPath.withCString { lstat($0, &current) } == 0
            && current.st_dev == identity.st_dev && current.st_ino == identity.st_ino
    }
}
