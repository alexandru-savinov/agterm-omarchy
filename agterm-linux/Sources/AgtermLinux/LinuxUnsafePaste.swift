import CGtk
import Foundation

/// What agterm does when libghostty asks it to confirm a clipboard request. libghostty makes the safety
/// call itself (Surface.zig `completeClipboardPaste`: `clipboard-paste-protection`, bracketed-paste mode,
/// `clipboard-paste-bracketed-safe`); it only reaches this callback for a paste it judged unsafe, and the
/// app's job is to ask the user rather than wave it through.
enum ClipboardConfirmRoute: Equatable {
    /// A paste libghostty judged unsafe: show the text and ask Paste / Cancel.
    case unsafePaste
    /// An OSC 52 read: the existing per-access clipboard consent prompt.
    case clipboardReadConsent
    /// Anything else this libghostty revision might raise: complete as before.
    case confirmAsIs
}

enum PasteLineEndings {
    /// libghostty's safety check looks only for "\n" (paste.zig `isSafe`) and its encoder leaves a lone
    /// "\r" alone, so CR-only lines would reach a raw prompt unasked and each CR would submit one.
    /// Apply only to known paste requests; OSC 52 replies must preserve the original bytes.
    static func normalized(_ text: String) -> String {
        guard text.contains("\r") else { return text }
        return text.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\r", with: "\n")
    }
}

enum LinuxClipboardReadPolicy {
    static func payload(_ text: String, request: ghostty_clipboard_request_e?) -> String {
        guard request == GHOSTTY_CLIPBOARD_REQUEST_PASTE else { return text }
        // File-manager copies become shell paths only for paste, never for a terminal clipboard query.
        return PasteLineEndings.normalized(PasteDecoder.posixPaths(fromURIList: text) ?? text)
    }
}

enum LinuxClipboardConfirmPolicy {
    static func route(for request: ghostty_clipboard_request_e) -> ClipboardConfirmRoute {
        switch request {
        case GHOSTTY_CLIPBOARD_REQUEST_PASTE: .unsafePaste
        case GHOSTTY_CLIPBOARD_REQUEST_OSC_52_READ: .clipboardReadConsent
        default: .confirmAsIs
        }
    }
}

/// A drop has no clipboard request of its own, and `ghostty_surface_text` completes its paste with
/// `allow_unsafe = true` (Surface.zig `textCallback`), skipping libghostty's paste protection. So a drop
/// is armed here and delivered through the `paste_from_clipboard` binding action, whose clipboard read
/// libghostty starts synchronously on the calling thread; `readClipboard` takes the armed text instead of
/// reading the clipboard, and the completion goes through the same protection as Ctrl+Shift+V.
final class ArmedDropPaste: @unchecked Sendable {
    static let shared = ArmedDropPaste()

    private let lock = NSLock()
    private var armed: (owner: UnsafeMutableRawPointer, text: String)?

    func arm(_ text: String, owner: UnsafeMutableRawPointer) {
        lock.withLock { armed = (owner, text) }
    }

    /// The armed text for `owner`, at most once; another surface's read never sees it.
    func take(owner: UnsafeMutableRawPointer?) -> String? {
        lock.withLock {
            guard let current = armed, current.owner == owner else { return nil }
            armed = nil
            return current.text
        }
    }
}

/// Where the user's answer to an unsafe paste goes: libghostty's pending request for one surface.
@MainActor
protocol UnsafePasteTarget: AnyObject {
    /// Hand the text back to libghostty as confirmed, so it is written to the terminal.
    func completeUnsafePaste(_ text: String, request: UnsafeMutableRawPointer?)
    /// End the request without writing anything to the terminal.
    func discardUnsafePaste(request: UnsafeMutableRawPointer?)
}

/// Asks before an unsafe paste reaches the terminal. Each paste is its own question (no coalescing and no
/// "always" answer); only an explicit Paste delivers, and closing the dialog any other way cancels.
@MainActor
final class UnsafePasteController {
    static let shared = UnsafePasteController()

    typealias Answer = @MainActor (String) -> Void
    /// Shows the text and calls the answer with the chosen response id at most once.
    typealias Presenter = @MainActor (_ text: String, _ requester: AnyObject, _ answer: @escaping Answer) -> Void

    static let pasteResponse = "paste"
    static let cancelResponse = "cancel"

    private let present: Presenter

    init(present: Presenter? = nil) {
        self.present = present ?? { text, requester, answer in
            Self.presentDialog(text, requester: requester as? GhosttySurface, answer: answer)
        }
    }

    func confirm(_ text: String, request: UnsafeMutableRawPointer?, on target: some UnsafePasteTarget,
                 done: @escaping @MainActor () -> Void = {}) {
        var answered = false
        present(text, target) { response in
            guard !answered else { return }
            answered = true
            defer { done() }
            if response == Self.pasteResponse {
                target.completeUnsafePaste(text, request: request)
            } else {
                target.discardUnsafePaste(request: request)
            }
        }
    }

