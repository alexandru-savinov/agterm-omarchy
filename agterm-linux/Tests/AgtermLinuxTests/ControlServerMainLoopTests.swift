import Foundation
import Glibc
import Testing
import agtermCore
@testable import AgtermLinux

/// A zmx runner that blocks each invocation until the test opens the gate, so a request is provably in
/// flight on a worker while the test pumps the GLib main loop.
private final class GatedZmxRunner: @unchecked Sendable {
    private let lock = NSLock()
    private let gate = DispatchSemaphore(value: 0)
    private var started: [[String]] = []
    private var finished = 0

    var startedArguments: [[String]] { lock.withLock { started } }
    var finishedCount: Int { lock.withLock { finished } }

    func open(_ count: Int = 1) { for _ in 0..<count { gate.signal() } }

    func client() -> LinuxZmxClient {
        LinuxZmxClient(executablePath: "/zmx", socketDirectory: "/tmp/zmx-mainloop-test", timeout: 3) { invocation in
            self.lock.withLock { self.started.append(invocation.arguments) }
            // Bounded: a broken test fails on its own expectations, never hangs the suite.
            _ = self.gate.wait(timeout: .now() + 10)
            self.lock.withLock { self.finished += 1 }
            return ""
        }
    }
}

/// Queues an idle callback on the GLib default context and reports whether it ran within `timeout`.
@MainActor
private func mainLoopTurns(within timeout: TimeInterval) -> Bool {
    let fired = TestSlot<Bool>()
    runOnMain { fired.set(true) }
    return pumpGLib(timeout: timeout) { fired.value != nil }
}

@MainActor
@Suite("Linux control zmx calls stay off the main loop", .serialized)
struct ControlServerMainLoopTests {
    private static let noController = ControlResponse(ok: false, error: "no controller")

    @Test("zmx.list runs its zmx call on the worker while the main loop keeps turning")
    func zmxListLeavesTheMainLoopFree() throws {
        let runner = GatedZmxRunner()
        let previous = gZmxClient
        gZmxClient = runner.client()
        defer { gZmxClient = previous }
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket)
        server.start()
        defer { server.stop() }
        let client = try ControlTestClient(path: directory.socket)
        defer { client.close() }
        let reply = TestSlot<ControlResponse?>()
        Thread.detachNewThread {
            client.send(#"{"cmd":"zmx.list"}"# + "\n")
            reply.set(client.response(timeout: 20))
        }

        #expect(pumpGLib(timeout: 5) { !runner.startedArguments.isEmpty })
        #expect(runner.startedArguments == [["list"]])
        #expect(mainLoopTurns(within: 2))
        #expect(runner.finishedCount == 0)
        #expect(reply.value == nil)

        runner.open()
        #expect(pumpGLib(timeout: 10) { reply.value != nil })
        // The zmx call ran first; the model join hops back to main, where no window is open in a test.
        #expect(reply.value == Self.noController)
        #expect(runner.finishedCount == 1)
    }

    @Test("a lead-pane type runs its zmx calls on the worker and finishes once on the main thread")
    func leadTypeLeavesTheMainLoopFree() throws {
        let runner = GatedZmxRunner()
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket)
        defer { server.stop() }
        let finishes = TestSlot<Int>()
        let finishedOnMain = TestSlot<Bool>()
        let done = ControlResponse(ok: true, result: ControlResult(id: "typed"))
        let stage = LinuxLeadPaneIO.typeStage(client: runner.client(), name: "agterm-pane", text: "echo hi") {
            finishes.set((finishes.value ?? 0) + 1)
            finishedOnMain.set(Thread.isMainThread)
            return done
        }
        let result = TestSlot<ControlResponse>()
        Thread.detachNewThread { result.set(server.perform(stage)) }

        #expect(waitUntil(timeout: 5) { !runner.startedArguments.isEmpty })
        #expect(runner.startedArguments.first?.first == "type")
        #expect(mainLoopTurns(within: 2))
        #expect(finishes.value == nil)

        runner.open(2)
        #expect(pumpGLib(timeout: 10) { result.value != nil })
        #expect(result.value == done)
        #expect(finishes.value == 1)
        #expect(finishedOnMain.value == true)
        pumpGLib(timeout: 0.2)
        #expect(finishes.value == 1)
    }

    @Test("stop during an in-flight zmx call answers the client exactly once")
    func stopDuringZmxCallRepliesOnce() throws {
        let runner = GatedZmxRunner()
        let previous = gZmxClient
        gZmxClient = runner.client()
        defer { gZmxClient = previous }
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket, timing: ControlServer.Timing(shutdownGrace: 0.3))
        server.start()
        defer { server.stop() }
        let client = try ControlTestClient(path: directory.socket)
        defer { client.close() }
        client.send(#"{"cmd":"zmx.list"}"# + "\n")
        #expect(pumpGLib(timeout: 5) { !runner.startedArguments.isEmpty })

        server.stop()
        runner.open()

        // Not pumping: the main hop after the zmx call is cancelled once the grace passes, the worker writes
        // that one reply and closes the connection.
        guard case .closed(let received) = client.readToClose(timeout: 5) else {
            Issue.record("the connection stayed open after stop")
            return
        }
        #expect(received.filter { $0 == 0x0A }.count == 1)
        #expect(try JSONDecoder().decode(ControlResponse.self, from: received.dropLast()) == MainReply.shutdown)
        // The queued hop now gets its turn: the cancel already won, so it starts no further zmx call.
        pumpGLib(timeout: 0.5)
        #expect(runner.startedArguments == [["list"]])
    }

    @Test("after stop no further worker step starts, and the reply resolves once")
    func stopSkipsLaterWorkerSteps() throws {
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket)
        let gate = DispatchSemaphore(value: 0)
        let inFirst = TestSlot<Bool>()
        let secondRan = TestSlot<Bool>()
        let stage = ControlStage.worker {
            inFirst.set(true)
            _ = gate.wait(timeout: .now() + 10)
            return .worker {
                secondRan.set(true)
                return .done(ControlResponse(ok: true))
            }
        }
        let result = TestSlot<ControlResponse>()
        Thread.detachNewThread { result.set(server.perform(stage)) }
        #expect(waitUntil(timeout: 5) { inFirst.value != nil })

        server.stop()
        gate.signal()

        #expect(waitUntil(timeout: 5) { result.value != nil })
        #expect(result.value == MainReply.shutdown)
        #expect(secondRan.value == nil)
    }

    @Test("a collected dispatch hands its stage to the worker; outside a dispatch it runs inline")
    func handoffCollectsOrRunsInline() {
        let answer = ControlResponse(ok: true, result: ControlResult(text: "from the stage"))
        let ranOn = TestSlot<Bool>()
        let stage = ControlStage.worker {
            ranOn.set(true)
            return .done(answer)
        }

        let collected = ControlStageHandoff.collect { ControlStageHandoff.handOff(stage) }
        #expect(ranOn.value == nil)
        #expect(collected.runInline() == answer)

        let plain = ControlStageHandoff.collect { ControlResponse(ok: false, error: "plain") }
        #expect(plain.runInline() == ControlResponse(ok: false, error: "plain"))

        #expect(ControlStageHandoff.handOff(.done(answer)) == answer)

        let outer = ControlStageHandoff.collect {
            let inner = ControlStageHandoff.collect { ControlStageHandoff.handOff(.done(ControlResponse(ok: true))) }
            #expect(inner.runInline() == ControlResponse(ok: true))
            return ControlStageHandoff.handOff(.done(answer))
        }
        #expect(outer.runInline() == answer)
    }
}
