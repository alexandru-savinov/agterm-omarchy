import agtermCore
import AppKit
import Carbon

/// The active keyboard layout's ability to type ASCII — the signal that decides how a key press resolves
/// to a keymap chord (`chordKey(forKeyCode:produced:layoutIsASCIICapable:)`).
///
/// A Latin layout (US, Dvorak, Colemak, US-International, French, German) reports true and binds by the
/// character it types. A non-Latin one (Russian, Greek, Hebrew, Arabic, Thai) reports false and binds by
/// physical position, since nothing it types can spell a chord.
///
/// This covers only the shortcuts the app resolves from a key event itself. Every other built-in
/// rides an AppKit menu key equivalent, which resolves on its own; ⌘N and ⌘W were observed firing on a
/// Russian layout, but how AppKit reaches them was not isolated here, and `.claude/rules/control-api.md`
/// records a conflicting account for ⌘C/⌘V/⌘A. Do not cite this type as evidence either way.
enum KeyboardLayout {
    /// Whether the active keyboard layout can type ASCII. Read fresh on every key press — the query costs
    /// well under a microsecond, so it needs no cache and no input-source-changed observer and can never go
    /// stale mid-session. An unresolvable layout falls back to true (bind by produced character), which is
    /// how every Latin layout already behaves.
    static var isASCIICapable: Bool {
        guard let source = TISCopyCurrentKeyboardLayoutInputSource()?.takeRetainedValue(),
              let value = TISGetInputSourceProperty(source, kTISPropertyInputSourceIsASCIICapable)
        else { return true }
        return CFBooleanGetValue(Unmanaged<CFBoolean>.fromOpaque(value).takeUnretainedValue())
    }
}

extension NSEvent {
    /// The keymap chord this key-down spells, or nil when it carries no usable base key. The base key is the
    /// named special key, else what `chordKey` resolves from `produced` under the active layout. The caller
    /// picks the accessor behind `produced`, which decides whether a shifted symbol keeps its shift; it is
    /// not read for a named key.
    func keymapChord(produced: @autoclosure () -> String?) -> Chord? {
        var mods: Modifier = []
        if modifierFlags.contains(.control) { mods.insert(.control) }
        if modifierFlags.contains(.command) { mods.insert(.command) }
        if modifierFlags.contains(.option) { mods.insert(.option) }
        if modifierFlags.contains(.shift) { mods.insert(.shift) }
        guard let key = namedKey(forKeyCode: keyCode)
            ?? chordKey(forKeyCode: keyCode, produced: produced(), layoutIsASCIICapable: KeyboardLayout.isASCIICapable)
        else { return nil }
        return Chord(mods: mods, key: key)
    }
}
