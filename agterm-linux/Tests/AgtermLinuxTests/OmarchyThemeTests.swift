import CGtk
import Foundation
import Testing
import agtermCore
#if canImport(Glibc)
import Glibc
#else
import Darwin
#endif
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

    @Test func rejectsAnOversizedFileAndAFIFO() throws {
        let root = try makeFixture()
        defer { try? FileManager.default.removeItem(at: root) }
        let theme = OmarchyTheme(home: root)
        try FileManager.default.createDirectory(at: theme.directory, withIntermediateDirectories: true)
        let valid = Data("background = #112233\n".utf8)
        let oversized = valid + Data(repeating: 65, count: OmarchyTheme.configReadLimit)
        try oversized.write(to: theme.config)
        #expect(theme.colorLines().isEmpty)
        try FileManager.default.removeItem(at: theme.config)
        try #require(mkfifo(theme.config.path, 0o600) == 0)
        #expect(theme.colorLines().isEmpty)
        try FileManager.default.removeItem(at: theme.config)
        try valid.write(to: theme.config)
        #expect(theme.colorLines() == ["background = #112233"])
    }

    @Test func generatedInputProducesOnlyCanonicalColorAssignments() throws {
        let seed: UInt64 = 0x84F4_1AB9_859C_113E
        let context = "SplitMix64 seed 0x\(String(seed, radix: 16))"
        var random = SplitMix64(state: seed)
        let keys = ["background", "foreground", "cursor-color", "cursor-text", "selection-background",
                    "selection-foreground", "palette", "command", "keybind", "config-file",
                    "background-opacity", "# foreground", "\u{FEFF}foreground"]
        let separators = [" = ", "=", "\t=\t", " : ", "==", "\u{2003}= ", " =\0", ""]
        let values = ["#0123aB", "aBCd09", "cell-foreground", "cell-background", "#FFFFFF",
                      "0=#123456", "007=ABCDEF", "255=123456", "256=#FFFFFF", "16=#000000",
                      "\"#abcdef\"", "#123456\0", "command=/usr/bin/false", "#xyzxyz", ""]
        let suffixes = ["", "", "", "", " ", "\t", "\r", "\r\ncommand = false",
                        "\u{2028}foreground = #fedcba", "\nconfig-file = ignored"]
        let characters = Array("09AZaz#= /\\\"\t\r\n\0;☃é\u{200B}\u{2028}")
        var input = ""
        for _ in 0..<60_000 {
            input += random.pick(keys) + random.pick(separators)
            if random.next().isMultiple(of: 4) {
                let count = Int(random.next() % 24)
                input += String((0..<count).map { _ in random.pick(characters) })
            } else {
                input += random.pick(values)
            }
            input += random.pick(suffixes) + "\n"
        }
        let output = OmarchyTheme.colorLines(in: input)
        try #require(output.count > 1_000, "\(context): generated too few accepted lines")
        for line in output where !isCanonicalColorAssignment(line) {
            Issue.record("\(context): invalid output \(line.debugDescription)")
            return
        }
        #expect(OmarchyTheme.colorLines(in: output.joined(separator: "\n")) == output, "\(context)")
    }

    private func isCanonicalColorAssignment(_ line: String) -> Bool {
        let bytes = Array(line.utf8)
        guard bytes.allSatisfy({ (32...126).contains($0) }),
              let equals = bytes.firstIndex(of: 61), equals > 0, equals + 1 < bytes.count,
              bytes[equals - 1] == 32, bytes[equals + 1] == 32 else { return false }
        let key = String(decoding: bytes[..<(equals - 1)], as: UTF8.self)
        let value = bytes[(equals + 2)...]
        switch key {
        case "palette":
            guard let split = value.firstIndex(of: 61) else { return false }
            let digits = value[..<split]
            guard (1...3).contains(digits.count), digits.allSatisfy({ (48...57).contains($0) }),
                  digits.count == 1 || digits.first != 48,
                  digits.reduce(0, { $0 * 10 + Int($1 - 48) }) <= 255 else { return false }
            return isASCIIHexColor(value[(split + 1)...])
        case "background", "foreground":
            return isASCIIHexColor(value)
        case "cursor-color", "cursor-text", "selection-background", "selection-foreground":
            return isASCIIHexColor(value) || value.elementsEqual("cell-background".utf8)
                || value.elementsEqual("cell-foreground".utf8)
        default:
            return false
        }
    }

    private func isASCIIHexColor(_ value: ArraySlice<UInt8>) -> Bool {
        let digits = value.first == 35 ? value.dropFirst() : value
        return digits.count == 6 && digits.allSatisfy {
            (48...57).contains($0) || (65...70).contains($0) || (97...102).contains($0)
        }
    }

    private struct SplitMix64: RandomNumberGenerator {
        var state: UInt64

        mutating func pick<T>(_ choices: [T]) -> T {
            choices[Int(next() % UInt64(choices.count))]
        }

        mutating func next() -> UInt64 {
            state &+= 0x9E37_79B9_7F4A_7C15
            var value = state
            value = (value ^ (value >> 30)) &* 0xBF58_476D_1CE4_E5B9
            value = (value ^ (value >> 27)) &* 0x94D0_49BB_1331_11EB
            return value ^ (value >> 31)
        }
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
                settings: ["background = #112233", "foreground = #FFFFFF", "palette = 1=#223344", "font-size = 15"],
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
            #expect(GhosttyConfigTheme.palette(from: config).dropFirst().first == (follows ? "#667788" : "#223344"))
            #expect(GhosttyConfigTheme.fontSize(from: config) == 15)
        }
    }

    private func makeFixture() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }
}
