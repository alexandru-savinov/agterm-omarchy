import Foundation
import Testing
@testable import agtermCore

@MainActor
struct RemoteReconnectDiagnosticsTests {
    @Test func observationPreservesTransitionsAndSeesAnEntryCancelledBetweenReadbacks() {
        let plain = RemoteReconnectBook(), observed = RemoteReconnectBook()
        let pane = UUID(), session = UUID(), now = Date(timeIntervalSince1970: 100)
        var events: [RemoteReconnectBook.Diagnostic] = []
        #expect(observed.onDiagnostic == nil)
        observed.onDiagnostic = { events.append($0) }

        func exercise(_ book: RemoteReconnectBook) -> [[UUID: RemoteReconnectBook.Entry]] {
            var snapshots: [[UUID: RemoteReconnectBook.Entry]] = []
            book.wait(pane: pane, session: session, host: "fixture-host", cover: true, now: now)
            snapshots.append(book.entries)
            #expect(book.due(now: now) == [pane])
            snapshots.append(book.entries)
            #expect(book.finished(pane: pane, ok: false, stderr: "private reason", now: now) == nil)
            snapshots.append(book.entries)
            book.retryNow(pane: pane, now: now)
            snapshots.append(book.entries)
            #expect(book.due(now: now) == [pane])
            snapshots.append(book.entries)
            book.cancel(pane: pane)
            snapshots.append(book.entries)
            #expect(book.finished(pane: pane, ok: true, now: now) == nil)
            #expect(book.readback(pane: pane) == nil)
            return snapshots
        }

        #expect(exercise(plain) == exercise(observed))
        #expect(events.map(\.stage) == [.waiting, .probing, .failed, .retryNow, .probing, .cancelled])
        #expect(events.allSatisfy { $0.pane == pane })
        #expect(events[2].failures == 1 && events[2].reasonPresent)
        #expect(events.last?.waiting == false)
    }

    @Test func duplicateWaitAndRejectedFinishDoNotInventTransitions() {
        let book = RemoteReconnectBook(), pane = UUID(), session = UUID()
        let now = Date(timeIntervalSince1970: 100)
        var stages: [RemoteReconnectBook.DiagnosticStage] = []
        book.onDiagnostic = { stages.append($0.stage) }
        #expect(book.finished(pane: pane, ok: true, now: now) == nil)
        book.wait(pane: pane, session: session, host: "fixture", cover: false, now: now)
        book.wait(pane: pane, session: session, host: "other", cover: true, now: now)
        #expect(book.finished(pane: pane, ok: true, now: now) == nil)
        #expect(stages == [.waiting])
        _ = book.due(now: now)
        #expect(book.finished(pane: pane, ok: true, now: now)?.host == "fixture")
        #expect(stages == [.waiting, .probing, .resumed])
        #expect(book.isEmpty)
    }
}

struct RemoteWrapperDiagnosticsTests {
    @Test(arguments: [Int32(23), Int32(255)])
    func enablingMetadataPreservesBytesForegroundInputAndSSHStatus(_ status: Int32) async throws {
        let fixture = try DiagnosticWrapperFixture()
        defer { fixture.cleanUp() }
        let off = try await fixture.run(status: status, enabled: false)
        let on = try await fixture.run(status: status, enabled: true)
        #expect(off.output == on.output)
        #expect(off.status == status && on.status == status)
        #expect(off.records.isEmpty)
        #expect(on.records.contains("event=ssh-return") && on.records.contains("status=\(status)"))
        #expect(!on.records.contains("fixture-host") && !on.records.contains("fixture-secret-nonce"))
        let stages = on.records.split(separator: "\n").map { String($0.split(separator: " ")[2]) }
        if status == 255 {
            #expect(stages == ["event=wrapper-start", "event=ssh-return", "event=osc-before", "event=osc-after",
                              "event=hold-before", "event=hold-return", "event=exit-intent"])
            #expect(on.records.contains("event=osc-after") && on.records.contains("event=hold-return"))
        } else {
            #expect(stages == ["event=wrapper-start", "event=ssh-return", "event=exit-intent"])
        }
    }

    @Test func anUnmanagedPaneKeepsItsOriginalExitPath() async throws {
        let fixture = try DiagnosticWrapperFixture()
        defer { fixture.cleanUp() }
        let result = try await fixture.run(status: 255, enabled: true, managed: false)
        #expect(result.status == 255 && result.records.isEmpty)
        #expect(String(decoding: result.output, as: UTF8.self).hasSuffix("disconnected, exit 255\n"))
    }
}

