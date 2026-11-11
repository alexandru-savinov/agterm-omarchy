import Foundation
import CGtk
import Testing
import agtermCore
@testable import AgtermLinux

@MainActor
@Suite("Real GTK clipboard dialog disposal",
       .enabled(if: ProcessInfo.processInfo.environment["AGTERM_GTK_TESTS"] == "1"))
struct ClipboardPromptGTKTests {
    @Test("finalizing an unanswered dialog denies every waiter without emitting a response",
          arguments: [ClipboardAccess.read, .write])
    func unansweredDialogDisposal(access: ClipboardAccess) throws {
        try #require(gtk_init_check() != 0)
        adw_init()
        var keys: [ClipboardPromptController.PromptKey] = []
        let controller = ClipboardPromptController { key, _ in keys.append(key) }
        let requester = NSObject()
        var answers: [Bool] = []
        controller.request(access, requester: requester) { answers.append($0) }
        controller.request(access, requester: requester) { answers.append($0) }
        #expect(keys.count == 1)

        let dialog = OpaquePointer(try #require(adw_alert_dialog_new("Consent fixture", nil)))
        _ = g_object_ref_sink(RAW(dialog))
        let weakDialog = UnsafeMutablePointer<gpointer?>.allocate(capacity: 1)
        weakDialog.initialize(to: RAW(dialog))
        defer { weakDialog.deinitialize(count: 1); weakDialog.deallocate() }
        g_object_add_weak_pointer(GOBJ(dialog), weakDialog)

        let responses = ResponseLog()
        "response".withCString {
            _ = g_signal_connect_data(RAW(dialog), $0, unsafeBitCast(recordResponse, to: GCallback.self),
                                      Unmanaged.passUnretained(responses).toOpaque(), nil, GConnectFlags(rawValue: 0))
        }
        var context: ClipboardPromptController.ResponseContext? = .init(key: keys[0], controller: controller)
        weak var observedContext = context
        ClipboardPromptController.connectResponse(dialog, context: context!)
        context = nil
        #expect(observedContext != nil)
        #expect(answers.isEmpty)

        // Final unref, not close/respond: GObject must destroy the production signal closure itself.
        withExtendedLifetime(responses) { g_object_unref(RAW(dialog)) }
        #expect(weakDialog.pointee == nil)
        #expect(responses.count == 0)
        #expect(observedContext == nil)
        #expect(answers == [false, false])

        controller.request(access, requester: requester) { answers.append($0) }
        #expect(keys.count == 2)
        #expect(answers == [false, false])
        controller.resolve(keys[1], response: "allow")
        #expect(answers == [false, false, true])
    }
}

@MainActor
private final class ResponseLog {
    var count = 0
}

private let recordResponse: @MainActor @convention(c) (OpaquePointer?, UnsafePointer<CChar>?, gpointer?) -> Void = { _, _, data in
    guard let data else { return }
    MainActor.assumeIsolated {
        Unmanaged<ResponseLog>.fromOpaque(data).takeUnretainedValue().count += 1
    }
}
