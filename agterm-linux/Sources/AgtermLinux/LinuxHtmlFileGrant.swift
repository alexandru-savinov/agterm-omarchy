import CWebKit
import Foundation
import Glibc

/// Maps `agterm-file` requests onto a page's grant root.
/// `resource` checks the symlink-resolved path, so in-root symlinks and a symlinked root still serve;
/// `openDescriptor` then opens that canonical path once from the root following no symlink, and `serve` streams
/// that descriptor. A component swapped between the two steps fails the open instead of being followed (#403).
enum LinuxHtmlFileGrant {
    struct Resource: Equatable, Sendable {
        let path: String
        let mime: String
    }

    /// The scheme, page id and percent-decoded path of a request URI, as `serve` reads them.
    static func request(_ uri: String) -> (scheme: String?, id: UUID, path: String)? {
        guard let components = URLComponents(string: uri), let host = components.host,
              let id = UUID(uuidString: host) else { return nil }
        return (components.scheme, id, components.path)
    }

    /// The path of `path` below `grantRoot` after both are resolved, or nil when it is the root or outside it.
    static func relativePath(of path: String, within grantRoot: String) -> String? {
        let root = resolved(grantRoot).pathComponents
        let file = resolved(path).pathComponents
        guard file.starts(with: root), file.count > root.count else { return nil }
        return file.dropFirst(root.count).joined(separator: "/")
    }

    static func resource(for uriPath: String, grantRoot: String) -> Resource? {
        let root = resolved(grantRoot)
        let path = root.appendingPathComponent(String(uriPath.drop(while: { $0 == "/" })))
            .standardizedFileURL.resolvingSymlinksInPath()
        guard path.pathComponents.starts(with: root.pathComponents),
              path.pathComponents.count > root.pathComponents.count else { return nil }
        let attributes = try? FileManager.default.attributesOfItem(atPath: path.path)
        guard attributes?[.type] as? FileAttributeType == .typeRegular,
              FileManager.default.isReadableFile(atPath: path.path) else { return nil }
        let mime: String = switch path.pathExtension.lowercased() {
        case "html", "htm": "text/html"
        case "css": "text/css"
        case "js", "mjs": "text/javascript"
        case "json": "application/json"
        case "svg": "image/svg+xml"
        case "png": "image/png"
        case "jpg", "jpeg": "image/jpeg"
        case "gif": "image/gif"
        case "webp": "image/webp"
        case "woff": "font/woff"
        case "woff2": "font/woff2"
        case "txt": "text/plain"
        default: "application/octet-stream"
        }
        return Resource(path: path.path, mime: mime)
    }

    /// How `openDescriptor` walks the path below the root.
    enum Resolution: Sendable {
        /// One `openat2` with `RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS`.
        case beneath
        /// One `openat` per component, each with `O_NOFOLLOW`, for kernels or sandboxes without `openat2`.
        case components
    }

    static let beneathResolutionAvailable = probedResolution() == .beneath

    /// Seccomp filters older than `openat2` answer EPERM rather than ENOSYS.
    static func probedResolution(_ probe: () -> (descriptor: Int32, error: Int32) = {
        let fd = agterm_openat2_beneath(AT_FDCWD, ".", agterm_o_path() | O_DIRECTORY | O_CLOEXEC)
        return (fd, errno)
    }) -> Resolution {
        let result = probe()
        if result.descriptor >= 0 { close(result.descriptor) }
        return result.descriptor < 0 && (result.error == ENOSYS || result.error == EPERM) ? .components : .beneath
    }

    /// A descriptor the caller owns for `resource` when it is still the regular file `resource` checked, or nil.
    /// `O_NONBLOCK` keeps a FIFO swapped in after the check from blocking the open on the GTK main thread.
    /// Directories are opened `O_PATH`, so like a lookup by name they need search permission, not read.
    static func openDescriptor(_ resource: Resource, grantRoot: String,
                               resolution: Resolution = beneathResolutionAvailable ? .beneath : .components) -> Int32? {
        let root = resolved(grantRoot)
        let file = URL(fileURLWithPath: resource.path).pathComponents
        guard file.starts(with: root.pathComponents), file.count > root.pathComponents.count else { return nil }
        let components = Array(file.dropFirst(root.pathComponents.count))
        guard !components.contains(where: { $0 == "." || $0 == ".." }) else { return nil }
        let rootFD = open(root.path, agterm_o_path() | O_DIRECTORY | O_CLOEXEC)
        guard rootFD >= 0 else { return nil }
        defer { close(rootFD) }
        let flags = O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_NOCTTY | O_CLOEXEC
        let fd = switch resolution {
        case .beneath: agterm_openat2_beneath(rootFD, components.joined(separator: "/"), flags)
        case .components: openComponents(components, from: rootFD, flags: flags)
        }
        guard fd >= 0 else { return nil }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG else {
            close(fd)
            return nil
        }
        return fd
    }

    private static func openComponents(_ components: [String], from rootFD: Int32, flags: Int32) -> Int32 {
        var parent = rootFD
        for name in components.dropLast() {
            let next = openat(parent, name, agterm_o_path() | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            if parent != rootFD { close(parent) }
            guard next >= 0 else { return -1 }
            parent = next
        }
        defer { if parent != rootFD { close(parent) } }
        return openat(parent, components[components.count - 1], flags)
    }

    private static func resolved(_ path: String) -> URL {
        URL(fileURLWithPath: path).standardizedFileURL.resolvingSymlinksInPath()
    }
}
