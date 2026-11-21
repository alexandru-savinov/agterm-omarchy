import Foundation
import Testing
@testable import agtermCore

final class ForegroundSpawnTests {
    private let root: URL

    init() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("foreground-spawn-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    deinit { try? FileManager.default.removeItem(at: root) }

    @Test func runFeedsDataOnStdin() throws {
        let out = root.appendingPathComponent("out")

        let outcome = try ForegroundSpawn.run(["/bin/sh", "-c", "cat > \"$0\"", out.path], input: .data(Data("payload".utf8)),
                                              environment: [:])

        #expect(outcome == .exited(0))
        #expect(try String(contentsOf: out, encoding: .utf8) == "payload")
    }

    @Test func runReadsStdinFromAFile() throws {
        let source = root.appendingPathComponent("source")
        let out = root.appendingPathComponent("out")
        try Data("from a file".utf8).write(to: source)

        let outcome = try ForegroundSpawn.run(["/bin/sh", "-c", "cat > \"$0\"", out.path], input: .file(source.path), environment: [:])

        #expect(outcome == .exited(0))
        #expect(try String(contentsOf: out, encoding: .utf8) == "from a file")
    }

    @Test func runWithNothingGivesAnEmptyStdin() throws {
        let out = root.appendingPathComponent("out")

        let outcome = try ForegroundSpawn.run(["/bin/sh", "-c", "wc -c < /dev/stdin | tr -d ' ' > \"$0\"", out.path], input: .nothing,
                                              environment: [:])

        #expect(outcome == .exited(0))
        #expect(try String(contentsOf: out, encoding: .utf8) == "0\n")
    }

    @Test func captureReturnsStdoutAndTheStatus() throws {
        let result = try ForegroundSpawn.capture(["/bin/sh", "-c", "printf 'x86_64\\n'; exit 4"], environment: [:])

        #expect(result.outcome == .exited(4))
        #expect(String(decoding: result.output, as: UTF8.self) == "x86_64\n")
    }

    @Test func captureReadsOutputLargerThanAPipeBuffer() throws {
        let result = try ForegroundSpawn.capture(["/bin/sh", "-c", "head -c 200000 /dev/zero"], environment: [:])

        #expect(result.outcome == .exited(0))
        #expect(result.output.count == 200_000)
    }

    @Test func aKilledCommandReportsTheSignal() throws {
        #expect(try ForegroundSpawn.run(["/bin/sh", "-c", "kill -TERM $$"], input: .nothing, environment: [:]) == .signaled(SIGTERM))
    }

    @Test func aMissingStdinFileIsASpawnFailure() {
        #expect(throws: ForegroundSpawn.Failure.self) {
            try ForegroundSpawn.run(["/bin/cat"], input: .file(root.appendingPathComponent("absent").path), environment: [:])
        }
    }

    @Test func missingExecutableAlsoFailsWithAPipe() {
        let executable = root.appendingPathComponent("absent").path
        let failure = ForegroundSpawn.Failure(operation: "posix_spawnp", errno: ENOENT)
        #expect(throws: failure) {
            try ForegroundSpawn.run([executable], input: .data(Data("payload".utf8)), environment: [:])
        }
        #expect(throws: failure) {
            try ForegroundSpawn.capture([executable], environment: [:])
        }
    }

    #if os(Linux)
    @Test func bothPipeEndsAreCloseOnExec() throws {
        let ends = try ForegroundSpawn.makePipe()
        defer {
            close(ends.read)
            close(ends.write)
        }
        for fd in [ends.read, ends.write] {
            let flags = fcntl(fd, F_GETFD)
            #expect(flags >= 0 && flags & FD_CLOEXEC != 0)
        }
    }
    #endif
}
