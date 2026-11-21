import CGtk
import agtermCore

/// GDK's X11 clipboard announces an empty offer while it waits for the owner's TARGETS response.
/// Keep the key out of WebKit until GDK has classified that offer.
@MainActor
final class LinuxHtmlClipboardPaste {
    enum Offer: Equatable { case unresolved, fileTransfer, other }
    enum Command: String { case paste = "Paste", plainText = "PasteAsPlainText" }

    typealias Action = @MainActor () -> Void
    typealias Scheduler = @MainActor (Double, @escaping Action) -> Action

    struct Source {
        let offer: @MainActor () -> Offer
        let observe: @MainActor (@escaping Action) -> Action?

        static func clipboard(_ clipboard: OpaquePointer) -> Self {
            Self(offer: { LinuxHtmlClipboardPaste.offer(gdk_clipboard_get_formats(clipboard)) },
                 observe: { observeHtmlClipboard(clipboard, changed: $0) })
        }
    }

    private final class Request {
        let source: Source
        let paste: Action
        var stopObserving: Action?
        var deadline: Action?
        var resume: Action?

        init(source: Source, paste: @escaping Action) {
            self.source = source
            self.paste = paste
        }
    }

    private let schedule: Scheduler
    private var pending: Request?

    init(schedule: @escaping Scheduler = { MainTimer.schedule(after: $0, $1) }) {
        self.schedule = schedule
    }

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

    static func command(key: UInt32, modifiers: GdkModifierType) -> Command? {
        let ctrl = GDK_CONTROL_MASK.rawValue
        let shift = GDK_SHIFT_MASK.rawValue
        // GTK text-view bindings handle Paste. WebKit's custom map matches uppercase V and masks Ctrl/Shift/Alt.
        let webKitModifiers = modifiers.rawValue & (ctrl | shift | GDK_ALT_MASK.rawValue)
        if key == UInt32(GDK_KEY_V) && webKitModifiers == (ctrl | shift) { return .plainText }
        switch (key, modifiers.rawValue & gtk_accelerator_get_default_mod_mask().rawValue) {
        case (UInt32(GDK_KEY_v), ctrl), (UInt32(GDK_KEY_V), ctrl),
             (UInt32(GDK_KEY_Insert), shift), (UInt32(GDK_KEY_KP_Insert), shift): return .paste
        default: return nil
        }
    }

    static func refusesPrimary(_ formats: OpaquePointer?) -> Bool {
        // A blocked middle click cannot be replayed safely as a keyboard paste at its original mouse position.
        offer(formats) != .other
    }

    /// True consumes the key. Only X11 has an asynchronous format-discovery phase worth waiting for.
    func intercept(_ source: Source?, holdUnresolved: Bool, paste: @escaping Action) -> Bool {
        cancel("superseded")
        guard let source else { return true }
        switch source.offer() {
        case .other: return false
        case .fileTransfer: return true
        case .unresolved:
            guard holdUnresolved else { return true }
            let request = Request(source: source, paste: paste)
            guard let stop = source.observe({ [weak self, weak request] in
                guard let request else { return }
                self?.clipboardChanged(request)
            }) else { return true }
            request.stopObserving = stop
            pending = request
            request.deadline = schedule(2) { [weak self, weak request] in
                guard let self, let request, pending === request else { return }
                cancel("expired")
            }
            trace("waiting")
            return true
        }
    }

    private func clipboardChanged(_ request: Request) {
        guard pending === request else { return }
        switch request.source.offer() {
        case .unresolved:
            request.resume?()
            request.resume = nil
        case .fileTransfer:
            cancel("blocked")
        case .other:
            guard request.resume == nil else { return }
            // GDK publishes X11 TARGETS only after reading it, including chunked (INCR) replies.
            request.resume = schedule(0) { [weak self, weak request] in
                guard let self, let request, pending === request else { return }
                request.resume = nil
                switch request.source.offer() {
                case .unresolved: break
                case .fileTransfer: cancel("blocked")
                case .other:
                    cancel("allowed")
                    request.paste()
                }
            }
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
        pending.stopObserving?()
        pending.deadline?()
        pending.resume?()
        trace(decision)
    }

    private func trace(_ decision: String) {
        #if DEBUG
        LinuxHtmlPasteTrace.emit("native-paste", [("decision", decision)])
        #endif
    }
}

private final class HtmlClipboardObserver {
    let changed: LinuxHtmlClipboardPaste.Action
    init(_ changed: @escaping LinuxHtmlClipboardPaste.Action) { self.changed = changed }
}

@MainActor
private func observeHtmlClipboard(_ clipboard: OpaquePointer,
                                  changed: @escaping LinuxHtmlClipboardPaste.Action) -> LinuxHtmlClipboardPaste.Action? {
    let data = Unmanaged.passRetained(HtmlClipboardObserver(changed)).toOpaque()
    let handler = g_signal_connect_data(RAW(clipboard), "changed",
                                        unsafeBitCast(onHtmlClipboardChanged, to: GCallback.self),
                                        data, releaseHtmlClipboardObserver, GConnectFlags(rawValue: 0))
    guard handler != 0 else {
        Unmanaged<HtmlClipboardObserver>.fromOpaque(data).release()
        return nil
    }
    g_object_ref(RAW(clipboard))
    return {
        g_signal_handler_disconnect(RAW(clipboard), handler)
        g_object_unref(RAW(clipboard))
    }
}

private let onHtmlClipboardChanged: @MainActor @convention(c) (OpaquePointer?, gpointer?) -> Void = { _, data in
    guard let data else { return }
    let observer = Unmanaged<HtmlClipboardObserver>.fromOpaque(data).takeUnretainedValue()
    observer.changed()
}

private let releaseHtmlClipboardObserver: @convention(c) (gpointer?, UnsafeMutablePointer<GClosure>?) -> Void = { data, _ in
    if let data { Unmanaged<HtmlClipboardObserver>.fromOpaque(data).release() }
}
