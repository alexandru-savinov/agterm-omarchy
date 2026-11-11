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
    /// What each released invocation prints, from its arguments; a throw is a failed zmx call.
    private let output: @Sendable ([String]) throws -> String

    init(output: @escaping @Sendable ([String]) throws -> String = { _ in "" }) {
        self.output = output
    }

    var startedArguments: [[String]] { lock.withLock { started } }
    var finishedCount: Int { lock.withLock { finished } }

    func open(_ count: Int = 1) { for _ in 0..<count { gate.signal() } }

    func client() -> LinuxZmxClient {
        LinuxZmxClient(executablePath: "/zmx", socketDirectory: "/tmp/zmx-mainloop-test", timeout: 3) { invocation in
            self.lock.withLock { self.started.append(invocation.arguments) }
            // Bounded: a broken test fails on its own expectations, never hangs the suite.
            _ = self.gate.wait(timeout: .now() + 10)
            self.lock.withLock { self.finished += 1 }
            return try self.output(invocation.arguments)
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

    @Test("a type waiting on the typing lock types nothing once stop has begun, and replies once")
    func stopDropsTypeQueuedOnTheLock() throws {
        let runner = GatedZmxRunner()
        let client = runner.client()
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket, timing: ControlServer.Timing(shutdownGrace: 0.3))
        defer { server.stop() }
        let typed = ControlResponse(ok: true, result: ControlResult(id: "typed"))
        let firstStage = LinuxLeadPaneIO.typeStage(client: client, name: "agterm-pane", text: "first") { typed }
        let secondStage = LinuxLeadPaneIO.typeStage(client: client, name: "agterm-pane", text: "second") { typed }
        let first = TestSlot<ControlResponse>()
        let second = TestSlot<ControlResponse>()
        Thread.detachNewThread { first.set(server.perform(firstStage)) }
        #expect(waitUntil(timeout: 5) { runner.startedArguments.count == 1 })
        Thread.detachNewThread { second.set(server.perform(secondStage)) }
        // The second request passes perform's stop check at once, then parks on the lock the first holds.
        Thread.sleep(forTimeInterval: 0.3)
        #expect(runner.startedArguments.count == 1)

        server.stop()
        runner.open(4)

        #expect(pumpGLib(timeout: 10) { first.value != nil && second.value != nil })
        #expect(second.value == MainReply.shutdown)
        // Only the first request reached zmx; the queued one sent no bytes after stop.
        #expect(runner.startedArguments == [["type", "agterm-pane"]])
    }

    @Test("a kill that completes after a swap closes the killed pane in its new role, never the other")
    func killAfterSwapClosesTheKilledPane() throws {
        let run = try runKill(output: { "killed session \($0[1])" }) { fixture in
            #expect(fixture.store.swapPanes(fixture.session.id) == nil)
            // The role the kill was aimed at now holds the other pane, the one a role lookup would close.
            #expect(fixture.session.surface === fixture.other)
        }
        #expect(run.closed == ["right:killed"])
        #expect(run.reply == ControlResponse(ok: true, result: ControlResult(
            id: run.fixture.session.id.uuidString, text: "killed agterm-killed", pane: "left")))
    }

    @Test("a kill that completes after its slot was replaced under the same pane id closes nothing")
    func killAfterReplacementClosesNothing() throws {
        let replacement = KillPaneSurface(token: "replacement")
        let run = try runKill(output: { "killed session \($0[1])" }) { fixture in
            fixture.session.surface = replacement
            #expect(fixture.session.paneIdentity == fixture.claim.paneIdentity)
        }
        #expect(run.closed == nil)
        #expect(run.fixture.session.surface === replacement)
        #expect(run.reply?.ok == true)
    }

    @Test("a kill that completes after its pane was closed closes nothing")
    func killAfterCloseClosesNothing() throws {
        let run = try runKill(output: { "killed session \($0[1])" }) { fixture in
            fixture.session.surface = nil
        }
        #expect(run.closed == nil)
        #expect(run.reply?.ok == true)
    }

    @Test("a delayed stale-socket or failed kill closes neither pane")
    func unconfirmedKillClosesNothing() throws {
        let stale = try runKill(output: { "cleaned up stale session \($0[1])" }) { fixture in
            #expect(fixture.store.swapPanes(fixture.session.id) == nil)
        }
        #expect(stale.closed == nil)
        #expect(stale.reply?.ok == false)
        #expect(stale.reply?.error?.contains("agterm-killed") == true)

        let failed = try runKill(output: { _ in throw KillRunnerDown() }) { _ in }
        #expect(failed.closed == nil)
        #expect(failed.reply?.ok == false)
        #expect(failed.reply?.error?.hasPrefix("could not kill agterm-killed") == true)
    }

    /// Starts a kill of the left pane's daemon through the production stage and `ControlServer.perform`,
    /// changes the model with `whileBlocked` while zmx is held, then releases zmx and waits for the reply.
    private func runKill(output: @escaping @Sendable ([String]) throws -> String,
                         whileBlocked change: (KillFixture) -> Void) throws
        -> (reply: ControlResponse?, closed: [String]?, fixture: KillFixture) {
        let fixture = try KillFixture()
        let runner = GatedZmxRunner { arguments in try arguments.first == "kill" ? output(arguments) : "" }
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket)
        defer { server.stop() }
        let closed = TestSlot<[String]>()
        let session = fixture.session
        let stage = ZmxKillStage.stage(client: runner.client(), daemon: "agterm-killed",
                                       target: ZmxKillTarget(claim: fixture.claim, in: session),
                                       session: { _ in session }) { role, surface in
            closed.set((closed.value ?? []) + ["\(role.rawValue):\(surface.paneToken)"])
        }
        let reply = TestSlot<ControlResponse>()
        Thread.detachNewThread { reply.set(server.perform(stage)) }
        #expect(waitUntil(timeout: 5) { !runner.startedArguments.isEmpty })
        #expect(runner.startedArguments == [["kill", "agterm-killed", "--force"]])

        change(fixture)
        runner.open()

        #expect(pumpGLib(timeout: 10) { reply.value != nil })
        return (reply.value, closed.value, fixture)
    }
}

private struct KillRunnerDown: Error {}

@MainActor
private final class KillPaneSurface: PaneRoleMutableSurface {
    let paneToken: String
    var isRealized: Bool { true }
    var backedByZmx: Bool { true }
    init(token: String) { paneToken = token }
    func teardown() {}
    func promoteToPrimaryPane() {}
    func setPaneRole(_ role: SwappablePaneRole) {}
}

/// A split session whose left pane ("killed") is the kill target and whose right pane ("other") must survive.
@MainActor
private struct KillFixture {
    let store: AppStore
    let session: Session
    let killed = KillPaneSurface(token: "killed")
    let other = KillPaneSurface(token: "other")
    let claim: ZmxPaneClaim

    init() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("agterm-kill-\(UUID().uuidString)")
        let workspace = Workspace(name: "work", sessions: [])
        store = AppStore(workspaces: [workspace], selectedSessionID: nil, persistence: PersistenceStore(directory: directory))
        session = try #require(store.addSession(toWorkspace: workspace.id, cwd: "/tmp"))
        session.surface = killed
        session.splitSurface = other
        session.splitPaneIdentity = UUID()
        session.hasSplit = true
        claim = ZmxPaneClaim(paneIdentity: session.paneIdentity, pane: .left, pendingClose: false,
                             windowID: UUID(), windowName: nil, windowState: .open, workspaceID: workspace.id,
                             workspaceName: "work", sessionID: session.id, sessionName: nil)
    }
}
