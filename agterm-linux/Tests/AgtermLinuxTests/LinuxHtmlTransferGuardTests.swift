import CGtk
import CWebKit
import Testing
@testable import AgtermLinux

@MainActor
@Suite("HTML overlay transfer guard")
struct LinuxHtmlTransferGuardTests {
    @Test(arguments: [
        ("paste", "[]", true),
        ("drop", "[]", false),
        ("dragover", "[]", false),
        ("paste", "reads === 1 ? [] : ['text/uri-list']", true),
        ("paste", "['text/plain']", false),
    ])
    func guardChecksFakeDataTransfer(_ name: String, _ types: String, _ refused: Bool) throws {
        let context = try #require(jsc_context_new())
        defer { g_object_unref(UnsafeMutableRawPointer(context)) }
        let script = """
            const listeners = {};
            const window = { addEventListener(name, listener) { listeners[name] = listener; } };
            \(LinuxHtmlOverlayPage.transferGuard)
            let reads = 0;
            const data = { get types() { ++reads; return \(types); }, files: [], dropEffect: 'copy' };
            const event = {
                \(name == "paste" ? "clipboardData" : "dataTransfer"): data,
                prevented: false, stopped: false,
                preventDefault() { this.prevented = true; },
                stopImmediatePropagation() { this.stopped = true; }
            };
            listeners['\(name)'](event);
            reads === 2 && event.prevented === \(refused) && event.stopped === \(refused) &&
                data.dropEffect === 'copy';
            """
        let result = try #require(script.withCString { jsc_context_evaluate(context, $0, -1) })
        defer { g_object_unref(UnsafeMutableRawPointer(result)) }
        #expect(jsc_context_get_exception(context) == nil)
        #expect(jsc_value_to_boolean(result) != 0)
    }

    @Test func menuRechecksTheOfferAtActivationForBothCommands() throws {
        let menu = try #require(webkit_context_menu_new())
        defer { g_object_unref(UnsafeMutableRawPointer(menu)) }
        for stock in [WEBKIT_CONTEXT_MENU_ACTION_PASTE, WEBKIT_CONTEXT_MENU_ACTION_PASTE_AS_PLAIN_TEXT] {
            webkit_context_menu_append(menu, webkit_context_menu_item_new_from_stock_action(stock))
        }
        var offer = LinuxHtmlClipboardPaste.Offer.other
        var commands: [LinuxHtmlClipboardPaste.Command] = []
        LinuxHtmlPasteMenu.replaceStockItems(in: menu, offer: { offer }, paste: { commands.append($0) })
        for index: UInt32 in 0..<2 {
            let item = try #require(webkit_context_menu_get_item_at_position(menu, index))
            #expect(webkit_context_menu_item_get_stock_action(item) != WEBKIT_CONTEXT_MENU_ACTION_PASTE)
            #expect(webkit_context_menu_item_get_stock_action(item) != WEBKIT_CONTEXT_MENU_ACTION_PASTE_AS_PLAIN_TEXT)
            let action = try #require(webkit_context_menu_item_get_gaction(item))
            offer = .fileTransfer
            g_action_activate(action, nil)
            offer = .unresolved
            g_action_activate(action, nil)
            offer = .other
            g_action_activate(action, nil)
        }
        #expect(commands == [.paste, .plainText])
    }
}
