import Foundation
import Testing
#if canImport(Glibc)
import Glibc
#else
import Darwin
#endif
@testable import AgtermLinux

@Suite("Generated Ghostty config storage")
struct GhosttyGeneratedConfigStorageTests {
    @Test("all generated inputs are private and earlier reload inputs remain immutable")
    func permissionsAndLifetime() throws {
        try withRoot { root in
            let storage = try GhosttyGeneratedConfigStorage(runtimeRoot: root.path)
            defer { try? storage.cleanup() }
            #expect(try permissions(storage.directoryPath) == 0o700)
            var paths = Set<String>()
            for kind in GhosttyGeneratedConfigStorage.Kind.allCases {
                let text = kind == .lightTheme || kind == .darkTheme ? "" : "\(kind.rawValue): λ\n"
                let path = try storage.write(text, as: kind)
                #expect(paths.insert(path).inserted)
                #expect(try permissions(path) == 0o600)
                #expect(try String(contentsOfFile: path, encoding: .utf8) == text)
            }
            let first = try storage.write("background = #123456", as: .runtime)
            let second = try storage.write(String(repeating: "new λ\n", count: 50_000), as: .runtime)
            #expect(first != second)
            #expect(try String(contentsOfFile: first, encoding: .utf8) == "background = #123456")
            #expect(try String(contentsOfFile: second, encoding: .utf8) == String(repeating: "new λ\n", count: 50_000))
        }
    }

    @Test("missing runtime uses a private fallback without changing the environment")
    func fallback() throws {
        try withRoot { root in
            let before = ProcessInfo.processInfo.environment
            for missing in [nil, ""] as [String?] {
                let storage = try GhosttyGeneratedConfigStorage(runtimeRoot: missing, temporaryRoot: root.path)
                #expect(URL(fileURLWithPath: storage.directoryPath).deletingLastPathComponent().path == root.path)
                #expect(try permissions(storage.directoryPath) == 0o700)
                try storage.cleanup()
            }
            #expect(ProcessInfo.processInfo.environment == before)
        }
    }

    @Test("invalid supplied roots fail instead of silently falling back")
    func setupFailures() throws {
        try withRoot { root in
            let file = root.appendingPathComponent("file")
            try Data("sentinel".utf8).write(to: file)
            for invalid in ["relative", root.path + "\0hidden", root.appendingPathComponent("absent/child").path, file.path] {
                #expect(throws: GhosttyGeneratedConfigStorage.Failure.self) {
                    _ = try GhosttyGeneratedConfigStorage(runtimeRoot: invalid, temporaryRoot: root.path)
                }
            }
            #expect(try FileManager.default.contentsOfDirectory(atPath: root.path) == ["file"])
        }
    }

    @Test("failed exclusive writes never publish stale input or delete an unrelated entry")
    func failedWrite() throws {
        try withRoot { root in
            let storage = try GhosttyGeneratedConfigStorage(runtimeRoot: root.path)
            let stale = URL(fileURLWithPath: storage.directoryPath).appendingPathComponent("runtime-1.conf")
            try Data("unrelated stale input".utf8).write(to: stale)
            #expect(throws: GhosttyGeneratedConfigStorage.Failure.self) {
                _ = try storage.write("replacement", as: .runtime)
            }
            #expect(try String(contentsOf: stale, encoding: .utf8) == "unrelated stale input")
            let fresh = try storage.write("fresh", as: .runtime)
            #expect(fresh != stale.path)
            #expect(try String(contentsOfFile: fresh, encoding: .utf8) == "fresh")
            // An unowned entry keeps the directory nonempty. Cleanup reports it instead of recursing.
            #expect(throws: GhosttyGeneratedConfigStorage.Failure.self) { try storage.cleanup() }
            try storage.cleanup()
            #expect(!FileManager.default.fileExists(atPath: fresh))
            #expect(try String(contentsOf: stale, encoding: .utf8) == "unrelated stale input")
            #expect(throws: GhosttyGeneratedConfigStorage.Failure.self) { _ = try storage.write("late", as: .runtime) }
        }
    }

    @Test("cleanup is local to its owner, idempotent, and prevents reuse")
    func cleanup() throws {
        try withRoot { root in
            let first = try GhosttyGeneratedConfigStorage(runtimeRoot: root.path)
            let second = try GhosttyGeneratedConfigStorage(runtimeRoot: root.path)
            defer { try? first.cleanup(); try? second.cleanup() }
            let unrelated = root.appendingPathComponent("desktop-sentinel")
            try Data("keep".utf8).write(to: unrelated)
            let firstPath = try first.write("first", as: .defaults)
            let secondPath = try second.write("second", as: .defaults)
            #expect(firstPath != secondPath)
            try first.cleanup()
            try first.cleanup()
            #expect(!FileManager.default.fileExists(atPath: first.directoryPath))
            #expect(!FileManager.default.fileExists(atPath: firstPath))
            #expect(try String(contentsOfFile: secondPath, encoding: .utf8) == "second")
            #expect(try String(contentsOf: unrelated, encoding: .utf8) == "keep")
            #expect(throws: GhosttyGeneratedConfigStorage.Failure.self) { _ = try first.write("late", as: .runtime) }
            let later = try second.write("still live", as: .runtime)
            #expect(try String(contentsOfFile: later, encoding: .utf8) == "still live")
        }
    }

    @Test("a replacement at the old directory path is never followed or removed")
    func replacedDirectory() throws {
        try withRoot { root in
            let storage = try GhosttyGeneratedConfigStorage(runtimeRoot: root.path)
            let owned = try storage.write("owned", as: .runtime)
            let moved = root.appendingPathComponent("moved-owner")
            try FileManager.default.moveItem(atPath: storage.directoryPath, toPath: moved.path)
            try FileManager.default.createDirectory(atPath: storage.directoryPath, withIntermediateDirectories: false)
            let replacement = URL(fileURLWithPath: storage.directoryPath).appendingPathComponent("unrelated")
            try Data("keep replacement".utf8).write(to: replacement)
            #expect(throws: GhosttyGeneratedConfigStorage.Failure.self) { _ = try storage.write("late", as: .runtime) }
            #expect(throws: GhosttyGeneratedConfigStorage.Failure.self) { try storage.cleanup() }
            try storage.cleanup()
            #expect(try String(contentsOf: replacement, encoding: .utf8) == "keep replacement")
            #expect(!FileManager.default.fileExists(atPath: moved.appendingPathComponent((owned as NSString).lastPathComponent).path))
        }
    }

    private func withRoot(_ body: (URL) throws -> Void) throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("agterm-config-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        try body(root)
    }

    private func permissions(_ path: String) throws -> Int {
        let attributes = try FileManager.default.attributesOfItem(atPath: path)
        return try #require(attributes[.posixPermissions] as? Int) & 0o777
    }
}
