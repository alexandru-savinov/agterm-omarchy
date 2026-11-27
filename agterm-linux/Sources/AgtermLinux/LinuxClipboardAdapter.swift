import CGtk
import Foundation
import agtermCore

struct ClipboardConfirmation: Equatable, Sendable {
    let items: [ClipboardItem]
    let available: [String]
}

/// The C side of the clipboard callbacks: copies libghostty's borrowed payloads into Swift values, packs
/// completions, and owns the GDK providers. `LinuxClipboardPolicy` makes the decisions.
enum LinuxClipboardAdapter {
    typealias Completion = (ghostty_surface_t, UnsafePointer<ghostty_clipboard_complete_s>, UnsafeMutableRawPointer?) -> Void
    typealias Denial = (ghostty_surface_t, UnsafeMutableRawPointer?) -> Void

    private static func engineCompletion(_ surface: ghostty_surface_t, _ complete: UnsafePointer<ghostty_clipboard_complete_s>,
                                         _ state: UnsafeMutableRawPointer?) {
        ghostty_surface_complete_clipboard_request(surface, complete, state)
    }

    private static func engineDenial(_ surface: ghostty_surface_t, _ state: UnsafeMutableRawPointer?) {
        ghostty_surface_deny_clipboard_request(surface, state)
    }

    // MARK: - Copying borrowed payloads

    static func copyMimes(_ mimes: UnsafePointer<UnsafePointer<CChar>?>?, count: Int) -> [String] {
        UnsafeBufferPointer(start: mimes, count: mimes == nil ? 0 : count).compactMap { $0.map { String(cString: $0) } }
    }

    /// A nil `data` is a zero-length item, never a dropped one: core reports `DONE` to the program right
    /// after the write callback, so a dropped representation is a false success.
    static func copyItems(_ items: UnsafePointer<ghostty_clipboard_content_s>?, count: Int) -> [ClipboardItem] {
        UnsafeBufferPointer(start: items, count: items == nil ? 0 : count).compactMap { item in
            guard let mime = item.mime else { return nil }
            let data = item.data.map { Data(bytes: $0, count: item.len) } ?? Data()
            return ClipboardItem(mime: String(cString: mime), data: data)
        }
    }

    static func copyConfirmation(_ confirm: ghostty_clipboard_confirm_s) -> ClipboardConfirmation {
        ClipboardConfirmation(items: copyItems(confirm.contents, count: confirm.contents_len),
                              available: copyMimes(confirm.available, count: confirm.available_len))
    }

    /// Nil for a kind this revision does not know: the caller denies it, so a Ghostty bump that adds a kind
    /// fails closed instead of completing unprompted as a paste.
    static func requestKind(_ request: ghostty_clipboard_request_e) -> ClipboardRequestKind? {
        switch request {
        case GHOSTTY_CLIPBOARD_REQUEST_PASTE: .paste
        case GHOSTTY_CLIPBOARD_REQUEST_OSC_52_READ: .osc52Read
        case GHOSTTY_CLIPBOARD_REQUEST_OSC_52_WRITE: .osc52Write
        case GHOSTTY_CLIPBOARD_REQUEST_KITTY_READ: .kittyRead
        case GHOSTTY_CLIPBOARD_REQUEST_KITTY_WRITE: .kittyWrite
        case GHOSTTY_CLIPBOARD_REQUEST_LIST: .list
        default: nil
        }
    }

    // MARK: - Completing requests

