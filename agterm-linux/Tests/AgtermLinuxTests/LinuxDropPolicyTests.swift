import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

/// Records what a drop asked of its surface. It has no typed-keystroke entry, mirroring `DropPasteTarget`.
@MainActor
private final class RecordingDropTarget: DropPasteTarget {
    enum Call: Equatable {
        case focus(supersedingPopoverCapture: Bool)
        case paste(String)
    }
    private(set) var calls: [Call] = []

    func grabFocus(supersedingPopoverCapture: Bool) { calls.append(.focus(supersedingPopoverCapture: supersedingPopoverCapture)) }
    func insertPasted(text: String) { calls.append(.paste(text)) }
}

@MainActor
@Suite("Linux drops are delivered as paste")
struct LinuxDropPolicyTests {
    @Test("a multi-line text drop is one paste, not typed lines")
    func multiLineTextDropIsPaste() {
        let payload = "echo first\necho second"
        // the typed session.type path would turn the line break into a Return key: the hazard routed around.
        #expect(KeystrokeSegments.split(payload).contains(.returnKey))

        let target = RecordingDropTarget()
        #expect(LinuxDropPolicy.deliverText(payload, to: target))
        #expect(target.calls == [.focus(supersedingPopoverCapture: true), .paste(payload)])
    }

    @Test("a uri-list drop pastes its escaped POSIX paths")
    func uriListDropIsPaste() {
        let target = RecordingDropTarget()
        #expect(LinuxDropPolicy.deliverText("file:///tmp/one%20two\nfile:///tmp/three", to: target))
        #expect(target.calls == [.focus(supersedingPopoverCapture: true), .paste(#"/tmp/one\ two /tmp/three"#)])
    }

    @Test("a file drop pastes the space-joined escaped paths")
    func fileDropIsPaste() {
        let target = RecordingDropTarget()
        let parts = [ShellEscape.path("/tmp/a b"), "", ShellEscape.path("/tmp/c")]
        #expect(LinuxDropPolicy.deliverFiles(parts, to: target))
        #expect(target.calls == [.focus(supersedingPopoverCapture: true), .paste(#"/tmp/a\ b /tmp/c"#)])
    }

    @Test("sidebar ids and empty drops are refused without touching the surface")
    func refusedDrops() {
        let target = RecordingDropTarget()
        #expect(!LinuxDropPolicy.deliverText(UUID().uuidString, to: target))
        #expect(!LinuxDropPolicy.deliverText("w:" + UUID().uuidString, to: target))
        #expect(!LinuxDropPolicy.deliverText("", to: target))
        #expect(!LinuxDropPolicy.deliverFiles(["", ""], to: target))
        #expect(!LinuxDropPolicy.deliverFiles([], to: target))
        #expect(target.calls.isEmpty)
    }
}
