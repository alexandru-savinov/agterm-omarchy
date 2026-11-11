import Foundation
import agtermCore

private enum LinuxControlZmxError {
    static let incompleteInventory =
        "the pane inventory is incomplete or has conflicting owners, so no daemon can be safely pruned"

    static func killRefusal(_ row: ZmxInventoryRow) -> String? {
        switch row.observation {
        case .absent: return "\(row.daemon) is not running"
        case .unreadable: return "\(row.daemon) is unreadable; killing it could orphan a live daemon"
        case .running: break
        }
        switch row.state {
        case .claimed: return nil
        case .pendingClose: return "\(row.daemon) belongs to a session waiting out its undo window"
        case .unknown, .conflicted: return incompleteInventory
        case .orphan, .foreign: return "\(row.daemon) is not claimed by that pane"
        }
    }
}

@MainActor
extension AppController {
    func restoreStatus() -> ControlRestoreStatus {
        let decision = gRestoreLaunchDecision
        return ControlRestoreStatus(
            configured: linuxSettingsStore().load().effectiveRestoreMode,
            requestedAtLaunch: decision.requested,
            active: decision.active,
            unavailableReason: decision.liveUnavailableReason
        )
    }

    func readRestoreMode() -> ControlResponse {
        ControlResponse(ok: true, result: ControlResult(restore: restoreStatus()))
    }

    func setRestoreMode(_ mode: RestoreMode) -> ControlResponse {
        let store = linuxSettingsStore()
        var settings = store.load()
        settings.restoreMode = mode
        settings.restoreRunningCommand = nil
        do {
            try store.save(settings)
        } catch {
            return ControlResponse(ok: false, error: "could not save the restore mode; settings keep "
                + store.load().effectiveRestoreMode.rawValue)
        }
        return ControlResponse(ok: true, result: ControlResult(restore: restoreStatus()))
    }

    /// Continues a local zmx request once the worker has read the session list. Runs on the main thread,
    /// so it only joins that list with the pane claims; any further zmx call goes back to the worker.
    func continueLocalZmx(_ request: ControlRequest, observed: [ZmxSessionRecord],
                          client: LinuxZmxClient) -> ControlStage {
        switch request.cmd {
        case .zmxList:
            return .done(ControlResponse(ok: true, result: ControlResult(zmx: ControlZmxInventory(
                restore: restoreStatus(), result: zmxJoin(observed), endpoint: client.endpoint
            ))))
        case .zmxPrune:
            return pruneZmxDaemons(observed: observed, client: client)
        case .zmxKill:
            let pane = request.args?.pane.flatMap(ZmxPaneRole.init(controlName:)) ?? .left
            return killZmxDaemon(target: request.target ?? "", window: request.args?.window, pane: pane,
                                 observed: observed, client: client)
        case .zmxTree:
            return .done(localAttachableSessions(observed: observed, client: client))
        default:
            return .done(err("unexpected zmx command: \(request.cmd.rawValue)"))
        }
    }

    private func zmxJoin(_ observed: [ZmxSessionRecord]) -> ZmxInventoryResult {
        let walk = library.paneClaims()
        return ZmxInventory.join(observed: observed, claims: walk.claims, inventoryComplete: walk.complete)
    }

