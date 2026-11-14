@testable import AgtermLinux

/// `main()` initializes libghostty's process runtime (resource env, `ghostty_init`) once before GTK starts;
/// GTK tests that start `GhosttyApp` directly must do the same, exactly once per test process.
enum GhosttyTestRuntime {
    static let initialized: Void = GhosttyApp.shared.initializeRuntime()
}
