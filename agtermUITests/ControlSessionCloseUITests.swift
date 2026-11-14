import XCTest

@MainActor
final class ControlSessionCloseUITests: ControlAPITestCase {
    // a fresh shell would start on an empty screen, so the marker proves Reopen brought the same one back.
    func testUndoableCloseKeepsTheRunningShellForReopen() throws {
        let created = try sendCommand(#"{"cmd":"session.new","args":{"name":"undoable-probe"}}"#)
        let id = try XCTUnwrap((created["result"] as? [String: Any])?["id"] as? String, "session.new: \(created)")
        let typed = try pollPaneText(target: id, pane: "left", contains: "undoable-42") {
            _ = try sendCommand(typeRequest(text: "echo undoable-$((40+2))\n", target: id, select: false))
        }
        XCTAssertNotNil(typed, "the probe line should run in the new session")

        let closed = try sendCommand(#"{"cmd":"session.close","target":"\#(id)","args":{"undoable":true}}"#)
        XCTAssertEqual(closed["ok"] as? Bool, true, "\(closed)")
        XCTAssertEqual((closed["result"] as? [String: Any])?["undoable"] as? Bool, true, "\(closed)")
        XCTAssertNil(try sessionNodeIfPresent(id: id))

        let listed = try sendCommand(#"{"cmd":"recent.list"}"#)
        let entries = try XCTUnwrap((listed["result"] as? [String: Any])?["recent"] as? [[String: Any]], "\(listed)")
        let entry = try XCTUnwrap(entries.first { ($0["sessionIDs"] as? [String]) == [id] }, "entry for \(id): \(entries)")
        let entryID = try XCTUnwrap(entry["id"] as? String)
        let opened = try sendCommand(#"{"cmd":"recent.open","target":"\#(entryID)"}"#)
        XCTAssertEqual(opened["ok"] as? Bool, true, "\(opened)")

        let restored = try pollPaneText(target: id, pane: "left", contains: "undoable-42", attempts: 1, perAttempt: 10) {}
        XCTAssertNotNil(restored, "the reopened session should still show the probe's output")
    }
}
