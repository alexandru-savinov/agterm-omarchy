import CGtk
import Foundation
import Glibc
import Testing
import agtermCore
@testable import AgtermLinux

/// Run each method in a fresh process: GhosttyApp.shutdown permanently closes its singleton.
@MainActor
@Suite("Held exit through real GTK adapters", .serialized,
       .enabled(if: ProcessInfo.processInfo.environment["AGTERM_REMOTE_HELD_GTK_TESTS"] == "1"))
struct RemoteHeldExitGTKTests {
    private static let ghosttyInitialization = ghostty_init(0, nil)

    @Test("queued held callback reconciles before a late native title books exactly one reconnect")
    func exitBeforeTitle() throws {
        try withFixture { controller, session, primary, split in
            let pane = try #require(session.splitPaneIdentity)
            let native = try #require(split.surface)
            seed(pane)
            try #require(GhosttyApp.exerciseHeldExitAction(split))
            #expect(ZmxLeadBook.shared.states[pane] == nil)
            #expect(split.heldLeadAttachment.state?.attachment.nonce == "held-fixture")
            #expect(!controller.store.remotePaneIsHeld(pane, forSession: session.id))
            try barrier()
            #expect(controller.store.remotePaneIsHeld(pane, forSession: session.id))
            #expect(session.surface === primary)
            #expect(session.splitSurface === split)
            #expect(split.surface == native)
            #expect(RemoteReconnectBook.shared.entries[pane] == nil)
            try #require(GhosttyApp.exerciseHeldExitAction(split, title: "agterm-remote;held-fixture:lost"))
            let entry = try #require(RemoteReconnectBook.shared.entries[pane])
            #expect(entry.session == session.id)
            #expect(entry.host == "held-fixture.invalid")
            #expect(entry.cover)
            #expect(RemoteReconnectBook.shared.entries.count == 1)
            #expect(split.heldLeadAttachment.state == nil)
            #expect(session.splitSurface === split)
            try #require(GhosttyApp.exerciseHeldExitAction(split, title: "agterm-remote;held-fixture:lost"))
            #expect(RemoteReconnectBook.shared.entries[pane] == entry)
            #expect(RemoteReconnectBook.shared.entries.count == 1)
        }
    }

    @Test("removal before queued held callback destroys the native target without reconnecting")
    func removedBeforeHeld() throws {
        try withFixture { controller, session, primary, split in
            let pane = try #require(session.splitPaneIdentity)
            seed(pane)
            let remotePrimary = try #require(session.remotePresentation?.binding.daemon(forLocalPane: session.paneIdentity))
            let remote = try #require(ZmxSupport.paneIdentity(fromDaemonName: remotePrimary))
            let layout = PresentationLayout(panes: [remote], primary: remote, shown: false)
            #expect(controller.store.applyRemoteLayout(layout, forSession: session.id).contains(pane))
            try #require(GhosttyApp.exerciseHeldExitAction(split))
            try barrier()
            #expect(session.surface === primary)
            #expect(session.splitSurface == nil)
            #expect(split.surface == nil)
            // A freed native target cannot receive an action; never reuse its former pointer.
            #expect(!GhosttyApp.exerciseHeldExitAction(split, title: "agterm-remote;held-fixture:lost"))
            #expect(RemoteReconnectBook.shared.entries[pane] == nil)
        }
    }

    @Test("an alive native surface retired from its model slot rejects a late title")
    func retiredSlot() throws {
        try withFixture { controller, session, _, split in
            let pane = try #require(session.splitPaneIdentity)
            seed(pane)
            try #require(GhosttyApp.exerciseHeldExitAction(split))
            try barrier()
            try #require(controller.store.remotePaneIsHeld(pane, forSession: session.id))
            session.splitSurface = nil
            defer { session.splitSurface = split }
            try #require(split.surface != nil)
            try #require(GhosttyApp.exerciseHeldExitAction(split, title: "agterm-remote;held-fixture:lost"))
            #expect(RemoteReconnectBook.shared.entries[pane] == nil)
            #expect(split.heldLeadAttachment.state?.attachment.nonce == "held-fixture")
        }
    }

    private func seed(_ pane: UUID) {
        ZmxLeadBook.shared.begin(ZmxLeadAttachment(nonce: "held-fixture", claim: true), pane: pane, reattaching: true)
    }

