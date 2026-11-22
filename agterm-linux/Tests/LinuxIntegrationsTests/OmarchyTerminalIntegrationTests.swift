import Foundation
import Testing
@testable import LinuxIntegrations

struct OmarchyTerminalIntegrationTests {
    @Test func repeatedEnableRestoresTheOriginalTerminalListAndMode() throws {
        let fixture = try Fixture()
        defer { fixture.clean() }
        try Data("-- user config\n".utf8).write(to: fixture.config)
        let original = Data([0xef, 0xbb, 0xbf]) + Data("# preferred terminal\r\nfoot.desktop\r\nkitty.desktop".utf8)
        try original.write(to: fixture.terminalList)
        try FileManager.default.setAttributes([.posixPermissions: 0o640], ofItemAtPath: fixture.terminalList.path)

        _ = try fixture.integration.setEnabled(true)
        let enabled = try Data(contentsOf: fixture.terminalList)
        #expect(enabled.starts(with: Data("io.github.melonamin.agterm.desktop\n".utf8)))
        #expect(enabled.suffix(original.count) == original)
        #expect(try fixture.mode(of: fixture.terminalList) == 0o640)
        _ = try fixture.integration.setEnabled(true)
        #expect(try Data(contentsOf: fixture.terminalList) == enabled)

        _ = try fixture.integration.setEnabled(false)
        _ = try fixture.integration.setEnabled(false)
        #expect(try Data(contentsOf: fixture.terminalList) == original)
        #expect(try fixture.mode(of: fixture.terminalList) == 0o640)
        #expect(try FileManager.default.contentsOfDirectory(atPath: fixture.omarchyConfig.path) == ["xdg-terminals.list"])
    }

    @Test func disableRemovesOnlyATerminalListThatEnableCreated() throws {
        let fixture = try Fixture()
        defer { fixture.clean() }
        try Data("-- user config\n".utf8).write(to: fixture.config)
        let fm = FileManager.default
        #expect(!fm.fileExists(atPath: fixture.terminalList.path))

        _ = try fixture.integration.setEnabled(true)
        #expect(fm.fileExists(atPath: fixture.terminalList.path))
        _ = try fixture.integration.setEnabled(false)
        #expect(!fm.fileExists(atPath: fixture.terminalList.path))
        #expect(try fm.contentsOfDirectory(atPath: fixture.omarchyConfig.path).isEmpty)

        let alreadySelected = Data("io.github.melonamin.agterm.desktop\nfoot.desktop".utf8)
        try alreadySelected.write(to: fixture.terminalList)
        _ = try fixture.integration.setEnabled(true)
        _ = try fixture.integration.setEnabled(false)
        #expect(try Data(contentsOf: fixture.terminalList) == alreadySelected)
    }

    @Test func disablePreservesAListEditedWhileEnabledAndReportsTheConflict() throws {
        let fixture = try Fixture()
        defer { fixture.clean() }
        let lua = Data("-- user config\n".utf8)
        try lua.write(to: fixture.config)
        try Data("foot.desktop\n".utf8).write(to: fixture.terminalList)
        _ = try fixture.integration.setEnabled(true)

        let edited = Data("kitty.desktop\nfoot.desktop\n".utf8)
        try edited.write(to: fixture.terminalList)
        let disabled = try fixture.integration.setEnabled(false)
        let report = String(decoding: try JSONEncoder().encode(disabled), as: UTF8.self)
        #expect(report.contains("conflict"))
        #expect(try Data(contentsOf: fixture.terminalList) == edited)
        #expect(try Data(contentsOf: fixture.config) == lua)
        #expect(!FileManager.default.fileExists(atPath: fixture.module.path))
    }