    /// Everything handed to `deliver` is freed when it returns, which the engine's borrow allows.
    static func complete(_ surface: ghostty_surface_t, items: [ClipboardItem], available: [String],
                         state: UnsafeMutableRawPointer?, confirmed: Bool = false,
                         using deliver: Completion = engineCompletion) {
        var strings: [UnsafeMutablePointer<CChar>] = []
        var buffers: [UnsafeMutableRawPointer] = []
        defer {
            strings.forEach { free($0) }
            buffers.forEach { $0.deallocate() }
        }

        var contents: [ghostty_clipboard_content_s] = []
        for item in items {
            guard let mime = strdup(item.mime) else { continue }
            strings.append(mime)
            // the engine slices `data` as a non-null pointer, zero-length items included
            let buffer = UnsafeMutableRawPointer.allocate(byteCount: max(item.data.count, 1), alignment: 1)
            buffers.append(buffer)
            item.data.copyBytes(to: buffer.assumingMemoryBound(to: UInt8.self), count: item.data.count)
            contents.append(ghostty_clipboard_content_s(
                mime: mime, data: buffer.assumingMemoryBound(to: CChar.self), len: item.data.count))
        }

        var mimes: [UnsafePointer<CChar>?] = []
        for mime in available {
            guard let copy = strdup(mime) else { continue }
            strings.append(copy)
            mimes.append(UnsafePointer(copy))
        }

        contents.withUnsafeBufferPointer { contentsBuffer in
            mimes.withUnsafeBufferPointer { mimesBuffer in
                var completion = ghostty_clipboard_complete_s(
                    contents: contentsBuffer.baseAddress, contents_len: contentsBuffer.count,
                    available: mimesBuffer.baseAddress, available_len: mimesBuffer.count,
                    confirmed: confirmed, remember: false)
                deliver(surface, &completion, state)
            }
        }
    }

    /// The prompt's answer. `liveSurface` is read only when it arrives, because a pointer taken earlier
    /// dangles once the pane closes under the prompt. A closed pane gets neither call: the request state
    /// then leaks in the engine, and no C API frees it without the surface.
    static func confirmationAnswer(liveSurface: @escaping () -> ghostty_surface_t?, payload: ClipboardConfirmation,
                                   state: UnsafeMutableRawPointer?, complete deliver: @escaping Completion = engineCompletion,
                                   deny: @escaping Denial = engineDenial) -> (Bool) -> Void {
        { allowed in
            guard let surface = liveSurface() else { return }
            guard allowed else {
                deny(surface, state)
                return
            }
            complete(surface, items: payload.items, available: payload.available, state: state, confirmed: true, using: deliver)
        }
    }

    // MARK: - GDK

    /// `SELECTION` is copy-on-select and `PRIMARY` is OSC 52 `p`; GDK has one primary clipboard for both.
    static func gdkClipboard(for loc: ghostty_clipboard_e) -> OpaquePointer? {
        guard let display = gdk_display_get_default() else { return nil }
        if loc == GHOSTTY_CLIPBOARD_SELECTION || loc == GHOSTTY_CLIPBOARD_PRIMARY {
            return gdk_display_get_primary_clipboard(display)
        }
        return gdk_display_get_clipboard(display)
    }

    /// What `gdk_clipboard_read_text_async` would find, without fetching content. Asking for `text/plain`
    /// alone would miss a clipboard offering only a charset form or an X11 atom.
    static func advertisesText(_ clipboard: OpaquePointer) -> Bool {
        guard let formats = gdk_clipboard_get_formats(clipboard) else { return false }
        // the union consumes the reference it is given and returns a new one
        let readable = gdk_content_formats_union_deserialize_gtypes(gdk_content_formats_ref(formats))
        defer { gdk_content_formats_unref(readable) }
        return gdk_content_formats_contain_gtype(readable, GType(64) /* G_TYPE_STRING */) != 0
    }

    static func advertisesURIList(_ clipboard: OpaquePointer) -> Bool {
        guard let formats = gdk_clipboard_get_formats(clipboard) else { return false }
        return LinuxClipboardPolicy.uriListMime.withCString { gdk_content_formats_contain_mime_type(formats, $0) != 0 }
    }

