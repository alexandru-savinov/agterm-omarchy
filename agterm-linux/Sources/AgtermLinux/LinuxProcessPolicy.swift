import Foundation
import agtermCore

extension CommandRestore {
    /// Parses `/proc/<pid>/cmdline` into argv.
    ///
    /// Empty fields followed by a non-empty one are real empty arguments and are
    /// kept (`a\0\0b\0` → `["a", "", "b"]`). All trailing empty fields are dropped:
    /// a process that retitles itself (Node `process.title` via libuv,
    /// setproctitle daemons) NUL-pads the rest of its argv area, and Linux gives
    /// no argc to tell that padding from a real trailing `''`. Replaying padding
    /// as empty arguments is worse than losing a rare real trailing `''`, so that
    /// is a documented limitation. Input with no non-empty field returns nil.
    static func parseProcCmdline(_ data: Data) -> [String]? {
        var fields = data.split(separator: 0, omittingEmptySubsequences: false)
        while let last = fields.last, last.isEmpty { fields.removeLast() }
        guard !fields.isEmpty else { return nil }
        return fields.map { String(decoding: $0, as: UTF8.self) }
    }
}
