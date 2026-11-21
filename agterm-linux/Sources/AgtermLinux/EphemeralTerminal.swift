import CGtk
import Foundation
import Glibc
import agtermCore

/// A single launcher terminal owns only its GTK window and surface. It never enters regular
/// application activation, which owns persistence, restoration, hooks and the control socket.
@MainActor func runEphemeralTerminal(_ launch: LinuxTerminalLaunch) -> Int32 {
    let appID = launch.appID ?? LinuxAppMetadata.applicationID
    // With no GApplication ID, GTK's Wayland toplevel takes its app_id from the program name
    // before its first commit. The compositor identity can therefore include names invalid on D-Bus.
    appID.withCString { g_set_prgname($0) }
    GhosttyApp.shared.initializeRuntime()
    let app = OpaquePointer(adw_application_new(nil, G_APPLICATION_NON_UNIQUE))
    defer { g_object_unref(RAW(app)) }
    let host = EphemeralTerminal(launch)
    let data = Unmanaged.passUnretained(host).toOpaque()
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
    let launch: LinuxTerminalLaunch

    init(_ launch: LinuxTerminalLaunch) { self.launch = launch }

    func show(in app: OpaquePointer?) {
        installGLibMainTimer()
        let appearance = LinuxAppearanceSide(isDark: AppController.systemIsDark)
        GhosttyApp.shared.start(appearanceSide: appearance)
        let directory = launch.workingDirectory(callerCwd: FileManager.default.currentDirectoryPath,
                                                fallback: ConfigPaths.defaultNewSessionCwd())
        let command = launch.command.map(CommandRestore.shellQuotedLine)
        let window = OpaquePointer(gtk_application_window_new(APPW(app)))
        (launch.title ?? "agterm").withCString { gtk_window_set_title(WIN(window), $0) }
        gtk_window_set_default_size(WIN(window), 1100, 700)
        let surface = GhosttySurface(sessionID: UUID(), cwd: directory, command: command,
                                     reportsPaneState: false, usesSessionWatermark: false)
        surface.onExit = { gtk_window_destroy(WIN(window)) }
        gtk_window_set_child(WIN(window), W(surface.rootWidget))
        gtk_window_present(WIN(window))
        surface.grabFocus()
    }
}

private let onEphemeralActivate: @MainActor @convention(c) (OpaquePointer?, gpointer?) -> Void = { app, data in
    guard let data else { return }
    MainActor.assumeIsolated { Unmanaged<EphemeralTerminal>.fromOpaque(data).takeUnretainedValue().show(in: app) }
}

private let onEphemeralShutdown: @MainActor @convention(c) (OpaquePointer?, gpointer?) -> Void = { _, _ in
    MainActor.assumeIsolated { GhosttyApp.shared.shutdown() }
}

private let onEphemeralQuitSignal: @MainActor @convention(c) (gpointer?) -> gboolean = { data in
    MainActor.assumeIsolated {
        let app = data.map { OpaquePointer($0) }
        if let window = gtk_application_get_active_window(APPW(app)) { gtk_window_destroy(window) }
        g_application_quit(GAPP(app))
    }
    return 0
}
