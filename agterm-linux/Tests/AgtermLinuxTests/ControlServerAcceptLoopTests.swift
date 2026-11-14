import Foundation
import Glibc
import Testing
import agtermCore
@testable import AgtermLinux

/// The accept loop's exhaustion backoff and its cap on concurrent connection workers.
@MainActor
@Suite("Linux control socket accept backoff and worker cap", .serialized)
struct ControlServerAcceptLoopTests {
    @Test("accept failures: exhaustion backs off within bounds, shutdown exits, the rest retry")
    func acceptFailureDecision() {
        let floor = ControlServer.acceptBackoffFloor
        let ceiling = ControlServer.acceptBackoffCeiling
        for code in [EMFILE, ENFILE, ENOBUFS, ENOMEM] {
            #expect(ControlServer.acceptFailure(code, previousBackoff: 0) == .backoff(floor))
            #expect(ControlServer.acceptFailure(code, previousBackoff: floor) == .backoff(floor * 2))
            #expect(ControlServer.acceptFailure(code, previousBackoff: ceiling) == .backoff(ceiling))
        }
        var backoff: TimeInterval = 0
        for _ in 0..<20 {
            guard case .backoff(let next) = ControlServer.acceptFailure(EMFILE, previousBackoff: backoff) else {
                Issue.record("EMFILE did not back off")
                return
            }
            #expect(next > backoff || next == ceiling)
            backoff = next
        }
        #expect(backoff == ceiling)
        for code in [EBADF, EINVAL, ENOTSOCK] {
            #expect(ControlServer.acceptFailure(code, previousBackoff: 0) == .exit)
        }
        for code in [EINTR, ECONNABORTED, EPROTO] {
            #expect(ControlServer.acceptFailure(code, previousBackoff: 0) == .retry)
        }
    }

    @Test("beyond the worker cap a connection waits in the backlog, then is served; stop still wakes the loop")
    func workerCapQueuesConnections() throws {
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket, maxConnectionWorkers: 1)
        server.start()
        defer { server.stop() }
        #expect(ControlServer.defaultConnectionWorkers == 64)

        let holder = try ControlTestClient(path: directory.socket)
        #expect(waitUntil(timeout: 5) { server.activeConnectionWorkers == 1 })
        let queued = try ControlTestClient(path: directory.socket)
        defer { queued.close() }
        queued.send("not json\n")
        #expect(queued.readLine(timeout: 1) == .timedOut(Data()))
        #expect(server.activeConnectionWorkers == 1)

        holder.close()
        #expect(queued.response(timeout: 5)?.error?.hasPrefix("invalid request: ") == true)
        // The accept loop reserves the freed slot before blocking in accept(), so busy need not reach zero.
        // Serving another request at cap 1 proves the queued request released its worker slot.
        let probe = try ControlTestClient(path: directory.socket)
        defer { probe.close() }
        probe.send("not json\n")
        #expect(probe.response(timeout: 5)?.error?.hasPrefix("invalid request: ") == true)

        // Park the loop on a full cap again, then stop: it must not wait out the 2 s stop deadline.
        let second = try ControlTestClient(path: directory.socket)
        defer { second.close() }
        #expect(waitUntil(timeout: 5) { server.activeConnectionWorkers == 1 })
        let stopped = Date()
        server.stop()
        #expect(Date().timeIntervalSince(stopped) < 1)
        #expect(server.activeAcceptLoops == 0)
    }
}
