import Foundation
import Glibc
import Testing
import agtermCore
@testable import AgtermLinux

/// Records what a `LinuxControlStreamOwner` delivers from its reader thread.
private final class StreamProbe: @unchecked Sendable {
    private let lock = NSLock()
    private var lines: [Data] = []
    private var closes = 0

    var received: [Data] {
        lock.lock()
        defer { lock.unlock() }
        return lines
    }

    var closeCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return closes
    }

    func line(_ data: Data) {
        lock.lock()
        lines.append(data)
        lock.unlock()
    }

    func closed() {
        lock.lock()
        closes += 1
        lock.unlock()
    }
}

/// One owner over a socketpair. `peer` is the test's end; the owner closes its own end.
private struct OwnedStream {
    let owner: LinuxControlStreamOwner
    let descriptor: Int32
    let inode: UInt64
    let peer: ControlTestClient
    let probe = StreamProbe()

    init() throws {
        let (mine, theirs) = try makeSocketPair()
        descriptor = mine
        inode = try #require(socketInode(mine))
        peer = ControlTestClient(fd: theirs)
        owner = LinuxControlStreamOwner(descriptor: mine)
        let sink = probe
        owner.start(onLine: { sink.line($0) }, onClose: { sink.closed() })
    }

    /// The owner's socket is gone: its number is free or now names a different file.
    var descriptorClosed: Bool { socketInode(descriptor) != inode }

    func waitForClose(timeout: TimeInterval) -> Bool {
        waitUntil(timeout: timeout) { probe.closeCount > 0 }
    }
}

private func socketInode(_ fd: Int32) -> UInt64? {
    var info = stat()
    guard fstat(fd, &info) == 0 else { return nil }
    return UInt64(info.st_ino)
}

private func frame(_ index: Int) -> Data { Data("frame-\(index)\n".utf8) }

/// Counts worker threads that finished.
private final class Finished: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0

    var value: Int {
        lock.lock()
        defer { lock.unlock() }
        return count
    }

    func increment() {
        lock.lock()
        count += 1
        lock.unlock()
    }
}

@Suite("Linux control stream owner")
struct LinuxControlStreamOwnerTests {
    @Test("peer EOF closes the stream once and releases the descriptor")
    func peerEOFClosesOnce() throws {
        let stream = try OwnedStream()

        stream.peer.close()

        #expect(stream.waitForClose(timeout: 5))
        stream.owner.shutdown()
        Thread.sleep(forTimeInterval: 0.2)
        #expect(stream.probe.closeCount == 1)
        #expect(stream.descriptorClosed)
    }

    @Test("a failed write shuts the stream down and closes it once")
    func writerFailureClosesOnce() throws {
        let stream = try OwnedStream()
        defer { stream.peer.close() }
        #expect(Glibc.shutdown(stream.peer.fd, Int32(SHUT_RD)) == 0)

        #expect(stream.owner.send(frame(0)))

        #expect(stream.waitForClose(timeout: 5))
        Thread.sleep(forTimeInterval: 0.2)
        #expect(stream.probe.closeCount == 1)
        #expect(stream.descriptorClosed)
        #expect(!stream.owner.send(frame(1)))
    }

    @Test("concurrent and late shutdowns close once and never touch a reused descriptor number")
    func repeatedShutdownClosesOnce() throws {
        let stream = try OwnedStream()
        defer { stream.peer.close() }
        let callers = Finished()
        for _ in 0..<8 {
            Thread.detachNewThread {
                for _ in 0..<100 { stream.owner.shutdown() }
                callers.increment()
            }
        }
        #expect(waitUntil(timeout: 5) { callers.value == 8 })
        #expect(stream.waitForClose(timeout: 5))
        #expect(stream.descriptorClosed)

        let (sentinel, sentinelPeer) = try makeSocketPair()
        defer {
            Glibc.close(sentinel)
            Glibc.close(sentinelPeer)
        }
        for _ in 0..<10 { stream.owner.shutdown() }

        #expect(stream.probe.closeCount == 1)
        #expect(fcntl(sentinel, F_GETFD) != -1)
        let probe = ControlTestClient(fd: sentinelPeer)
        #expect(ControlTestClient(fd: sentinel).send("alive\n"))
        #expect(probe.readLine(timeout: 2) == .line(Data("alive".utf8)))
    }

