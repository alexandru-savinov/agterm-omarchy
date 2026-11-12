import Foundation
import agtermCore
#if canImport(Glibc)
import Glibc
#endif

enum LinuxZmxLaunch {
    /// zmx ignores creation argv when the named daemon already exists.
    static func prefillCommand(_ configuration: ZmxSupport.Configuration, line: String) -> String? {
        guard let shell = configuration.environment["SHELL"],
              let integration = configuration.environment["ZDOTDIR"],
              let shim = Bundle.module.resourceURL?.appendingPathComponent("live-prefill"),
              FileManager.default.isReadableFile(atPath: shim.appendingPathComponent(".zshenv").path) else { return nil }
        var arguments = configuration.attachArguments + [
            "/usr/bin/env", "ZDOTDIR=\(shim.path)", "AGTERM_LIVE_INTEGRATION=\(integration)",
            "AGTERM_LIVE_PREFILL=\(line)", shell, "-il"
        ]
        if let host = configuration.sessionHostExecutablePath {
            arguments = [host, "client", configuration.daemonName, "--"] + arguments
        }
        return CommandRestore.shellQuotedLine(arguments)
    }

    static func executablePath(environment: [String: String] = ProcessInfo.processInfo.environment) -> String {
        if let override = environment["AGTERM_ZMX_PATH"], !override.isEmpty {
            return URL(fileURLWithPath: override).standardizedFileURL.path
        }
        let executable = URL(fileURLWithPath: CommandLine.arguments.first ?? "")
            .standardizedFileURL.resolvingSymlinksInPath()
        let installed = executable.deletingLastPathComponent().appendingPathComponent("zmx").path
        if FileManager.default.isExecutableFile(atPath: installed) { return installed }
        return URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("vendor/zmx/zmx").path
    }

    static func liveUnavailableReason() -> String? {
        switch configuration(paneIdentity: UUID(), baseEnvironment: [:]) {
        case .success: nil
        case .failure(let rejection): rejection.message
        }
    }

    /// The socket directory for this process, chosen once (`ZmxSocketDirectory.resolveLinux` documents
    /// the order and the upgrade rule) so every pane and the zmx client use the same one for the run.
    static let socketDirectoryChoice: Result<String, ZmxSocketDirectory.Refusal> = ZmxSocketDirectory.resolveLinux(
        stateDirectory: linuxStateDirectory().path, environment: ProcessInfo.processInfo.environment
    )

    /// The chosen directory, re-verified (and re-created as 0700 if it vanished) at this call.
    static func verifiedSocketDirectory() -> Result<String, ZmxSocketDirectory.Refusal> {
        socketDirectoryChoice.flatMap { ZmxSocketDirectory.verify($0) }
    }

    static func configuration(paneIdentity: UUID, baseEnvironment: [String: String],
                              lead: ZmxLeadAttachment? = nil)
        -> Result<ZmxSupport.Configuration, ZmxSupport.Rejection> {
        let environment = ProcessInfo.processInfo.environment
        let resources = GhosttyResourceResolver(
            candidates: ghosttyResourceCandidates(),
            fileExists: { FileManager.default.fileExists(atPath: $0) }
        ).resolve()
        return ZmxSupport.configuration(for: .init(
            zmxExecutablePath: executablePath(environment: environment),
            passwordDatabaseShell: passwordDatabaseLoginShell(),
            resourcesDirectory: resources,
            stateDirectory: linuxStateDirectory().path,
            paneIdentity: paneIdentity,
            baseEnvironment: baseEnvironment,
            inheritedZdotdir: environment["ZDOTDIR"], lead: lead,
            socketDirectory: { verifiedSocketDirectory() }
        ))
    }

    static func passwordDatabaseLoginShell() -> String? {
        guard let entry = getpwuid(getuid()), let shell = entry.pointee.pw_shell else { return nil }
        let value = String(cString: shell)
        return value.isEmpty ? nil : value
    }
}

@MainActor var gRestoreLaunchDecision = RestoreMode.none.launchDecision(liveUnavailableReason: nil)
@MainActor var gZmxClient: LinuxZmxClient?
@MainActor var gZmxRunningNames: Set<String>?
