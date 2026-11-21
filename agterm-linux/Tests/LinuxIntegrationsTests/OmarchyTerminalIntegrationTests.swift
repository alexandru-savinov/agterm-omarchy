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

    private struct Fixture {
        let root: URL
        let directory: URL
        let integration: OmarchyTerminalIntegration
        var config: URL { directory.appendingPathComponent("hyprland.lua") }
        var module: URL { directory.appendingPathComponent("agterm-omarchy.lua") }

        init() throws {
            root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
            let customConfig = root.appendingPathComponent("xdg config", isDirectory: true)
            directory = customConfig.appendingPathComponent("hypr", isDirectory: true)
            integration = OmarchyTerminalIntegration(environment: IntegrationEnvironment(
                homeDirectory: root, executableURL: root.appendingPathComponent("agtermctl"),
                pathDirectories: [], resourceRoot: nil, xdgConfigHome: customConfig.path))
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        }

        func mode(of url: URL) throws -> Int? {
            (try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? NSNumber)?.intValue
        }

        func clean() { try? FileManager.default.removeItem(at: root) }
    }
}
