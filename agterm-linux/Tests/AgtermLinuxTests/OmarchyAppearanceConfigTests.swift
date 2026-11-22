import Foundation
import Testing
@testable import AgtermLinux

struct OmarchyAppearanceConfigTests {
    @Test func importsOnlyAllowlistedAppearanceKeys() throws {
        let config = try #require(OmarchyAppearanceConfig.parse("""
            window-padding-x = "14"
            window-padding-y = 0, 14
            cursor-style-blink = false
            window-padding-balance = true
            cursor-style = bar
            config-file = elsewhere.conf
            """))
        #expect(config.lines() == ["window-padding-x = 14", "window-padding-y = 0,14", "cursor-style-blink = false"])
    }

    @Test func explicitAppearanceOverridesOnlyTheirOwnKeys() throws {
        let config = try #require(OmarchyAppearanceConfig.parse("""
            window-padding-x = 14
            window-padding-y = 14
            cursor-style-blink = false
            """))
        let lines = config.fallbackLines(overriddenBy: ["window-padding-x = 8", "cursor-style-blink = true"],
                                         scopedPath: nil, home: "/fixture")
        #expect(lines == ["window-padding-y = 14"])
    }

    @Test func malformedAppearanceValuesAreRejected() {
        for line in ["window-padding-x = small", "window-padding-y = 2.5", "window-padding-x = 14,",
                     "window-padding-y = 2,4,6", "cursor-style-blink = 0", "cursor-style-blink = maybe", "cursor-style-blink"] {
            #expect(OmarchyAppearanceConfig.parse("font-size = 14\n" + line) == nil, "Malformed line: \(line)")
        }
    }

    @Test(arguments: ["font-size = 1e40", "font-size = -2", "window-padding-x = 129",
                      "window-padding-y = 14,-1", "window-padding-y = 18446744073709551616"])
    func outOfRangeAppearanceValuesLeaveOtherAssignmentsIntact(_ assignment: String) throws {
        let config = try #require(OmarchyAppearanceConfig.parse("""
            font-family = JetBrains Mono
            font-size = 12
            window-padding-y = 14
            \(assignment)
            cursor-style-blink = false
            """))
        #expect(config.lines() == [
            "font-family = \"JetBrains Mono\"", "font-size = 12.0", "window-padding-y = 14", "cursor-style-blink = false",
        ])
    }

    @Test func emptyAppearanceValuesRemoveTheFollowedOverrides() throws {
        let config = try #require(OmarchyAppearanceConfig.parse("""
            font-size = 14
            window-padding-x = 14
            window-padding-y = 14
            cursor-style-blink = false
            font-size =
            window-padding-x =
            window-padding-y = ""
            cursor-style-blink =
            """))
        #expect(config.lines().isEmpty)
        #expect(try #require(OmarchyAppearanceConfig.parse("window-padding-x = 14\nwindow-padding-x = 0")).lines() == ["window-padding-x = 0"])
    }

    @Test func keepsFallbackOrderAndOnlyFontKeys() throws {
        let fonts = try #require(OmarchyAppearanceConfig.parse("""
            # font-family = Ignored
            font-family = "JetBrains Mono"
            font-family = Noto Sans CJK
            font-size = "7.5"
            font-size = 7
            font-family-bold = Ignored
            config-file = unrelated.conf
            background = #000000
            """))
        #expect(fonts.lines() == [
            "font-family = \"JetBrains Mono\"", "font-family = \"Noto Sans CJK\"", "font-size = 7.0",
        ])
    }

    @Test func emptyFamilyResetsEarlierEntries() throws {
        let fonts = try #require(OmarchyAppearanceConfig.parse("""
            font-family = First
            font-family = ""
            font-family = Second
            font-family = Third
            """))
        #expect(fonts.lines() == ["font-family = \"Second\"", "font-family = \"Third\""])
        let cleared = try #require(OmarchyAppearanceConfig.parse("font-family = First\nfont-family =\n"))
        #expect(cleared.families == [])
        #expect(cleared.lines() == ["font-family ="])
    }

    @Test func overridesAreIndependent() {
        let fonts = OmarchyAppearanceConfig(families: ["First", "Second"], size: 11)
        #expect(fonts.lines(overriddenBy: ["font-size"]) == ["font-family = \"First\"", "font-family = \"Second\""])
        #expect(fonts.lines(overriddenBy: ["font-family"]) == ["font-size = 11.0"])
        #expect(fonts.lines(overriddenBy: ["font-size", "font-family"]).isEmpty)
    }

    @Test func scopedFamilyIncludesSuppressTheWholeOmarchyList() throws {
        let home = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let scoped = home.appending(path: "ghostty.conf")
        let include = home.appending(path: "fonts.conf")
        try "config-file = ?\"fonts.conf\"".write(to: scoped, atomically: true, encoding: .utf8)
        let fonts = OmarchyAppearanceConfig(families: ["First", "Second"], size: 11)
        func fallback(_ settings: [String] = []) -> [String] {
            fonts.fallbackLines(overriddenBy: settings, scopedPath: scoped.path, home: home.path)
        }

        #expect(fallback() == fonts.lines())
        try "font-size = 9".write(to: include, atomically: true, encoding: .utf8)
        #expect(fallback() == fonts.lines()) // scoping an ordinary scalar is handled by Ghostty's load order
        try "font-family = Custom".write(to: include, atomically: true, encoding: .utf8)
        #expect(fallback() == ["font-size = 11.0"])
        try "font-family =".write(to: include, atomically: true, encoding: .utf8)
        #expect(fallback(["font-size = 13"]).isEmpty)
    }

    @Test func malformedOrOversizedRewriteKeepsLastGoodSnapshot() throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appending(path: "config")
        func write(_ text: String) throws { try text.write(to: file, atomically: true, encoding: .utf8) }

        var source = OmarchyAppearanceSource()
        try write("font-family = JetBrains Mono\nfont-size = 14")
        let firstChanged = source.refresh(from: file)
        #expect(firstChanged)
        let accepted = OmarchyAppearanceConfig(families: ["JetBrains Mono"], size: 14)
        #expect(source.config == accepted)
        for malformed in ["font-size = NaN", "font-size = infinity", "font-size = huge", "font-size",
                          "font-family = \"unterminated", "font-family = bad\0value",
                          "font-size = 9\n#" + String(repeating: "a", count: 64 * 1024)] {
            try write(malformed)
            let changed = source.refresh(from: file)
            #expect(!changed)
            #expect(source.config == accepted)
        }
        try write("font-family = \"JetBrains Mono\"\nfont-size = 14\nkeybind = ctrl+a=unbind")
        let sameChanged = source.refresh(from: file)
        #expect(!sameChanged)
        try write("font-family =\nfont-size = 9")
        let resetChanged = source.refresh(from: file)
        #expect(resetChanged)
        #expect(source.config == OmarchyAppearanceConfig(families: [], size: 9))
    }
}
