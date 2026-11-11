import CGtk
import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

/// `LinuxAutoFollowCoordinator` owns the Linux auto-follow runtime on GLib: its timer is a real
/// `g_timeout_add` source and its attention re-arm hops through `g_idle_add`, so these tests drive the
/// production coordinator by pumping the GLib default context headlessly (no display, no `gtk_init`).
/// Every test is synchronous, so no other `@MainActor` test can interleave and dispatch our sources.
///
/// The coordinator's follow action ends in `handleAutoFollow`, which force-unwraps the process-global
/// `gLibrary`; each test installs an empty throwaway library (it owns none of the test's sessions, so the
/// host-side GTK reconciliation returns before touching any window) and restores the previous one.
@MainActor
@Suite(.serialized)
struct LinuxAutoFollowCoordinatorTests {
    /// Short enough to keep the suite fast, long enough to be a real GLib timeout.
    private let grace: TimeInterval = 0.02
    /// A window several graces long, inside which a coordinator that is going to fire has fired.
    private let settle: TimeInterval = 0.25

    @Test func firesToTheBlockedSessionWhenIdleAndEnabled() {
        withFixture { fixture in
            fixture.coordinator.configure(timeout: grace, stayOnActive: false)
            pump(for: 2, until: { fixture.store.selectedSessionID == fixture.blocked.id })
            #expect(fixture.store.selectedSessionID == fixture.blocked.id)
            #expect(fixture.blocked.autoFollowConsumed)
        }
    }

    @Test func doesNotFireWhenDisabled() {
        withFixture { fixture in
            fixture.coordinator.configure(timeout: nil, stayOnActive: false)
            fixture.coordinator.noteUserActivity()          // activity with the feature off arms nothing
            pump(for: settle)
            #expect(fixture.store.selectedSessionID == fixture.current.id)
            #expect(fixture.coordinator.timeoutMs == nil)
        }
    }

    @Test func disablingCancelsAPendingFire() {
        withFixture { fixture in
            fixture.coordinator.configure(timeout: grace, stayOnActive: false)
            #expect(fixture.coordinator.timeoutMs == 20)
            fixture.coordinator.configure(timeout: nil, stayOnActive: false)
            pump(for: settle)
            #expect(fixture.store.selectedSessionID == fixture.current.id)
        }
    }

    @Test func stopCancelsAPendingFire() {
        withFixture { fixture in
            fixture.coordinator.configure(timeout: grace, stayOnActive: false)
            fixture.coordinator.stop()
            pump(for: settle)
            #expect(fixture.store.selectedSessionID == fixture.current.id)
            #expect(fixture.coordinator.timeoutMs == nil)
        }
    }

    @Test func doesNotFireWhileSuppressedAndFiresAfterResumeAndActivity() {
        withFixture { fixture in
            // Preferences, a GUI ask, a picker: counted, so overlapping suppressors hold to the LAST resume.
            fixture.coordinator.suppress()
            fixture.coordinator.suppress()
            fixture.coordinator.configure(timeout: grace, stayOnActive: false)
            pump(for: settle)
            #expect(fixture.store.selectedSessionID == fixture.current.id)

            fixture.coordinator.resume()
            fixture.coordinator.noteUserActivity()
            pump(for: settle)
            #expect(fixture.store.selectedSessionID == fixture.current.id)  // one suppressor still holds

            fixture.coordinator.resume()
            // resume() alone does not re-arm (the suppressed fire was a no-op with no reschedule) ...
            pump(for: settle)
            #expect(fixture.store.selectedSessionID == fixture.current.id)
            // ... the next activity does, and with nothing blocking it the follow happens.
            fixture.coordinator.noteUserActivity()
            pump(for: 2, until: { fixture.store.selectedSessionID == fixture.blocked.id })
            #expect(fixture.store.selectedSessionID == fixture.blocked.id)
        }
    }

    @Test func aBlockThatLandsWhileAlreadyIdleIsFollowed() {
        // The AT-SPI `auto-follow` positive leg: no keystroke, only a status change re-arms the timer.
        withFixture(blockedAtStart: false) { fixture in
            fixture.coordinator.configure(timeout: grace, stayOnActive: false)
            pump(for: settle)                               // the first fire finds no target
            #expect(fixture.store.selectedSessionID == fixture.current.id)

            fixture.store.setAgentIndicator(AgentIndicator(status: .blocked), forSession: fixture.blocked.id)
            pump(for: 2, until: { fixture.store.selectedSessionID == fixture.blocked.id })
            #expect(fixture.store.selectedSessionID == fixture.blocked.id)
        }
    }

