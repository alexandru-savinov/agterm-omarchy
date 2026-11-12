import Foundation
import agtermCore

private enum LinuxLeadPaneSource {
    case surface
    case daemon(String, LinuxZmxClient)
    case refused(String)
}

@MainActor
extension AppController {
    private func leadPaneSource(_ surface: GhosttySurface, viewport: Bool = false) -> LinuxLeadPaneSource {
        let pane = UUID(uuidString: surface.paneToken)
        let covered = ZmxLeadBook.shared.covered(pane: pane)
        guard covered || ZmxLeadBook.shared.role(pane: pane) != nil else { return .surface }
        guard surface.backedByZmx, let pane, let client = gZmxClient else {
            return covered
                ? .refused("pane is in use on its origin host; take the lead to drive it from here")
                : .surface
        }
        return !covered && viewport ? .surface : .daemon(ZmxSupport.daemonName(for: pane), client)
    }

    func coveredPaneRefusal(_ surface: GhosttySurface) -> ControlResponse? {
        guard ZmxLeadBook.shared.covered(pane: UUID(uuidString: surface.paneToken)) else { return nil }
        return err("pane is covered while another host leads it; take the lead first (session lead)")
    }

    /// Reads a lead-covered pane through its zmx daemon. The read is handed to the control worker, and
    /// `pane` is the role reported in the reply.
    func leadText(_ surface: GhosttySurface, session: UUID, all: Bool, lines: Int?, pane: String?) -> ControlResponse? {
        switch leadPaneSource(surface, viewport: !all && lines == nil) {
        case .surface: return nil
        case .refused(let reason): return err(reason)
        case .daemon(let name, let client):
            return ControlStageHandoff.handOff(LinuxLeadPaneIO.textStage(
                client: client, name: name, all: all, lines: lines, reply: ControlResult(id: session.uuidString, pane: pane)))
        }
    }

    func leadCursor(_ surface: GhosttySurface, controlID: String) -> ControlResponse? {
        switch leadPaneSource(surface) {
        case .surface: return nil
        case .refused(let reason): return err(reason)
        case .daemon(let name, let client):
            return ControlStageHandoff.handOff(LinuxLeadPaneIO.cursorStage(client: client, name: name, controlID: controlID))
        }
    }

    /// Types into a lead-covered pane through its zmx daemon on the control worker; the attention-status
    /// update comes back to the main thread afterwards.
    func leadType(_ text: String, surface: GhosttySurface, session: UUID, pane: String) -> ControlResponse? {
        if RemoteReconnectBook.shared.waiting(pane: UUID(uuidString: surface.paneToken)) {
            return err("pane is waiting to reconnect")
        }
        switch leadPaneSource(surface) {
        case .surface: return nil
        case .refused(let reason): return err(reason)
        case .daemon(let name, let client):
            let token = surface.paneToken
            return ControlStageHandoff.handOff(LinuxLeadPaneIO.typeStage(client: client, name: name, text: text) { [self] in
                if !text.isEmpty, let role = store.session(withID: session)?.paneRole(forToken: token) {
                    clearAttentionStatus(session, pane: role, keystroke: InterruptKeystroke.classify(text: text))
                }
                return ControlResponse(ok: true, result: ControlResult(id: session.uuidString, pane: pane))
            })
        }
    }
}

/// The zmx half of the lead-pane commands, as stages whose process calls run on the control worker.
enum LinuxLeadPaneIO {
    /// Keeps one request's text and its paced final Return together, as the main thread used to, so two
    /// concurrent `session type` calls cannot interleave their bytes in a pane.
    private static let typing = LeadTypingLock()

    /// `reply` carries the id and pane of the answer; the read fills in its text.
    static func textStage(client: LinuxZmxClient, name: String, all: Bool, lines: Int?,
                          reply: ControlResult) -> ControlStage {
        .worker {
            guard let screen = client.screen(name: name, all: all || lines != nil) else {
                return .done(ControlResponse(ok: false, error: "failed to read surface buffer"))
            }
            var result = reply
            result.text = lines.map(screen.lastLines) ?? screen.text
            return .done(ControlResponse(ok: true, result: result))
        }
    }

    static func cursorStage(client: LinuxZmxClient, name: String, controlID: String) -> ControlStage {
        .worker {
            guard let screen = client.screen(name: name, all: false) else {
                return .done(ControlResponse(ok: false, error: "failed to read cursor position"))
            }
            return .done(ControlResponse(ok: true, result: ControlResult(
                id: controlID, cursor: ControlCursor(column: screen.cursorColumn))))
        }
    }

    /// `finish` runs on the main thread once the daemon accepted every byte.
    ///
    /// A request can wait on the typing lock while another one types. If `stop()` begins during that wait,
    /// it types nothing and answers with the shutdown reply. A request that already started typing still
    /// sends its final Return, so its bytes stay whole.
    static func typeStage(client: LinuxZmxClient, name: String, text: String,
                          finish: @escaping @MainActor @Sendable () -> ControlResponse) -> ControlStage {
        .stoppableWorker { stopped in
            let paced = KeystrokeSegments.paced(text)
            let bytes = KeystrokeSegments.ptyBytes(paced.head)
            typing.lock()
            defer { typing.unlock() }
            guard !stopped() else { return .done(MainReply.shutdown) }
            guard bytes.isEmpty || client.type(name: name, bytes: bytes) else {
                return .done(ControlResponse(ok: false, error: "the pane's zmx daemon did not accept the input"))
            }
            if paced.pacedReturn {
                Thread.sleep(forTimeInterval: KeystrokeSegments.submitGap)
                guard client.type(name: name, bytes: [0x0D]) else {
                    return .done(ControlResponse(ok: false, error: "the pane's zmx daemon did not accept the final Return"))
                }
            }
            return .main { .done(finish()) }
        }
    }
}

private final class LeadTypingLock: @unchecked Sendable {
    private let base = NSLock()
    func lock() { base.lock() }
    func unlock() { base.unlock() }
}
