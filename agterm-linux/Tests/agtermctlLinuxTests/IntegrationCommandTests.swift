import ArgumentParser
import Foundation
import Testing
@testable import agtermctlLinux

@Suite("agtermctl integration command")
struct IntegrationCommandTests {
    @Test(arguments: ["enable", "status", "disable"])
    func omarchyActionsParse(action: String) throws {
        let command = try AgtermctlLinux.parseAsRoot(["integration", "omarchy", action, "--json"])
        let omarchy = try #require(command as? Integration.Omarchy)
        #expect(omarchy.action.rawValue == action)
        #expect(omarchy.json)
    }

    @Test func omarchyTextReportsConfigAndRefreshRecovery() throws {
        let fixture = try CLIFixture(copyResources: false)
        let config = fixture.home.appendingPathComponent(".config/hypr/hyprland.lua")
        let command = ["integration", "omarchy"]
        let missing = try fixture.run(command + ["status"])
        #expect(missing.status == 0)
        #expect(missing.output.contains("Omarchy terminal tag: unavailable"))
        #expect(missing.output.contains("Hyprland Lua config is missing: \(config.path)"))
        #expect(missing.error.isEmpty)

        let configText = "-- default config\n"
        try fixture.write(configText, to: config)
        let enabled = try fixture.run(command + ["enable"])
        #expect(enabled.status == 0)
        #expect(enabled.output.contains("Omarchy terminal tag: enabled"))
        #expect(enabled.output.contains("Hyprland config: \(config.path)"))
        let module = config.deletingLastPathComponent().appendingPathComponent("agterm-omarchy.lua")
        #expect(enabled.output.contains("Rule module: \(module.path)"))
        let list = fixture.home.appendingPathComponent(".config/xdg-terminals.list")
        #expect(enabled.output.contains("Default terminal list: \(list.path)"))
        #expect(enabled.output.contains("First entry: io.github.melonamin.agterm.desktop"))

        try fixture.write(configText, to: config)
        let refreshed = try fixture.run(command + ["status"])
        #expect(refreshed.status == 0)
        #expect(refreshed.output.contains("Omarchy terminal tag: incomplete"))
        #expect(try fixture.run(command + ["enable"]).output.contains("Omarchy terminal tag: enabled"))
        let disabled = try fixture.run(command + ["disable"])
        #expect(disabled.status == 0)
        #expect(!FileManager.default.fileExists(atPath: list.path))
    }

    @Test func omarchyEnableWarnsWhenPATHSelectsADifferentCLI() throws {
        let fixture = try CLIFixture(copyResources: false)
        try fixture.write("-- user config\n", to: fixture.home.appendingPathComponent(".config/hypr/hyprland.lua"))
        let oldCLI = fixture.home.appendingPathComponent(".local/bin/agtermctl")
        try fixture.write("#!/bin/sh\nexit 97\n", to: oldCLI)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: oldCLI.path)
        let command = ["integration", "omarchy"]
        let path = oldCLI.deletingLastPathComponent().path

