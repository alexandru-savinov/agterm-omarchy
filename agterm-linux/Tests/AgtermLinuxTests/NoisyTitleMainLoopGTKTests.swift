import CGtk
import Foundation
import Glibc
import Testing
import agtermCore
@testable import AgtermLinux

/// Run in its own process: GhosttyApp.shutdown permanently closes its singleton.
@MainActor
@Suite("A noisy title producer cannot starve the GTK main loop", .serialized,
       .enabled(if: ProcessInfo.processInfo.environment["AGTERM_TITLE_NOISE_GTK_TESTS"] == "1"))
struct NoisyTitleMainLoopGTKTests {
    private static let producerSeconds = 20.0

    @Test("main-loop callbacks and screen reads run while titles keep arriving")
    func idleCallbacksRunDuringTitleNoise() throws {
        for assignment in gdkEnvironment.assignments {
            try #require(setenv(assignment.name, assignment.value, 1) == 0)
        }
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let previousStateDirectory = ProcessInfo.processInfo.environment["AGTERM_STATE_DIR"]
        try #require(setenv("AGTERM_STATE_DIR", root.path, 1) == 0)
        defer {
            if let previousStateDirectory { setenv("AGTERM_STATE_DIR", previousStateDirectory, 1) } else { unsetenv("AGTERM_STATE_DIR") }
        }
        // Every launch past the first reads a saved settings file; the selected pane's titles re-read it.
        var settings = AppSettings(theme: AppSettings.defaultTheme)
        settings.welcomeShown = true
        try linuxSettingsStore().save(settings)

        try #require(gtk_init_check() != 0)
        adw_init()
        _ = GhosttyTestRuntime.initialized
        let previousLibrary = gLibrary
        let previousController = gController
        let previousTimer = MainTimer.scheduleTimer
        installGLibMainTimer()
        defer { gLibrary = previousLibrary; gController = previousController; MainTimer.scheduleTimer = previousTimer }
        GhosttyApp.shared.start(appearanceSide: .light)
        defer { GhosttyApp.shared.shutdown() }
        try #require(GhosttyApp.shared.app != nil)

        // scripts/omarchy-config-probe.py's OSC 2 flood, batched so each PTY always holds a backlog for libghostty.
        let producer = root.appendingPathComponent("noise.py")
        try """
        import json, os, pathlib, sys, time
        marker = pathlib.Path(sys.argv[1])
        print('AGTERM_TITLE_NOISE_STARTED', flush=True)
        end = time.monotonic() + \(Self.producerSeconds)
        i = 0
        while time.monotonic() < end:
            sys.stdout.write(''.join('\\033]2;AGTERM_TITLE_NOISE_%d\\007' % n for n in range(i, i + 256))); sys.stdout.flush()
            i += 256
            if i == 256:
                marker.write_text(json.dumps({'pid': os.getpid()}))
        """.write(to: producer, atomically: true, encoding: .utf8)
        let markers = ["primary", "split"].map { root.appendingPathComponent("noise-started-\($0).json") }
        let commands = markers.map { "exec python3 " + CommandRestore.shellQuotedLine([producer.path, $0.path]) }

        let library = WindowLibrary(directory: root)
        gLibrary = library
        let windowID = try #require(library.activeWindowID)
        let store = try #require(library.store(for: windowID))
        store.workspaces = []
        store.selectedSessionID = nil
        let app = OpaquePointer(try #require(adw_application_new(
            "io.github.agterm.titlenoise.x" + UUID().uuidString.replacingOccurrences(of: "-", with: ""),
            G_APPLICATION_NON_UNIQUE)))
        defer { g_object_unref(RAW(app)) }
        try #require(g_application_register(GAPP(app), nil, nil) != 0)
        let controller = AppController(app: app, windowID: windowID, library: library)
        gWindows[windowID] = controller
        defer {
            store.workspaces = []
            controller.windowWillClose()
            gtk_window_destroy(WIN(controller.windowPointer))
            _ = pumpGLib(timeout: 1)
        }
        // Both panes of the selected session report titles through the window controller, as in the app.
        let workspace = Workspace(name: "noise", sessions: [])
        store.workspaces = [workspace]
        let session = try #require(store.addSession(toWorkspace: workspace.id, cwd: root.path, command: commands[0]))
        store.toggleSplit(session.id)
        session.splitInitialCommand = commands[1]
        try #require(store.selectedSessionID == session.id)
        controller.reconcile()
        let primary = try #require(session.surface as? GhosttySurface)
        let split = try #require(session.splitSurface as? GhosttySurface)
        let panes = [primary, split]

        var noisePIDs: [Int32] = []
        let started = pumpGLib(timeout: 15) {
            noisePIDs = markers.compactMap { marker in
                guard let data = try? Data(contentsOf: marker),
                      let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let pid = value["pid"] as? Int else { return nil }
                return Int32(pid)
            }
            return noisePIDs.count == markers.count && panes.allSatisfy { $0.surface != nil }
        }
        try #require(started, "producers that wrote a batch: \(noisePIDs.count) of \(markers.count)")
        let noisy = primary

        for attempt in 1...5 {
            let ran = TestSlot<Double>()
            let screen = TestSlot<String>()
            let posted = Date()
            runOnMain {
                MainActor.assumeIsolated {
                    ran.set(Date().timeIntervalSince(posted))
                    screen.set(noisy.readScreenText(all: false, lines: nil) ?? "")
                }
            }
            _ = pumpGLib(timeout: 3) { ran.value != nil }
            let latency = try #require(ran.value, "attempt \(attempt): an idle callback did not run within 3 s of title noise")
            print("title-noise attempt \(attempt): idle callback latency \(latency) s")
            #expect(latency < 1, "attempt \(attempt): idle callback waited \(latency) s behind the title drain")
            #expect(screen.value?.contains("AGTERM_TITLE_NOISE_STARTED") == true, "attempt \(attempt): screen read missed output")
            #expect(noisePIDs.allSatisfy(Self.isRunning), "attempt \(attempt): a producer ended before the callback ran")
            #expect(session.oscTitle?.hasPrefix("AGTERM_TITLE_NOISE_") == true, "attempt \(attempt): titles never reached the store")
        }
    }

    /// A reaped or zombie producer no longer refills the mailbox.
    private static func isRunning(_ pid: Int32) -> Bool {
        guard let stat = try? String(contentsOfFile: "/proc/\(pid)/stat", encoding: .utf8),
              let close = stat.lastIndex(of: ")") else { return false }
        let state = stat[stat.index(close, offsetBy: 2)]
        return state != "Z" && state != "X"
    }
}