    /// `read` owns the surface reference from here and always completes on the main thread.
    ///
    /// A paste asks the owner for its `text/uri-list` first, and the text form only when it refuses or the
    /// list is not one of files. A file list's text form is newline-separated paths (GDK's
    /// `file_text_serializer`), which libghostty would hold as an unsafe multi-line paste. The cached offer
    /// formats are not consulted: on X11 they can still describe the previous owner when a paste follows a
    /// copy at once, while a read goes to the current owner. Any other request reads the text form only.
    @MainActor static func readText(_ read: ClipboardTextRead) {
        guard read.begin() else { return }
        guard LinuxClipboardPolicy.readsFileList(read.kind) else {
            read.fileOffer = advertisesURIList(read.clipboard)
            readPlainText(read)
            return
        }
        LinuxClipboardPolicy.uriListMime.withCString { mime in
            // GDK interns the MIME types before it returns, so the borrowed strings may end with this call.
            var mimes: [UnsafePointer<CChar>?] = [mime, nil]
            PasteDebug.log("engine URI start")
            gdk_clipboard_read_async(
                read.clipboard, &mimes, 0 /* G_PRIORITY_DEFAULT */, read.cancellable,
                { source, result, data in
                    let read = Unmanaged<ClipboardTextRead>.fromOpaque(data!).takeRetainedValue()
                    MainActor.assumeIsolated {
                        var error: UnsafeMutablePointer<GError>?
                        let input = source.flatMap { gdk_clipboard_read_finish(OpaquePointer($0), result, nil, &error) }
                        PasteDebug.log("engine URI end \(PasteDebug.result(input, error: error))")
                        defer { if let input { g_object_unref(input) } }
                        guard read.canContinue() else { return }
                        guard let input else { return LinuxClipboardAdapter.readPlainText(read) }
                        // Use the current owner's response for the remote-file guard, even if decoding fails.
                        read.fileOffer = true
                        LinuxClipboardAdapter.readPaths(read, input: input)
                    }
                },
                Unmanaged.passRetained(read).toOpaque()
            )
        }
    }

    @MainActor private static func readPaths(_ read: ClipboardTextRead, input: UnsafeMutablePointer<GInputStream>) {
        // The splice holds its own references to both streams until it completes.
        let output = g_memory_output_stream_new_resizable()
        PasteDebug.log("engine URI splice start")
        g_output_stream_splice_async(
            output, input,
            GOutputStreamSpliceFlags(rawValue: (1 << 0) | (1 << 1)), // CLOSE_SOURCE | CLOSE_TARGET
            0 /* G_PRIORITY_DEFAULT */, read.cancellable,
            { source, result, data in
                let read = Unmanaged<ClipboardTextRead>.fromOpaque(data!).takeRetainedValue()
                MainActor.assumeIsolated {
                    let paths = source.flatMap { LinuxClipboardAdapter.splicePaths($0, result: result) }
                    PasteDebug.log("engine URI splice end bytes=\(paths?.utf8.count ?? 0)")
                    guard read.canContinue() else { return }
                    guard let paths else { return LinuxClipboardAdapter.readPlainText(read) }
                    read.finish(text: paths)
                }
            },
            Unmanaged.passRetained(read).toOpaque()
        )
        g_object_unref(output)
    }

    private static func splicePaths(_ source: UnsafeMutablePointer<GObject>, result: OpaquePointer?) -> String? {
        let output: UnsafeMutablePointer<GOutputStream> = UnsafeMutablePointer(OpaquePointer(source))
        guard g_output_stream_splice_finish(output, result, nil) >= 0 else { return nil }
        let memory: UnsafeMutablePointer<GMemoryOutputStream> = UnsafeMutablePointer(OpaquePointer(source))
        let size = Int(g_memory_output_stream_get_data_size(memory))
        let bytes = g_memory_output_stream_get_data(memory)
        return LinuxClipboardPolicy.pastedPaths(uriList: bytes.map { Data(bytes: $0, count: size) } ?? Data())
    }

    /// The text form, as GDK converts it, unchanged: an owner that offers no file list offers literal text.
    @MainActor private static func readPlainText(_ read: ClipboardTextRead) {
        PasteDebug.log("engine TEXT start kind=\(String(describing: read.kind))")
        gdk_clipboard_read_text_async(
            read.clipboard, read.cancellable,
            { source, result, data in
                let read = Unmanaged<ClipboardTextRead>.fromOpaque(data!).takeRetainedValue()
                MainActor.assumeIsolated {
                    var error: UnsafeMutablePointer<GError>?
                    let raw = source.flatMap { gdk_clipboard_read_text_finish(OpaquePointer($0), result, &error) }
                    PasteDebug.log("engine TEXT end \(PasteDebug.result(raw, error: error)) bytes=\(raw.map { strlen($0) } ?? 0)")
                    defer { g_free(raw) }
                    guard read.canContinue() else { return }
                    let text = raw.map { String(cString: $0) }
                    guard read.canContinue() else { return }
                    // X11 can return empty text for an owner that only offers an image.
                    if LinuxClipboardPolicy.readsFileList(read.kind), text?.isEmpty ?? true {
                        LinuxClipboardAdapter.readImage(read)
                    } else {
                        read.finish(text: text)
                    }
                }
            },
            Unmanaged.passRetained(read).toOpaque()
        )
    }

