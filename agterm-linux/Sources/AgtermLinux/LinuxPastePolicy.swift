import Foundation
import agtermCore

extension ShellEscape {
    static func dropPayload(_ payload: String) -> String? {
        if let paths = PasteDecoder.posixPaths(fromURIList: payload) {
            return paths
        }
        return payload.isEmpty ? nil : payload
    }
}

enum PasteDecoder {
    /// A drop shell-escapes each path so a name with a space stays one word at the prompt; a clipboard
    /// read (`LinuxClipboardPolicy.pastedPaths`) hands the paths over as upstream does, space-joined.
    static func posixPaths(fromURIList text: String, escaped: Bool = true) -> String? {
        let lines = text.split(whereSeparator: \.isNewline).map(String.init)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty && !$0.hasPrefix("#") }
        guard !lines.isEmpty, lines.allSatisfy({ $0.hasPrefix("file://") }) else { return nil }
        let paths = lines.compactMap { URL(string: $0)?.path }
        guard paths.count == lines.count else { return nil }
        return (escaped ? paths.map(ShellEscape.path) : paths).joined(separator: " ")
    }
}

/// What a terminal drop needs from its surface: focus, then a PASTE. There is deliberately no typed
/// (`inject`) entry here, so a drop cannot reach the keystroke path where each line break is a Return.
@MainActor
protocol DropPasteTarget: AnyObject {
    func grabFocus(supersedingPopoverCapture: Bool)
    /// A paste that goes through libghostty's paste protection, never around it.
    func pasteThroughProtection(text: String)
}

/// Drops are delivered as a paste, as on macOS (`GhosttySurfaceView.performDragOperation`): a multi-line
/// drop is inserted as text and honours the terminal's bracketed-paste mode. It is exactly as safe as
/// Ctrl+Shift+V: when the program has not enabled bracketed paste, a multi-line drop asks first.
@MainActor
enum LinuxDropPolicy {
    /// A text/uri-list drop. Sidebar row/workspace ids are refused here as a defensive guard in case a
    /// compositor offers COPY for an in-app sidebar drag. Returns whether the drop was accepted.
    @discardableResult
    static func deliverText(_ payload: String, to target: some DropPasteTarget) -> Bool {
        if UUID(uuidString: payload) != nil { return false }
        if payload.hasPrefix("w:"), UUID(uuidString: String(payload.dropFirst(2))) != nil { return false }
        guard let text = ShellEscape.dropPayload(payload) else { return false }
        return paste(text, to: target)
    }

    /// A file-list drop: already shell-escaped paths, space-joined. Returns whether the drop was accepted.
    @discardableResult
    static func deliverFiles(_ escapedPaths: [String], to target: some DropPasteTarget) -> Bool {
        paste(escapedPaths.filter { !$0.isEmpty }.joined(separator: " "), to: target)
    }

    private static func paste(_ text: String, to target: some DropPasteTarget) -> Bool {
        guard !text.isEmpty else { return false }
        target.grabFocus(supersedingPopoverCapture: true)
        target.pasteThroughProtection(text: text)
        return true
    }
}
