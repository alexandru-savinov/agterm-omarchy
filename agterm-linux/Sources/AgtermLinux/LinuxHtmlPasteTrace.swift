#if DEBUG
import CGtk
import CWebKit
import Foundation

/// Opt-in stderr observer for the `html-overlay` mixed-offer paste check (#421), read by
/// `tests/atspi_html_overlay.py`. Compiled out of release builds and silent unless `AGTERM_ATSPI_PASTE_TRACE=1`.
/// It records type names, booleans and `CLOCK_MONOTONIC` microseconds only, never clipboard or page text,
/// and requests no clipboard transfer: formats are the cached set GDK already holds.
@MainActor
enum LinuxHtmlPasteTrace {
    nonisolated static let environmentKey = "AGTERM_ATSPI_PASTE_TRACE"
    nonisolated static let prefix = "agterm-paste-trace:"
    nonisolated static let handler = "agtermPasteTrace"
    nonisolated static let enabled = ProcessInfo.processInfo.environment[environmentKey] == "1"
    private static let fileTransferMimes = ["text/uri-list", "x-special/gnome-copied-files",
                                            "application/vnd.portal.filetransfer"]
    private static var observingClipboard = false

    /// `agterm-paste-trace: event=<name> at=<µs> key=value…`; `LinuxHtmlPasteTraceTests` pins the shape.
    nonisolated static func line(event: String, at time: Int64, fields: [(String, String)]) -> String {
        ([prefix, "event=\(event)", "at=\(time)"] + fields.map { "\($0.0)=\($0.1)" }).joined(separator: " ")
    }

