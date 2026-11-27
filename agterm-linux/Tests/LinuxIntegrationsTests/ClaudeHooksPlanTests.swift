import Foundation
import Testing
@testable import LinuxIntegrations

@Suite("Claude-only lifecycle hook planner")
struct ClaudeHooksPlanTests {
    @Test(arguments: ["", "{}", ClaudeFixture.herdr, ClaudeFixture.custom])
    func preservesSettingsAndIsIdempotent(original: String) throws {
        let fixture = try ClaudeFixture()
        try fixture.write(original, to: fixture.settings)
        let service = fixture.service
        #expect(try service.apply(service.planClaudeHooks()).succeeded)
        let installed = try fixture.contents(fixture.settings)
        let backup = fixture.settings.appendingPathExtension("bak")
        #expect(try fixture.contents(backup) == original)
        for event in ["PermissionRequest", "PostToolUse", "Stop", "Notification", "UserPromptSubmit"] {
            let command = try fixture.packagedCommand(event)
            #expect(command.contains("agterm-agent-status' ]"))
        }
        if original == ClaudeFixture.herdr {
            #expect(installed.contains("herdr hook claude stop"))
        }
        if original == ClaudeFixture.custom {
            #expect(installed.contains("custom-logger --audit"))
            #expect(installed.contains("customSetting"))
        }
        #expect(service.planClaudeHooks().steps.isEmpty)
        #expect(try fixture.contents(fixture.settings) == installed)
        #expect(try fixture.contents(backup) == original)
    }

    @Test func honorsClaudeConfigDir() throws {
        let fixture = try ClaudeFixture(useOverride: true)
        try fixture.write(ClaudeFixture.herdr, to: fixture.settings)
        try fixture.write(ClaudeFixture.custom, to: fixture.defaultSettings)
        let service = fixture.service
        #expect(try service.apply(service.planClaudeHooks()).succeeded)
        #expect(try fixture.contents(fixture.defaultSettings) == ClaudeFixture.custom)
        #expect(try fixture.contents(fixture.settings.appendingPathExtension("bak")) == ClaudeFixture.herdr)
        #expect(try fixture.packagedCommand("Stop").contains("completed --auto-reset"))
        #expect(try service.apply(service.planClaudeHooks(install: false)).succeeded)
        #expect(try fixture.contents(fixture.defaultSettings) == ClaudeFixture.custom)
        #expect(try fixture.object(fixture.settings).isEqual(try fixture.object(ClaudeFixture.herdr)))
    }

    @Test(arguments: [ClaudeFixture.herdr, ClaudeFixture.custom])
    func uninstallPreservesOtherSettings(original: String) throws {
        let fixture = try ClaudeFixture()
        try fixture.write(original, to: fixture.settings)
        let service = fixture.service
        #expect(try service.apply(service.planClaudeHooks()).succeeded)
        #expect(try service.apply(service.planClaudeHooks(install: false)).succeeded)
        #expect(try fixture.object(fixture.settings).isEqual(try fixture.object(original)))
        #expect(try fixture.contents(fixture.settings.appendingPathExtension("bak")) == original)
        #expect(try service.apply(service.planClaudeHooks()).succeeded)
        #expect(try fixture.contents(fixture.settings.appendingPathExtension("bak")) == original)
        #expect(try service.apply(service.planClaudeHooks(install: false)).succeeded)
        #expect(!service.planClaudeHooks(install: false).canApply)
    }

    @Test(arguments: ["{}", "{\"Stop\":[],\"OtherEvent\":[]}"])
    func writesSortedSettingsAndRestoresEmptyHookContainers(hooks: String) throws {
        let fixture = try ClaudeFixture()
        let original = """
        {"zulu":[],"hooks":\(hooks),"alpha":{"zulu":true,"alpha":[]}}
        """
        try fixture.write(original, to: fixture.settings)
        let service = fixture.service
        for install in [true, false] {
            #expect(try service.apply(service.planClaudeHooks(install: install)).succeeded)
            let object = try fixture.object(fixture.settings)
            let sorted = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
            #expect(try fixture.contents(fixture.settings) == String(decoding: sorted, as: UTF8.self) + "\n")
        }
        #expect(try fixture.object(fixture.settings).isEqual(try fixture.object(original)))
    }

    @Test func uninstallBacksUpUserEditsWhenActivationCreatedSettings() throws {
        let fixture = try ClaudeFixture()
        let fm = FileManager.default
        let backup = fixture.settings.appendingPathExtension("bak")
        try fm.createDirectory(at: fixture.settings.deletingLastPathComponent(), withIntermediateDirectories: true)
        let service = fixture.service
        #expect(try service.apply(service.planClaudeHooks()).succeeded)
        #expect(!fm.fileExists(atPath: backup.path))
        let installed = try fixture.contents(fixture.settings)
        let edited = "{\"userSetting\":true,\"unrelated\":[]," + installed.dropFirst()
        try fixture.write(edited, to: fixture.settings)
        #expect(try service.apply(service.planClaudeHooks(install: false)).succeeded)
        #expect(try fixture.contents(backup) == edited)
        let remaining = try fixture.object("{\"userSetting\":true,\"unrelated\":[]}")
        #expect(try fixture.object(fixture.settings).isEqual(remaining))
        #expect(!service.planClaudeHooks(install: false).canApply)
        #expect(try service.apply(service.planClaudeHooks()).succeeded)
        #expect(try service.apply(service.planClaudeHooks(install: false)).succeeded)
        #expect(try fixture.contents(backup) == edited)
    }