    /// Long pastes are shown truncated, and the hidden tail is stated, since all of it gets pasted.
    static func preview(_ text: String, limit: Int = 2000) -> String {
        guard text.count > limit else { return text }
        let hidden = text.dropFirst(limit)
        let hiddenLines = hidden.reduce(0) { $1 == "\n" ? $0 + 1 : $0 }
        let characters = "\(hidden.count) more character\(hidden.count == 1 ? "" : "s")"
        let notice = hiddenLines > 0
            ? "\(hiddenLines) more line\(hiddenLines == 1 ? "" : "s") (\(characters)) not shown"
            : "\(characters) not shown"
        return String(text.prefix(limit)) + "\n… " + notice
    }

    /// Immutable, and `answer` is main-actor isolated, so handing it to the main actor from the GLib
    /// destroy-notify (which is not declared main-actor) is safe.
    final class ResponseContext: @unchecked Sendable {
        let answer: Answer
        init(answer: @escaping Answer) { self.answer = answer }
    }

    private static func presentDialog(_ text: String, requester: GhosttySurface?, answer: @escaping Answer) {
        let heading = "Paste this text?"
        // libghostty also flags a bracketed paste carrying the end-of-paste marker, so do not claim why
        let body = "This text may not reach the program in this terminal as a single paste: each line "
            + "can run as a command as soon as it is pasted."
        let dialog = OpaquePointer(heading.withCString { h in body.withCString { b in adw_alert_dialog_new(h, b) } })
        cancelResponse.withCString { i in "Cancel".withCString { l in adw_alert_dialog_add_response(cast(dialog), i, l) } }
        pasteResponse.withCString { i in "Paste".withCString { l in adw_alert_dialog_add_response(cast(dialog), i, l) } }
        pasteResponse.withCString { adw_alert_dialog_set_response_appearance(cast(dialog), $0, ADW_RESPONSE_DESTRUCTIVE) }
        cancelResponse.withCString { adw_alert_dialog_set_default_response(cast(dialog), $0) }
        cancelResponse.withCString { adw_alert_dialog_set_close_response(cast(dialog), $0) }

        let label = OpaquePointer(preview(text).withCString { gtk_label_new($0) })
        gtk_label_set_selectable(label, 1)
        gtk_label_set_wrap(label, 1)
        gtk_label_set_xalign(label, 0)
        gtk_widget_add_css_class(W(label), "monospace")
        if let scroller = op(gtk_scrolled_window_new()) {
            gtk_scrolled_window_set_policy(scroller, GTK_POLICY_NEVER, GTK_POLICY_AUTOMATIC)
            gtk_scrolled_window_set_max_content_height(scroller, 240)
            gtk_scrolled_window_set_propagate_natural_height(scroller, 1)
            gtk_scrolled_window_set_child(scroller, W(label))
            adw_alert_dialog_set_extra_child(cast(dialog), W(scroller))
        }

        let ctx = Unmanaged.passRetained(ResponseContext(answer: answer)).toOpaque()
        "response".withCString {
            _ = g_signal_connect_data(RAW(dialog), $0,
                unsafeBitCast(onUnsafePasteResponse, to: GCallback.self), ctx,
                onUnsafePasteDialogDestroyed, GConnectFlags(rawValue: 0))
        }
        adw_dialog_present(cast(dialog), W(window(for: requester)))
    }

    private static func window(for requester: GhosttySurface?) -> OpaquePointer? {
        guard let requester else { return gController?.windowPointer }
        return gWindows.values.first { $0.store.session(withID: requester.sessionID) != nil }?.windowPointer
            ?? gController?.windowPointer
    }
}

private let onUnsafePasteResponse: @MainActor @convention(c) (OpaquePointer?, UnsafePointer<CChar>?, gpointer?) -> Void = { _, response, data in
    guard let data else { return }
    let ctx = Unmanaged<UnsafePasteController.ResponseContext>.fromOpaque(data).takeUnretainedValue()
    let id = response.map { String(cString: $0) } ?? UnsafePasteController.cancelResponse
    MainActor.assumeIsolated { ctx.answer(id) }
}

/// A dialog destroyed without a response (window closed, app quitting) cancels; the controller ignores
/// this second answer when a response already came.
private let onUnsafePasteDialogDestroyed: @convention(c) (gpointer?, UnsafeMutablePointer<GClosure>?) -> Void = { data, _ in
    guard let data else { return }
    let ctx = Unmanaged<UnsafePasteController.ResponseContext>.fromOpaque(data).takeRetainedValue()
    MainActor.assumeIsolated { ctx.answer(UnsafePasteController.cancelResponse) }
}