    private func pruneZmxDaemons(observed: [ZmxSessionRecord], client: LinuxZmxClient) -> ControlStage {
        guard let candidates = ZmxPrunePolicy.namesToPrune(zmxJoin(observed)) else {
            return .done(err(LinuxControlZmxError.incompleteInventory))
        }
        guard !candidates.isEmpty else {
            return .done(ControlResponse(ok: true, result: ControlResult(text: "no orphan daemons", affected: 0)))
        }
        return .worker {
            guard let recheck = client.listSessions() else {
                return .done(ControlResponse(ok: false, error: "could not re-read the zmx session list before pruning"))
            }
            return .main { [self] in
                // The pane claims can change while zmx runs off the main thread, so join them again with the
                // fresh list: a daemon is killed only if both joins call it an orphan and it is detached.
                guard let fresh = ZmxPrunePolicy.namesToPrune(zmxJoin(recheck)).map({ Set($0) }) else {
                    return .done(err(LinuxControlZmxError.incompleteInventory))
                }
                let detached = Set(recheck.filter { $0.clients == 0 }.map(\.name))
                let names = candidates.filter { detached.contains($0) && fresh.contains($0) }
                guard !names.isEmpty else {
                    return .done(ControlResponse(ok: true,
                                                 result: ControlResult(text: "no orphan daemons left to prune", affected: 0)))
                }
                return .worker {
                    let outcomes = client.killObservedOrphan(names: names)
                    let killed = outcomes.values.filter { $0 == .killed }.count
                    return .done(ControlResponse(ok: true, result: ControlResult(
                        text: AppController.zmxPruneReport(outcomes), affected: killed
                    )))
                }
            }
        }
    }

    nonisolated private static func zmxPruneReport(_ outcomes: [String: LinuxZmxClient.KillOutcome]) -> String {
        outcomes.keys.sorted().map { name in
            switch outcomes[name] {
            case .killed: "killed \(name)"
            case .staleSocket: "\(name): cleaned up a stale socket, the daemon may still be running"
            case .failed(let reason): "\(name): not killed (\(reason))"
            case nil: "\(name): no result"
            }
        }.joined(separator: "; ")
    }

    private func killZmxDaemon(target: String, window: String?, pane: ZmxPaneRole,
                               observed: [ZmxSessionRecord], client: LinuxZmxClient) -> ControlStage {
        let inventory = zmxJoin(observed)
        guard target != "active" else {
            return .done(err("zmx.kill needs a session id; 'active' is not accepted"))
        }
        guard window != "active" else {
            return .done(err("zmx.kill needs a window id; 'active' is not accepted"))
        }
        let windowIDs = Array(Set(inventory.rows.compactMap { $0.claim?.windowID }))
        var owned = inventory.rows.filter { $0.claim?.pane == pane }
        if let window, !window.isEmpty {
            guard case .resolved(let windowID) = ControlResolve.resolve(window, candidates: windowIDs, active: nil)
            else { return .done(err("no such window: \(window)")) }
            owned = owned.filter { $0.claim?.windowID == windowID }
        }
        let candidates = owned.compactMap { $0.claim?.sessionID }
        guard case .resolved(let sessionID) = ControlResolve.resolve(target, candidates: candidates, active: nil),
              let row = owned.first(where: { $0.claim?.sessionID == sessionID }), let claim = row.claim else {
            return .done(err("no \(pane.rawValue) pane daemon for session \(target)"))
        }
        if let refusal = LinuxControlZmxError.killRefusal(row) {
            return .done(err(refusal))
        }
        let daemon = row.daemon
        return .worker {
            switch client.killConfirmed(name: daemon) {
            case .killed:
                return .main { AppController.finishZmxKill(claim: claim, daemon: daemon) }
            case .staleSocket:
                return .done(ControlResponse(ok: false, error: "\(daemon) did not confirm the kill; zmx cleaned "
                    + "up a stale socket and the daemon may still be running"))
            case .failed(let reason):
                return .done(ControlResponse(ok: false, error: "could not kill \(daemon): \(reason)"))
            }
        }
    }

    /// The daemon is gone; close its pane if it is still the zmx-backed surface that claimed it.
    private static func finishZmxKill(claim: ZmxPaneClaim, daemon: String) -> ControlStage {
        if let controller = gWindows[claim.windowID],
           let session = controller.store.session(withID: claim.sessionID) {
            let surface = (claim.pane == .left ? session.surface : session.splitSurface) as? GhosttySurface
            if surface?.backedByZmx == true, surface?.claimProcessExit() == true {
                if claim.pane == .left {
                    controller.closePrimaryPane(claim.sessionID, alreadyFinalized: claim.paneIdentity)
                } else {
                    controller.closeSplitPane(claim.sessionID, alreadyFinalized: claim.paneIdentity)
                }
            }
        }
        return .done(ControlResponse(ok: true, result: ControlResult(
            id: claim.sessionID.uuidString, text: "killed \(daemon)", pane: claim.pane.rawValue
        )))
    }