    @Test(arguments: ["file", "profile", "regular"])
    func privateSettingsAndBackupKeepModeAndSymlinks(alias: String) throws {
        let fixture = try ClaudeFixture()
        let fm = FileManager.default
        let target = alias == "regular" ? fixture.settings : fixture.home.appendingPathComponent("shared/settings.json")
        try fixture.write(ClaudeFixture.herdr, to: target)
        try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: target.path)
        if alias == "file" {
            try fm.createDirectory(at: fixture.settings.deletingLastPathComponent(), withIntermediateDirectories: true)
            try fm.createSymbolicLink(at: fixture.settings, withDestinationURL: target)
        } else if alias == "profile" {
            try fm.createSymbolicLink(at: fixture.settings.deletingLastPathComponent(),
                                     withDestinationURL: target.deletingLastPathComponent())
        }
        let service = fixture.service
        let backup = target.appendingPathExtension("bak")
        for install in [true, false] {
            #expect(try service.apply(service.planClaudeHooks(install: install)).succeeded)
            for file in [target, backup] {
                let attributes = try fm.attributesOfItem(atPath: file.path)
                #expect(attributes[.posixPermissions] as? Int == 0o600)
            }
            #expect(try fixture.contents(backup) == ClaudeFixture.herdr)
            #expect(try fixture.object(target).isEqual(try fixture.object(ClaudeFixture.herdr)) == !install)
        }
        if alias != "regular" {
            let link = alias == "file" ? fixture.settings : fixture.settings.deletingLastPathComponent()
            let destination = alias == "file" ? target : target.deletingLastPathComponent()
            #expect(try fm.destinationOfSymbolicLink(atPath: link.path) == destination.path)
        }
    }

    @Test func packageAndPersonalHooksAreDeduplicatedAndRemoved() throws {
        let fixture = try ClaudeFixture()
        let service = fixture.service
        let packageStop = "[ -x '/usr/bin/agterm-agent-status' ] || exit 0; exec '/usr/bin/agterm-agent-status' completed --auto-reset"
        let otherLauncher = "/opt/person' personal/lib/agterm/bin/agterm-agent-status"
        let quotedOther = "'/opt/person'\\'' personal/lib/agterm/bin/agterm-agent-status'"
        let otherStop = "[ -x \(quotedOther) ] || exit 0; exec \(quotedOther) completed --auto-reset"
        let custom = "echo \(otherLauncher)"
        let object = ["hooks": ["Stop": [["hooks": [packageStop, otherStop, custom].map {
            ["type": "command", "command": $0]
        }]]]]
        let data = try JSONSerialization.data(withJSONObject: object)
        try fixture.write(String(decoding: data, as: UTF8.self), to: fixture.settings)
        #expect(try service.apply(service.planClaudeHooks()).succeeded)
        let installed = try fixture.contents(fixture.settings)
        #expect(!installed.contains("/usr/bin/agterm-agent-status"))
        #expect(try fixture.commands("Stop").count == 2)
        #expect(!service.planClaudeHooks().canApply)
        #expect(try service.apply(service.planClaudeHooks(install: false)).succeeded)
        #expect(try fixture.commands("Stop") == [custom])

        try fixture.write(String(decoding: data, as: UTF8.self), to: fixture.settings)
        #expect(try service.apply(service.planClaudeHooks(install: false)).succeeded)
        #expect(try fixture.commands("Stop") == [custom])
    }

    @Test func missingProfileWaitsForClaude() throws {
        let fixture = try ClaudeFixture()
        let service = fixture.service
        #expect(!service.planClaudeHooks().canApply)
        #expect(!service.planClaudeHooks().warnings.isEmpty)
        let profile = fixture.settings.deletingLastPathComponent()
        #expect(!FileManager.default.fileExists(atPath: profile.path))
        try FileManager.default.createDirectory(at: profile, withIntermediateDirectories: true)
        #expect(try service.apply(service.planClaudeHooks()).succeeded)
        #expect(try fixture.packagedCommand("PermissionRequest").hasSuffix(" blocked"))
        #expect(!FileManager.default.fileExists(atPath: fixture.settings.appendingPathExtension("bak").path))
    }

    @Test func migratesStockLegacyHooksAndKeepsSiblingCommands() throws {
        let fixture = try ClaudeFixture()
        let service = fixture.service
        let legacy = service.environment.hooksDirectory.appendingPathComponent("agterm-claude-status.sh").path
        let settings: [String: Any] = [
            "hooks": [
                "Stop": [
                    ["hooks": [
                        ["type": "command", "command": "'\(legacy)' completed --auto-reset"],
                        ["type": "command", "command": "custom --audit"],
                        ["type": "command", "command": "'\(legacy)' completed --auto-reset", "timeout": 10],
                    ]],
                ],
            ],
        ]
        let data = try JSONSerialization.data(withJSONObject: settings)
        try fixture.write(String(decoding: data, as: UTF8.self), to: fixture.settings)
        #expect(try service.apply(service.planClaudeHooks()).succeeded)
        #expect(try fixture.packagedCommand("Stop").hasPrefix("[ -x "))
        #expect(try service.apply(service.planClaudeHooks(install: false)).succeeded)
        let hooks = try #require(try fixture.object(fixture.settings)["hooks"] as? [String: [[String: Any]]])
        let entry = try #require(hooks["Stop"]?.first)
        let commands = try #require(entry["hooks"] as? [[String: Any]])
        #expect(commands.count == 2)
        #expect(commands[0]["command"] as? String == "custom --audit")
        #expect(commands[1]["timeout"] as? Int == 10)
    }

    @Test func removedBinaryIsQuiet() throws {
        let fixture = try ClaudeFixture()
        try fixture.write("{}", to: fixture.settings)
        let service = fixture.service
        let wrapper = fixture.cli.deletingLastPathComponent().appendingPathComponent("agterm-agent-status")
        let marker = fixture.home.appendingPathComponent("wrapper-called")
        try fixture.write("#!/bin/sh\n: > \"$HOME/wrapper-called\"\n", to: wrapper)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: wrapper.path)
        #expect(try service.apply(service.planClaudeHooks()).succeeded)
        let command = try fixture.packagedCommand("Stop")
        try replay(command, home: fixture.home)
        #expect(FileManager.default.fileExists(atPath: marker.path))
        try FileManager.default.removeItem(at: marker)
        try FileManager.default.removeItem(at: wrapper)
        try replay(command, home: fixture.home)
        #expect(!FileManager.default.fileExists(atPath: marker.path))
    }

    private func replay(_ command: String, home: URL) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = ["-c", command]
        process.environment = ["HOME": home.path, "PATH": "/usr/bin:/bin"]
        let output = Pipe()
        process.standardOutput = output
        process.standardError = output
        try process.run()
        process.waitUntilExit()
        #expect(process.terminationStatus == 0)
        #expect(output.fileHandleForReading.readDataToEndOfFile().isEmpty)
    }
}

