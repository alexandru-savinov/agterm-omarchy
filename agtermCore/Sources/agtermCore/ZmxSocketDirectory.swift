import Foundation

/// Where zmx keeps a pane's session socket, and the check that the directory belongs to this user
/// before anything is launched into it or asked of it.
///
/// zmx itself accepts any existing directory it is pointed at: it creates a missing one (mode 0750
/// before the umask) and binds each session socket without changing the socket's mode. The privacy
/// of the sessions therefore rests entirely on the directory, so agterm creates it as 0700 before
/// zmx ever runs and verifies it on every use: a real directory (not a symbolic link), owned by the
/// effective user, with no group or other permission bits.
public enum ZmxSocketDirectory {
    /// `sockaddr_un.sun_path` on Linux is 108 bytes including the terminating NUL.
    public static let linuxMaximumSocketPathBytes = 107
    /// The longest session name agterm hands zmx: `ZmxSupport.daemonName(for:)`, prefix + 32 hex digits.
    public static let longestDaemonNameBytes = ZmxSupport.namePrefix.utf8.count + 32
    /// The state-directory fallback's leaf; the state directory already names one instance.
    static let stateDirectoryLeaf = "zmx"

    /// Whether every socket agterm can create in `directory` fits `sun_path`. A path that does not fit is
    /// never shortened by zmx; the session would fail to start, so such a location is not chosen at all.
    public static func fitsSocketPath(_ directory: String) -> Bool {
        directory.utf8.count + 1 + longestDaemonNameBytes <= linuxMaximumSocketPathBytes
    }

    public struct Entry: Equatable, Sendable {
        public enum Kind: Equatable, Sendable { case directory, symbolicLink, socket, other }
        public let kind: Kind
        public let ownerID: UInt32
        /// Permission bits only (`mode & 0o7777`).
        public let permissions: UInt16

        public init(kind: Kind, ownerID: UInt32, permissions: UInt16) {
            self.kind = kind
            self.ownerID = ownerID
            self.permissions = permissions & 0o7777
        }
    }

    public enum Inspection: Equatable, Sendable {
        case missing
        case present(Entry)
        case unreadable(String)
    }

    public struct Refusal: Error, Equatable, Sendable {
        public enum Reason: Equatable, Sendable {
            case symbolicLink
            case notADirectory
            case foreignOwner
            case writableByOthers
            case couldNotRestrict
            case missing
            case unverifiable(String)
            case creationFailed(Int32)
        }

        public let path: String
        public let reason: Reason

        public init(path: String, reason: Reason) {
            self.path = path
            self.reason = reason
        }

        public var message: String {
            let what = switch reason {
            case .symbolicLink: "is a symbolic link"
            case .notADirectory: "is not a directory"
            case .foreignOwner: "is not owned by the current user"
            case .writableByOthers: "is writable by other users"
            case .couldNotRestrict: "could not be restricted to the current user"
            case .missing: "does not exist"
            case .unverifiable(let detail): "could not be verified (\(detail))"
            case .creationFailed(let code): "could not be created (errno \(code))"
            }
            return "the zmx socket directory \(path) \(what)"
        }
    }

    /// The filesystem as the check sees it; tests inject entries the test user cannot create (another owner).
    public struct FileSystem: Sendable {
        public var effectiveUserID: @Sendable () -> UInt32
        /// Describes `path` itself, never what a symbolic link at `path` points to.
        public var inspect: @Sendable (String) -> Inspection
        /// Creates exactly `path` with mode 0700; returns nil on success, else errno.
        public var makeDirectory: @Sendable (String) -> Int32?
        /// Sets `path` to mode 0700; true on success.
        public var restrict: @Sendable (String) -> Bool
        /// Names of the entries in `path`.
        public var entryNames: @Sendable (String) -> [String]

        public init(effectiveUserID: @escaping @Sendable () -> UInt32,
                    inspect: @escaping @Sendable (String) -> Inspection,
                    makeDirectory: @escaping @Sendable (String) -> Int32?,
                    restrict: @escaping @Sendable (String) -> Bool,
                    entryNames: @escaping @Sendable (String) -> [String]) {
            self.effectiveUserID = effectiveUserID
            self.inspect = inspect
            self.makeDirectory = makeDirectory
            self.restrict = restrict
            self.entryNames = entryNames
        }