    /// A paste with no usable file paths or decoded text asks the current owner for an image, staged like an
    /// image-only paste. `pasteClipboard` routes image-only offers by GDK's cached formats, and on X11 those are
    /// still empty for a few milliseconds after another client claims the clipboard, so an image copied just
    /// before the paste arrives here. Only a local pane stages: the origin copy belongs to `pasteOffer`.
    @MainActor private static func readImage(_ read: ClipboardTextRead) {
        guard read.retained.surface.pasteOriginHost == nil else { return read.finish(text: nil) }
        PasteDebug.log("engine IMAGE start")
        gdk_clipboard_read_texture_async(
            read.clipboard, read.cancellable,
            { source, result, data in
                let read = Unmanaged<ClipboardTextRead>.fromOpaque(data!).takeRetainedValue()
                MainActor.assumeIsolated {
                    var error: UnsafeMutablePointer<GError>?
                    let texture = source.flatMap { gdk_clipboard_read_texture_finish(OpaquePointer($0), result, &error) }
                    PasteDebug.log("engine IMAGE end \(PasteDebug.result(texture, error: error))")
                    defer { if let texture { g_object_unref(UnsafeMutableRawPointer(texture)) } }
                    guard read.canContinue() else { return }
                    let png = texture.flatMap(LinuxPasteClipboard.png)
                    guard read.canContinue() else { return }
                    guard let png else { return read.finish(text: nil) }
                    let surface = read.retained.surface
                    surface.controller?.showToast("Saving pasted image…")
                    PasteDebug.log("engine stage start bytes=\(png.count)")
                    DispatchQueue.global(qos: .userInitiated).async { @Sendable in
                        let staged = LinuxPasteTransfer.text(PasteOffer(image: png), host: nil)
                        runOnMain { MainActor.assumeIsolated {
                            guard read.canContinue() else { return }
                            PasteDebug.log("engine stage end success=\((try? staged.get()) != nil)")
                            switch staged {
                            case .success(let path): read.finish(text: path)
                            case .failure(let failure):
                                read.retained.surface.controller?.showToast(failure.reason)
                                read.finish(text: nil, error: failure.reason)
                            }
                        } }
                    }
                }
            },
            Unmanaged.passRetained(read).toOpaque()
        )
    }

    /// GDK ownership: the bytes provider takes its own `GBytes` reference, the union takes over its child
    /// providers and copies the array, and `gdk_clipboard_set_content` borrows the provider.
    static func setClipboard(_ items: [ClipboardItem], loc: ghostty_clipboard_e) {
        guard let clipboard = gdkClipboard(for: loc), let provider = contentProvider(for: items) else { return }
        defer { g_object_unref(provider) }
        if gdk_clipboard_set_content(clipboard, provider) == 0 {
            LinuxStructuredLogger(category: "Clipboard").notice("gdk_clipboard_set_content refused \(items.count) item(s)")
        }
    }

    private static func contentProvider(for items: [ClipboardItem]) -> UnsafeMutablePointer<GdkContentProvider>? {
        var providers = LinuxClipboardPolicy.gdkRepresentations(items).map(bytesProvider)
        guard providers.count > 1 else { return providers.first ?? nil }
        return gdk_content_provider_new_union(&providers, gsize(providers.count))
    }

    private static func bytesProvider(_ item: ClipboardItem) -> UnsafeMutablePointer<GdkContentProvider>? {
        let bytes = item.data.withUnsafeBytes { g_bytes_new($0.baseAddress, gsize($0.count)) }
        defer { g_bytes_unref(bytes) }
        return item.mime.withCString { gdk_content_provider_new_for_bytes($0, bytes) }
    }
}

