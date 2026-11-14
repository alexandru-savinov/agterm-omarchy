import Foundation
import agtermCore
#if canImport(Glibc)
import Glibc
#endif

enum LinuxProcessStandardIO: Sendable, Equatable {
    case null
    case stderrFile(String)
}

struct LinuxProcessLaunchRequest: Sendable, Equatable {
    let executablePath: String
    let arguments: [String]
    let environment: [String: String]
    let currentDirectoryPath: String?
    let standardIO: LinuxProcessStandardIO
}

enum LinuxCommandPath {
    private static let systemDefault = "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin"

    static var bundledCLIDirectory: String? {
        let executable = try? FileManager.default.destinationOfSymbolicLink(atPath: "/proc/self/exe")
        return resolvedExecutableDirectory(executable)
    }

    static func resolvedExecutableDirectory(_ executable: String?) -> String? {
        guard let executable, executable.hasPrefix("/") else { return nil }
        return URL(fileURLWithPath: executable).standardizedFileURL.deletingLastPathComponent().path
    }

    static func widened(
        _ path: String?, bundledCLIDirectory: String?, homeDirectory: String
    ) -> String {
        var result: [String] = []
        var seen = Set<String>()
        func add(_ entry: String) {
            guard !entry.isEmpty, seen.insert(entry).inserted else { return }
            result.append(entry)
        }
        bundledCLIDirectory.map(add)
        let base = path.flatMap { $0.isEmpty ? nil : $0 } ?? systemDefault
        base
            .split(separator: ":", omittingEmptySubsequences: true).forEach { add(String($0)) }
        add((homeDirectory as NSString).appendingPathComponent(".local/bin"))
        systemDefault.split(separator: ":").forEach { add(String($0)) }
        return result.joined(separator: ":")
    }
}

protocol LinuxProcessLaunching: Sendable {
    func launch(
        _ request: LinuxProcessLaunchRequest,
        onTermination: @escaping @Sendable (Int32) -> Void
    ) throws
}

/// posix_spawn and a waitpid thread, not Foundation's Process: on Linux, Process reports exit only after every
/// descendant holding its inherited status socket has closed it, so a backgrounded child stalled completion.
struct SpawnLinuxProcessLauncher: LinuxProcessLaunching {
    func launch(
        _ request: LinuxProcessLaunchRequest,
        onTermination: @escaping @Sendable (Int32) -> Void
    ) throws {
        var actions = posix_spawn_file_actions_t()
        posix_spawn_file_actions_init(&actions)
        defer { posix_spawn_file_actions_destroy(&actions) }
        posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0)
        posix_spawn_file_actions_addopen(&actions, 1, "/dev/null", O_WRONLY, 0)
        switch request.standardIO {
        case .null:
            posix_spawn_file_actions_addopen(&actions, 2, "/dev/null", O_WRONLY, 0)
        case .stderrFile(let path):
            posix_spawn_file_actions_addopen(&actions, 2, path, O_WRONLY, 0)
        }
        if let path = request.currentDirectoryPath { posix_spawn_file_actions_addchdir_np(&actions, path) }
        posix_spawn_file_actions_addclosefrom_np(&actions, 3)
        var attributes = posix_spawnattr_t()
        posix_spawnattr_init(&attributes)
        defer { posix_spawnattr_destroy(&attributes) }
        posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETPGROUP))

        var pid = pid_t()
        let arguments = [request.executablePath] + request.arguments
        let environment = request.environment.map { "\($0.key)=\($0.value)" }
        let status = withCStringArray(arguments) { argv in
            withCStringArray(environment) { envp in
                posix_spawn(&pid, request.executablePath, &actions, &attributes, argv, envp)
            }
        }
        guard status == 0 else {
            throw NSError(domain: NSPOSIXErrorDomain, code: Int(status), userInfo: [
                NSLocalizedDescriptionKey: "\(request.executablePath): \(String(cString: strerror(status)))"
            ])
        }
        let child = pid
        Thread.detachNewThread { onTermination(Self.waitForExit(child)) }
    }

    /// The exit status, or the signal number for a signalled child, as Foundation's `terminationStatus` reports.
    private static func waitForExit(_ pid: pid_t) -> Int32 {
        var status: Int32 = 0
        while waitpid(pid, &status, 0) < 0, errno == EINTR {}
        let signal = status & 0x7f
        return signal == 0 ? (status >> 8) & 0xff : signal
    }
}

private func withCStringArray<R>(_ strings: [String], _ body: ([UnsafeMutablePointer<CChar>?]) -> R) -> R {
    let pointers = strings.map { strdup($0) } + [nil]
    defer { pointers.forEach { free($0) } }
    return body(pointers)
}

