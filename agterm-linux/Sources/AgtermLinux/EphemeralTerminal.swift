import CGtk
import Foundation
import Glibc
import agtermCore

/// Ephemeral terminals never use SettingsStore.load(), which can quarantine a malformed file.
@MainActor func loadLinuxTerminalSettings() -> AppSettings {
    EphemeralTerminal.readOnlySettings ?? linuxSettingsStore().load()
}

/// A single launcher terminal owns only its GTK window and surface. It never enters regular
/// application activation, which owns persistence, restoration, hooks and the control socket.
@MainActor func runEphemeralTerminal(_ launch: LinuxTerminalLaunch) -> Int32 {
    let appID = launch.appID.flatMap { $0.isEmpty ? nil : $0 } ?? LinuxAppMetadata.applicationID
    // With no GApplication ID, GTK's Wayland toplevel takes its app_id from the program name
    // before its first commit. The compositor identity can therefore include names invalid on D-Bus.
    appID.withCString { g_set_prgname($0) }
    let host = EphemeralTerminal(launch)
    let data = Unmanaged.passUnretained(host).toOpaque()
    let reloadSource = g_unix_signal_add(SIGUSR2, onEphemeralReloadSignal, data)
    defer { _ = g_source_remove(reloadSource) }
    GhosttyApp.shared.initializeRuntime()
    let app = OpaquePointer(adw_application_new(nil, G_APPLICATION_NON_UNIQUE))
    defer { g_object_unref(RAW(app)) }
    connect(app, "activate", unsafeBitCast(onEphemeralActivate, to: GCallback.self), data)
    connect(app, "shutdown", unsafeBitCast(onEphemeralShutdown, to: GCallback.self))
    _ = g_unix_signal_add(SIGTERM, onEphemeralQuitSignal, RAW(app))
    _ = g_unix_signal_add(SIGINT, onEphemeralQuitSignal, RAW(app))
    // These options have already been parsed. GApplication sees only argv[0].
    var argv = [CommandLine.unsafeArgv[0], nil]
    return withExtendedLifetime(host) {
        argv.withUnsafeMutableBufferPointer { g_application_run(GAPP(app), 1, $0.baseAddress) }
    }
}

@MainActor private final class EphemeralTerminal {
    static var readOnlySettings: AppSettings?
    let launch: LinuxTerminalLaunch
    private weak var surface: GhosttySurface?

    init(_ launch: LinuxTerminalLaunch) { self.launch = launch }

    private static func readSettings() -> AppSettings {
        let file = linuxStateDirectory().appendingPathComponent("settings.json")
        let data = try? Data(contentsOf: file)
        return data.flatMap { try? JSONDecoder().decode(AppSettings.self, from: $0) }
            ?? AppSettings(theme: AppSettings.defaultTheme)
    }

    func show(in app: OpaquePointer?) {
        installGLibMainTimer()
        installAppCSS()
        let settings = Self.readSettings()
        Self.readOnlySettings = settings
        let appearance = LinuxAppearanceSide(isDark: AppController.systemIsDark)
        GhosttyApp.shared.start(appearanceSide: appearance)
        let directory = launch.workingDirectory(callerCwd: FileManager.default.currentDirectoryPath,
                                                fallback: ConfigPaths.defaultNewSessionCwd())
        let command = launch.command.map(CommandRestore.shellQuotedLine)
        let window = OpaquePointer(gtk_application_window_new(APPW(app)))
        (launch.title ?? "agterm").withCString { gtk_window_set_title(WIN(window), $0) }
        gtk_window_set_default_size(WIN(window), 1100, 700)
        applyTerminalWindowTranslucency(window, opacity: settings.backgroundOpacity)
        let surface = GhosttySurface(sessionID: UUID(), cwd: directory, command: command,
                                     reportsPaneState: false, usesSessionWatermark: false,
                                     unsettingEnvironment: ["AGTERM_SESSION_ID", "AGTERM_PANE_ID", "AGTERM_WINDOW_ID", "AGTERM_SOCKET"])
        self.surface = surface
        surface.onExit = { gtk_window_destroy(WIN(window)) }
        gtk_window_set_child(WIN(window), W(surface.rootWidget))
        gtk_window_present(WIN(window))
        surface.grabFocus()
    }

    func reloadConfig() {
        guard let surface, surface.surface != nil, let window = gtk_widget_get_root(W(surface.rootWidget)) else { return }
        let settings = Self.readSettings()
        let appearance = LinuxAppearanceSide(isDark: AppController.systemIsDark)
        let ghostty = GhosttyApp.shared
        guard let config = ghostty.buildConfig(settings: settings, isDark: appearance.isDark) else { return }
        defer { ghostty_config_free(config) }
        Self.readOnlySettings = settings
        ghostty.currentThemeBackgroundHex = GhosttyConfigTheme.colors(from: config).background
        ghostty.currentBaseFontSize = GhosttyConfigTheme.fontSize(from: config)
        ghostty.applyColorScheme(appearance)
        surface.applyColorScheme(appearance)
        ghostty.updateConfig(config)
        surface.reapplySessionConfigIfNeeded(windowOpacity: settings.backgroundOpacity, settings: settings)
        applyTerminalWindowTranslucency(window, opacity: settings.backgroundOpacity)
        surface.queueRender()
    }
}

private let onEphemeralActivate: @MainActor @convention(c) (OpaquePointer?, gpointer?) -> Void = { app, data in
    guard let data else { return }
    MainActor.assumeIsolated { Unmanaged<EphemeralTerminal>.fromOpaque(data).takeUnretainedValue().show(in: app) }
}

private let onEphemeralShutdown: @MainActor @convention(c) (OpaquePointer?, gpointer?) -> Void = { _, _ in
    MainActor.assumeIsolated { GhosttyApp.shared.shutdown() }
}

private let onEphemeralReloadSignal: @MainActor @convention(c) (gpointer?) -> gboolean = { data in
    guard let data else { return 0 }
    MainActor.assumeIsolated { Unmanaged<EphemeralTerminal>.fromOpaque(data).takeUnretainedValue().reloadConfig() }
    return 1 // G_SOURCE_CONTINUE
}

private let onEphemeralQuitSignal: @MainActor @convention(c) (gpointer?) -> gboolean = { data in
    MainActor.assumeIsolated {
        let app = data.map { OpaquePointer($0) }
        if let window = gtk_application_get_active_window(APPW(app)) { gtk_window_destroy(window) }
        g_application_quit(GAPP(app))
    }
    return 0
}
