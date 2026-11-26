import Foundation

/// The host's main-actor timer seam — the ONE place deferred main-actor work is scheduled from, so a
/// host whose main loop drains something other than the dispatch main queue can swap in its own timer
/// once at startup instead of every call site growing a platform branch.
///
/// The defaults use the dispatch main queue and, for pending-close grace, a sleeping main-actor task.
/// A host whose main loop services neither MUST install its own timer before scheduling anything: the
/// GTK port's GLib loop is one — under `g_application_run` neither libdispatch's main queue nor the
/// Swift Concurrency main-actor executor is drained, so the default would silently never fire (this
/// was the Linux theme-preview live-navigation bug, and it also muted every debounced save and the
/// soft-close grace finalizer).
///
/// Foundation-only — host-free. `@MainActor` so scheduled work runs on the main actor like its callers.
@MainActor
public enum MainTimer {
    public typealias Scheduler = @MainActor (TimeInterval, @escaping @MainActor () -> Void) -> (@MainActor () -> Void)

    /// Host override for both timer modes. GTK installs this at startup and tests swap it to drive time.
    public static var scheduleTimer: Scheduler?

    private static let dispatchScheduler: Scheduler = { delay, fire in
        var delivery: Task<Void, Never>?
        let item = DispatchWorkItem {
            guard delay > 0 else {
                fire()
                return
            }
            delivery = Task {
                guard !Task.isCancelled else { return }
                delivery = nil
                fire()
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: item)
        return {
            item.cancel()
            delivery?.cancel()
            delivery = nil
        }
    }

    /// Schedules `fire` after `delay` and returns a closure that cancels the pending callback.
    @discardableResult
    public static func schedule(after delay: TimeInterval,
                                _ fire: @escaping @MainActor () -> Void) -> (@MainActor () -> Void) {
        (scheduleTimer ?? dispatchScheduler)(delay, fire)
    }

    /// Preserve upstream's actor-task countdown for pending closes. Its wakeup resumes the original
    /// task alongside queued undo work. A host override supplies the clock and delivery instead.
    static func scheduleCloseGrace(after delay: TimeInterval,
                                   _ fire: @escaping @MainActor () -> Void) -> (@MainActor () -> Void) {
        if let scheduleTimer { return scheduleTimer(delay, fire) }
        let nanoseconds = UInt64(delay * 1_000_000_000)
        let task = Task { @MainActor in
            try? await Task.sleep(nanoseconds: nanoseconds)
            guard !Task.isCancelled else { return }
            fire()
        }
        return { task.cancel() }
    }
}
