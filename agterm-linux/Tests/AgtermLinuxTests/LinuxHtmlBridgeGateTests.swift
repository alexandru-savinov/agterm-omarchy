import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

struct LinuxHtmlBridgeGateTests {
    let pageID = UUID()
    let nonce = UUID().uuidString
    let session = UUID()
    var page: HtmlBridgePage { HtmlBridgePage(window: "W1", session: session, pane: .right) }
    var fileURI: String { "agterm-file://\(pageID.uuidString.lowercased())/index.html" }
    static let fileSource = HtmlSource.file(path: "/tmp/index.html", grantRoot: "/tmp")
    var gate: LinuxHtmlBridgeGate { LinuxHtmlBridgeGate(pageID: pageID, nonce: nonce) }
    var renameEnvelope: String { #"{"nonce":"\#(nonce)","request":{"cmd":"session.rename","args":{"name":"x"}}}"# }

    private func decide(source: HtmlSource = Self.fileSource, closed: Bool = false, page: HtmlBridgePage?? = nil,
                        uri: String?? = nil, json: String?? = nil) -> LinuxHtmlBridgeGate.Decision {
        let page = page ?? self.page
        let uri = uri ?? fileURI
        let json = json ?? renameEnvelope
        return gate.decide(source: source, closed: closed, page: { page }, uri: { uri }, json: { json })
    }

    @Test func aMainFrameFilePageRequestDispatchesToThePagesOwnSession() throws {
        guard case .dispatch(let request) = decide() else { Issue.record("expected a dispatch"); return }
        #expect(request.cmd == .sessionRename)
        #expect(request.target == session.uuidString)
        #expect(request.args?.name == "x")

        let overlay = #"{"nonce":"\#(nonce)","request":{"cmd":"session.overlay.close"}}"#
        guard case .dispatch(let close) = decide(uri: "about:blank", json: overlay) else {
            Issue.record("expected a dispatch from a grantless file page"); return
        }
        #expect(close.target == session.uuidString)
        #expect(close.args?.pane == "right")
        guard case .dispatch(let tree) = decide(json: #"{"nonce":"\#(nonce)","request":{"cmd":"tree"}}"#) else {
            Issue.record("expected a dispatch"); return
        }
        #expect(tree.args?.window == "W1")
    }

    @Test(arguments: [
        #"{"nonce":"guess","request":{"cmd":"session.rename","args":{"name":"x"}}}"#,
        #"{"request":{"cmd":"session.rename","args":{"name":"x"}}}"#,
        #"{"nonce":42,"request":{"cmd":"session.rename","args":{"name":"x"}}}"#,
        #"{"cmd":"session.rename","args":{"name":"x"}}"#,
        #"[{"cmd":"session.rename"}]"#,
        "not json",
    ])
    func aRequestWithoutThePagesNonceIsRefusedAsNotTheMainFrame(_ json: String) {
        #expect(decide(json: json) == .reject(LinuxHtmlBridgeGate.notMainFrame))
    }

    @Test func theNonceEnvelopeNeedsARequest() {
        #expect(decide(json: #"{"nonce":"\#(nonce)"}"#) == .reject(LinuxHtmlBridgeGate.notMainFrame))
    }

    @Test(arguments: [
        "https://example.com/index.html",
        "http://127.0.0.1:8000/",
        "file:///tmp/index.html",
        "agterm-file://00000000-0000-0000-0000-000000000000/index.html",
        "agterm-file://evil/index.html",
        "about:srcdoc",
        "",
    ])
    func aPageOutsideItsOwnFileGrantNeverReadsTheRequest(_ uri: String) {
        var read = false
        let decision = gate.decide(source: Self.fileSource, closed: false, page: { page }, uri: { uri },
                                   json: { read = true; return renameEnvelope })
        #expect(decision == .reject(LinuxHtmlBridgeGate.leftGrant))
        #expect(!read)
    }

    @Test func aGrantHostMustBeThisPagesWholeIdFollowedByAPath() {
        let id = pageID.uuidString.lowercased()
        for uri in ["agterm-file://\(id)", "agterm-file://\(id).evil/index.html", "agterm-file://\(pageID.uuidString)/x.html"] {
            #expect(decide(uri: .some(uri)) == .reject(LinuxHtmlBridgeGate.leftGrant), "\(uri)")
        }
    }

    @Test func aClosedPageRefusesBeforeTouchingTheStoreOrWebView() {
        var touched: [String] = []
        let decision = gate.decide(
            source: Self.fileSource, closed: true,
            page: { touched.append("page"); return page },
            uri: { touched.append("uri"); return fileURI },
            json: { touched.append("json"); return renameEnvelope })
        #expect(decision == .reject(LinuxHtmlBridgeGate.leftGrant))
        #expect(touched.isEmpty)
    }

    @Test func aPageWithNoOverlaySlotOrNoUriIsRefused() {
        #expect(decide(page: .some(nil)) == .reject(LinuxHtmlBridgeGate.leftGrant))
        #expect(decide(uri: .some(nil)) == .reject(LinuxHtmlBridgeGate.leftGrant))
    }

    @Test func anUnserializableValueIsAnInvalidRequest() {
        #expect(decide(json: .some(nil)) == .reject(LinuxHtmlBridgeGate.invalidRequest))
    }

    @Test func aBridgeRefusedCommandKeepsHtmlBridgesErrorText() {
        let json = #"{"nonce":"\#(nonce)","request":{"cmd":"zmx.reset"}}"#
        #expect(decide(json: json) == .reject("zmx.reset cannot be sent from a page"))
    }

    @Test func onlyAFileSourceInstallsTheBridge() throws {
        #expect(LinuxHtmlBridgeGate.installs(for: .file(path: "/tmp/a.html", grantRoot: "/tmp")))
        #expect(LinuxHtmlBridgeGate.installs(for: .file(path: "/tmp/a.html", grantRoot: nil)))
        #expect(LinuxHtmlBridgeGate.installs(for: .replica(path: "/remote/a.html", content: .html)))
        #expect(!LinuxHtmlBridgeGate.installs(for: .replica(path: "/remote/a.md", content: .markdown)))
        #expect(!LinuxHtmlBridgeGate.installs(for: .url(try #require(URL(string: "https://example.com")))))
    }

    @Test func aUrlSourceIsRefusedEvenOnItsOwnGrantUri() throws {
        let url = HtmlSource.url(try #require(URL(string: "https://example.com")))
        #expect(decide(source: url) == .reject(LinuxHtmlBridgeGate.leftGrant))
    }

    @Test func aFilePageServedFromItsPageSchemeDispatches() {
        let uri = "agterm-page://\(pageID.uuidString.lowercased())/index.html"
        guard case .dispatch(let request) = decide(uri: .some(uri)) else { Issue.record("expected a dispatch"); return }
        #expect(request.target == session.uuidString)
        #expect(decide(uri: "agterm-page://\(UUID().uuidString.lowercased())/index.html") == .reject(LinuxHtmlBridgeGate.leftGrant))
    }

    @Test func anAdmittedReplicaRequestKeepsThePagesObjectAndBytes() throws {
        let json = #"{"nonce":"\#(nonce)","request":{"cmd":"session.overlay.reload"}}"#
        guard case .admitted(let admitted) = gate.admit(source: .replica(path: "/remote/a.html", content: .html), closed: false,
                                                        page: { page }, uri: { fileURI }, json: { json }) else {
            Issue.record("expected admission"); return
        }
        #expect(linuxBridgeAddressesOwnPage(admitted.object))
        let decoded = try JSONSerialization.jsonObject(with: admitted.requestData) as? [String: Any]
        #expect(decoded?["cmd"] as? String == "session.overlay.reload")
    }

    @Test(arguments: [#""x""#, "1", "null", "true", #"[{"cmd":"tree"}]"#])
    func aNonObjectRequestCarryingTheNonceIsAnInvalidRequest(_ request: String) {
        guard case .reject(let error) = decide(json: #"{"nonce":"\#(nonce)","request":\#(request)}"#) else {
            Issue.record("expected a refusal"); return
        }
        #expect(error.hasPrefix("invalid request: "))
    }

    // bridgeRequest reads jsc_value_to_json through String(cString:), which repairs invalid UTF-8
    @Test func aNonUtf8PayloadNeverTrapsAndOnlyAWellFormedEnvelopeDispatches() {
        let cString: ([UInt8]) -> String = { bytes in (bytes + [0]).withUnsafeBufferPointer { String(cString: $0.baseAddress!) } }
        let broken: [UInt8] = Array(#"{"nonce":""#.utf8) + [0xFF, 0xFE] + Array(#"","request":"#.utf8) + [0xC0, 0x7D]
        #expect(decide(json: .some(cString(broken))) == .reject(LinuxHtmlBridgeGate.notMainFrame))
        let named = Array(#"{"nonce":"\#(nonce)","request":{"cmd":"session.rename","args":{"name":""#.utf8) + [0xFF] + Array(#""}}}"#.utf8)
        guard case .dispatch(let request) = decide(json: .some(cString(named))) else { Issue.record("expected a dispatch"); return }
        #expect(request.args?.name == "\u{FFFD}")
    }
}
