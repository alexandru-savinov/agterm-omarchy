import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

/// A helper connection that claimed a job: its reply, and a reader positioned after it.
private struct ClaimedHelper {
    let reply: ControlResponse?
    let reader: ControlLineReader
    var client: ControlTestClient { reader.client }

    func frame(timeout: TimeInterval = 5) -> OverlayJobFrame? {
        reader.next(timeout: timeout).flatMap { try? JSONDecoder().decode(OverlayJobFrame.self, from: $0) }
    }

    @discardableResult
    func send(_ frames: [OverlayJobFrame]) -> Bool {
        client.send(frames.reduce(into: Data()) { $0.append(try! $1.line()) })
    }
}

/// A started `ControlServer` with registered overlay jobs, driven over its real socket.
@MainActor
private final class OverlayJobFixture {
    let directory: ControlSocketDirectory
    let server: ControlServer
    let context = OverlayLaunchContext(command: "true", cwd: "/tmp", sessionEnvironment: [:])
    private(set) var finished: [String] = []
    private var helpers: [ControlTestClient] = []

    init() throws {
        directory = try ControlSocketDirectory()
        server = ControlServer(path: directory.socket)
        server.start()
        server.attachPresentationHub()
        let forward = server.overlayJobs.onFinished
        server.overlayJobs.onFinished = { [weak self] job in
            self?.finished.append(job.id)
            forward?(job)
        }
    }

    func tearDown() {
        for helper in helpers { helper.close() }
        server.shutdownOverlayJobs()
        server.stop()
        pumpGLib(timeout: 0.2)
        directory.remove()
    }

    func register() -> String {
        server.overlayJobs.register(session: UUID(), pane: nil, owner: 1, context: context)
    }

    func state(_ job: String) -> OverlayJobState? { server.overlayJobs.job(job)?.state }

    func connect() throws -> ControlTestClient {
        let client = try ControlTestClient(path: directory.socket)
        helpers.append(client)
        return client
    }

    /// Closes a helper now instead of at teardown, so its descriptor is closed exactly once.
    func hangUp(_ helper: ClaimedHelper) {
        helpers.removeAll { $0.fd == helper.client.fd }
        helper.client.close()
    }

    nonisolated static func runLine(_ job: String) -> String {
        String(decoding: try! JSONEncoder().encode(ControlRequest(cmd: .sessionOverlayJobRun, target: job)), as: UTF8.self)
            + "\n"
    }

    /// Claims `job` and, when the claim succeeds, pumps until the server has adopted the stream.
    func claim(_ job: String, beforeAdoption: (() -> Void)? = nil) throws -> ClaimedHelper {
        let reader = ControlLineReader(try connect())
        let reply = TestSlot<ControlResponse?>()
        Thread.detachNewThread {
            reader.client.send(Self.runLine(job))
            reply.set(reader.next(timeout: 10).flatMap { try? JSONDecoder().decode(ControlResponse.self, from: $0) })
        }
        if let beforeAdoption {
            #expect(pumpGLib(timeout: 5) { self.isClaimed(job) })
            beforeAdoption()
        }
        #expect(pumpGLib(timeout: 10) { reply.value != nil })
        let response = reply.value ?? nil
        if response?.ok == true {
            #expect(pumpGLib(timeout: 5) { self.server.overlayJobStreams[job] != nil })
        }
        return ClaimedHelper(reply: response, reader: reader)
    }

    func isClaimed(_ job: String) -> Bool {
        if case .claimed = state(job) { return true }
        return false
    }

    func finishCount(_ job: String) -> Int { finished.count { $0 == job } }
}

@MainActor
@Suite("Linux overlay-job helper connections", .serialized)
struct ControlServerOverlayJobTests {
    private static let notClaimable = ControlResponse(ok: false, error: "job not claimable")

    @Test("a claimed and adopted helper receives the launch context")
    func claimDeliversContext() throws {
        let fixture = try OverlayJobFixture()
        defer { fixture.tearDown() }
        let job = fixture.register()

        let helper = try fixture.claim(job)

        #expect(helper.reply == ControlResponse(ok: true, result: ControlResult(id: job)))
        #expect(helper.frame() == .context(fixture.context))
        #expect(fixture.isClaimed(job))
    }

    @Test("a helper gone before its claim reply releases the claim as unknown")
    func helperGoneBeforeReply() throws {
        let fixture = try OverlayJobFixture()
        defer { fixture.tearDown() }
        let job = fixture.register()
        let client = try ControlTestClient(path: fixture.directory.socket)
        client.send(OverlayJobFixture.runLine(job))
        client.close()

        #expect(pumpGLib(timeout: 10) { fixture.state(job) == .finished(.unknown) })
        #expect(fixture.server.overlayJobStreams[job] == nil)

        #expect(try fixture.claim(job).reply == Self.notClaimable)
    }

