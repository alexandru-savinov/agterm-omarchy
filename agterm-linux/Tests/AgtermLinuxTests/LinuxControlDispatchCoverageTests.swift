import Foundation
import Testing
import agtermCore

/// Every control command must reach a real Linux handler, or be listed below as deliberately unsupported.
///
/// `Command` is not `CaseIterable` and the Linux dispatcher needs a live `AppController` (GTK), so this reads
/// the sources instead of calling them: the command list comes from the `Command` enum in agtermCore (every
/// parsed case is round-tripped through `Command(rawValue:)` and its case name, so the parse is the real
/// enum), and the handled set comes from the `switch` statements that route a request on Linux. A new
/// command that only reaches `handleControl`'s `default` arm ("command not yet supported on Linux") fails
/// `everyCommandHasALinuxHandler` until it gets a handler or an entry in `intentionallyUnsupported`.
@Suite("Linux control dispatch covers every command")
struct LinuxControlDispatchCoverageTests {
    /// Commands the Linux build leaves to `handleControl`'s `default` arm, which answers
    /// `command not yet supported on Linux: <cmd>`. Adding a name here is a product decision, and the skill
    /// docs must say Linux refuses it.
    static let intentionallyUnsupported: Set<String> = [
        // `zmx reset --force`: macOS only, documented as such in the skill (SKILL.md, reference.md,
        // examples.md, troubleshooting.md).
        "zmxReset",
        // Test-only hook that simulates a macOS light/dark flip for XCUITest; no CLI verb, absent from the skill.
        "debugAppearance",
    ]

    static let repository = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()   // AgtermLinuxTests
        .deletingLastPathComponent()   // Tests
        .deletingLastPathComponent()   // agterm-linux
        .deletingLastPathComponent()   // repository root

    static var linuxSources: URL {
        repository.appendingPathComponent("agterm-linux/Sources/AgtermLinux", isDirectory: true)
    }

    static func read(_ url: URL) throws -> String { try String(contentsOf: url, encoding: .utf8) }

    static func linuxFiles(prefix: String) throws -> [URL] {
        try FileManager.default.contentsOfDirectory(atPath: linuxSources.path)
            .filter { $0.hasPrefix(prefix) && $0.hasSuffix(".swift") }
            .sorted()
            .map { linuxSources.appendingPathComponent($0) }
    }

    /// Case name → raw value for every case of agtermCore's `Command`.
    static func commands() throws -> [(name: String, raw: String)] {
        let source = try read(repository.appendingPathComponent("agtermCore/Sources/agtermCore/ControlProtocol.swift"))
        return try #require(SourceScan.enumCases(named: "Command", in: source), "enum Command not found")
    }

    /// Commands that some Linux routing point handles:
    /// - an arm of the dispatcher's entry switches (`dispatch` and `dispatchNewParityCommand`; the per-family
    ///   switches below them are only reached through an entry arm, so they do not count on their own);
    /// - an arm of `handleControl`'s inline switch (its `default` is the unsupported answer);
    /// - a `ControlServer` intercept, either `request.cmd == .x` or a command switch outside `route(for:)`,
    ///   which only picks the window controller and handles nothing.
    /// An arm whose whole body is `return nil` hands the command on and is not a handler.
    static func handledCommands() throws -> Set<String> {
        var handled = Set<String>()
        var entryFunctions = Set<String>()
        for file in try linuxFiles(prefix: "LinuxControlDispatcher") {
            let source = try read(file)
            for block in SourceScan.commandSwitches(in: source)
            where ["dispatch", "dispatchNewParityCommand"].contains(block.function) {
                entryFunctions.insert(block.function)
                handled.formUnion(block.handled)
            }
        }
        #expect(entryFunctions == ["dispatch", "dispatchNewParityCommand"],
                "dispatcher entry switches moved; found \(entryFunctions.sorted())")

        let control = try read(linuxSources.appendingPathComponent("AppControllerControl.swift"))
        let inline = SourceScan.commandSwitches(in: control).filter { $0.function == "handleControl" }
        #expect(inline.count == 1, "handleControl's command switch not found")
        for block in inline { handled.formUnion(block.handled) }

