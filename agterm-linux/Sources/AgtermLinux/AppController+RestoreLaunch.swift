import agtermCore
import Foundation

struct LinuxLaunchSeed: Equatable {
    let command: String?
    let initialInput: String?
    let waitAfterCommand: Bool
}

/// The pure seed decisions behind a restored Linux pane, kept off the main actor so tests can pin them.
///
/// Linux policy: a restored command is PRE-FILLED at the prompt and waits for Enter; it never runs by itself.
/// Every line typed here therefore ends without CR/LF, and `CommandRestore.shouldRestore` (captures) and
/// `CommandRestore.restoreInput` (pins) refuse any control character, so nothing inside a line can submit it.
/// The shared core keeps its newline-terminated input for macOS; the Linux target drops it here.
enum LinuxRestoreSeed {
    /// The captured foreground argv as the line pre-filled into a restored login shell, or nil when the launch is
    /// not in rerun mode or `CommandRestore.shouldRestore` refuses it (denylisted, control character, invalid UTF-8).
    static func restoreLine(_ argv: [String]?, restoreEnabled: Bool, denylist: Set<String>) -> String? {
        guard restoreEnabled, let argv, CommandRestore.shouldRestore(argv: argv, denylist: denylist) else { return nil }
        return CommandRestore.shellQuotedLine(argv)
    }

    /// The ordinary (non-zmx) seed for a restore plan, with the pinned line's trailing newline removed.
    static func ordinarySeed(_ plan: CommandRestore.RestorePlan) -> LinuxLaunchSeed {
        LinuxLaunchSeed(command: plan.command, initialInput: plan.initialInput.map(prefilled),
                        waitAfterCommand: plan.waitAfterCommand)
    }

    /// The zmx-backed seed. `daemonRunning` is whether the launch inventory listed this pane's daemon (nil: unknown).
    ///
    /// With a capture, zmx is never handed a command to run: the attach is the plain one, so a missing daemon is
    /// created with the normal login shell, and the captured line is pre-filled only when the inventory says the
    /// daemon is gone — typing into a live (or unknown) session would land in whatever it is running. A capture,
    /// even a refused one, still preempts the durable creation command, so an unknown inventory drops the capture.
    /// The line is typed at the tty, so the first reader takes it: a startup file that reads input can consume it.
    /// Without a capture the session's own creation command recreates a lost daemon, as before.
    static func wrappedSeed(_ configuration: ZmxSupport.Configuration, replay: [String]?, durable: String?,
                            denylist: Set<String>, daemonRunning: Bool?) -> LinuxLaunchSeed {
        guard let replay else {
            return LinuxLaunchSeed(
                command: ZmxSupport.attachCommand(configuration, replaying: nil, creationCommand: durable,
                                                  denylist: denylist),
                initialInput: nil, waitAfterCommand: false
            )
        }
        let line = daemonRunning == false ? restoreLine(replay, restoreEnabled: true, denylist: denylist) : nil
        return LinuxLaunchSeed(command: ZmxSupport.attachCommand(configuration, replaying: nil, denylist: denylist),
                               initialInput: line, waitAfterCommand: false)
    }

    private static func prefilled(_ input: String) -> String {
        input.hasSuffix("\n") ? String(input.dropLast()) : input
    }
}

struct LinuxPaneLaunchProvider {
    let environment: [String: String]
    let backedByZmx: Bool
    let shouldPace: Bool
    let resolve: @MainActor (StatusPane) -> LinuxLaunchSeed
}

struct LinuxLaunchSeedPolicy {
    let restoreEnabled: Bool
    let denylist: Set<String>
    let runningNames: Set<String>?

    /// Replay reads the launch latch, never current settings: a restore-mode change applies after restart.
    init(launch decision: RestoreLaunchDecision, denylist: Set<String>, runningNames: Set<String>?) {
        restoreEnabled = decision.active == .rerun
        self.denylist = denylist
        self.runningNames = runningNames
    }
}

@MainActor
extension AppController {
    /// Defers consuming captured argv and restore overrides until libghostty actually creates the pane.
    /// A paced pane may be swapped while waiting, so the provider resolves against its live role.
    func paneLaunchProvider(for session: Session, pane: StatusPane) -> LinuxPaneLaunchProvider {
        let baseEnvironment = sessionEnv(for: session, pane: pane)
        let identity = pane == .right ? session.splitPaneIdentity : session.paneIdentity
        let decision = gRestoreLaunchDecision
        let disposition: ZmxSupport.LaunchDisposition
        let lead = ZmxLeadAttachment(claim: false)
        if decision.requested == .live, session.remoteHost == nil, let identity {
            let configuration = try? LinuxZmxLaunch.configuration(
                paneIdentity: identity, baseEnvironment: baseEnvironment, lead: lead
            ).get()
            disposition = ZmxSupport.launchDisposition(
                requested: decision.requested, active: decision.active, configuration: configuration
            )
        } else {
            disposition = .ordinary
        }
        if disposition.backedByZmx, let identity {
            ZmxLeadBook.shared.begin(lead, pane: identity)
        }
        let policy = LinuxLaunchSeedPolicy(
            launch: decision, denylist: restoreDenylist(), runningNames: gZmxRunningNames
        )
        let shouldPace = shouldPace(
            session: session, pane: pane, disposition: disposition, policy: policy
        )
        let environment: [String: String]
        if case .wrapped(let configuration) = disposition {
            environment = configuration.environment
        } else {
            environment = baseEnvironment
        }
        return LinuxPaneLaunchProvider(
            environment: environment,
            backedByZmx: disposition.backedByZmx,
            shouldPace: shouldPace
        ) { [weak self, weak session] livePane in
            guard let self, let session else {
                return Self.unownedSeed(disposition: disposition)
            }
            return self.launchSeed(
                session: session, pane: livePane, disposition: disposition, policy: policy
            )
        }
    }

