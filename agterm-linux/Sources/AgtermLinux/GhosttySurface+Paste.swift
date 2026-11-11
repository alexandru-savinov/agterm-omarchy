import CGtk

extension GhosttySurface: DropPasteTarget {
    /// Paste text through libghostty's paste protection, exactly as Ctrl+Shift+V does: the text is armed
    /// for this surface and the `paste_from_clipboard` action starts a clipboard request that
    /// `GhosttyApp.readClipboard` answers with it (see `ArmedDropPaste`). libghostty then frames it in
    /// bracketed-paste markers when the program asked for them, and when it did not and the text spans
    /// lines, it asks through the confirm callback instead of letting each line run. `ghostty_surface_text`
    /// is not used: it pastes with the protection bypassed. Distinct from `inject(text:)`, the control
    /// channel's typed `session.type` path. Unlike macOS, a live IME composition is not committed first:
    /// the GtkIMContext is private to GhosttySurface.swift.
    func pasteThroughProtection(text: String) {
        guard let surface, !text.isEmpty else { return }
        let owner = Unmanaged.passUnretained(self).toOpaque()
        ArmedDropPaste.shared.arm(text, owner: owner)
        // a request libghostty did not start must not leave the text armed for a later real paste
        defer { _ = ArmedDropPaste.shared.take(owner: owner) }
        let action = "paste_from_clipboard"
        _ = action.withCString { ghostty_surface_binding_action(surface, $0, UInt(action.utf8.count)) }
    }
}

extension GhosttySurface: UnsafePasteTarget {
    /// A surface torn down while the dialog was open already freed its request: nothing to answer.
    func completeUnsafePaste(_ text: String, request: UnsafeMutableRawPointer?) {
        guard let surface else { return }
        text.withCString { ghostty_surface_complete_clipboard_request(surface, $0, request, true) }
    }

    /// This libghostty revision has no deny call. An empty completion writes nothing (Surface.zig
    /// `completeClipboardPaste` returns early on empty data) and frees the request.
    func discardUnsafePaste(request: UnsafeMutableRawPointer?) {
        guard let surface else { return }
        "".withCString { ghostty_surface_complete_clipboard_request(surface, $0, request, true) }
    }
}