    private func withFixture(_ body: (AppController, Session, GhosttySurface, GhosttySurface) throws -> Void) throws {
        try #require(gWindows.isEmpty && GhosttyApp.shared.app == nil)
        try #require(RemoteReconnectBook.shared.isEmpty)
        print("fixture process before Ghostty init: \(ownedProcessState(getpid()))")
        try #require(Self.ghosttyInitialization == 0)
        print("fixture process after Ghostty init: \(ownedProcessState(getpid()))")
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let ssh = root.appendingPathComponent("ssh")
        let pidDirectory = root.appendingPathComponent("ssh-pids")
        let panePIDs = root.appendingPathComponent("pane-pids")
        for directory in [pidDirectory, panePIDs] {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        }
        try "#!/bin/sh\nprintf '%s\\n' \"$$\" >\(CommandRestore.shellQuotedLine([pidDirectory.path]))/\"$$\"\nexec /usr/bin/env --default-signal=TERM /bin/cat >/dev/null\n".write(
            to: ssh, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: ssh.path)
        let paneCommand = root.appendingPathComponent("held-pane")
        try "#!/bin/sh\nprintf '%s\\n' \"$$\" >\(CommandRestore.shellQuotedLine([panePIDs.path]))/\"$$\"\nexec /bin/cat >/dev/null\n".write(
            to: paneCommand, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: paneCommand.path)
        let environment = ["HOME": root.path, "XDG_CONFIG_HOME": root.path, "XDG_CACHE_HOME": root.path,
                           "AGTERM_STATE_DIR": root.path,
                           "PATH": root.path + ":/usr/bin:/bin"]
        let previous = environment.keys.map { ($0, ProcessInfo.processInfo.environment[$0]) }
        defer {
            for (key, value) in previous {
                if let value { setenv(key, value, 1) } else { unsetenv(key) }
            }
        }
        for (key, value) in environment { try #require(setenv(key, value, 1) == 0) }
        try #require(gtk_init_check() != 0)
        adw_init()
        let oldLibrary = gLibrary
        let oldController = gController
        let oldTimer = MainTimer.scheduleTimer
        // Surface spawning and transport cleanup need the production GLib timers.
        installGLibMainTimer()
        defer { gLibrary = oldLibrary; gController = oldController; MainTimer.scheduleTimer = oldTimer }
        let appearanceSide = LinuxAppearanceSide(isDark: AppController.systemIsDark)
        print("fixture launch appearance isDark=\(appearanceSide.isDark)")
        GhosttyApp.shared.start(appearanceSide: appearanceSide)
        defer { GhosttyApp.shared.shutdown() }
        let library = WindowLibrary(directory: root)
        gLibrary = library
        let id = try #require(library.activeWindowID)
        let store = try #require(library.store(for: id))
        store.workspaces = []
        store.selectedSessionID = nil
        let app = OpaquePointer(try #require(adw_application_new(
            "io.github.agterm.heldfixture.x" + UUID().uuidString.replacingOccurrences(of: "-", with: ""),
            G_APPLICATION_NON_UNIQUE)))
        defer { g_object_unref(RAW(app)) }
        try #require(g_application_register(GAPP(app), nil, nil) != 0)
        let controller = AppController(app: app, windowID: id, library: library)
        gWindows[id] = controller
        var panes: [UUID] = []
        var childPIDs: [Int32] = []
        defer {
            controller.stopRemotePresentations()
            store.workspaces = []
            controller.windowWillClose()
            gtk_window_destroy(WIN(controller.windowPointer))
            for pane in panes { ZmxLeadBook.shared.forget(pane: pane); RemoteReconnectBook.shared.cancel(pane: pane) }
            do {
                try barrier()
                childPIDs += try recordedPIDs(in: pidDirectory)
                childPIDs += try recordedPIDs(in: panePIDs)
                for pid in childPIDs {
                    try pumpUntil(details: { ownedProcessState(pid) }, {
                        Glibc.kill(pid, 0) == -1 && errno == ESRCH
                    })
                }
            } catch { Issue.record("fixture cleanup did not complete: \(error)") }
        }
        let workspace = Workspace(name: "held", sessions: [])
        store.workspaces = [workspace]
        let session = try #require(store.addSession(toWorkspace: workspace.id, cwd: root.path,
                                                   remoteHost: "held-fixture.invalid"))
        session.initialCommand = CommandRestore.shellQuotedLine([paneCommand.path])
        session.commandWait = true
        store.toggleSplit(session.id)
        session.splitInitialCommand = session.initialCommand
        session.splitCommandWait = true
        let splitID = try #require(session.splitPaneIdentity)
        panes = [session.paneIdentity, splitID]
        store.bindRemote(RemoteBinding(remoteSessionID: "fixture", daemonsByLocalPane: [
            session.paneIdentity: ZmxSupport.daemonName(for: UUID()), splitID: ZmxSupport.daemonName(for: UUID())
        ], presentationVersion: 1), forSession: session.id)
        controller.reconcile()
        let primary = try #require(session.surface as? GhosttySurface)
        let split = try #require(session.splitSurface as? GhosttySurface)
        try pumpUntil(details: {
            "primary: \(surfaceState(primary)); split: \(surfaceState(split)); " +
                "ssh markers: \((try? recordedPIDs(in: pidDirectory)) ?? []); " +
                "pane markers: \((try? recordedPIDs(in: panePIDs)) ?? [])"
        }, {
            primary.surface != nil && split.surface != nil &&
                ((try? FileManager.default.contentsOfDirectory(atPath: pidDirectory.path).isEmpty) == false) &&
                ((try? FileManager.default.contentsOfDirectory(atPath: panePIDs.path).count) == 2)
        })
        #expect(try recordedPIDs(in: pidDirectory).count == 1)
        try #require(primary.waitAfterCommand && split.waitAfterCommand)
        #expect(controller.remoteClients.count == 1)
        // Pause only this controller's remote tick, after real setup. Reconcile sees the sentinel.
        controller.remoteTickCancel?()
        controller.remoteTickCancel = {}
        try body(controller, session, primary, split)
    }

    private func recordedPIDs(in directory: URL) throws -> [Int32] {
        try FileManager.default.contentsOfDirectory(atPath: directory.path).map { name in
            let pid = try #require(Int32(name))
            try #require(pid > 1)
            let recorded = try String(contentsOf: directory.appendingPathComponent(name), encoding: .utf8)
            try #require(recorded.trimmingCharacters(in: .whitespacesAndNewlines) == name)
            return pid
        }
    }

    private func surfaceState(_ surface: GhosttySurface) -> String {
        "native=\(surface.surface != nil), widget=\(gtk_widget_get_realized(W(surface.glArea))), " +
            "size=\(gtk_widget_get_width(W(surface.glArea)))x\(gtk_widget_get_height(W(surface.glArea))), " +
            "awaitingPermit=\(surface.awaitingSpawnPermit), spawnKey=\(String(describing: surface.spawnKey)), " +
            "seedPending=\(surface.launchSeed != nil), command=\(String(reflecting: surface.command)), " +
            "wait=\(surface.waitAfterCommand), initialInput=\(String(reflecting: surface.initialInput)), " +
            "screen=\(String(reflecting: surface.readScreenText(all: true, lines: nil)))"
    }

    private func ownedProcessState(_ pid: Int32) -> String {
        let status = (try? String(contentsOfFile: "/proc/\(pid)/status", encoding: .utf8)) ?? "unavailable"
        let fields = status.split(separator: "\n").filter {
            $0.hasPrefix("State:") || $0.hasPrefix("PPid:") || $0.hasPrefix("Name:") ||
                $0.hasPrefix("SigBlk:") || $0.hasPrefix("SigIgn:") || $0.hasPrefix("SigCgt:")
        }
        return "recorded fixture PID \(pid): \(fields.joined(separator: "; "))"
    }

    private func pumpUntil(details: () -> String = { "GLib idle barrier" }, _ done: () -> Bool) throws {
        let deadline = Date().addingTimeInterval(5)
        while !done(), Date() < deadline {
            if g_main_context_iteration(nil, 0) == 0 { usleep(1_000) }
        }
        try #require(done(), "bounded GTK fixture condition was not reached: \(details())")
    }

    private func barrier() throws {
        let marker = Barrier()
        runOnMain { MainActor.assumeIsolated { marker.delivered = true } }
        try pumpUntil { marker.delivered }
    }

    @MainActor private final class Barrier { var delivered = false }
}
