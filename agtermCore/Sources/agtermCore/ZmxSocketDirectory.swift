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
    /// `sockaddr_un.sun_path` on macOS is 104 bytes including the terminating NUL.
    public static let darwinMaximumSocketPathBytes = 103
    /// The longest session name agterm hands zmx: `ZmxSupport.daemonName(for:)`, prefix + 32 hex digits.
    public static let longestDaemonNameBytes = ZmxSupport.namePrefix.utf8.count + 32
    /// The state-directory fallback's leaf; the state directory already names one instance.
    static let stateDirectoryLeaf = "zmx"

    /// Whether every socket agterm can create in `directory` fits `sun_path`. A path that does not fit is
    /// never shortened by zmx; the session would fail to start, so such a location is not chosen at all.
    public static func fitsSocketPath(_ directory: String, maximumBytes: Int = linuxMaximumSocketPathBytes) -> Bool {
        directory.utf8.count + 1 + longestDaemonNameBytes <= maximumBytes
    }

    /// The leaf inside the macOS per-user temporary directory. That directory is already long
    /// (`/var/folders/xx/<up to 32 characters>/T`, 50 bytes at worst), and the usual
    /// `agterm-zmx-<16 hex>` leaf would push the longest daemon socket past macOS's 103 usable bytes,
    /// so it is shortened deterministically to the first 8 hex digits of the same hash. The directory is
    /// private to one user, so the shorter name only has to tell that user's state directories apart.
    public static func darwinUserTemporaryLeaf(forStateDirectory stateDirectory: String) -> String {
        let leaf = ZmxSupport.socketDirectoryLeaf(forStateDirectory: stateDirectory)
        return "zmx-" + String(leaf.dropFirst("agterm-zmx-".count).prefix(8))
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
        /// `path` with every symbolic link resolved; nil when it cannot be resolved (missing, for one).
        public var realPath: @Sendable (String) -> String?

        public init(effectiveUserID: @escaping @Sendable () -> UInt32,
                    inspect: @escaping @Sendable (String) -> Inspection,
                    makeDirectory: @escaping @Sendable (String) -> Int32?,
                    restrict: @escaping @Sendable (String) -> Bool,
                    entryNames: @escaping @Sendable (String) -> [String],
                    realPath: @escaping @Sendable (String) -> String? = { $0 }) {
            self.effectiveUserID = effectiveUserID
            self.inspect = inspect
            self.makeDirectory = makeDirectory
            self.restrict = restrict
            self.entryNames = entryNames
            self.realPath = realPath
        }

        public static let live = FileSystem(
            effectiveUserID: { UInt32(geteuid()) },
            inspect: { FileSystem.liveInspect($0) },
            makeDirectory: { path in mkdir(path, 0o700) == 0 ? nil : errno },
            // chmod follows a final symbolic link, but `verify` only calls this on a path it has just
            // seen as a real directory this user owns, inside a parent other users cannot rename entries
            // in (the user's runtime or temporary directory, a state directory whose whole parent chain
            // was verified, or sticky /tmp), and re-inspects afterwards.
            restrict: { path in chmod(path, 0o700) == 0 },
            entryNames: { path in (try? FileManager.default.contentsOfDirectory(atPath: path)) ?? [] },
            realPath: { path in
                guard let resolved = realpath(path, nil) else { return nil }
                defer { free(resolved) }
                return String(cString: resolved)
            }
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

    /// A directory only this user can write to: set, absolute, a real directory owned by this user with
    /// mode 0700 or stricter. Used for the per-user locations the system provides (`XDG_RUNTIME_DIR`,
    /// macOS's per-user temporary directory), verified rather than assumed.
    static func privateBaseDirectory(_ raw: String?, fileSystem: FileSystem) -> String? {
        guard let raw, raw.hasPrefix("/") else { return nil }
        let path = URL(fileURLWithPath: raw, isDirectory: true).standardizedFileURL.path
        guard case .present(let entry) = fileSystem.inspect(path), entry.kind == .directory,
              entry.ownerID == fileSystem.effectiveUserID(), entry.permissions & 0o077 == 0 else { return nil }
        return path
    }

    /// `XDG_RUNTIME_DIR` when it is a private directory of this user — what systemd-logind provides.
    static func privateRuntimeDirectory(environment: [String: String], fileSystem: FileSystem) -> String? {
        privateBaseDirectory(environment["XDG_RUNTIME_DIR"], fileSystem: fileSystem)
    }

    /// Whether an ancestor of the state directory keeps other users from renaming or replacing what is
    /// below it: a real directory owned by this user or by root and writable by neither group nor
    /// others, or a root-owned sticky directory such as /tmp, where nobody else can rename an entry
    /// they do not own.
    static func isSafeAncestor(_ entry: Entry, effectiveUserID: UInt32) -> Bool {
        guard entry.kind == .directory, entry.ownerID == effectiveUserID || entry.ownerID == 0 else { return false }
        if entry.permissions & 0o022 == 0 { return true }
        return entry.ownerID == 0 && entry.permissions & 0o1000 != 0
    }

    /// The state directory, with symbolic links resolved, when it may hold the socket directory: itself a
    /// real directory owned by this user and writable by nobody else (the predicate `evaluate` applies,
    /// without requiring exactly 0700 — it is never restricted), and every directory above it up to `/`
    /// a safe ancestor. Nil when any of that fails, and the state directory is then not a candidate.
    static func safeStateDirectory(_ stateDirectory: String, fileSystem: FileSystem = .live) -> String? {
        let standardized = URL(fileURLWithPath: stateDirectory, isDirectory: true).standardizedFileURL.path
        guard standardized.hasPrefix("/"), let real = fileSystem.realPath(standardized), real.hasPrefix("/"),
              case .present(let entry) = fileSystem.inspect(real) else { return nil }
        let euid = fileSystem.effectiveUserID()
        if case .refused = evaluate(entry, effectiveUserID: euid) { return nil }
        var ancestor = real
        while ancestor != "/" {
            ancestor = (ancestor as NSString).deletingLastPathComponent
            if ancestor.isEmpty { return nil }
            guard case .present(let above) = fileSystem.inspect(ancestor),
                  isSafeAncestor(above, effectiveUserID: euid) else { return nil }
        }
        return real
    }

    /// Whether `directory` may be kept for the daemons an earlier version started there: it holds at
    /// least one socket named like an agterm daemon, and every entry named like one is a socket owned
    /// by this user. One foreign-owned or non-socket entry under a daemon name and the directory is not
    /// reused — its sessions cannot be told apart from something planted.
    static func holdsOnlyOwnDaemonSockets(_ directory: String, fileSystem: FileSystem) -> Bool {
        let euid = fileSystem.effectiveUserID()
        let daemons = fileSystem.entryNames(directory).filter(ZmxSupport.isDaemonName)
        guard !daemons.isEmpty else { return false }
        return daemons.allSatisfy { name in
            guard case .present(let entry) = fileSystem.inspect(directory + "/" + name) else { return false }
            return entry.kind == .socket && entry.ownerID == euid
        }
    }

    /// Chooses the Linux zmx socket directory for `stateDirectory`. Call once per process and keep the
    /// answer: panes and the zmx client must agree on it for the whole run.
    ///
    /// 1. Upgrade rule: the directory earlier versions used, `/tmp/agterm-zmx-<hash>`, stays in use while
    ///    it passes the same verification and still holds agterm daemon sockets, all owned by this user.
    ///    Those daemons were started by an earlier version during this boot; moving away would leave
    ///    them unreachable (not killed, but orphaned until they exit). They cannot outlive the boot —
    ///    /tmp and /run are both cleared — so the next boot starts in the new location. A legacy
    ///    directory that fails verification, or holds a daemon-named entry that is not this user's
    ///    socket, is never used, even with sessions in it.
    /// 2. `$XDG_RUNTIME_DIR/agterm-zmx-<hash>` when the runtime directory itself is private.
    /// 3. `<state directory>/zmx` when the state directory and its whole parent chain pass
    ///    `safeStateDirectory`; otherwise the state directory is skipped.
    /// 4. `/tmp/agterm-zmx-<hash>`, still created as 0700 and verified.
    ///
    /// A location whose longest socket path would not fit `sun_path` is skipped, never truncated. The
    /// chosen location is verified (and created) here; when that fails the failure is returned rather
    /// than trying a later location, so a tampered directory surfaces instead of being silently avoided.
    public static func resolveLinux(stateDirectory: String, environment: [String: String],
                                    fileSystem: FileSystem = .live) -> Result<String, Refusal> {
        let base = privateRuntimeDirectory(environment: environment, fileSystem: fileSystem).map {
            $0 + "/" + ZmxSupport.socketDirectoryLeaf(forStateDirectory: stateDirectory)
        }
        return resolve(stateDirectory: stateDirectory, privateCandidate: base,
                       maximumBytes: linuxMaximumSocketPathBytes, fileSystem: fileSystem)
    }

    /// Chooses the macOS zmx socket directory for `stateDirectory`; the same rules as `resolveLinux`, in
    /// the same order, with the per-user temporary directory in place of the runtime directory:
    /// `<user temporary directory>/zmx-<8 hex>` (`darwinUserTemporaryLeaf`) when that directory is
    /// private to this user. `userTemporaryDirectory` is the macOS per-user temporary directory
    /// (`confstr(_CS_DARWIN_USER_TEMP_DIR)`), supplied by the caller so the choice is a pure function
    /// that is tested on every platform. Call once per process and keep the answer.
    public static func resolveDarwin(stateDirectory: String, userTemporaryDirectory: String?,
                                     fileSystem: FileSystem = .live) -> Result<String, Refusal> {
        let base = privateBaseDirectory(userTemporaryDirectory, fileSystem: fileSystem).map {
            $0 + "/" + darwinUserTemporaryLeaf(forStateDirectory: stateDirectory)
        }
        return resolve(stateDirectory: stateDirectory, privateCandidate: base,
                       maximumBytes: darwinMaximumSocketPathBytes, fileSystem: fileSystem)
    }

    private static func resolve(stateDirectory: String, privateCandidate: String?, maximumBytes: Int,
                                fileSystem: FileSystem) -> Result<String, Refusal> {
        let legacy = ZmxSupport.socketDirectory(forStateDirectory: stateDirectory)
        if case .present(let entry) = fileSystem.inspect(legacy), entry.kind == .directory,
           holdsOnlyOwnDaemonSockets(legacy, fileSystem: fileSystem),
           case .success = verify(legacy, create: false, fileSystem: fileSystem) {
            return .success(legacy)
        }
        if let candidate = privateCandidate, fitsSocketPath(candidate, maximumBytes: maximumBytes) {
            return verify(candidate, fileSystem: fileSystem)
        }
        if let state = safeStateDirectory(stateDirectory, fileSystem: fileSystem) {
            let candidate = state + "/" + stateDirectoryLeaf
            if fitsSocketPath(candidate, maximumBytes: maximumBytes) { return verify(candidate, fileSystem: fileSystem) }
        }
        return verify(legacy, fileSystem: fileSystem)
    }
}
