import Foundation
import Testing

private final class CLIExecutableBundleMarker: NSObject {}

enum CLIExecutableFixture {
    static func executable() throws -> URL {
        try #require(locate(startingAt: Bundle(for: CLIExecutableBundleMarker.self).bundleURL),
                     "swift test must build agtermctl beside the test bundle")
    }

    static func locate(startingAt directory: URL) -> URL? {
        var directory = directory
        for _ in 0..<6 {
            let candidate = directory.appendingPathComponent("agtermctl")
            if FileManager.default.isExecutableFile(atPath: candidate.path) { return candidate }
            directory.deleteLastPathComponent()
        }
        return nil
    }
}

struct CLIExecutableFixtureTests {
    @Test(arguments: ["", "PackageTests.xctest", "PackageTests.xctest/Contents/MacOS"])
    func findsSiblingExecutable(bundlePath: String) throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let bundle = root.appendingPathComponent(bundlePath)
        try FileManager.default.createDirectory(at: bundle, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let executable = root.appendingPathComponent("agtermctl")
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to: executable)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: executable.path)
        #expect(CLIExecutableFixture.locate(startingAt: bundle) == executable)
    }

    @Test func rejectsMissingOrNonExecutableSibling() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        #expect(CLIExecutableFixture.locate(startingAt: root) == nil)
        let executable = root.appendingPathComponent("agtermctl")
        try Data().write(to: executable)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: executable.path)
        #expect(CLIExecutableFixture.locate(startingAt: root) == nil)
    }
}
