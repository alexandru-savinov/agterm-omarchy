import ArgumentParser
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif
import Foundation
import Testing
import agtermCore
@testable import agtermctlKit

/// The fork's SocketClient additions: bounded connect, write and read deadlines (`SocketClient.timeout`), and
/// `recent.clear` counting items.
struct SocketClientDeadlineTests {
    /// The connect hint for a refused or missing socket whose ownership lock is held. Linux has no
    /// non-competing `flock` probe, so its hint stays generic.
    static let heldLockHint: String = {
        #if os(Linux)
        "agterm may be stopped or unable to accept connections"
        #else
        "the socket owner is present but not accepting connections"
        #endif
    }()

    @Test(arguments: [(1, "1 item"), (2, "2 items"), (0, "0 items")])
    func formatResponseAffectedItems(_ affected: Int, _ expected: String) {
        let response = ControlResponse(ok: true, result: ControlResult(affected: affected))
        #expect(SocketClient.formatResponse(response, affectedNoun: "item") == expected)
    }

    @Test func recentClearRunLabelsAffectedEntriesAsItems() throws {
        let server = StubServer(response: ControlResponse(ok: true, result: ControlResult(affected: 2)))
        try server.start()
        defer { server.stop() }
        let run = try SocketClientTests().runCLI(["recent", "clear", "--socket", server.path])
        #expect(run.status == 0)
        #expect(run.out == Data("2 items\n".utf8))
    }

    @Test func silentResponseHasABoundedDeadline() throws {
        let release = DispatchSemaphore(value: 0)
        let server = StubServer(line: Data(), serve: { socket in
            consumeFixtureRequest(socket)
            _ = release.wait(timeout: .now() + 1)
        })
        try server.start()
        defer { release.signal(); server.stop() }
        let error = try #require(throws: SocketClientError.self) {
            _ = try SocketClient(path: server.path, timeout: 0.15).send(ControlRequest(cmd: .tree))
        }
        #expect(error.description.contains("timed out"))
        #expect(error.description.contains("may have completed"))
    }

    @Test func partialAndDrippingResponsesDoNotRenewTheDeadline() throws {
        for dripping in [false, true] {
            let release = DispatchSemaphore(value: 0)
            let server = StubServer(line: Data(), serve: { socket in
                consumeFixtureRequest(socket)
                guard sendFixtureBytes(Data("{".utf8), socket: socket) else { return }
                for _ in 0..<20 {
                    if release.wait(timeout: .now() + 0.05) == .success { return }
                    if dripping, !sendFixtureBytes(Data(" ".utf8), socket: socket) { return }
                }
            })
            try server.start()
            defer { release.signal(); server.stop() }
            let error = try #require(throws: SocketClientError.self) {
                _ = try SocketClient(path: server.path, timeout: 0.2).send(ControlRequest(cmd: .tree))
            }
            #expect(error.description.contains("timed out"))
        }
    }

    @Test func aPeerThatDoesNotReadCannotBlockTheRequestWrite() throws {
        let release = DispatchSemaphore(value: 0)
        let server = StubServer(line: Data(), serve: { _ in
            _ = release.wait(timeout: .now() + 1)
        })
        try server.start()
        defer { release.signal(); server.stop() }
        let request = ControlRequest(cmd: .sessionType, target: "active",
                                     args: ControlArgs(text: String(repeating: "x", count: 600_000)))
        let error = try #require(throws: SocketClientError.self) {
            _ = try SocketClient(path: server.path, timeout: 0.2).send(request)
        }
        #expect(error.description.contains("timed out"))
    }

    @Test func successfulResponseAndEOFKeepTheirExistingSemantics() throws {
        for line in ["", "{\"ok\":true}", "{\"ok\":true}\n"] {
            let server = StubServer(line: Data(), serve: { socket in
                consumeFixtureRequest(socket)
                _ = sendFixtureBytes(Data(line.utf8), socket: socket)
            })
            try server.start()
            defer { server.stop() }
            let client = SocketClient(path: server.path, timeout: 1)
            if line.isEmpty {
                let error = try #require(throws: SocketClientError.self) {
                    _ = try client.send(ControlRequest(cmd: .tree))
                }
                #expect(error.description.contains("no response"))
            } else {
                #expect(try client.send(ControlRequest(cmd: .tree)).response.ok)
            }
        }
    }

    @Test func connectedStreamRemainsBlockingAndCanOutliveTheConnectBudget() throws {
        let server = StubServer(line: Data(), serve: { socket in
            Thread.sleep(forTimeInterval: 0.3)
            _ = sendFixtureBytes(Data([42]), socket: socket)
        })
        try server.start()
        defer { server.stop() }
        let socket = try SocketClient(path: server.path, timeout: 0.1).connect()
        defer { close(socket) }
        #expect(fcntl(socket, F_GETFL) & O_NONBLOCK == 0)
        var readTimeout = timeval()
        var size = socklen_t(MemoryLayout<timeval>.size)
        #expect(getsockopt(socket, SOL_SOCKET, SO_RCVTIMEO, &readTimeout, &size) == 0)
        #expect(readTimeout.tv_sec == 0 && readTimeout.tv_usec == 0)
        var byte: UInt8 = 0
        #expect(read(socket, &byte, 1) == 1)
        #expect(byte == 42)
    }

    #if os(Linux)
    @Test func fullUnixBacklogHasABoundedConnectDeadline() throws {
        let server = try RefusedSocket()
        defer { server.stop() }
        let listening = try server.startListening()
        let queued = try SocketClient(path: server.path, timeout: 1).connect()
        defer { close(queued) }
        let release = DispatchSemaphore(value: 0)
        let finished = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            _ = release.wait(timeout: .now() + 1)
            let accepted = systemAccept(listening)
            if accepted >= 0 { close(accepted) }
            finished.signal()
        }
        defer { release.signal(); finished.wait() }
        let error = try #require(throws: SocketClientError.self) {
            let unexpected = try SocketClient(path: server.path, timeout: 0.15).connect()
            close(unexpected)
        }
        #expect(error.description.contains("timed out waiting to connect"))
        #expect(!error.description.contains("may have completed"))
    }
    #endif
}

private func consumeFixtureRequest(_ socket: Int32) {
    var timeout = timeval(tv_sec: 1, tv_usec: 0)
    _ = setsockopt(socket, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
    var byte: UInt8 = 0
    while read(socket, &byte, 1) == 1 {
        if byte == UInt8(ascii: "\n") { return }
    }
}

private func sendFixtureBytes(_ data: Data, socket: Int32) -> Bool {
    #if canImport(Darwin)
    var noSigPipe: Int32 = 1
    _ = setsockopt(socket, SOL_SOCKET, SO_NOSIGPIPE, &noSigPipe, socklen_t(MemoryLayout<Int32>.size))
    #endif
    return data.withUnsafeBytes { buffer in
        guard let base = buffer.baseAddress, !buffer.isEmpty else { return true }
        #if canImport(Darwin)
        return send(socket, base, buffer.count, 0) == buffer.count
        #else
        return Glibc.send(socket, base, buffer.count, Int32(MSG_NOSIGNAL)) == buffer.count
        #endif
    }
}
