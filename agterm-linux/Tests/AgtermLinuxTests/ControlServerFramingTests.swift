import Foundation
import Glibc
import Testing
import agtermCore
@testable import AgtermLinux

/// Drives a real `ControlServer` socket. Serialized and main-actor because valid requests hop to the GLib
/// default context, which only the test thread pumps.
@MainActor
@Suite("Linux control socket framing and bounds", .serialized)
struct ControlServerFramingTests {
    private static let noController = ControlResponse(ok: false, error: "no controller")
    private static let undecodable = ControlResponse(ok: false, error: "could not decode request")

    @Test("a request of exactly 1 MiB is read and dispatched")
    func maximumLineIsDispatched() throws {
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket)
        server.start()
        defer { server.stop() }
        let prefix = #"{"cmd":"version""#
        let body = prefix + String(repeating: " ", count: (1 << 20) - prefix.utf8.count - 1) + "}"
        #expect(body.utf8.count == 1 << 20)

        let client = try ControlTestClient(path: directory.socket)
        defer { client.close() }
        let reply = TestSlot<ControlResponse?>()
        Thread.detachNewThread {
            client.send(body + "\n")
            reply.set(client.response(timeout: 30))
        }

        #expect(pumpGLib(timeout: 40) { reply.value != nil })
        #expect(reply.value == Self.noController)
    }

    @Test("one byte over 1 MiB closes the connection without a response")
    func oversizedLineIsDropped() throws {
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket)
        server.start()
        defer { server.stop() }
        let client = try ControlTestClient(path: directory.socket)
        defer { client.close() }

        client.send(Data(repeating: UInt8(ascii: "x"), count: (1 << 20) + 1) + Data("\n".utf8))

        #expect(client.readToClose(timeout: 30) == .closed(Data()))
    }

    @Test("malformed JSON is refused without a main-thread dispatch")
    func malformedRequestIsRefused() throws {
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket)
        server.start()
        defer { server.stop() }

        for line in [#"{"cmd":"#, #"{"cmd":"no.such.command"}"#] {
            let client = try ControlTestClient(path: directory.socket)
            defer { client.close() }
            client.send(line + "\n")
            #expect(client.response(timeout: 5) == Self.undecodable)
        }
    }

    @Test("a connection carries one request, and a fresh client is served after it")
    func oneRequestPerConnection() throws {
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket)
        server.start()
        defer { server.stop() }
        let first = try ControlTestClient(path: directory.socket)
        defer { first.close() }

        first.send("not json\nstill not json\n")

        guard case .closed(let received) = first.readToClose(timeout: 5) else {
            Issue.record("the server kept the connection open")
            return
        }
        #expect(received.filter { $0 == 0x0A }.count == 1)
        #expect(try JSONDecoder().decode(ControlResponse.self, from: received.dropLast()) == Self.undecodable)

        let fresh = try ControlTestClient(path: directory.socket)
        defer { fresh.close() }
        let reply = TestSlot<ControlResponse?>()
        Thread.detachNewThread {
            fresh.send(#"{"cmd":"version"}"# + "\n")
            reply.set(fresh.response(timeout: 5))
        }
        #expect(pumpGLib(timeout: 10) { reply.value != nil })
        #expect(reply.value == Self.noController)
    }

    @Test("silent and drip-feeding clients do not starve a valid client")
    func slowClientsDoNotStarveOthers() throws {
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket)
        server.start()
        defer { server.stop() }
        let started = Date()
        let silent = try ControlTestClient(path: directory.socket)
        defer { silent.close() }
        let drip = try ControlTestClient(path: directory.socket)
        defer { drip.close() }
        let stopDrip = TestSlot<Bool>()
        let dripDone = TestSlot<Bool>()
        Thread.detachNewThread {
            while stopDrip.value == nil, drip.send(" ") { Thread.sleep(forTimeInterval: 1) }
            dripDone.set(true)
        }
        defer {
            stopDrip.set(true)
            waitUntil(timeout: 5) { dripDone.value != nil }
        }

        let valid = try ControlTestClient(path: directory.socket)
        defer { valid.close() }
        let reply = TestSlot<ControlResponse?>()
        Thread.detachNewThread {
            valid.send(#"{"cmd":"version"}"# + "\n")
            reply.set(valid.response(timeout: 5))
        }
        #expect(pumpGLib(timeout: 3) { reply.value != nil })
        #expect(reply.value == Self.noController)

        #expect(waitUntil(timeout: 10) { silent.isClosedByPeer })
        let silentClosedAfter = Date().timeIntervalSince(started)
        #expect(silentClosedAfter > 4.5 && silentClosedAfter < 8)
        // a byte a second outlives the 5 s idle limit; only the request deadline ends it
        #expect(!drip.isClosedByPeer)
    }

    @Test("a drip-feeding client is closed at the request deadline; a slow complete request is still served")
    func dripFeedHitsTheRequestDeadline() throws {
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket, timing: ControlServer.Timing(requestDeadline: 1.5))
        server.start()
        defer { server.stop() }
        let started = Date()
        let drip = try ControlTestClient(path: directory.socket)
        defer { drip.close() }
        let dripDone = TestSlot<Bool>()
        Thread.detachNewThread {
            for _ in 0..<40 {
                guard drip.send(" ") else { break }
                Thread.sleep(forTimeInterval: 0.25)
            }
            dripDone.set(true)
        }
        defer { waitUntil(timeout: 15) { dripDone.value != nil } }
        let slow = try ControlTestClient(path: directory.socket)
        defer { slow.close() }

        for piece in [#"{"cmd""#, #":"no.such"#, "\"}\n"] {
            slow.send(piece)
            Thread.sleep(forTimeInterval: 0.3)
        }
        #expect(slow.response(timeout: 5) == Self.undecodable)

        #expect(waitUntil(timeout: 8) { drip.isClosedByPeer })
        let closedAfter = Date().timeIntervalSince(started)
        #expect(closedAfter > 1.3 && closedAfter < 4)
    }

    @Test("stop ends the accept loop, and the same server can start and serve again")
    func stopEndsTheAcceptLoop() throws {
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket)
        defer { server.stop() }

        server.start()
        #expect(server.activeAcceptLoops == 1)
        server.stop()
        #expect(server.activeAcceptLoops == 0)
        #expect(!FileManager.default.fileExists(atPath: directory.socket))

        server.start()
        #expect(server.activeAcceptLoops == 1)
        #expect(server.boundSocketPath == directory.socket)
        let client = try ControlTestClient(path: directory.socket)
        defer { client.close() }
        client.send("not json\n")
        #expect(client.response(timeout: 5) == Self.undecodable)
        server.stop()
        #expect(server.activeAcceptLoops == 0)
    }

    @Test("a stale stop leaves a newer owner's socket alone")
    func staleStopKeepsTheNewerSocket() throws {
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let first = ControlServer(path: directory.socket)
        first.start()
        first.stop()
        let second = ControlServer(path: directory.socket)
        second.start()
        defer { second.stop() }

        first.stop()

        #expect(second.boundSocketPath == directory.socket)
        #expect(FileManager.default.fileExists(atPath: directory.socket))
        let client = try ControlTestClient(path: directory.socket)
        defer { client.close() }
        client.send("not json\n")
        #expect(client.response(timeout: 5) == Self.undecodable)
    }

    @Test("a request the main loop never serves resolves after stop's grace")
    func pendingRequestResolvesAfterStop() throws {
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket, timing: ControlServer.Timing(shutdownGrace: 0.5))
        let result = TestSlot<ControlResponse>()
        Thread.detachNewThread { result.set(server.performRequest(ControlRequest(cmd: .version))) }

        Thread.sleep(forTimeInterval: 0.5)
        #expect(result.value == nil)
        let stopped = Date()
        server.stop()

        #expect(waitUntil(timeout: 5) { result.value != nil })
        let elapsed = Date().timeIntervalSince(stopped)
        #expect(result.value == MainReply.shutdown)
        #expect(elapsed > 0.4 && elapsed < 3)
        pumpGLib(timeout: 0.2)
    }

    @Test("a reply the main loop delivers within the grace wins over shutdown")
    func replyWithinGraceWins() throws {
        let directory = try ControlSocketDirectory()
        defer { directory.remove() }
        let server = ControlServer(path: directory.socket, timing: ControlServer.Timing(shutdownGrace: 5))
        let result = TestSlot<ControlResponse>()
        Thread.detachNewThread { result.set(server.performRequest(ControlRequest(cmd: .version))) }
        Thread.sleep(forTimeInterval: 0.2)

        server.stop()

        #expect(pumpGLib(timeout: 3) { result.value != nil })
        #expect(result.value == Self.noController)
    }

    @Test("a main reply resolves exactly once whichever side finishes first")
    func mainReplyResolvesOnce() {
        let ok = ControlResponse(ok: true)

        let cancelledFirst = MainReply()
        cancelledFirst.cancel()
        #expect(!cancelledFirst.begin())
        cancelledFirst.resolve(ok)
        #expect(cancelledFirst.wait() == MainReply.shutdown)

        let cancelledMidTurn = MainReply()
        #expect(cancelledMidTurn.begin())
        cancelledMidTurn.cancel()
        cancelledMidTurn.resolve(ok)
        cancelledMidTurn.end()
        #expect(cancelledMidTurn.wait() == ok)

        let unresolvedMidTurn = MainReply()
        #expect(unresolvedMidTurn.begin())
        unresolvedMidTurn.cancel()
        unresolvedMidTurn.end()
        #expect(unresolvedMidTurn.wait() == MainReply.shutdown)

        let awaitingCallback = MainReply()
        #expect(awaitingCallback.begin())
        awaitingCallback.end()
        awaitingCallback.cancel()
        awaitingCallback.resolve(ok)
        #expect(awaitingCallback.wait() == MainReply.shutdown)
    }
}