    @Test("fragmented and coalesced frames arrive in order, byte-exact")
    func framesSurviveFragmentation() throws {
        let stream = try OwnedStream()
        defer {
            stream.owner.shutdown()
            stream.peer.close()
        }

        for piece in ["ab", "c\nde", "f\n", "g\nh\ni\n", "\u{e9}\u{1F600}\n"] {
            #expect(stream.peer.send(piece))
            Thread.sleep(forTimeInterval: 0.05)
        }

        let expected = ["abc", "def", "g", "h", "i", "\u{e9}\u{1F600}"].map { Data($0.utf8) }
        #expect(waitUntil(timeout: 5) { stream.probe.received.count == expected.count })
        #expect(stream.probe.received == expected)
        #expect(stream.probe.closeCount == 0)
    }

    @Test("a frame of exactly maxFrameBytes is delivered")
    func maximumFrameIsDelivered() throws {
        let stream = try OwnedStream()
        defer {
            stream.owner.shutdown()
            stream.peer.close()
        }
        let payload = Data(repeating: UInt8(ascii: "m"), count: PresentationCodec.maxFrameBytes)
        let peer = stream.peer
        Thread.detachNewThread { _ = peer.send(payload + Data("\n".utf8)) }

        #expect(waitUntil(timeout: 5) { stream.probe.received.count == 1 })
        #expect(stream.probe.received.first == payload)
        #expect(stream.probe.closeCount == 0)
    }

    @Test("an oversized frame closes the stream, terminated or not", arguments: [true, false])
    func oversizedFrameCloses(terminated: Bool) throws {
        let stream = try OwnedStream()
        defer { stream.peer.close() }
        var payload = Data(repeating: UInt8(ascii: "o"), count: PresentationCodec.maxFrameBytes + 1)
        if terminated { payload.append(0x0A) }
        let peer = stream.peer
        Thread.detachNewThread { _ = peer.send(payload) }

        #expect(stream.waitForClose(timeout: 5))
        #expect(stream.probe.received.isEmpty)
        #expect(stream.descriptorClosed)
    }

    @Test("a stalled peer fills the queue at 64 frames and the send timeout closes the stream")
    func stalledPeerFillsQueueAndTimesOut() throws {
        let stream = try OwnedStream()
        defer { stream.peer.close() }
        let started = Date()
        #expect(stream.owner.send(Data(repeating: UInt8(ascii: "s"), count: 4 << 20)))
        Thread.sleep(forTimeInterval: 0.3)

        let accepted = (0..<65).map { stream.owner.send(frame($0)) }

        #expect(accepted.prefix(64).allSatisfy { $0 })
        #expect(accepted.last == false)
        #expect(stream.waitForClose(timeout: 20))
        let closedAfter = Date().timeIntervalSince(started)
        // one 5 s send timeout ends a partial write, a second ends the retry
        #expect(closedAfter > 4.5 && closedAfter < 16)
        #expect(stream.descriptorClosed)
    }

    @Test("a reading peer receives every queued frame")
    func readingPeerReceivesEveryFrame() throws {
        let stream = try OwnedStream()
        defer {
            stream.owner.shutdown()
            stream.peer.close()
        }
        let expected = (0..<64).reduce(into: Data()) { $0.append(frame($1)) }

        let accepted = (0..<64).map { stream.owner.send(frame($0)) }

        #expect(accepted.allSatisfy { $0 })
        #expect(stream.peer.read(byteCount: expected.count, timeout: 5) == expected)
        #expect(stream.probe.closeCount == 0)
    }

    @MainActor
    @Test("a full queue makes the presentation offer fail and the hub drop the stalled viewer")
    func fullQueueStallsThePresentationViewer() throws {
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket)
        defer { server.stop() }
        let stream = try OwnedStream()
        defer { stream.peer.close() }
        #expect(stream.owner.send(Data(repeating: UInt8(ascii: "s"), count: 4 << 20)))
        Thread.sleep(forTimeInterval: 0.3)
        for index in 0..<64 { _ = stream.owner.send(frame(index)) }
        let session = UUID()
        let viewer = LinuxPresentationStream(session: session, owner: stream.owner, server: server)
        let hub = PresentationHub(staleTimeout: 30)
        let hello = PresentationHello(version: PresentationCodec.version, kinds: [], mode: .mirror)

        #expect(!viewer.offer(PresentationFrame(gen: 1, rev: 0, body: .hello(hello))))
        _ = try hub.subscribe(session: session, hello: hello, sink: viewer) {
            PresentationSnapshot(status: nil, hud: nil)
        }

        #expect(hub.subscriberCount(session: session) == 0)
        #expect(stream.waitForClose(timeout: 3))
        #expect(stream.descriptorClosed)
    }
}
