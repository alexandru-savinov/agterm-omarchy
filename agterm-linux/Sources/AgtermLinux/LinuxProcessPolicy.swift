import Foundation
import agtermCore

extension CommandRestore {
    static func parseProcCmdline(_ data: Data) -> [String]? {
        guard !data.isEmpty else { return nil }
        // Remove only the record terminator; preceding NULs encode empty arguments.
        let payload = data.last == 0 ? data.dropLast() : data[...]
        return payload.split(separator: 0, omittingEmptySubsequences: false)
            .map { String(decoding: $0, as: UTF8.self) }
    }
}
