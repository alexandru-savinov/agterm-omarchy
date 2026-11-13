import Foundation
import Testing
@testable import AgtermLinux
#if canImport(Glibc)
import Glibc
#endif

/// A disposable tree: `root` is the grant, `root2` its sibling-prefix neighbour, `outside` a non-descendant.
private final class GrantTree {
    let base: String
    var root: String { base + "/root" }
    var outside: String { base + "/outside/secret.txt" }

    init() throws {
        let raw = FileManager.default.temporaryDirectory.appendingPathComponent("agterm-grant-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: raw, withIntermediateDirectories: true)
        base = raw.resolvingSymlinksInPath().path
        let fm = FileManager.default
        for dir in ["root/assets/css", "root/assets/img", "root/empty", "root2", "outside"] {
            try fm.createDirectory(atPath: "\(base)/\(dir)", withIntermediateDirectories: true)
        }
        for file in ["root/index.html", "root/assets/css/site.css", "root/assets/img/logo.png", "root/data.bin",
                     "root2/secret.txt", "outside/secret.txt"] {
            #expect(fm.createFile(atPath: "\(base)/\(file)", contents: Data("x".utf8)))
        }
        try fm.createSymbolicLink(atPath: "\(root)/link-out", withDestinationPath: outside)
        try fm.createSymbolicLink(atPath: "\(root)/link-sibling", withDestinationPath: "\(base)/root2/secret.txt")
        try fm.createSymbolicLink(atPath: "\(root)/dir-out", withDestinationPath: "\(base)/outside")
        try fm.createSymbolicLink(atPath: "\(root)/relative-out", withDestinationPath: "../outside/secret.txt")
        try fm.createSymbolicLink(atPath: "\(root)/dangling", withDestinationPath: "\(base)/missing.txt")
        try fm.createSymbolicLink(atPath: "\(root)/link-in", withDestinationPath: "\(root)/assets/css/site.css")
        try fm.createSymbolicLink(atPath: "\(base)/root-link", withDestinationPath: root)
        #expect(mkfifo("\(root)/pipe", 0o600) == 0)
    }

    deinit { try? FileManager.default.removeItem(atPath: base) }

    func resource(_ uriPath: String, grant: String? = nil) -> LinuxHtmlFileGrant.Resource? {
        LinuxHtmlFileGrant.resource(for: uriPath, grantRoot: grant ?? root)
    }

    /// The path `serve` hands to `resource` for a raw request URI.
    func served(_ encodedPath: String) throws -> LinuxHtmlFileGrant.Resource? {
        let id = UUID()
        let request = try #require(LinuxHtmlFileGrant.request("agterm-file://\(id.uuidString.lowercased())\(encodedPath)"))
        #expect(request.id == id)
        return resource(request.path)
    }
}

@Suite("HTML file grants serve only readable regular descendants")
struct LinuxHtmlFileGrantTests {
    @Test(arguments: [
        ("/index.html", "index.html", "text/html"),
        ("/assets/css/site.css", "assets/css/site.css", "text/css"),
        ("assets/img/logo.png", "assets/img/logo.png", "image/png"),
        ("//assets/./css/../img/logo.png", "assets/img/logo.png", "image/png"),
        ("/data.bin", "data.bin", "application/octet-stream"),
        ("/link-in", "assets/css/site.css", "text/css"),
    ])
    func nestedRegularDescendantsAreServed(_ uriPath: String, _ relative: String, _ mime: String) throws {
        let tree = try GrantTree()
        #expect(tree.resource(uriPath) == .init(path: "\(tree.root)/\(relative)", mime: mime))
    }

    @Test func aSymlinkedGrantRootServesItsDescendants() throws {
        let tree = try GrantTree()
        let resource = tree.resource("/assets/css/site.css", grant: "\(tree.base)/root-link")
        #expect(resource?.path == "\(tree.root)/assets/css/site.css")
    }

    @Test(arguments: [
        "/%2e%2e/outside/secret.txt",
        "/%2E%2E/root2/secret.txt",
        "/assets/%2e%2e/%2e%2e/outside/secret.txt",
        "/%2e%2e%2foutside%2fsecret.txt",
        "/..%2F..%2F..%2F..%2Fetc%2Fpasswd",
        "/.%2e/root2/secret.txt",
        "/%252e%252e/outside/secret.txt",
    ])
    func encodedParentSegmentsDoNotEscape(_ encodedPath: String) throws {
        let tree = try GrantTree()
        #expect(try tree.served(encodedPath) == nil)
    }

    @Test func anEncodedDotSegmentInsideTheRootStillResolves() throws {
        let tree = try GrantTree()
        #expect(try tree.served("/assets/%2e%2e/index.html")?.path == "\(tree.root)/index.html")
    }

    @Test(arguments: ["/../root2/secret.txt", "/../root2", "/../outside/secret.txt", "/../../../../etc/passwd"])
    func parentSegmentsDoNotReachSiblings(_ uriPath: String) throws {
        let tree = try GrantTree()
        #expect(tree.resource(uriPath) == nil)
    }

    @Test func aSiblingPrefixRootIsNotADescendant() throws {
        let tree = try GrantTree()
        #expect(LinuxHtmlFileGrant.relativePath(of: "\(tree.base)/root2/secret.txt", within: tree.root) == nil)
        #expect(LinuxHtmlFileGrant.relativePath(of: "\(tree.root)/assets/css/site.css", within: tree.root)
            == "assets/css/site.css")
    }

    @Test(arguments: ["/link-out", "/link-sibling", "/dir-out/secret.txt", "/relative-out"])
    func symlinksLeavingTheRootAreRefused(_ uriPath: String) throws {
        let tree = try GrantTree()
        #expect(tree.resource(uriPath) == nil)
        #expect(LinuxHtmlFileGrant.relativePath(of: tree.root + uriPath, within: tree.root) == nil)
    }

    @Test func aDanglingSymlinkIsRefused() throws {
        let tree = try GrantTree()
        #expect(tree.resource("/dangling") == nil)
    }

    @Test(arguments: ["", "/", "//", "/.", "/assets", "/empty", "/empty/", "/pipe", "/missing.html"])
    func nonRegularOrMissingTargetsAreRefused(_ uriPath: String) throws {
        let tree = try GrantTree()
        #expect(tree.resource(uriPath) == nil)
    }

    @Test(.enabled(if: getuid() != 0, "root reads mode 000 files"))
    func anUnreadableRegularFileIsRefused() throws {
        let tree = try GrantTree()
        try FileManager.default.setAttributes([.posixPermissions: 0], ofItemAtPath: "\(tree.root)/data.bin")
        #expect(tree.resource("/data.bin") == nil)
    }

    @Test func anEncodedNulNeverResolvesOutsideTheRoot() throws {
        let tree = try GrantTree()
        for path in ["/index.html%00.png", "/assets%00/../../outside/secret.txt", "/link-out%00"] {
            let served = try tree.served(path)
            #expect(served.map { $0.path.hasPrefix(tree.root + "/") } ?? true, "\(path) -> \(String(describing: served))")
        }
    }
}
