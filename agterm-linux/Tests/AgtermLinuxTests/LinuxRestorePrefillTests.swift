import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

/// On Linux a restored command is pre-filled at the prompt and waits for Enter; it never runs by itself.
@Suite("Linux restore pre-fill")
struct LinuxRestorePrefillTests {
    private let configuration = ZmxSupport.Configuration(
        executablePath: "/usr/bin/zmx", environment: ["SHELL": "/bin/zsh", "ZDOTDIR": "/tmp/integration"],
        daemonName: "agterm-pane", socketDirectory: "/tmp/zmx", paneID: "pane")

    private func endsLine(_ value: String?) -> Bool {
        guard let last = value?.unicodeScalars.last else { return false }
        return last == "\n" || last == "\r"
    }

    @Test("Rerun types the quoted command with no trailing newline or carriage return")
    func rerunLineHasNoNewline() {
        let line = LinuxRestoreSeed.restoreLine(["claude", "--resume", "two words"], restoreEnabled: true, denylist: [])
        #expect(line == "'claude' '--resume' 'two words'")
        #expect(!endsLine(line))
    }

    @Test("a Rerun restore plan seeds the pre-filled line as initial input, not a command")
    func rerunSeedPrefills() {
        let input = LinuxRestoreSeed.restoreLine(["make", "watch"], restoreEnabled: true, denylist: [])
        let seed = LinuxRestoreSeed.ordinarySeed(CommandRestore.restorePlan(.init(
            wasRestored: true, restoreEnabled: true, hadForeground: true, foregroundInput: input,
            initialCommand: nil, restoreOverride: nil)))
        #expect(seed == LinuxLaunchSeed(command: nil, initialInput: "'make' 'watch'", waitAfterCommand: false))
    }

    @Test("a pinned restore command is pre-filled verbatim, without the newline the shared plan adds")
    func pinnedOverridePrefills() {
        let plan = CommandRestore.restorePlan(.init(
            wasRestored: true, restoreEnabled: true, hadForeground: true, foregroundInput: nil,
            initialCommand: nil, restoreOverride: "cd x && make watch"))
        #expect(plan.initialInput == "cd x && make watch\n", "the shared core plan is unchanged")
        let seed = LinuxRestoreSeed.ordinarySeed(plan)
        #expect(seed.initialInput == "cd x && make watch")
        #expect(seed.command == nil)
    }

    @Test("a captured argv carrying CR or LF is refused in every mode",
          arguments: [["echo", "a\nb"], ["echo", "a\rb"], ["printf\n"], ["x", "\r\n"], ["sleep", "1\n"]])
    func lineBreaksRefused(argv: [String]) {
        #expect(LinuxRestoreSeed.restoreLine(argv, restoreEnabled: true, denylist: []) == nil)
        let seed = LinuxRestoreSeed.wrappedSeed(configuration, replay: argv, durable: nil, denylist: [],
                                                daemonRunning: false)
        #expect(seed.initialInput == nil)
        #expect(seed.command == configuration.command)
    }

    @Test("a pinned restore command carrying CR or LF is refused", arguments: ["make\nwatch", "make watch\r"])
    func pinnedLineBreaksRefused(pin: String) {
        let seed = LinuxRestoreSeed.ordinarySeed(CommandRestore.restorePlan(.init(
            wasRestored: true, restoreEnabled: true, hadForeground: false, foregroundInput: nil,
            initialCommand: nil, restoreOverride: pin)))
        #expect(seed.initialInput == nil)
    }

    @Test("a captured argv carrying a C1 control character is neither pre-filled nor replayed",
          arguments: ["\u{85}", "\u{9B}"])
    func c1Refused(c1: String) {
        #expect(LinuxRestoreSeed.restoreLine(["echo", "a\(c1)b"], restoreEnabled: true, denylist: []) == nil)
        let seed = LinuxRestoreSeed.wrappedSeed(configuration, replay: ["echo", "a\(c1)b"], durable: nil,
                                                denylist: [], daemonRunning: false)
        #expect(seed.initialInput == nil)
    }

    @Test("no accepted line can submit itself")
    func noAcceptedLineCarriesLineBreak() {
        let samples: [[String]] = [["claude"], ["vim", "it's here"], ["printf", "%s\\n", "x"], ["a", "", "b"]]
        for argv in samples {
            let line = LinuxRestoreSeed.restoreLine(argv, restoreEnabled: true, denylist: [])
            #expect(line != nil)
            #expect(line?.unicodeScalars.contains { $0 == "\n" || $0 == "\r" } == false)
        }
    }