        let status = try fixture.run(command + ["status"], path: path)
        #expect(status.status == 0)
        #expect(status.output.contains("Warning:"))
        #expect(status.output.contains(oldCLI.path))
        let enabled = try fixture.run(command + ["enable"], path: path)
        #expect(enabled.status == 0)
        #expect(enabled.error.contains("Warning:"))
        #expect(enabled.error.contains(oldCLI.path))
        #expect(try String(contentsOf: oldCLI, encoding: .utf8) == "#!/bin/sh\nexit 97\n")
    }

    @Test("status accepts the control socket compatibility option")
    func statusParsing() throws {
        let command = try AgtermctlLinux.parseAsRoot([
            "integration", "status", "--json", "--socket", "/tmp/ignored.sock",
        ])
        let status = try #require(command as? Integration.Status)
        #expect(status.socket == "/tmp/ignored.sock")
    }

    @Test("install supports hooks dry-run")
    func hooksDryRunParsing() throws {
        let command = try AgtermctlLinux.parseAsRoot([
            "integration", "install", "hooks", "--dry-run", "--socket", "/tmp/ignored.sock",
        ])
        let install = try #require(command as? Integration.Install)
        #expect(install.integration == "hooks")
        #expect(install.dryRun)
        #expect(install.socket == "/tmp/ignored.sock")
    }

    @Test("install rejects unknown integration")
    func invalidIntegration() throws {
        #expect(throws: Error.self) {
            _ = try AgtermctlLinux.parseAsRoot(["integration", "install", "unknown"])
        }
    }

    @Test("Claude-only CLI activation changes only Claude settings and backup")
    func claudeOnlyProcess() throws {
        let fixture = try CLIFixture()
        let original = "{\"hooks\":{},\"customSetting\":true}\n"
        let settings = ".claude/settings.json"
        let backup = settings + ".bak"
        for path in [".zshrc", ".bashrc", ".config/fish/config.fish", ".codex/config.toml",
                     ".pi/agent/settings.json", ".config/opencode/opencode.json", settings] {
            try fixture.write(path == settings ? original : "fixture for \(path)\n",
                              to: fixture.home.appendingPathComponent(path))
        }
        let before = try fixture.homeSnapshot()
        let install = ["integration", "install", "hooks", "--only", "claude"]
        let result = try fixture.run(install)
        #expect(result.status == 0, Comment(rawValue: result.error))
        let after = try fixture.homeSnapshot()
        #expect(Set(after.keys).subtracting(before.keys) == [backup])
        #expect(after.filter { $0.key != settings && $0.key != backup } == before.filter { $0.key != settings })
        #expect(after[settings] != before[settings])
        #expect(after[backup] == before[settings])
        #expect(try fixture.run(install).status == 0)
        #expect(try fixture.homeSnapshot() == after)

        let uninstall = ["integration", "uninstall", "hooks", "--only", "claude"]
        let removed = try fixture.run(uninstall)
        #expect(removed.status == 0, Comment(rawValue: removed.error))
        let disabled = try fixture.homeSnapshot()
        #expect(disabled.filter { $0.key != settings && $0.key != backup } == before.filter { $0.key != settings })
        #expect(disabled[backup] == before[settings])
        let json = try #require(disabled[settings])
        let object = try #require(JSONSerialization.jsonObject(with: json) as? [String: Any])
        #expect(object["customSetting"] as? Bool == true)
        #expect((object["hooks"] as? [String: Any])?.isEmpty == true)
        #expect(try fixture.run(uninstall).status == 0)
        #expect(try fixture.homeSnapshot() == disabled)
    }

    @Test func claudeInstallRefusesMissingOrNonExecutableLauncher() throws {
        let fixture = try CLIFixture()
        let settings = fixture.home.appendingPathComponent(".claude/settings.json")
        try fixture.write("{\"legacy\":true}", to: settings)
        let before = try fixture.homeSnapshot()
        let statusLauncher = try fixture.claudeCLI().deletingLastPathComponent().appendingPathComponent("agterm-agent-status")
        try FileManager.default.removeItem(at: statusLauncher)
        let install = ["integration", "install", "hooks", "--only", "claude"]
        for present in [false, true] {
            if present { try fixture.write("not executable", to: statusLauncher) }
            let result = try fixture.run(install, executable: fixture.claudeCLI(createLauncher: false))
            #expect(result.status != 0)
            #expect(result.error.contains("not executable"))
            #expect(!result.output.contains("OK"))
            #expect(try fixture.homeSnapshot() == before)
        }
    }

    @Test("Claude-only CLI uses the selected profile and waits for it to exist")
    func claudeProfileProcess() throws {
        let fixture = try CLIFixture()
        let profile = fixture.home.appendingPathComponent("Claude Profile")
        let install = ["integration", "install", "hooks", "--only", "claude"]
        let missing = try fixture.run(install, claudeConfig: profile)
        #expect(missing.status == 0)
        #expect(missing.output.contains("Re-run after it exists"))
        #expect(try fixture.homeSnapshot().isEmpty)

        let settings = profile.appendingPathComponent("settings.json")
        try fixture.write("{}", to: settings)
        #expect(try fixture.run(install, claudeConfig: profile).status == 0)
        #expect(try String(contentsOf: settings, encoding: .utf8).contains("PermissionRequest"))
        #expect(!FileManager.default.fileExists(atPath: fixture.home.appendingPathComponent(".claude").path))
    }

    @Test("status JSON is stable and independent of the control socket")
    func statusJSONProcess() throws {
        let fixture = try CLIFixture()
        let result = try fixture.run(["integration", "status", "--json"])
        #expect(result.status == 0)
        let object = try #require(JSONSerialization.jsonObject(with: Data(result.output.utf8)) as? [String: Any])
        let items = try #require(object["items"] as? [[String: Any]])
        #expect(items.count == 6)
        #expect(items.compactMap { $0["kind"] as? String } == [
            "cli", "claude-hooks", "codex-hooks", "pi-hooks", "opencode-plugin", "agent-skill",
        ])
        #expect(!result.output.contains("socket"))

        let text = try fixture.run(["integration", "status"])
        #expect(text.status == 0)
        #expect(text.output.contains("Pi Extension: Unavailable"))
    }

    @Test("dry-run JSON previews without mutating HOME")
    func dryRunProcess() throws {
        let fixture = try CLIFixture()
        let result = try fixture.run(["integration", "install", "skill", "--dry-run", "--json"])
        #expect(result.status == 0)
        let object = try #require(JSONSerialization.jsonObject(with: Data(result.output.utf8)) as? [String: Any])
        #expect(object["kind"] as? String == "skill")
        #expect(object["canApply"] as? Bool == true)
        #expect(!FileManager.default.fileExists(atPath: fixture.home.appendingPathComponent(".claude").path))
    }

    @Test("conflicts, invalid resources, and failed writes have distinct exits")
    func processExitStatuses() throws {
        let conflict = try CLIFixture()
        let skill = conflict.home.appendingPathComponent(".codex/skills/agterm/SKILL.md")
        try conflict.write("unrelated", to: skill)
        let conflictResult = try conflict.run([
            "integration", "install", "skill", "--dry-run", "--json",
        ])
        #expect(conflictResult.status == 2)

        let invalid = try CLIFixture(copyResources: false)
        let invalidResult = try invalid.run(["integration", "install", "skill", "--dry-run"])
        #expect(invalidResult.status == 1)

        let failed = try CLIFixture()
        let homeFile = failed.root.appendingPathComponent("home-file")
        try failed.write("not a directory", to: homeFile)
        let failedResult = try failed.run(["integration", "install", "skill"], home: homeFile)
        #expect(failedResult.status == 4)
    }

    @Test("install applies safe skill targets and reports protected targets")
    func partialConflictProcess() throws {
        let fixture = try CLIFixture()
        try FileManager.default.createDirectory(
            at: fixture.home.appendingPathComponent(".claude"), withIntermediateDirectories: true)
        let protected = fixture.home.appendingPathComponent(".codex/skills/agterm/SKILL.md")
        try fixture.write("user authored", to: protected)

        let result = try fixture.run(["integration", "install", "skill", "--json"])

        #expect(result.status == 2)
        let object = try #require(JSONSerialization.jsonObject(
            with: Data(result.output.utf8)) as? [String: Any])
        #expect((object["conflicts"] as? [String])?.count == 1)
        #expect(FileManager.default.fileExists(atPath: fixture.home.appendingPathComponent(
            ".claude/skills/agterm/SKILL.md").path))
        #expect(try String(contentsOf: protected, encoding: .utf8) == "user authored")
    }

    @Test("hooks CLI installs Pi into isolated HOME")
    func piHooksProcess() throws {
        let fixture = try CLIFixture()
        let piAgent = fixture.home.appendingPathComponent(".pi/agent", isDirectory: true)
        try FileManager.default.createDirectory(at: piAgent, withIntermediateDirectories: true)

        let result = try fixture.run(["integration", "install", "hooks", "--json"])

        #expect(result.status == 0)
        let extensionURL = piAgent.appendingPathComponent("extensions/agterm-status.ts")
        let extensionContents = try String(contentsOf: extensionURL, encoding: .utf8)
        #expect(extensionContents.contains("// agterm-pi-status-extension"))
        #expect(!FileManager.default.fileExists(atPath: extensionURL.path + ".bak"))
    }
}

