import Foundation
import Testing
@testable import agtermCore

/// An in-memory filesystem for the cases a test user cannot create on disk (another owner) and for
/// whole-resolution scenarios.
private final class FakeDisk: @unchecked Sendable {
    let uid: UInt32 = 1000
    private var entries: [String: ZmxSocketDirectory.Entry] = [:]
    private(set) var restricted: [String] = []
    private(set) var created: [String] = []

    func put(_ path: String, _ kind: ZmxSocketDirectory.Entry.Kind, mode: UInt16 = 0o700, owner: UInt32? = nil) {
        entries[path] = .init(kind: kind, ownerID: owner ?? uid, permissions: mode)
    }

    func entry(_ path: String) -> ZmxSocketDirectory.Entry? { entries[path] }

    var fileSystem: ZmxSocketDirectory.FileSystem {
        ZmxSocketDirectory.FileSystem(
            effectiveUserID: { [uid] in uid },
            inspect: { [self] path in entries[path].map { ZmxSocketDirectory.Inspection.present($0) } ?? .missing },
            makeDirectory: { [self] path in
                created.append(path)
                entries[path] = .init(kind: .directory, ownerID: uid, permissions: 0o700)
                return nil
            },
            restrict: { [self] path in
                restricted.append(path)
                guard let old = entries[path] else { return false }
                entries[path] = .init(kind: old.kind, ownerID: old.ownerID, permissions: 0o700)
                return true
            },
            entryNames: { [self] path in
                entries.keys.filter { ($0 as NSString).deletingLastPathComponent == path }
                    .map { ($0 as NSString).lastPathComponent }
            }
        )
    }
}

@Suite("zmx socket directory")
struct ZmxSocketDirectoryTests {
    private static let state = "/home/u/.local/share/agterm"
    private static let leaf = ZmxSupport.socketDirectoryLeaf(forStateDirectory: state)
    private static let legacy = ZmxSupport.socketDirectory(forStateDirectory: state)
    private static let daemon = ZmxSupport.daemonName(for: UUID())

    // MARK: predicate

    @Test("only a real 0700 directory owned by the effective user is private")
    func predicate() {
        typealias Item = ZmxSocketDirectory.Entry
        let me: UInt32 = 501
        #expect(ZmxSocketDirectory.evaluate(Item(kind: .directory, ownerID: me, permissions: 0o700), effectiveUserID: me) == .privateDirectory)
        #expect(ZmxSocketDirectory.evaluate(Item(kind: .directory, ownerID: me + 1, permissions: 0o700), effectiveUserID: me) == .refused(.foreignOwner))
        #expect(ZmxSocketDirectory.evaluate(Item(kind: .directory, ownerID: 0, permissions: 0o700), effectiveUserID: me) == .refused(.foreignOwner))
        #expect(ZmxSocketDirectory.evaluate(Item(kind: .symbolicLink, ownerID: me, permissions: 0o777), effectiveUserID: me) == .refused(.symbolicLink))
        #expect(ZmxSocketDirectory.evaluate(Item(kind: .other, ownerID: me, permissions: 0o600), effectiveUserID: me) == .refused(.notADirectory))
        #expect(ZmxSocketDirectory.evaluate(Item(kind: .socket, ownerID: me, permissions: 0o700), effectiveUserID: me) == .refused(.notADirectory))
        for mode: UInt16 in [0o777, 0o770, 0o702, 0o720, 0o1777] {
            #expect(ZmxSocketDirectory.evaluate(Item(kind: .directory, ownerID: me, permissions: mode), effectiveUserID: me) == .refused(.writableByOthers))
        }
        // zmx's own 0750 (and a umask-022 0755): nobody else could have written into it
        for mode: UInt16 in [0o750, 0o755, 0o711, 0o500] {
            #expect(ZmxSocketDirectory.evaluate(Item(kind: .directory, ownerID: me, permissions: mode), effectiveUserID: me) == .restrictable)
        }
    }

    @Test("a foreign-owned directory is refused and never restricted")
    func foreignOwnerRefused() {
        let disk = FakeDisk()
        disk.put("/tmp/x", .directory, mode: 0o700, owner: 4242)
        let result = ZmxSocketDirectory.verify("/tmp/x", fileSystem: disk.fileSystem)
        #expect(result == .failure(.init(path: "/tmp/x", reason: .foreignOwner)))
        #expect(disk.restricted.isEmpty)
        #expect(disk.created.isEmpty)
    }

