// The Linux control socket: a unix-domain socket that decodes one ControlRequest
// per connection and dispatches it onto the shared AppController/AppStore, writing
// one ControlResponse back. Mirrors the macOS ControlServer, but hops to the GTK
// main thread via g_idle (runOnMain) + a semaphore instead of DispatchQueue.main.
// The wire protocol (ControlProtocol) is shared from agtermCore; the Linux agtermctl wrapper lives in
// agterm-linux so Glibc socket code stays inside the Linux boundary.
import Foundation
import agtermCore
#if canImport(Glibc)
import Glibc
#endif

final class ControlServer: @unchecked Sendable {
    /// The request-read and shutdown bounds. `requestDeadline` and `shutdownGrace` are owner-tunable
    /// defaults (#132, #134).
    struct Timing: Sendable {
        /// Longest silence between two request bytes.
        var readTimeoutMS: Int32 = 5_000
        /// Longest whole request read, so a byte every few seconds cannot hold a worker thread.
        var requestDeadline: TimeInterval = 30
        /// How long `stop()` lets a request still waiting on the main thread finish before failing it.
        var shutdownGrace: TimeInterval = 2
    }

    let path: String
    let timing: Timing
    private var listenFD: Int32 = -1
    private var lockFD: Int32 = -1
    private var refused = false
    private static let maxLine = 1 << 20
    static let unavailableSuffix = ".unavailable"
    private let acceptLoops = NSCondition()
    private var runningAcceptLoops = 0
    private let pendingLock = NSLock()
    private var pendingReplies: [ObjectIdentifier: MainReply] = [:]
    private var stopping = false
    /// The cap on concurrent connection workers. agtermctl opens one short connection per command and
    /// streams (`zmx.present`, overlay jobs) hand their descriptor to the main loop and free the worker,
    /// so a worker is held only while a request is read, served on main, or waits on zmx/ssh (seconds).
    /// Dozens of agent panes polling about once a second need a handful at a time; 64 leaves more than
    /// tenfold headroom while bounding threads and descriptors well under the usual 1024 soft limit.
    static let defaultConnectionWorkers = 64
    private let connectionSlots: ControlConnectionSlots
    var activeConnectionWorkers: Int { connectionSlots.busy }

    private let logger = LinuxStructuredLogger(category: "ControlServer")
    @MainActor lazy var presentationHub = PresentationHub(staleTimeout: 30)
    @MainActor var presentationStreams: [LinuxPresentationStream] = []
    @MainActor var presentationHeartbeatCancel: (@MainActor () -> Void)?
    @MainActor lazy var overlayJobs = OverlayJobs()
    @MainActor var overlayJobStreams: [String: LinuxOverlayJobStream] = [:]
    @MainActor var pendingJobCancels: Set<String> = []
    private let setSocketPermissions: (String, Int32) -> Int32

    /// The socket path once actually bound (nil before bind / after a bind failure), so a spawned
    /// shell's `AGTERM_SOCKET` only advertises a socket that exists. Mirrors macOS `boundSocketPath`.
    var boundSocketPath: String? { listenFD >= 0 ? path : nil }
    var resolvedSocketPath: String { refused ? path + Self.unavailableSuffix : path }
    var activeAcceptLoops: Int {
        acceptLoops.lock()
        defer { acceptLoops.unlock() }
        return runningAcceptLoops
    }

    init(
        path: String? = nil,
        setSocketPermissions: ((String, Int32) -> Int32)? = nil,
        timing: Timing = Timing(),
        maxConnectionWorkers: Int = ControlServer.defaultConnectionWorkers
    ) {
        self.path = path ?? Self.defaultSocketPath()
        self.timing = timing
        self.connectionSlots = ControlConnectionSlots(limit: max(1, maxConnectionWorkers))
        self.setSocketPermissions = setSocketPermissions ?? { path, _ in
            path.withCString { chmod($0, 0o600) }
        }
        _ = acquireOwnership()
    }

    static func defaultSocketPath() -> String {
        ControlResolve.socketPath(stateDir: ProcessInfo.processInfo.environment["AGTERM_STATE_DIR"],
                                  appSupport: PersistenceStore.defaultDirectory.path)
    }

