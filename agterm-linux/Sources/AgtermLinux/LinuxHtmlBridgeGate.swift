import Foundation
import agtermCore

/// The WebKit-free admission half of `bridgeRequest`; closures keep each WebKit read after the checks it follows.
struct LinuxHtmlBridgeGate {
    let pageID: UUID
    let nonce: String

    enum Decision: Equatable {
        case dispatch(ControlRequest)
        case reject(String)
    }

    /// The request as the page sent it, for replica forwarding, and re-serialized for `HtmlBridge.request`.
    struct Admitted {
        let page: HtmlBridgePage
        let object: Any
        let requestData: Data
    }

    enum Admission {
        case admitted(Admitted)
        case reject(String)
    }

    static let leftGrant = "page closed or navigation left the file grant"
    static let invalidRequest = "invalid request"
    static let notMainFrame = "request must come from the file page's main frame"

    static func installs(for source: HtmlSource) -> Bool { source.kind == .file }

    func admit(source: HtmlSource, closed: Bool, page: () -> HtmlBridgePage?, uri: () -> String?,
               json: () -> String?) -> Admission {
        let id = pageID.uuidString.lowercased()
        guard Self.installs(for: source), !closed, let page = page(), let uri = uri(),
              uri == "about:blank" || uri.hasPrefix("agterm-file://\(id)/") || uri.hasPrefix("agterm-page://\(id)/") else {
            return .reject(Self.leftGrant)
        }
        guard let json = json() else { return .reject(Self.invalidRequest) }
        guard let envelope = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any],
              envelope["nonce"] as? String == nonce, let object = envelope["request"],
              // fragments: corelibs traps on a non-container top level, and a page-world script can send one
              let requestData = try? JSONSerialization.data(withJSONObject: object, options: .fragmentsAllowed) else {
            return .reject(Self.notMainFrame)
        }
        return .admitted(Admitted(page: page, object: object, requestData: requestData))
    }

    static func request(_ admitted: Admitted) -> Decision {
        switch HtmlBridge.request(from: admitted.requestData, page: admitted.page) {
        case .failure(let error): return .reject(error.message)
        case .success(let request): return .dispatch(request)
        }
    }

    func decide(source: HtmlSource, closed: Bool, page: () -> HtmlBridgePage?, uri: () -> String?,
                json: () -> String?) -> Decision {
        switch admit(source: source, closed: closed, page: page, uri: uri, json: json) {
        case .reject(let error): return .reject(error)
        case .admitted(let admitted): return Self.request(admitted)
        }
    }
}
