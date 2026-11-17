import Foundation
import Glibc
import agtermCore

/// Ends the Live daemons the bundled zmx cannot talk to, before any pane attaches to one
/// (`ZmxIncompatibleReset`). Runs once at launch, bounded: a daemon that ignores SIGTERM gets SIGKILL,
/// and its shell then loses its terminal like any closed one.
enum LinuxIncompatibleDaemons {
    static let termGrace: TimeInterval = 1.5
    static let killGrace: TimeInterval = 1.0

    /// A daemon found for the reset, with the start time that tells it apart from a later process
    /// reusing its pid: every signal re-reads it first.
    private struct Found {
        let pid: Int32
        let started: Int64
    }

    @MainActor
    static func reset(client: LinuxZmxClient, library: WindowLibrary) -> LiveReset.Outcome? {
        guard let records = client.listSessions() else { return nil }
        let candidates = ZmxIncompatibleReset.candidates(claims: library.paneClaims(), records: records)
        guard !candidates.isEmpty else { return nil }
        let sockets = unixSockets()
        let processes = zmxProcesses()
        var found: [String: Found] = [:]
        for candidate in candidates {
            guard let pid = ZmxIncompatibleReset.daemonPID(daemon: candidate.daemon,
                                                           socketDirectory: client.socketDirectory,
                                                           sockets: sockets, processes: processes),
                  let started = startTime(of: pid) else { continue }
            found[candidate.daemon] = Found(pid: pid, started: started)
        }
        let daemons = Array(Dictionary(found.values.map { ($0.pid, $0) }, uniquingKeysWith: { first, _ in first }).values)
        daemons.forEach { send(SIGTERM, to: $0) }
        var alive = waitForExit(daemons, within: termGrace)
        if !alive.isEmpty {
            alive.forEach { send(SIGKILL, to: $0) }
            alive = waitForExit(alive, within: killGrace)
        }
        // A daemon ended by SIGKILL leaves its socket, which this listing removes once it refuses connections.
        // Whatever it still reports as incompatible was not ended, whether or not its process was found.
        let still = Set((client.listSessions() ?? records).filter(\.incompatible).map(\.name))
        let outcome = ZmxIncompatibleReset.outcome(candidates: candidates, daemonPIDs: found.mapValues(\.pid),
                                                   survivors: Set(alive.map(\.pid)), stillIncompatible: still)
        FileHandle.standardError.write(Data(("agterm: Live reset at launch: \(candidates.count) pane(s) on an "
            + "incompatible zmx; ended \(daemons.count - alive.count) of \(daemons.count) daemon(s) found, "
            + "\(outcome.unconfirmed.count) pane(s) not reset\n").utf8))
        return outcome
    }

    private static func send(_ signal: Int32, to daemon: Found) {
        guard startTime(of: daemon.pid) == daemon.started else { return }
        _ = Glibc.kill(daemon.pid, signal)
    }

    private static func waitForExit(_ daemons: [Found], within seconds: TimeInterval) -> [Found] {
        let deadline = Date().addingTimeInterval(seconds)
        var alive = daemons.filter { startTime(of: $0.pid) == $0.started }
        while !alive.isEmpty, Date() < deadline {
            usleep(50_000)
            alive = alive.filter { startTime(of: $0.pid) == $0.started }
        }
        return alive
    }

    /// Start time in clock ticks, nil once the process is gone or a zombie: a zombie no longer holds the
    /// socket or the terminal.
    private static func startTime(of pid: Int32) -> Int64? {
        guard let stat = procText("/proc/\(pid)/stat"),
              let close = stat.lastIndex(of: ")") else { return nil }
        let fields = stat[stat.index(after: close)...].split(whereSeparator: \.isWhitespace)
        guard fields.count > 19, fields[0] != "Z", fields[0] != "X" else { return nil }
        return Int64(fields[19])
    }

    /// `/proc/net/unix`: `Num RefCount Protocol Flags Type St Inode Path`; flag 0x10000 is a listening socket.
    static func unixSockets() -> [ZmxIncompatibleReset.SocketView] {
        guard let table = procText("/proc/net/unix") else { return [] }
        return table.split(separator: "\n").dropFirst().compactMap { line in
            let fields = line.split(separator: " ", omittingEmptySubsequences: true)
            guard fields.count >= 8, let flags = UInt32(fields[3], radix: 16), let inode = UInt64(fields[6]) else {
                return nil
            }
            return ZmxIncompatibleReset.SocketView(inode: inode, path: fields[7...].joined(separator: " "),
                                                   listening: flags & 0x10000 != 0)
        }
    }

    /// A procfs file read to its end. Its size reads as zero, and Foundation's whole-file readers stop after
    /// one page there: measured on the Omarchy VM, `/proc/net/unix` came back without the daemon's line.
    static func procData(_ path: String) -> Data? {
        let descriptor = open(path, O_RDONLY | O_CLOEXEC)
        guard descriptor >= 0 else { return nil }
        defer { close(descriptor) }
        var data = Data()
        var chunk = [UInt8](repeating: 0, count: 65536)
        while true {
            let count = read(descriptor, &chunk, chunk.count)
            if count == 0 { return data }
            if count < 0 {
                if errno == EINTR { continue }
                return nil
            }
            data.append(contentsOf: chunk[0..<count])
        }
    }

    static func procText(_ path: String) -> String? {
        procData(path).map { String(decoding: $0, as: UTF8.self) }
    }

    /// The zmx processes, with the socket inodes their descriptors hold.
    static func zmxProcesses() -> [ZmxIncompatibleReset.ProcessView] {
        guard let names = try? FileManager.default.contentsOfDirectory(atPath: "/proc") else { return [] }
        return names.compactMap { name in
            guard let pid = Int32(name), let data = procData("/proc/\(pid)/cmdline") else { return nil }
            let arguments = data.split(separator: 0).map { String(decoding: $0, as: UTF8.self) }
            guard let first = arguments.first, (first as NSString).lastPathComponent == "zmx",
                  let descriptors = try? FileManager.default.contentsOfDirectory(atPath: "/proc/\(pid)/fd") else {
                return nil
            }
            let sockets = Set(descriptors.compactMap { descriptor -> UInt64? in
                guard let target = try? FileManager.default.destinationOfSymbolicLink(atPath: "/proc/\(pid)/fd/\(descriptor)"),
                      target.hasPrefix("socket:["), target.hasSuffix("]") else { return nil }
                return UInt64(target.dropFirst("socket:[".count).dropLast())
            })
            return ZmxIncompatibleReset.ProcessView(pid: pid, arguments: arguments, sockets: sockets)
        }
    }
}
