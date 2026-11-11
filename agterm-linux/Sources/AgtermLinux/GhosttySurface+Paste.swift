import CGtk

extension GhosttySurface: DropPasteTarget {
    /// Insert text as a paste via `ghostty_surface_text` (the macOS `insertPasted` seam): libghostty wraps it
    /// in bracketed-paste markers when the program asked for them, so line breaks are not typed Returns —
    /// with paste's caveat that a raw prompt with mode 2004 off still submits each line, exactly like
    /// Ctrl+Shift+V. Distinct from `inject(text:)`, the control channel's typed `session.type` path.
    /// Unlike macOS, a live IME composition is not committed first: the GtkIMContext is private to
    /// GhosttySurface.swift.
    func insertPasted(text: String) {
        guard let surface, !text.isEmpty else { return }
        text.withCString { ghostty_surface_text(surface, $0, UInt(text.utf8.count)) }
    }
}
