import CGtk
import agtermCore

/// GDK's X11 clipboard announces an empty offer while it waits for the owner's TARGETS response.
/// Keep the key out of WebKit until GDK has classified that offer.
@MainActor
final class LinuxHtmlClipboardPaste {
    enum Offer: Equatable { case unresolved, fileTransfer, other }

    private var pending: (clipboard: OpaquePointer, handler: gulong, paste: @MainActor () -> Void)?
    private var cancelDeadline: (@MainActor () -> Void)?

    static func offer(_ formats: OpaquePointer?) -> Offer {
        guard let formats else { return .unresolved }
        if hasFileTransfer(formats) { return .fileTransfer }
        var mimeCount: gsize = 0
        var typeCount: gsize = 0
        _ = gdk_content_formats_get_mime_types(formats, &mimeCount)
        _ = gdk_content_formats_get_gtypes(formats, &typeCount)
        return mimeCount == 0 && typeCount == 0 ? .unresolved : .other
    }

    static func hasFileTransfer(_ formats: OpaquePointer?) -> Bool {
        guard let formats else { return false }
        if gdk_content_formats_contain_gtype(formats, gdk_file_list_get_type()) != 0 { return true }
        return [LinuxClipboardPolicy.uriListMime, "x-special/gnome-copied-files", "application/vnd.portal.filetransfer"]
            .contains { mime in
                mime.withCString { gdk_content_formats_contain_mime_type(formats, $0) != 0 }
            }
    }

    /// True consumes the current key. A fully classified, non-file offer takes WebKit's normal path.
    func intercept(_ clipboard: OpaquePointer?, paste: @escaping @MainActor () -> Void) -> Bool {
        cancel("superseded")
        guard let clipboard else { return true }
        switch Self.offer(gdk_clipboard_get_formats(clipboard)) {
        case .other: return false
        case .fileTransfer: return true
        case .unresolved:
            let handler = g_signal_connect_data(RAW(clipboard), "changed",
                                                unsafeBitCast(onHtmlClipboardChanged, to: GCallback.self),
                                                Unmanaged.passUnretained(self).toOpaque(), nil, GConnectFlags(rawValue: 0))
            guard handler != 0 else { return true }
            pending = (clipboard, handler, paste)
            g_object_ref(RAW(clipboard))
            // Bound the owner wait even when TARGETS never arrives. Retain the observer until it disconnects.
            cancelDeadline = MainTimer.schedule(after: 2) { [self] in cancel("expired") }
            trace("waiting")
            return true
        }
    }

    fileprivate func clipboardChanged(_ clipboard: OpaquePointer?) {
        guard let pending, pending.clipboard == clipboard else { return }
        switch Self.offer(gdk_clipboard_get_formats(pending.clipboard)) {
        case .unresolved:
            // A second empty notification means the offer was invalidated before classification.
            cancel("invalidated")
        case .fileTransfer:
            cancel("blocked")
        case .other:
            cancel("allowed")
            pending.paste()
        }
    }

    func keyPressed(isModifier: Bool) {
        guard pending != nil else { return }
        // A modifier by itself leaves the paste target and selection intact.
        if isModifier {
            trace("modifier")
        } else {
            cancel("key")
        }
    }

    func cancel(_ decision: String) {
        guard let pending else { return }
        self.pending = nil
        cancelDeadline?()
        cancelDeadline = nil
        g_signal_handler_disconnect(RAW(pending.clipboard), pending.handler)
        g_object_unref(RAW(pending.clipboard))
        trace(decision)
    }

    private func trace(_ decision: String) {
        #if DEBUG
        LinuxHtmlPasteTrace.emit("native-paste", [("decision", decision)])
        #endif
    }
}

private let onHtmlClipboardChanged: @MainActor @convention(c) (OpaquePointer?, gpointer?) -> Void = { clipboard, data in
    guard let data else { return }
    Unmanaged<LinuxHtmlClipboardPaste>.fromOpaque(data).takeUnretainedValue().clipboardChanged(clipboard)
}