    /// Comma-joined type names; anything that is not a plain type token is reduced to `?`.
    nonisolated static func types(_ names: [String]) -> String {
        guard !names.isEmpty else { return "none" }
        return names.map { name in
            name.count <= 80 && !name.isEmpty && name.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || "._+/-;=".contains($0)) })
                ? name : "?"
        }.joined(separator: ",")
    }

    nonisolated static func flag(_ value: Bool) -> String { value ? "1" : "0" }

    static func emit(_ event: String, _ fields: [(String, String)]) {
        guard enabled else { return }
        FileHandle.standardError.write(Data((line(event: event, at: Int64(g_get_monotonic_time()), fields: fields) + "\n").utf8))
    }

    /// The fields the page's key guard decides on, recomputed from the same cached formats.
    static func formatFields(_ clipboard: OpaquePointer?) -> [(String, String)] {
        guard let clipboard, let formats = gdk_clipboard_get_formats(clipboard) else { return [("formats", "absent")] }
        var count: gsize = 0
        var names: [String] = []
        if let mimes = gdk_content_formats_get_mime_types(formats, &count) {
            names = (0..<Int(count)).compactMap { index in mimes[index].map { String(cString: $0) } }
        }
        let fileList = gdk_content_formats_contain_gtype(formats, gdk_file_list_get_type()) != 0
        let mimeHits = fileTransferMimes.map { mime in mime.withCString { gdk_content_formats_contain_mime_type(formats, $0) != 0 } }
        return [("local", flag(gdk_clipboard_is_local(clipboard) != 0)), ("fileListGType", flag(fileList)),
                ("uriList", flag(mimeHits[0])), ("gnomeFiles", flag(mimeHits[1])), ("portal", flag(mimeHits[2])),
                ("refuses", flag(fileList || mimeHits.contains(true))), ("mimes", types(names))]
    }

    static func displayClipboard() -> OpaquePointer? {
        gdk_display_get_default().flatMap { gdk_display_get_clipboard($0) }
    }

    /// One app-wide `changed` observer: GDK emits it once it has claimed the new owner's formats.
    static func observeClipboard() {
        guard enabled, !observingClipboard, let clipboard = displayClipboard() else { return }
        observingClipboard = true
        connect(clipboard, "changed", unsafeBitCast(onTracedClipboardChanged, to: GCallback.self))
        emit("receiver-formats", [("reason", "install")] + formatFields(clipboard))
    }

    static func key(_ event: OpaquePointer, insert: Bool) {
        let modifiers = UInt32(gdk_event_get_modifier_state(event).rawValue)
        let keyFields: [(String, String)] = [("key", insert ? "insert" : "v"),
                                             ("ctrl", flag(modifiers & UInt32(GDK_CONTROL_MASK.rawValue) != 0)),
                                             ("shift", flag(modifiers & UInt32(GDK_SHIFT_MASK.rawValue) != 0))]
        emit("receiver-key", keyFields + formatFields(displayClipboard()))
    }

    /// A message from the instrumented transfer guard; only its booleans and type names are kept.
    static func receive(_ value: OpaquePointer?) {
        guard let raw = jsc_value_to_json(value, 0) else { return emit("dom-message", [("valid", "0")]) }
        let data = Data(String(cString: raw).utf8)
        g_free(raw)
        guard let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let name = body["event"] as? String else { return emit("dom-message", [("valid", "0")]) }
        let bool = { (key: String) in (body[key] as? Bool).map(flag) ?? "-" }
        if name == "input" {
            return emit("dom-input", [("inputType", types([body["inputType"] as? String ?? ""]))])
        }
        let calls = (body["calls"] as? [[String]]) ?? []
        var fields: [(String, String)] = ["transfer", "first", "second", "filesChecked", "refused"].map { ($0, bool($0)) }
        fields.append(("calls", String(calls.count)))
        for (index, seen) in calls.enumerated() { fields.append(("types\(index)", types(seen))) }
        emit("dom-\(types([name]))", fields)
    }

    /// The transfer guard to inject: instrumented, with its message handler registered, only while tracing.
    static func guardSource(_ source: String, world: String, manager: OpaquePointer) -> String {
        guard enabled else { return source }
        observeClipboard()
        guard let traced = instrument(source) else {
            emit("instrument-failed", [])
            return source
        }
        _ = webkit_user_content_manager_register_script_message_handler(manager, handler, world)
        return traced
    }

    /// The guard with reporting spliced in at fixed anchors, or nil when any anchor no longer matches once.
    /// Evaluation order and results are unchanged: the same `types` loop, the same short-circuit chain.
    nonisolated static func instrument(_ source: String) -> String? {
        let edits = [
            ("(() => {\n", "(() => {\n  let trace = null;\n"
                + "  const post = message => { try { window.webkit.messageHandlers.\(handler).postMessage(message); } catch (e) {} };\n"
                + "  window.addEventListener('input', event => post({event: 'input', inputType: String(event.inputType || '')}), true);\n"),
            ("const blocked = (data, paste) => {\n",
             "const blocked = (data, paste) => {\n  trace = {transfer: !!data, calls: []};\n"),
            ("for (const type of data.types) {\n", "const seen = []; trace.calls.push(seen);\n"
                + "for (const type of data.types) {\n  seen.push(String(type));\n"),
            ("return hasURI() || hasURI() || data.files.length > 0 || (paste && !hasType);\n",
             "const first = hasURI(); const second = first || hasURI();\n"
                + "const result = first || second || data.files.length > 0 || (paste && !hasType);\n"
                + "Object.assign(trace, {first, second: first ? null : second, filesChecked: !(first || second)});\n"
                + "return result;\n"),
            ("if (!blocked(transfer, name === 'paste')) return;\n",
             "const refused = blocked(transfer, name === 'paste');\n"
                + "if (name === 'paste') post(Object.assign({event: name}, trace || {transfer: false}, {refused}));\n"
                + "if (!refused) return;\n"),
        ]
        var result = source
        for (anchor, replacement) in edits {
            guard result.components(separatedBy: anchor).count == 2 else { return nil }
            result = result.replacingOccurrences(of: anchor, with: replacement)
        }
        return result
    }
}

private let onTracedClipboardChanged: @MainActor @convention(c) (OpaquePointer?, gpointer?) -> Void = { clipboard, _ in
    LinuxHtmlPasteTrace.emit("receiver-formats", [("reason", "changed")] + LinuxHtmlPasteTrace.formatFields(clipboard))
}

let onHtmlPasteTraceMessage: @MainActor @convention(c) (OpaquePointer?, OpaquePointer?, gpointer?) -> Void = { _, value, _ in
    LinuxHtmlPasteTrace.receive(value)
}
#endif