        public static let live = FileSystem(
            effectiveUserID: { UInt32(geteuid()) },
            inspect: { FileSystem.liveInspect($0) },
            makeDirectory: { path in mkdir(path, 0o700) == 0 ? nil : errno },
            // chmod follows a final symbolic link, but `verify` only calls this on a path it has just
            // seen as a real directory this user owns, inside a parent other users cannot rename entries
            // in (the user's runtime or state directory, or sticky /tmp), and re-inspects afterwards.
            restrict: { path in chmod(path, 0o700) == 0 },
            entryNames: { path in (try? FileManager.default.contentsOfDirectory(atPath: path)) ?? [] }
        )

        private static func liveInspect(_ path: String) -> Inspection {
            guard !path.isEmpty else { return .unreadable("empty path") }
            let attributes: [FileAttributeKey: Any]
            do {
                // lstat semantics: a symbolic link reports itself, not its target
                attributes = try FileManager.default.attributesOfItem(atPath: path)
            } catch let error as CocoaError where error.code == .fileReadNoSuchFile || error.code == .fileNoSuchFile {
                return .missing
            } catch {
                return .unreadable(String(describing: error))
            }
            let kind: Entry.Kind = switch attributes[.type] as? FileAttributeType {
            case .typeDirectory?: .directory
            case .typeSymbolicLink?: .symbolicLink
            case .typeSocket?: .socket
            default: .other
            }
            guard let owner = (attributes[.ownerAccountID] as? NSNumber)?.uint32Value,
                  let mode = (attributes[.posixPermissions] as? NSNumber)?.uint16Value else {
                return .unreadable("owner or mode unavailable")
            }
            return .present(Entry(kind: kind, ownerID: owner, permissions: mode))
        }
    }

    public enum Verdict: Equatable, Sendable {
        /// A real directory this user owns with mode exactly 0700.
        case privateDirectory
        /// Owned by this user and writable by nobody else, but not exactly 0700 (zmx's own 0750, for one):
        /// nothing in it can have come from another user, so it is restricted to 0700 and checked again.
        case restrictable
        case refused(Refusal.Reason)
    }

    /// The ownership-and-mode predicate, free of the filesystem.
    public static func evaluate(_ entry: Entry, effectiveUserID: UInt32) -> Verdict {
        switch entry.kind {
        case .symbolicLink: return .refused(.symbolicLink)
        case .socket, .other: return .refused(.notADirectory)
        case .directory: break
        }
        guard entry.ownerID == effectiveUserID else { return .refused(.foreignOwner) }
        guard entry.permissions & 0o022 == 0 else { return .refused(.writableByOthers) }
        return entry.permissions == 0o700 ? .privateDirectory : .restrictable
    }

    /// Verifies `path` (creating it first as 0700 when `create` and it is absent) and returns it only
    /// when it is a private directory. Called before every zmx invocation and every pane launch.
    public static func verify(_ path: String, create: Bool = true,
                              fileSystem: FileSystem = .live) -> Result<String, Refusal> {
        func refuse(_ reason: Refusal.Reason) -> Result<String, Refusal> {
            .failure(Refusal(path: path, reason: reason))
        }
        guard path.hasPrefix("/") else { return refuse(.unverifiable("path is not absolute")) }
        var inspection = fileSystem.inspect(path)
        if inspection == .missing, create {
            // EEXIST: something appeared between the two calls; the inspection below decides about it
            if let code = fileSystem.makeDirectory(path), code != EEXIST { return refuse(.creationFailed(code)) }
            inspection = fileSystem.inspect(path)
        }
        let euid = fileSystem.effectiveUserID()
        switch inspection {
        case .missing: return refuse(.missing)
        case .unreadable(let detail): return refuse(.unverifiable(detail))
        case .present(let entry):
            switch evaluate(entry, effectiveUserID: euid) {
            case .privateDirectory: return .success(path)
            case .refused(let reason): return refuse(reason)
            case .restrictable:
                guard fileSystem.restrict(path),
                      case .present(let after) = fileSystem.inspect(path),
                      evaluate(after, effectiveUserID: euid) == .privateDirectory else {
                    return refuse(.couldNotRestrict)
                }
                return .success(path)
            }
        }
    }

