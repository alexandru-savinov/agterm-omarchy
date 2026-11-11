import ArgumentParser
import Foundation
import Testing
import agtermCore
#if canImport(Darwin)
import Darwin
#else
import Glibc
#endif
@testable import agtermctlKit

struct StreamHandshakeTests {
    private static let request = ControlRequest(cmd: .zmxPresent, target: "fixture")
    private static let ok = "{\"ok\":true}\n"

    @Test(arguments: ["", "{", "drip"])
    func stalledRepliesHaveAFixedDeadline(partial: String) throws {
        try withPeer({ peer in
            if partial == "drip" {
                for _ in 0..<30 {
                    guard StreamBridge.writeAll(peer, Data(" ".utf8)) else { break }
                    usleep(20_000)
                }
            } else {
                _ = StreamBridge.writeAll(peer, Data(partial.utf8))
            }
        }) { socket in
            let flags = fcntl(socket, F_GETFL)
            let bridge = StreamBridge(socket: socket, input: -1, output: -1)
            let start = ProcessInfo.processInfo.systemUptime
            expectTimeout {
                try bridge.open(Self.request, deadline: start + 0.15)
            }
            #expect(ProcessInfo.processInfo.systemUptime - start < 1)
            #expect(fcntl(socket, F_GETFL) == flags)
        }
    }

    @Test(arguments: [false, true])
    func contextSharesTheReplyBudget(partial: Bool) throws {
        let context = try OverlayJobFrame.context(OverlayLaunchContext(command: "exit 7", cwd: "/tmp", sessionEnvironment: [:])).line()
        try withPeer({ peer in
            usleep(180_000)
            _ = StreamBridge.writeAll(peer, Data(Self.ok.utf8))
            if partial {
                for _ in 0..<13 {
                    guard StreamBridge.writeAll(peer, Data(" ".utf8)) else { break }
                    usleep(20_000)
                }
            } else {
                usleep(260_000)
            }
            _ = StreamBridge.writeAll(peer, context)
        }) { socket in
            let flags = fcntl(socket, F_GETFL)
            let start = ProcessInfo.processInfo.systemUptime
            expectTimeout {
                _ = try OverlayJobRunner(socket: socket).claim("fixture", deadline: start + 0.3)
            }
            // A reset context budget would accept the complete frame sent at about 0.44 seconds.
            #expect(ProcessInfo.processInfo.systemUptime - start < 1)
            #expect(fcntl(socket, F_GETFL) == flags)
        }
    }

    @Test func stalledRequestWriteIsBounded() throws {
        try withPeer({ _ in }) { socket in
            var size: Int32 = 1024
            #expect(setsockopt(socket, SOL_SOCKET, SO_SNDBUF, &size, socklen_t(MemoryLayout<Int32>.size)) == 0)
            let flags = fcntl(socket, F_GETFL)
            let request = ControlRequest(cmd: .zmxPresent, target: String(repeating: "x", count: 600_000))
            expectTimeout {
                try StreamBridge(socket: socket, input: -1, output: -1)
                    .open(request, deadline: ProcessInfo.processInfo.systemUptime + 0.15)
            }
            #expect(fcntl(socket, F_GETFL) == flags)
        }
    }

    @Test func replyContextAndFollowingFrameRemainSeparate() throws {
        let context = OverlayLaunchContext(command: "exit 7", cwd: "/tmp", sessionEnvironment: [:])
        let bytes = Data(Self.ok.utf8) + (try OverlayJobFrame.context(context).line()) + Data("next-frame\n".utf8)
        try withPeer({ peer in _ = StreamBridge.writeAll(peer, bytes) }) { socket in
            let flags = fcntl(socket, F_GETFL)
            let result = try OverlayJobRunner(socket: socket).claim("fixture")
            #expect(result == context)
            #expect(fcntl(socket, F_GETFL) == flags)
            var next = [UInt8](repeating: 0, count: 11)
            let count = read(socket, &next, next.count)
            #expect(count == 11)
            #expect(String(decoding: next.prefix(max(0, count)), as: UTF8.self) == "next-frame\n")
        }
    }

    @Test func healthyIdleStreamOutlivesHandshakeDeadline() throws {
        try withPeer({ peer in
            _ = StreamBridge.writeAll(peer, Data(Self.ok.utf8))
            usleep(250_000)
            _ = StreamBridge.writeAll(peer, Data("later-frame\n".utf8))
            shutdown(peer, Int32(SHUT_WR))
        }) { socket in
            var input: [Int32] = [-1, -1]
            var output: [Int32] = [-1, -1]
            try #require(pipe(&input) == 0)
            defer { close(input[0]); close(input[1]) }
            try #require(pipe(&output) == 0)
            defer { close(output[0]); close(output[1]) }
            let bridge = StreamBridge(socket: socket, input: input[0], output: output[1])
            try bridge.open(Self.request, deadline: ProcessInfo.processInfo.systemUptime + 0.1)
            #expect(fcntl(socket, F_GETFL) & O_NONBLOCK == 0)
            bridge.pump()
            var bytes = [UInt8](repeating: 0, count: 32)
            let count = read(output[0], &bytes, bytes.count)
            #expect(String(decoding: bytes.prefix(max(0, count)), as: UTF8.self) == "later-frame\n")
        }
    }