private final class CLIFixture {
    struct Result {
        let status: Int32
        let output: String
        let error: String
    }

    let root: URL
    let home: URL
    let resources: URL

    init(copyResources: Bool = true) throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString,
                                                                             isDirectory: true)
        home = root.appendingPathComponent("home", isDirectory: true)
        resources = root.appendingPathComponent("resources", isDirectory: true)
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: resources, withIntermediateDirectories: true)
        if copyResources {
            let root = packageRoot.deletingLastPathComponent()
            try FileManager.default.copyItem(
                at: root.appendingPathComponent("agterm/Resources/agent-status"),
                to: resources.appendingPathComponent("agent-status"))
            try FileManager.default.copyItem(
                at: root.appendingPathComponent("plugins/agterm/skills/agterm"),
                to: resources.appendingPathComponent("agent-skill"))
        }
    }

    deinit { try? FileManager.default.removeItem(at: root) }

    func claudeCLI(createLauncher: Bool = true) throws -> URL {
        let cli = root.appendingPathComponent("prefix/lib/agterm/bin/agtermctl")
        if !FileManager.default.fileExists(atPath: cli.path) {
            try FileManager.default.createDirectory(at: cli.deletingLastPathComponent(), withIntermediateDirectories: true)
            try FileManager.default.copyItem(at: testProductsDirectory.appendingPathComponent("agtermctl-linux"), to: cli)
        }
        if createLauncher {
            let launcher = cli.deletingLastPathComponent().appendingPathComponent("agterm-agent-status")
            if !FileManager.default.fileExists(atPath: launcher.path) {
                try write("#!/bin/sh\nexit 0\n", to: launcher)
                try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: launcher.path)
            }
        }
        return cli
    }

    func run(_ arguments: [String], home selectedHome: URL? = nil, path: String = "",
             claudeConfig: URL? = nil, executable: URL? = nil) throws -> Result {
        let process = Process()
        if arguments.suffix(2) == ["--only", "claude"] {
            process.executableURL = try executable ?? claudeCLI()
        } else {
            process.executableURL = testProductsDirectory.appendingPathComponent("agtermctl-linux")
        }
        process.arguments = arguments
        var environment = ProcessInfo.processInfo.environment
        environment["HOME"] = (selectedHome ?? home).path
        environment["XDG_CONFIG_HOME"] = nil
        environment["CLAUDE_CONFIG_DIR"] = claudeConfig?.path
        environment["PATH"] = path
        environment["AGTERM_RESOURCE_ROOT"] = resources.path
        environment["AGTERM_STATE_DIR"] = root.appendingPathComponent("state").path
        environment["AGTERM_SOCKET"] = root.appendingPathComponent("missing.sock").path
        process.environment = environment
        let output = Pipe()
        let error = Pipe()
        process.standardOutput = output
        process.standardError = error
        try process.run()
        process.waitUntilExit()
        return Result(status: process.terminationStatus,
                      output: String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self),
                      error: String(decoding: error.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self))
    }

    func homeSnapshot() throws -> [String: Data] {
        let paths = try FileManager.default.subpathsOfDirectory(atPath: home.path)
        return try Dictionary(uniqueKeysWithValues: paths.map { path in
            let url = home.appendingPathComponent(path)
            let directory = try url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory == true
            return (path, directory ? Data() : try Data(contentsOf: url))
        })
    }

    func write(_ text: String, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try text.write(to: url, atomically: true, encoding: .utf8)
    }

    private var packageRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }

    private var testProductsDirectory: URL {
        URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
    }
}
