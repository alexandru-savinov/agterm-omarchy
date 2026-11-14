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

    /// The parent chain of the suite's state directory as a typical home lays it out; the state
    /// directory candidate is taken only when every ancestor is safe.
    init() {
        put("/", .directory, mode: 0o755, owner: 0)
        put("/home", .directory, mode: 0o755, owner: 0)
        put("/home/u", .directory, mode: 0o750)
        put("/home/u/.local", .directory, mode: 0o700)
        put("/home/u/.local/share", .directory, mode: 0o755)
    }

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

    // MARK: state directory verification

    @Test("a state directory in a world-writable, non-sticky directory is skipped for the /tmp last resort")
    func stateInWorldWritableParentSkipped() {
        let disk = FakeDisk()
        disk.put("/scratch", .directory, mode: 0o777, owner: 0)
        disk.put("/scratch/agterm", .directory, mode: 0o700)
        let legacy = ZmxSupport.socketDirectory(forStateDirectory: "/scratch/agterm")
        let result = ZmxSocketDirectory.resolveLinux(stateDirectory: "/scratch/agterm", environment: [:], fileSystem: disk.fileSystem)
        #expect(result == .success(legacy))
        #expect(disk.entry("/scratch/agterm/zmx") == nil)
        #expect(disk.created == [legacy])
    }

    @Test("an unsafe state directory or ancestor is skipped",
          arguments: ["state-group-writable", "state-foreign", "state-symlink", "state-file", "ancestor-foreign",
                      "ancestor-group-writable", "ancestor-user-sticky", "ancestor-missing", "ancestor-symlink"])
    func unsafeStateSkipped(_ scenario: String) {
        let disk = FakeDisk()
        if scenario.hasPrefix("ancestor-") { disk.put(Self.state, .directory) }
        switch scenario {
        case "state-group-writable": disk.put(Self.state, .directory, mode: 0o770)
        case "state-foreign": disk.put(Self.state, .directory, mode: 0o700, owner: 4242)
        case "state-symlink": disk.put(Self.state, .symbolicLink, mode: 0o777)
        case "state-file": disk.put(Self.state, .other, mode: 0o600)
        case "ancestor-foreign": disk.put("/home/u", .directory, mode: 0o755, owner: 4242)
        case "ancestor-group-writable": disk.put("/home", .directory, mode: 0o775, owner: 0)
        // sticky protects entries only when the sticky directory's owner is trusted to keep it so: root
        case "ancestor-user-sticky": disk.put("/home", .directory, mode: 0o1777, owner: 4242)
        case "ancestor-symlink": disk.put("/home/u/.local", .symbolicLink, mode: 0o777)
        default: break  // ancestor-missing: /home vanishes below
        }
        let fileSystem: ZmxSocketDirectory.FileSystem
        if scenario == "ancestor-missing" {
            var base = disk.fileSystem
            let inner = base.inspect
            base.inspect = { $0 == "/home" ? .missing : inner($0) }
            fileSystem = base
        } else {
            fileSystem = disk.fileSystem
        }
        #expect(ZmxSocketDirectory.safeStateDirectory(Self.state, fileSystem: fileSystem) == nil)
        #expect(ZmxSocketDirectory.resolveLinux(stateDirectory: Self.state, environment: [:], fileSystem: fileSystem)
            == .success(Self.legacy))
    }

    @Test("a safe state directory is taken, including below root-owned sticky /tmp, and symbolic links are resolved first")
    func safeStateTaken() {
        let disk = FakeDisk()
        disk.put(Self.state, .directory, mode: 0o755)
        #expect(ZmxSocketDirectory.safeStateDirectory(Self.state, fileSystem: disk.fileSystem) == Self.state)
        #expect(disk.restricted.isEmpty)

        disk.put("/tmp", .directory, mode: 0o1777, owner: 0)
        disk.put("/tmp/state", .directory, mode: 0o700)
        #expect(ZmxSocketDirectory.safeStateDirectory("/tmp/state", fileSystem: disk.fileSystem) == "/tmp/state")

        var linked = disk.fileSystem
        linked.realPath = { $0 == "/link/state" ? Self.state : nil }
        #expect(ZmxSocketDirectory.safeStateDirectory("/link/state", fileSystem: linked) == Self.state)
        #expect(ZmxSocketDirectory.resolveLinux(stateDirectory: "/link/state", environment: [:], fileSystem: linked)
            == .success(Self.state + "/zmx"))
        var unresolvable = disk.fileSystem
        unresolvable.realPath = { _ in nil }
        #expect(ZmxSocketDirectory.safeStateDirectory(Self.state, fileSystem: unresolvable) == nil)
    }

    @Test("on disk: a state directory inside a world-writable non-sticky scratch directory is not trusted")
    func liveWorldWritableParent() throws {
        let root = try scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.setAttributes([.posixPermissions: 0o777], ofItemAtPath: root.path)
        let state = root.appending(path: "state").path
        try FileManager.default.createDirectory(atPath: state, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        #expect(ZmxSocketDirectory.safeStateDirectory(state) == nil)
        #expect(!FileManager.default.fileExists(atPath: state + "/zmx"))
    }

    // MARK: legacy socket ownership

    @Test("the old directory is not reused when a daemon-named entry in it is not this user's socket",
          arguments: ["foreign-socket", "foreign-only", "symlink-named-like-daemon"])
    func legacyRefusedForForeignSocket(_ scenario: String) {
        let disk = FakeDisk()
        disk.put("/run/user/1000", .directory, mode: 0o700)
        disk.put(Self.state, .directory)
        disk.put(Self.legacy, .directory, mode: 0o700)
        let other = ZmxSupport.daemonName(for: UUID())
        switch scenario {
        case "foreign-socket":
            disk.put(Self.legacy + "/" + Self.daemon, .socket, mode: 0o755)
            disk.put(Self.legacy + "/" + other, .socket, mode: 0o755, owner: 4242)
        case "foreign-only":
            disk.put(Self.legacy + "/" + other, .socket, mode: 0o755, owner: 4242)
        default:
            disk.put(Self.legacy + "/" + Self.daemon, .socket, mode: 0o755)
            disk.put(Self.legacy + "/" + other, .symbolicLink, mode: 0o777)
        }
        let result = ZmxSocketDirectory.resolveLinux(stateDirectory: Self.state, environment: ["XDG_RUNTIME_DIR": "/run/user/1000"],
                                                     fileSystem: disk.fileSystem)
        #expect(result == .success("/run/user/1000/" + Self.leaf))
        #expect(disk.restricted.isEmpty)
    }

    @Test("entries that are not daemon names do not block reuse of the old directory")
    func legacyIgnoresOtherNames() {
        let disk = FakeDisk()
        disk.put(Self.state, .directory)
        disk.put(Self.legacy, .directory, mode: 0o700)
        disk.put(Self.legacy + "/" + Self.daemon, .socket, mode: 0o755)
        disk.put(Self.legacy + "/session-host", .directory, mode: 0o700)
        disk.put(Self.legacy + "/agterm-notes", .socket, mode: 0o755, owner: 4242)
        #expect(ZmxSocketDirectory.resolveLinux(stateDirectory: Self.state, environment: [:], fileSystem: disk.fileSystem)
            == .success(Self.legacy))
    }

    // MARK: macOS

    /// A typical per-user temporary directory: a 30-character second segment, as macOS issues them.
    private static let userTemporary = "/var/folders/zz/" + String(repeating: "a", count: 24) + "0000gn/T/"
    private static var userTemporaryStandardized: String { String(userTemporary.dropLast()) }
    private static let darwinLeaf = ZmxSocketDirectory.darwinUserTemporaryLeaf(forStateDirectory: state)

    @Test("macOS uses its private per-user temporary directory with the short leaf")
    func darwinUserTemporaryChosen() {
        // the fixture must itself fit sun_path, or the choice below is skipped for length, not for safety
        #expect(Self.userTemporaryStandardized.utf8.count == 48)
        #expect(ZmxSocketDirectory.fitsSocketPath(Self.userTemporaryStandardized + "/" + Self.darwinLeaf,
                                                  maximumBytes: ZmxSocketDirectory.darwinMaximumSocketPathBytes))
        #expect(Self.darwinLeaf.utf8.count == 12)
        #expect(Self.darwinLeaf.hasPrefix("zmx-"))
        #expect(Self.leaf.hasPrefix("agterm-zmx-" + String(Self.darwinLeaf.dropFirst(4))))
        #expect(ZmxSocketDirectory.darwinUserTemporaryLeaf(forStateDirectory: Self.state) == Self.darwinLeaf)
        #expect(ZmxSocketDirectory.darwinUserTemporaryLeaf(forStateDirectory: "/other") != Self.darwinLeaf)
        let disk = FakeDisk()
        disk.put(Self.userTemporaryStandardized, .directory, mode: 0o700)
        disk.put(Self.state, .directory)
        let result = ZmxSocketDirectory.resolveDarwin(stateDirectory: Self.state, userTemporaryDirectory: Self.userTemporary,
                                                      fileSystem: disk.fileSystem)
        let expected = Self.userTemporaryStandardized + "/" + Self.darwinLeaf
        #expect(result == .success(expected))
        #expect(disk.created == [expected])
        #expect(disk.entry(expected)?.permissions == 0o700)
    }

    @Test("macOS falls back to the verified state directory when the per-user temporary directory is unusable",
          arguments: [nil, "", "var/folders/x/T", "0755", "foreign", "link", "too-long"])
    func darwinUserTemporaryFallback(_ spec: String?) {
        let disk = FakeDisk()
        disk.put(Self.state, .directory)
        var temporary: String? = spec
        if let spec, ["0755", "foreign", "link"].contains(spec) { temporary = Self.userTemporary }
        switch spec {
        case "0755": disk.put(Self.userTemporaryStandardized, .directory, mode: 0o755)
        case "foreign": disk.put(Self.userTemporaryStandardized, .directory, mode: 0o700, owner: 4242)
        case "link": disk.put(Self.userTemporaryStandardized, .symbolicLink, mode: 0o777)
        case "too-long":
            let long = "/" + String(repeating: "t", count: 51)  // 52 + 1 + 12 + 1 + 39 = 105 > 103
            temporary = long
            disk.put(long, .directory, mode: 0o700)
        default: break
        }
        let result = ZmxSocketDirectory.resolveDarwin(stateDirectory: Self.state, userTemporaryDirectory: temporary,
                                                      fileSystem: disk.fileSystem)
        #expect(result == .success(Self.state + "/zmx"))
    }

    @Test("macOS keeps the old /tmp directory while its daemon sockets are owned by this user, and otherwise never trusts it unverified")
    func darwinLegacyRules() {
        let disk = FakeDisk()
        disk.put(Self.userTemporaryStandardized, .directory, mode: 0o700)
        disk.put(Self.legacy, .directory, mode: 0o750)
        disk.put(Self.legacy + "/" + Self.daemon, .socket, mode: 0o755)
        #expect(ZmxSocketDirectory.resolveDarwin(stateDirectory: Self.state, userTemporaryDirectory: Self.userTemporary,
                                                 fileSystem: disk.fileSystem) == .success(Self.legacy))

        let foreign = FakeDisk()
        foreign.put(Self.userTemporaryStandardized, .directory, mode: 0o700)
        foreign.put(Self.legacy, .directory, mode: 0o700)
        foreign.put(Self.legacy + "/" + Self.daemon, .socket, mode: 0o755, owner: 4242)
        #expect(ZmxSocketDirectory.resolveDarwin(stateDirectory: Self.state, userTemporaryDirectory: Self.userTemporary,
                                                 fileSystem: foreign.fileSystem)
            == .success(Self.userTemporaryStandardized + "/" + Self.darwinLeaf))

        let planted = FakeDisk()
        planted.put(Self.legacy, .directory, mode: 0o700, owner: 4242)
        #expect(ZmxSocketDirectory.resolveDarwin(stateDirectory: Self.state, userTemporaryDirectory: nil,
                                                 fileSystem: planted.fileSystem)
            == .failure(.init(path: Self.legacy, reason: .foreignOwner)))
    }

    // MARK: sun_path, macOS

    @Test("the longest macOS socket path fits sun_path in the worst-case per-user temporary directory, only with the short leaf")
    func darwinSocketPathWorstCase() {
        #expect(ZmxSocketDirectory.darwinMaximumSocketPathBytes == 103)
        let worstTemporary = "/var/folders/xx/" + String(repeating: "y", count: 32) + "/T"
        #expect(worstTemporary.utf8.count == 50)
        let short = worstTemporary + "/" + Self.darwinLeaf
        #expect((short + "/" + Self.daemon).utf8.count == 103)
        #expect(ZmxSocketDirectory.fitsSocketPath(short, maximumBytes: ZmxSocketDirectory.darwinMaximumSocketPathBytes))
        #expect(!ZmxSocketDirectory.fitsSocketPath(short + "x", maximumBytes: ZmxSocketDirectory.darwinMaximumSocketPathBytes))
        // the Linux leaf would not fit there: this is why macOS shortens it
        #expect(!ZmxSocketDirectory.fitsSocketPath(worstTemporary + "/" + Self.leaf,
                                                   maximumBytes: ZmxSocketDirectory.darwinMaximumSocketPathBytes))
        // the old /tmp name and the session-host socket fit on macOS too
        #expect(ZmxSocketDirectory.fitsSocketPath(Self.legacy, maximumBytes: ZmxSocketDirectory.darwinMaximumSocketPathBytes))
        #expect((try? SessionHost.paths(socketDirectory: short)) != nil)
        // the same directory checked against Linux's larger limit still fits, so the default is Linux's
        #expect(ZmxSocketDirectory.fitsSocketPath(short + "xxxx"))

        let disk = FakeDisk()
        disk.put(worstTemporary, .directory, mode: 0o700)
        #expect(ZmxSocketDirectory.resolveDarwin(stateDirectory: Self.state, userTemporaryDirectory: worstTemporary,
                                                 fileSystem: disk.fileSystem) == .success(short))
    }
}
