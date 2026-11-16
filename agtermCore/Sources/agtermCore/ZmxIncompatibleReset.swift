import Foundation

/// The launch-time reset of Live panes whose daemons the bundled zmx cannot talk to.
///
/// zmx v1 changed its IPC framing, so after an update a daemon a pre-v1 zmx started lists as
/// `err=IncompatibleProtocol`, and attaching to it shows a blank pane that takes no input. Its socket
/// cannot carry a kill either, so the app ends the daemon process itself and the pane restores as a pane
/// whose daemon is missing. Host-free: this picks the panes, finds the process and reports the outcome.
public enum ZmxIncompatibleReset {
    public struct Candidate: Equatable, Sendable {
        public let paneIdentity: UUID
        public let sessionID: UUID
        public let daemon: String
    }

    /// Claimed panes whose daemon is listed as incompatible. A daemon nobody claims is left alone.
    public static func candidates(claims: ZmxClaimWalk, records: [ZmxSessionRecord]) -> [Candidate] {
        let incompatible = Set(records.filter(\.incompatible).map(\.name))
        var seen: Set<UUID> = []
        return claims.claims.compactMap { claim in
            let daemon = ZmxSupport.daemonName(for: claim.paneIdentity)
            guard incompatible.contains(daemon), seen.insert(claim.paneIdentity).inserted else { return nil }
            return Candidate(paneIdentity: claim.paneIdentity, sessionID: claim.sessionID, daemon: daemon)
        }
    }

    /// A unix socket as `/proc/net/unix` lists it: the path is the one it was bound to.
    public struct SocketView: Equatable, Sendable {
        public let inode: UInt64
        public let path: String
        public let listening: Bool

        public init(inode: UInt64, path: String, listening: Bool) {
            self.inode = inode
            self.path = path
            self.listening = listening
        }
    }

    /// A process as the daemon search sees it: its arguments and the socket inodes its descriptors hold.
    public struct ProcessView: Equatable, Sendable {
        public let pid: Int32
        public let arguments: [String]
        public let sockets: Set<UInt64>

        public init(pid: Int32, arguments: [String], sockets: Set<UInt64>) {
            self.pid = pid
            self.arguments = arguments
            self.sockets = sockets
        }
    }

    /// The daemon serving `daemon`: the one process holding a listening socket bound in `socketDirectory`.
    /// A client holds only a connected, unbound socket. zmx binds either the session name itself or, when it
    /// publishes atomically (patch 0003), `.s/<pid>` before linking the name; that path names no session,
    /// so there the holder must also have been started for this one. Anything but one holder finds nothing.
    public static func daemonPID(daemon: String, socketDirectory: String, sockets: [SocketView],
                                 processes: [ProcessView]) -> Int32? {
        let named = socketDirectory + "/" + daemon
        let staged = socketDirectory + "/.s/"
        var inodes: [UInt64: Bool] = [:]
        for socket in sockets where socket.listening {
            if socket.path == named {
                inodes[socket.inode] = true
            } else if socket.path.hasPrefix(staged) {
                inodes[socket.inode] = false
            }
        }
        let holders = processes.filter { process in
            process.sockets.contains { inode in
                guard let byName = inodes[inode] else { return false }
                return byName || startedFor(daemon, arguments: process.arguments)
            }
        }
        return Set(holders.map(\.pid)).count == 1 ? holders[0].pid : nil
    }

    private static func startedFor(_ daemon: String, arguments: [String]) -> Bool {
        arguments.count >= 3 && (arguments[0] as NSString).lastPathComponent == "zmx"
            && ["attach", "a", "run", "r"].contains(arguments[1]) && arguments[2] == daemon
    }

    /// `daemonPIDs` holds the process found per daemon name and `survivors` the ones still running after
    /// the app ended them. `stillIncompatible` is the listing taken afterwards: a daemon it still names was
    /// not ended, whatever happened to the process found for it, so its pane is unconfirmed, never gone.
    public static func outcome(candidates: [Candidate], daemonPIDs: [String: Int32], survivors: Set<Int32>,
                               stillIncompatible: Set<String>) -> LiveReset.Outcome {
        var dispositions: [LiveReset.Target: LiveReset.Disposition] = [:]
        var unconfirmed = survivors
        for candidate in candidates {
            let pid = daemonPIDs[candidate.daemon]
            let target = LiveReset.Target(paneIdentity: candidate.paneIdentity, sessionID: candidate.sessionID,
                                          daemon: candidate.daemon, leaderPID: pid ?? 0, reason: .outdated)
            if stillIncompatible.contains(candidate.daemon) {
                dispositions[target] = .kill
                unconfirmed.insert(target.leaderPID)
            } else {
                dispositions[target] = pid == nil ? .gone : .kill
            }
        }
        return LiveReset.outcome(narrowed: LiveReset.Narrowed(dispositions: dispositions, inventoryFailed: false),
                                 survivors: unconfirmed, inventoryFailed: false)
    }

    /// The toast the first window shows; nil when nothing was reset. Short enough for a toast on a narrow
    /// window, which truncates rather than wraps.
    public static func notice(outcome: LiveReset.Outcome) -> String? {
        let affected = outcome.sessions.affected
        guard affected > 0 else { return nil }
        let reset = outcome.sessions.reset
        if reset == affected {
            return "Restarted \(affected) live \(affected == 1 ? "session" : "sessions") left by an older agterm"
        }
        return "Restarted \(reset) of \(affected) live sessions left by an older agterm; the others still run"
    }
}