    private func launchSeed(session: Session, pane: StatusPane,
                            disposition: ZmxSupport.LaunchDisposition,
                            policy: LinuxLaunchSeedPolicy) -> LinuxLaunchSeed {
        switch disposition {
        case .wrapped(let configuration):
            return LinuxRestoreSeed.wrappedSeed(
                configuration, replay: session.takePendingForegroundCommand(pane: pane),
                durable: durableCommand(session: session, pane: pane), denylist: policy.denylist,
                daemonRunning: policy.runningNames.map { $0.contains(configuration.daemonName) }
            )
        case .ordinary:
            let capture = session.takePendingForegroundCommand(pane: pane)
            let plan = CommandRestore.restorePlan(.init(
                wasRestored: session.wasRestored,
                restoreEnabled: policy.restoreEnabled,
                hadForeground: capture != nil,
                foregroundInput: restoreInput(capture, policy: policy),
                initialCommand: durableCommand(session: session, pane: pane),
                restoreOverride: session.takePendingRestoreOverride(pane: pane),
                requestedWait: requestedWait(session: session, pane: pane)
            ))
            return LinuxRestoreSeed.ordinarySeed(plan)
        case .fallback:
            let plan = CommandRestore.restorePlan(.init(
                wasRestored: session.wasRestored,
                restoreEnabled: false,
                hadForeground: false,
                foregroundInput: nil,
                initialCommand: durableCommand(session: session, pane: pane),
                restoreOverride: nil,
                requestedWait: requestedWait(session: session, pane: pane)
            ))
            return LinuxLaunchSeed(command: plan.command, initialInput: plan.initialInput,
                                   waitAfterCommand: plan.waitAfterCommand)
        }
    }

    private func shouldPace(session: Session, pane: StatusPane,
                            disposition: ZmxSupport.LaunchDisposition,
                            policy: LinuxLaunchSeedPolicy) -> Bool {
        guard session.wasRestored else { return false }
        switch disposition {
        case .wrapped(let configuration):
            if policy.runningNames?.contains(configuration.daemonName) == true { return false }
            if let capture = peekCapture(session: session, pane: pane) {
                return CommandRestore.shouldRestore(argv: capture, denylist: policy.denylist)
            }
            return durableCommand(session: session, pane: pane) != nil
        case .ordinary:
            let capture = peekCapture(session: session, pane: pane)
            let plan = CommandRestore.restorePlan(.init(
                wasRestored: true,
                restoreEnabled: policy.restoreEnabled,
                hadForeground: capture != nil,
                foregroundInput: restoreInput(capture, policy: policy),
                initialCommand: durableCommand(session: session, pane: pane),
                restoreOverride: peekRestoreOverride(session: session, pane: pane),
                requestedWait: false
            ))
            return plan.command != nil || plan.initialInput != nil
        case .fallback:
            return false
        }
    }

    private func restoreInput(_ argv: [String]?, policy: LinuxLaunchSeedPolicy) -> String? {
        LinuxRestoreSeed.restoreLine(argv, restoreEnabled: policy.restoreEnabled, denylist: policy.denylist)
    }

    private func peekCapture(session: Session, pane: StatusPane) -> [String]? {
        switch pane {
        case .left: session.pendingForegroundCommand
        case .right: session.pendingSplitForegroundCommand
        case .scratch: nil
        }
    }

    private func peekRestoreOverride(session: Session, pane: StatusPane) -> String? {
        switch pane {
        case .left: session.pendingRestoreCommand
        case .right: session.pendingSplitRestoreCommand
        case .scratch: nil
        }
    }

    private func durableCommand(session: Session, pane: StatusPane) -> String? {
        switch pane {
        case .left: session.initialCommand
        case .right: session.splitInitialCommand
        case .scratch: nil
        }
    }

    private func requestedWait(session: Session, pane: StatusPane) -> Bool {
        switch pane {
        case .left: session.commandWait
        case .right: session.splitCommandWait
        case .scratch: false
        }
    }

    private static func unownedSeed(disposition: ZmxSupport.LaunchDisposition) -> LinuxLaunchSeed {
        guard case .wrapped(let configuration) = disposition else {
            return LinuxLaunchSeed(command: nil, initialInput: nil, waitAfterCommand: false)
        }
        return LinuxLaunchSeed(command: configuration.command, initialInput: nil,
                               waitAfterCommand: false)
    }

    private func restoreDenylist() -> Set<String> {
        let path = ConfigPaths.restoreDenylistPath(configDirectory: configDirectory())
        return (try? String(contentsOf: path, encoding: .utf8)).map(CommandRestore.parseDenylist)
            ?? ["tmux", "screen", "zellij"]
    }
}
