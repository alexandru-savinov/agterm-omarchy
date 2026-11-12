import CGtk
import Foundation
import Testing
@testable import AgtermLinux

/// The REAL `g_timeout_add_full` source behind `BlinkPhaseCoordinator`, driven headlessly by pumping the
/// GLib default context (no display, no `gtk_init`) — the recorder in `BlinkPhaseCoordinatorTests` never
/// reaches GLib, so it cannot see a source that survives `cancel()`. Each subject's tick is wrapped to
/// count GLib DISPATCHES, not applied phases: the coordinator swallows a late tick (`tick()` guards
/// `isArmed`), so counting applies would pass over a leaked source that still wakes the main loop.
///
/// The negative window is measured by a positive control, not by a sleep: a sibling source with the same
/// interval keeps ticking on the same context, and the cancelled one must stay silent while the sibling
/// fires `clockTicks` more times. A surviving source due on the same schedule would fire in between.
@MainActor
@Suite(.serialized)
struct BlinkPhaseSourceGLibTests {
    private static let clockTicks = 4

    @Test("cancel() and resync(shouldRun: false) remove the real GLib source while a sibling keeps ticking")
    func cancelledSourcesStopDispatching() throws {
        var clockFires = 0, cancelledFires = 0, resyncedFires = 0
        let clock = BlinkPhaseCoordinator(start: counting { clockFires += 1 })
        let cancelled = BlinkPhaseCoordinator(start: counting { cancelledFires += 1 })
        let resynced = BlinkPhaseCoordinator(start: counting { resyncedFires += 1 })
        defer { clock.cancel(); cancelled.cancel(); resynced.cancel() }

        clock.resync(shouldRun: true) { _ in }
        cancelled.resync(shouldRun: true) { _ in }
        resynced.resync(shouldRun: true) { _ in }

        // Positive arm: every subject's real source does dispatch, so a zero below is not a dead counter.
        try #require(pumpGLib(until: { cancelledFires >= 2 && resyncedFires >= 2 && clockFires >= 2 }),
                     "the real blink sources never dispatched within the bound")

        cancelled.cancel()
        resynced.resync(shouldRun: false) { _ in }
        #expect(!cancelled.isArmed && !resynced.isArmed)
        let cancelledBase = cancelledFires, resyncedBase = resyncedFires, clockBase = clockFires

        try #require(pumpGLib(until: { clockFires >= clockBase + Self.clockTicks }),
                     "the control source stopped ticking, so the silence below would prove nothing")
        #expect(cancelledFires == cancelledBase, "a cancelled blink source kept dispatching")
        #expect(resyncedFires == resyncedBase, "a resync(shouldRun: false) left its blink source dispatching")
    }

    /// The production starter, with its tick wrapped so the test counts every GLib dispatch.
    private func counting(
        _ onFire: @escaping @MainActor () -> Void
    ) -> @MainActor (TimeInterval, @escaping @MainActor () -> Bool) -> (@MainActor () -> Void) {
        { interval, tick in
            BlinkPhaseSource.start(interval: interval) {
                onFire()
                return tick()
            }
        }
    }

    /// Blocking iterations of the default context until `done` holds, bounded by a monotonic deadline.
    /// Only called while the clock source is armed, so each blocking iteration has a source to wake on.
    private func pumpGLib(until done: () -> Bool, timeout: TimeInterval = 30) -> Bool {
        let deadline = DispatchTime.now().uptimeNanoseconds + UInt64(timeout * 1_000_000_000)
        while !done() {
            if DispatchTime.now().uptimeNanoseconds > deadline { return false }
            _ = g_main_context_iteration(nil, 1)
        }
        return true
    }
}

#if DEBUG
@Suite("blink phase trace")
struct BlinkPhaseTraceTests {
    @Test("the trace line has the exact shape the blink-unmapped AT-SPI scenario parses")
    func lineShapeIsPinned() throws {
        let window = try #require(UUID(uuidString: "0A1B2C3D-0000-4000-8000-00000000ABCD"))
        #expect(BlinkPhaseTrace.line(window: window, event: "resync",
                                     fields: [("armed", "1"), ("marked", "2")])
                == "agterm-blink-trace: window=0a1b2c3d-0000-4000-8000-00000000abcd event=resync armed=1 marked=2")
        #expect(BlinkPhaseTrace.environmentKey == "AGTERM_ATSPI_BLINK_TRACE")
    }
}
#endif