    @Test("the denylist still refuses multiplexers")
    func denylistKept() {
        for name in ["tmux", "screen", "zellij"] {
            #expect(LinuxRestoreSeed.restoreLine([name, "attach"], restoreEnabled: true,
                                                 denylist: ["tmux", "screen", "zellij"]) == nil)
        }
        #expect(LinuxRestoreSeed.restoreLine(["claude"], restoreEnabled: false, denylist: []) == nil)
    }

    @Test("Live recovery gives only a new daemon the pre-filled captured command")
    func liveRecoveryPrefills() {
        let seed = LinuxRestoreSeed.wrappedSeed(configuration, replay: ["sleep", "100"], durable: "make dev",
                                                denylist: [], daemonRunning: false)
        #expect(seed.command == LinuxZmxLaunch.prefillCommand(configuration, line: "'sleep' '100'"))
        #expect(seed.command?.contains("-lic") == false)
        #expect(seed.command?.contains("AGTERM_LIVE_PREFILL=") == true)
        #expect(seed.command?.contains("make dev") == false, "a capture still preempts the durable command")
        #expect(seed.initialInput == nil)
        #expect(!endsLine(seed.initialInput))
    }

    @Test("Live recovery wraps the session host the same way and still pre-fills")
    func liveRecoveryUnderSessionHost() {
        let hosted = ZmxSupport.Configuration(
            executablePath: "/usr/bin/zmx", environment: ["SHELL": "/bin/zsh", "ZDOTDIR": "/tmp/integration"],
            daemonName: "agterm-pane", socketDirectory: "/tmp/zmx", paneID: "pane",
            sessionHostExecutablePath: "/usr/lib/agterm/host")
        let seed = LinuxRestoreSeed.wrappedSeed(hosted, replay: ["sleep", "100"], durable: nil, denylist: [],
                                                daemonRunning: false)
        #expect(seed.command == LinuxZmxLaunch.prefillCommand(hosted, line: "'sleep' '100'"))
        #expect(seed.command?.hasPrefix("'/usr/lib/agterm/host' 'client' 'agterm-pane' '--'") == true)
        #expect(seed.initialInput == nil)
    }

    @Test("reattaching to a live daemon types nothing", arguments: [true as Bool?, nil])
    func liveReattachTypesNothing(daemonRunning: Bool?) {
        let seed = LinuxRestoreSeed.wrappedSeed(configuration, replay: ["sleep", "100"], durable: nil,
                                                denylist: [], daemonRunning: daemonRunning)
        #expect(seed.command == configuration.command)
        #expect(seed.initialInput == nil)
    }

    @Test("a denylisted capture after daemon loss gives a plain shell with nothing typed")
    func liveRecoveryDenylisted() {
        let seed = LinuxRestoreSeed.wrappedSeed(configuration, replay: ["tmux", "attach"], durable: "make dev",
                                                denylist: ["tmux"], daemonRunning: false)
        #expect(seed.command == configuration.command)
        #expect(seed.initialInput == nil)
    }

    @Test("with no capture, the session's own creation command is still how a lost daemon is recreated")
    func durableCreationCommandUnchanged() {
        let seed = LinuxRestoreSeed.wrappedSeed(configuration, replay: nil, durable: "make dev", denylist: [],
                                                daemonRunning: false)
        #expect(seed.command == ZmxSupport.attachCommand(configuration, replaying: nil, creationCommand: "make dev",
                                                         denylist: []))
        #expect(seed.initialInput == nil)
    }

    @Test("actual Live emitter survives a competing creator and preserves new-shell startup")
    func realCreationBoundPrefill() throws {
        let repository = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let zmx = repository.appendingPathComponent("agterm-linux/vendor/zmx/zmx").path
        try #require(FileManager.default.isExecutableFile(atPath: zmx), "build vendor zmx before this integration test")
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("prefill-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let environment = ["SHELL": "/bin/zsh",
                           "ZDOTDIR": repository.appendingPathComponent("agterm-linux/vendor/ghostty/share/ghostty/shell-integration/zsh").path,
                           "GHOSTTY_ZSH_ZDOTDIR": directory.appendingPathComponent("z").path]
        let name = "agterm-prefill-\(UUID().uuidString.prefix(8))"
        let config = ZmxSupport.Configuration(executablePath: zmx, environment: environment,
                                             daemonName: name, socketDirectory: directory.appendingPathComponent("s").path,
                                             paneID: "fixture")
        let seed = LinuxRestoreSeed.wrappedSeed(config, replay: ["/bin/sh", "-c", "printf %s CREATION_BOUND_OK > \"$AGTERM_PREFILL_REPORT\""],
                                                durable: "false", denylist: [], daemonRunning: false)
        let plan: [String: Any] = ["command": try #require(seed.command), "initialInput": seed.initialInput ?? "",
                                   "environment": environment, "name": name, "root": directory.path, "zmx": zmx]
        let path = directory.appendingPathComponent("plan.json")
        try JSONSerialization.data(withJSONObject: plan).write(to: path)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [repository.appendingPathComponent("scripts/tests/test_zmx_prefill.py").path,
                             "--production-plan", path.path]
        try process.run()
        process.waitUntilExit()
        #expect(process.terminationStatus == 0, "real-zmx production emitter regression failed")
    }

}