    @Test func refusalKeepsItsStatusAndRestoresFlags() throws {
        try withPeer({ peer in
            _ = StreamBridge.writeAll(peer, Data("{\"ok\":false,\"error\":\"refused\"}\n".utf8))
        }) { socket in
            let flags = fcntl(socket, F_GETFL)
            do {
                try StreamBridge(socket: socket, input: -1, output: -1).open(Self.request)
                Issue.record("Expected server refusal")
            } catch {
                #expect(Agtermctl.controlExitCode(for: error) == .failure)
                #expect(String(describing: error).contains("refused"))
            }
            #expect(fcntl(socket, F_GETFL) == flags)
        }
    }

    @Test(arguments: [false, true])
    func realCLIHandshakeTimeoutExitsFourWithoutLaunching(overlay: Bool) throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("handshake-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let path = directory.appendingPathComponent("s").path
        let marker = directory.appendingPathComponent("launched")
        #if canImport(Darwin)
        let listener = socket(AF_UNIX, SOCK_STREAM, 0)
        #else
        let listener = socket(AF_UNIX, Int32(SOCK_STREAM.rawValue), 0)
        #endif
        try #require(listener >= 0)
        defer { close(listener) }
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let bytes = path.utf8CString
        try #require(bytes.count <= MemoryLayout.size(ofValue: address.sun_path))
        withUnsafeMutablePointer(to: &address.sun_path) { pointer in
            pointer.withMemoryRebound(to: CChar.self, capacity: bytes.count) { destination in
                bytes.withUnsafeBufferPointer { destination.update(from: $0.baseAddress!, count: $0.count) }
            }
        }
        let bound = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        try #require(bound == 0)
        try #require(listen(listener, 1) == 0)
        var directoryURL = Bundle.main.bundleURL
        var executable: URL?
        for _ in 0..<6 {
            let candidate = directoryURL.appendingPathComponent("agtermctl")
            if FileManager.default.isExecutableFile(atPath: candidate.path) { executable = candidate; break }
            directoryURL.deleteLastPathComponent()
        }
        let process = Process()
        process.executableURL = try #require(executable)
        process.arguments = (overlay ? ["session", "overlay", "run-job", "fixture"] : ["zmx", "present", "fixture"])
            + ["--socket", path]
        process.standardInput = FileHandle.nullDevice
        let output = Pipe()
        let error = Pipe()
        process.standardOutput = output
        process.standardError = error
        let finished = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in finished.signal() }
        try process.run()
        defer {
            if process.isRunning { kill(process.processIdentifier, SIGKILL); process.waitUntilExit() }
        }
        var ready = pollfd(fd: listener, events: Int16(POLLIN), revents: 0)
        try #require(poll(&ready, 1, 5_000) > 0)
        let connection = accept(listener, nil, nil)
        try #require(connection >= 0)
        defer { close(connection) }
        #if canImport(Darwin)
        var enabled: Int32 = 1
        setsockopt(connection, SOL_SOCKET, SO_NOSIGPIPE, &enabled, socklen_t(MemoryLayout<Int32>.size))
        #endif
        if overlay {
            let context = OverlayLaunchContext(command: "echo launched > '\(marker.path)'", cwd: directory.path, sessionEnvironment: [:])
            // Withhold only the context's newline: valid JSON must not launch before the complete frame.
            let partial = Data(Self.ok.utf8) + (try OverlayJobFrame.context(context).line().dropLast())
            try #require(StreamBridge.writeAll(connection, partial))
        }
        let completed = finished.wait(timeout: .now() + 35) == .success
        try #require(completed, "CLI startup must finish within its production handshake budget")
        #expect(process.terminationStatus == 4)
        #expect(output.fileHandleForReading.readDataToEndOfFile().isEmpty)
        let diagnostic = String(decoding: error.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        #expect(diagnostic.contains("timed out"))
        #expect(diagnostic.contains("do not retry automatically"))
        #expect(!FileManager.default.fileExists(atPath: marker.path))
    }

    private func expectTimeout(_ body: () throws -> Void) {
        do {
            try body()
            Issue.record("Expected handshake timeout")
        } catch {
            #expect((error as? SocketClientError)?.kind == .transport)
            #expect(Agtermctl.controlExitCode(for: error) == ExitCode(4))
            #expect(String(describing: error).contains("timed out"))
            #expect(String(describing: error).contains("do not retry automatically"))
        }
    }

    /// The watchdog makes a regression to blocking IO fail instead of hanging the test process.
    private func withPeer(_ serve: @escaping @Sendable (Int32) -> Void, body: (Int32) throws -> Void) throws {
        var pair: [Int32] = [-1, -1]
        #if canImport(Darwin)
        try #require(socketpair(AF_UNIX, SOCK_STREAM, 0, &pair) == 0)
        for fd in pair {
            var enabled: Int32 = 1
            setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, socklen_t(MemoryLayout<Int32>.size))
        }
        #else
        try #require(socketpair(AF_UNIX, Int32(SOCK_STREAM.rawValue), 0, &pair) == 0)
        #endif
        let client = pair[0]
        let peer = pair[1]
        let served = DispatchSemaphore(value: 0)
        let cancel = DispatchSemaphore(value: 0)
        let watched = DispatchSemaphore(value: 0)
        Thread { serve(peer); served.signal() }.start()
        Thread {
            if cancel.wait(timeout: .now() + 2) == .timedOut {
                shutdown(client, Int32(SHUT_RDWR))
                shutdown(peer, Int32(SHUT_RDWR))
            }
            watched.signal()
        }.start()
        defer {
            shutdown(client, Int32(SHUT_RDWR))
            shutdown(peer, Int32(SHUT_RDWR))
            cancel.signal()
            watched.wait()
            served.wait()
            close(client)
            close(peer)
        }
        try body(client)
    }
}
