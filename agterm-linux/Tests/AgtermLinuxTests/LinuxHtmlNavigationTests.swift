import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

@Suite("WebKitGTK navigation adapter decisions")
struct LinuxHtmlNavigationTests {
    private let id = UUID()
    private var granted: HtmlOverlay { HtmlOverlay(source: .file(path: "/tmp/g/index.html", grantRoot: "/tmp/g"), id: id) }
    private let dev = HtmlOverlay(source: .url(URL(string: "http://127.0.0.1:5173/")!))

    private func decide(_ raw: String, _ page: HtmlOverlay, newWindow: Bool = false, clicked: Bool = false,
                        alreadyFailed: Bool = false) throws -> LinuxHtmlNavigation.Outcome {
        LinuxHtmlNavigation.decide(try #require(URL(string: raw)), newWindow: newWindow, linkClicked: clicked,
                                   page: page, alreadyFailed: alreadyFailed)
    }

    private func own(_ path: String) -> String { "agterm-file://\(id.uuidString.lowercased())/\(path)" }

    @Test func sameGrantRedirectIsAllowedAsTheMappedFile() throws {
        let outcome = try decide(own("sub/next.html"), granted)
        #expect(outcome == .init(url: URL(fileURLWithPath: "/tmp/g/sub/next.html"), decision: .allow, failsPage: false))
    }

    @Test(arguments: ["../outside.html", "%2E%2E/outside.html", "sub/../../etc/passwd"])
    func grantEscapeIsCancelledAndFailsThePage(_ path: String) throws {
        let outcome = try decide(own(path), granted)
        #expect(outcome.decision == .cancel && outcome.failsPage)
    }

    @Test func anotherPagesFileURIIsNotMappedIntoThisGrant() throws {
        let outcome = try decide("agterm-file://\(UUID().uuidString.lowercased())/index.html", granted)
        #expect(outcome.url.scheme == "agterm-file" && outcome.decision == .cancel && outcome.failsPage)
    }

    @Test func crossOriginScriptRedirectIsCancelledBeforeTheRequest() throws {
        #expect(try decide("http://127.0.0.1:9/x", granted).decision == .cancel)
        #expect(try decide("http://127.0.0.1:9/x", dev) == .init(url: URL(string: "http://127.0.0.1:9/x")!,
                                                                  decision: .cancel, failsPage: true))
    }

    @Test func sameOriginNavigationOfAURLPageIsAllowed() throws {
        #expect(try decide("http://127.0.0.1:5173/next?x=1", dev).decision == .allow)
    }

    @Test func unnamedSubframeIsDecidedAsTheMainFrame() throws {
        let outcome = try decide("https://embed.example/frame", dev)
        #expect(outcome.decision == .cancel && outcome.failsPage)
    }

    @Test func clickedCrossOriginLinkAsksForTheBrowserWithoutFailing() throws {
        #expect(try decide("https://example.com/", dev, clicked: true).decision == .openExternal)
        #expect(try decide("https://example.com/", granted, clicked: true) == .init(
            url: URL(string: "https://example.com/")!, decision: .openExternal, failsPage: false))
    }

    @Test(arguments: [(true, HtmlNavigationDecision.openExternal), (false, .cancel)])
    func newWindowGoesToTheBrowserOnlyWhenClicked(_ clicked: Bool, _ expected: HtmlNavigationDecision) throws {
        for page in [granted, dev] {
            let outcome = try decide("https://example.com/", page, newWindow: true, clicked: clicked)
            #expect(outcome.decision == expected && !outcome.failsPage)
        }
    }

    @Test func newWindowForAFileOrGrantURIIsCancelled() throws {
        #expect(try decide(own("next.html"), granted, newWindow: true, clicked: true).decision == .cancel)
        #expect(try decide("file:///etc/passwd", dev, newWindow: true, clicked: true).decision == .cancel)
    }

    @Test func aPageThatAlreadyFailedIsNotFailedAgain() throws {
        #expect(!(try decide("http://127.0.0.1:9/x", dev, alreadyFailed: true).failsPage))
    }
}
