import CGtk
import Foundation
import agtermCore

@MainActor
extension GhosttySurface {
    /// Drop a live font zoom back to the configured default without pinning that default on the session:
    /// the CELL_SIZE report it triggers matches `pendingFontRestore` and is swallowed by `reportFontSize`.
    func resetFontZoomToDefault() {
        guard let current = currentFontSize() else { return }
        let target = linuxSettingsStore().load().fontSize ?? DashboardLayout.ghosttyDefaultFontSize
        guard abs(current - target) > 0.5, let surface else { return }
        pendingFontRestore = target
        let action = "set_font_size:\(String(format: "%g", target))"
        let applied = action.withCString { ghostty_surface_binding_action(surface, $0, UInt(action.utf8.count)) }
        if !applied { performBindingAction(FontBindingAction.reset) }
    }
}
