import CGtk
import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

@MainActor
@Suite("Session switch release callbacks", .serialized,
       .enabled(if: ProcessInfo.processInfo.environment["AGTERM_SWITCH_GTK_TESTS"] == "1"))
struct SessionSwitchReleaseGTKTests {
    private static let ghosttyInitialization = ghostty_init(0, nil)

    @Test("the observed true-to-false sequence commits exactly once")
    func transientDeviceState() throws {
        try withController { controller, timers in
            let target = try #require(controller.sessionSwitcher.current)
            let reader = ControlSamples([true, false])
            controller.scheduleSessionSwitchCommit(releasing: 37, readControlState: reader.read)
            pump()
            #expect(controller.sessionSwitcher.isActive)
            #expect(reader.reads == 1)
            let retry = try #require(timers.entries.first)
            #expect(retry.delay == 0.05)
            retry.fire()
            #expect(reader.reads == 2)
            #expect(!controller.sessionSwitcher.isActive)
            #expect(controller.store.selectedSessionID == target)
            let committedGeneration = controller.sessionSwitchReleaseGeneration
            retry.fire() // Simulate delivery even after completion/cancellation.
            #expect(reader.reads == 2)
            #expect(controller.sessionSwitchReleaseGeneration == committedGeneration)
            #expect(controller.store.selectedSessionID == target)
        }
    }

    @Test("true or unavailable recheck never releases a genuinely held second Ctrl", arguments: [true, nil] as [Bool?])
    func stillHeldOrUnavailable(second: Bool?) throws {
        try withController { controller, timers in
            let selected = controller.store.selectedSessionID
            let reader = ControlSamples([true, second])
            controller.scheduleSessionSwitchCommit(releasing: 37, readControlState: reader.read)
            pump()
            try #require(timers.entries.count == 1)
            timers.entries[0].fire()
            #expect(reader.reads == 2)
            #expect(controller.sessionSwitcher.isActive)
            #expect(controller.store.selectedSessionID == selected)
            #expect(timers.entries.count == 1, "the recheck must never become a poll")
            controller.scheduleSessionSwitchCommit(releasing: 105, readControlState: { false })
            pump()
            #expect(!controller.sessionSwitcher.isActive)
            #expect(controller.store.selectedSessionID != selected)
        }
    }

    @Test("new input or cancellation rejects an already queued initial idle", arguments: ["press", "cancel", "close"])
    func staleInitialIdle(action: String) throws {
        try withController { controller, timers in
            let reader = ControlSamples([false])
            controller.scheduleSessionSwitchCommit(releasing: 37, readControlState: reader.read)
            if action == "press" {
                _ = controller.sessionSwitchKeyPressed(keyval: 0xFFE3, keycode: 37, state: 0)
            } else if action == "cancel" {
                controller.cancelSessionSwitch()
            } else {
                close(controller)
            }
            pump()
            #expect(reader.reads == 0)
            #expect(timers.entries.isEmpty)
        }
    }

    @Test("forced late callbacks cannot change a newer release, cycle, or closed owner",
          arguments: ["press", "release", "cancel-new-cycle", "close", "registry-loss"])
    func staleRecheck(action: String) throws {
        try withController { controller, timers in
            let oldReader = ControlSamples([true, false])
            controller.scheduleSessionSwitchCommit(releasing: 37, readControlState: oldReader.read)
            pump()
            let old = try #require(timers.entries.first)
            let nextReader = ControlSamples([true, false])
            if action == "press" {
                _ = controller.sessionSwitchKeyPressed(keyval: 0xFFE4, keycode: 105, state: ModifierKeyMods.controlBit)
            } else if action == "cancel-new-cycle" {
                controller.cancelSessionSwitch()
                controller.quickSwitchSession()
            } else if action == "close" {
                close(controller)
            } else if action == "registry-loss" {
                gWindows.removeValue(forKey: controller.windowID)
            }
            if action == "release" || action == "cancel-new-cycle" {
                controller.scheduleSessionSwitchCommit(releasing: 105, readControlState: nextReader.read)
                pump()
                try #require(timers.entries.count == 2)
            }
            let generation = controller.sessionSwitchReleaseGeneration
            let selection = controller.store.selectedSessionID
            let cycle = controller.sessionSwitcher
            old.fire() // Cancellation is deliberately ignored: the callback guard must do the work.
            #expect(oldReader.reads == 1, "stale callback queried a disposed or superseded owner")
            #expect(controller.sessionSwitchReleaseGeneration == generation)
            #expect(controller.store.selectedSessionID == selection)
            #expect(controller.sessionSwitcher == cycle)
            if timers.entries.count == 2 {
                #expect(!timers.entries[1].cancelled)
                #expect(controller.cancelSessionSwitchReleaseRecheck != nil, "old callback cleared the new handle")
                timers.entries[1].fire()
                #expect(nextReader.reads == 2)
                #expect(!controller.sessionSwitcher.isActive)
            }
            if action == "registry-loss" { gWindows[controller.windowID] = controller }
        }
    }

