#if DEBUG
import Foundation

/// Opt-in stderr observer for the blink timer, read by the `blink-unmapped` AT-SPI scenario
/// (`tests/atspi_blink_unmapped.py`). Opacity and a GLib source are invisible to AT-SPI, so without this a
/// pulse left running for an unmapped glyph cannot be seen from outside the process. Compiled out of
/// release builds and silent unless `AGTERM_ATSPI_BLINK_TRACE=1`, like the other `AGTERM_ATSPI_*` hooks.
enum BlinkPhaseTrace {
    static let environmentKey = "AGTERM_ATSPI_BLINK_TRACE"
    static let prefix = "agterm-blink-trace:"
    static let enabled = ProcessInfo.processInfo.environment[environmentKey] == "1"

    /// `agterm-blink-trace: window=<lowercase uuid> event=<name> key=value…` — the scenario parses exactly
    /// this shape, and `BlinkPhaseTraceTests` pins it.
    static func line(window: UUID, event: String, fields: [(String, String)]) -> String {
        ([prefix, "window=\(window.uuidString.lowercased())", "event=\(event)"]
            + fields.map { "\($0.0)=\($0.1)" }).joined(separator: " ")
    }

    static func emit(window: UUID, event: String, fields: [(String, String)]) {
        guard enabled else { return }
        FileHandle.standardError.write(Data((line(window: window, event: event, fields: fields) + "\n").utf8))
    }
}
#endif
