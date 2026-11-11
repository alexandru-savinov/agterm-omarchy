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

    @Test("Live recovery after daemon loss starts a plain shell and pre-fills the captured command")
    func liveRecoveryPrefills() {
        let seed = LinuxRestoreSeed.wrappedSeed(configuration, replay: ["sleep", "100"], durable: "make dev",
                                                denylist: [], daemonRunning: false)
        #expect(seed.command == ZmxSupport.attachCommand(configuration, replaying: nil, denylist: []))
        #expect(seed.command?.contains("-lic") == false)
        #expect(seed.command?.contains("sleep") == false)
        #expect(seed.command?.contains("make dev") == false, "a capture still preempts the durable command")
        #expect(seed.initialInput == "'sleep' '100'")
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
        #expect(seed.command == ZmxSupport.attachCommand(hosted, replaying: nil, denylist: []))
        #expect(seed.initialInput == "'sleep' '100'")
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
}
