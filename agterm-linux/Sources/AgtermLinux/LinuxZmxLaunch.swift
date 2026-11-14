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
    /// Latched once for the app run: reloading config cannot rename a running daemon's shell.
    static let liveCommandShell: String? = {
        let settings = linuxSettingsStore().load(), env = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser
        let directory = ConfigPaths.configDirectory(setting: settings.configDirectory, stateDir: env["AGTERM_STATE_DIR"], home: home)
        var paths: [String] = []
        if settings.inheritGlobalGhosttyConfig == true {
            let configHome = env["XDG_CONFIG_HOME"] ?? home.appendingPathComponent(".config").path
            let folder = URL(fileURLWithPath: configHome).appendingPathComponent("ghostty")
            paths += [folder.appendingPathComponent("config").path, folder.appendingPathComponent("config.ghostty").path]
        }
        paths.append(ConfigPaths.ghosttyConfigPath(configDirectory: directory).path)
        let pairs = GhosttyConfigText.keyValues(loading: paths, home: home.path) {
            try? String(contentsOfFile: $0, encoding: .utf8)
        }
        guard case .value(let value) = GhosttyConfigText.lastSetting(of: "command", in: pairs, parse: { $0 }) else { return nil }
        return ZmxSupport.commandShell(fromCommand: value)
    }()

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
            inheritedZdotdir: environment["ZDOTDIR"], lead: lead, commandShell: liveCommandShell,
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