    private func localAttachableSessions(observed: [ZmxSessionRecord], client: LinuxZmxClient) -> ControlResponse {
        let inventory = ControlZmxInventory(restore: restoreStatus(), result: zmxJoin(observed), endpoint: client.endpoint)
        let windows = library.windows.compactMap { entry -> RemoteWindowProjection? in
            guard let controller = gWindows[entry.id],
                  let tree = controller.controlTree(window: nil).result?.tree else { return nil }
            return RemoteWindowProjection(id: entry.id.uuidString, name: entry.name, tree: tree)
        }
        do {
            return ControlResponse(ok: true, result: ControlResult(
                remote: try RemoteTreeMerger.candidates(windows: windows, inventory: inventory)
            ))
        } catch let error as RemoteTreeMerger.MergeError {
            return ControlResponse(ok: false, error: error.message)
        } catch {
            return ControlResponse(ok: false, error: "the session list could not be built")
        }
    }


    func attachRemoteSession(host: String, session remoteID: String,
                             tree: ControlRemoteTree) -> ControlResponse {
        guard let remote = tree.sessions.first(where: { $0.id == remoteID }) else {
            return ControlResponse(ok: false, error: "no attachable session \(remoteID) on \(host)")
        }
        guard let workspace = store.currentWorkspaceID else {
            return ControlResponse(ok: false, error: "no window to attach into")
        }
        let byRole = Dictionary(remote.panes.map { (ZmxPaneRole(controlName: $0.pane), $0.daemon) },
                                uniquingKeysWith: { first, _ in first })
        guard byRole.count == remote.panes.count, let left = byRole[.left] else {
            return ControlResponse(ok: false, error: "\(host) reported panes agterm cannot address")
        }
        let primary: String
        let split: String?
        let leads = (left: ZmxLeadAttachment(claim: true), right: ZmxLeadAttachment(claim: true))
        do {
            primary = try RemoteSession.attachPaneCommand(
                host: host, endpoint: tree.endpoint, daemon: left,
                session: remote.name, pane: .left, lead: leads.left
            )
            split = try byRole[.right].map {
                try RemoteSession.attachPaneCommand(
                    host: host, endpoint: tree.endpoint, daemon: $0,
                    session: remote.name, pane: .right, lead: leads.right
                )
            }
        } catch {
            return ControlResponse(ok: false, error: "\(host) reported a session agterm cannot address")
        }
        guard let created = store.addSession(
            toWorkspace: workspace, cwd: Self.homeCwd, command: primary,
            name: remote.name, wait: true, remoteHost: host
        ) else {
            return ControlResponse(ok: false, error: "could not create the session")
        }
        if let split {
            created.splitInitialCommand = split
            created.splitCommandWait = true
            store.setSplitVisibility(
                created.id, shown: true,
                axis: remote.splitAxis.flatMap(SplitAxis.init(rawValue:)) ?? .leftRight
            )
        }
        var daemons = [created.paneIdentity: left]
        ZmxLeadBook.shared.begin(leads.left, pane: created.paneIdentity)
        if let right = byRole[.right], let local = created.splitPaneIdentity {
            daemons[local] = right
            ZmxLeadBook.shared.begin(leads.right, pane: local)
        }
        let origin = RemoteBinding.Origin(host: host, endpoint: tree.endpoint, sessionName: remote.name)
        store.bindRemote(RemoteBinding(remoteSessionID: remote.id, daemonsByLocalPane: daemons,
                                       presentationVersion: tree.presentation, origin: origin),
                         forSession: created.id)
        reconcile()
        sessionFocusTarget(for: created.id, wantSplit: created.splitFocused)?.grabFocus()
        return ControlResponse(ok: true, result: ControlResult(id: created.id.uuidString))
    }
}
