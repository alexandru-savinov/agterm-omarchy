import Foundation
import CGtk
import Testing
import agtermCore
@testable import AgtermLinux

@MainActor
@Suite("Real GTK surface teardown before a spawn grant",
       .enabled(if: ProcessInfo.processInfo.environment["AGTERM_GTK_TESTS"] == "1"))
struct LinuxSpawnRegistryGTKTests {
    @Test("teardown cancels the pending grant and widget destruction releases the surface")
    func teardownBeforeGrant() throws {
        for assignment in gdkEnvironment.assignments {
            try #require(setenv(assignment.name, assignment.value, 1) == 0)
        }
        try #require(gtk_init_check() != 0)
        adw_init()
        _ = GhosttyTestRuntime.initialized
        GhosttyApp.shared.start(appearanceSide: .light)
        defer { GhosttyApp.shared.shutdown() }
        try #require(GhosttyApp.shared.app != nil)

        // Keep this synchronous: the timer seam is process-global, and no grant may race teardown.
        let original = MainTimer.scheduleTimer
        let queue = SpawnGrantQueue()
        MainTimer.scheduleTimer = { _, fire in
            queue.fires.append(fire)
            return {}
        }
        defer { MainTimer.scheduleTimer = original }

        let key = UUID()
        gSpawnRegistry.pacer.arm(order: [key], burst: [])
        var pane: GhosttySurface? = GhosttySurface(sessionID: UUID(), cwd: "/tmp")
        weak var observedPane = pane
        let widget = try #require(pane?.rootWidget)
        _ = g_object_ref_sink(RAW(widget))
        var ownsWidget = true
        defer {
            pane?.teardown()
            gSpawnRegistry.cancel(key)
            if ownsWidget { g_object_unref(RAW(widget)) }
        }
        gSpawnRegistry.enqueue(pane!, key: key, shouldPace: true)
        pane!.createSurface()
        try #require(pane!.awaitingSpawnPermit)
        try #require(pane!.surface == nil)
        try #require(gSpawnRegistry.registeredKeys == [key])
        try #require(!queue.fires.isEmpty)

        pane!.teardown()
        try #require(gSpawnRegistry.registeredKeys.isEmpty, "teardown left a pending registry entry")
        try #require(gSpawnRegistry.pacer.isPassthrough, "teardown left the pacer blocked")
        #expect(pane!.spawnKey == nil)
        #expect(pane!.spawnPacer == nil)
        while !queue.fires.isEmpty { queue.fires.removeFirst()() }
        #expect(pane!.surface == nil, "a queued grant spawned the torn-down pane")

        // GTK owns a retained wrapper until its GL area's destroy signal runs.
        pane = nil
        #expect(observedPane != nil)
        g_object_unref(RAW(widget))
        ownsWidget = false
        #expect(observedPane == nil, "the destroyed widget or registry retained the closed pane")
        #expect(gSpawnRegistry.registeredKeys.isEmpty)
    }
}

@MainActor
private final class SpawnGrantQueue {
    var fires: [@MainActor () -> Void] = []
}
