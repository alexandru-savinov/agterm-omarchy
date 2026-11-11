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

    /// The restore capture for raw `/proc/<pid>/cmdline` bytes: `parseProcCmdline`, minus captures that
    /// carry the retitle signature. A process that rewrote its title (`process.title`, setproctitle) puts
    /// a whole display string such as `npm run dev` into the first field and NUL-pads the rest of its argv
    /// area; replayed, that string is shell-quoted as ONE word (`'npm run dev'`) and fails with command
    /// not found. The signature is detected conservatively, on the raw bytes: more than the single NUL
    /// terminator at the end AND exactly one field AND that field contains whitespace. Such a capture is
    /// not restorable (nil); the string is never split, because no split of a title is provably the
    /// original argv. A real argv[0] containing a space ends in exactly one NUL and stays restorable.
    ///
    /// Limitation: a process retitled to a single word (`claude`) is indistinguishable from a real
    /// one-word argv in cmdline alone, so it restores without its original arguments. The restore path
    /// does not consult `/proc/<pid>/exe` or whether the first field resolves on PATH, and this does not
    /// add either: neither would recover the arguments the retitle overwrote.
    static func restorableProcCmdline(_ data: Data) -> [String]? {
        guard let argv = parseProcCmdline(data) else { return nil }
        let trailingNULs = data.reversed().prefix { $0 == 0 }.count
        if trailingNULs > 1, argv.count == 1,
           argv[0].unicodeScalars.contains(where: { CharacterSet.whitespaces.contains($0) }) {
            return nil
        }
        return argv
    }
}