    @Test func statusAndEnableReportEarlierDesktopAndCLIWithoutEditingThem() throws {
        let fixture = try Fixture()
        defer { fixture.clean() }
        try Data("-- user config\n".utf8).write(to: fixture.config)
        let packageRoot = fixture.root.appendingPathComponent("usr")
        let oldRoot = fixture.root.appendingPathComponent("old")
        let desktopName = "applications/io.github.melonamin.agterm.desktop"
        let packageDesktop = packageRoot.appendingPathComponent("share/\(desktopName)")
        let oldDesktop = oldRoot.appendingPathComponent("share/\(desktopName)")
        let packageCLI = packageRoot.appendingPathComponent("bin/agtermctl")
        let oldCLI = oldRoot.appendingPathComponent("bin/agtermctl")
        let oldBytes = Data("older build".utf8)
        let fm = FileManager.default
        for file in [packageDesktop, oldDesktop, packageCLI, oldCLI] {
            try fm.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            try oldBytes.write(to: file)
            try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: file.path)
        }
        let directoryOnPATH = fixture.root.appendingPathComponent("not-a-cli").appendingPathComponent("agtermctl")
        try fm.createDirectory(at: directoryOnPATH, withIntermediateDirectories: true)
        let dataDirs = [oldRoot, packageRoot].map { $0.appendingPathComponent("share").path }.joined(separator: ":")
        let integration = OmarchyTerminalIntegration(environment: IntegrationEnvironment(
            homeDirectory: fixture.root, executableURL: packageCLI,
            pathDirectories: [directoryOnPATH.deletingLastPathComponent(), oldCLI.deletingLastPathComponent(), packageCLI.deletingLastPathComponent()],
            resourceRoot: nil, knownCommandLineTools: [packageCLI], xdgConfigHome: fixture.directory.deletingLastPathComponent().path,
            probeEnvironment: ["XDG_DATA_DIRS": dataDirs]))

        for status in [try integration.status(), try integration.setEnabled(true)] {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.withoutEscapingSlashes]
            let report = String(decoding: try encoder.encode(status), as: UTF8.self)
            #expect(report.contains("shadows"))
            #expect(report.contains(oldDesktop.path))
            #expect(report.contains(oldCLI.path))
        }
        #expect(try Data(contentsOf: oldDesktop) == oldBytes)
        #expect(try Data(contentsOf: oldCLI) == oldBytes)
    }

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

    @Test func disablePreservesUserCodeAppendedAfterAnUnterminatedHashHeader() throws {
        let fixture = try Fixture()
        defer { fixture.clean() }
        try Data("# h".utf8).write(to: fixture.config)
        _ = try fixture.integration.setEnabled(true)

        let enabled = try Data(contentsOf: fixture.config)
        let userCode = "require(\"user.config\")\n"
        try (enabled + Data(userCode.utf8)).write(to: fixture.config)
        _ = try fixture.integration.setEnabled(false)

        let restored = try String(contentsOf: fixture.config, encoding: .utf8)
        #expect(restored == "# h\n" + userCode)
    }

    private struct Fixture {
        let root: URL
        let directory: URL
        let integration: OmarchyTerminalIntegration
        var config: URL { directory.appendingPathComponent("hyprland.lua") }
        var module: URL { directory.appendingPathComponent("agterm-omarchy.lua") }
        var omarchyConfig: URL { root.appendingPathComponent(".config", isDirectory: true) }
        var terminalList: URL { omarchyConfig.appendingPathComponent("xdg-terminals.list") }

        init(useXDG: Bool = true) throws {
            root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
            let configRoot = root.appendingPathComponent(useXDG ? "xdg config" : ".config", isDirectory: true)
            directory = configRoot.appendingPathComponent("hypr", isDirectory: true)
            integration = OmarchyTerminalIntegration(environment: IntegrationEnvironment(
                homeDirectory: root, executableURL: root.appendingPathComponent("agtermctl"),
                pathDirectories: [], resourceRoot: nil, xdgConfigHome: useXDG ? configRoot.path : nil))
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try FileManager.default.createDirectory(at: omarchyConfig, withIntermediateDirectories: true)
        }

        func mode(of url: URL) throws -> Int? {
            (try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? NSNumber)?.intValue
        }

        func clean() { try? FileManager.default.removeItem(at: root) }
    }
}
