import Foundation
import agtermCore

/// LinuxHtmlNavigation is the WebKitGTK `decide-policy` adapter with the WebKit calls taken out.
/// WebKitGTK before 2.56 gives a navigation action no target-frame identity: `frame_name` is the requested
/// target name and is nil for every unnamed iframe, so each frame navigation is decided as the main frame's.
/// That keeps the cross-origin check ahead of the request, at the cost of cancelling the cross-origin
/// subframes a macOS URL page allows; trusting a non-nil name instead would let `target="_top"` pass as one.
enum LinuxHtmlNavigation {
    struct Outcome: Equatable {
        let url: URL
        let decision: HtmlNavigationDecision
        /// failsPage reports a blocked main-frame navigation the page did not ask for by a click.
        let failsPage: Bool
    }

    static func decide(_ rawURL: URL, newWindow: Bool, linkClicked: Bool, page: HtmlOverlay,
                       alreadyFailed: Bool) -> Outcome {
        let url = fileURL(rawURL, page: page) ?? rawURL
        let target: HtmlNavigationTarget = newWindow ? .newWindow : .mainFrame
        let decision = HtmlNavigationPolicy.decide(
            HtmlNavigationAction(url: url, target: target, userActivated: linkClicked), overlay: page)
        let fails = decision != .allow && target == .mainFrame && !linkClicked && !alreadyFailed
        return Outcome(url: url, decision: decision, failsPage: fails)
    }

    /// fileURL maps this page's own `agterm-file` URI back into its grant; any other page's stays unmapped.
    private static func fileURL(_ rawURL: URL, page: HtmlOverlay) -> URL? {
        guard rawURL.scheme == "agterm-file", case .file(_, let grantRoot?) = page.source,
              rawURL.host?.lowercased() == page.id.uuidString.lowercased() else { return nil }
        return URL(fileURLWithPath: grantRoot).appendingPathComponent(String(rawURL.path.dropFirst()))
    }
}
