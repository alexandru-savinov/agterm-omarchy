import Foundation
import agtermCore

/// The WebKit-free admission half of `bridgeRequest`; closures keep each WebKit read after the checks it follows.
enum LinuxHtmlBridgeGate {
    enum Decision: Equatable {
        case dispatch(ControlRequest)
        case reject(String)
    }

    static let leftGrant = "page closed or navigation left the file grant"
    static let invalidRequest = "invalid request"
    static let notMainFrame = "request must come from the file page's main frame"

    static func installs(for source: HtmlSource) -> Bool {
        if case .file = source { true } else { false }
    }

    static func decide(pageID: UUID, nonce: String, closed: Bool, page: () -> HtmlBridgePage?,
                       uri: () -> String?, json: () -> String?) -> Decision {
        guard !closed, let page = page(), let uri = uri(),
              uri == "about:blank" || uri.hasPrefix("agterm-file://\(pageID.uuidString.lowercased())/") else {
            return .reject(leftGrant)
        }
        guard let json = json() else { return .reject(invalidRequest) }
        guard let envelope = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any],
              envelope["nonce"] as? String == nonce, let object = envelope["request"],
              let requestData = try? JSONSerialization.data(withJSONObject: object) else {
            return .reject(notMainFrame)
        }
        switch HtmlBridge.request(from: requestData, page: page) {
        case .failure(let error): return .reject(error.message)
        case .success(let request): return .dispatch(request)
        }
    }
}
