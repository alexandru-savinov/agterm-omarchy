import CGtk
import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

@MainActor
@Suite("Real GTK zoom ratio restoration", .serialized,
       .enabled(if: ProcessInfo.processInfo.environment["AGTERM_GTK_TESTS"] == "1"))
struct ZoomExitRatioGTKTests {
    private static let ghosttyInitialization = ghostty_init(0, nil)

    @Test("a stale zoom callback preserves the replacement host and its pending restore")
    func staleCallbackPreservesReplacement() throws {
        try withController { controller, session, replacement in
            let old = makePaned()
            defer { g_object_unref(RAW(old)) }
            controller.sessionPanes[session.id] = old
            let context = ZoomExitRatioContext(
                controller: controller, sessionID: session.id, paned: old, zoomedWidth: 0)
            context.ticks = 2
            controller.sessionPanes[session.id] = replacement
            let generation = controller.splitRatioRestore.begin(
                windowID: controller.windowID, sessionID: session.id, paned: replacement)
            let pending = controller.splitRatioRestore.pending[session.id]
            let position = gtk_paned_get_position(replacement)
            let width = gtk_widget_get_width(W(replacement))

            #expect(controller.restoreRatioAfterZoomExit(context) == 0)
            #expect(context.ticks == 2)
            #expect(controller.splitRatioRestore.pending[session.id] == pending)
            #expect(gtk_paned_get_position(replacement) == position)
            print("measured: stale callback preserved replacement width \(width), position \(position), ratio 0.31")
            #expect(gtk_widget_get_width(W(replacement)) == width)
            #expect(session.splitRatio == 0.31)

            pump(for: 0.15)
            #expect(controller.splitRatioRestore.pending[session.id] == pending)
            #expect(gtk_paned_get_position(replacement) == position)
            #expect(controller.tryRestorePanedRatio(
                windowID: controller.windowID, sessionID: session.id,
                paned: replacement, generation: generation) == 0)
            #expect(gtk_paned_get_position(replacement) == Int32(Double(width) * 0.31))
        }
    }

    @Test("a current zoom callback restores its real host at the bounded settle tick")
    func currentCallbackRestoresRatio() throws {
        try withController { controller, session, paned in
            let width = gtk_widget_get_width(W(paned))
            let context = ZoomExitRatioContext(
                controller: controller, sessionID: session.id, paned: paned, zoomedWidth: width)
            controller.zoomPendingRatioRestore.insert(session.id)
            #expect(controller.restoreRatioAfterZoomExit(context) == 1)
            #expect(controller.restoreRatioAfterZoomExit(context) == 1)
            #expect(controller.restoreRatioAfterZoomExit(context) == 0)
            #expect(context.ticks == 3)
            #expect(gtk_paned_get_position(paned) == Int32(Double(width) * 0.31))
            #expect(!controller.zoomPendingRatioRestore.contains(session.id))
            #expect(session.splitRatio == 0.31)
            print("measured: current callback restored width \(width), position \(gtk_paned_get_position(paned)), ratio 0.31")
        }
    }

    private func withController(_ body: (AppController, Session, OpaquePointer) throws -> Void) throws {
        try #require(Self.ghosttyInitialization == 0)
        try #require(gtk_init_check() != 0)
        adw_init()
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let previousLibrary = gLibrary
        let previousController = gController
        let previousTimer = MainTimer.scheduleTimer
        installGLibMainTimer()
        defer {
            gLibrary = previousLibrary
            gController = previousController
            MainTimer.scheduleTimer = previousTimer
        }
        let library = WindowLibrary(directory: directory)
        gLibrary = library
        let windowID = try #require(library.activeWindowID)
        let store = try #require(library.store(for: windowID))
        store.workspaces = []
        store.selectedSessionID = nil
        let app = OpaquePointer(try #require(adw_application_new(
            "io.github.agterm.zoomratiofixture.x" + UUID().uuidString.replacingOccurrences(of: "-", with: ""),
            G_APPLICATION_NON_UNIQUE)))
        defer { g_object_unref(RAW(app)) }
        try #require(g_application_register(GAPP(app), nil, nil) != 0)
        let controller = AppController(app: app, windowID: windowID, library: library)
        gWindows[windowID] = controller
        defer {
            controller.sessionPanes.removeAll()
            store.workspaces = []
            controller.windowWillClose()
            gtk_window_destroy(WIN(controller.windowPointer))
            pump(for: 0.05)
        }
        let session = Session(initialCwd: directory.path)
        session.splitRatio = 0.31
        store.workspaces = [Workspace(name: "ratio", sessions: [session])]
        let paned = makePaned()
        defer { g_object_unref(RAW(paned)) }
        controller.sessionPanes[session.id] = paned
        gtk_overlay_add_overlay(controller.deck, W(paned))
        let deadline = Date().addingTimeInterval(3)
        while gtk_widget_get_width(W(paned)) < 500, Date() < deadline { pump(for: 0.01) }
        try #require(gtk_widget_get_width(W(paned)) >= 500)
        gtk_paned_set_position(paned, 173)
        pump(for: 0.05)
        try body(controller, session, paned)
    }

    private func makePaned() -> OpaquePointer {
        let paned = OpaquePointer(gtk_paned_new(GTK_ORIENTATION_HORIZONTAL))!
        _ = g_object_ref_sink(RAW(paned))
        gtk_paned_set_start_child(paned, gtk_box_new(GTK_ORIENTATION_VERTICAL, 0))
        gtk_paned_set_end_child(paned, gtk_box_new(GTK_ORIENTATION_VERTICAL, 0))
        return paned
    }

    private func pump(for seconds: TimeInterval) {
        let deadline = Date().addingTimeInterval(seconds)
        while Date() < deadline {
            if g_main_context_iteration(nil, 0) == 0 { usleep(1_000) }
        }
    }
}