    func start() {
        guard listenFD < 0 else { return }
        signal(SIGPIPE, SIG_IGN)
        guard path.utf8.count < 104 else {
            logger.notice("control socket path too long (\(path.utf8.count) bytes): \(path)")
            return
        }
        guard lockFD >= 0 || acquireOwnership() else { return }
        let fd = socket(AF_UNIX, Int32(SOCK_STREAM.rawValue), 0)
        guard fd >= 0 else {
            let reason = String(cString: strerror(errno))
            logger.notice("control socket() failed: \(reason)")
            return
        }

        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let bytes = path.utf8CString
        withUnsafeMutablePointer(to: &addr.sun_path) { dst in
            dst.withMemoryRebound(to: CChar.self, capacity: bytes.count) { buf in
                bytes.withUnsafeBufferPointer { src in buf.update(from: src.baseAddress!, count: src.count) }
            }
        }
        unlink(path)
        let bound = withUnsafePointer(to: &addr) { p in
            p.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                bind(fd, sa, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard bound == 0 else {
            let reason = String(cString: strerror(errno))
            logger.notice("control bind(\(path)) failed: \(reason)")
            close(fd)
            return
        }
        // Secure the pathname before listen makes connections queueable. A permissive process umask can
        // otherwise leave a short cross-user connection window, and a failed chmod must never be ignored.
        guard setSocketPermissions(path, fd) == 0 else {
            let errorNumber = errno
            let reason = String(cString: strerror(errorNumber))
            logger.notice("control chmod(\(path), 0600) failed: \(reason)")
            close(fd)
            unlink(path)
            return
        }
        guard listen(fd, 8) == 0 else {
            let reason = String(cString: strerror(errno))
            logger.notice("control listen() failed: \(reason)")
            close(fd)
            unlink(path)
            return
        }
        listenFD = fd
        connectionSlots.open()
        pendingLock.lock()
        stopping = false
        pendingLock.unlock()
        acceptLoops.lock()
        runningAcceptLoops += 1
        acceptLoops.unlock()
        Thread.detachNewThread { [self] in
            acceptLoop(fd)
            acceptLoops.lock()
            runningAcceptLoops -= 1
            acceptLoops.broadcast()
            acceptLoops.unlock()
        }
        logger.notice("control socket at \(path)")
    }

    func stop() {
        defer { releaseOwnership() }
        failPendingRepliesAfterGrace()
        guard listenFD >= 0 else { return }
        // close() alone does not wake a thread blocked in accept() on Linux; that thread would then
        // accept on whatever socket next reuses the descriptor number.
        _ = Glibc.shutdown(listenFD, Int32(SHUT_RDWR))
        connectionSlots.close()
        let deadline = Date(timeIntervalSinceNow: 2)
        acceptLoops.lock()
        while runningAcceptLoops > 0, acceptLoops.wait(until: deadline) {}
        acceptLoops.unlock()
        close(listenFD)
        listenFD = -1
        unlink(path)
    }

    private func acquireOwnership() -> Bool {
        let lockPath = path + ".lock"
        let fd = open(lockPath, O_CREAT | O_RDWR | O_CLOEXEC, 0o600)
        guard fd >= 0 else {
            let reason = String(cString: strerror(errno))
            logger.notice("control lock open(\(lockPath)) failed: \(reason)")
            return false
        }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else {
            close(fd)
            refused = true
            logger.notice("control socket \(path) is already served by another instance — not binding")
            return false
        }
        lockFD = fd
        refused = false
        return true
    }

    private func releaseOwnership() {
        guard lockFD >= 0 else { return }
        close(lockFD)
        lockFD = -1
    }

    /// What the accept loop does after `accept()` fails with `code`, given the backoff it last slept.
    enum AcceptFailure: Equatable {
        /// stop()'s shutdown makes accept fail with EINVAL; the listener is gone.
        case exit
        /// EINTR (signal), ECONNABORTED and the like are transient: keep serving. Mirrors the macOS loop.
        case retry
        /// Out of descriptors or kernel memory: retrying at once would spin until one frees.
        case backoff(TimeInterval)
    }

    static let acceptBackoffFloor: TimeInterval = 0.01
    static let acceptBackoffCeiling: TimeInterval = 0.25

    static func acceptFailure(_ code: Int32, previousBackoff: TimeInterval) -> AcceptFailure {
        switch code {
        case EBADF, EINVAL, ENOTSOCK:
            return .exit
        case EMFILE, ENFILE, ENOBUFS, ENOMEM:
            return .backoff(min(max(previousBackoff * 2, acceptBackoffFloor), acceptBackoffCeiling))
        default:
            return .retry
        }
    }

    /// Takes a worker slot before each accept, so beyond the cap new connections wait in the listen backlog
    /// (and then in the client's blocking connect) instead of being accepted and dropped.
    private func acceptLoop(_ fd: Int32) {
        var backoff: TimeInterval = 0
        while connectionSlots.acquire() {
            let conn = accept(fd, nil, nil)
            guard conn >= 0 else {
                let code = errno
                connectionSlots.release()
                switch Self.acceptFailure(code, previousBackoff: backoff) {
                case .exit:
                    return
                case .retry:
                    continue
                case .backoff(let interval):
                    backoff = interval
                    // stop() closes the slots, which ends this pause at once.
                    guard connectionSlots.pause(interval) else { return }
                    continue
                }
            }
            backoff = 0
            Thread.detachNewThread { [self] in
                if !handle(conn) { close(conn) }
                connectionSlots.release()
            }
        }
    }

    private func handle(_ conn: Int32) -> Bool {
        guard let line = readLine(conn) else { return false }
        let response: ControlResponse
        if let req = try? JSONDecoder().decode(ControlRequest.self, from: line) {
            if req.cmd == .zmxPresent {
                let reply = openPresentationOnMain(req.target)
                guard writeResponse(conn, reply), reply.ok,
                      let id = reply.result?.id.flatMap(UUID.init(uuidString:)) else { return false }
                return adoptPresentationOnMain(conn, session: id)
            }
            if req.cmd == .sessionOverlayJobRun {
                let reply = claimOverlayJobOnMain(req.target)
                let written = writeResponse(conn, reply)
                guard reply.ok, let job = reply.result?.id else { return false }
                guard written, adoptOverlayJobOnMain(conn, job: job) else {
                    abandonOverlayJobOnMain(job)
                    return false
                }
                return true
            }
            response = performRequest(req)
        } else {
            response = ControlResponse(ok: false, error: "could not decode request")
        }
        _ = writeResponse(conn, response)
        return false
    }

    /// Shared one-request entry point for socket and file-page workers.
    func performRequest(_ request: ControlRequest) -> ControlResponse {
        if request.cmd == .sessionRestart { return restartOnMain(request) }
        if request.cmd == .browserClear { return clearBrowserOnMain(request) }
        if Self.isRemoteZmxRequest(request) { return dispatchRemoteZmx(request) }
        if let stage = Self.localZmxStage(request) { return perform(stage) }
        return dispatchOnMain(request)
    }

    /// Drives `first` to its one reply on this worker: `.worker` steps run here, `.main` steps hop to GTK
    /// through `awaitMain`. Once `stop()` has begun no new worker step starts, so nothing new is spawned
    /// against a shutting-down app; a main hop still gets `awaitMain`'s shutdown grace.
    func perform(_ first: ControlStage) -> ControlResponse {
        var stage = first
        while true {
            switch stage {
            case .done(let response):
                return response
            case .worker(let step):
                guard !isStopping else { return MainReply.shutdown }
                stage = step()
            case .main(let step):
                stage = awaitMainStage(step)
            }
        }
    }

    /// One main-thread turn of a staged request. The body runs at most once (`MainReply.begin`); when a
    /// shutdown cancel wins before it runs, the chain ends with the shutdown reply.
    private func awaitMainStage(_ step: @escaping @MainActor @Sendable () -> ControlStage) -> ControlStage {
        let next = ControlStageSlot()
        let response = awaitMain { reply in
            next.set(step())
            reply.resolve(ControlStageSlot.handedOn)
        }
        return next.take() ?? .done(response)
    }

    private var isStopping: Bool {
        pendingLock.lock()
        defer { pendingLock.unlock() }
        return stopping
    }

    /// Runs `body` on the GTK main thread and blocks until it resolves the reply. After `stop()`, a reply
    /// still pending once `timing.shutdownGrace` passes resolves as a shutdown failure instead.
    func awaitMain(_ body: @escaping @MainActor @Sendable (MainReply) -> Void) -> ControlResponse {
        let reply = MainReply()
        pendingLock.lock()
        pendingReplies[ObjectIdentifier(reply)] = reply
        let failLater = stopping
        pendingLock.unlock()
        if failLater { failAfterGrace([reply]) }
        runOnMain {
            MainActor.assumeIsolated {
                guard reply.begin() else { return }
                body(reply)
                reply.end()
            }
        }
        let response = reply.wait()
        pendingLock.lock()
        pendingReplies[ObjectIdentifier(reply)] = nil
        pendingLock.unlock()
        return response
    }

    private func failPendingRepliesAfterGrace() {
        pendingLock.lock()
        stopping = true
        let replies = Array(pendingReplies.values)
        pendingLock.unlock()
        failAfterGrace(replies)
    }

    private func failAfterGrace(_ replies: [MainReply]) {
        guard !replies.isEmpty else { return }
        DispatchQueue.global().asyncAfter(deadline: .now() + timing.shutdownGrace) {
            for reply in replies { reply.cancel() }
        }
    }

    private func restartOnMain(_ request: ControlRequest) -> ControlResponse {
        awaitMain { reply in
            switch Self.route(for: request) {
            case .controller(let controller?): controller.restartPane(request) { reply.resolve($0) }
            case .controller(nil): reply.resolve(ControlResponse(ok: false, error: "no controller"))
            case .failure(let error): reply.resolve(ControlResponse(ok: false, error: error))
            }
        }
    }

    private func clearBrowserOnMain(_ request: ControlRequest) -> ControlResponse {
        if request.target != nil || request.args?.window != nil {
            return ControlResponse(ok: false, error: "browser.clear takes no target or --window")
        }
        return awaitMain { reply in LinuxBrowserStore.shared.clear { reply.resolve($0) } }
    }

    private func writeResponse(_ conn: Int32, _ response: ControlResponse) -> Bool {
        guard var data = try? JSONEncoder().encode(response) else { return false }
        data.append(0x0A)
        return writeAll(conn, data)
    }

    private static func isRemoteZmxRequest(_ request: ControlRequest) -> Bool {
        request.cmd == .zmxAttach
            || (request.cmd == .zmxTree
                && request.args?.host?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false)
    }

    /// SSH waits on a per-connection worker, never GTK's main thread and never the listener thread.
    private func dispatchRemoteZmx(_ request: ControlRequest) -> ControlResponse {
        Self.dispatchRemoteZmx(request, readTree: readRemoteTree(host:), attach: attachRemoteOnMain)
    }

    /// Validates and trims like the shared dispatcher, before any ssh call.
    static func dispatchRemoteZmx(
        _ request: ControlRequest, readTree: (String) -> ControlResponse,
        attach: (_ host: String, _ session: String, _ tree: ControlRemoteTree, _ window: String?) -> ControlResponse
    ) -> ControlResponse {
        guard let host = request.args?.host?.linuxTrimmedOrNil else {
            return ControlResponse(ok: false, error: request.cmd == .zmxAttach
                ? "zmx.attach requires a host" : "invalid host")
        }
        guard request.cmd == .zmxAttach else { return readTree(host) }
        guard let remoteID = request.target?.linuxTrimmedOrNil else {
            return ControlResponse(ok: false, error: "zmx.attach requires a remote session")
        }
        guard isPlainRemoteToken(remoteID) else {
            return ControlResponse(ok: false, error: "invalid remote session")
        }
        let tree = readTree(host)
        guard tree.ok, let remote = tree.result?.remote else { return tree }
        return attach(host, remoteID, remote, request.args?.window?.linuxTrimmedOrNil)
    }

    private func readRemoteTree(host: String) -> ControlResponse {
        let argv: [String]
        do {
            argv = try RemoteSession.treeCommand(host: host)
        } catch {
            return ControlResponse(ok: false, error: "invalid host")
        }
        let result = LinuxRemoteCommand.run(argv, deadline: LinuxRemoteCommand.treeDeadline)
        guard result.status == 0 else {
            let stderr = result.stderr.trimmingCharacters(in: .whitespacesAndNewlines)
            let detail = RemoteTreeMerger.remoteError(stdout: result.stdout) ?? (stderr.isEmpty ? nil : stderr)
            return ControlResponse(ok: false, error: detail ?? "the remote command failed on \(host)")
        }
        do {
            let decoded = try RemoteTreeMerger.decode(stdout: result.stdout)
            let stamped = decoded.stamped(host: host)
            return ControlResponse(ok: true, result: ControlResult(remote: stamped))
        } catch let error as RemoteTreeMerger.MergeError {
            return ControlResponse(ok: false, error: error.message)
        } catch {
            return ControlResponse(ok: false, error: "the remote answer could not be read")
        }
    }

    private func attachRemoteOnMain(host: String, session: String,
                                    tree: ControlRemoteTree, window: String?) -> ControlResponse {
        awaitMain { reply in
            let controller: AppController?
            if let window {
                switch gLibrary?.resolveWindow(window) {
                case .resolved(let id): controller = gWindows[id]
                case .ambiguous(let hits):
                    reply.resolve(ControlResponse(ok: false,
                        error: ControlResolve.ambiguousMessage(noun: "window", target: window, hits: hits)))
                    return
                case .notFound, nil:
                    reply.resolve(ControlResponse(ok: false,
                        error: ControlResolve.notFoundMessage(noun: "window", target: window)))
                    return
                }
            } else {
                controller = gController
            }
            reply.resolve(controller?.attachRemoteSession(host: host, session: session, tree: tree)
                ?? ControlResponse(ok: false, error: "window not open"))
        }
    }

    private static func isPlainRemoteToken(_ value: String) -> Bool {
        !value.isEmpty && !value.unicodeScalars.contains {
            $0.properties.isWhitespace || $0.value < 0x20 || $0.value == 0x7f
        }
    }

    /// Run the dispatch on the GTK main thread. A handler that reaches a zmx call hands that tail off
    /// (`ControlStageHandoff`), and it then runs on this worker instead of the main loop.
    private func dispatchOnMain(_ req: ControlRequest) -> ControlResponse {
        perform(.main { ControlStageHandoff.collect { Self.route(for: req).response(for: req) } })
    }

    enum ControllerRoute {
        case controller(AppController?)
        case failure(String)

        @MainActor func response(for req: ControlRequest) -> ControlResponse {
            switch self {
            case .controller(let controller):
                return controller?.handleControl(req) ?? ControlResponse(ok: false, error: "no controller")
            case .failure(let error):
                return ControlResponse(ok: false, error: error)
            }
        }
    }

    @MainActor static func route(for req: ControlRequest) -> ControllerRoute {
        if let window = req.args?.window, !window.isEmpty {
            guard let library = gLibrary else {
                return .failure("window not open")
            }
            let id: UUID
            switch library.resolveWindow(window) {
            case .resolved(let resolved):
                id = resolved
            case .ambiguous(let hits):
                return .failure(ControlResolve.ambiguousMessage(noun: "window", target: window, hits: hits))
            case .notFound:
                return .failure(ControlResolve.notFoundMessage(noun: "window", target: window))
            }
            guard let controller = gWindows[id] else {
                return .failure("window not open")
            }
            return .controller(controller)
        }
        switch req.cmd {
        case .sessionRestart, .sessionOverlaySubmit, .keymapRun, .sessionClose, .sessionDuplicate, .sessionSelect, .sessionGo, .sessionRename, .sessionReveal,
             .sessionMove, .sessionType,
             .sessionStatus, .sessionRestore, .sessionFlag, .sessionContext, .sessionSeen,
             .sessionSplit, .sessionSplitClose, .sessionSwap, .sessionLead, .sessionScratch, .sessionFocus,
             .sessionCopy, .sessionPaste, .sessionSelectAll, .sessionSearch,
             .sessionOverlayOpen, .sessionOverlayClose, .sessionOverlayResize, .sessionOverlayResult,
             .sessionOverlayReload, .sessionOverlayNavigate,
             .sessionOverlayCopy, .sessionOverlayText,
             .sessionHudOpen, .sessionHudUpdate, .sessionHudClose,
             .askOpen,
             .sessionBackground, .sessionResize, .sessionText, .notify,
             .fontInc, .fontDec, .fontReset:
            return routeOwningSession(req.target) ?? .controller(gController)
        case .workspaceRename, .workspaceDelete, .workspaceSelect, .workspaceMove, .workspaceFocus,
             .workspaceCollapse, .workspaceExpand:
            return routeOwningWorkspace(req.target) ?? .controller(gController)
        case .sessionNew:
            return routeOwningWorkspace(req.args?.workspace) ?? .controller(gController)
        case .surfaceCursor where req.args?.paneID?.isEmpty == false:
            let target = req.target?.trimmingCharacters(in: .whitespacesAndNewlines)
            if target == "quick" || target.flatMap(TerminalSurfaceID.init(rawValue:)) != nil {
                return .failure("surface.cursor: --pane-id takes a session target")
            }
            return routeOwningSession(req.target) ?? .controller(gController)
        case .surfaceZoom, .surfaceCursor:
            return routeOwningSurface(req.target) ?? .controller(gController)
        case .tree, .eventsRead, .workspaceNew, .workspaceGo, .quick, .quickType, .quickText, .dashboard,
             .sidebar, .sidebarMode, .sidebarFlaggedLayout, .sidebarExpand, .sidebarCollapse, .workspaceFilter,
             .windowNew, .windowList, .windowSelect, .windowGo, .windowClose, .windowRename, .windowDelete,
             .windowResize, .windowMove, .windowZoom, .windowFullscreen, .windowMinimize,
             .keymapReload, .keymapList, .hooksReload, .hooksList, .configReload, .themeSet, .themeList,
             .pickOpen, .pickResult, .pickCancel, .askResult, .askCancel, .sidebarWidth,
             .restoreClear, .restoreCapture, .restoreMode, .recentClear, .version,
             .browserClear, .zmxScreen, .zmxList, .zmxPrune, .zmxKill, .zmxReset, .zmxTree, .zmxAttach, .zmxPresent,
             .sessionOverlayJobRun, .debugAppearance:
            return .controller(gController)
        }
    }

    @MainActor private static func explicitTarget(_ target: String?) -> String? {
        guard let target = target?.trimmingCharacters(in: .whitespacesAndNewlines),
              !target.isEmpty, target != "active" else { return nil }
        return target
    }

    @MainActor private static func routeOwningSession(_ target: String?) -> ControllerRoute? {
        guard let target = explicitTarget(target) else { return nil }
        let controllers = gWindows.values
        let candidates = controllers.flatMap { $0.store.workspaces.flatMap { $0.sessions.map(\.id) } }
        switch ControlResolve.resolve(target, candidates: candidates, active: nil) {
        case .resolved(let id):
            return .controller(controllers.first { $0.store.session(withID: id) != nil })
        case .ambiguous(let hits):
            return .failure(ControlResolve.ambiguousMessage(noun: "session", target: target, hits: hits))
        case .notFound:
            return .failure(ControlResolve.notFoundMessage(noun: "session", target: target))
        }
    }

    @MainActor private static func routeOwningSurface(_ target: String?) -> ControllerRoute? {
        guard let target = explicitTarget(target), target != "quick",
              let surfaceID = TerminalSurfaceID(rawValue: target) else { return nil }
        return routeOwningSession(surfaceID.sessionID.uuidString)
    }

    @MainActor private static func routeOwningWorkspace(_ target: String?) -> ControllerRoute? {
        guard let target = explicitTarget(target) else { return nil }
        let controllers = gWindows.values
        let candidates = controllers.flatMap { $0.store.workspaces.map(\.id) }
        switch ControlResolve.resolve(target, candidates: candidates, active: nil) {
        case .resolved(let id):
            return .controller(controllers.first { $0.store.workspaces.contains { $0.id == id } })
        case .ambiguous(let hits):
            return .failure(ControlResolve.ambiguousMessage(noun: "workspace", target: target, hits: hits))
        case .notFound:
            return .failure(ControlResolve.notFoundMessage(noun: "workspace", target: target))
        }
    }

    private func readLine(_ conn: Int32) -> Data? {
        var buffer = Data()
        var byte: UInt8 = 0
        let deadline = Date(timeIntervalSinceNow: timing.requestDeadline)
        while true {
            let remainingMS = deadline.timeIntervalSinceNow * 1_000
            guard remainingMS > 0 else { return nil }
            var pfd = pollfd(fd: conn, events: Int16(POLLIN), revents: 0)
            let ready = poll(&pfd, 1, Int32(min(Double(timing.readTimeoutMS), remainingMS.rounded(.up))))
            if ready == 0 { return nil }
            if ready < 0 {
                if errno == EINTR { continue }
                return nil
            }
            guard (pfd.revents & Int16(POLLIN)) != 0 else { return nil }
            let n = read(conn, &byte, 1)
            if n == 0 { return buffer.isEmpty ? nil : buffer }
            if n < 0 { return nil }
            if byte == 0x0A { return buffer }
            buffer.append(byte)
            if buffer.count > Self.maxLine { return nil }
        }
    }

    private func writeAll(_ conn: Int32, _ data: Data) -> Bool {
        data.withUnsafeBytes { raw in
            guard let base = raw.bindMemory(to: UInt8.self).baseAddress else { return true }
            var offset = 0
            while offset < data.count {
                let n = Glibc.send(conn, base + offset, data.count - offset, Int32(MSG_NOSIGNAL))
                if n < 0, errno == EINTR { continue }
                if n <= 0 { return false }
                offset += n
            }
            return true
        }
    }
}

/// Counts connection workers; parks the accept loop while every slot is busy or while it backs off.
/// `close()` (from `stop()`) wakes both waits at once.
final class ControlConnectionSlots: @unchecked Sendable {
    let limit: Int
    private let condition = NSCondition()
    private var held = 0
    private var closed = false

    init(limit: Int) { self.limit = limit }

    var busy: Int {
        condition.lock()
        defer { condition.unlock() }
        return held
    }

    func open() {
        condition.lock()
        closed = false
        condition.unlock()
    }

    func close() {
        condition.lock()
        closed = true
        condition.broadcast()
        condition.unlock()
    }

    /// Waits for a free slot; false once closed.
    func acquire() -> Bool {
        condition.lock()
        defer { condition.unlock() }
        while held >= limit, !closed { condition.wait() }
        guard !closed else { return false }
        held += 1
        return true
    }

    func release() {
        condition.lock()
        held -= 1
        condition.broadcast()
        condition.unlock()
    }

    /// Sleeps up to `interval`; false when closed before or during the sleep.
    func pause(_ interval: TimeInterval) -> Bool {
        condition.lock()
        defer { condition.unlock() }
        let deadline = Date(timeIntervalSinceNow: interval)
        while !closed, condition.wait(until: deadline) {}
        return !closed
    }
}

/// The next stage a main-thread turn produced, handed back to the waiting worker.
private final class ControlStageSlot: @unchecked Sendable {
    /// Resolves the turn's `MainReply`; the worker reads the real result from the slot.
    static let handedOn = ControlResponse(ok: true)
    private let lock = NSLock()
    private var stage: ControlStage?

    func set(_ value: ControlStage) {
        lock.lock()
        stage = value
        lock.unlock()
    }

    func take() -> ControlStage? {
        lock.lock()
        defer { lock.unlock() }
        return stage
    }
}

/// One worker's wait for a main-thread reply, resolved exactly once: by the body, or by `cancel()`.
/// A cancel never interrupts a body mid-turn, so a body that adopts a descriptor cannot also see it closed.
final class MainReply: @unchecked Sendable {
    static let shutdown = ControlResponse(ok: false, error: "agterm is shutting down")

    private enum State { case waiting, running(cancelRequested: Bool), awaitingCallback, done }
    private let lock = NSLock()
    private let resolved = DispatchSemaphore(value: 0)
    private var state = State.waiting
    private var value = ControlResponse(ok: false, error: "internal")

    /// False once a cancel has won, so the body must not run.
    func begin() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard case .waiting = state else { return false }
        state = .running(cancelRequested: false)
        return true
    }

    /// The body returned; one that has not resolved yet is waiting on an async callback.
    func end() {
        lock.lock()
        defer { lock.unlock() }
        guard case .running(let cancelRequested) = state else { return }
        if cancelRequested { finish(Self.shutdown) } else { state = .awaitingCallback }
    }

    func resolve(_ response: ControlResponse) {
        lock.lock()
        defer { lock.unlock() }
        if case .done = state { return }
        finish(response)
    }

    func cancel() {
        lock.lock()
        defer { lock.unlock() }
        switch state {
        case .waiting, .awaitingCallback: finish(Self.shutdown)
        case .running: state = .running(cancelRequested: true)
        case .done: break
        }
    }

    func wait() -> ControlResponse {
        resolved.wait()
        lock.lock()
        defer { lock.unlock() }
        return value
    }

    private func finish(_ response: ControlResponse) {
        value = response
        state = .done
        resolved.signal()
    }
}
