import Foundation
import Glibc
import Testing
import agtermCore
@testable import AgtermLinux

@Suite("Linux custom-command process launching")
struct LinuxCustomCommandProcessTests {
    @Test("requests preserve expansion, environment, cwd, pane, and null stdio")
    func requestPolicy() {
        let command = CustomCommand(
            name: "inspect", command: "printf '%s' \"$AGT_PANE\"; echo {AGT_PANE}:{AGT_SELECTION}",
            shortcut: "")
        for pane in [CommandContext.Pane.left, .right, .scratch] {
            let context = CommandContext(
                sessionID: "session", sessionName: "name", sessionPWD: "/tmp/work",
                workspaceID: "workspace", workspaceName: "work", windowID: "window",
                windowName: "main", pane: pane, selection: "selected", socket: "/tmp/agterm.sock")
            let request = LinuxCustomCommandProcess.request(
                command: command, context: context,
                baseEnvironment: ["KEEP": "yes", "AGT_PANE": "stale"])

            #expect(request.executablePath == "/bin/sh")
            #expect(request.arguments == [
                "-c", "printf '%s' \"$AGT_PANE\"; echo \(pane.rawValue):selected"
            ])
            #expect(request.environment["KEEP"] == "yes")
            #expect(request.environment["AGT_PANE"] == pane.rawValue)
            #expect(request.environment["AGT_SELECTION"] == "selected")
            #expect(request.currentDirectoryPath == "/tmp/work")
            #expect(request.standardIO == .null)
            #expect(request.environment["PATH"]?.contains("/.local/bin") == true)
        }
    }

    @Test("custom command PATH is Linux-native, bundled-first, stable, and deduplicated")
    func commandPath() {
        let path = LinuxCommandPath.widened(
            "/custom/bin:/usr/bin:/custom/bin", bundledCLIDirectory: "/app/bin",
            homeDirectory: "/home/test")
        #expect(path.split(separator: ":").map(String.init) == [
            "/app/bin", "/custom/bin", "/usr/bin", "/home/test/.local/bin",
            "/usr/local/bin", "/bin", "/usr/local/sbin", "/usr/sbin", "/sbin"
        ])
        #expect(!path.contains("homebrew"))
    }

    @Test("bundled CLI directory requires the resolved absolute executable path")
    func bundledCLIPath() {
        #expect(LinuxCommandPath.resolvedExecutableDirectory("agterm-linux") == nil)
        #expect(LinuxCommandPath.resolvedExecutableDirectory("/opt/agterm/bin/agterm-linux.bin")
                == "/opt/agterm/bin")
        #expect(LinuxCommandPath.bundledCLIDirectory?.hasPrefix("/") == true)
    }

    /// The restore lives in `request`, not in `launch`'s default argument, so a caller that supplies its
    /// own base environment cannot opt out of it. Driven off `gdkEnvironment` itself rather than a
    /// literal, because which variable this GTK build assigns — and therefore restores — is a runtime
    /// fact; the values are pinned from literals in `LinuxGdkPreLaunchEnvironmentTests`.
    @Test("the request restores the pre-launch GDK environment over a caller's own base")
    func requestRestoresGdkEnvironment() {
        let poisoned = gdkEnvironment.childRestore.keys.reduce(into: [String: String]()) { base, name in
            base[name] = "leaked-from-agterm"
        }
        let request = LinuxCustomCommandProcess.request(
            command: CustomCommand(name: "noop", command: "true", shortcut: ""),
            context: CommandContext(), baseEnvironment: poisoned)
        for (name, restored) in gdkEnvironment.childRestore {
            #expect(request.environment[name] == restored)
        }
    }

    @Test("empty cwd is omitted")
    func emptyCwd() {
        let command = CustomCommand(name: "noop", command: "true", shortcut: "")
        let request = LinuxCustomCommandProcess.request(
            command: command, context: CommandContext(), baseEnvironment: [:])
        #expect(request.currentDirectoryPath == nil)
    }

    @Test("spawn errors and non-zero exits report while successful exits stay silent")
    func failureRouting() {
        let command = CustomCommand(name: "failure", command: "exit 19", shortcut: "")
        let context = CommandContext(sessionPWD: "/tmp", pane: .right)
        let failures = LockedValue<[LinuxCustomCommandFailure]>([])

        let throwing = RecordingProcessLauncher()
        throwing.error = NSError(domain: NSPOSIXErrorDomain, code: Int(ENOENT))
        LinuxCustomCommandProcess.launch(
            command: command, context: context, baseEnvironment: [:], launcher: throwing
        ) { failure in failures.withValue { $0.append(failure) } }
        #expect(failures.value.count == 1)
        if case .launch(let detail) = failures.value[0] {
            #expect(!detail.isEmpty)
        } else {
            Issue.record("spawn error was not classified as a launch failure")
        }

        let running = RecordingProcessLauncher()
        LinuxCustomCommandProcess.launch(
            command: command, context: context, baseEnvironment: [:], launcher: running
        ) { failure in failures.withValue { $0.append(failure) } }
        running.finish(status: 0)
        #expect(failures.value.count == 1)

        LinuxCustomCommandProcess.launch(
            command: command, context: context, baseEnvironment: [:], launcher: running
        ) { failure in failures.withValue { $0.append(failure) } }
        running.finish(status: 19)
        #expect(failures.value.last == .exit(19, nil))
    }

    @Test("spawn launcher rejects a missing executable")
    func missingExecutable() {
        let launcher = SpawnLinuxProcessLauncher()
        let request = LinuxProcessLaunchRequest(
            executablePath: "/definitely/missing/agterm-command",
            arguments: [], environment: [:], currentDirectoryPath: nil, standardIO: .null)
        do {
            try launcher.launch(request) { _ in }
            Issue.record("missing executable unexpectedly launched")
        } catch {
            #expect(!error.localizedDescription.isEmpty)
        }
    }

    @Test("spawn launcher rejects an invalid working directory")
    func invalidWorkingDirectory() {
        let launcher = SpawnLinuxProcessLauncher()
        let request = LinuxProcessLaunchRequest(
            executablePath: "/bin/sh", arguments: ["-c", "true"], environment: [:],
            currentDirectoryPath: "/definitely/missing/agterm-cwd", standardIO: .null)
        do {
            try launcher.launch(request) { _ in }
            Issue.record("invalid working directory unexpectedly launched")
        } catch {
            #expect(!error.localizedDescription.isEmpty)
        }
    }

    @Test("spawn launcher reports successful and non-zero termination")
    func realTermination() async {
        let launcher = SpawnLinuxProcessLauncher()
        for (line, expected) in [("true", Int32(0)), ("exit 23", Int32(23))] {
            await confirmation { confirmed in
                let request = LinuxProcessLaunchRequest(
                    executablePath: "/bin/sh", arguments: ["-c", line], environment: [:],
                    currentDirectoryPath: "/tmp", standardIO: .null)
                do {
                    try launcher.launch(request) { status in
                        #expect(status == expected)
                        confirmed()
                    }
                } catch {
                    Issue.record("shell launch failed: \(error.localizedDescription)")
                    confirmed()
                }
                try? await Task.sleep(for: .milliseconds(200))
            }
        }
    }

    @Test("opt-in failure HUD captures the final stderr diagnostic from a real child")
    func realStderrCapture() async {
        let command = CustomCommand(
            name: "capture", command: "printf 'first\\n\\033[31mfinal diagnostic\\033[0m\\n' >&2; exit 23",
            shortcut: "", errorHud: true)
        let failure = await withCheckedContinuation { (continuation: CheckedContinuation<LinuxCustomCommandFailure, Never>) in
            LinuxCustomCommandProcess.launch(
                command: command, context: CommandContext(sessionPWD: "/tmp"),
                baseEnvironment: [:], launcher: SpawnLinuxProcessLauncher()
            ) { continuation.resume(returning: $0) }
        }
        #expect(failure == .exit(23, "final diagnostic"))
        #expect(failure.reason == "exit 23")
        #expect(failure.toast(commandName: command.name) == "command failed (exit 23): capture")
    }

    @Test("the stderr capture is owner-only under umask 0 and unlinked after exit 0 and exit 23")
    func stderrCapturePrivacy() throws {
        let launcher = RecordingProcessLauncher()
        let command = CustomCommand(name: "capture", command: "true", shortcut: "", errorHud: true)
        for status in [Int32(0), 23] {
            let previous = umask(0)
            LinuxCustomCommandProcess.launch(
                command: command, context: CommandContext(), baseEnvironment: [:], launcher: launcher) { _ in }
            umask(previous)
            guard case .stderrFile(let path) = try #require(launcher.requests.last).standardIO else {
                Issue.record("errorHud did not request a stderr file")
                return
            }
            var info = stat()
            #expect(stat(path, &info) == 0)
            #expect(info.st_mode & 0o777 == 0o600)
            launcher.finish(status: status)
            #expect(!FileManager.default.fileExists(atPath: path))
        }
    }

    @Test("a descendant holding stderr cannot stall completion, and the capture is gone on delivery")
    func stderrDescendant() async throws {
        let pidFile = FileManager.default.temporaryDirectory
            .appendingPathComponent("agterm-command-test-\(UUID().uuidString).pid").path
        defer { try? FileManager.default.removeItem(atPath: pidFile) }
        let command = CustomCommand(
            name: "descendant", command: "(sleep 30 >&2 & echo $! > '\(pidFile)'); echo diag >&2; exit 7",
            shortcut: "", errorHud: true)
        let launcher = ObservingProcessLauncher()
        let failure = await withCheckedContinuation { (continuation: CheckedContinuation<LinuxCustomCommandFailure, Never>) in
            LinuxCustomCommandProcess.launch(
                command: command, context: CommandContext(sessionPWD: "/tmp"), baseEnvironment: [:], launcher: launcher
            ) { continuation.resume(returning: $0) }
        }
        #expect(failure == .exit(7, "diag"))
        guard case .stderrFile(let path) = try #require(launcher.requests.first).standardIO else {
            Issue.record("errorHud did not request a stderr file")
            return
        }
        #expect(!FileManager.default.fileExists(atPath: path))

        let pid = try #require(pid_t(try String(contentsOfFile: pidFile, encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines)))
        // A live descendant proves completion did not wait for its EOF, without timing executor scheduling.
        #expect(isLiveSleep(pid))
        #expect(kill(pid, SIGKILL) == 0)
        for _ in 0..<100 where isLiveSleep(pid) { try await Task.sleep(for: .milliseconds(20)) }
        #expect(!isLiveSleep(pid))
    }

    @Test("closing and reopening the same window id cannot reactivate an old origin")
    @MainActor
    func controllerIncarnation() async {
        let oldOrigin = LinuxCustomCommandOrigin()
        let launcher = RecordingProcessLauncher()
        let deliveries = LockedValue<[LinuxCustomCommandFailure]>([])
        let command = CustomCommand(name: "slow", command: "exit 29", shortcut: "")
        LinuxCustomCommandProcess.launch(
            command: command, context: CommandContext(), baseEnvironment: [:], launcher: launcher
        ) { [weak oldOrigin] failure in
            Task { @MainActor in
                oldOrigin?.deliverIfActive { deliveries.withValue { $0.append(failure) } }
            }
        }
        oldOrigin.invalidate()
        let reopenedOrigin = LinuxCustomCommandOrigin()
        launcher.finish(status: 29)
        try? await Task.sleep(for: .milliseconds(50))

        #expect(deliveries.value.isEmpty)
        #expect(!oldOrigin.isActive)
        #expect(reopenedOrigin.isActive)
        #expect(oldOrigin !== reopenedOrigin)
    }
}

