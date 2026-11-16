import Foundation
import Glibc
import agtermCore

/// Ends the Live daemons the bundled zmx cannot talk to, before any pane attaches to one
/// (`ZmxIncompatibleReset`). Runs once at launch, bounded: a daemon that ignores SIGTERM gets SIGKILL,
/// and its shell then loses its terminal like any closed one.
enum LinuxIncompatibleDaemons {
    static let termGrace: TimeInterval = 1.5
    static let killGrace: TimeInterval = 1.0

    @MainActor
    static func reset(client: LinuxZmxClient, library: WindowLibrary) -> LiveReset.Outcome? {
        guard let records = client.listSessions() else { return nil }
        let candidates = ZmxIncompatibleReset.candidates(claims: library.paneClaims(), records: records)
        guard !candidates.isEmpty else { return nil }
        let processes = processTable()
        var pids: [String: Int32] = [:]
        for candidate in candidates {
            pids[candidate.daemon] = ZmxIncompatibleReset.daemonPID(
                daemon: candidate.daemon, socketDirectory: client.socketDirectory, processes: processes)
        }
        let ended = Set(pids.values)
        for pid in ended { _ = Glibc.kill(pid, SIGTERM) }
        var survivors = waitForExit(ended, within: termGrace)
        if !survivors.isEmpty {
            for pid in survivors { _ = Glibc.kill(pid, SIGKILL) }
            survivors = waitForExit(survivors, within: killGrace)
        }
        // A daemon ended by SIGKILL leaves its socket; listing removes a socket that refuses connections.
        _ = client.listSessions()
        let outcome = ZmxIncompatibleReset.outcome(candidates: candidates, daemonPIDs: pids, survivors: survivors)
        FileHandle.standardError.write(Data(("agterm: Live reset at launch: \(candidates.count) pane(s) on an "
            + "incompatible zmx; ended \(ended.count - survivors.count) daemon(s), \(survivors.count) still running\n").utf8))
        return outcome
    }

    private static func waitForExit(_ pids: Set<Int32>, within seconds: TimeInterval) -> Set<Int32> {
        let deadline = Date().addingTimeInterval(seconds)
        var alive = pids.filter(isRunning)
        while !alive.isEmpty, Date() < deadline {
            usleep(50_000)
            alive = alive.filter(isRunning)
        }
        return alive
    }

    /// A zombie has ended: it no longer holds the socket or the terminal.
    private static func isRunning(_ pid: Int32) -> Bool {
        guard let stat = try? String(contentsOfFile: "/proc/\(pid)/stat", encoding: .utf8),
              let close = stat.lastIndex(of: ")") else { return false }
        let state = stat[stat.index(after: close)...].split(whereSeparator: \.isWhitespace).first
        return state != "Z" && state != "X"
    }

    static func processTable() -> [ZmxIncompatibleReset.ProcessView] {
        guard let names = try? FileManager.default.contentsOfDirectory(atPath: "/proc") else { return [] }
        return names.compactMap { name in
            guard let pid = Int32(name),
                  let stat = try? String(contentsOfFile: "/proc/\(pid)/stat", encoding: .utf8),
                  let close = stat.lastIndex(of: ")"),
                  let arguments = nulSeparated("/proc/\(pid)/cmdline"), !arguments.isEmpty,
                  let environment = nulSeparated("/proc/\(pid)/environ") else { return nil }
            // after the comm: state ppid pgrp session tty_nr
            let fields = stat[stat.index(after: close)...].split(whereSeparator: \.isWhitespace)
            guard fields.count > 4, let terminal = Int64(fields[4]) else { return nil }
            return ZmxIncompatibleReset.ProcessView(pid: pid, arguments: arguments, environment: environment,
                                                    terminal: terminal)
        }
    }

    private static func nulSeparated(_ path: String) -> [String]? {
        guard let data = FileManager.default.contents(atPath: path) else { return nil }
        return data.split(separator: 0).map { String(decoding: $0, as: UTF8.self) }
    }
}
