import CGtk
import Foundation
import agtermCore

@MainActor
final class OmarchyThemeObserver {
    typealias Change = @MainActor (String?, String?) -> Void
    typealias Cancel = @MainActor () -> Void
    typealias Observe = @MainActor (URL, @escaping Change) -> Cancel?

    static let shared = OmarchyThemeObserver(theme: OmarchyTheme(), reload: {
        if OmarchyThemePreference().load() != false { reloadObservedGhosttyConfig() }
    })

    private let theme: OmarchyTheme
    private let observe: Observe
    private let reload: @MainActor () -> Void
    private let debounce = Debouncer()
    private var cancelParentWatch: Cancel?
    private var cancelThemeWatch: Cancel?
    private var needsThemeWatch = false

    init(theme: OmarchyTheme, observe: @escaping Observe = watchDirectory, reload: @escaping @MainActor () -> Void) {
        self.theme = theme
        self.observe = observe
        self.reload = reload
    }

    func start() {
        guard cancelParentWatch == nil else { return }
        // Omarchy swaps out the theme directory. The stable parent sees that swap and the
        // generation marker. The inner watch also handles direct saves to ghostty.conf.
        cancelParentWatch = observe(theme.current) { [weak self] file, otherFile in
            if file == "theme" || otherFile == "theme" {
                self?.needsThemeWatch = true
            } else if file != "theme.generation" && otherFile != "theme.generation" {
                return
            }
            self?.scheduleReload()
        }
        watchThemeDirectory()
    }

    func stop() {
        debounce.cancel()
        cancelThemeWatch?()
        cancelParentWatch?()
        cancelThemeWatch = nil
        cancelParentWatch = nil
        needsThemeWatch = false
    }

    private func watchThemeDirectory() {
        cancelThemeWatch?()
        cancelThemeWatch = observe(theme.directory) { [weak self] file, otherFile in
            if file == "ghostty.conf" || otherFile == "ghostty.conf" {
                self?.scheduleReload()
            }
        }
        needsThemeWatch = false
    }

    private func scheduleReload() {
        debounce.schedule(after: 0.25) { [weak self] in
            guard let self else { return }
            if self.needsThemeWatch { self.watchThemeDirectory() }
            self.reload()
        }
    }

    private final class Callback: Sendable {
        let changed: Change
        init(_ changed: @escaping Change) { self.changed = changed }
    }

    private static func watchDirectory(_ url: URL, changed: @escaping Change) -> Cancel? {
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory), isDirectory.boolValue
        else { return nil }
        let file = g_file_new_for_path(url.path)
        defer { g_object_unref(RAW(file)) }
        guard let monitor = g_file_monitor_directory(file, agterm_file_monitor_watch_moves(), nil, nil) else { return nil }
        let callback = Unmanaged.passRetained(Callback(changed)).toOpaque()
        let handler: @convention(c) (OpaquePointer?, OpaquePointer?, OpaquePointer?, GFileMonitorEvent, gpointer?) -> Void = {
            _, file, otherFile, _, data in
            guard let data else { return }
            let callback = Unmanaged<Callback>.fromOpaque(data).takeUnretainedValue()
            let name = basename(file)
            let otherName = basename(otherFile)
            MainActor.assumeIsolated { callback.changed(name, otherName) }
        }
        _ = g_signal_connect_data(RAW(OpaquePointer(monitor)), "changed", unsafeBitCast(handler, to: GCallback.self), callback,
                                  { data, _ in if let data { Unmanaged<Callback>.fromOpaque(data).release() } },
                                  GConnectFlags(rawValue: 0))
        return {
            g_file_monitor_cancel(monitor)
            g_object_unref(RAW(OpaquePointer(monitor)))
        }
    }

    nonisolated private static func basename(_ file: OpaquePointer?) -> String? {
        guard let file, let name = g_file_get_basename(file) else { return nil }
        defer { g_free(name) }
        return String(validatingCString: name)
    }
}

/// File and signal reloads keep per-pane configuration, just like desktop light/dark changes.
@MainActor
func reloadObservedGhosttyConfig() {
    _ = gWindows.values.first?.reloadConfigForAppearanceChange(LinuxAppearanceSide(isDark: AppController.systemIsDark))
}
