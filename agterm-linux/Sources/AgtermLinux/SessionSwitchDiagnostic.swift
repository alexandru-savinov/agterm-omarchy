// Disposable #357 observation only; this file is not proposed for production.
import CGtk
import Foundation
import agtermCore

@MainActor
enum SessionSwitchDiagnostic {
    static let enabled = ProcessInfo.processInfo.environment["AGTERM_SWITCH_DIAGNOSTIC"] == "1"
    private(set) static var inputGeneration: UInt64 = 0

    static func controlEvent(_ event: String, keycode: UInt32, owner: AppController?) -> UInt64 {
        guard enabled else { return 0 }
        inputGeneration += 1
        emit(event, owner: owner, sequence: inputGeneration, extra: "controlCode=\(keycode)")
        return inputGeneration
    }

    static func emit(_ event: String, owner: AppController?, sequence: UInt64 = 0,
                     extra: String = "") {
        guard enabled else { return }
        var focusKind = "none"
        var active = false
        if let owner {
            active = gtk_window_is_active(WIN(owner.windowPointer)) != 0
            if let focus = gtk_window_get_focus(WIN(owner.windowPointer)),
               let cssName = gtk_widget_get_css_name(focus) {
                // Toolkit type only: never entry text, accessible name, or window title.
                focusKind = String(cString: cssName)
            }
        }
        let window = owner?.windowID.uuidString ?? "none"
        let fields = "pid=\(ProcessInfo.processInfo.processIdentifier) t=\(ProcessInfo.processInfo.systemUptime) "
            + "event=\(event) seq=\(sequence) input=\(inputGeneration) owner=\(owner != nil) "
            + "window=\(window) windowActive=\(active) focusKind=\(focusKind) "
            + "cycle=\(owner?.sessionSwitcher.isActive == true) \(extra)"
        FileHandle.standardError.write(Data(("switch-diag: " + fields + "\n").utf8))
    }

    static func observeLater(owner: AppController?, sequence: UInt64, initialHeld: Bool?) {
        guard enabled, initialHeld == true else { return }
        let generation = inputGeneration
        // One observation only. Never feeds a value back into commit/cancel or held-key tracking.
        MainTimer.schedule(after: 0.05) { [weak owner] in
            let held = ModifierKeyMods.currentControlIsHeld()
            emit("followup", owner: owner, sequence: sequence,
                 extra: "held=\(String(describing: held)) interveningControl=\(inputGeneration != generation)")
        }
    }
}
