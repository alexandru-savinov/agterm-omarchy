import CGtk
import CWebKit
import Foundation
import agtermCore

@MainActor
extension LinuxHtmlOverlayPage {
    func installBridge() {
        guard LinuxHtmlBridgeGate.installs(for: overlay.source) else { return }
        let data = Unmanaged.passUnretained(self).toOpaque()
        connect(manager, "script-message-with-reply-received::agterm", unsafeBitCast(onHtmlBridgeRequest, to: GCallback.self), data)
        for world in overlay.javascript ? ["agterm-bridge", ""] : ["agterm-bridge"] {
            world.withCString { name in
                _ = "agterm".withCString {
                    webkit_user_content_manager_register_script_message_handler_with_reply(manager, $0,
                                                                                           world.isEmpty ? nil : name)
                }
            }
        }
        addBridgeScript(LinuxHtmlBridgeScripts.adapter(nonce: bridgeNonce), world: "agterm-bridge")
        if overlay.javascript { addBridgeScript(LinuxHtmlBridgeScripts.helper(nonce: bridgeNonce), world: nil) }
    }

    func addBridgeScript(_ text: String, world: String?, atEnd: Bool = false) {
        let injection = atEnd ? WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_END : WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_START
        text.withCString { source in
            let script: OpaquePointer?
            if let world {
                script = world.withCString {
                    webkit_user_script_new_for_world(source, WEBKIT_USER_CONTENT_INJECT_TOP_FRAME,
                                                     injection, $0, nil, nil)
                }
            } else {
                script = webkit_user_script_new(source, WEBKIT_USER_CONTENT_INJECT_TOP_FRAME,
                                                injection, nil, nil)
            }
            webkit_user_content_manager_add_script(manager, script)
            webkit_user_script_unref(script)
        }
    }

    func bridgeRequest(_ value: OpaquePointer?, reply: OpaquePointer?) {
        guard let reply else { return }
        var owner: (controller: AppController, session: Session)?
        let admission = LinuxHtmlBridgeGate(pageID: id, nonce: bridgeNonce).admit(
            source: overlay.source, closed: closed,
            page: {
                guard let store, let controller, let slot = store.htmlOverlaySlot(id) else { return nil }
                owner = (controller, slot.session)
                return HtmlBridgePage(window: controller.windowID.uuidString, session: slot.session.id, pane: slot.pane)
            },
            uri: { webkit_web_view_get_uri(cast(webView)).map(String.init(cString:)) },
            json: {
                guard let json = jsc_value_to_json(value, 0) else { return nil }
                defer { g_free(json) }
                return String(cString: json)
            })
        let admitted: LinuxHtmlBridgeGate.Admitted
        switch admission {
        case .reject(let error): return rejectBridge(reply, error)
        case .admitted(let passed): admitted = passed
        }
        if let owner, let record = owner.controller.replicaPages[id], let channel = record.channel,
           !(owner.session.content == .page && linuxBridgeAddressesOwnPage(admitted.object)) {
            guard let context = jsc_value_get_context(value) else { return rejectBridge(reply, "no JavaScript context") }
            let delivery = LinuxHtmlBridgeReply(reply: reply, context: context)
            channel.bridge(String(decoding: admitted.requestData, as: UTF8.self)) { result in
                switch result {
                case .success(let response):
                    delivery.finish((try? JSONDecoder().decode(ControlResponse.self, from: Data(response.utf8)))
                        ?? ControlResponse(ok: false, error: "invalid response from page origin"))
                case .failure: delivery.finish(ControlResponse(ok: false, error: "page channel closed"))
                }
            }
            return
        }
        let request: ControlRequest
        switch LinuxHtmlBridgeGate.request(admitted) {
        case .reject(let error): return rejectBridge(reply, error)
        case .dispatch(let decoded): request = decoded
        }
        guard let context = jsc_value_get_context(value) else { return rejectBridge(reply, "no JavaScript context") }
        let delivery = LinuxHtmlBridgeReply(reply: reply, context: context)
        let server = gControlServer
        Thread.detachNewThread {
            let response = server.performRequest(request)
            runOnMain { MainActor.assumeIsolated { delivery.finish(response) } }
        }
    }

    private func rejectBridge(_ reply: OpaquePointer, _ error: String) {
        error.withCString { webkit_script_message_reply_return_error_message(reply, $0) }
    }

    func applyZoom() {
        let zoom = linuxSettingsStore().load().effectiveHtmlOverlayZoom
        webkit_web_view_set_zoom_level(cast(webView), zoom)
    }
}

/// Retain only the reply and JS context across the worker; touch both again on GTK's main thread.
@MainActor
private final class LinuxHtmlBridgeReply {
    let reply: OpaquePointer
    let context: OpaquePointer
    init(reply: OpaquePointer, context: OpaquePointer) {
        self.reply = webkit_script_message_reply_ref(reply)
        self.context = context
        g_object_ref(RAW(context))
    }
    func finish(_ response: ControlResponse) {
        if !response.ok {
            (response.error ?? "request failed").withCString { webkit_script_message_reply_return_error_message(reply, $0) }
        } else {
            let data = response.result.flatMap { try? JSONEncoder().encode($0) } ?? Data("{}".utf8)
            let value = String(decoding: data, as: UTF8.self).withCString { jsc_value_new_from_json(context, $0) }
            webkit_script_message_reply_return_value(reply, value)
            if let value { g_object_unref(RAW(value)) }
        }
        webkit_script_message_reply_unref(reply)
        g_object_unref(RAW(context))
    }
}

private let onHtmlBridgeRequest: @MainActor @convention(c)
    (OpaquePointer?, OpaquePointer?, OpaquePointer?, gpointer?) -> gboolean = { _, value, reply, data in
        guard let data else { return 0 }
        Unmanaged<LinuxHtmlOverlayPage>.fromOpaque(data).takeUnretainedValue().bridgeRequest(value, reply: reply)
        return 1
    }

func linuxBridgeAddressesOwnPage(_ object: Any) -> Bool {
    guard let request = object as? [String: Any], let command = request["cmd"] as? String,
          ["session.overlay.reload", "session.overlay.navigate"].contains(command) else { return false }
    let args = request["args"] as? [String: Any]
    return ![request["target"], args?["targets"], args?["window"]].contains { $0 != nil && !($0 is NSNull) }
}
