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
    @Test("generated files are private and ordinary inputs end at their consumer")
    func permissionsAndLifetime() throws {
        try withRoot { root in
            let storage = try GhosttyGeneratedConfigStorage(runtimeRoot: root.path)
            defer { try? storage.cleanup() }
            let themes = try storage.appearanceThemes()
            #expect(try permissions(storage.directoryPath) == 0o700)
            for path in [themes.light, themes.dark] {
                #expect(try permissions(path) == 0o600)
                #expect(try String(contentsOfFile: path, encoding: .utf8) == "")
            }
            for kind in GhosttyGeneratedConfigStorage.Kind.allCases {
                let text = String(repeating: "\(kind.rawValue): λ\n", count: 10_000)
                let path = try storage.withConfigFile(text, as: kind) { path throws -> String in
                    #expect(try permissions(path) == 0o600)
                    #expect(try String(contentsOfFile: path, encoding: .utf8) == text)
                    return path
                }
                #expect(!FileManager.default.fileExists(atPath: path))
                #expect(storage.retainedFileCount == 2)
            }
            #expect(try storage.appearanceThemes() == themes)
        }
    }

    @Test("1000 rebuilds keep exactly one theme pair and bounded bookkeeping")
    func repeatedBuilds() throws {
        try withRoot { root in
            let storage = try GhosttyGeneratedConfigStorage(runtimeRoot: root.path)
            defer { try? storage.cleanup() }
            let themes = try storage.appearanceThemes()
            var firstArgument: String?
            for iteration in 0..<1_000 {
                for kind in GhosttyGeneratedConfigStorage.Kind.allCases {
                    let value = "\(iteration):\(kind.rawValue)"
                    let copied = try storage.withConfigFile(value, as: kind) { path in
                        #expect(storage.retainedFileCount == 3)
                        return try String(contentsOfFile: path, encoding: .utf8)
                    }
                    #expect(copied == value)
                    if firstArgument == nil { firstArgument = copied }
                    #expect(storage.retainedFileCount == 2)
                }
                #expect(try storage.appearanceThemes() == themes)
                #expect(try FileManager.default.contentsOfDirectory(atPath: storage.directoryPath).count == 2)
            }
            #expect(firstArgument == "0:defaults")
        }
    }

    @Test("failed consumers release their ordinary file and bookkeeping")
    func failedConsumer() throws {
        enum ConsumerFailure: Error { case stopped }
        try withRoot { root in
            let storage = try GhosttyGeneratedConfigStorage(runtimeRoot: root.path)
            defer { try? storage.cleanup() }
            _ = try storage.appearanceThemes()
            var input: String?
            #expect(throws: ConsumerFailure.self) {
                try storage.withConfigFile("consumed then failed", as: .runtime) { path in
                    input = path
                    throw ConsumerFailure.stopped
                }
            }
            #expect(!FileManager.default.fileExists(atPath: try #require(input)))
            #expect(storage.retainedFileCount == 2)
            #expect(try FileManager.default.contentsOfDirectory(atPath: storage.directoryPath).count == 2)
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

    @Test("failed exclusive writes never consume stale input or delete an unrelated entry")
    func failedWrite() throws {
        try withRoot { root in
            let storage = try GhosttyGeneratedConfigStorage(runtimeRoot: root.path)
            let stale = URL(fileURLWithPath: storage.directoryPath).appendingPathComponent("runtime-1.conf")
            try Data("unrelated stale input".utf8).write(to: stale)
            var consumed = false
            #expect(throws: GhosttyGeneratedConfigStorage.Failure.self) {
                try storage.withConfigFile("replacement", as: .runtime) { _ in consumed = true }
            }
            #expect(!consumed)
            #expect(try String(contentsOf: stale, encoding: .utf8) == "unrelated stale input")
            try storage.withConfigFile("fresh", as: .runtime) { path throws in
                #expect(path != stale.path)
                #expect(try String(contentsOfFile: path, encoding: .utf8) == "fresh")
            }
            #expect(storage.retainedFileCount == 0)
            #expect(throws: GhosttyGeneratedConfigStorage.Failure.self) { try storage.cleanup() }
            try storage.cleanup()
            #expect(try String(contentsOf: stale, encoding: .utf8) == "unrelated stale input")
            #expect(throws: GhosttyGeneratedConfigStorage.Failure.self) {
                try storage.withConfigFile("late", as: .runtime) { _ in consumed = true }
            }
            #expect(!consumed)
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
            let firstThemes = try first.appearanceThemes()
            let secondThemes = try second.appearanceThemes()
            #expect(firstThemes != secondThemes)
            try first.cleanup()
            try first.cleanup()
            #expect(!FileManager.default.fileExists(atPath: first.directoryPath))
            #expect(try String(contentsOfFile: secondThemes.dark, encoding: .utf8) == "")
            #expect(try String(contentsOf: unrelated, encoding: .utf8) == "keep")
            #expect(throws: GhosttyGeneratedConfigStorage.Failure.self) { _ = try first.appearanceThemes() }
            try second.withConfigFile("still live", as: .runtime) { path throws in
                #expect(try String(contentsOfFile: path, encoding: .utf8) == "still live")
            }
        }
    }

    @Test("a replacement at the old directory path is never followed or removed")
    func replacedDirectory() throws {
        try withRoot { root in
            let storage = try GhosttyGeneratedConfigStorage(runtimeRoot: root.path)
            let themes = try storage.appearanceThemes()
            let moved = root.appendingPathComponent("moved-owner")
            try FileManager.default.moveItem(atPath: storage.directoryPath, toPath: moved.path)
            try FileManager.default.createDirectory(atPath: storage.directoryPath, withIntermediateDirectories: false)
            let replacement = URL(fileURLWithPath: storage.directoryPath).appendingPathComponent("unrelated")
            try Data("keep replacement".utf8).write(to: replacement)
            #expect(throws: GhosttyGeneratedConfigStorage.Failure.self) { _ = try storage.appearanceThemes() }
            #expect(throws: GhosttyGeneratedConfigStorage.Failure.self) { try storage.cleanup() }
            try storage.cleanup()
            #expect(try String(contentsOf: replacement, encoding: .utf8) == "keep replacement")
            #expect(!FileManager.default.fileExists(atPath: moved.appendingPathComponent((themes.light as NSString).lastPathComponent).path))
        }
    }

    @Test("two overlapping processes share the desktop root but never their config files")
    func overlappingProcesses() throws {
        try withRoot { root in
            let executable = try compileChild(in: root, source: Self.childSource)
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
                #expect(paths.count == 5)
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

    #if os(Linux)
    @Test("pinned libghostty replays cloned arguments after temporary input removal")
    func actualGhosttyReplay() throws {
        try withRoot { root in
            let executable = try compileChild(in: root, source: Self.ghosttySource, ghostty: true)
            let child = try StorageChild(executable: executable, root: root, marker: "ghostty", syntheticDesktop: false)
            defer { child.stop() }
            #expect(try child.readPaths() == ["rebuilds": "1000", "retained": "2", "replay": "old-new-old"])
            try child.clean()
        }
    }

    /// Separate process: ghostty_init owns global allocator state and must not race other test suites.
    /// No GTK init, surface, display, shell or session is created by this headless C API regression.
    private static let ghosttySource = #"""
        import Foundation
        import CGhostty

        enum CheckFailure: Error { case failed(String) }
        func check(_ condition: Bool, _ message: String) throws {
            guard condition else { throw CheckFailure.failed(message) }
        }
        final class Capture {
            var config: ghostty_config_t?
            func clear() { if let config { ghostty_config_free(config) }; config = nil }
            func accept(_ source: ghostty_config_t) { clear(); config = ghostty_config_clone(source) }
            func verify(background: UInt8, fontSize: Float) throws {
                guard let config else { throw CheckFailure.failed("missing config-change callback") }
                var color = ghostty_config_color_s()
                var size: Float = 0
                try check("background".withCString { ghostty_config_get(config, &color, $0, 10) }, "background getter")
                try check("font-size".withCString { ghostty_config_get(config, &size, $0, 9) }, "font-size getter")
                try check(color.r == background && color.g == background && color.b == background, "earlier background changed")
                try check(size == fontSize, "earlier defaults changed")
                try check(ghostty_config_diagnostics_count(config) == 0, "conditional replay diagnostics")
            }
            deinit { clear() }
        }
        @main struct GhosttyReplay {
            static func build(_ storage: GhosttyGeneratedConfigStorage, color: String, size: Int) throws -> ghostty_config_t {
                guard let config = ghostty_config_new() else { throw CheckFailure.failed("config allocation") }
                do {
                    try storage.withConfigFile("font-size = \(size)\n", as: .defaults) {
                        $0.withCString { ghostty_config_load_file(config, $0) }
                    }
                    let themes = try storage.appearanceThemes()
                    let contents = "theme = light:\(themes.light),dark:\(themes.dark)\nbackground = \(color)\ntitle = temporary\n"
                    try storage.withConfigFile(contents, as: .runtime) {
                        $0.withCString { ghostty_config_load_file(config, $0) }
                    }
                    ghostty_config_load_recursive_files(config)
                    try storage.withConfigFile("title =\n", as: .title) {
                        $0.withCString { ghostty_config_load_file(config, $0) }
                    }
                    ghostty_config_finalize(config)
                    try check(storage.retainedFileCount == 2, "ordinary bookkeeping retained")
                    try check(FileManager.default.contentsOfDirectory(atPath: storage.directoryPath).count == 2, "ordinary files retained")
                    try check(ghostty_config_diagnostics_count(config) == 0, "initial config diagnostics")
                    return config
                } catch { ghostty_config_free(config); throw error }
            }
            static func exercise(_ storage: GhosttyGeneratedConfigStorage) throws {
                let original = try build(storage, color: "#111111", size: 13)
                guard let old = ghostty_config_clone(original) else { throw CheckFailure.failed("clone allocation") }
                ghostty_config_free(original)
                defer { ghostty_config_free(old) }
                let latest = try build(storage, color: "#eeeeee", size: 19)
                defer { ghostty_config_free(latest) }
                for iteration in 0..<1_000 {
                    let temporary = try build(storage, color: iteration.isMultiple(of: 2) ? "#abcdef" : "#fedcba", size: 15)
                    ghostty_config_free(temporary)
                }
                let capture = Capture()
                var runtime = ghostty_runtime_config_s()
                runtime.userdata = Unmanaged.passUnretained(capture).toOpaque()
                runtime.wakeup_cb = { _ in }
                runtime.action_cb = { app, target, action in
                    if target.tag == GHOSTTY_TARGET_APP, action.tag == GHOSTTY_ACTION_CONFIG_CHANGE,
                       let app, let userdata = ghostty_app_userdata(app), let config = action.action.config_change.config {
                        Unmanaged<Capture>.fromOpaque(userdata).takeUnretainedValue().accept(config)
                    }
                    return true
                }
                runtime.read_clipboard_cb = { _, _, _, _, _, _ in GHOSTTY_CLIPBOARD_READ_UNSUPPORTED }
                runtime.confirm_read_clipboard_cb = { _, _, _, _ in }
                runtime.write_clipboard_cb = { _, _, _, _, _ in }
                runtime.close_surface_cb = { _, _ in }
                guard let app = ghostty_app_new(&runtime, old) else { throw CheckFailure.failed("app allocation") }
                defer { ghostty_app_free(app); capture.clear() }
                for dark in [true, false, true] {
                    ghostty_app_set_color_scheme(app, dark ? GHOSTTY_COLOR_SCHEME_DARK : GHOSTTY_COLOR_SCHEME_LIGHT)
                    ghostty_app_update_config(app, old)
                    try capture.verify(background: 0x11, fontSize: 13)
                    ghostty_app_update_config(app, latest)
                    try capture.verify(background: 0xee, fontSize: 19)
                    ghostty_app_update_config(app, old)
                    try capture.verify(background: 0x11, fontSize: 13)
                }
            }
            static func main() throws {
                try check(ghostty_init(0, nil) == 0, "ghostty initialization")
                let storage = try GhosttyGeneratedConfigStorage()
                defer { try? storage.cleanup() }
                try exercise(storage)
                // Every app/config owner has been freed; the immutable pair remains until cleanup.
                try check(storage.retainedFileCount == 2, "theme lifetime")
                FileHandle.standardOutput.write(Data("{\"rebuilds\":\"1000\",\"retained\":\"2\",\"replay\":\"old-new-old\"}\n".utf8))
                guard readLine() == "cleanup" else { throw CheckFailure.failed("cleanup barrier") }
                try storage.cleanup()
                try check(!FileManager.default.fileExists(atPath: storage.directoryPath), "cleanup directory")
                FileHandle.standardOutput.write(Data("cleaned\n".utf8))
            }
        }
        """#
    #endif

    /// Compile the production helper, not a reimplementation, into a separate process.
    private func compileChild(in root: URL, source: String, ghostty: Bool = false) throws -> URL {
        let package = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let helper = package.appendingPathComponent("Sources/AgtermLinux/GhosttyGeneratedConfigStorage.swift")
        let driver = root.appendingPathComponent("StorageChild.swift")
        try source.write(to: driver, atomically: true, encoding: .utf8)
        let executable = root.appendingPathComponent("storage-child")
        var arguments = ["swiftc", "-swift-version", "6", "-parse-as-library", helper.path, driver.path, "-o", executable.path]
        if ghostty {
            let vendor = package.appendingPathComponent("vendor/ghostty")
            let module = root.appendingPathComponent("CGhostty")
            try FileManager.default.createDirectory(at: module, withIntermediateDirectories: false)
            let map = "module CGhostty { header \"\(vendor.path)/include/ghostty.h\" link \"ghostty\" export * }"
            try map.write(to: module.appendingPathComponent("module.modulemap"), atomically: true, encoding: .utf8)
            arguments += ["-I", root.path, "-L", vendor.appendingPathComponent("lib").path,
                          "-Xlinker", "-rpath", "-Xlinker", vendor.appendingPathComponent("lib").path]
        }
        let compiler = Process()
        let output = Pipe()
        compiler.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        compiler.arguments = arguments
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
                let themes = try storage.appearanceThemes()
                var paths = ["lightTheme": themes.light, "darkTheme": themes.dark]
                func consume(_ kinds: ArraySlice<GhosttyGeneratedConfigStorage.Kind>) throws {
                    guard let kind = kinds.first else {
                        guard ProcessInfo.processInfo.environment == before else { exit(3) }
                        var payload = try JSONSerialization.data(withJSONObject: paths, options: [.sortedKeys])
                        payload.append(10)
                        FileHandle.standardOutput.write(payload)
                        guard readLine() == "cleanup" else { exit(4) }
                        return
                    }
                    try storage.withConfigFile("\(marker):\(kind.rawValue)", as: kind) { path in
                        paths[kind.rawValue] = path
                        try consume(kinds.dropFirst())
                    }
                }
                try consume(GhosttyGeneratedConfigStorage.Kind.allCases[...])
                guard storage.retainedFileCount == 2 else { exit(5) }
                try storage.cleanup()
                try storage.cleanup()
                do {
                    try storage.withConfigFile("after cleanup", as: .runtime) { _ in exit(6) }
                    exit(7)
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

    init(executable: URL, root: URL, marker: String, syntheticDesktop: Bool = true) throws {
        process.executableURL = executable
        process.arguments = [marker]
        var environment = ProcessInfo.processInfo.environment
        environment["XDG_RUNTIME_DIR"] = root.path
        if syntheticDesktop {
            environment["WAYLAND_DISPLAY"] = "disposable-display"
            environment["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=disposable-bus"
            environment["SHELL"] = "/disposable/login-shell"
        }
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
