import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

@MainActor
@Suite("Linux browser profiles are isolated per state directory")
struct LinuxBrowserProfileIsolationTests {
    private static func stateRoot() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("agterm-browser-isolation-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root.standardizedFileURL
    }

    private static func isInside(_ path: URL, _ root: URL) -> Bool {
        path.standardizedFileURL.path.hasPrefix(root.standardizedFileURL.path + "/")
    }

    @Test("the persistent data directory derives from its own state root and is reused")
    func dataDirectoryDerivesFromItsRoot() throws {
        let root = try Self.stateRoot()
        defer { try? FileManager.default.removeItem(at: root) }

        let data = try LinuxBrowserStore.dataDirectory(stateRoot: root)

        let id = try #require(try BrowserProfile(directory: root).existingIdentifier())
        #expect(data.standardizedFileURL == root.appendingPathComponent("browser/\(id.uuidString)").standardizedFileURL)
        #expect(try LinuxBrowserStore.dataDirectory(stateRoot: root) == data)
        #expect(try FileManager.default.contentsOfDirectory(atPath: root.path) == ["browser-profile"])
    }

    @Test("two state roots never resolve to the same or a nested profile path")
    func twoRootsGetSeparatePaths() throws {
        let one = try Self.stateRoot()
        let two = try Self.stateRoot()
        defer {
            try? FileManager.default.removeItem(at: one)
            try? FileManager.default.removeItem(at: two)
        }

        let first = try LinuxBrowserStore.dataDirectory(stateRoot: one)
        let second = try LinuxBrowserStore.dataDirectory(stateRoot: two)

        #expect(first != second)
        #expect(first.lastPathComponent != second.lastPathComponent)
        #expect(Self.isInside(first, one) && !Self.isInside(first, two))
        #expect(Self.isInside(second, two) && !Self.isInside(second, one))
    }

    @Test("an ephemeral page creates no profile, directory, or session")
    func ephemeralNeverTouchesTheStateRoot() throws {
        let root = try Self.stateRoot()
        let missing = root.appendingPathComponent("never-created", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = LinuxBrowserStore()

        #expect(store.failure(persistent: false, stateRoot: root) == nil)
        #expect(store.failure(persistent: false, stateRoot: missing) == nil)

        #expect(store.session == nil)
        #expect(try FileManager.default.contentsOfDirectory(atPath: root.path).isEmpty)
        #expect(!FileManager.default.fileExists(atPath: missing.path))
    }

    @Test("a disposable root's profile path stays out of the default state directory")
    func disposableRootStaysOffTheRealProfile() throws {
        let root = try Self.stateRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        #expect(!Self.isInside(root, PersistenceStore.defaultDirectory))

        let data = try LinuxBrowserStore.dataDirectory(stateRoot: root)

        #expect(Self.isInside(data, root))
        #expect(!Self.isInside(data, PersistenceStore.defaultDirectory))
    }
}
