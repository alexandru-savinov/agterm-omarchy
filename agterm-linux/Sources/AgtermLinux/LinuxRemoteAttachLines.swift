import Foundation
import agtermCore

/// LinuxRemoteAttachLines builds the attach line for a split or scratch an origin added to a row attached from it,
/// as the macOS ControlServer does. Neither claims the lead: the origin's own client of that pane keeps it.
@MainActor
final class LinuxRemoteAttachLines: RemoteAttachLines {
    static let shared = LinuxRemoteAttachLines()

    func paneAttach(_ origin: RemoteBinding.Origin, daemon: String, local: UUID) -> String {
        attach(origin, daemon: daemon, local: local, session: origin.sessionName, pane: .right, what: "pane")
    }

    func scratchAttach(_ origin: RemoteBinding.Origin, daemon: String, local: UUID) -> String {
        attach(origin, daemon: daemon, local: local, session: "scratch", pane: .left, what: "scratch")
    }

    private func attach(_ origin: RemoteBinding.Origin, daemon: String, local: UUID, session: String,
                        pane: ZmxPaneRole, what: String) -> String {
        let lead = ZmxLeadAttachment(claim: false)
        guard let line = try? RemoteSession.attachPaneCommand(host: origin.host, endpoint: origin.endpoint, daemon: daemon,
                                                              session: session, pane: pane, lead: lead) else {
            return CommandRestore.shellQuotedLine(["/usr/bin/env", "/bin/sh", "-c",
                                                   "printf '%s\\n' 'agterm: \(origin.host) listed a \(what) agterm cannot address'; exit 1"])
        }
        ZmxLeadBook.shared.begin(lead, pane: local)
        return line
    }
}
