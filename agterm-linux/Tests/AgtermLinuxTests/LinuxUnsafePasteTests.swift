import CGtk
import Foundation
import Testing
@testable import AgtermLinux

/// Records what the user's answer asked of libghostty's pending paste request.
@MainActor
private final class RecordingPasteTarget: UnsafePasteTarget {
    enum Call: Equatable {
        case complete(String, UnsafeMutableRawPointer?)
        case discard(UnsafeMutableRawPointer?)
    }
    private(set) var calls: [Call] = []

    func completeUnsafePaste(_ text: String, request: UnsafeMutableRawPointer?) { calls.append(.complete(text, request)) }
    func discardUnsafePaste(request: UnsafeMutableRawPointer?) { calls.append(.discard(request)) }
}

/// Stands in for the dialog: remembers what it was shown and lets the test answer.
@MainActor
private final class DialogLog {
    private(set) var shown: [String] = []
    private var answers: [UnsafePasteController.Answer] = []

    func record(_ text: String, _ requester: AnyObject, _ answer: @escaping UnsafePasteController.Answer) {
        shown.append(text)
        answers.append(answer)
    }

    func answer(_ index: Int, _ response: String) { answers[index](response) }
}

nonisolated(unsafe) private let request = UnsafeMutableRawPointer(bitPattern: 0x5EED)

@MainActor
@Suite("Linux asks before an unsafe multi-line paste")
struct LinuxUnsafePasteTests {
    @Test("a paste libghostty flags as unsafe asks the user; OSC 52 reads keep their consent prompt")
    func confirmRoutes() {
        #expect(LinuxClipboardConfirmPolicy.route(for: GHOSTTY_CLIPBOARD_REQUEST_PASTE) == .unsafePaste)
        #expect(LinuxClipboardConfirmPolicy.route(for: GHOSTTY_CLIPBOARD_REQUEST_OSC_52_READ) == .clipboardReadConsent)
        #expect(LinuxClipboardConfirmPolicy.route(for: GHOSTTY_CLIPBOARD_REQUEST_OSC_52_WRITE) == .confirmAsIs)
    }

    @Test("Paste delivers the full text to the pending request")
    func pasteDelivers() {
        let dialog = DialogLog()
        let controller = UnsafePasteController(present: dialog.record)
        let target = RecordingPasteTarget()
        var done = 0
        controller.confirm("rm -rf build\nmake\n", request: request, on: target) { done += 1 }
        #expect(dialog.shown == ["rm -rf build\nmake\n"])
        #expect(target.calls.isEmpty)

        dialog.answer(0, UnsafePasteController.pasteResponse)
        #expect(target.calls == [.complete("rm -rf build\nmake\n", request)])
        #expect(done == 1)
    }

    @Test("Cancel, closing the dialog, or any other response delivers nothing")
    func cancelDeliversNothing() {
        for response in [UnsafePasteController.cancelResponse, "close", ""] {
            let dialog = DialogLog()
            let controller = UnsafePasteController(present: dialog.record)
            let target = RecordingPasteTarget()
            controller.confirm("echo one\necho two", request: request, on: target)
            dialog.answer(0, response)
            #expect(target.calls == [.discard(request)], "response \(response)")
        }
    }

    @Test("only the first answer counts: a destroy after Paste does not also cancel")
    func firstAnswerWins() {
        let dialog = DialogLog()
        let controller = UnsafePasteController(present: dialog.record)
        let target = RecordingPasteTarget()
        var done = 0
        controller.confirm("a\nb", request: request, on: target) { done += 1 }
        dialog.answer(0, UnsafePasteController.pasteResponse)
        dialog.answer(0, UnsafePasteController.cancelResponse)
        #expect(target.calls == [.complete("a\nb", request)])
        #expect(done == 1)
    }

    @Test("each unsafe paste is its own question, and an earlier Paste is not remembered")
    func noRememberedAnswer() {
        let dialog = DialogLog()
        let controller = UnsafePasteController(present: dialog.record)
        let target = RecordingPasteTarget()
        controller.confirm("first\n", request: request, on: target)
        dialog.answer(0, UnsafePasteController.pasteResponse)
        controller.confirm("second\n", request: request, on: target)
        #expect(dialog.shown == ["first\n", "second\n"])
        #expect(target.calls == [.complete("first\n", request)])
        dialog.answer(1, UnsafePasteController.cancelResponse)
        #expect(target.calls == [.complete("first\n", request), .discard(request)])
    }

    @Test("an armed drop is taken once, and only by its own surface's clipboard read")
    func armedDropOwnership() {
        let armed = ArmedDropPaste()
        let owner = UnsafeMutableRawPointer(bitPattern: 0xA1)!
        let other = UnsafeMutableRawPointer(bitPattern: 0xB2)!
        #expect(armed.take(owner: owner) == nil)

        armed.arm("echo dropped\n", owner: owner)
        #expect(armed.take(owner: other) == nil)
        #expect(armed.take(owner: nil) == nil)
        #expect(armed.take(owner: owner) == "echo dropped\n")
        #expect(armed.take(owner: owner) == nil)
    }

    @Test("the dialog preview is capped; short text is shown whole")
    func previewCap() {
        #expect(UnsafePasteController.preview("a\nb") == "a\nb")
        let long = String(repeating: "x", count: 2500)
        let shown = UnsafePasteController.preview(long)
        #expect(shown.hasPrefix(String(repeating: "x", count: 2000)))
        #expect(shown.count == 2002)
    }
}
