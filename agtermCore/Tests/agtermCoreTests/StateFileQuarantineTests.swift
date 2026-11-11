import Foundation
import Testing
@testable import agtermCore

final class StateFileQuarantineTests {
    private let directory: URL

    init() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent("agterm-quarantine-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    deinit {
        try? FileManager.default.removeItem(at: directory)
    }

    private var fileURL: URL { directory.appendingPathComponent("state.json") }

    // a launch that keeps failing must not fill the disk: only the newest `keepPerFile` copies survive,
    // even when several land within one millisecond
    @Test func preservedCopiesAreCappedKeepingTheNewest() throws {
        let store = PersistenceStore(directory: directory, fileName: "state.json")
        for attempt in 0..<(StateFileQuarantine.keepPerFile + 3) {
            try Data("broken \(attempt)".utf8).write(to: fileURL)
            #expect(store.load() == Snapshot())
            #expect(!FileManager.default.fileExists(atPath: fileURL.path))
        }
        let kept = try StateFileQuarantine.preservedCopies(of: fileURL).map { try String(decoding: Data(contentsOf: $0), as: UTF8.self) }
        #expect(StateFileQuarantine.keepPerFile == 3)
        #expect(kept == ["broken 3", "broken 4", "broken 5"])
        #expect(StateFileQuarantine.preservedNames(in: directory)?.count == StateFileQuarantine.keepPerFile)
    }

    // a clock that went backwards must not make the newest copy the first one pruned
    @Test func aCopyMadeUnderAnEarlierClockStillSortsNewest() throws {
        let late = Date(timeIntervalSince1970: 2_000_000_000)
        for (index, now) in [late, late, late, Date(timeIntervalSince1970: 1_000_000_000)].enumerated() {
            try Data("copy \(index)".utf8).write(to: fileURL)
            #expect(StateFileQuarantine.preserve(fileURL, reason: .unreadable("test"), now: now) != nil)
        }
        let kept = try StateFileQuarantine.preservedCopies(of: fileURL).map { try String(decoding: Data(contentsOf: $0), as: UTF8.self) }
        #expect(kept == ["copy 1", "copy 2", "copy 3"])
    }

    @Test func preservedNamesKeepNoJSONExtension() throws {
        try Data("{".utf8).write(to: fileURL)
        let copy = try #require(StateFileQuarantine.preserve(fileURL, reason: .unreadable("test")))
        #expect(copy.lastPathComponent.hasPrefix("state.json.unreadable-"))
        #expect(copy.pathExtension != "json")
        #expect(copy.deletingLastPathComponent().standardizedFileURL.path == directory.standardizedFileURL.path)
    }

    @Test func missingFileOrDirectoryIsNeverMoved() throws {
        #expect(StateFileQuarantine.load(fileURL) { try JSONDecoder().decode(Snapshot.self, from: $0) } == nil)
        try FileManager.default.createDirectory(at: fileURL, withIntermediateDirectories: false)
        #expect(StateFileQuarantine.load(fileURL) { try JSONDecoder().decode(Snapshot.self, from: $0) } == nil)
        #expect(FileManager.default.fileExists(atPath: fileURL.path))
        #expect(StateFileQuarantine.preservedNames(in: directory) == [])
    }

    @Test func aReadableFileIsReturnedAndLeftInPlace() throws {
        let snapshot = Snapshot(workspaces: [WorkspaceSnapshot(id: UUID(), name: "kept", sessions: [])])
        try PersistenceStore(directory: directory, fileName: "state.json").save(snapshot)
        #expect(PersistenceStore(directory: directory, fileName: "state.json").load() == snapshot)
        #expect(StateFileQuarantine.preservedNames(in: directory) == [])
    }

    @Test func reasonNamesNewerOlderAndCorruptFiles() {
        let error = StateFileQuarantine.VersionMismatch(found: 0)
        func reason(_ json: String) -> StateFileQuarantine.Reason {
            StateFileQuarantine.reason(for: Data(json.utf8), supportedVersion: 1, error: error)
        }
        #expect(reason(#"{"version":7}"#) == .newerVersion(found: 7, supported: 1))
        #expect(reason(#"{"version":7}"#).summary.contains("written by a newer version"))
        #expect(reason(#"{"version":0}"#) == .unsupportedVersion(found: 0, supported: 1))
        // truncated JSON, and a matching version that still failed to decode, are corruption
        #expect(reason("{") == .unreadable(String(describing: error)))
        #expect(reason(#"{"version":1,"x":"#) == .unreadable(String(describing: error)))
    }
}

/// The per-window variants that do not fit `WindowLibraryTests`' file-length budget.
@MainActor
final class WindowFileQuarantineTests {
    private let directory: URL

    init() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent("agterm-window-quarantine-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory.appendingPathComponent("windows"),
                                                withIntermediateDirectories: true)
    }

    deinit {
        try? FileManager.default.removeItem(at: directory)
    }

    private func windowStore(_ id: UUID) -> PersistenceStore {
        PersistenceStore(directory: directory.appendingPathComponent("windows"), fileName: "\(id.uuidString).json")
    }

    private func windowFileURL(_ id: UUID) -> URL {
        directory.appendingPathComponent("windows/\(id.uuidString).json")
    }

    private func writeOpenIndex(_ id: UUID) throws {
        try JSONEncoder().encode(WindowsIndex(frontmost: id, windows: [WindowEntry(id: id, name: "w", isOpen: true)]))
            .write(to: directory.appendingPathComponent("windows.json"))
    }

    // a higher format version is not corruption, but this build would overwrite it all the same
    @Test func newerVersionPerWindowFileIsPreservedNotOverwritten() throws {
        let id = UUID()
        var future = Snapshot(workspaces: [WorkspaceSnapshot(id: UUID(), name: "from the future", sessions: [])])
        future.version = Snapshot.currentVersion + 1
        let original = try JSONEncoder().encode(future)
        try original.write(to: windowFileURL(id))
        try writeOpenIndex(id)
        let library = WindowLibrary(directory: directory)
        let store = try #require(library.store(for: id))
        #expect(store.workspaces.isEmpty)

        store.save() // the close path's unconditional save

        #expect(try StateFileQuarantine.preservedCopies(of: windowFileURL(id)).map { try Data(contentsOf: $0) }
            == [original])
        #expect(try windowStore(id).loadChecked() == store.snapshot())
    }

    @Test func missingPerWindowFilePreservesNothing() throws {
        let id = UUID()
        try writeOpenIndex(id)
        let library = WindowLibrary(directory: directory)
        library.store(for: id)?.save()
        #expect(StateFileQuarantine.preservedNames(in: directory.appendingPathComponent("windows")) == [])
    }
}
