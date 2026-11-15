import Foundation
import agtermCore

@MainActor
extension AppController {
    var remoteHeldExits: LinuxRemoteHeldExitRouter {
        LinuxRemoteHeldExitRouter(store: store,
                                  closeRemovedPane: { [weak self] local, id in self?.closeRemovedRemotePane(local, forSession: id) },
                                  reconcile: { [weak self] focusActive in self?.reconcile(focusActive: focusActive) })
    }
}

/// A held surface keeps its last identity only until one final link notice consumes it.
@MainActor
final class LinuxHeldLeadAttachment {
    private(set) var state: ZmxLeadState?

    func retain(_ state: ZmxLeadState?) { self.state = state ?? self.state }
    func consume() { state = nil }
}

/// Routes a replica surface held on its exit prompt to the store. Only the surface still in its slot counts:
/// a hook that outlived its slot must not mark whatever replaced it.
@MainActor
struct LinuxRemoteHeldExitRouter {
    let store: AppStore
    let closeRemovedPane: (_ local: UUID, _ session: UUID) -> Void
    let reconcile: (_ focusActive: Bool) -> Void

    /// Retain authentication for a final PTY notice while clearing the ended pane's visible role.
    func attachmentEnded(_ surface: any TerminalSurface, forSession id: UUID) -> ZmxLeadState? {
        guard let pane = paneIdentity(surface, forSession: id) else { return nil }
        let state = ZmxLeadBook.shared.states[pane]
        ZmxLeadBook.shared.forget(pane: pane)
        return state
    }

    func linkLost(_ notice: RemoteLinkNotice, from surface: any TerminalSurface, forSession id: UUID,
                  heldAttachment: LinuxHeldLeadAttachment) -> Bool {
        let tracePane = paneIdentity(surface, forSession: id)
        Trace357.log("linkLost \((surface as? GhosttySurface)?.trace357 ?? "?") slotPane=\(Trace357.short(tracePane)) notice=\(Trace357.short(notice.nonce)) live=\(Trace357.short(tracePane.flatMap { ZmxLeadBook.shared.states[$0]?.attachment.nonce })) held=\(Trace357.short(heldAttachment.state?.attachment.nonce)) host=\(store.session(withID: id)?.remoteHost != nil)")
        guard let pane = paneIdentity(surface, forSession: id),
              let session = store.session(withID: id), let host = session.remoteHost,
              let attachment = ZmxLeadBook.shared.states[pane] ?? heldAttachment.state,
              notice.nonce == attachment.attachment.nonce else { Trace357.log("linkLost rejected"); return false }
        let cover = attachment.role != nil || attachment.reattaching
        heldAttachment.consume()
        ZmxLeadBook.shared.forget(pane: pane)
        store.remotePaneHeld(pane, forSession: id)
        paneHeld(surface, forSession: id)
        RemoteReconnectBook.shared.wait(pane: pane, session: id, host: host, cover: cover, now: Date())
        Trace357.log("linkLost accepted pane=\(Trace357.short(pane)) waiting=\(RemoteReconnectBook.shared.waiting(pane: pane))")
        return true
    }

    private func paneIdentity(_ surface: any TerminalSurface, forSession id: UUID) -> UUID? {
        guard let session = store.session(withID: id) else { return nil }
        if session.surface === surface { return session.paneIdentity }
        if session.splitSurface === surface { return session.splitPaneIdentity }
        return nil
    }

    func paneHeld(_ surface: any TerminalSurface, forSession id: UUID) {
        guard let session = store.session(withID: id), session.remotePresentation != nil else { return }
        let local: UUID
        if session.surface === surface {
            local = session.paneIdentity
        } else if session.splitSurface === surface, let split = session.splitPaneIdentity {
            local = split
        } else {
            return
        }
        store.remotePaneHeld(local, forSession: id)
        Trace357.log("paneHeld pane=\(Trace357.short(local)) canClose=\(store.canCloseRemovedRemotePane(local, forSession: id))")
        // a removal frame that arrived first could not close a replica still running its ssh
        closeRemovedPane(local, id)
        reconcile(false)
    }

    func overlayHeld(_ surface: any TerminalSurface, forSession id: UUID) {
        guard let session = store.session(withID: id) else { return }
        let pane: OverlayPane?
        let replica: OverlayReplica?
        if session.overlaySurface === surface {
            pane = nil
            replica = session.overlayReplica
        } else if let role = session.paneOverlayRole(of: surface) {
            pane = role
            replica = session.paneOverlay(role)?.replica
        } else {
            return
        }
        guard replica != nil else { return }
        store.replicaOverlayHeld(forSession: id, pane: pane)
        reconcile(false)
    }
}
