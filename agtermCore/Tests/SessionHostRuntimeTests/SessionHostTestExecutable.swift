import Foundation
import Testing

/// SwiftPM places declared executable dependencies beside the test bundle, including with a custom
/// scratch path or the Swift Build engine's Products/Debug layout. Never reuse a source-tree binary.
enum SessionHostTestExecutable {
    private final class BundleMarker: NSObject {}

    static func url(_ name: String) throws -> URL {
        let directory = Bundle(for: BundleMarker.self).bundleURL.deletingLastPathComponent()
        let executable = directory.appendingPathComponent(name)
        try #require(FileManager.default.isExecutableFile(atPath: executable.path),
                     "Missing built test fixture: \(executable.path)")
        return executable
    }
}
