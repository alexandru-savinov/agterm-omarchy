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

    private static func zmx(_ pid: Int32, _ daemon: String, holding sockets: Set<UInt64>) -> ZmxIncompatibleReset.ProcessView {
        .init(pid: pid, arguments: ["/opt/agterm/bin/zmx", "attach", daemon], sockets: sockets)
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

    @Test func theDaemonIsTheProcessHoldingTheListeningSocket() {
        let daemon = Self.name(Self.paneA)
        let other = Self.name(Self.paneB)
        let sockets: [ZmxIncompatibleReset.SocketView] = [
            .init(inode: 11, path: Self.directory + "/.s/4100", listening: true),
            .init(inode: 12, path: Self.directory + "/.s/4200", listening: true),
            .init(inode: 13, path: Self.directory + "/" + daemon, listening: false),
            .init(inode: 14, path: "/run/user/1000/agterm-zmx-ffffffffffffffff/.s/4300", listening: true),
        ]
        // clients of the same session hold connected sockets and carry the same arguments
        let processes = [
            Self.zmx(41, daemon, holding: [11]),
            Self.zmx(42, other, holding: [12]),
            Self.zmx(43, daemon, holding: [13, 99]),
            Self.zmx(44, daemon, holding: []),
            Self.zmx(45, daemon, holding: [14]),
        ]
        #expect(ZmxIncompatibleReset.daemonPID(daemon: daemon, socketDirectory: Self.directory, sockets: sockets,
                                               processes: processes) == 41)
        #expect(ZmxIncompatibleReset.daemonPID(daemon: other, socketDirectory: Self.directory, sockets: sockets,
                                               processes: processes) == 42)
    }

    @Test func aSocketBoundUnderTheSessionNameNeedsNoArguments() {
        let daemon = Self.name(Self.paneA)
        let sockets = [ZmxIncompatibleReset.SocketView(inode: 21, path: Self.directory + "/" + daemon, listening: true)]
        let holder = ZmxIncompatibleReset.ProcessView(pid: 51, arguments: ["zmx"], sockets: [21])
        #expect(ZmxIncompatibleReset.daemonPID(daemon: daemon, socketDirectory: Self.directory, sockets: sockets,
                                               processes: [holder]) == 51)
    }

    @Test func twoHoldersOrNoneFindNothing() {
        let daemon = Self.name(Self.paneA)
        let sockets = [
            ZmxIncompatibleReset.SocketView(inode: 31, path: Self.directory + "/.s/1", listening: true),
            ZmxIncompatibleReset.SocketView(inode: 32, path: Self.directory + "/.s/2", listening: true),
        ]
        #expect(ZmxIncompatibleReset.daemonPID(daemon: daemon, socketDirectory: Self.directory, sockets: sockets,
                                               processes: [Self.zmx(61, daemon, holding: [31]),
                                                           Self.zmx(62, daemon, holding: [32])]) == nil)
        #expect(ZmxIncompatibleReset.daemonPID(daemon: daemon, socketDirectory: Self.directory, sockets: sockets,
                                               processes: []) == nil)
    }

    @Test func aDaemonStillListedAfterTheResetIsUnconfirmedEvenWithoutAProcess() throws {
        let candidates: [ZmxIncompatibleReset.Candidate] = [
            .init(paneIdentity: Self.paneA, sessionID: Self.sessionA, daemon: Self.name(Self.paneA)),
            .init(paneIdentity: Self.paneASplit, sessionID: Self.sessionA, daemon: Self.name(Self.paneASplit)),
            .init(paneIdentity: Self.paneB, sessionID: Self.sessionB, daemon: Self.name(Self.paneB)),
        ]
        // paneA ended; paneASplit's process was never found and its daemon is still listed; paneB is gone
        let outcome = ZmxIncompatibleReset.outcome(
            candidates: candidates, daemonPIDs: [Self.name(Self.paneA): 21], survivors: [],
            stillIncompatible: [Self.name(Self.paneASplit)])
        #expect(outcome.panes == LiveReset.PaneCounts(confirmed: 3, killed: 1, gone: 1, skipped: 0))
        #expect(outcome.unconfirmed == [Self.paneASplit])
        #expect(outcome.sessions == LiveReset.SessionCounts(affected: 2, reset: 1, partial: 1, unconfirmed: 1))
        #expect(ZmxIncompatibleReset.notice(outcome: outcome)
            == "Restarted 1 of 2 live sessions left by an older agterm; the others still run")
    }

    @Test func onlyAnUnconfirmedPaneRunsTheHeldMessageInsteadOfAttaching() throws {
        let candidates: [ZmxIncompatibleReset.Candidate] = [
            .init(paneIdentity: Self.paneA, sessionID: Self.sessionA, daemon: Self.name(Self.paneA)),
            .init(paneIdentity: Self.paneB, sessionID: Self.sessionB, daemon: Self.name(Self.paneB)),
        ]
        let outcome = ZmxIncompatibleReset.outcome(candidates: candidates, daemonPIDs: [Self.name(Self.paneA): 21],
                                                   survivors: [], stillIncompatible: [Self.name(Self.paneB)])
        let held = try #require(ZmxIncompatibleReset.unconfirmedPaneCommand(daemon: Self.name(Self.paneB), outcome: outcome))
        let shell = Process()
        shell.executableURL = URL(fileURLWithPath: "/bin/sh")
        shell.arguments = ["-c", held]
        let output = Pipe()
        shell.standardOutput = output
        try shell.run()
        shell.waitUntilExit()
        #expect(shell.terminationStatus == 1)
        #expect(String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
            == ZmxIncompatibleReset.unconfirmedPaneMessage + "\n")
        #expect(ZmxIncompatibleReset.unconfirmedPaneCommand(daemon: Self.name(Self.paneA), outcome: outcome) == nil)
        #expect(ZmxIncompatibleReset.unconfirmedPaneCommand(daemon: Self.name(Self.paneB), outcome: nil) == nil)
    }

    @Test func aSurvivingProcessIsUnconfirmed() {
        let candidate = ZmxIncompatibleReset.Candidate(paneIdentity: Self.paneB, sessionID: Self.sessionB,
                                                       daemon: Self.name(Self.paneB))
        let outcome = ZmxIncompatibleReset.outcome(candidates: [candidate], daemonPIDs: [Self.name(Self.paneB): 22],
                                                   survivors: [22], stillIncompatible: [])
        #expect(outcome.unconfirmed == [Self.paneB])
        #expect(outcome.sessions.reset == 0)
    }

    @Test func aCleanResetNamesEverySession() {
        let candidate = ZmxIncompatibleReset.Candidate(paneIdentity: Self.paneA, sessionID: Self.sessionA,
                                                       daemon: Self.name(Self.paneA))
        let outcome = ZmxIncompatibleReset.outcome(candidates: [candidate], daemonPIDs: [Self.name(Self.paneA): 21],
                                                   survivors: [], stillIncompatible: [])
        #expect(outcome.sessions == LiveReset.SessionCounts(affected: 1, reset: 1, partial: 0, unconfirmed: 0))
        #expect(ZmxIncompatibleReset.notice(outcome: outcome) == "Restarted 1 live session left by an older agterm")
    }

    @Test func nothingToResetShowsNoNotice() {
        let outcome = ZmxIncompatibleReset.outcome(candidates: [], daemonPIDs: [:], survivors: [], stillIncompatible: [])
        #expect(ZmxIncompatibleReset.notice(outcome: outcome) == nil)
    }
}
