import CGtk
import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

@MainActor
@Suite("Real GTK theme preview cancellation", .serialized,
       .enabled(if: ProcessInfo.processInfo.environment["AGTERM_THEME_GTK_TESTS"] == "1"))
struct ThemePickerCancellationGTKTests {
    @Test("all cancellation routes discard an observed pending preview even when its timer arrives late")
    func pendingPreviewCancellation() throws {
        try #require(gtk_init_check() != 0)
        adw_init()
        let originalTimer = MainTimer.scheduleTimer
        let originalLibrary = gLibrary
        let originalController = gController
        let originalWindows = gWindows
        installGLibMainTimer()
        let glibTimer = MainTimer.scheduleTimer
        defer {
            MainTimer.scheduleTimer = originalTimer
            gLibrary = originalLibrary
            gController = originalController
            gWindows = originalWindows
        }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let originalState = ProcessInfo.processInfo.environment["AGTERM_STATE_DIR"]
        try #require(setenv("AGTERM_STATE_DIR", directory.path, 1) == 0)
        defer {
            if let originalState { setenv("AGTERM_STATE_DIR", originalState, 1) } else { unsetenv("AGTERM_STATE_DIR") }
            try? FileManager.default.removeItem(at: directory)
        }
        let settingsStore = linuxSettingsStore()
        var settings = AppSettings()
        settings.theme = "Dracula"
        try settingsStore.save(settings)
        let savedBytes = try Data(contentsOf: directory.appendingPathComponent("settings.json"))
        GhosttyApp.shared.start(appearanceSide: LinuxAppearanceSide(isDark: true))
        defer { GhosttyApp.shared.shutdown() }
        let library = WindowLibrary(directory: directory)
        gLibrary = library
        let app = OpaquePointer(try #require(adw_application_new(
            "io.github.agterm.themecancelfixture", G_APPLICATION_NON_UNIQUE)))
        defer { g_object_unref(RAW(app)) }
        try #require(g_application_register(GAPP(app), nil, nil) != 0)
        let ids = [try #require(library.activeWindowID), library.newWindow().id, library.newWindow().id]
        var controllers: [AppController] = []
        for id in ids {
            let store = try #require(library.store(for: id))
            store.workspaces = []
            store.selectedSessionID = nil
            let controller = AppController(app: app, windowID: id, library: library)
            gWindows[id] = controller
            controllers.append(controller)
        }
        defer {
            MainTimer.scheduleTimer = glibTimer
            for controller in controllers where gWindows[controller.windowID] != nil {
                controller.windowWillClose()
                gtk_window_destroy(WIN(controller.windowPointer))
            }
        }
        let owner = controllers[2]
        let survivors = Array(controllers.prefix(2))
        for route in ["Escape", "WM destroy", "parent close"] {
            owner.showThemePicker()
            owner.themePreviewDebouncer.cancel()
            let list = try #require(owner.themeList)
            let index = try #require(owner.themeItems.firstIndex(of: "Atom One Light"))
            let row = try #require(gtk_list_box_get_row_at_index(list, Int32(index)))
            var pending: (@MainActor () -> Void)?
            var cancelled = false
            let capture: @MainActor (TimeInterval, @escaping @MainActor () -> Void) -> (@MainActor () -> Void) = { delay, fire in
                #expect(delay == AppController.themePreviewDebounceInterval)
                #expect(pending == nil)
                pending = fire
                return { cancelled = true }
            }
            MainTimer.scheduleTimer = capture
            owner.themePreviewSelected(OpaquePointer(row))
            let positive = try #require(pending)
            #expect(!cancelled)
            MainTimer.scheduleTimer = glibTimer
            positive()
            #expect(AppController.themePreviewSettings?.theme == "Atom One Light")
            try expectChrome(survivors, hex: "#f9f9f9")

            pending = nil
            cancelled = false
            MainTimer.scheduleTimer = capture
            owner.themePreviewSelected(OpaquePointer(row))
            let stale = try #require(pending)
            #expect(!cancelled)
            MainTimer.scheduleTimer = glibTimer
            switch route {
            case "Escape": owner.cancelTheme()
            case "WM destroy": gtk_window_destroy(WIN(try #require(owner.themeWindow)))
            default:
                owner.windowWillClose()
                gtk_window_destroy(WIN(owner.windowPointer))
            }
            #expect(cancelled)
            #expect(owner.themeWindow == nil)
            #expect(AppController.themePreviewSettings == nil)
            var delivered = false
            MainTimer.schedule(after: 0) { stale(); delivered = true }
            let deadline = Date().addingTimeInterval(2)
            while !delivered, Date() < deadline { _ = g_main_context_iteration(nil, 0) }
            try #require(delivered, "the real GLib loop did not deliver the stale callback")
            #expect(AppController.themePreviewSettings == nil)
            #expect(try Data(contentsOf: directory.appendingPathComponent("settings.json")) == savedBytes)
            try expectChrome(survivors, hex: "#282a36")
            #expect(survivors.allSatisfy { gWindows[$0.windowID] === $0 })
            print("measured: \(route) cancelled=\(cancelled), late delivery=\(delivered), " +
                  "preview=\(String(describing: AppController.themePreviewSettings?.theme))")
        }
    }

    private func expectChrome(_ controllers: [AppController], hex: String) throws {
        var expected = GdkRGBA()
        try #require(gdk_rgba_parse(&expected, hex) != 0)
        for controller in controllers {
            let context = gtk_widget_get_style_context(W(controller.windowPointer))
            var actual = GdkRGBA()
            try #require(gtk_style_context_lookup_color(context, "window_bg_color", &actual) != 0)
            #expect(abs(actual.red - expected.red) < 0.001)
            #expect(abs(actual.green - expected.green) < 0.001)
            #expect(abs(actual.blue - expected.blue) < 0.001)
        }
    }
}
