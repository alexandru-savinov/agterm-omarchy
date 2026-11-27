import CGtk
import Foundation
import agtermCore

@MainActor
extension GhosttySurface {
    var pasteOriginHost: String? {
        guard let row = controller?.store.session(withID: sessionID), let host = row.remoteHost,
              let binding = row.remotePresentation?.binding, let pane = UUID(uuidString: paneToken) else { return nil }
        return binding.daemon(forLocalPane: pane) != nil || row.originScratch == pane ? host : nil
    }
    var pasteRefusal: String? {
        if let refusal = controller?.coveredPaneRefusal(self) { return refusal.error ?? "another viewer leads this pane" }
        return pasteCopying ? "a paste is still copying" : nil
    }

    /// Clipboard, palette, control and drop paths all share ownership and lead checks.
    func pasteClipboard(completion: @escaping @MainActor (String?) -> Void = { _ in }) {
        if let refusal = pasteRefusal { return completion(refusal) }
        guard let surface else { return completion("session not realized") }
        // Let the engine ask the current clipboard owner for ordinary local pastes. Its read callback
        // tries the URI list, text and then an image directly, even while GDK still reports the previous owner's formats.
        // The completion waits for that read, so a `session.paste` reply follows the inserted text.
        if pasteOriginHost == nil, !LinuxPasteClipboard.hasImageOnlyOffer() {
            EnginePasteCompletion.arm(completion, for: self)
            let action = "paste_from_clipboard"
            _ = action.withCString { ghostty_surface_binding_action(surface, $0, UInt(action.utf8.count)) }
            // libghostty answered without a clipboard read (or started none)
            EnginePasteCompletion.take(for: self)?(nil)
            return
        }
        pasteCopying = true
        LinuxPasteClipboard.read { [weak self] offer in
            guard let self else { return completion("session closed") }
            self.pasteCopying = false
            self.pasteOffer(offer, completion: completion)
        }
    }

    func userPaste() {
        pasteClipboard { [weak self] reason in
            if let reason { self?.controller?.showToast(reason) }
        }
    }

    @discardableResult
    func pasteOffer(_ offer: PasteOffer, completion: @escaping @MainActor (String?) -> Void = { _ in }) -> Bool {
        if let refusal = pasteRefusal { completion(refusal); return false }
        guard surface != nil else { completion("session not realized"); return false }
        let remote = pasteOriginHost
        let onOrigin = remote != nil
        if offer.route(onOrigin: onOrigin) == .asIs {
            if let text = offer.asIsText { pasteThroughProtection(text: text) }
            completion(nil)
            return true
        }
        pasteCopying = true
        controller?.showToast(onOrigin ? "Copying paste to \(remote ?? "origin")…" : "Saving pasted image…")
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let copied = LinuxPasteTransfer.text(offer, host: onOrigin ? remote : nil)
            runOnMain { MainActor.assumeIsolated {
                guard let self else { return completion("session closed") }
                self.pasteCopying = false
                guard self.surface != nil else { return completion("session not realized") }
                guard self.pasteOriginHost == remote else { return completion("the pane changed while the paste was copying") }
                if let refusal = self.pasteRefusal { return completion(refusal) }
                switch copied {
                case .success(let text): self.pasteThroughProtection(text: text); completion(nil)
                case .failure(let failure):
                    self.controller?.showToast(failure.reason)
                    completion(failure.reason)
                }
            } }
        }
        return true
    }

    func handlesPasteKey(keyval: UInt32, keycode: UInt32, state: UInt32) -> Bool {
        let modifiers = state & (ModifierKeyMods.controlBit | ModifierKeyMods.superBit | ModifierKeyMods.altBit | (1 << 0))
        let character = gdk_keyval_to_unicode(gdk_keyval_to_lower(keyval))
        let pasteKey = character == 0x76 || keycode == 55 && character > 127
        return modifiers == ModifierKeyMods.controlBit | (1 << 0) && pasteKey
    }
}

/// Carries `pasteClipboard`'s completion across the synchronous `paste_from_clipboard` action to the clipboard read
/// libghostty starts inside it (`LinuxClipboardAdapter.readText`).
@MainActor
enum EnginePasteCompletion {
    private static var pending: (surface: ObjectIdentifier, completion: @MainActor (String?) -> Void)?

    static func arm(_ completion: @escaping @MainActor (String?) -> Void, for surface: GhosttySurface) {
        pending = (ObjectIdentifier(surface), completion)
    }

    static func take(for surface: GhosttySurface) -> (@MainActor (String?) -> Void)? {
        guard let current = pending, current.surface == ObjectIdentifier(surface) else { return nil }
        pending = nil
        return current.completion
    }
}

enum LinuxPasteTransfer {
    struct Failure: Error, Sendable { let reason: String }

    /// Runs only on a worker. The shared bundle format enforces file kinds, entry and byte limits.
    static func text(_ offer: PasteOffer, host: String?) -> Result<String, Failure> {
        do {
            guard let host else {
                guard case .image(let bytes) = offer.action else { throw Failure(reason: "nothing to paste") }
                return .success(ShellEscape.path(try PasteStaging().stage(image: bytes, name: PasteOffer.imageName(at: Date()))))
            }
            let entries: [PasteBundle.Entry]
            if case .image(let bytes) = offer.action {
                entries = try PasteBundle.entries(image: bytes, name: PasteOffer.imageName(at: Date()))
            } else { entries = try PasteBundle.entries(files: offer.fileURLs) }
            let bundle = try PasteBundle.temporaryFile(entries)
            defer { try? FileManager.default.removeItem(at: bundle.deletingLastPathComponent()) }
            let argv = try RemoteSession.pasteReceiveCommand(host: host)
            let megabytes = Double(entries.reduce(Int64(0)) { $0 + $1.header.size }) / 1_000_000
            let reply = PasteStaging.Reply(result: LinuxRemoteCommand.run(argv, deadline: 15 + megabytes, inputURL: bundle))
            guard let paths = reply.paths else { throw Failure(reason: reply.error ?? "the paste could not be copied") }
            let text: String?
            if case .image = offer.action { text = paths.count == 1 ? ShellEscape.path(paths[0]) : nil } else { text = offer.insertText(filePaths: paths) }
            guard let text else { throw Failure(reason: "the origin answered for a different paste") }
            return .success(text)
        } catch let failure as Failure { return .failure(failure) } catch {
            return .failure(Failure(reason: PasteStaging.reason(for: error)))
        }
    }
}
