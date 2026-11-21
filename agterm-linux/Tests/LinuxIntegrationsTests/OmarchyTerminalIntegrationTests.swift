import Foundation
import Testing
@testable import LinuxIntegrations

struct OmarchyTerminalIntegrationTests {
    @Test func enableAndDisableAreIdempotentAndRestoreExactUserBytes() throws {
        let fixture = try Fixture()
        defer { fixture.clean() }
        let initial = Data("#!/usr/bin/env lua\r\n-- custom config\r\nreturn { user = true }".utf8)
        try initial.write(to: fixture.config)
        try FileManager.default.setAttributes([.posixPermissions: 0o640], ofItemAtPath: fixture.config.path)

        #expect(try fixture.integration.status().state == .disabled)
        #expect(try fixture.integration.setEnabled(true).state == .enabled)
        let enabled = try Data(contentsOf: fixture.config)
        let generated = try String(contentsOf: fixture.module, encoding: .utf8)
        #expect(enabled.starts(with: Data("#!/usr/bin/env lua\r\n-- >>>".utf8)))
        #expect(generated.contains(#"class = [[^io\.github\.melonamin\.agterm$]]"#))
        #expect(generated.contains(#"tag = "+terminal""#))
        #expect(try fixture.mode(of: fixture.config) == 0o640)
        #expect(try fixture.mode(of: fixture.module) == 0o600)
        #expect(try fixture.integration.setEnabled(true).state == .enabled)
        #expect(try Data(contentsOf: fixture.config) == enabled)

        #expect(try fixture.integration.setEnabled(false).state == .disabled)
        #expect(try fixture.integration.setEnabled(false).state == .disabled)
        #expect(try Data(contentsOf: fixture.config) == initial)
        #expect(try fixture.mode(of: fixture.config) == 0o640)
        #expect(try FileManager.default.contentsOfDirectory(atPath: fixture.directory.path) == ["hyprland.lua"])
    }

    @Test func disableKeepsUserEditsAndCanCleanUpAnInterruptedEnable() throws {
        let fixture = try Fixture()
        defer { fixture.clean() }
        let initial = Data([0xef, 0xbb, 0xbf]) + Data("-- my config\n".utf8)
        try initial.write(to: fixture.config)

        _ = try fixture.integration.setEnabled(true)
        try FileManager.default.removeItem(at: fixture.module)
        #expect(try fixture.integration.status().state == .incomplete)
        #expect(try fixture.integration.setEnabled(true).state == .enabled)
        let extra = Data("-- edited while active\n".utf8)
        let currentlyEnabled = try Data(contentsOf: fixture.config)
        try (currentlyEnabled + extra).write(to: fixture.config)
        _ = try fixture.integration.setEnabled(false)
        #expect(try Data(contentsOf: fixture.config) == initial + extra)

        _ = try fixture.integration.setEnabled(true)
        try (initial + extra).write(to: fixture.config)
        #expect(try fixture.integration.status().state == .incomplete)
        #expect(try fixture.integration.setEnabled(false).state == .disabled)
        #expect(try Data(contentsOf: fixture.config) == initial + extra)
        #expect(!FileManager.default.fileExists(atPath: fixture.module.path))
    }

    @Test func conflictingFilesAndSymlinksArePreserved() throws {
        let fixture = try Fixture()
        defer { fixture.clean() }
        let initial = Data("-- custom config\n".utf8)
        try initial.write(to: fixture.config)
        let foreign = Data("-- user-owned module with unrelated content\n".utf8)
        try foreign.write(to: fixture.module)
        #expect(throws: (any Error).self) { try fixture.integration.setEnabled(true) }
        #expect(throws: (any Error).self) { try fixture.integration.setEnabled(false) }
        #expect(try Data(contentsOf: fixture.config) == initial)
        #expect(try Data(contentsOf: fixture.module) == foreign)

        try FileManager.default.removeItem(at: fixture.module)
        let stray = fixture.directory.appendingPathComponent("foreign.lua")
        try foreign.write(to: stray)
        try FileManager.default.createSymbolicLink(at: fixture.module, withDestinationURL: stray)
        #expect(throws: (any Error).self) { try fixture.integration.setEnabled(true) }
        #expect(try Data(contentsOf: stray) == foreign)
        #expect(try Data(contentsOf: fixture.config) == initial)
    }