    @Test func staysOnAnActiveCurrentOnlyUnderStayOnActive() {
        withFixture { fixture in
            fixture.store.setAgentIndicator(AgentIndicator(status: .active), forSession: fixture.current.id)
            fixture.coordinator.configure(timeout: grace, stayOnActive: true)
            pump(for: settle)
            #expect(fixture.store.selectedSessionID == fixture.current.id)

            // turning stayOnActive off re-arms by itself and changes the decision
            fixture.coordinator.configure(timeout: grace, stayOnActive: false)
            pump(for: 2, until: { fixture.store.selectedSessionID == fixture.blocked.id })
            #expect(fixture.store.selectedSessionID == fixture.blocked.id)
        }
    }

    @Test func doesNotLeaveABlockedCurrentSession() {
        withFixture { fixture in
            fixture.store.setAgentIndicator(AgentIndicator(status: .blocked), forSession: fixture.current.id)
            fixture.blocked.statusChangedAt = Date(timeIntervalSince1970: 1)   // older: FIFO would pick it
            fixture.coordinator.configure(timeout: grace, stayOnActive: false)
            pump(for: settle)
            #expect(fixture.store.selectedSessionID == fixture.current.id)
        }
    }

    @Test func aConsumedBlockIsNotFollowedAgainUntilItReentersBlocked() {
        withFixture { fixture in
            fixture.coordinator.configure(timeout: grace, stayOnActive: false)
            pump(for: 2, until: { fixture.store.selectedSessionID == fixture.blocked.id })
            #expect(fixture.store.selectedSessionID == fixture.blocked.id)

            _ = fixture.store.selectSession(fixture.current.id)   // the user walks away from it
            fixture.coordinator.noteUserActivity()
            pump(for: settle)
            #expect(fixture.store.selectedSessionID == fixture.current.id)

            fixture.store.setAgentIndicator(AgentIndicator(status: .idle), forSession: fixture.blocked.id)
            fixture.store.setAgentIndicator(AgentIndicator(status: .blocked), forSession: fixture.blocked.id)
            pump(for: 2, until: { fixture.store.selectedSessionID == fixture.blocked.id })
            #expect(fixture.store.selectedSessionID == fixture.blocked.id)
        }
    }

    // MARK: - Fixture

    private struct Fixture {
        let store: AppStore
        let current: Session
        let blocked: Session
        let coordinator: LinuxAutoFollowCoordinator
    }

    /// A two-session window selected on `current`, with `blocked` (optionally) already blocked, plus an
    /// empty `gLibrary` for `handleAutoFollow`. Stops the coordinator and drains its sources afterwards so
    /// nothing it scheduled can fire into a later test.
    private func withFixture(blockedAtStart: Bool = true, _ body: (Fixture) -> Void) {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("agterm-autofollow-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let previousLibrary = gLibrary
        gLibrary = WindowLibrary(directory: directory.appendingPathComponent("library", isDirectory: true))
        defer { gLibrary = previousLibrary }

        let current = Session(initialCwd: "/current")
        let blocked = Session(initialCwd: "/blocked")
        let store = AppStore(
            workspaces: [Workspace(name: "work", sessions: [current, blocked])],
            selectedSessionID: current.id,
            persistence: PersistenceStore(directory: directory.appendingPathComponent("store", isDirectory: true))
        )
        if blockedAtStart {
            store.setAgentIndicator(AgentIndicator(status: .blocked), forSession: blocked.id)
        }
        let coordinator = LinuxAutoFollowCoordinator(store: store)
        defer {
            coordinator.stop()
            pump(for: 0.05)
        }
        body(Fixture(store: store, current: current, blocked: blocked, coordinator: coordinator))
    }

    /// Dispatches ready GLib sources for at most `seconds`, returning early once `until` holds. Bounded by
    /// wall time (not iterations) because the coordinator's source is a real timeout.
    private func pump(for seconds: TimeInterval, until done: () -> Bool = { false }) {
        let deadline = Date().addingTimeInterval(seconds)
        while Date() < deadline {
            if done() { return }
            if g_main_context_iteration(nil, 0) == 0 { usleep(1_000) }
        }
    }
}