    // MARK: real filesystem

    private func scratch() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appending(path: "zmx-dir-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: false)
        return url
    }

    private func mode(_ path: String) throws -> Int {
        try #require(FileManager.default.attributesOfItem(atPath: path)[.posixPermissions] as? NSNumber).intValue & 0o7777
    }

    @Test("a missing directory is created as 0700")
    func createsPrivate() throws {
        let root = try scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let path = root.appending(path: "zmx").path
        #expect(ZmxSocketDirectory.verify(path) == .success(path))
        #expect(try mode(path) == 0o700)
    }

    @Test("a pre-created group- or world-writable directory is refused and left as found", arguments: [0o777, 0o770, 0o703])
    func writableRefused(_ bits: Int) throws {
        let root = try scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let path = root.appending(path: "zmx").path
        try FileManager.default.createDirectory(atPath: path, withIntermediateDirectories: false)
        try FileManager.default.setAttributes([.posixPermissions: bits], ofItemAtPath: path)
        #expect(ZmxSocketDirectory.verify(path) == .failure(.init(path: path, reason: .writableByOthers)))
        #expect(try mode(path) == bits)
    }

    @Test("a pre-created readable-only directory of ours is restricted to 0700", arguments: [0o750, 0o755])
    func readableRestricted(_ bits: Int) throws {
        let root = try scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let path = root.appending(path: "zmx").path
        try FileManager.default.createDirectory(atPath: path, withIntermediateDirectories: false)
        try FileManager.default.setAttributes([.posixPermissions: bits], ofItemAtPath: path)
        #expect(ZmxSocketDirectory.verify(path) == .success(path))
        #expect(try mode(path) == 0o700)
    }