    @Test func brokenIncludeDoesNotOverwriteTheUsersConfigOrRemoveTheModule() throws {
        let fixture = try Fixture()
        defer { fixture.clean() }
        try Data("-- custom config\n".utf8).write(to: fixture.config)
        _ = try fixture.integration.setEnabled(true)
        let module = try Data(contentsOf: fixture.module)
        let edited = try String(contentsOf: fixture.config, encoding: .utf8)
            .replacingOccurrences(of: "tag_rule()", with: "other_rule()")
        try Data(edited.utf8).write(to: fixture.config)
        #expect(throws: (any Error).self) { try fixture.integration.setEnabled(false) }
        #expect(try Data(contentsOf: fixture.config) == Data(edited.utf8))
        #expect(try Data(contentsOf: fixture.module) == module)
    }

    @Test func missingEntrypointReportsUnavailableWithoutCreatingIt() throws {
        let fixture = try Fixture()
        defer { fixture.clean() }
        let status = try fixture.integration.status()
        #expect(status.state == .unavailable)
        #expect(status.detail?.contains("config is missing") == true)
        #expect(throws: (any Error).self) { try fixture.integration.setEnabled(true) }
        #expect(try FileManager.default.contentsOfDirectory(atPath: fixture.directory.path).isEmpty)

        try FileManager.default.removeItem(at: fixture.directory)
        #expect(try fixture.integration.status().state == .unavailable)
        #expect(!FileManager.default.fileExists(atPath: fixture.directory.path))
    }

    @Test func symlinkedEntrypointReportsUnavailableAndPreservesTheTarget() throws {
        let fixture = try Fixture()
        defer { fixture.clean() }
        let target = fixture.directory.appendingPathComponent("user.lua")
        let initial = Data("-- user config\n".utf8)
        try initial.write(to: target)
        try FileManager.default.createSymbolicLink(at: fixture.config, withDestinationURL: target)

        let status = try fixture.integration.status()
        #expect(status.state == .unavailable)
        #expect(status.detail?.contains("config is a symbolic link") == true)
        #expect(throws: (any Error).self) { try fixture.integration.setEnabled(true) }
        #expect(throws: (any Error).self) { try fixture.integration.setEnabled(false) }
        #expect(try FileManager.default.destinationOfSymbolicLink(atPath: fixture.config.path) == target.path)
        #expect(try Data(contentsOf: target) == initial)
        #expect(!FileManager.default.fileExists(atPath: fixture.module.path))
    }

    @Test(arguments: ["# first line\n", "# first line"])
    func homeFallbackPreservesLuaHashHeader(header: String) throws {
        let fixture = try Fixture(useXDG: false)
        defer { fixture.clean() }
        let initial = Data(header.utf8)
        try initial.write(to: fixture.config)

        let status = try fixture.integration.setEnabled(true)
        #expect(status.state == .enabled)
        #expect(status.configPath == fixture.root.appendingPathComponent(".config/hypr/hyprland.lua").path)
        let config = try String(contentsOf: fixture.config, encoding: .utf8)
        #expect(config.split(separator: "\n").first == "# first line")
        #expect(config.contains("-- >>> agterm Omarchy terminal tag >>>"))
        #expect(try fixture.integration.status().state == .enabled)
        #expect(try fixture.integration.setEnabled(false).state == .disabled)
        #expect(try Data(contentsOf: fixture.config) == initial)
    }

    private struct Fixture {
        let root: URL
        let directory: URL
        let integration: OmarchyTerminalIntegration
        var config: URL { directory.appendingPathComponent("hyprland.lua") }
        var module: URL { directory.appendingPathComponent("agterm-omarchy.lua") }

        init(useXDG: Bool = true) throws {
            root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
            let configRoot = root.appendingPathComponent(useXDG ? "xdg config" : ".config", isDirectory: true)
            directory = configRoot.appendingPathComponent("hypr", isDirectory: true)
            integration = OmarchyTerminalIntegration(environment: IntegrationEnvironment(
                homeDirectory: root, executableURL: root.appendingPathComponent("agtermctl"),
                pathDirectories: [], resourceRoot: nil, xdgConfigHome: useXDG ? configRoot.path : nil))
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        }

        func mode(of url: URL) throws -> Int? {
            (try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? NSNumber)?.intValue
        }

        func clean() { try? FileManager.default.removeItem(at: root) }
    }
}