private final class RecordingProcessLauncher: LinuxProcessLaunching, @unchecked Sendable {
    private let lock = NSLock()
    var error: (any Error)?
    private var recorded: [LinuxProcessLaunchRequest] = []
    private var completions: [@Sendable (Int32) -> Void] = []

    var requests: [LinuxProcessLaunchRequest] {
        lock.lock()
        defer { lock.unlock() }
        return recorded
    }

    func launch(
        _ request: LinuxProcessLaunchRequest,
        onTermination: @escaping @Sendable (Int32) -> Void
    ) throws {
        lock.lock()
        defer { lock.unlock() }
        if let error { throw error }
        recorded.append(request)
        completions.append(onTermination)
    }

    func finish(status: Int32) {
        lock.lock()
        let completion = completions.removeFirst()
        lock.unlock()
        completion(status)
    }
}

private final class ObservingProcessLauncher: LinuxProcessLaunching, @unchecked Sendable {
    private let lock = NSLock()
    private var recorded: [LinuxProcessLaunchRequest] = []

    var requests: [LinuxProcessLaunchRequest] {
        lock.lock()
        defer { lock.unlock() }
        return recorded
    }

    func launch(_ request: LinuxProcessLaunchRequest, onTermination: @escaping @Sendable (Int32) -> Void) throws {
        lock.lock()
        recorded.append(request)
        lock.unlock()
        try SpawnLinuxProcessLauncher().launch(request, onTermination: onTermination)
    }
}

/// A zombie counts as dead: the orphan's new parent may never reap it.
private func isLiveSleep(_ pid: pid_t) -> Bool {
    guard let stat = try? String(contentsOfFile: "/proc/\(pid)/stat", encoding: .utf8),
          let close = stat.lastIndex(of: ")"),
          let cmdline = FileManager.default.contents(atPath: "/proc/\(pid)/cmdline") else { return false }
    return stat[stat.index(close, offsetBy: 2)...].first != "Z"
        && String(decoding: cmdline, as: UTF8.self).hasPrefix("sleep\u{0}30")
}

private final class LockedValue<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: Value

    init(_ value: Value) { storage = value }

    var value: Value {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }

    func withValue(_ body: (inout Value) -> Void) {
        lock.lock()
        defer { lock.unlock() }
        body(&storage)
    }
}
