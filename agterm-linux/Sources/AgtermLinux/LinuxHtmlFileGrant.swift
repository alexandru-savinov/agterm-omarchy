import Foundation

/// Maps `agterm-file` requests onto a page's grant root.
/// Containment is checked on the symlink-resolved path at request time only:
/// `agterm_uri_scheme_finish_file` reopens that path later, so a swap in between is not prevented.
enum LinuxHtmlFileGrant {
    struct Resource: Equatable {
        let path: String
        let mime: String
    }

    /// The page id and the percent-decoded path of a request URI, as `serve` reads them.
    static func request(_ uri: String) -> (id: UUID, path: String)? {
        guard let components = URLComponents(string: uri), let host = components.host,
              let id = UUID(uuidString: host) else { return nil }
        return (id, components.path)
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

    private static func resolved(_ path: String) -> URL {
        URL(fileURLWithPath: path).standardizedFileURL.resolvingSymlinksInPath()
    }
}
