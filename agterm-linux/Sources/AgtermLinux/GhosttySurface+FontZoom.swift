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
        guard dashboardFontOverride == nil else { return }
        guard let current = currentFontSize() else { return }
        // The effective base config, not the UI setting: with the setting cleared, a `font-size` from the
        // scoped or inherited Ghostty config is what new sessions use, so a reset must land there too.
        let target = GhosttyApp.shared.currentBaseFontSize
            ?? linuxSettingsStore().load().fontSize ?? DashboardLayout.ghosttyDefaultFontSize
        // Reset uses the surface config's size, which may still carry its old session/creation size.
        // Keep its background overlay, replace that size, then clear libghostty's manual-adjustment flag
        // even when the current size already matches; later config changes must reach this surface.
        guard reapplyBackgroundOverlay(force: true, fontSizeOverride: target) else { return }
        pendingFontRestore = abs(current - target) > 0.5 ? target : nil
        performBindingAction(FontBindingAction.reset)
    }
}