    @Test("a helper that drops after adoption ends its job unknown")
    func helperGoneAfterAdoption() throws {
        let fixture = try OverlayJobFixture()
        defer { fixture.tearDown() }
        let job = fixture.register()
        let helper = try fixture.claim(job)
        #expect(helper.frame() == .context(fixture.context))

        fixture.hangUp(helper)

        #expect(pumpGLib(timeout: 10) { fixture.state(job) == .finished(.unknown) })
        #expect(pumpGLib(timeout: 5) { fixture.server.overlayJobStreams[job] == nil })
        #expect(fixture.finishCount(job) == 1)
        #expect(try fixture.claim(job).reply == Self.notClaimable)
    }

    @Test("a cancel before adoption reaches the helper right after its context")
    func cancelBeforeAdoption() throws {
        let fixture = try OverlayJobFixture()
        defer { fixture.tearDown() }
        let job = fixture.register()

        let helper = try fixture.claim(job) {
            fixture.server.overlayJobs.cancel(job)
            #expect(fixture.server.pendingJobCancels == [job])
        }

        #expect(helper.frame() == .context(fixture.context))
        #expect(helper.frame() == .cancel)
        #expect(fixture.server.pendingJobCancels.isEmpty)

        let other = fixture.register()
        let plain = try fixture.claim(other)
        #expect(plain.frame() == .context(fixture.context))
        #expect(plain.reader.next(timeout: 0.5) == nil)
    }

    @Test("malformed or origin-only frames close the helper stream", arguments: [
        "not json\n",
        #"{"kind":"bogus"}"# + "\n",
        String(decoding: try! OverlayJobFrame.cancel.line(), as: UTF8.self),
        String(decoding: try! OverlayJobFrame.context(
            OverlayLaunchContext(command: "true", cwd: "/tmp", sessionEnvironment: [:])).line(), as: UTF8.self),
    ])
    func forbiddenFrameCloses(line: String) throws {
        let fixture = try OverlayJobFixture()
        defer { fixture.tearDown() }
        let job = fixture.register()
        let helper = try fixture.claim(job)
        #expect(helper.frame() == .context(fixture.context))

        helper.client.send(line)

        #expect(pumpGLib(timeout: 10) { fixture.state(job) == .finished(.unknown) })
        #expect(helper.reader.next(timeout: 2) == nil)
        #expect(fixture.finishCount(job) == 1)
    }

    @Test("exited before started finishes once, and a duplicate exited changes nothing")
    func exitedBeforeStartedAndDuplicate() throws {
        let fixture = try OverlayJobFixture()
        defer { fixture.tearDown() }
        let job = fixture.register()
        let helper = try fixture.claim(job)
        #expect(helper.frame() == .context(fixture.context))

        helper.send([.exited(3)])
        #expect(pumpGLib(timeout: 5) { fixture.state(job) == .finished(.exited(3)) })
        helper.send([.exited(4), .started])
        pumpGLib(timeout: 0.5)

        #expect(fixture.state(job) == .finished(.exited(3)))
        fixture.hangUp(helper)
        #expect(pumpGLib(timeout: 5) { fixture.server.overlayJobStreams[job] == nil })
        #expect(fixture.state(job) == .finished(.exited(3)))
        #expect(fixture.finishCount(job) == 1)
    }

    @Test("a valid exited behind a malformed line in the same write does not count")
    func exitedBehindMalformedLineIsDropped() throws {
        let fixture = try OverlayJobFixture()
        defer { fixture.tearDown() }
        let job = fixture.register()
        let helper = try fixture.claim(job)
        #expect(helper.frame() == .context(fixture.context))

        helper.client.send(Data("not json\n".utf8) + (try OverlayJobFrame.exited(0).line()))

        #expect(pumpGLib(timeout: 10) { fixture.server.overlayJobStreams[job] == nil })
        #expect(fixture.state(job) == .finished(.unknown))
        #expect(fixture.finishCount(job) == 1)
    }

    @Test("the start window expires a silent claim on the GLib timer, while a started job keeps running")
    func claimExpiresOnTheGLibTimer() throws {
        try withGLibMainTimer {
            let fixture = try OverlayJobFixture()
            defer { fixture.tearDown() }
            let running = fixture.register()
            let silent = fixture.register()
            let runningHelper = try fixture.claim(running)
            #expect(runningHelper.frame() == .context(fixture.context))
            runningHelper.send([.started])
            #expect(pumpGLib(timeout: 5) { fixture.state(running) == .running })
            let claimed = Date()
            let silentHelper = try fixture.claim(silent)
            #expect(silentHelper.frame() == .context(fixture.context))

            #expect(pumpGLib(timeout: OverlayJobs.startWindow + 5) { fixture.state(silent) == .finished(.unknown) })
            #expect(Date().timeIntervalSince(claimed) >= OverlayJobs.startWindow)
            #expect(fixture.state(running) == .running)

            silentHelper.send([.started, .exited(0)])
            pumpGLib(timeout: 0.5)
            #expect(fixture.state(silent) == .finished(.unknown))
            #expect(fixture.finishCount(silent) == 1)
            #expect(fixture.finishCount(running) == 0)
        }
    }
}
