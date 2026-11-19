import Foundation
import agtermCore

/// LinuxRemoteAttachLines builds the attach line for a split or scratch an origin added to a row attached from it,
/// as the macOS ControlServer does. Neither claims the lead: the origin's own client of that pane keeps it.
@MainActor
final class LinuxRemoteAttachLines: RemoteAttachLines {
    static let shared = LinuxRemoteAttachLines()

    func paneAttach(_ origin: RemoteBinding.Origin, daemon: String, local: UUID) -> String {
        attach(origin, daemon: daemon, local: local, as: Role(session: origin.sessionName, pane: .right, what: "pane"))
    }

    func scratchAttach(_ origin: RemoteBinding.Origin, daemon: String, local: UUID) -> String {
        attach(origin, daemon: daemon, local: local, as: Role(session: "scratch", pane: .left, what: "scratch"))
    }

    /// Role is the session name the attach shows, its pane role and the word a refusal names it by.
    private struct Role {
        let session: String
        let pane: ZmxPaneRole
        let what: String
    }

    private func attach(_ origin: RemoteBinding.Origin, daemon: String, local: UUID, as role: Role) -> String {
        let lead = ZmxLeadAttachment(claim: false)
        guard let line = try? RemoteSession.attachPaneCommand(host: origin.host, endpoint: origin.endpoint, daemon: daemon,
                                                              session: role.session, pane: role.pane, lead: lead) else {
            return CommandRestore.shellQuotedLine(["/usr/bin/env", "/bin/sh", "-c",
                                                   "printf '%s\\n' 'agterm: \(origin.host) listed a \(role.what) agterm cannot address'; exit 1"])
        }
        ZmxLeadBook.shared.begin(lead, pane: local)
        return line
    }
}
