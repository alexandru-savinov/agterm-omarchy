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
        try #require(gtk_init_check() != 0)
        adw_init()
        _ = GhosttyTestRuntime.initialized
        GhosttyApp.shared.start(appearanceSide: .light)
        defer { GhosttyApp.shared.shutdown() }
        try #require(GhosttyApp.shared.app != nil)

        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let marker = root.appendingPathComponent("noise-started.json")
        // scripts/omarchy-config-probe.py's producer: one flushed OSC 2 write per title, a marker after 100.
        let producer = root.appendingPathComponent("noise.py")
        try """
        import json, os, pathlib, sys, time
        marker = pathlib.Path(\(String(reflecting: marker.path)))
        print('AGTERM_TITLE_NOISE_STARTED', flush=True)
        end = time.monotonic() + \(Self.producerSeconds)
        i = 0
        while time.monotonic() < end:
            sys.stdout.write('\\033]2;AGTERM_TITLE_NOISE_%d\\007' % i); sys.stdout.flush(); i += 1
            if i == 100:
                marker.write_text(json.dumps({'pid': os.getpid()}))
        """.write(to: producer, atomically: true, encoding: .utf8)

        let window = try #require(op(gtk_window_new()))
        gtk_window_set_default_size(WIN(window), 640, 400)
        var pane: GhosttySurface? = GhosttySurface(
            sessionID: UUID(), cwd: root.path,
            command: "exec python3 " + CommandRestore.shellQuotedLine([producer.path]),
            reportsPaneState: false)
        gtk_window_set_child(WIN(window), W(pane!.rootWidget))
        defer {
            pane?.teardown()
            gtk_window_destroy(WIN(window))
            pane = nil
            _ = pumpGLib(timeout: 1)
        }
        gtk_window_present(WIN(window))

        var noisePID: Int32?
        let started = pumpGLib(timeout: 15) {
            guard pane!.surface != nil, let data = try? Data(contentsOf: marker),
                  let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let pid = value["pid"] as? Int else { return false }
            noisePID = Int32(pid)
            return true
        }
        try #require(started, "the producer never wrote 100 titles; surface created: \(pane!.surface != nil)")
        let pid = try #require(noisePID)
        let noisy = try #require(pane)

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
            #expect(latency < 1, "attempt \(attempt): idle callback waited \(latency) s behind the title drain")
            #expect(screen.value?.contains("AGTERM_TITLE_NOISE_STARTED") == true, "attempt \(attempt): screen read missed output")
            #expect(Self.isRunning(pid), "attempt \(attempt): the producer ended before the callback ran")
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
