import Foundation
import Testing
@testable import agtermCore

struct ZmxIncompatibleResetTests {
    private static let sessionA = UUID()
    private static let sessionB = UUID()
    private static let paneA = UUID()
    private static let paneASplit = UUID()
    private static let paneB = UUID()
    private static let directory = "/run/user/1000/agterm-zmx-0123456789abcdef"

    private static func claim(_ pane: UUID, session: UUID = sessionA) -> ZmxPaneClaim {
        ZmxPaneClaim(paneIdentity: pane, pane: .left, pendingClose: false, windowID: UUID(), windowName: "w",
                     windowState: .open, workspaceID: nil, workspaceName: "default",
                     sessionID: session, sessionName: "build")
    }

    private static func name(_ pane: UUID) -> String { ZmxSupport.daemonName(for: pane) }

    private static func process(_ pid: Int32, _ arguments: [String], directory: String = directory,
                                terminal: Int64 = 0) -> ZmxIncompatibleReset.ProcessView {
        .init(pid: pid, arguments: arguments, environment: ["HOME=/h", "ZMX_DIR=" + directory], terminal: terminal)
    }

    @Test func parserKeepsTheErrorZmxPrinted() throws {
        let records = try ZmxListParser.parse("name=agterm-x\terr=IncompatibleProtocol\tstatus=unreachable\n")
        #expect(records == [ZmxSessionRecord(name: "agterm-x", clients: nil, error: "IncompatibleProtocol")])
        #expect(records[0].incompatible)
        #expect(!ZmxSessionRecord(name: "agterm-y", clients: nil, error: "Timeout").incompatible)
    }

    @Test func onlyClaimedIncompatibleDaemonsAreCandidates() throws {
        let claims = ZmxClaimWalk(claims: [Self.claim(Self.paneA), Self.claim(Self.paneASplit),
                                           Self.claim(Self.paneB, session: Self.sessionB)], complete: true)
        let records = [
            ZmxSessionRecord(name: Self.name(Self.paneA), clients: nil, error: "IncompatibleProtocol"),
            // a busy daemon is not incompatible: ending it would destroy work a later attach can reach
            ZmxSessionRecord(name: Self.name(Self.paneASplit), clients: nil, error: "Timeout"),
            ZmxSessionRecord(name: Self.name(Self.paneB), clients: 1, leaderPID: 7),
            ZmxSessionRecord(name: Self.name(UUID()), clients: nil, error: "IncompatibleProtocol"),
        ]
        #expect(ZmxIncompatibleReset.candidates(claims: claims, records: records) == [
            .init(paneIdentity: Self.paneA, sessionID: Self.sessionA, daemon: Self.name(Self.paneA)),
        ])
    }

    @Test func theDaemonIsTheTerminalLessProcessForThatNameAndDirectory() {
        let daemon = Self.name(Self.paneA)
        let processes = [
            Self.process(10, ["/opt/agterm/bin/zmx", "attach", daemon], terminal: 34816),
            Self.process(11, ["/opt/agterm/bin/zmx", "attach", daemon]),
            Self.process(12, ["/opt/agterm/bin/zmx", "attach", daemon], directory: "/run/user/1000/other"),
            Self.process(13, ["/opt/agterm/bin/zmx", "attach", Self.name(Self.paneB)]),
            Self.process(14, ["/usr/bin/zsh", "attach", daemon]),
        ]
        #expect(ZmxIncompatibleReset.daemonPID(daemon: daemon, socketDirectory: Self.directory,
                                               processes: processes) == 11)
        #expect(ZmxIncompatibleReset.daemonPID(daemon: daemon, socketDirectory: Self.directory,
                                               processes: processes + [Self.process(15, ["zmx", "a", daemon])]) == nil)
        #expect(ZmxIncompatibleReset.daemonPID(daemon: daemon, socketDirectory: Self.directory, processes: []) == nil)
    }

    @Test func outcomeCountsEndedGoneAndSurvivingDaemonsPerSession() throws {
        let candidates: [ZmxIncompatibleReset.Candidate] = [
            .init(paneIdentity: Self.paneA, sessionID: Self.sessionA, daemon: Self.name(Self.paneA)),
            .init(paneIdentity: Self.paneASplit, sessionID: Self.sessionA, daemon: Self.name(Self.paneASplit)),
            .init(paneIdentity: Self.paneB, sessionID: Self.sessionB, daemon: Self.name(Self.paneB)),
        ]
        let outcome = ZmxIncompatibleReset.outcome(
            candidates: candidates,
            daemonPIDs: [Self.name(Self.paneA): 21, Self.name(Self.paneB): 22],
            survivors: [22])
        #expect(outcome.panes == LiveReset.PaneCounts(confirmed: 3, killed: 1, gone: 1, skipped: 0))
        #expect(outcome.unconfirmed == [Self.paneB])
        #expect(outcome.sessions == LiveReset.SessionCounts(affected: 2, reset: 1, partial: 1, unconfirmed: 1))
        let notice = try #require(ZmxIncompatibleReset.notice(outcome: outcome))
        #expect(notice.hasPrefix("2 live sessions were started by an earlier agterm"))
        #expect(notice.hasSuffix("Some earlier processes may still be running."))
    }

    @Test func nothingToResetShowsNoNotice() {
        let outcome = ZmxIncompatibleReset.outcome(candidates: [], daemonPIDs: [:], survivors: [])
        #expect(ZmxIncompatibleReset.notice(outcome: outcome) == nil)
    }
}
