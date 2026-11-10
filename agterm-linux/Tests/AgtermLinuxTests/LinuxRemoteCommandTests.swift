import Foundation
import Glibc
import Testing
import agtermCore
@testable import AgtermLinux

@Suite("Linux remote command temporary files, output bound, and timeout")
struct LinuxRemoteCommandTests {
    @Test("stdin bytes with embedded NULs round-trip through cat")
    func nulRoundTrip() throws {
        let input = Data("a\u{0}b\u{0}\nc\u{0}".utf8)
        let result = try runIsolated(["cat"], input: input)
        #expect(result.status == 0)
        #expect(Data(result.stdout.utf8) == input)
    }

    @Test("success, nonzero exit, and a missing command each leave nothing behind")
    func exitPaths() throws {
        let success = try runIsolated(["sh", "-c", "printf ok"])
        #expect(success == RemoteCommandResult(status: 0, stdout: "ok", stderr: ""))

        let failure = try runIsolated(["sh", "-c", "printf out; echo err >&2; exit 23"])
        #expect(failure == RemoteCommandResult(status: 23, stdout: "out", stderr: "err\n"))

        let missing = try runIsolated(["agterm-definitely-missing-command"], input: Data("x".utf8))
        #expect(missing.status == 127)
        #expect(missing.stdout.isEmpty)
        #expect(missing.stderr.contains("agterm-definitely-missing-command"))
    }

    @Test("timeout reports 124 and leaves nothing behind")
    func timeoutCleanup() throws {
        let result = try runIsolated(["sleep", "30"], deadline: 0.3, input: Data("x".utf8))
        #expect(result.status == 124)
    }

    @Test("input and output files are owner-only while the child runs")
    func fileModes() throws {
        let root = try makeDirectory()
        defer { try? FileManager.default.removeItem(atPath: root) }
        let listing = try runIsolated(
            ["sh", "-c", "cat >/dev/null; stat -c '%a %n' \"$0\"/agterm-*", root], root: root, input: Data("x".utf8))
        let lines = listing.stdout.split(separator: "\n")
        #expect(lines.count == 3)
        #expect(lines.filter { $0.contains("/agterm-input-") }.count == 1)
        #expect(lines.filter { $0.contains("/agterm-remote-") }.count == 2)
        #expect(lines.allSatisfy { $0.hasPrefix("600 ") })
    }

    @Test("output over the declared bound fails whole and still cleans up")
    func largeOutputFails() throws {
        let size = 2 * LinuxRemoteCommand.outputLimit
        let result = try runIsolated(
            ["sh", "-c", "head -c \(size) /dev/zero; head -c \(size) /dev/zero >&2"], deadline: 60)
        #expect(result.status == LinuxRemoteCommand.outputLimitStatus)
        #expect(result.stdout.isEmpty)
        #expect(result.stderr == "remote command output exceeded \(LinuxRemoteCommand.outputLimit) bytes")
    }

    @Test("the bound admits exactly the limit and rejects one byte more, on either stream")
    func boundEdge() throws {
        let exact = try runIsolated(["sh", "-c", "head -c 1024 /dev/zero | tr '\\0' a"], outputLimit: 1024)
        #expect(exact.status == 0)
        #expect(exact.stdout == String(repeating: "a", count: 1024))

        let over = try runIsolated(["sh", "-c", "head -c 1025 /dev/zero | tr '\\0' a"], outputLimit: 1024)
        #expect(over.status == LinuxRemoteCommand.outputLimitStatus)
        #expect(over.stdout.isEmpty)

        let overStderr = try runIsolated(["sh", "-c", "head -c 1025 /dev/zero >&2; exit 0"], outputLimit: 1024)
        #expect(overStderr.status == LinuxRemoteCommand.outputLimitStatus)
    }

    @Test("a TERM-ignoring child and its descendant are killed after the grace")
    func termIgnoringChild() throws {
        try expectTimeout(script: "trap '' TERM; sleep 30 & echo $! > \"$0\"; wait", minimum: 0.75)
    }

    @Test("a backgrounded descendant neither delays the timeout nor outlives it")
    func backgroundedDescendant() throws {
        try expectTimeout(script: "sleep 30 & echo $! > \"$0\"; sleep 30", minimum: 0.5)
    }

    private func expectTimeout(script: String, minimum: TimeInterval) throws {
        let fixtures = try makeDirectory()
        defer { try? FileManager.default.removeItem(atPath: fixtures) }
        let pidFile = fixtures + "/descendant.pid"
        let start = Date()
        let result = try runIsolated(["sh", "-c", script, pidFile], deadline: 0.5)
        let elapsed = Date().timeIntervalSince(start)
        #expect(result.status == 124)
        #expect(elapsed >= minimum)
        #expect(elapsed < 0.5 + 0.25 + 1.0)

        let pid = try #require(readPID(pidFile))
        defer { if isLiveSleep(pid) { _ = Glibc.kill(pid, SIGKILL) } }
        #expect(!isLiveSleep(pid))
    }
}

private func runIsolated(_ argv: [String], deadline: TimeInterval = 10, root: String? = nil, input: Data? = nil,
                         outputLimit: Int = LinuxRemoteCommand.outputLimit) throws -> RemoteCommandResult {
    let directory = try root ?? makeDirectory()
    defer { if root == nil { try? FileManager.default.removeItem(atPath: directory) } }
    let result = LinuxRemoteCommand.run(argv, deadline: deadline, input: input,
                                        outputLimit: outputLimit, temporaryDirectory: directory)
    #expect(try FileManager.default.contentsOfDirectory(atPath: directory).isEmpty)
    #expect(descriptors(into: directory).isEmpty)
    return result
}

private func makeDirectory() throws -> String {
    var template = Array("/tmp/agterm-rc-test-XXXXXX".utf8CString)
    guard mkdtemp(&template) != nil else { throw POSIXError(.init(rawValue: errno) ?? .EIO) }
    return String(decoding: template.dropLast().map { UInt8(bitPattern: $0) }, as: UTF8.self)
}

/// Open descriptors of this process that still name a file under `directory`, deleted or not.
private func descriptors(into directory: String) -> [String] {
    let names = (try? FileManager.default.contentsOfDirectory(atPath: "/proc/self/fd")) ?? []
    return names.compactMap { try? FileManager.default.destinationOfSymbolicLink(atPath: "/proc/self/fd/" + $0) }
        .filter { $0.hasPrefix(directory + "/") }
}

private func readPID(_ path: String) -> pid_t? {
    for _ in 0..<50 {
        if let text = try? String(contentsOfFile: path, encoding: .utf8),
           let pid = pid_t(text.trimmingCharacters(in: .whitespacesAndNewlines)) { return pid }
        usleep(20_000)
    }
    return nil
}

/// Matches the fixture's `sleep` by command line, so a recycled PID is never signalled.
private func isLiveSleep(_ pid: pid_t) -> Bool {
    guard let stat = try? String(contentsOfFile: "/proc/\(pid)/stat", encoding: .utf8),
          let close = stat.lastIndex(of: ")"),
          let cmdline = FileManager.default.contents(atPath: "/proc/\(pid)/cmdline") else { return false }
    let state = stat[stat.index(close, offsetBy: 2)...].first
    return state != "Z" && String(decoding: cmdline, as: UTF8.self).hasPrefix("sleep\u{0}30")
}
