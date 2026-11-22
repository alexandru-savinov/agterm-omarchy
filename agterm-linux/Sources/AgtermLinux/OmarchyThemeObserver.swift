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
    private var started = false
    private var cancelParentWatch: Cancel?
    private var cancelThemeWatch: Cancel?
    private var cancelConfigWatch: Cancel?
    private var needsThemeWatch = false
    private var needsAppearanceWatch = false
    private var themeChanged = false
    private var appearanceConfigChanged = false
    private var appearanceSource = OmarchyAppearanceSource()
    var appearance: OmarchyAppearanceConfig { appearanceSource.config }

    init(theme: OmarchyTheme, observe: @escaping Observe = watchDirectory, reload: @escaping @MainActor () -> Void) {
        self.theme = theme
        self.observe = observe
        self.reload = reload
    }

    func start() {
        guard !started else { return }
        started = true
        // Omarchy swaps out the theme directory. The stable parent sees that swap and the
        // generation marker. The inner watch also handles direct saves to ghostty.conf.
        cancelParentWatch = observe(theme.current) { [weak self] file, otherFile in
            if file == "theme" || otherFile == "theme" {
                self?.needsThemeWatch = true
            } else if file != "theme.generation" && otherFile != "theme.generation" {
                return
            }
            self?.scheduleThemeReload()
        }
        watchThemeDirectory()
        if theme.isInstalled {
            watchAppearanceConfig()
            _ = appearanceSource.refresh(from: theme.appearanceConfig)
        }
    }

    func stop() {
        debounce.cancel()
        cancelConfigWatch?()
        cancelThemeWatch?()
        cancelParentWatch?()
        cancelThemeWatch = nil
        cancelParentWatch = nil
        cancelConfigWatch = nil
        needsThemeWatch = false
        needsAppearanceWatch = false
        themeChanged = false
        appearanceConfigChanged = false
        appearanceSource = OmarchyAppearanceSource()
        started = false
    }

    private func watchThemeDirectory() {
        cancelThemeWatch?()
        cancelThemeWatch = observe(theme.directory) { [weak self] file, otherFile in
            if file == "ghostty.conf" || otherFile == "ghostty.conf" {
                self?.scheduleThemeReload()
            }
        }
        needsThemeWatch = false
    }

    private func scheduleThemeReload() {
        themeChanged = true
        scheduleReload()
    }

    private func watchAppearanceConfig() {
        cancelConfigWatch?()
        cancelConfigWatch = nil
        needsAppearanceWatch = false
        let ghostty = theme.appearanceConfig.deletingLastPathComponent()
        let config = ghostty.deletingLastPathComponent()
        // Watch the closest available directory. If a missing child is created or renamed into
        // place, re-arm before reading the snapshot so the next file replacement is observed.
        for (directory, child) in [(ghostty, "config"), (config, "ghostty"), (theme.home, ".config")] {
            guard let cancel = observe(directory, { [weak self] file, otherFile in
                guard let self, file == child || otherFile == child else { return }
                if directory != ghostty { self.needsAppearanceWatch = true }
                self.appearanceConfigChanged = true
                self.scheduleReload()
            }) else { continue }
            cancelConfigWatch = cancel
            break
        }
    }

    private func scheduleReload() {
        debounce.schedule(after: 0.25) { [weak self] in
            guard let self else { return }
            if self.needsThemeWatch { self.watchThemeDirectory() }
            if self.needsAppearanceWatch { self.watchAppearanceConfig() }
            let appearanceChanged = self.appearanceConfigChanged && self.appearanceSource.refresh(from: self.theme.appearanceConfig)
            let reloadNeeded = self.themeChanged || appearanceChanged
            self.themeChanged = false
            self.appearanceConfigChanged = false
            if reloadNeeded { self.reload() }
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
        let handler: @convention(c) (OpaquePointer?, OpaquePointer?, OpaquePointer?, GFileMonitorEvent, gpointer?) -> Void = { _, file, otherFile, _, data in
            guard let data else { return }
            let callback = Unmanaged<Callback>.fromOpaque(data).takeUnretainedValue()
            let name = OmarchyThemeObserver.basename(file)
            let otherName = OmarchyThemeObserver.basename(otherFile)
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
