#if DEBUG
import Foundation
import Testing
@testable import AgtermLinux

@MainActor
@Suite("HTML paste trace records only types and booleans")
struct LinuxHtmlPasteTraceTests {
    @Test func theLineShapeIsWhatTheScenarioParses() {
        #expect(LinuxHtmlPasteTrace.line(event: "receiver-key", at: 42, fields: [("ctrl", "1"), ("mimes", "text/plain")])
            == "agterm-paste-trace: event=receiver-key at=42 ctrl=1 mimes=text/plain")
        #expect(LinuxHtmlPasteTrace.environmentKey == "AGTERM_ATSPI_PASTE_TRACE")
    }

    @Test func typeNamesAreReducedToPlainTokens() {
        #expect(LinuxHtmlPasteTrace.types([]) == "none")
        #expect(LinuxHtmlPasteTrace.types(["text/uri-list", "text/plain;charset=utf-8", "UTF8_STRING", "a b", ""])
            == "text/uri-list,text/plain;charset=utf-8,UTF8_STRING,?,?")
        #expect(LinuxHtmlPasteTrace.types([String(repeating: "x", count: 81)]) == "?")
    }

    @Test func theProductionGuardTakesEveryAnchorOnce() throws {
        let traced = try #require(LinuxHtmlPasteTrace.instrument(LinuxHtmlOverlayPage.transferGuard))
        #expect(traced.contains("window.webkit.messageHandlers.agtermPasteTrace"))
        #expect(traced.contains("const result = first || second || data.files.length > 0;"))
        #expect(!LinuxHtmlOverlayPage.transferGuard.contains("agtermPasteTrace"))
    }

    @Test func aDriftedGuardIsNotInstrumented() {
        #expect(LinuxHtmlPasteTrace.instrument("(() => {\n})();\n") == nil)
    }
}
#endif