private final class ClaudeFixture {
    static let herdr = """
    {"hooks":{"Stop":[{"hooks":[{"type":"command","command":"herdr hook claude stop"}]}]}}
    """
    static let custom = """
    {"customSetting":true,"hooks":{"Stop":[{"matcher":"Bash","hooks":[
        {"type":"command","command":"custom-logger --audit","timeout":10},
        {"type":"command","command":"echo /usr/bin/agterm-agent-status"}
    ]}],"OtherEvent":[{"custom":true}]}}
    """

    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    var home: URL { root.appendingPathComponent("home") }
    var defaultSettings: URL { home.appendingPathComponent(".claude/settings.json") }
    var settings: URL { useOverride ? home.appendingPathComponent("Claude Profile/settings.json") : defaultSettings }
    let useOverride: Bool
    let cli: URL

    init(useOverride: Bool = false) throws {
        self.useOverride = useOverride
        cli = root.appendingPathComponent("prefix with ' quote/lib/agterm/bin/agtermctl")
        try write("#!/bin/sh\nexit 0\n", to: cli)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: cli.path)
        let wrapper = cli.deletingLastPathComponent().appendingPathComponent("agterm-agent-status")
        try write("#!/bin/sh\nexit 0\n", to: wrapper)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: wrapper.path)
    }

    deinit { try? FileManager.default.removeItem(at: root) }

    var service: IntegrationService {
        let repository = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        var environment = ["HOME": home.path, "PATH": "/usr/bin:/bin"]
        if useOverride { environment["CLAUDE_CONFIG_DIR"] = settings.deletingLastPathComponent().path }
        return IntegrationService(environment: IntegrationEnvironment(
            homeDirectory: home, executableURL: cli, pathDirectories: [],
            resourceRoot: repository.appendingPathComponent("agterm/Resources"),
            probeEnvironment: environment
        ), skipOpenCode: true)
    }

    func write(_ contents: String, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try contents.write(to: url, atomically: true, encoding: .utf8)
    }

    func contents(_ url: URL) throws -> String {
        try String(contentsOf: url, encoding: .utf8)
    }

    func object(_ url: URL) throws -> NSDictionary { try object(contents(url)) }

    func object(_ text: String) throws -> NSDictionary {
        try #require(JSONSerialization.jsonObject(with: Data(text.utf8)) as? NSDictionary)
    }

    func packagedCommand(_ event: String) throws -> String {
        try #require(try commands(event).first { $0.hasPrefix("[ -x ") && $0.contains("agterm-agent-status") })
    }

    func commands(_ event: String) throws -> [String] {
        let data = try Data(contentsOf: settings)
        let root = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let hooks = try #require(root["hooks"] as? [String: [[String: Any]]])
        let commands = hooks[event, default: []].flatMap { $0["hooks"] as? [[String: Any]] ?? [] }
        return commands.compactMap { $0["command"] as? String }
    }
}
