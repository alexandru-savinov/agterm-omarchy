import CGtk
import Foundation
import agtermCore

@MainActor
extension AppController {
    func attachCompanionPage(_ target: String?, window: String?) -> ControlResponse {
        switch resolveSessionResponse(target) {
        case .failure(let response): return response
        case .success(let id): return gControlServer.attachCompanionPage(id, in: store)
        }
    }

    func createLinuxCompanion(_ target: String?, options: CompanionOptions, window: String?) -> ControlResponse {
        switch resolveSessionResponse(target) {
        case .failure(let response): return response
        case .success(let id):
            switch store.addCompanion(to: id, options: options) {
            case .failure: return err("could not create companion")
            case .success(let companion):
                reconcile(focusActive: options.select && gtk_window_is_active(WIN(windowPointer)) != 0)
                realizeCompanion(companion)
                return companionReceipt(companion)
            }
        }
    }

    func createLinuxPageCompanion(_ target: String?, window: String?, options: ControlSessionOverlayOpenOptions) -> ControlResponse {
        switch resolveSessionResponse(target) {
        case .failure(let response): return response
        case .success(let id):
            guard let source = options.page else { return err("a page companion needs --html or --url") }
            if let failure = LinuxBrowserStore.shared.failure(persistent: options.persistent) {
                return err("session.companion.page: \(failure)")
            }
            let overlay = HtmlOverlay(source: source, navigation: options.navigation, javascript: options.javascript,
                                      chromeless: options.chromeless, persistent: options.persistent, browse: options.browse)
            switch store.addPageCompanion(to: id, overlay: overlay, select: options.follow,
                                         backgroundColor: options.backgroundColor) {
            case .failure: return err("could not create page companion")
            case .success(let companion):
                _ = LinuxHtmlOverlayRegistry.shared.page(for: overlay, controller: self, backgroundColor: options.backgroundColor)
                reconcile(focusActive: options.follow)
                return companionReceipt(companion, pageID: overlay.id)
            }
        }
    }

    func duplicateLinuxCompanion(_ target: String?, window: String?, select: Bool) -> ControlResponse {
        switch resolveSessionResponse(target) {
        case .failure(let response): return response
        case .success(let id):
            guard let source = store.session(withID: id) else { return err("no such session") }
            guard source.content == .terminal else { return err("a page companion has no terminal to duplicate") }
            guard !source.programOverlayVisible else { return err("a program overlay covers the session; nothing was created") }
            let snapshot = gZmxForegroundResolver?.passSnapshot(for: [source], timeout: nil)
            func plan(_ pane: OverlayPane) -> Result<DuplicateReplay.Plan, DuplicateReplay.Refusal> {
                if let cover = source.paneOverlay(pane), cover.html == nil && !cover.hidden {
                    return .failure(.init(pane: pane.rawValue, reason: .replay(.unreadable)))
                }
                guard let surface = (pane == .left ? source.surface : source.splitSurface) as? GhosttySurface,
                      surface.isRealized else {
                    return .failure(.init(pane: pane.rawValue, reason: .replay(.unreadable)))
                }
                return DuplicateReplay.resolve(surface.replayObservation(snapshot: snapshot), pane: pane.rawValue,
                                               fallbackDirectory: source.cwd(for: pane == .left ? .left : .right),
                                               shell: surface.loginShellName, denylist: restoreDenylist())
            }
            let left: DuplicateReplay.Plan
            let right: DuplicateReplay.Plan?
            switch plan(.left) {
            case .failure(let failure): return err(failure.message)
            case .success(let value): left = value
            }
            if source.hasSplit {
                switch plan(.right) {
                case .failure(let failure): return err(failure.message)
                case .success(let value): right = value
                }
            } else { right = nil }
            switch store.duplicateAsCompanion(of: id, left: left, right: right, axis: source.splitAxis,
                                              ratio: source.splitRatio, select: select) {
            case .failure: return err("could not duplicate companion")
            case .success(let copy):
                reconcile(focusActive: select)
                realizeCompanion(copy)
                return companionReceipt(copy)
            }
        }
    }

    func setSessionExpansion(_ target: String?, window: String?, expanded: Bool) -> ControlResponse {
        switch resolveSessionResponse(target) {
        case .failure(let response): return response
        case .success(let id):
            guard store.setSessionExpanded(id, expanded: expanded) else { return err("session has no companions") }
            sidebarRuntime.transientParentIDs.remove(id)
            syncSidebar()
            return ok(id)
        }
    }

    private func companionReceipt(_ companion: Session, pageID: UUID? = nil) -> ControlResponse {
        ControlResponse(ok: true, result: ControlResult(id: companion.id.uuidString, pageID: pageID?.uuidString,
                                                       parent: companion.parentID?.uuidString))
    }

    /// A background companion must start its shell before a remote viewer can attach to it.
    private func realizeCompanion(_ companion: Session) {
        surfaces[companion.id]?.realizeWidgetIfNeeded()
        splitSurfaces[companion.id]?.realizeWidgetIfNeeded()
    }

    func newCompanionFromGUI(duplicate: Bool = false, target: UUID? = nil) {
        let id = target ?? store.selectedSessionID
        if let id, store.session(withID: id)?.remoteHost != nil {
            return beginRemoteCompanionFromGUI(id, duplicate: duplicate)
        }
        let response = duplicate ? duplicateLinuxCompanion(id?.uuidString, window: nil, select: true)
            : createLinuxCompanion(id?.uuidString, options: CompanionOptions(), window: nil)
        if !response.ok, let message = response.error {
            _ = openHud(nil, window: nil, spec: HudSpec(message: message, hideAfter: 8))
        }
    }

    /// `session.companion.convert`: the target moves under a fresh shell opened in its place
    /// (`AppStore.convertToCompanion`); the reply names the target and its new parent.
    func convertLinuxCompanion(_ target: String?) -> ControlResponse {
        switch resolveSessionResponse(target) {
        case .failure(let response): return response
        case .success(let id):
            switch store.convertToCompanion(id) {
            case .failure(let refusal): return err(refusal.message)
            case .success(let parent):
                reconcile(focusActive: false)
                return ControlResponse(ok: true, result: ControlResult(id: id.uuidString, parent: parent.id.uuidString))
            }
        }
    }

    /// Convert to Companion from the GUI, through the control seam; a refusal shows the way a companion one does.
    func convertCompanionFromGUI() {
        guard let id = store.selectedSessionID else { return }
        let response = convertLinuxCompanion(id.uuidString)
        if !response.ok, let message = response.error {
            _ = openHud(nil, window: nil, spec: HudSpec(message: message, hideAfter: 8))
        }
    }

    /// Remote windows (Open Remote Window…, New Remote Session…, Detached Sessions…) are hosted only by the macOS
    /// app; a chord bound to one says so rather than doing nothing.
    func refuseRemoteWindowAction(_ title: String) {
        _ = openHud(nil, window: nil, spec: HudSpec(message: "\(title) is not available in agterm-linux", hideAfter: 8))
    }
}