    @Test("a symbolic link is refused even when it points at a private directory of ours")
    func symlinkRefused() throws {
        let root = try scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let target = root.appending(path: "target").path
        try FileManager.default.createDirectory(atPath: target, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let link = root.appending(path: "zmx").path
        try FileManager.default.createSymbolicLink(atPath: link, withDestinationPath: target)
        #expect(ZmxSocketDirectory.verify(link) == .failure(.init(path: link, reason: .symbolicLink)))
        let dangling = root.appending(path: "dangling").path
        try FileManager.default.createSymbolicLink(atPath: dangling, withDestinationPath: root.appending(path: "nowhere").path)
        #expect(ZmxSocketDirectory.verify(dangling) == .failure(.init(path: dangling, reason: .symbolicLink)))
        #expect(!FileManager.default.fileExists(atPath: root.appending(path: "nowhere").path))
    }

    @Test("a regular file and a relative path are refused")
    func fileAndRelativeRefused() throws {
        let root = try scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appending(path: "zmx").path
        try Data().write(to: URL(fileURLWithPath: file))
        #expect(ZmxSocketDirectory.verify(file) == .failure(.init(path: file, reason: .notADirectory)))
        #expect((try? ZmxSocketDirectory.verify("relative/zmx").get()) == nil)
        #expect((try? ZmxSocketDirectory.verify("").get()) == nil)
    }

    // MARK: location

    @Test("a private XDG_RUNTIME_DIR is used")
    func runtimeDirectoryChosen() {
        let disk = FakeDisk()
        disk.put("/run/user/1000", .directory, mode: 0o700)
        disk.put(Self.state, .directory)
        let result = ZmxSocketDirectory.resolveLinux(stateDirectory: Self.state, environment: ["XDG_RUNTIME_DIR": "/run/user/1000/"],
                                                     fileSystem: disk.fileSystem)
        #expect(result == .success("/run/user/1000/" + Self.leaf))
        #expect(disk.entry("/run/user/1000/" + Self.leaf)?.permissions == 0o700)
    }

    @Test("an unusable XDG_RUNTIME_DIR falls back to the state directory",
          arguments: [nil, "", "run/user/1000", "/run/user/1000|0755", "/run/user/1000|0700|0", "/run/user/1000|link"])
    func runtimeDirectoryFallback(_ spec: String?) {
        let disk = FakeDisk()
        disk.put(Self.state, .directory)
        var environment: [String: String] = [:]
        if let spec {
            let parts = spec.split(separator: "|").map(String.init)
            environment["XDG_RUNTIME_DIR"] = parts.first ?? ""
            if parts.count > 1, let path = parts.first {
                if parts[1] == "link" {
                    disk.put(path, .symbolicLink, mode: 0o777)
                } else {
                    disk.put(path, .directory, mode: UInt16(parts[1], radix: 8) ?? 0, owner: parts.count > 2 ? UInt32(parts[2]) : nil)
                }
            }
        }
        let result = ZmxSocketDirectory.resolveLinux(stateDirectory: Self.state, environment: environment, fileSystem: disk.fileSystem)
        #expect(result == .success(Self.state + "/zmx"))
    }

    @Test("without a state directory the /tmp name is the last resort, still verified")
    func tmpLastResort() {
        let disk = FakeDisk()
        #expect(ZmxSocketDirectory.resolveLinux(stateDirectory: Self.state, environment: [:], fileSystem: disk.fileSystem)
            == .success(Self.legacy))
        #expect(disk.entry(Self.legacy)?.permissions == 0o700)

        let planted = FakeDisk()
        planted.put(Self.legacy, .directory, mode: 0o700, owner: 4242)
        #expect(ZmxSocketDirectory.resolveLinux(stateDirectory: Self.state, environment: [:], fileSystem: planted.fileSystem)
            == .failure(.init(path: Self.legacy, reason: .foreignOwner)))
    }

    @Test("a refused chosen location is reported, not silently replaced by a later one")
    func chosenLocationRefusalSurfaces() {
        let disk = FakeDisk()
        disk.put("/run/user/1000", .directory, mode: 0o700)
        disk.put("/run/user/1000/" + Self.leaf, .symbolicLink, mode: 0o777)
        disk.put(Self.state, .directory)
        let result = ZmxSocketDirectory.resolveLinux(stateDirectory: Self.state, environment: ["XDG_RUNTIME_DIR": "/run/user/1000"],
                                                     fileSystem: disk.fileSystem)
        #expect(result == .failure(.init(path: "/run/user/1000/" + Self.leaf, reason: .symbolicLink)))
    }

    // MARK: sun_path

    @Test("the longest socket path fits sun_path in the systemd runtime directory")
    func socketPathFits() {
        #expect(ZmxSocketDirectory.longestDaemonNameBytes == 39)
        #expect(Self.daemon.utf8.count == ZmxSocketDirectory.longestDaemonNameBytes)
        let worst = "/run/user/\(UInt32.max)/" + Self.leaf
        let socket = worst + "/" + Self.daemon
        #expect(socket.utf8.count == 88)
        #expect(socket.utf8.count <= ZmxSocketDirectory.linuxMaximumSocketPathBytes)
        #expect(ZmxSocketDirectory.fitsSocketPath(worst))
        #expect(ZmxSocketDirectory.fitsSocketPath(Self.legacy))
        // the exact edge: 107 usable bytes
        let edge = "/" + String(repeating: "d", count: 107 - 1 - 39 - 1)
        #expect(ZmxSocketDirectory.fitsSocketPath(edge))
        #expect(!ZmxSocketDirectory.fitsSocketPath(edge + "d"))
    }

    @Test("a runtime or state directory too long for sun_path is skipped, never truncated")
    func longLocationsSkipped() {
        let disk = FakeDisk()
        let longRuntime = "/" + String(repeating: "r", count: 60)
        let longState = "/" + String(repeating: "s", count: 70)
        disk.put(longRuntime, .directory, mode: 0o700)
        disk.put(longState, .directory)
        let legacy = ZmxSupport.socketDirectory(forStateDirectory: longState)
        let result = ZmxSocketDirectory.resolveLinux(stateDirectory: longState, environment: ["XDG_RUNTIME_DIR": longRuntime],
                                                     fileSystem: disk.fileSystem)
        #expect(result == .success(legacy))
        #expect(disk.created == [legacy])
    }

    // MARK: upgrade rule

    @Test("the old /tmp directory stays in use while it is ours and holds agterm daemon sockets")
    func legacyKeptForLiveDaemons() {
        let disk = FakeDisk()
        disk.put("/run/user/1000", .directory, mode: 0o700)
        disk.put(Self.state, .directory)
        disk.put(Self.legacy, .directory, mode: 0o750)  // as zmx created it
        disk.put(Self.legacy + "/" + Self.daemon, .socket, mode: 0o755)
        let result = ZmxSocketDirectory.resolveLinux(stateDirectory: Self.state, environment: ["XDG_RUNTIME_DIR": "/run/user/1000"],
                                                     fileSystem: disk.fileSystem)
        #expect(result == .success(Self.legacy))
        #expect(disk.restricted == [Self.legacy])
        #expect(disk.entry(Self.legacy)?.permissions == 0o700)
        #expect(disk.created.isEmpty)
    }

    @Test("the old directory is left behind when it is empty, holds no agterm sockets, or fails verification",
          arguments: ["empty", "foreign-name", "not-socket", "foreign-owner", "writable", "symlink"])
    func legacyNotKept(_ scenario: String) {
        let disk = FakeDisk()
        disk.put("/run/user/1000", .directory, mode: 0o700)
        disk.put(Self.state, .directory)
        switch scenario {
        case "symlink": disk.put(Self.legacy, .symbolicLink, mode: 0o777)
        case "foreign-owner": disk.put(Self.legacy, .directory, mode: 0o700, owner: 4242)
        case "writable": disk.put(Self.legacy, .directory, mode: 0o777)
        default: disk.put(Self.legacy, .directory, mode: 0o750)
        }
        switch scenario {
        case "empty": break
        case "foreign-name": disk.put(Self.legacy + "/agterm-notes", .socket)
        case "not-socket": disk.put(Self.legacy + "/" + Self.daemon, .other)
        default: disk.put(Self.legacy + "/" + Self.daemon, .socket)
        }
        let result = ZmxSocketDirectory.resolveLinux(stateDirectory: Self.state, environment: ["XDG_RUNTIME_DIR": "/run/user/1000"],
                                                     fileSystem: disk.fileSystem)
        #expect(result == .success("/run/user/1000/" + Self.leaf))
        #expect(disk.restricted.isEmpty)
    }

    // MARK: launch plumbing

    @Test("a refused directory rejects the Live configuration with the reason")
    func configurationCarriesRefusal() throws {
        let resources = FileManager.default.temporaryDirectory.appending(path: "zmx-dir-resources-\(UUID().uuidString)")
        let integration = resources.appending(path: "shell-integration/zsh")
        try FileManager.default.createDirectory(at: integration, withIntermediateDirectories: true)
        try Data().write(to: integration.appending(path: ".zshenv"))
        defer { try? FileManager.default.removeItem(at: resources) }
        func inputs(_ directory: @escaping @Sendable () -> Result<String, ZmxSocketDirectory.Refusal>) -> ZmxSupport.Inputs {
            ZmxSupport.Inputs(zmxExecutablePath: "/bin/echo", passwordDatabaseShell: "/bin/zsh", resourcesDirectory: resources.path,
                              stateDirectory: "/tmp/state", paneIdentity: UUID(), baseEnvironment: [:], inheritedZdotdir: nil,
                              socketDirectory: directory)
        }
        let refusal = ZmxSocketDirectory.Refusal(path: "/run/user/1/agterm-zmx-0", reason: .symbolicLink)
        let refused = ZmxSupport.configuration(for: inputs { .failure(refusal) })
        #expect(refused == .failure(.socketDirectoryRefused(refusal)))
        #expect(ZmxSupport.Rejection.socketDirectoryRefused(refusal).message
            == "the zmx socket directory /run/user/1/agterm-zmx-0 is a symbolic link")

        guard case .success(let configuration) = ZmxSupport.configuration(for: inputs { .success("/run/user/1/agterm-zmx-0") }) else {
            Issue.record("expected a configuration")
            return
        }
        #expect(configuration.socketDirectory == "/run/user/1/agterm-zmx-0")
        #expect(configuration.environment["ZMX_DIR"] == "/run/user/1/agterm-zmx-0")
    }
}
