import Foundation
import Glibc

/// The one-time automatic switch to Mesa software rendering after the default renderer fails to create the
/// terminal's GL context.
///
/// GtkGLArea reports a context failure on the first surface's realize, before any terminal ran. Mesa reads
/// `LIBGL_ALWAYS_SOFTWARE` once when its driver loads, which has already happened by then, so the only way
/// to act on it is to replace the process image: agterm re-execs itself once with the variable set and a
/// guard variable that forbids a second attempt. The new image shows a one-time notice once its first
/// surface renders. The decisions are pure so the guard, the opt-outs and the child-environment
/// bookkeeping are testable without GTK; `relaunch(…)` is the only side effect.
///
/// The exec environment is the PRE-LAUNCH environment (`preLaunchProcessEnvironment`), never the live one:
/// by first realize `main()` has rewritten `GDK_DISABLE`/`GDK_DEBUG` and removed the launcher's EGL shim
/// from `LD_PRELOAD`, and a relaunch from those values would lose the shim and record agterm's own GDK
/// value as the user's. Both added variables are recorded in the launcher's `AGTERM_ORIG_*` bookkeeping,
/// exactly as `bin/agterm-linux`'s `agterm_set` does, so no pane, hook, custom command or zmx daemon
/// inherits them (`LauncherChildEnvironment`).
enum LinuxSoftwareRenderingFallback {
    static let softwareKey = "LIBGL_ALWAYS_SOFTWARE"
    /// Set in the relaunched image only; its presence means "this process IS the fallback".
    static let guardKey = "AGTERM_GL_SOFTWARE_FALLBACK"
    /// `AGTERM_DISABLE_GL_FALLBACK=1` keeps the GL-error overlay instead of relaunching.
    static let disableKey = "AGTERM_DISABLE_GL_FALLBACK"

    static let notice = "OpenGL failed with the default renderer, so agterm restarted with software rendering. " +
        "To keep software rendering, reinstall with scripts/install-linux.sh --software-rendering " +
        "or set LIBGL_ALWAYS_SOFTWARE=1."

    /// Mesa's own boolean reading of the variable (`env_var_as_boolean`): only these spellings select
    /// software rendering; empty, `0`, `false` and unknown values leave the hardware driver in charge.
    static func requestsSoftware(_ value: String?) -> Bool {
        guard let value else { return false }
        return ["1", "true", "y", "yes"].contains(value.lowercased())
    }

    static func isFallbackLaunch(_ environment: [String: String]) -> Bool {
        environment[guardKey]?.isEmpty == false
    }

    /// The environment to re-exec with after a GL context failure, or nil when agterm must instead keep the
    /// GL-error overlay: a terminal surface already rendered in this process (a later failure must never
    /// restart live sessions), this process is already the fallback (never loop), the user disabled it, or
    /// software rendering was already requested (the fallback could not change anything).
    static func relaunchEnvironment(preLaunch environment: [String: String],
                                    surfaceEverCreated: Bool) -> [String: String]? {
        guard !surfaceEverCreated,
              !isFallbackLaunch(environment),
              environment[disableKey] != "1",
              !requestsSoftware(environment[softwareKey]) else { return nil }
        var result = environment
        if result[LauncherChildEnvironment.recordMarkerKey]?.isEmpty != false {
            // No launcher record for THIS process (a direct binary or AppImage run). Any AGTERM_ORIG_* keys
            // are stale blanks from an outer pane that restore() would ignore; drop them before writing a
            // record of our own, or the marker would revive them as originals.
            for key in result.keys where key.hasPrefix(LauncherChildEnvironment.originalPrefix) {
                result.removeValue(forKey: key)
            }
            result[LauncherChildEnvironment.recordMarkerKey] = "1"
        }
        record(softwareKey, value: "1", in: &result)
        record(guardKey, value: "1", in: &result)
        return result
    }

    /// `agterm_set` from the launcher: keep the caller's value as `AGTERM_ORIG_<NAME>`, or list the name as
    /// unset, then assign.
    private static func record(_ name: String, value: String, in environment: inout [String: String]) {
        let unsetKey = LauncherChildEnvironment.unsetListKey
        if let original = environment[name] {
            environment[LauncherChildEnvironment.originalPrefix + name] = original
        } else {
            let listed = environment[unsetKey] ?? ""
            environment[unsetKey] = listed.isEmpty ? name : "\(listed) \(name)"
        }
        environment[name] = value
    }

    /// The stderr line written just before the relaunch.
    static let relaunchLogLine =
        "agterm: GL context failed with the default renderer; restarting once with LIBGL_ALWAYS_SOFTWARE=1"

    // MARK: - Effects

    /// Set once the first libghostty surface exists; from then on a GL failure keeps the overlay.
    @MainActor static var surfaceEverCreated = false
    @MainActor private static var noticeShown = false

    /// True once, in the relaunched image, when its first surface rendered: the caller shows the notice.
    @MainActor static func takePendingNotice(environment: [String: String] = preLaunchProcessEnvironment) -> Bool {
        guard isFallbackLaunch(environment), !noticeShown else { return false }
        noticeShown = true
        return true
    }

    /// Replace this process with a software-rendering copy of itself when eligible. Returns only when not
    /// eligible or the exec failed, and the caller then shows the GL-error overlay.
    @MainActor static func relaunchIfEligible(environment: [String: String] = preLaunchProcessEnvironment,
                                              arguments: [String] = CommandLine.arguments) {
        guard let target = relaunchEnvironment(preLaunch: environment,
                                               surfaceEverCreated: surfaceEverCreated) else { return }
        // The real path keeps the loader's $ORIGIN (the launcher's LD_PRELOAD shim) resolving as before.
        let executable = (try? FileManager.default.destinationOfSymbolicLink(atPath: "/proc/self/exe"))
            ?? "/proc/self/exe"
        FileHandle.standardError.write(Data((relaunchLogLine + "\n").utf8))
        markDescriptorsCloseOnExec()
        let argv = (arguments.isEmpty ? [executable] : arguments).map { strdup($0) } + [nil]
        let envp = target.map { strdup("\($0.key)=\($0.value)") } + [nil]
        execve(executable, argv, envp)
        let reason = String(cString: strerror(errno))
        FileHandle.standardError.write(Data("agterm: software-rendering restart failed: \(reason)\n".utf8))
        argv.forEach { free($0) }
        envp.forEach { free($0) }
    }

    /// Descriptors not opened close-on-exec (the control socket's listener, a library's pipe) must not leak
    /// into the new image, where they would hold a lock or a socket the relaunch itself needs.
    private static func markDescriptorsCloseOnExec() {
        let entries = (try? FileManager.default.contentsOfDirectory(atPath: "/proc/self/fd")) ?? []
        for entry in entries {
            guard let fd = Int32(entry), fd > 2 else { continue }
            let flags = fcntl(fd, F_GETFD)
            if flags >= 0 { _ = fcntl(fd, F_SETFD, flags | FD_CLOEXEC) }
        }
    }
}

/// The process environment exactly as it was handed to this image, read by `main()` BEFORE its first
/// `setenv`; the same lazily-initialized-global contract as `gdkEnvironment`.
let preLaunchProcessEnvironment = ProcessInfo.processInfo.environment
