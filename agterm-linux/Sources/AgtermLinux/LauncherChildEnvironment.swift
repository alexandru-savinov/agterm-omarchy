import Foundation
import Glibc

/// What agterm's packaged launcher (and an AppImage runtime) changed in agterm's own environment, and how
/// every child agterm spawns gets the user's values back.
///
/// `bin/agterm-linux` (written by `scripts/stage-linux.sh`) exports the bundle's `LD_LIBRARY_PATH`, the
/// software-renderer `LIBGL_ALWAYS_SOFTWARE=1`, and app-private `AGTERM_*` locations. agterm itself needs
/// them; a pane, hook, custom command or zmx daemon must not inherit them (a `swift build` in a pane would
/// load the bundled runtime, and every GL program started from the terminal would be forced to software).
/// Before each override the launcher records the caller's value as `AGTERM_ORIG_<NAME>`, or lists the name
/// in the space-separated `AGTERM_ORIG_UNSET` when it was unset. Only names the launcher recorded are
/// restored — agterm's documented per-child variables (`AGTERM_SOCKET`, `AGTERM_SESSION_ID`, …) are set by
/// the spawning site and never touched here — and the bookkeeping itself never reaches a child.
///
/// The app process keeps its own values: the restore is applied per child, never to agterm's environ.
/// `LinuxGdkPolicy.PreLaunchEnvironment` captures it once, and every spawning site reaches it through the
/// same two helpers that carry the GDK restore.
enum LauncherChildEnvironment {
    static let originalPrefix = "AGTERM_ORIG_"
    static let unsetListKey = "AGTERM_ORIG_UNSET"
    /// Non-empty only when a launcher wrote the record for THIS process. Older panes handed bookkeeping
    /// on as empty strings; a nested dev app must not read those stale blanks as original values.
    static let recordMarkerKey = "AGTERM_ORIG_SET"

    /// Each variable a child must see restored: a value to set it, a stored `nil` to remove it. An
    /// environment without launcher bookkeeping or `APPDIR` yields an empty map; a record without the
    /// marker restores nothing and only keeps its bookkeeping keys away from children.
    static func restore(from environment: [String: String]) -> [String: String?] {
        var restore: [String: String?] = [:]
        let recorded = environment[recordMarkerKey]?.isEmpty == false
        let unset = recorded ? (environment[unsetListKey] ?? "").split(separator: " ").map(String.init) : []
        for name in unset where !name.isEmpty {
            restore.updateValue(nil, forKey: name)
        }
        for (key, value) in environment where key.hasPrefix(originalPrefix) {
            restore.updateValue(nil, forKey: key)
            let name = String(key.dropFirst(originalPrefix.count))
            // A recorded value wins over a name also (inconsistently) listed as unset.
            if recorded, key != unsetListKey, key != recordMarkerKey, !name.isEmpty {
                restore.updateValue(value, forKey: name)
            }
        }
        // AppImage: whatever library path a child would get, minus the mounted image's own directories.
        if let appDirectory = environment["APPDIR"] {
            let libraryPath = restore.keys.contains("LD_LIBRARY_PATH")
                ? restore["LD_LIBRARY_PATH"] ?? nil : environment["LD_LIBRARY_PATH"]
            if let libraryPath {
                restore.updateValue(withoutAppImagePaths(libraryPath, appDirectory: appDirectory),
                                    forKey: "LD_LIBRARY_PATH")
            }
        }
        return restore
    }

    /// The one function every `Process`/`posix_spawn` child environment passes through: sets each
    /// restored value and REMOVES each restored absence. Everything else passes through untouched.
    static func apply(_ restore: [String: String?], to environment: [String: String]) -> [String: String] {
        var result = environment
        for (name, value) in restore {
            if let value { result[name] = value } else { result.removeValue(forKey: name) }
        }
        return result
    }

    /// Values for libghostty's unchanged string-pair API; absence travels separately as a deletion.
    static func surfacePairs(_ restore: [String: String?]) -> [String: String] {
        restore.compactMapValues { $0 }
    }

    /// A child environment derived from `environment` itself (it is both the capture and the base).
    static func sanitized(_ environment: [String: String]) -> [String: String] {
        apply(restore(from: environment), to: environment)
    }

    /// AppImage only, and only the library path: an AppImage runtime's `LD_LIBRARY_PATH` is dropped from
    /// agterm's own environ (glibc read it at startup, so agterm's own loading is unaffected). The launcher
    /// bookkeeping is deliberately NOT applied here — agterm itself still needs `LIBGL_ALWAYS_SOFTWARE` and
    /// the `AGTERM_*` locations, which are restored per child instead.
    static func sanitizeCurrentProcess() {
        let environment = ProcessInfo.processInfo.environment
        guard let appDirectory = environment["APPDIR"],
              let libraryPath = environment["LD_LIBRARY_PATH"] else { return }
        if let remaining = withoutAppImagePaths(libraryPath, appDirectory: appDirectory) {
            setenv("LD_LIBRARY_PATH", remaining, 1)
        } else {
            unsetenv("LD_LIBRARY_PATH")
        }
    }

    /// `libraryPath` without `appDirectory` and anything under it; nil when nothing remains.
    private static func withoutAppImagePaths(_ libraryPath: String, appDirectory: String) -> String? {
        let appDirectoryPath = URL(fileURLWithPath: appDirectory).standardizedFileURL.path
        let appDirectoryPrefix = appDirectoryPath.hasSuffix("/") ? appDirectoryPath : "\(appDirectoryPath)/"
        let remainingPaths = libraryPath
            .split(separator: ":", omittingEmptySubsequences: false)
            .map(String.init)
            .filter { $0 != appDirectoryPath && !$0.hasPrefix(appDirectoryPrefix) }
        return remainingPaths.isEmpty ? nil : remainingPaths.joined(separator: ":")
    }
}
