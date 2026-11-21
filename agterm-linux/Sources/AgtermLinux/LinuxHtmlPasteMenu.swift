import CGtk
import CWebKit

@MainActor
extension LinuxHtmlOverlayPage {
    func guardPasteMenuItems(_ menu: OpaquePointer) {
        LinuxHtmlPasteMenu.replaceStockItems(in: menu, offer: { [weak self] in
            guard let self, !closed, onScreen else { return .unresolved }
            return LinuxHtmlClipboardPaste.offer(clipboard.flatMap { gdk_clipboard_get_formats($0) })
        }, paste: { [weak self] in self?.executePaste($0) })
    }
}

@MainActor
enum LinuxHtmlPasteMenu {
    static func replaceStockItems(in menu: OpaquePointer,
                                  offer: @escaping @MainActor () -> LinuxHtmlClipboardPaste.Offer,
                                  paste: @escaping @MainActor (LinuxHtmlClipboardPaste.Command) -> Void) {
        // WebKit executes stock actions itself. Replace the two paste items so only our action can edit.
        for index in (0..<webkit_context_menu_get_n_items(menu)).reversed() {
            guard let original = webkit_context_menu_get_item_at_position(menu, index) else { continue }
            let command: LinuxHtmlClipboardPaste.Command
            let label: String
            switch webkit_context_menu_item_get_stock_action(original) {
            case WEBKIT_CONTEXT_MENU_ACTION_PASTE:
                command = .paste
                label = "_Paste"
            case WEBKIT_CONTEXT_MENU_ACTION_PASTE_AS_PLAIN_TEXT:
                command = .plainText
                label = "Paste As Plain _Text"
            default: continue
            }
            let enabled = webkit_context_menu_item_get_gaction(original).map { g_action_get_enabled($0) } ?? 0
            webkit_context_menu_remove(menu, original)
            guard let action = command.rawValue.withCString({ g_simple_action_new($0, nil) }) else { continue }
            defer { g_object_unref(RAW(action)) }
            g_simple_action_set_enabled(action, enabled)
            let activation = HtmlPasteMenuActivation {
                guard offer() == .other else { return }
                paste(command)
            }
            g_object_set_data_full(GOBJ(action), "agterm-html-paste", Unmanaged.passRetained(activation).toOpaque(),
                                   releaseHtmlPasteMenuActivation)
            connect(action, "activate", unsafeBitCast(onHtmlPasteMenuActivate, to: GCallback.self))
            let replacement = label.withCString {
                webkit_context_menu_item_new_from_gaction(action, g_dgettext("WebKitGTK-6.0", $0), nil)
            }
            webkit_context_menu_insert(menu, replacement, Int32(index))
        }
    }
}

private final class HtmlPasteMenuActivation {
    let run: LinuxHtmlClipboardPaste.Action
    init(_ run: @escaping LinuxHtmlClipboardPaste.Action) { self.run = run }
}

private let onHtmlPasteMenuActivate: @MainActor @convention(c) (OpaquePointer?, OpaquePointer?, gpointer?) -> Void = {
    action, _, _ in
    guard let data = g_object_get_data(GOBJ(action), "agterm-html-paste") else { return }
    Unmanaged<HtmlPasteMenuActivation>.fromOpaque(data).takeUnretainedValue().run()
}

private let releaseHtmlPasteMenuActivation: @convention(c) (gpointer?) -> Void = { data in
    if let data { Unmanaged<HtmlPasteMenuActivation>.fromOpaque(data).release() }
}
