import Foundation
import CGtk
import Testing
import agtermCore
@testable import AgtermLinux

@MainActor
@Suite("Linux clipboard consent coalescing and requester lifetime")
struct ClipboardPromptControllerTests {
    @Test("signal destruction denies unresolved coalesced waiters and releases the context")
    func destroyedPrompt() {
        let prompts = PromptLog()
        let controller = ClipboardPromptController(present: prompts.record)
        let requester = Requester()
        var answers: [Bool] = []
        controller.request(.write, requester: requester) { answers.append($0) }
        controller.request(.write, requester: requester) { answers.append($0) }
        var context: ClipboardPromptController.ResponseContext? = .init(key: prompts.keys[0], controller: controller)
        weak var observed = context
        let data = Unmanaged.passRetained(context!).toOpaque()
        context = nil
        onClipboardPromptDestroyed(data, nil)
        #expect(answers == [false, false])
        #expect(observed == nil)
        controller.request(.write, requester: requester) { answers.append($0) }
        #expect(prompts.keys.count == 2)
        #expect(answers.count == 2)
    }

    @Test("an answered dialog cannot resolve a replacement prompt when its signal is destroyed")
    func answeredPromptDestroyed() {
        let prompts = PromptLog()
        let controller = ClipboardPromptController(present: prompts.record)
        let requester = Requester()
        var answers: [Bool] = []
        controller.request(.write, requester: requester) { answers.append($0) }
        let context = ClipboardPromptController.ResponseContext(key: prompts.keys[0], controller: controller)
        let data = Unmanaged.passRetained(context).toOpaque()
        "allow".withCString { onClipboardPromptResponse(nil, $0, data) }
        controller.request(.write, requester: requester) { answers.append($0) }
        "always-allow".withCString { onClipboardPromptResponse(nil, $0, data) }
        onClipboardPromptDestroyed(data, nil)
        #expect(answers == [true])
        controller.resolve(prompts.keys[1], response: "deny")
        controller.request(.write, requester: requester) { answers.append($0) }
        #expect(prompts.keys.count == 3)
        #expect(answers == [true, false])
    }

    @Test("write consent completion checks the current surface before touching the clipboard")
    func writeCompletionAfterTeardown() {
        var writes: [String] = []
        let surface: ghostty_surface_t = UnsafeMutableRawPointer(bitPattern: 1)!
        for (current, allowed) in [(Optional(surface), false), (nil, true), (nil, false)] {
            GhosttyApp.completeClipboardWrite("fixture", loc: GHOSTTY_CLIPBOARD_STANDARD,
                                             surface: current, allowed: allowed) { text, _ in writes.append(text) }
        }
        #expect(writes.isEmpty)
        GhosttyApp.completeClipboardWrite("fixture", loc: GHOSTTY_CLIPBOARD_STANDARD,
                                         surface: surface, allowed: true) { text, _ in writes.append(text) }
        #expect(writes == ["fixture"])
    }

    @Test("a waiter may destroy its response signal without resolving twice")
    func reentrantSignalDestruction() {
        let prompts = PromptLog()
        let controller = ClipboardPromptController(present: prompts.record)
        let requester = Requester()
        var data: UnsafeMutableRawPointer?
        var answers: [Bool] = []
        controller.request(.read, requester: requester) { allowed in
            answers.append(allowed)
            onClipboardPromptDestroyed(data, nil)
        }
        controller.request(.read, requester: requester) { answers.append($0) }
        var context: ClipboardPromptController.ResponseContext? = .init(key: prompts.keys[0], controller: controller)
        weak var observed = context
        data = Unmanaged.passRetained(context!).toOpaque()
        context = nil
        "allow".withCString { onClipboardPromptResponse(nil, $0, data) }
        #expect(answers == [true, true])
        #expect(observed == nil)
    }

    @Test("one requester and one access share a prompt, and each waiter fires once")
    func coalescing() {
        let prompts = PromptLog()
        let controller = ClipboardPromptController(present: prompts.record)
        let requester = Requester()
        var answers: [String: [Bool]] = [:]
        controller.request(.read, requester: requester) { answers["first", default: []].append($0) }
        controller.request(.read, requester: requester) { answers["second", default: []].append($0) }
        #expect(prompts.keys.count == 1)
        #expect(answers.isEmpty)

        controller.resolve(prompts.keys[0], response: "allow")
        controller.resolve(prompts.keys[0], response: "allow")
        #expect(answers == ["first": [true], "second": [true]])
    }

    @Test("different requesters, and read versus write, open separate prompts")
    func separatePrompts() {
        let prompts = PromptLog()
        let controller = ClipboardPromptController(present: prompts.record)
        let first = Requester(), second = Requester()
        var answers: [Bool] = []
        controller.request(.read, requester: first) { answers.append($0) }
        controller.request(.read, requester: second) { answers.append($0) }
        controller.request(.write, requester: first) { answers.append($0) }
        controller.request(.write, requester: nil) { answers.append($0) }
        #expect(Set(prompts.keys).count == 4)
        #expect(prompts.keys.map(\.access) == [.read, .read, .write, .write])

        controller.resolve(prompts.keys[1], response: "deny")
        #expect(answers == [false])
    }

    @Test("always-allow and always-deny are remembered per access")
    func rememberedPerAccess() {
        let prompts = PromptLog()
        let controller = ClipboardPromptController(present: prompts.record)
        let requester = Requester()
        var answers: [Bool] = []
        controller.request(.read, requester: requester) { answers.append($0) }
        controller.resolve(prompts.keys[0], response: "always-allow")
        controller.request(.read, requester: Requester()) { answers.append($0) }
        #expect(prompts.keys.count == 1)

        controller.request(.write, requester: requester) { answers.append($0) }
        #expect(prompts.keys.count == 2)
        controller.resolve(prompts.keys[1], response: "always-deny")
        controller.request(.write, requester: nil) { answers.append($0) }
        #expect(prompts.keys.count == 2)
        #expect(answers == [true, true, false, false])
    }

    @Test("resolving after teardown reaches the waiter while the requester is still retained, then frees it")
    func requesterTeardown() {
        let prompts = PromptLog()
        let controller = ClipboardPromptController(present: prompts.record)
        var requester: Requester? = Requester()
        weak var observed = requester
        var seen: [(allowed: Bool, alive: Bool, tornDown: Bool)] = []
        controller.request(.write, requester: requester) { allowed in
            seen.append((allowed, observed != nil, observed?.tornDown ?? true))
        }
        requester?.tornDown = true
        requester = nil
        #expect(observed != nil)

        controller.resolve(prompts.keys[0], response: "allow")
        #expect(seen.count == 1)
        #expect(seen.first?.allowed == true)
        #expect(seen.first?.alive == true)
        #expect(seen.first?.tornDown == true)
        #expect(observed == nil)
    }
}

@MainActor
private final class PromptLog {
    private(set) var keys: [ClipboardPromptController.PromptKey] = []

    func record(_ key: ClipboardPromptController.PromptKey, _: AnyObject?) { keys.append(key) }
}

private final class Requester {
    var tornDown = false
}
