import CGtk
import Foundation
import agtermCore

@MainActor
final class ClipboardPromptController {
    static let shared = ClipboardPromptController()

    /// Shows the prompt for `key`; the dialog's answer must come back through `resolve`.
    typealias Presenter = @MainActor (PromptKey, AnyObject?) -> Void

    struct PromptKey: Hashable {
        let access: ClipboardAccess
        let requester: ObjectIdentifier?
    }

    private struct Pending {
        let requester: AnyObject?
        var waiters: [(Bool) -> Void]
    }

    @MainActor
    final class ResponseContext {
        let key: PromptKey
        private let controller: ClipboardPromptController
        private var resolved = false

        init(key: PromptKey, controller: ClipboardPromptController) {
            self.key = key
            self.controller = controller
        }

        func resolve(_ response: String) {
            guard !resolved else { return }
            resolved = true
            controller.resolve(key, response: response)
        }
    }

    private var policy = ClipboardPromptPolicy()
    private var pending: [PromptKey: Pending] = [:]
    private let present: Presenter

    init(present: Presenter? = nil) {
        self.present = present ?? { key, requester in Self.presentDialog(key, requester: requester as? GhosttySurface) }
    }

    func request(_ access: ClipboardAccess, requester: AnyObject?, completion: @escaping (Bool) -> Void) {
        switch policy.decision(for: access) {
        case .allow:
            completion(true)
        case .deny:
            completion(false)
        case .prompt:
            let key = PromptKey(access: access, requester: requester.map(ObjectIdentifier.init))
            if pending[key] != nil {
                pending[key]?.waiters.append(completion)
                return
            }
            pending[key] = Pending(requester: requester, waiters: [completion])
            present(key, requester)
        }
    }

    private static func presentDialog(_ key: PromptKey, requester: GhosttySurface?) {
        let heading = key.access == .read ? "Allow a program to read the clipboard?" : "Allow a program to set the clipboard?"
        let body = key.access == .read
            ? "A program running in the terminal is trying to read your clipboard contents (OSC 52)."
            : "A program running in the terminal is trying to replace your clipboard contents (OSC 52)."
        let dialog = OpaquePointer(heading.withCString { h in body.withCString { b in adw_alert_dialog_new(h, b) } })
        "deny".withCString { i in "Deny".withCString { l in adw_alert_dialog_add_response(cast(dialog), i, l) } }
        "allow".withCString { i in "Allow".withCString { l in adw_alert_dialog_add_response(cast(dialog), i, l) } }
        "always-deny".withCString { i in "Always Deny".withCString { l in adw_alert_dialog_add_response(cast(dialog), i, l) } }
        "always-allow".withCString { i in "Always Allow".withCString { l in adw_alert_dialog_add_response(cast(dialog), i, l) } }
        "allow".withCString { adw_alert_dialog_set_response_appearance(cast(dialog), $0, ADW_RESPONSE_SUGGESTED) }
        "always-deny".withCString { adw_alert_dialog_set_response_appearance(cast(dialog), $0, ADW_RESPONSE_DESTRUCTIVE) }
        "deny".withCString { adw_alert_dialog_set_close_response(cast(dialog), $0) }
        connectResponse(dialog, context: ResponseContext(key: key, controller: .shared))
        adw_dialog_present(cast(dialog), W(window(for: requester)))
    }

    static func connectResponse(_ dialog: OpaquePointer, context: ResponseContext) {
        let ctx = Unmanaged.passRetained(context).toOpaque()
        "response".withCString {
            _ = g_signal_connect_data(RAW(dialog), $0,
                unsafeBitCast(onClipboardPromptResponse, to: GCallback.self), ctx,
                onClipboardPromptDestroyed, GConnectFlags(rawValue: 0))
        }
    }

    func resolve(_ key: PromptKey, response: String) {
        guard let entry = pending.removeValue(forKey: key) else { return }
        let allowed = response == "allow" || response == "always-allow"
        if response == "always-allow" || response == "always-deny" {
            policy.remember(key.access, allow: allowed)
        }
        for waiter in entry.waiters { waiter(allowed) }
    }

    private static func window(for requester: GhosttySurface?) -> OpaquePointer? {
        guard let requester else { return gController?.windowPointer }
        return gWindows.values.first { $0.store.session(withID: requester.sessionID) != nil }?.windowPointer
            ?? gController?.windowPointer
    }
}

let onClipboardPromptResponse: @MainActor @convention(c) (OpaquePointer?, UnsafePointer<CChar>?, gpointer?) -> Void = { _, response, data in
    guard let data else { return }
    let ctx = Unmanaged<ClipboardPromptController.ResponseContext>.fromOpaque(data).takeUnretainedValue()
    let id = response.map { String(cString: $0) } ?? "deny"
    MainActor.assumeIsolated { ctx.resolve(id) }
}

let onClipboardPromptDestroyed: @convention(c) (gpointer?, UnsafeMutablePointer<GClosure>?) -> Void = { data, _ in
    guard let data else { return }
    let ctx = Unmanaged<ClipboardPromptController.ResponseContext>.fromOpaque(data).takeRetainedValue()
    MainActor.assumeIsolated { ctx.resolve("deny") }
}