private struct DiagnosticWrapperFixture {
    let root: URL
    let incarnation = UUID()
    let pane = UUID()
    var log: URL { root.appendingPathComponent(incarnation.uuidString + ".log") }

    init() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("reconnect-diagnostic-\(UUID())")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let ssh = root.appendingPathComponent("ssh")
        try """
        #!/bin/sh
        [ "$1" = "-G" ] && { printf 'serveraliveinterval 0\\n'; exit 0; }
        exit "$AGTERM_FAKE_SSH_STATUS"
        """.write(to: ssh, atomically: false, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: ssh.path)
        // Explicit disposable writer double exercises the fixed argv/stdin boundary without adding an
        // interpreter dependency to shared-core tests. The Linux file-only suite tests the real writer.
        let writer = root.appendingPathComponent("writer-double")
        try """
        #!/bin/sh
        [ "$1" = -I ] && [ "$2" = -S ] || exit 81
        shift 3
        [ "$1" = --append-wrapper357 ] || exit 82
        IFS= read -r unexpected && exit 83
        printf 'DIAG357 wrapper event=%s incarnation=%s pane=%s nonceTag=%s pid=%s status=%s\\n' \\
            "$8" "$4" "$5" "$6" "$7" "$9" >>"$2/$4.log"
        """.write(to: writer, atomically: false, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: writer.path)
    }

    func cleanUp() { try? FileManager.default.removeItem(at: root) }

    func run(status: Int32, enabled: Bool, managed: Bool = true) async throws -> (status: Int32, output: Data, records: String) {
        try "".write(to: log, atomically: false, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: log.path)
        let command = try RemoteSession.attachPaneCommand(
            host: "fixture-host", endpoint: ControlZmxEndpoint(executable: "/unused/zmx", socketDirectory: "/unused"),
            daemon: ZmxSupport.daemonName(for: UUID()), session: "fixture", pane: .left,
            lead: managed ? ZmxLeadAttachment(nonce: "fixture-secret-nonce", claim: false) : nil)
        let process = Process(), input = Pipe(), output = Pipe()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = ["-c", command]
        process.environment = [
            "PATH": root.path + ":/usr/bin:/bin", "AGTERM_FAKE_SSH_STATUS": String(status),
            "AGTERM_DIAG357": enabled ? "1" : "0", "AGTERM_DIAG357_DIR": root.path,
            "AGTERM_DIAG357_INCARNATION": incarnation.uuidString, "AGTERM_PANE_ID": pane.uuidString,
            "AGTERM_DIAG357_NONCE_TAG": String(repeating: "a", count: 64),
            "AGTERM_DIAG357_INTERPRETER": root.appendingPathComponent("writer-double").path,
            "AGTERM_DIAG357_WRITER": root.appendingPathComponent("unused-module-double").path,
            "AGTERM_DIAG357_IDENTITY": "1:2:3:4"
        ]
        process.standardInput = input
        process.standardOutput = output
        process.standardError = Pipe()
        let (exited, continuation) = AsyncStream<Void>.makeStream()
        process.terminationHandler = { _ in continuation.finish() }
        try process.run()
        defer {
            try? input.fileHandleForWriting.close()
            if process.isRunning { process.terminate() }
        }
        if status == 255 && managed {
            if enabled {
                for _ in 0..<100 {
                    if (try String(contentsOf: log, encoding: .utf8)).contains("event=hold-before") { break }
                    try await Task.sleep(for: .milliseconds(10))
                }
                #expect(try String(contentsOf: log, encoding: .utf8).contains("event=hold-before"))
            } else {
                try await Task.sleep(for: .milliseconds(100))
            }
            try #require(process.isRunning)
            try input.fileHandleForWriting.write(contentsOf: Data("fixture input\n".utf8))
        }
        try input.fileHandleForWriting.close()
        for await _ in exited {}
        return (process.terminationStatus, output.fileHandleForReading.readDataToEndOfFile(),
                try String(contentsOf: log, encoding: .utf8))
    }
}
