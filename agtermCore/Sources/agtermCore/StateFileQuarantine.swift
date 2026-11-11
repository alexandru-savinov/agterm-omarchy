import Foundation

/// Never destroy what could not be read.
///
/// Every state store here recovers an unusable file as an empty default, and the next save atomically
/// replaces it with that default. For a file that EXISTS but cannot be decoded (truncated, hand-edited, or
/// written by a newer build) that replace is the only copy of the user's state going away. `load(_:)`
/// therefore renames such a file to `<name>.unreadable-<UTC stamp>[-n]` in the same directory before the
/// caller gets its default, and logs one line naming the preserved file.
///
/// At most `keepPerFile` preserved copies are kept per file name, so a launch that keeps failing cannot fill
/// the disk: the FIRST copy always stays, since it is the one most likely to hold the user's real state (a
/// hand-edited file with a typo, a tree from a newer build), plus the newest `keepPerFile - 1`; the ones in
/// between are pruned. A preserved copy is never read back: restoring it is the user's call.
public enum StateFileQuarantine {
    /// Preserved copies kept per state file name.
    public static let keepPerFile = 3

    /// The infix of every preserved copy's name. It has no `.` after it, so a preserved `x.json` no longer
    /// has the `json` extension and no directory scan for live state files picks it up.
    public static let marker = ".unreadable-"

    /// What a `decode` closure throws for a well-formed file whose version it does not accept.
    public struct VersionMismatch: Error, Equatable {
        public let found: Int
        public init(found: Int) { self.found = found }
    }

    public enum Reason: Equatable, Sendable {
        case unreadable(String)
        case newerVersion(found: Int, supported: Int)
        case unsupportedVersion(found: Int, supported: Int)

        public var summary: String {
            switch self {
            case .unreadable(let detail):
                return "could not be read or decoded: \(detail)"
            case .newerVersion(let found, let supported):
                return "written by a newer version (format \(found); this build reads \(supported))"
            case .unsupportedVersion(let found, let supported):
                return "unsupported format \(found) (this build reads \(supported))"
            }
        }
    }

    /// Reads and decodes `url`. Nil when the file is absent, or when it existed but `decode` rejected it, in
    /// which case it has been moved aside first. `decode` must throw for a version it does not accept;
    /// `supportedVersion` only lets the log say whether the file came from a newer build.
    public static func load<T>(_ url: URL, supportedVersion: Int? = nil, decode: (Data) throws -> T) -> T? {
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory),
              !isDirectory.boolValue else { return nil }
        let data: Data
        do {
            data = try Data(contentsOf: url)
        } catch {
            preserve(url, reason: .unreadable(String(describing: error)))
            return nil
        }
        do {
            return try decode(data)
        } catch {
            preserve(url, reason: reason(for: data, supportedVersion: supportedVersion, error: error))
            return nil
        }
    }

    /// Why `data` was rejected: a readable `version` field other than `supportedVersion` names the version,
    /// anything else is plain corruption.
    public static func reason(for data: Data, supportedVersion: Int?, error: Error) -> Reason {
        struct VersionProbe: Decodable { let version: Int? }
        if let supportedVersion,
           let found = (try? JSONDecoder().decode(VersionProbe.self, from: data))?.version,
           found != supportedVersion {
            return found > supportedVersion
                ? .newerVersion(found: found, supported: supportedVersion)
                : .unsupportedVersion(found: found, supported: supportedVersion)
        }
        return .unreadable(String(describing: error))
    }

    /// Renames `url` aside and prunes beyond `keepPerFile`, sparing the first copy. Returns the preserved copy, nil
    /// when the rename failed (logged; the original is then still in place).
    @discardableResult
    public static func preserve(_ url: URL, reason: Reason, now: Date = Date()) -> URL? {
        let directory = url.deletingLastPathComponent()
        let base = url.lastPathComponent
        // the new copy must sort after every existing one, or pruning would remove it first: on a stamp
        // collision, or a clock that went backwards, it takes the newest stamp with the next counter
        var stamp = stampFormatter.string(from: now)
        var counter = 0
        if let newest = copies(of: url).last, newest.key.stamp >= stamp {
            stamp = newest.key.stamp
            counter = newest.key.counter + 1
        }
        let name = base + marker + stamp + (counter > 0 ? "-\(counter)" : "")
        let destination = directory.appendingPathComponent(name)
        do {
            try FileManager.default.moveItem(at: url, to: destination)
        } catch {
            NSLog("agterm: %@ %@ but could not be preserved: %@", base, reason.summary, String(describing: error))
            return nil
        }
        // later failures are usually the app's own default re-failing (two builds alternating, a crash
        // loop); dropping the first copy would delete the real state they pushed out
        let all = copies(of: url)
        if all.count > keepPerFile {
            for copy in all[1..<(all.count - (keepPerFile - 1))] { try? FileManager.default.removeItem(at: copy.url) }
        }
        NSLog("agterm: %@ %@; preserved it as %@ and started from an empty default", base, reason.summary,
              destination.path)
        return destination
    }

    /// The preserved copies of `url`, oldest first.
    public static func preservedCopies(of url: URL) -> [URL] {
        copies(of: url).map { $0.url }
    }

    /// Names of every preserved copy in `directory`, sorted. Nil when the directory exists but cannot be
    /// listed; an absent directory holds none.
    public static func preservedNames(in directory: URL) -> [String]? {
        guard FileManager.default.fileExists(atPath: directory.path) else { return [] }
        guard let names = try? FileManager.default.contentsOfDirectory(atPath: directory.path) else { return nil }
        return names.filter { $0.contains(marker) }.sorted()
    }

    private struct SortKey: Comparable {
        let stamp: String
        let counter: Int
        static func < (a: SortKey, b: SortKey) -> Bool { (a.stamp, a.counter) < (b.stamp, b.counter) }
    }

    private static func copies(of url: URL) -> [(url: URL, key: SortKey)] {
        let directory = url.deletingLastPathComponent()
        let prefix = url.lastPathComponent + marker
        let names = (try? FileManager.default.contentsOfDirectory(atPath: directory.path)) ?? []
        return names.compactMap { name -> (url: URL, key: SortKey)? in
            guard name.hasPrefix(prefix) else { return nil }
            let parts = name.dropFirst(prefix.count).split(separator: "-", maxSplits: 1)
            guard let stamp = parts.first else { return nil }
            let counter = parts.count > 1 ? Int(parts[1]) ?? 0 : 0
            return (directory.appendingPathComponent(name), SortKey(stamp: String(stamp), counter: counter))
        }
        .sorted { $0.key < $1.key }
    }

    /// Fixed-width UTC, no `.`, so names sort chronologically and keep no file extension.
    private static var stampFormatter: DateFormatter {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.dateFormat = "yyyyMMdd'T'HHmmssSSS'Z'"
        return formatter
    }
}
