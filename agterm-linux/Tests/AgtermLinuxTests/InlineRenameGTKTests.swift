import CGtk
import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

@MainActor
@Suite("Real GTK inline rename effects", .serialized,
       .enabled(if: ProcessInfo.processInfo.environment["AGTERM_RENAME_GTK_TESTS"] == "1"))
struct InlineRenameGTKTests {
    private static let ghosttyInitialization = ghostty_init(0, nil)

    @Test(arguments: [false, true])
    func activateAndReentrantLeaveCommitOnce(workspace: Bool) throws {
        try withEditor(workspace: workspace) { controller, entry, effects in
            effects.reenter = { controller.commitInlineRename(RAW(entry)) }
            #expect(gtk_widget_activate(W(entry)) != 0)
            pump(for: 0.1)
            #expect(effects.names == ["edited draft"])
            #expect(controller.renaming == nil)
            gtk_editable_set_text(entry, "late stale draft")
            controller.commitInlineRename(RAW(entry))
            #expect(effects.names == ["edited draft"])
        }
    }

    @Test(arguments: [false, true])
    func focusLeaveCommitsOnce(workspace: Bool) throws {
        try withEditor(workspace: workspace) { controller, entry, effects in
            let sink = OpaquePointer(gtk_button_new_with_label("focus destination"))!
            gtk_overlay_add_overlay(controller.deck, W(sink))
            pump(for: 0.05)
            #expect(gtk_widget_grab_focus(W(sink)) != 0)
            pump(for: 0.1)
            #expect(effects.names == ["edited draft"])
            controller.commitInlineRename(RAW(entry))
            #expect(effects.names == ["edited draft"])
        }
    }

    @Test(arguments: [false, true], [false, true])
    func detachCommitsWithoutFocusLeaveFallback(workspace: Bool, remove: Bool) throws {
        try withEditor(workspace: workspace) { controller, entry, effects in
            let controllers = gtk_widget_observe_controllers(W(entry))!
            defer { g_object_unref(RAW(controllers)) }
            var blocked: guint = 0
            for index in 0..<g_list_model_get_n_items(controllers) {
                let item = g_list_model_get_item(controllers, index)!
                blocked += g_signal_handlers_block_matched(
                    item, G_SIGNAL_MATCH_FUNC, 0, 0, nil,
                    unsafeBitCast(onRenameCommit, to: UnsafeMutableRawPointer.self), nil)
                g_object_unref(item)
            }
            #expect(blocked == 1)
            let store = controller.store
            if workspace {
                if remove {
                    store.workspaces.removeFirst()
                } else {
                    store.applyFocusMode(.on, to: store.workspaces[1].id)
                }
            } else {
                let id = store.workspaces[0].sessions[0].id
                if remove {
                    store.workspaces[0].sessions.removeAll()
                } else {
                    store.moveSession(id, toWorkspace: store.workspaces[1].id)
                }
            }
            controller.syncSidebar()
            pump(for: 0.1)
            #expect(effects.names == ["edited draft"])
            #expect(controller.renaming == nil)
            gtk_editable_set_text(entry, "stale after detach")
            controller.commitInlineRename(RAW(entry))
            #expect(effects.names == ["edited draft"])
            if workspace {
                #expect(store.workspaces.count == (remove ? 1 : 2))
                #expect(store.workspaces.last?.name == "destination")
            } else if !remove {
                #expect(store.workspaces[1].sessions.first?.customName == "edited draft")
            } else {
                #expect(store.workspaces.allSatisfy { $0.sessions.isEmpty })
            }
        }
    }

    @Test(arguments: [false, true])
    func cancelRejectsLaterCommit(workspace: Bool) throws {
        try withEditor(workspace: workspace) { controller, entry, effects in
            controller.cancelInlineRename()
            controller.commitInlineRename(RAW(entry))
            pump(for: 0.1)
            #expect(effects.names.isEmpty)
            #expect(controller.renaming == nil)
        }
    }

    private func withEditor(workspace: Bool,
                            _ body: (AppController, OpaquePointer, RenameEffects) throws -> Void) throws {
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
            "io.github.agterm.renamefixture.x" + UUID().uuidString.replacingOccurrences(of: "-", with: ""),
            G_APPLICATION_NON_UNIQUE)))
        defer { g_object_unref(RAW(app)) }
        try #require(g_application_register(GAPP(app), nil, nil) != 0)
        let controller = AppController(app: app, windowID: windowID, library: library)
        gWindows[windowID] = controller
        defer {
            controller.onCommitRename = nil
            controller.onWorkspaceRename = nil
            store.workspaces = []
            controller.windowWillClose()
            gtk_window_destroy(WIN(controller.windowPointer))
            pump(for: 0.05)
        }
        let session = Session(initialCwd: directory.path)
        let source = Workspace(name: "source", sessions: [session])
        store.workspaces = [source, Workspace(name: "destination", sessions: [])]
        controller.syncSidebar()
        pump(for: 0.1)
        controller.beginRename(id: workspace ? source.id : session.id, isWorkspace: workspace)
        pump(for: 0.1)
        let entry = try #require(controller.renameEntry)
        _ = g_object_ref(RAW(entry))
        defer { g_object_unref(RAW(entry)) }
        try #require(gtk_widget_has_focus(W(entry)) != 0 || gtk_widget_is_ancestor(
            gtk_window_get_focus(WIN(controller.windowPointer)), W(entry)) != 0)
        gtk_editable_set_text(entry, "edited draft")
        let effects = RenameEffects()
        defer { effects.reenter = nil }
        controller.onCommitRename = { id, name in
            effects.record(name)
            store.renameSession(id, to: name)
        }
        controller.onWorkspaceRename = { id, name in
            effects.record(name)
            store.renameWorkspace(id, to: name)
        }
        try body(controller, entry, effects)
    }

    private func pump(for seconds: TimeInterval) {
        let deadline = Date().addingTimeInterval(seconds)
        while Date() < deadline {
            if g_main_context_iteration(nil, 0) == 0 { usleep(1_000) }
        }
    }
}

@MainActor
private final class RenameEffects {
    var names: [String] = []
    var reenter: (() -> Void)?

    func record(_ name: String) {
        names.append(name)
        if names.count == 1 { reenter?() }
    }
}
