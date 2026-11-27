import CGtk
import Foundation
import agtermCore

/// GDK reads complete on GTK's main context. Borrowed lists, strings and PNG bytes are copied in the callback.
@MainActor
enum LinuxPasteClipboard {
    nonisolated static func dropOffer(_ raw: String) -> PasteOffer {
        let lines = raw.split(whereSeparator: \.isNewline).map(String.init).filter { !$0.hasPrefix("#") }
        let urls = lines.compactMap { URL(string: $0) }.filter { $0.scheme != nil }
        if !urls.isEmpty, urls.count == lines.count { return PasteOffer(urls: urls) }
        return PasteOffer(text: ShellEscape.dropPayload(raw))
    }
    private final class Read {
        let reply: @MainActor (PasteOffer) -> Void
        init(_ reply: @escaping @MainActor (PasteOffer) -> Void) { self.reply = reply }
    }

    static func hasImageOnlyOffer() -> Bool {
        guard let clipboard = LinuxClipboardAdapter.gdkClipboard(for: GHOSTTY_CLIPBOARD_STANDARD),
              let formats = gdk_clipboard_get_formats(clipboard) else { return false }
        PasteDebug.log("route snapshot formats=[\(PasteDebug.formats(formats))]")
        let readable = gdk_content_formats_union_deserialize_gtypes(gdk_content_formats_ref(formats))
        defer { gdk_content_formats_unref(readable) }
        return gdk_content_formats_contain_gtype(readable, gdk_texture_get_type()) != 0
            && !LinuxClipboardAdapter.advertisesText(clipboard)
            && !LinuxClipboardAdapter.advertisesURIList(clipboard)
            && gdk_content_formats_contain_gtype(formats, gdk_file_list_get_type()) == 0
    }

    static func read(_ reply: @escaping @MainActor (PasteOffer) -> Void) {
        guard let clipboard = LinuxClipboardAdapter.gdkClipboard(for: GHOSTTY_CLIPBOARD_STANDARD),
              let formats = gdk_clipboard_get_formats(clipboard) else { return reply(PasteOffer()) }
        PasteDebug.log("offer snapshot formats=[\(PasteDebug.formats(formats))]")
        let read = Read(reply)
        if LinuxClipboardAdapter.advertisesURIList(clipboard) || gdk_content_formats_contain_gtype(formats, gdk_file_list_get_type()) != 0 {
            PasteDebug.log("offer FILE start")
            gdk_clipboard_read_value_async(clipboard, gdk_file_list_get_type(), 0, nil, { source, result, data in
                MainActor.assumeIsolated {
                    let read = Unmanaged<Read>.fromOpaque(data!).takeRetainedValue()
                    var error: UnsafeMutablePointer<GError>?
                    let value = gdk_clipboard_read_value_finish(OpaquePointer(source), result, &error)
                    PasteDebug.log("offer FILE end \(PasteDebug.result(value, error: error))")
                    read.reply(PasteOffer(urls: value.map(LinuxPasteClipboard.files) ?? []))
                }
            }, Unmanaged.passRetained(read).toOpaque())
        } else if LinuxClipboardAdapter.advertisesText(clipboard) {
            PasteDebug.log("offer TEXT start")
            gdk_clipboard_read_text_async(clipboard, nil, { source, result, data in
                MainActor.assumeIsolated {
                    let read = Unmanaged<Read>.fromOpaque(data!).takeRetainedValue()
                    var error: UnsafeMutablePointer<GError>?
                    let raw = gdk_clipboard_read_text_finish(OpaquePointer(source), result, &error)
                    PasteDebug.log("offer TEXT end \(PasteDebug.result(raw, error: error)) bytes=\(raw.map { strlen($0) } ?? 0)")
                    defer { g_free(raw) }
                    read.reply(PasteOffer(text: raw.map { String(cString: $0) }))
                }
            }, Unmanaged.passRetained(read).toOpaque())
        } else {
            PasteDebug.log("offer IMAGE start")
            gdk_clipboard_read_texture_async(clipboard, nil, { source, result, data in
                MainActor.assumeIsolated {
                    let read = Unmanaged<Read>.fromOpaque(data!).takeRetainedValue()
                    var error: UnsafeMutablePointer<GError>?
                    let texture = gdk_clipboard_read_texture_finish(OpaquePointer(source), result, &error)
                    PasteDebug.log("offer IMAGE end \(PasteDebug.result(texture, error: error))")
                    defer { if let texture { g_object_unref(UnsafeMutableRawPointer(texture)) } }
                    read.reply(PasteOffer(image: texture.flatMap(LinuxPasteClipboard.png)))
                }
            }, Unmanaged.passRetained(read).toOpaque())
        }
    }

    static func png(_ texture: OpaquePointer) -> Data? {
        guard let bytes = gdk_texture_save_to_png_bytes(texture) else { return nil }
        defer { g_bytes_unref(bytes) }
        var size: gsize = 0
        guard let pointer = g_bytes_get_data(bytes, &size) else { return nil }
        return Data(bytes: pointer, count: Int(size))
    }

    static func files(_ value: UnsafePointer<GValue>) -> [URL] {
        guard let boxed = g_value_get_boxed(value), let list = gdk_file_list_get_files(OpaquePointer(boxed)) else { return [] }
        // GDK transfers the list container. The GFiles remain borrowed.
        defer { g_slist_free(list) }
        var urls: [URL] = []
        var node: UnsafeMutablePointer<GSList>? = list
        while let current = node {
            if let pointer = current.pointee.data, let raw = g_file_get_uri(OpaquePointer(pointer)) {
                if let url = URL(string: String(cString: raw)) { urls.append(url) }
                g_free(raw)
            }
            node = current.pointee.next
        }
        return urls
    }
}