    @Test("the deferred decision still honors a newly active policy gate")
    func changedPolicy() throws {
        try withController { controller, timers in
            let reader = ControlSamples([true, false])
            let selected = controller.store.selectedSessionID
            controller.scheduleSessionSwitchCommit(releasing: 37, readControlState: reader.read)
            pump()
            let retry = try #require(timers.entries.first)
            let popover = OpaquePointer(try #require(gtk_popover_new()))
            _ = g_object_ref_sink(RAW(popover))
            gtk_widget_set_parent(W(popover), W(controller.deck))
            controller.contextMenuPopover = popover
            gtk_widget_set_visible(W(popover), 1)
            defer {
                controller.contextMenuPopover = nil
                gtk_widget_unparent(W(popover))
                g_object_unref(RAW(popover))
            }
            try #require(controller.contextMenuIsOpen)
            retry.fire()
            #expect(!controller.sessionSwitcher.isActive)
            #expect(controller.store.selectedSessionID == selected)
        }
    }

    private func withController(_ body: (AppController, ReleaseTimers) throws -> Void) throws {
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
            "io.github.agterm.switchfixture.x" + UUID().uuidString.replacingOccurrences(of: "-", with: ""),
            G_APPLICATION_NON_UNIQUE)))
        defer { g_object_unref(RAW(app)) }
        try #require(g_application_register(GAPP(app), nil, nil) != 0)
        let controller = AppController(app: app, windowID: windowID, library: library)
        gWindows[windowID] = controller
        defer {
            if gWindows[windowID] === controller { close(controller) }
            pump()
        }
        let first = Session(initialCwd: directory.path)
        let second = Session(initialCwd: directory.path)
        store.workspaces = [Workspace(name: "fixture", sessions: [first, second])]
        store.selectSession(second.id)
        store.selectSession(first.id)
        controller.quickSwitchSession()
        try #require(controller.sessionSwitcher.isActive)
        pump()
        let timers = ReleaseTimers()
        let realTimer = MainTimer.scheduleTimer
        // Synchronous scope: no unrelated test may see the process-global seam.
        MainTimer.scheduleTimer = { delay, fire in
            if delay != 0.05 { return realTimer(delay, fire) }
            let entry = ReleaseTimer(delay: delay, fire: fire)
            timers.entries.append(entry)
            return { entry.cancelled = true }
        }
        defer { MainTimer.scheduleTimer = realTimer }
        try body(controller, timers)
    }

    private func close(_ controller: AppController) {
        controller.store.workspaces = []
        controller.windowWillClose()
        gtk_window_destroy(WIN(controller.windowPointer))
    }

    private func pump() {
        let barrier = IdleBarrier()
        runOnMain { MainActor.assumeIsolated { barrier.fired = true } }
        let deadline = Date().addingTimeInterval(5)
        while !barrier.fired && Date() < deadline {
            if g_main_context_iteration(nil, 0) == 0 { usleep(1_000) }
        }
        #expect(barrier.fired, "the actual GLib idle queue was not drained")
    }

}

@MainActor
private final class ControlSamples {
    var values: [Bool?]
    var reads = 0
    init(_ values: [Bool?]) { self.values = values }
    func read() -> Bool? {
        reads += 1
        return values.isEmpty ? nil : values.removeFirst()
    }
}

@MainActor
private final class ReleaseTimers {
    var entries: [ReleaseTimer] = []
}

@MainActor
private final class ReleaseTimer {
    let delay: TimeInterval
    let fire: @MainActor () -> Void
    var cancelled = false
    init(delay: TimeInterval, fire: @escaping @MainActor () -> Void) {
        self.delay = delay
        self.fire = fire
    }
}

@MainActor
private final class IdleBarrier {
    var fired = false
}
