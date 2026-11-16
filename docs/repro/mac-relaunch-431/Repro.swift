// Minimal reproduction for #431: a SwiftUI WindowGroup app whose windows opt out of AppKit state restoration
// never opens a window again after an unclean exit (SIGTERM, SIGKILL, crash, force quit) once AppKit has
// written its saved state, about 15 s into the run.
//
// It models the three agterm ingredients and nothing else:
//   1. a plain `WindowGroup` whose startup work runs in the window's `.task` (agterm starts its control server
//      there, so no window means no socket);
//   2. `NSQuitAlwaysKeepsWindows = false` in Info.plist;
//   3. every window sets `isRestorable = false` (agterm's WindowAccessor does exactly this).
//
// Environment: REPRO_MARKER=<file> appends one line per milestone; REPRO_FIX=1 applies the proposed fix;
// REPRO_KEEP_RESTORABLE=1 drops ingredient 3. SIGUSR1 asks for a clean NSApp.terminate.
import AppKit
import SwiftUI

@main
struct Relaunch431Repro: App {
    @NSApplicationDelegateAdaptor(ReproDelegate.self) private var delegate

    init() {
        if ProcessInfo.processInfo.environment["REPRO_FIX"] == "1" {
            // the proposed fix, at the same point agtermApp.init() would run it: before NSApplicationMain
            UserDefaults.standard.register(defaults: ["ApplePersistenceIgnoreState": true])
        }
    }

    var body: some Scene {
        WindowGroup(id: "terminal") {
            Text("relaunch-431 repro")
                .frame(minWidth: 320, minHeight: 120)
                .background(RestorationOptOut())
                .task { Marker.write("window-task-ran") }
        }
    }
}

final class ReproDelegate: NSObject, NSApplicationDelegate {
    private var quitSource: DispatchSourceSignal?

    func applicationDidFinishLaunching(_: Notification) {
        Marker.write("did-finish-launching")
        signal(SIGUSR1, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: SIGUSR1, queue: .main)
        source.setEventHandler { NSApp.terminate(nil) }
        source.resume()
        quitSource = source
    }
}

struct RestorationOptOut: NSViewRepresentable {
    func makeNSView(context _: Context) -> NSView { OptOutView() }
    func updateNSView(_: NSView, context _: Context) {}

    final class OptOutView: NSView {
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            guard let window, ProcessInfo.processInfo.environment["REPRO_KEEP_RESTORABLE"] != "1" else { return }
            window.isRestorable = false
            window.restorationClass = nil
            window.disableSnapshotRestoration()
            window.invalidateRestorableState()
        }
    }
}

enum Marker {
    static func write(_ milestone: String) {
        guard let path = ProcessInfo.processInfo.environment["REPRO_MARKER"] else { return }
        let line = Data("\(milestone) pid=\(getpid())\n".utf8)
        if let handle = FileHandle(forWritingAtPath: path) {
            handle.seekToEndOfFile()
            handle.write(line)
            try? handle.close()
        } else {
            FileManager.default.createFile(atPath: path, contents: line)
        }
    }
}
