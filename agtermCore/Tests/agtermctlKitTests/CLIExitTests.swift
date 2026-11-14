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

struct CLIExitTests {
    @Test func ordinaryAndCommandStatusesRemainUnchanged() {
        #expect(Agtermctl.controlExitCode(for: SocketClientError("local failure")) == .failure)
        #expect(Agtermctl.controlExitCode(for: SocketClientError("wire failure", kind: .transport)) == ExitCode(4))
        for code: Int32 in [0, 1, 2, 3, 4, 42, 127, 143] {
            #expect(Agtermctl.controlExitCode(for: ExitCode(code)) == ExitCode(code))
        }
        let invalid = ValidationError("invalid argument")
        #expect(Agtermctl.controlExitCode(for: invalid) == Agtermctl.exitCode(for: invalid))
    }

    @Test func missingSocketHasTransportStatus() throws {
        let result = try runCLI(["tree", "--socket", "/tmp/agterm-missing-\(UUID().uuidString)"])
        #expect(result.status == 4)
        #expect(result.output.isEmpty)
        #expect(result.error.contains("connect("))
    }

    @Test func responsesDistinguishServerAndTransportFailures() throws {
        for command in [["tree"], ["zmx", "present", "fixture"]] {
            for (reply, status, diagnostic) in [
                ("{\"ok\":false,\"error\":\"fixture refusal\"}\n", Int32(1), "fixture refusal"),
                ("not-json\n", 4, "could not decode"),
                ("", 4, command == ["tree"] ? "no response" : "without answering")
            ] {
                try withReply(reply) { path in
                    let result = try runCLI(command + ["--socket", path])
                    #expect(result.status == status)
                    #expect(result.output.isEmpty)
                    #expect(result.error.contains(diagnostic))
                }
            }
        }
    }

    @Test func successfulAndJSONServerResponsesKeepTheirOutput() throws {
        for (reply, status) in [("{\"ok\":true}\n", Int32(0)),
                                ("{\"ok\":false,\"error\":\"fixture refusal\"}\n", 1)] {
            try withReply(reply) { path in
                let result = try runCLI(["tree", "--json", "--socket", path])
                #expect(result.status == status)
                #expect(result.output == reply)
                #expect(result.error.isEmpty)
            }
        }
    }

    @Test func cancellationAndEscapeKeepTheirProcessStatuses() throws {
        for (outcome, status) in [("cancelled", Int32(2)), ("escaped", 3)] {
            try withReply("{\"ok\":true,\"result\":{\"ask\":{\"result\":\"\(outcome)\"}}}\n") { path in
                let result = try runCLI(["ask", "result", "fixture", "--socket", path])
                #expect(result.status == status)
                #expect(result.output.contains(outcome))
                #expect(result.error.isEmpty)
            }
        }
    }

    @Test func overlayClaimDistinguishesRefusalAndTransportFailures() throws {
        for (reply, status, diagnostic) in [
            ("{\"ok\":false,\"error\":\"fixture refusal\"}\n", Int32(1), "fixture refusal"),
            ("not-json\n", 4, "could not decode"),
            ("", 4, "without answering"),
            ("{\"ok\":true}\n", 4, "no launch context"),
            ("{\"ok\":true}\nnot-json\n", 4, "no launch context")
        ] {
            try withReply(reply) { path in
                let result = try runCLI(["session", "overlay", "run-job", "fixture", "--socket", path])
                #expect(result.status == status)
                #expect(result.output.isEmpty)
                #expect(result.error.contains(diagnostic))
                #expect(result.error.split(separator: "\n").count == 1)
            }
        }
    }

    @Test func helpAndUsageKeepArgumentParserBehavior() throws {
        let help = try runCLI(["--help"])
        #expect(help.status == 0)
        #expect(help.output.contains("USAGE:"))
        #expect(help.error.isEmpty)
        let invalid = try runCLI(["--not-an-option"])
        #expect(invalid.status == 64)
        #expect(invalid.output.isEmpty)
        #expect(invalid.error.contains("Unknown option"))
    }

    private func runCLI(_ arguments: [String]) throws -> (status: Int32, output: String, error: String) {
        var directory = Bundle.main.bundleURL
        var executable: URL?
        for _ in 0..<6 {
            let candidate = directory.appendingPathComponent("agtermctl")
            if FileManager.default.isExecutableFile(atPath: candidate.path) {
                executable = candidate
                break
            }
            directory.deleteLastPathComponent()
        }
        let process = Process()
        process.executableURL = try #require(executable, "swift test must build the agtermctl executable")
        process.arguments = arguments
        process.standardInput = FileHandle.nullDevice
        let output = Pipe()
        let error = Pipe()
        process.standardOutput = output
        process.standardError = error
        let finished = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in finished.signal() }
        try process.run()
        let completed = finished.wait(timeout: .now() + 5) == .success
        if !completed {
            kill(process.processIdentifier, SIGKILL)
            process.waitUntilExit()
        }
        try #require(completed, "CLI subprocess exceeded fixture deadline")
        return (process.terminationStatus,
                String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self),
                String(decoding: error.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self))
    }

    private func withReply(_ reply: String, body: (String) throws -> Void) throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("cli-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let path = directory.appendingPathComponent("s").path
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
                bytes.withUnsafeBufferPointer { source in
                    destination.update(from: source.baseAddress!, count: source.count)
                }
            }
        }
        let bound = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        try #require(bound == 0)
        try #require(listen(listener, 1) == 0)
        let finished = DispatchSemaphore(value: 0)
        Thread {
            defer { finished.signal() }
            var ready = pollfd(fd: listener, events: Int16(POLLIN), revents: 0)
            guard poll(&ready, 1, 3_000) > 0 else { return }
            let connection = accept(listener, nil, nil)
            guard connection >= 0 else { return }
            defer { close(connection) }
            ready.fd = connection
            guard poll(&ready, 1, 3_000) > 0 else { return }
            var request = [UInt8](repeating: 0, count: 4096)
            guard read(connection, &request, request.count) > 0 else { return }
            let data = Data(reply.utf8)
            #if canImport(Darwin)
            var noSigPipe: Int32 = 1
            setsockopt(connection, SOL_SOCKET, SO_NOSIGPIPE, &noSigPipe, socklen_t(MemoryLayout<Int32>.size))
            data.withUnsafeBytes { _ = write(connection, $0.baseAddress, $0.count) }
            #else
            data.withUnsafeBytes { _ = Glibc.send(connection, $0.baseAddress, $0.count, Int32(MSG_NOSIGNAL)) }
            #endif
        }.start()
        defer { finished.wait() }
        try body(path)
    }
}
