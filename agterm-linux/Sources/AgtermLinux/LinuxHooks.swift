import Foundation
import agtermCore
#if canImport(Glibc)
import Glibc
#endif

@MainActor var gHooks: LinuxHooks?

/// GTK's main loop owns hook state; the shared scheduler owns ordering and queue limits.
@MainActor
final class LinuxHooks {
    private let scheduler = HookScheduler(launcher: LinuxHookLauncher())
    private var diagnostics: [KeymapDiagnostic] = []
    private var path: URL

    init(library: WindowLibrary, configDirectory: URL) {
        path = ConfigPaths.hooksPath(configDirectory: configDirectory)
        scheduler.onFailure = { entry, detail in
            gController?.showToast("Hook \(entry.kind.rawValue) failed: \(detail)")
        }
        library.onControlEvent = { [scheduler] event in scheduler.dispatch(event) }
        reload(configDirectory: configDirectory)
    }

    @discardableResult
    func reload(configDirectory: URL) -> Int {
        path = ConfigPaths.hooksPath(configDirectory: configDirectory)
        do {
            let parsed = parseHooksConf(try String(contentsOf: path, encoding: .utf8))
            diagnostics = parsed.diagnostics
            scheduler.apply(parsed.hooks)
        } catch {
            diagnostics = FileManager.default.fileExists(atPath: path.path)
                ? [KeymapDiagnostic(line: 0, message: "could not read hooks.conf: \(error.localizedDescription)")]
                : []
            scheduler.apply(Hooks())
        }
        return diagnostics.count
    }

    func list() -> ControlHooks {
        ControlHooks(path: path.path,
                     diagnostics: diagnostics.map { ControlKeymapDiagnostic(line: $0.line, message: $0.message) },
                     hooks: scheduler.status)
    }
}

@MainActor
extension AppController {
    func reloadHooks() -> ControlResponse {
        guard let hooks = gHooks else { return err("hooks are unavailable") }
        let count = hooks.reload(configDirectory: configDirectory())
        return ControlResponse(ok: true, result: ControlResult(count: count))
    }

    func listHooks() -> ControlResponse {
        guard let hooks = gHooks else { return err("hooks are unavailable") }
        return ControlResponse(ok: true, result: ControlResult(hooks: hooks.list()))
    }
}

/// Launch one shell per event and finish on GLib's main thread after stdin closes and the child exits.
/// The control server ignores SIGPIPE, so a hook exiting before reading yields EPIPE to the writer.
@MainActor
final class LinuxHookLauncher: HookLauncher {
    private let shellPath: String
    private let socketPath: @MainActor () -> String

    init(shellPath: String = "/bin/sh", socketPath: @escaping @MainActor () -> String = { gControlServer.resolvedSocketPath }) {
        self.shellPath = shellPath
        self.socketPath = socketPath
    }

    func launch(entry: HookEntry, event: ControlEvent,
                onDeliveryFailure: @escaping @MainActor @Sendable (String) -> Void,
                onExit: @escaping @MainActor @Sendable (Int32) -> Void) throws -> Int32 {
        var environment = gdkEnvironment.restoringChildEnvironment(ProcessInfo.processInfo.environment)
        environment["AGT_EVENT_KIND"] = event.kind.rawValue
        environment["AGT_EVENT_STATUS"] = event.payload.status ?? ""
        environment["AGT_EVENT_HOST"] = event.payload.host ?? ""
        environment["AGT_SESSION_ID"] = event.session ?? ""
        environment["AGT_WORKSPACE_ID"] = event.workspace ?? ""
        environment["AGT_WINDOW_ID"] = event.window ?? ""
        environment["AGT_SOCKET"] = socketPath()
        let executableDirectory = URL(fileURLWithPath: CommandLine.arguments[0])
            .standardizedFileURL.resolvingSymlinksInPath().deletingLastPathComponent().path
        environment["PATH"] = CommandPath.widened(environment["PATH"], bundledCLIDirectory: executableDirectory)

        let process = Process()
        process.executableURL = URL(fileURLWithPath: shellPath)
        process.arguments = ["-c", entry.command]
        process.environment = environment
        let input = Pipe()
        process.standardInput = input
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        let data = try JSONEncoder().encode(event) + Data("\n".utf8)
        try process.run()
        DispatchQueue.global(qos: .utility).async {
            // A hook may exit without reading its event; its exit status is the outcome. Written with
            // write(2) because Foundation on Linux reports EPIPE as CocoaError 512, not a POSIX error.
            if let failure = Self.write(data, to: input.fileHandleForWriting.fileDescriptor), failure != EPIPE {
                let detail = String(cString: strerror(failure))
                runOnMain { MainActor.assumeIsolated { onDeliveryFailure(detail) } }
            }
            try? input.fileHandleForWriting.close()
            process.waitUntilExit()
            let status = process.terminationStatus
            runOnMain { MainActor.assumeIsolated { onExit(status) } }
        }
        return process.processIdentifier
    }

    /// nil once every byte is written, otherwise the errno that stopped it.
    nonisolated static func write(_ data: Data, to descriptor: Int32) -> Int32? {
        data.withUnsafeBytes { raw -> Int32? in
            guard let base = raw.baseAddress else { return nil }
            var offset = 0
            while offset < raw.count {
                let count = Glibc.write(descriptor, base + offset, raw.count - offset)
                if count < 0, errno == EINTR { continue }
                guard count > 0 else { return count < 0 ? errno : EIO }
                offset += count
            }
            return nil
        }
    }
}
