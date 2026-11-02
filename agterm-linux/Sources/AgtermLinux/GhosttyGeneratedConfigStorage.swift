import Foundation
#if canImport(Glibc)
import Glibc
#else
import Darwin
#endif

/// Owns immutable libghostty inputs for one application lifetime. Config clones can replay files later,
/// so even an older runtime overlay stays intact until every surface and the app have been freed.
/// The desktop runtime root is an input only: never replace XDG_RUNTIME_DIR in the process environment.
final class GhosttyGeneratedConfigStorage {
    enum Kind: String, CaseIterable {
        case defaults, runtime, lightTheme, darkTheme, title
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
    private var names: [String] = []
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

    /// No previously published file is overwritten. Return a path only after the complete write closes.
    func write(_ contents: String, as kind: Kind) throws -> String {
        lock.lock()
        defer { lock.unlock() }
        guard !closed else { throw Failure.closed }
        guard ownsDirectoryPath else { throw Failure.directoryReplaced }
        nextFile += 1
        let name = "\(kind.rawValue)-\(nextFile).conf"
        let descriptor = name.withCString {
            openat(directoryFD, $0, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, mode_t(S_IRUSR | S_IWUSR))
        }
        guard descriptor >= 0 else { throw Failure.systemCall("create \(kind.rawValue)", errno) }
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
                guard count > 0 else { throw Failure.systemCall("write \(kind.rawValue)", count == 0 ? EIO : errno) }
                offset += count
            }
        }
        let result = close(descriptor)
        needsClose = false
        guard result == 0 else { throw Failure.systemCall("close \(kind.rawValue)", errno) }
        guard ownsDirectoryPath else { throw Failure.directoryReplaced }
        names.append(name)
        published = true
        return (directoryPath as NSString).appendingPathComponent(name)
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