    /// `XDG_RUNTIME_DIR` when it is set, absolute, a real directory owned by this user and mode 0700 or
    /// stricter — what systemd-logind provides, verified rather than assumed.
    static func privateRuntimeDirectory(environment: [String: String], fileSystem: FileSystem) -> String? {
        guard let raw = environment["XDG_RUNTIME_DIR"], raw.hasPrefix("/") else { return nil }
        let path = URL(fileURLWithPath: raw, isDirectory: true).standardizedFileURL.path
        guard case .present(let entry) = fileSystem.inspect(path), entry.kind == .directory,
              entry.ownerID == fileSystem.effectiveUserID(), entry.permissions & 0o077 == 0 else { return nil }
        return path
    }

    /// Whether `directory` holds at least one socket named like an agterm daemon.
    static func holdsDaemonSockets(_ directory: String, fileSystem: FileSystem) -> Bool {
        fileSystem.entryNames(directory).contains { name in
            guard ZmxSupport.isDaemonName(name),
                  case .present(let entry) = fileSystem.inspect(directory + "/" + name) else { return false }
            return entry.kind == .socket
        }
    }

    /// Chooses the Linux zmx socket directory for `stateDirectory`. Call once per process and keep the
    /// answer: panes and the zmx client must agree on it for the whole run.
    ///
    /// 1. Upgrade rule: the directory earlier versions used, `/tmp/agterm-zmx-<hash>`, stays in use while
    ///    it passes the same verification and still holds agterm daemon sockets. Those daemons were
    ///    started by an earlier version during this boot; moving away would leave them unreachable (not
    ///    killed, but orphaned until they exit). They cannot outlive the boot — /tmp and /run are both
    ///    cleared — so the next boot starts in the new location. A legacy directory that fails
    ///    verification is never used, even with sessions in it.
    /// 2. `$XDG_RUNTIME_DIR/agterm-zmx-<hash>` when the runtime directory itself is private.
    /// 3. `<state directory>/zmx` when the state directory exists.
    /// 4. `/tmp/agterm-zmx-<hash>`, still created as 0700 and verified.
    ///
    /// A location whose longest socket path would not fit `sun_path` is skipped, never truncated. The
    /// chosen location is verified (and created) here; when that fails the failure is returned rather
    /// than trying a later location, so a tampered directory surfaces instead of being silently avoided.
    public static func resolveLinux(stateDirectory: String, environment: [String: String],
                                    fileSystem: FileSystem = .live) -> Result<String, Refusal> {
        let leaf = ZmxSupport.socketDirectoryLeaf(forStateDirectory: stateDirectory)
        let legacy = ZmxSupport.socketDirectory(forStateDirectory: stateDirectory)
        if case .present(let entry) = fileSystem.inspect(legacy), entry.kind == .directory,
           holdsDaemonSockets(legacy, fileSystem: fileSystem),
           case .success = verify(legacy, create: false, fileSystem: fileSystem) {
            return .success(legacy)
        }
        if let runtime = privateRuntimeDirectory(environment: environment, fileSystem: fileSystem) {
            let candidate = runtime + "/" + leaf
            if fitsSocketPath(candidate) { return verify(candidate, fileSystem: fileSystem) }
        }
        let state = URL(fileURLWithPath: stateDirectory, isDirectory: true).standardizedFileURL.path
        if state.hasPrefix("/"), case .present(let entry) = fileSystem.inspect(state), entry.kind == .directory {
            let candidate = state + "/" + stateDirectoryLeaf
            if fitsSocketPath(candidate) { return verify(candidate, fileSystem: fileSystem) }
        }
        return verify(legacy, fileSystem: fileSystem)
    }
}
