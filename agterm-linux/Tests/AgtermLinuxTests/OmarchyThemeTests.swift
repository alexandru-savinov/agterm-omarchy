import CGtk
import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

struct OmarchyThemeTests {
    @Test func parsesOnlyValidColorAssignments() {
        let source = """
            # foreground = #000000
            background = #102030
            foreground=abcdef
            cursor-color = #112233
            cursor-text = cell-background
            selection-background = #223344
            selection-foreground = #334455
            palette = 0 = #445566
            palette = 15=#556677
            command = /bin/false
            shell-integration = none
            config-file = /tmp/something.conf
            background-opacity = 0.5
            theme = another
            background
            foreground = #oops
            cursor-text =
            selection-foreground = #332211 command = invalid
            palette = -1=#aabbcc
            palette = 256=#aabbcc
            palette = 2=
            """
        #expect(OmarchyTheme.colorLines(in: source) == [
            "background = #102030", "foreground = abcdef", "cursor-color = #112233",
            "cursor-text = cell-background", "selection-background = #223344",
            "selection-foreground = #334455", "palette = 0=#445566", "palette = 15=#556677",
        ])
    }

    @Test func missingFileContributesNoColorsAndPreferencePersists() throws {
        let root = try makeFixture()
        defer { try? FileManager.default.removeItem(at: root) }
        let theme = OmarchyTheme(home: root)
        let state = root.appendingPathComponent("state")
        let preference = OmarchyThemePreference(stateDirectory: state)
        #expect(!preference.follows(theme))
        #expect(theme.colorLines().isEmpty)

        try FileManager.default.createDirectory(at: theme.directory, withIntermediateDirectories: true)
        #expect(preference.follows(theme))
        #expect(theme.colorLines().isEmpty)
        try "background = #112233\ncommand = /bin/false".write(to: theme.config, atomically: true, encoding: .utf8)
        #expect(theme.colorLines() == ["background = #112233"])

        try preference.save(false)
        #expect(!OmarchyThemePreference(stateDirectory: state).follows(theme))
        try preference.save(true)
        try FileManager.default.removeItem(at: theme.directory)
        #expect(OmarchyThemePreference(stateDirectory: state).follows(theme))
    }

    @Test func explicitThemeChoiceWinsEvenForTheSeededAndGhosttyDefaults() throws {
        let root = try makeFixture()
        defer { try? FileManager.default.removeItem(at: root) }
        let preference = OmarchyThemePreference(stateDirectory: root)
        let seeded = AppSettings(theme: AppSettings.defaultTheme)
        #expect(!preference.usesAgtermTheme(seeded))
        #expect(!preference.usesAgtermTheme(AppSettings()))
        #expect(preference.usesAgtermTheme(AppSettings(theme: "Dracula")))
        #expect(preference.usesAgtermTheme(seeded, previewing: true))
        var paired = seeded
        paired.followSystemAppearance = true
        #expect(preference.usesAgtermTheme(paired))

        try preference.save(true)
        try preference.saveAgtermThemeChoice(explicit: true)
        let reopened = OmarchyThemePreference(stateDirectory: root)
        #expect(reopened.usesAgtermTheme(seeded))
        #expect(reopened.usesAgtermTheme(AppSettings()))
        try reopened.saveAgtermThemeChoice(explicit: false)
        #expect(!preference.usesAgtermTheme(seeded))
        #expect(preference.load() == true)
    }

    @Test func scopedColorsOverrideOmarchyAndItsGlobalInclude() throws {
        _ = GhosttyTestRuntime.initialized
        let root = try makeFixture()
        defer { try? FileManager.default.removeItem(at: root) }
        let storage = try GhosttyGeneratedConfigStorage(runtimeRoot: root.path)
        defer { try? storage.cleanup() }
        let theme = OmarchyTheme(home: root)
        try FileManager.default.createDirectory(at: theme.directory, withIntermediateDirectories: true)
        try "background = #445566\nforeground = #A1B2C3\npalette = 1=#334455".write(
            to: theme.config, atomically: true, encoding: .utf8)
        let global = root.appendingPathComponent("global.conf")
        try "config-file = \(theme.config.path)".write(to: global, atomically: true, encoding: .utf8)
        let scoped = root.appendingPathComponent("ghostty.conf")
        try "background = #778899\npalette = 1=#667788\nfont-size = 11\nconfig-file = child.conf".write(
            to: scoped, atomically: true, encoding: .utf8)
        try "split-divider-color = #ABCDEF".write(
            to: root.appendingPathComponent("child.conf"), atomically: true, encoding: .utf8)

        for (follows, explicit) in [(true, false), (true, true), (false, false)] {
            let config = try #require(ghostty_config_new())
            defer { ghostty_config_free(config) }
            let layers = LinuxGhosttyConfigLayers(
                settings: ["background = #112233", "foreground = #FFFFFF", "font-size = 15"],
                omarchy: follows ? theme.colorLines() : [],
                prefersAgtermTheme: explicit)
            try layers.load(into: config, storage: storage, scopedPath: scoped.path, loadGlobal: follows ? {
                global.path.withCString { ghostty_config_load_file(config, $0) }
            } : nil)
            ghostty_config_finalize(config)
            let colors = GhosttyConfigTheme.colors(from: config)
            #expect(colors.background == (follows ? "#778899" : "#112233"))
            #expect(colors.foreground == (follows && !explicit ? "#A1B2C3" : "#FFFFFF"))
            #expect(colors.splitDivider == "#ABCDEF")
            #expect(GhosttyConfigTheme.palette(from: config).dropFirst().first == "#667788")
            #expect(GhosttyConfigTheme.fontSize(from: config) == 15)
        }
    }

    private func makeFixture() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }
}