        for file in try linuxFiles(prefix: "ControlServer") {
            let source = try read(file)
            handled.formUnion(SourceScan.commandComparisons(in: source))
            for block in SourceScan.commandSwitches(in: source) where block.function != "route" {
                handled.formUnion(block.handled)
            }
        }
        return handled
    }

    @Test("the parsed command list is the real Command enum")
    func parsedCommandsAreReal() throws {
        let commands = try Self.commands()
        #expect(commands.count >= 100, "parsed only \(commands.count) commands")
        #expect(Set(commands.map(\.raw)).count == commands.count, "duplicate raw values parsed")
        for (name, raw) in commands {
            let command = try #require(Command(rawValue: raw), "\(raw) does not decode")
            #expect(String(describing: command) == name, "\(raw) parsed as \(name), enum says \(command)")
        }
        for name in ["tree", "windowMove", "zmxReset", "debugAppearance"] {
            #expect(commands.contains { $0.name == name }, "\(name) missing from the parse")
        }
    }

    @Test("every control command has a Linux handler or is listed as unsupported")
    func everyCommandHasALinuxHandler() throws {
        let names = Set(try Self.commands().map(\.name))
        let handled = try Self.handledCommands()
        let unhandled = names.subtracting(handled)
        #expect(unhandled.subtracting(Self.intentionallyUnsupported).isEmpty,
                "no Linux handler (falls to \"command not yet supported on Linux\"): \(unhandled.subtracting(Self.intentionallyUnsupported).sorted())")
        // The allow-list stays exact: a listed command that gained a handler must leave the list (and its
        // docs caveat), and a typo cannot silently allow nothing.
        #expect(Self.intentionallyUnsupported.subtracting(unhandled).isEmpty,
                "listed as unsupported but handled or not a command: \(Self.intentionallyUnsupported.subtracting(unhandled).sorted())")
    }

    @Test("the unsupported answer and the window.move refusal the docs quote still exist")
    func documentedRefusalsExist() throws {
        let control = try Self.read(Self.linuxSources.appendingPathComponent("AppControllerControl.swift"))
        #expect(control.contains(#"err("command not yet supported on Linux: \(req.cmd.rawValue)")"#))
        // window.move is routed (so it is not in the allow-list) but always refused; the skill documents it.
        #expect(try Self.handledCommands().contains("windowMove"))
        let windows = try Self.read(Self.linuxSources.appendingPathComponent("ControlActions+AppControllerWindows.swift"))
        #expect(windows.contains("window.move is not supported on this platform"))
    }

    // MARK: - Negative arms: the scanner must not count what does not handle a command.

    @Test("the scanner counts only top-level, non-forwarding arms of a command switch")
    func scannerRejectsNonHandlers() {
        let source = #"""
        struct D {
            func dispatch(_ request: ControlRequest) -> ControlResponse? {
                if case .alpha = other { return nil }
                switch request.cmd {
                case .alpha, .beta:
                    let label = "case .notACase: { brace"
                    switch request.args?.mode {
                    case .nested: break
                    default: break
                    }
                    return handle(label, "\(x ? "y" : "z")")
                case .gamma where request.args?.page != nil:
                    return other()
                // case .commented: return nil
                case .forwarded, .alsoForwarded:
                    return nil
                default:
                    return nil
                }
            }
            func route(_ req: ControlRequest) {
                if req.cmd == .delta { start() }
                switch req.cmd {
                case .routed: break
                }
            }
        }
        """#
        let blocks = SourceScan.commandSwitches(in: source)
        #expect(blocks.map(\.function) == ["dispatch", "route"])
        #expect(blocks.first?.handled == ["alpha", "beta", "gamma"])
        #expect(SourceScan.commandComparisons(in: source) == ["delta"])
        #expect(SourceScan.enumCases(named: "Command", in: source) == nil)
    }

    @Test("a command with no handler is reported")
    func coverageFiresOnAMissingHandler() {
        let enumSource = """
        public enum Command: String, Codable, Sendable {
            case tree
            case kept = "kept.cmd", dropped = "dropped.cmd" // trailing comment
        }
        """
        let parsed = SourceScan.enumCases(named: "Command", in: enumSource)
        #expect(parsed?.map(\.name) == ["tree", "kept", "dropped"])
        #expect(parsed?.map(\.raw) == ["tree", "kept.cmd", "dropped.cmd"])
        let dispatcher = """
        func dispatch(_ request: ControlRequest) -> ControlResponse? {
            switch request.cmd {
            case .tree, .kept: return handle()
            default: return nil
            }
        }
        """
        let handled = SourceScan.commandSwitches(in: dispatcher).flatMap(\.handled)
        #expect(Set(parsed?.map(\.name) ?? []).subtracting(handled) == ["dropped"])
    }
}

/// A small Swift source scanner: skips comments and string literals (including interpolations and raw
/// strings) so braces, `case` and `:` inside them never count.
enum SourceScan {
    struct CommandSwitch {
        var function: String
        var handled: [String]
    }

    /// Every `switch req.cmd {` / `switch request.cmd {`, with its enclosing function name and the command
    /// names of its top-level arms whose body is not just `return nil`.
    static func commandSwitches(in source: String) -> [CommandSwitch] {
        let b = Array(source.utf8)
        var result: [CommandSwitch] = []
        var i = 0
        var function = ""
        while i < b.count {
            if let next = skipTrivia(b, i) { i = next; continue }
            if word(b, i, "func") {
                var j = i + 4
                while j < b.count, b[j] == UInt8(ascii: " ") { j += 1 }
                let start = j
                while j < b.count, isIdentifier(b[j]) { j += 1 }
                function = String(decoding: b[start..<j], as: UTF8.self)
                i = j
                continue
            }
            if word(b, i, "switch"), let open = commandSwitchOpen(b, i + 6) {
                let (handled, end) = arms(b, from: open + 1)
                result.append(CommandSwitch(function: function, handled: handled))
                i = end
                continue
            }
            i += 1
        }
        return result
    }

    /// Command names compared directly: `req.cmd == .x` / `request.cmd == .x`.
    static func commandComparisons(in source: String) -> Set<String> {
        let regex = try! NSRegularExpression(pattern: #"\b(?:req|request)\.cmd\s*==\s*\.(\w+)"#)
        let range = NSRange(source.startIndex..., in: source)
        return Set(regex.matches(in: source, range: range).compactMap { match in
            Range(match.range(at: 1), in: source).map { String(source[$0]) }
        })
    }

    /// `(case name, raw value)` for each case of `enum <name>: String`, in declaration order.
    static func enumCases(named name: String, in source: String) -> [(name: String, raw: String)]? {
        guard let header = source.range(of: "enum \(name): String") else { return nil }
        let b = Array(source.utf8)
        var i = source.utf8.distance(from: source.utf8.startIndex, to: header.upperBound)
        while i < b.count, b[i] != UInt8(ascii: "{") { i += 1 }
        let end = blockEnd(b, from: i + 1)
        let body = String(decoding: b[(i + 1)..<end], as: UTF8.self)
        var cases: [(name: String, raw: String)] = []
        for line in body.split(separator: "\n") {
            var text = line.trimmingCharacters(in: .whitespaces)
            guard text.hasPrefix("case ") else { continue }
            if let comment = text.range(of: "//") { text = String(text[..<comment.lowerBound]) }
            for part in text.dropFirst(5).split(separator: ",") {
                let pieces = part.split(separator: "=", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
                let caseName = pieces[0]
                let raw = pieces.count == 2 ? pieces[1].trimmingCharacters(in: CharacterSet(charactersIn: "\"")) : caseName
                cases.append((caseName, raw))
            }
        }
        return cases
    }

    // MARK: - Scanner internals

    private static func isIdentifier(_ c: UInt8) -> Bool {
        (c >= 0x30 && c <= 0x39) || (c >= 0x41 && c <= 0x5A) || (c >= 0x61 && c <= 0x7A) || c == UInt8(ascii: "_")
    }

    private static func isSpace(_ c: UInt8) -> Bool {
        c == UInt8(ascii: " ") || c == UInt8(ascii: "\t") || c == UInt8(ascii: "\n") || c == UInt8(ascii: "\r")
    }

    /// Whether the identifier `w` starts at `i` as a whole word.
    private static func word(_ b: [UInt8], _ i: Int, _ w: String) -> Bool {
        let w = Array(w.utf8)
        guard i + w.count <= b.count, Array(b[i..<(i + w.count)]) == w else { return false }
        if i > 0, isIdentifier(b[i - 1]) || b[i - 1] == UInt8(ascii: ".") { return false }
        return i + w.count == b.count || !isIdentifier(b[i + w.count])
    }

    /// After `switch`, the index of the `{` when the subject is `req.cmd` or `request.cmd`.
    private static func commandSwitchOpen(_ b: [UInt8], _ start: Int) -> Int? {
        var j = start
        while j < b.count, isSpace(b[j]) { j += 1 }
        var subject = j
        while subject < b.count, b[subject] != UInt8(ascii: "{"), b[subject] != UInt8(ascii: "\n") { subject += 1 }
        guard subject < b.count, b[subject] == UInt8(ascii: "{") else { return nil }
        let text = String(decoding: b[j..<subject], as: UTF8.self).trimmingCharacters(in: .whitespaces)
        return text == "req.cmd" || text == "request.cmd" ? subject : nil
    }

    /// Skips a comment or string literal starting at `i`, returning the index after it, or nil if none.
    private static func skipTrivia(_ b: [UInt8], _ i: Int) -> Int? {
        let c = b[i]
        let next = i + 1 < b.count ? b[i + 1] : 0
        if c == UInt8(ascii: "/"), next == UInt8(ascii: "/") {
            var j = i
            while j < b.count, b[j] != UInt8(ascii: "\n") { j += 1 }
            return j
        }
        if c == UInt8(ascii: "/"), next == UInt8(ascii: "*") {
            var j = i + 2
            while j + 1 < b.count, !(b[j] == UInt8(ascii: "*") && b[j + 1] == UInt8(ascii: "/")) { j += 1 }
            return min(j + 2, b.count)
        }
        if c == UInt8(ascii: "#") {
            var hashes = 0
            var j = i
            while j < b.count, b[j] == UInt8(ascii: "#") { hashes += 1; j += 1 }
            guard j < b.count, b[j] == UInt8(ascii: "\"") else { return nil }
            return skipRawString(b, j, hashes: hashes)
        }
        if c == UInt8(ascii: "\"") { return skipString(b, i) }
        return nil
    }

    private static func isTripleQuote(_ b: [UInt8], _ i: Int) -> Bool {
        i + 2 < b.count && b[i] == UInt8(ascii: "\"") && b[i + 1] == UInt8(ascii: "\"") && b[i + 2] == UInt8(ascii: "\"")
    }

    /// `i` is at the opening quote of a (possibly multi-line) string with interpolations.
    private static func skipString(_ b: [UInt8], _ i: Int) -> Int {
        let triple = isTripleQuote(b, i)
        var j = i + (triple ? 3 : 1)
        while j < b.count {
            if b[j] == UInt8(ascii: "\\") {
                if j + 1 < b.count, b[j + 1] == UInt8(ascii: "(") { j = skipInterpolation(b, j + 2); continue }
                j += 2
                continue
            }
            if triple ? isTripleQuote(b, j) : b[j] == UInt8(ascii: "\"") { return j + (triple ? 3 : 1) }
            if !triple, b[j] == UInt8(ascii: "\n") { return j }
            j += 1
        }
        return b.count
    }

    /// `i` is at the opening quote after `hashes` `#`; ends at the quote(s) followed by as many `#`.
    private static func skipRawString(_ b: [UInt8], _ i: Int, hashes: Int) -> Int {
        let triple = isTripleQuote(b, i)
        var j = i + (triple ? 3 : 1)
        while j < b.count {
            if triple ? isTripleQuote(b, j) : b[j] == UInt8(ascii: "\"") {
                var k = j + (triple ? 3 : 1)
                var count = 0
                while k < b.count, b[k] == UInt8(ascii: "#"), count < hashes { count += 1; k += 1 }
                if count == hashes { return k }
            }
            j += 1
        }
        return b.count
    }

    /// `i` is just after `\(`; returns the index after the matching `)`.
    private static func skipInterpolation(_ b: [UInt8], _ i: Int) -> Int {
        var depth = 1
        var j = i
        while j < b.count {
            if let next = skipTrivia(b, j) { j = next; continue }
            if b[j] == UInt8(ascii: "(") { depth += 1 }
            if b[j] == UInt8(ascii: ")") { depth -= 1; if depth == 0 { return j + 1 } }
            j += 1
        }
        return b.count
    }

    /// The index of the `}` closing a block whose `{` is just before `i`.
    private static func blockEnd(_ b: [UInt8], from i: Int) -> Int {
        var depth = 1
        var j = i
        while j < b.count {
            if let next = skipTrivia(b, j) { j = next; continue }
            if b[j] == UInt8(ascii: "{") { depth += 1 }
            if b[j] == UInt8(ascii: "}") { depth -= 1; if depth == 0 { return j } }
            j += 1
        }
        return b.count
    }

    /// One `case` arm of a command switch: its command names and the byte range of its body.
    private struct Arm {
        var names: [String]
        var bodyStart: Int
        var bodyEnd = -1
    }

    /// Scans the arms of a switch body starting just after its `{`: returns the handled command names and
    /// the index after the closing `}`.
    private static func arms(_ b: [UInt8], from start: Int) -> (handled: [String], end: Int) {
        var arms: [Arm] = []
        var depth = 1
        var lineStart = true
        var j = start
        func closeArm(at index: Int) {
            if let last = arms.indices.last, arms[last].bodyEnd < 0 { arms[last].bodyEnd = index }
        }
        while j < b.count {
            if let next = skipTrivia(b, j) { j = next; continue }
            let c = b[j]
            if c == UInt8(ascii: "\n") { lineStart = true; j += 1; continue }
            if isSpace(c) { j += 1; continue }
            if c == UInt8(ascii: "{") { depth += 1 }
            if c == UInt8(ascii: "}") { depth -= 1 }
            if depth == 0 { break }
            let atArm = depth == 1 && lineStart
            if atArm, word(b, j, "default") { closeArm(at: j) }
            if atArm, word(b, j, "case") {
                closeArm(at: j)
                let (names, colon) = label(b, from: j + 4)
                arms.append(Arm(names: names, bodyStart: colon + 1))
                j = colon + 1
                lineStart = false
                continue
            }
            lineStart = false
            j += 1
        }
        closeArm(at: j)
        let handled = arms.flatMap { forwardsOnly(b, $0) ? [] : $0.names }
        return (handled, min(j + 1, b.count))
    }

    /// A `case` label from just after the keyword to the first `:` outside parentheses and strings: the
    /// `.name` patterns before any `where` clause, and the index of that `:`.
    private static func label(_ b: [UInt8], from start: Int) -> (names: [String], colon: Int) {
        var k = start
        var parens = 0
        var bytes: [UInt8] = []
        while k < b.count {
            if let next = skipTrivia(b, k) { k = next; continue }
            if b[k] == UInt8(ascii: "(") { parens += 1 }
            if b[k] == UInt8(ascii: ")") { parens -= 1 }
            if b[k] == UInt8(ascii: ":"), parens == 0 { break }
            bytes.append(b[k])
            k += 1
        }
        var text = String(decoding: bytes, as: UTF8.self)
        if let clause = text.range(of: " where ") { text = String(text[..<clause.lowerBound]) }
        let names = text.split(separator: ",").compactMap { part -> String? in
            let trimmed = part.trimmingCharacters(in: .whitespacesAndNewlines)
            guard trimmed.hasPrefix("."), trimmed.dropFirst().allSatisfy({ $0.isLetter || $0.isNumber }) else { return nil }
            return String(trimmed.dropFirst())
        }
        return (names, k)
    }

    /// Whether an arm's whole body (comment lines aside) is `return nil`, handing the command on.
    private static func forwardsOnly(_ b: [UInt8], _ arm: Arm) -> Bool {
        let body = String(decoding: b[arm.bodyStart..<max(arm.bodyStart, arm.bodyEnd)], as: UTF8.self)
        let code = body.split(separator: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty && !$0.hasPrefix("//") }
            .joined(separator: " ")
        return code == "return nil"
    }
}
