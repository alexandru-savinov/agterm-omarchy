import Foundation

/// Every window opts out of restoration and `WindowLibrary` reopens its own set, so AppKit's saved state never
/// holds a window. After an unclean exit AppKit restores it anyway, SwiftUI then opens no launch window, and the
/// scene `.task` that binds the control socket never runs (#431). Registered, so a value the user sets still wins.
enum AppKitSavedState {
    static let ignoreKey = "ApplePersistenceIgnoreState"

    static func ignore(in defaults: UserDefaults = .standard) {
        defaults.register(defaults: [ignoreKey: true])
    }
}