enum LinuxCustomCommandFailure: Sendable, Equatable {
    case launch(String)
    case exit(Int32, String?)

    var reason: String {
        switch self {
        case .launch(let detail): detail
        case .exit(let status, _): "exit \(status)"
        }
    }

    var detail: String? {
        if case .exit(_, let detail) = self { return detail }
        return nil
    }

    func toast(commandName: String) -> String {
        switch self {
        case .launch(let detail): "command failed to launch: \(commandName) — \(detail)"
        case .exit(let status, _): "command failed (exit \(status)): \(commandName)"
        }
    }
}

enum LinuxCustomCommandProcess {
    /// The environment-assembly choke point for custom commands, which is why the GDK restore is applied
    /// here rather than at a caller or in a replaceable default argument. It must WIN over the assembled
    /// environment (`restoringChildEnvironment`): the base is normally a copy of agterm's own already-
    /// mutated process environment, so losing to the caller would hand the child the overrides straight
    /// back.
    static func request(
        command: CustomCommand, context: CommandContext, baseEnvironment: [String: String],
        stderrPath: String? = nil, localWorkingDirectory: String? = nil
    ) -> LinuxProcessLaunchRequest {
        var environment = baseEnvironment.merging(context.environment()) { _, commandValue in commandValue }
        let home = baseEnvironment["HOME"] ?? FileManager.default.homeDirectoryForCurrentUser.path
        environment["PATH"] = LinuxCommandPath.widened(
            baseEnvironment["PATH"], bundledCLIDirectory: LinuxCommandPath.bundledCLIDirectory,
            homeDirectory: home)
        environment = gdkEnvironment.restoringChildEnvironment(environment)
        return LinuxProcessLaunchRequest(
            executablePath: "/bin/sh",
            arguments: ["-c", context.expand(command.command)],
            environment: environment,
            currentDirectoryPath: (localWorkingDirectory ?? context.sessionPWD).isEmpty
                ? nil : (localWorkingDirectory ?? context.sessionPWD),
            standardIO: stderrPath.map(LinuxProcessStandardIO.stderrFile) ?? .null)
    }

    @discardableResult
    static func launch(
        command: CustomCommand,
        context: CommandContext,
        baseEnvironment: [String: String] = ProcessInfo.processInfo.environment,
        localWorkingDirectory: String? = nil,
        launcher: any LinuxProcessLaunching,
        onFailure: @escaping @Sendable (LinuxCustomCommandFailure) -> Void
    ) -> Bool {
        let capture = command.errorHud ? LinuxCommandStderrCapture() : nil
        let request = request(command: command, context: context, baseEnvironment: baseEnvironment,
                              stderrPath: capture?.path, localWorkingDirectory: localWorkingDirectory)
        do {
            try launcher.launch(request) { status in
                let detail = capture?.consume()
                if status != 0 { onFailure(.exit(status, detail)) }
            }
            return true
        } catch {
            _ = capture?.consume()
            onFailure(.launch(error.localizedDescription))
            return false
        }
    }
}

/// A file lets a command's descendants keep stderr after the app exits without inheriting a broken pipe.
/// Read only the bounded tail on completion, then unlink it even after a successful command.
private final class LinuxCommandStderrCapture: @unchecked Sendable {
    let path: String

    init?() {
        let path = FileManager.default.temporaryDirectory
            .appendingPathComponent("agterm-command-\(UUID().uuidString).err").path
        // createFile follows the umask; O_EXCL with an explicit 0600 cannot be widened by it.
        let descriptor = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0o600)
        guard descriptor >= 0 else { return nil }
        close(descriptor)
        self.path = path
    }

    func consume() -> String? {
        defer { try? FileManager.default.removeItem(atPath: path) }
        guard let reader = try? FileHandle(forReadingFrom: URL(fileURLWithPath: path)) else { return nil }
        defer { try? reader.close() }
        let size = (try? reader.seekToEnd()) ?? 0
        let wanted = min(size, UInt64(CommandFailure.tailLimit))
        try? reader.seek(toOffset: size - wanted)
        return CommandFailure.detail(fromTail: [UInt8]((try? reader.read(upToCount: Int(wanted))) ?? Data()))
    }
}

/// A per-controller generation token. Closing a window invalidates this instance; reopening the same
/// persisted window id creates a different token, so an old process completion cannot reach the new UI.
@MainActor
final class LinuxCustomCommandOrigin {
    let launcher: any LinuxProcessLaunching
    private(set) var isActive = true

    init(launcher: any LinuxProcessLaunching = SpawnLinuxProcessLauncher()) {
        self.launcher = launcher
    }

    func invalidate() { isActive = false }

    func deliverIfActive(_ action: () -> Void) {
        guard isActive else { return }
        action()
    }
}