/// A started text read, carried through GDK's async callbacks. It holds a reference on the clipboard,
/// which the read may need again for the text form after the uri-list is refused.
final class ClipboardTextRead: @unchecked Sendable {
    let clipboard: OpaquePointer
    let state: UnsafeMutableRawPointer?
    let retained: RetainedGhosttySurface
    let list: Bool
    /// Taken as the read starts: completing the request invalidates `state`.
    let kind: ClipboardRequestKind?
    @MainActor fileprivate var fileOffer = false
    @MainActor fileprivate private(set) var cancellable: UnsafeMutablePointer<GCancellable>?
    @MainActor private var pasteDone: EnginePasteCompletion?
    @MainActor private var deadline: DispatchTime?
    @MainActor private var cancelTimer: (@MainActor () -> Void)?
    @MainActor private var origin: String?
    @MainActor private var finished = false

    init(clipboard: OpaquePointer, state: UnsafeMutableRawPointer?, retained: RetainedGhosttySurface, list: Bool) {
        g_object_ref(RAW(clipboard))
        self.clipboard = clipboard
        self.state = state
        self.retained = retained
        self.list = list
        kind = state.flatMap { LinuxClipboardAdapter.requestKind(ghostty_clipboard_request_type($0)) }
    }

    deinit { g_object_unref(RAW(clipboard)) }

    @MainActor fileprivate func begin() -> Bool {
        let surface = retained.surface
        pasteDone = EnginePasteCompletion.take(for: surface)
        guard kind == .paste else { return true }
        if let refusal = surface.pasteRefusal {
            finish(text: nil, error: refusal)
            return false
        }
        origin = surface.pasteOriginHost
        surface.pasteCopying = true
        let now = DispatchTime.now()
        let deadline = min(now + 10, pasteDone?.deadline ?? .distantFuture)
        self.deadline = deadline
        guard deadline > now else {
            finish(text: nil, error: LinuxPasteTransfer.timeoutReason)
            return false
        }
        cancellable = g_cancellable_new()
        let delay = Double(deadline.uptimeNanoseconds - now.uptimeNanoseconds) / 1_000_000_000
        cancelTimer = MainTimer.schedule(after: delay) {
            PasteDebug.log("engine deadline fired")
            self.finish(text: nil, error: LinuxPasteTransfer.timeoutReason)
        }
        return true
    }

    /// Async results may arrive even after cancellation. Check before accessing the retained surface.
    @MainActor fileprivate func canContinue() -> Bool {
        guard !finished else { return false }
        guard let deadline else { return true }
        if DispatchTime.now() >= deadline {
            PasteDebug.log("engine callback observed deadline")
            finish(text: nil, error: LinuxPasteTransfer.timeoutReason)
            return false
        }
        if let refusal = retained.surface.pasteDestinationRefusal(origin: origin) {
            finish(text: nil, error: refusal)
            return false
        }
        return true
    }

    /// Rereads the live surface, which a teardown during the GDK read frees. The orphaned request state
    /// then leaks: no C API frees it without the surface.
    @MainActor fileprivate func finish(text: String?, error: String? = nil) {
        guard !finished else { return }
        finished = true
        cancelTimer?()
        cancelTimer = nil
        if let cancellable {
            if error != nil { g_cancellable_cancel(cancellable) }
            g_object_unref(cancellable)
            self.cancellable = nil
        }
        let done = pasteDone?.finish
        pasteDone = nil
        if deadline != nil { retained.surface.pasteCopying = false }
        defer { retained.release() }
        guard let surface = retained.surface.surface else { done?("session closed"); return }
        if let error {
            ghostty_surface_deny_clipboard_request(surface, state)
            done?(error)
            return
        }
        // Engine-specific paste bindings do no transfer. Never insert this host's file paths on the origin.
        let remoteFiles = fileOffer && retained.surface.pasteOriginHost != nil
        PasteDebug.log("engine submit bytes=\(text?.utf8.count ?? 0) remoteFiles=\(remoteFiles)")
        let completion = LinuxClipboardPolicy.textCompletion(text: remoteFiles ? nil : text, list: list)
        LinuxClipboardAdapter.complete(surface, items: completion.items, available: completion.available, state: state)
        done?(nil)
    }
}
