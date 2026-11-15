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
    private static let noisyPanes = 4

    @Test("main-loop callbacks and screen reads run while titles keep arriving")
    func idleCallbacksRunDuringTitleNoise() throws {
        for assignment in gdkEnvironment.assignments {
            try #require(setenv(assignment.name, assignment.value, 1) == 0)
        }
        try #require(gtk_init_check() != 0)
        adw_init()
        _ = GhosttyTestRuntime.initialized
        GhosttyApp.shared.start(appearanceSide: .light)
        defer { GhosttyApp.shared.shutdown() }
        try #require(GhosttyApp.shared.app != nil)

        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
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

        // Every pane's reader feeds the one app mailbox the main thread drains.
        let markers = (0..<Self.noisyPanes).map { root.appendingPathComponent("noise-started-\($0).json") }
        let window = try #require(op(gtk_window_new()))
        gtk_window_set_default_size(WIN(window), 960, 480)
        let row = try #require(OpaquePointer(gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0)))
        gtk_box_set_homogeneous(cast(row), 1)
        gtk_window_set_child(WIN(window), W(row))
        var panes: [GhosttySurface] = markers.map { marker in
            GhosttySurface(
                sessionID: UUID(), cwd: root.path,
                command: "exec python3 " + CommandRestore.shellQuotedLine([producer.path, marker.path]),
                reportsPaneState: false)
        }
        for pane in panes { gtk_box_append(cast(row), W(pane.rootWidget)) }
        defer {
            for pane in panes { pane.teardown() }
            gtk_window_destroy(WIN(window))
            panes = []
            _ = pumpGLib(timeout: 1)
        }
        gtk_window_present(WIN(window))

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
        let noisy = panes[0]

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
