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
            for key in ["XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "SHELL"] {
                #expect(ProcessInfo.processInfo.environment[key] == before[key])
            }
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

    @Test("two overlapping processes share the desktop root but never their config files")
    func overlappingProcesses() throws {
        try withRoot { root in
            let executable = try compileChild(in: root)
            let runtime = root.appendingPathComponent("desktop-runtime")
            try FileManager.default.createDirectory(at: runtime, withIntermediateDirectories: false)
            let sentinel = runtime.appendingPathComponent("unrelated-desktop-file")
            try Data("keep".utf8).write(to: sentinel)
            let first = try StorageChild(executable: executable, root: runtime, marker: "first")
            defer { first.stop() }
            let second = try StorageChild(executable: executable, root: runtime, marker: "second")
            defer { second.stop() }
            let firstPaths = try first.readPaths()
            let secondPaths = try second.readPaths()
            #expect(first.process.isRunning && second.process.isRunning)
            #expect(first.process.processIdentifier != second.process.processIdentifier)
            #expect(Set(firstPaths.values).isDisjoint(with: secondPaths.values))
            for paths in [firstPaths, secondPaths] {
                #expect(paths.count == GhosttyGeneratedConfigStorage.Kind.allCases.count)
                for path in paths.values {
                    let directory = URL(fileURLWithPath: path).deletingLastPathComponent()
                    #expect(directory.deletingLastPathComponent().path == runtime.path)
                    #expect(try permissions(directory.path) == 0o700)
                    #expect(try permissions(path) == 0o600)
                }
            }
            try assertContents(firstPaths, marker: "first")
            try assertContents(secondPaths, marker: "second")
            try first.clean()
            for path in firstPaths.values { #expect(!FileManager.default.fileExists(atPath: path)) }
            try assertContents(secondPaths, marker: "second")
            #expect(second.process.isRunning)
            try second.clean()
            #expect(try FileManager.default.contentsOfDirectory(atPath: runtime.path) == ["unrelated-desktop-file"])
            #expect(try String(contentsOf: sentinel, encoding: .utf8) == "keep")
        }
    }

    private func assertContents(_ paths: [String: String], marker: String) throws {
        for (kind, path) in paths {
            let expected = kind == "lightTheme" || kind == "darkTheme" ? "" : "\(marker):\(kind)"
            #expect(try String(contentsOfFile: path, encoding: .utf8) == expected)
        }
    }

    /// Compile the production helper, not a reimplementation. Each child keeps it alive behind a pipe
    /// barrier so the test observes simultaneous ownership and one cleanup while the peer is still live.
    private func compileChild(in root: URL) throws -> URL {
        let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("Sources/AgtermLinux/GhosttyGeneratedConfigStorage.swift")
        let driver = root.appendingPathComponent("StorageChild.swift")
        try Self.childSource.write(to: driver, atomically: true, encoding: .utf8)
        let executable = root.appendingPathComponent("storage-child")
        let compiler = Process()
        let output = Pipe()
        compiler.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        compiler.arguments = ["swiftc", "-swift-version", "6", "-parse-as-library", source.path, driver.path, "-o", executable.path]
        compiler.standardOutput = output
        compiler.standardError = output
        try compiler.run()
        let log = output.fileHandleForReading.readDataToEndOfFile()
        compiler.waitUntilExit()
        try #require(compiler.terminationStatus == 0, "Child fixture compile failed: \(String(decoding: log, as: UTF8.self))")
        return executable
    }

    private static let childSource = #"""
        import Foundation
        @main struct StorageChild {
            static func main() throws {
                let before = ProcessInfo.processInfo.environment
                let storage = try GhosttyGeneratedConfigStorage()
                defer { try? storage.cleanup() }
                let marker = CommandLine.arguments[1]
                var paths: [String: String] = [:]
                for kind in GhosttyGeneratedConfigStorage.Kind.allCases {
                    let text = kind == .lightTheme || kind == .darkTheme ? "" : "\(marker):\(kind.rawValue)"
                    paths[kind.rawValue] = try storage.write(text, as: kind)
                }
                guard ProcessInfo.processInfo.environment == before else { exit(3) }
                var payload = try JSONSerialization.data(withJSONObject: paths, options: [.sortedKeys])
                payload.append(10)
                FileHandle.standardOutput.write(payload)
                guard readLine() == "cleanup" else { exit(4) }
                try storage.cleanup()
                try storage.cleanup()
                do {
                    _ = try storage.write("after cleanup", as: .runtime)
                    exit(5)
                } catch GhosttyGeneratedConfigStorage.Failure.closed { }
                FileHandle.standardOutput.write(Data("cleaned\n".utf8))
            }
        }
        """#

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

private final class StorageChild {
    let process = Process()
    private let input = Pipe()
    private let output = Pipe()

    init(executable: URL, root: URL, marker: String) throws {
        process.executableURL = executable
        process.arguments = [marker]
        var environment = ProcessInfo.processInfo.environment
        environment["XDG_RUNTIME_DIR"] = root.path
        environment["WAYLAND_DISPLAY"] = "disposable-display"
        environment["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=disposable-bus"
        environment["SHELL"] = "/disposable/login-shell"
        process.environment = environment
        process.standardInput = input
        process.standardOutput = output
        try process.run()
    }

    func readPaths() throws -> [String: String] {
        let data = try line()
        return try #require(JSONSerialization.jsonObject(with: data) as? [String: String])
    }

    func clean() throws {
        input.fileHandleForWriting.write(Data("cleanup\n".utf8))
        #expect(String(decoding: try line(), as: UTF8.self) == "cleaned\n")
        process.waitUntilExit()
        #expect(process.terminationStatus == 0)
    }

    func stop() {
        try? input.fileHandleForWriting.close()
        if process.isRunning { process.terminate() }
        process.waitUntilExit()
    }

    private func line() throws -> Data {
        var data = Data()
        let deadline = Date().addingTimeInterval(15)
        while !data.contains(10) {
            let remaining = max(0, deadline.timeIntervalSinceNow * 1_000)
            var descriptor = pollfd(fd: output.fileHandleForReading.fileDescriptor, events: Int16(POLLIN), revents: 0)
            let result = poll(&descriptor, 1, Int32(remaining))
            if result < 0 && errno == EINTR { continue }
            try #require(result > 0, "Timed out waiting for config-storage child")
            let bytes = output.fileHandleForReading.availableData
            try #require(!bytes.isEmpty, "Config-storage child exited before its response")
            data.append(bytes)
        }
        return data
    }
}
