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

    /// One process as the daemon search sees it.
    public struct ProcessView: Equatable, Sendable {
        public let pid: Int32
        public let arguments: [String]
        public let environment: [String]
        /// The controlling terminal; zero for none.
        public let terminal: Int64

        public init(pid: Int32, arguments: [String], environment: [String], terminal: Int64) {
            self.pid = pid
            self.arguments = arguments
            self.environment = environment
            self.terminal = terminal
        }
    }

    /// The daemon serving `daemon` in `socketDirectory`: a zmx process started for that session name with
    /// that `ZMX_DIR` and no controlling terminal. A client of the same session runs on a terminal, which is
    /// what tells it apart; more than one match finds nothing rather than guessing.
    public static func daemonPID(daemon: String, socketDirectory: String, processes: [ProcessView]) -> Int32? {
        let directory = "ZMX_DIR=" + socketDirectory
        let matches = processes.filter { process in
            guard process.terminal == 0, process.arguments.count >= 3,
                  (process.arguments[0] as NSString).lastPathComponent == "zmx",
                  ["attach", "a", "run", "r"].contains(process.arguments[1]),
                  process.arguments[2] == daemon else { return false }
            return process.environment.contains(directory)
        }
        return matches.count == 1 ? matches[0].pid : nil
    }

    /// `daemonPIDs` holds the process found per daemon name; a candidate without one had nothing left
    /// to end and counts as gone. `survivors` are the pids still running after the app ended them.
    public static func outcome(candidates: [Candidate], daemonPIDs: [String: Int32],
                               survivors: Set<Int32>) -> LiveReset.Outcome {
        var dispositions: [LiveReset.Target: LiveReset.Disposition] = [:]
        for candidate in candidates {
            let pid = daemonPIDs[candidate.daemon]
            let target = LiveReset.Target(paneIdentity: candidate.paneIdentity, sessionID: candidate.sessionID,
                                          daemon: candidate.daemon, leaderPID: pid ?? 0, reason: .outdated)
            dispositions[target] = pid == nil ? .gone : .kill
        }
        return LiveReset.outcome(narrowed: LiveReset.Narrowed(dispositions: dispositions, inventoryFailed: false),
                                 survivors: survivors, inventoryFailed: false)
    }

    /// The toast the first window shows; nil when nothing was reset. Short enough for a toast on a narrow
    /// window, which truncates rather than wraps.
    public static func notice(outcome: LiveReset.Outcome) -> String? {
        let affected = outcome.sessions.affected
        guard affected > 0 else { return nil }
        let text = "Restarted \(affected) live \(affected == 1 ? "session" : "sessions") left by an older agterm"
        return outcome.sessions.unconfirmed > 0 ? text + "; some old processes may still run" : text
    }
}
