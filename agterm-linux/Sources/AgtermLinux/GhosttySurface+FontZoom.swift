import CGtk
import Foundation
import agtermCore

@MainActor
extension GhosttySurface {
    /// Drop a live font zoom back to the configured default without pinning that default on the session:
    /// the CELL_SIZE report it triggers matches `pendingFontRestore` and is swallowed by `reportFontSize`.
    func resetFontZoomToDefault() {
        // A dashboard tile runs at the dashboard's size, which is applied only when the dashboard opens:
        // resetting it here would leave the tile at full size until the dashboard is reopened.
        guard let current = currentFontSize() else { return }
        // The effective base config, not the UI setting: with the setting cleared, a `font-size` from the
        // scoped or inherited Ghostty config is what new sessions use, so a reset must land there too.
        let target = GhosttyApp.shared.currentBaseFontSize
            ?? linuxSettingsStore().load().fontSize ?? DashboardLayout.ghosttyDefaultFontSize
        guard abs(current - target) > 0.5, let surface else { return }
        pendingFontRestore = target
        let action = "set_font_size:\(String(format: "%g", target))"
        let applied = action.withCString { ghostty_surface_binding_action(surface, $0, UInt(action.utf8.count)) }
        if !applied { performBindingAction(FontBindingAction.reset) }
    }
}
