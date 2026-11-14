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
///
/// A dispatcher entry arm that routes to a family dispatcher (`return dispatchWindowCommand(request)`) does
/// not handle the command by itself: the family switch ends in `default: preconditionFailure`, so a routed
/// command without an arm there traps the app. The scanner follows each route to the switch that finally
/// handles the command, and `everyRoutedCommandReachesAFamilyArm` names any route that ends without an arm.
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

    /// The dispatcher's entry switches; every other command switch in the dispatcher files is a family switch,
    /// reached only through a route from one of these.
    static let entryFunctions = ["dispatch", "dispatchNewParityCommand"]

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

    /// Commands agtermCore's `ControlDispatcher.dispatchSynchronous` answers before any Linux switch runs.
    static func sharedSynchronousCommands() throws -> Set<String> {
        let source = try read(repository.appendingPathComponent("agtermCore/Sources/agtermCore/ControlDispatcher.swift"))
        let start = try #require(source.range(of: "public func dispatchSynchronous("), "dispatchSynchronous not found")
        let end = try #require(source.range(of: "default: return nil", range: start.upperBound..<source.endIndex),
                               "dispatchSynchronous has no default arm")
        var names = Set<String>()
        for line in source[start.upperBound..<end.lowerBound].split(separator: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard trimmed.hasPrefix("case ") || trimmed.hasPrefix(".") else { continue }
            let arms = trimmed.split(separator: ":", maxSplits: 1).first.map(String.init) ?? trimmed
            for token in arms.split(whereSeparator: { $0 == "," || $0 == " " }) where token.hasPrefix(".") {
                names.insert(String(token.dropFirst()))
            }
        }
        return names
    }

    /// Case name → raw value for every case of agtermCore's `Command`.
    static func commands() throws -> [(name: String, raw: String)] {
        let source = try read(repository.appendingPathComponent("agtermCore/Sources/agtermCore/ControlProtocol.swift"))
        return try #require(SourceScan.enumCases(named: "Command", in: source), "enum Command not found")
    }

    /// Every command switch in the `LinuxControlDispatcher*` files.
    static func dispatcherSwitches() throws -> [SourceScan.CommandSwitch] {
        try linuxFiles(prefix: "LinuxControlDispatcher").flatMap { SourceScan.commandSwitches(in: try read($0)) }
    }

    /// Commands that some Linux routing point handles:
    /// - an arm of the dispatcher's entry switches whose route ends in a handling arm (`SourceScan.resolve`);
    /// - an arm of `handleControl`'s inline switch (its `default` is the unsupported answer);
    /// - a `ControlServer` intercept: `if req.cmd == .x {`, a classifier that gates a dispatch call
    ///   (`isRemoteZmxRequest(request) ? dispatchRemoteZmx(request)`, the only handler of `zmx.attach`), or a
    ///   command switch outside `route(for:)`, which only picks the window controller and handles nothing. A
    ///   comparison that only classifies (a predicate gating no dispatch, a `guard`, a ternary on an error
    ///   string) does not count.
    /// An arm whose whole body is `return nil` hands the command on and is not a handler.
    static func handledCommands() throws -> Set<String> {
        let switches = try dispatcherSwitches()
        let functions = switches.map(\.function)
        #expect(Set(functions).count == functions.count,
                "a dispatcher function has more than one command switch: \(functions.sorted())")
        #expect(Set(entryFunctions).isSubset(of: Set(functions)),
                "dispatcher entry switches moved; found \(functions.sorted())")
        var handled = SourceScan.resolve(entries: entryFunctions, switches: switches).handled

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
        // `LinuxControlDispatcher.dispatch` asks the shared core's synchronous dispatcher first.
        let handled = try Self.handledCommands().union(Self.sharedSynchronousCommands())
        let unhandled = names.subtracting(handled)
        #expect(unhandled.subtracting(Self.intentionallyUnsupported).isEmpty,
                "no Linux handler (falls to \"command not yet supported on Linux\"): \(unhandled.subtracting(Self.intentionallyUnsupported).sorted())")
        // The allow-list stays exact: a listed command that gained a handler must leave the list (and its
        // docs caveat), and a typo cannot silently allow nothing.
        #expect(Self.intentionallyUnsupported.subtracting(unhandled).isEmpty,
                "listed as unsupported but handled or not a command: \(Self.intentionallyUnsupported.subtracting(unhandled).sorted())")
    }

    @Test("every command an entry arm routes has an arm in the family switch it is routed to")
    func everyRoutedCommandReachesAFamilyArm() throws {
        let routing = SourceScan.resolve(entries: Self.entryFunctions, switches: try Self.dispatcherSwitches())
        #expect(routing.missing.isEmpty,
                "routed to a family switch with no arm for it (hits preconditionFailure): \(routing.missing.map(\.description))")
        // The resolver must actually follow routes in the real sources, or the check above cannot fire.
        #expect(routing.followed.contains("dispatchWindowCommand"), "route into dispatchWindowCommand not followed")
        #expect(routing.followed.contains("dispatchHtmlOverlayCommand"), "two-level route not followed")
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
                if req.cmd == .predicate || other { start() }
                guard req.cmd == .guarded else { return }
                let isRemote = req.cmd == .classified
                // if req.cmd == .commentedOut { start() }
                let text = "if req.cmd == .quoted { start() }"
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

    @Test("a classifier predicate counts only where it gates a dispatch call")
    func classifierCountsOnlyWhenItGates() {
        let predicates = """
        static func isRemote(_ request: ControlRequest) -> Bool {
            request.cmd == .attach || (request.cmd == .tree && request.args?.host != nil)
        }
        static func isUnused(_ request: ControlRequest) -> Bool { request.cmd == .neverGated }
        """
        // Defined but gating nothing: a predicate, not a handler.
        #expect(SourceScan.commandComparisons(in: predicates).isEmpty)
        let ternary = predicates + "\nfunc perform(_ request: ControlRequest) -> ControlResponse {\n"
            + "    return Self.isRemote(request) ? dispatchRemote(request) : dispatchOnMain(request)\n}\n"
        #expect(SourceScan.commandComparisons(in: ternary) == ["attach", "tree"])
        let ifGate = predicates + "\nfunc perform(_ req: ControlRequest) -> ControlResponse {\n"
            + "    if isRemote(req) { return dispatchRemote(req) }\n    return other(req)\n}\n"
        #expect(SourceScan.commandComparisons(in: ifGate) == ["attach", "tree"])
        // Used as a plain boolean, not as the condition of a dispatch call: still a predicate.
        let plain = predicates + "\nlet remote = Self.isRemote(request) && ready\n"
        #expect(SourceScan.commandComparisons(in: plain).isEmpty)
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

    /// The defect the routing check exists for: the entry arm routes `.zoom` to a family switch that has no
    /// arm for it, so it would hit the family's `preconditionFailure`; `.overlay` does the same one level down.
    static let routedFixture = """
    func dispatch(_ request: ControlRequest) -> ControlResponse? {
        switch request.cmd {
        case .rename, .zoom,
                .minimize:
            return dispatchWindowCommand(request)
        case .overlay:
            return dispatchSurfaceCommand(request)
        case .leaf: return dispatchLeaf(request)
        default: return nil
        }
    }
    func dispatchWindowCommand(_ request: ControlRequest) -> ControlResponse {
        switch request.cmd {
        case .rename: return rename()
        case .minimize: return nil
        default: preconditionFailure("unexpected window command")
        }
    }
    func dispatchSurfaceCommand(_ request: ControlRequest) -> ControlResponse {
        switch request.cmd {
        case .overlay: return dispatchOverlayCommand(request)
        default: preconditionFailure("unexpected surface command")
        }
    }
    func dispatchOverlayCommand(_ request: ControlRequest) -> ControlResponse {
        switch request.cmd {
        case .other: return other()
        default: preconditionFailure("unexpected overlay command")
        }
    }
    func dispatchLeaf(_ request: ControlRequest) -> ControlResponse {
        switch request.args?.mode {
        case .a: return a()
        default: return b()
        }
    }
    """

    @Test("an entry route to a family switch without an arm is reported, at every level")
    func routeWithoutFamilyArmIsReported() {
        let routing = SourceScan.resolve(entries: ["dispatch"], switches: SourceScan.commandSwitches(in: Self.routedFixture))
        // .rename reaches an arm; .leaf goes to a function that owns no command switch, so it is a handler;
        // .minimize reaches an arm that only forwards (`return nil`): unhandled, but not a missing arm.
        #expect(routing.handled == ["rename", "leaf"])
        #expect(routing.missing.map(\.description) == [
            "zoom: dispatch -> dispatchWindowCommand",
            "overlay: dispatchSurfaceCommand -> dispatchOverlayCommand",
        ])
        #expect(routing.followed == ["dispatchWindowCommand", "dispatchSurfaceCommand", "dispatchOverlayCommand"])
        // The same fixture with the arms present resolves cleanly: the check is about the arm, not the shape.
        let fixed = Self.routedFixture
            .replacingOccurrences(of: "case .rename: return rename()", with: "case .rename, .zoom: return rename()")
            .replacingOccurrences(of: "case .other: return other()", with: "case .other, .overlay: return other()")
        let fixedRouting = SourceScan.resolve(entries: ["dispatch"], switches: SourceScan.commandSwitches(in: fixed))
        #expect(fixedRouting.handled == ["rename", "zoom", "overlay", "leaf"])
        #expect(fixedRouting.missing.isEmpty)
    }

    @Test("multi-line case lists parse whole, and only the #if branch of a conditional is read")
    func enumAndSwitchParseIsStructural() {
        let enumSource = """
        public enum Command: String, Codable, Sendable {
            case first, second = "second.cmd",
                 third = "third.cmd",   // a comment between items
                 fourth
            #if os(Linux)
            case linuxOnly = "linux.only"
            #elseif os(macOS)
            case macOnly = "mac.only"
            #else
            case otherOnly
            #endif
            case last = "last.cmd"
            var label: String {
                switch self {
                case .first: return "x"
                default: return "y"
                }
            }
        }
        """
        let parsed = SourceScan.enumCases(named: "Command", in: enumSource)
        #expect(parsed?.map(\.name) == ["first", "second", "third", "fourth", "linuxOnly", "last"])
        #expect(parsed?.map(\.raw) == ["first", "second.cmd", "third.cmd", "fourth", "linux.only", "last.cmd"])

        let dispatcher = """
        func dispatch(_ request: ControlRequest) -> ControlResponse? {
            switch request.cmd {
            case .a,
                 .b:
                return handle()
            #if canImport(Glibc)
            case .readBranch: return handle()
            #else
            case .skippedBranch: return handle()
            #endif
            default: return nil
            }
        }
        """
        #expect(SourceScan.commandSwitches(in: dispatcher).flatMap(\.handled) == ["a", "b", "readBranch"])
    }
}

/// A small Swift source scanner: skips comments and string literals (including interpolations and raw
/// strings) so braces, `case` and `:` inside them never count.
///
/// Conditional compilation is read deterministically: every `#if` keeps its first branch (the `#if` branch
/// itself) and drops every `#elseif`/`#else` branch, whatever the condition. Directives sit at line start;
/// dropped text and the directive lines are blanked to spaces (newlines kept), so offsets do not move.
enum SourceScan {
    struct CommandSwitch {
        var function: String
        var arms: [SwitchArm]

        /// Command names of arms whose body is not just `return nil`.
        var handled: [String] { arms.filter { !$0.forwardsOnly }.flatMap(\.names) }
    }

    struct SwitchArm {
        var names: [String]
        /// The whole body (comment lines aside) is `return nil`, handing the command on.
        var forwardsOnly: Bool
        /// Functions the body calls (`name(`), outside comments and strings.
        var calls: Set<String>
    }

    /// A route whose target switch has no arm for the command: the family `default` would trap.
    struct MissingArm: Hashable {
        var command: String
        var from: String
        var to: String
        var description: String { "\(command): \(from) -> \(to)" }
    }

    struct Routing {
        /// Commands of the entry switches whose route ends in a handling arm.
        var handled: Set<String> = []
        /// Routes into a switch with no arm for the command, in discovery order.
        var missing: [MissingArm] = []
        /// Family functions a route was followed into, in first-visit order.
        var followed: [String] = []
    }

    /// Follows each entry-switch command to the switch that finally handles it.
    ///
    /// An arm handles a command when its body is not just `return nil` and either calls no function that owns
    /// a command switch (a leaf handler), or every such function it calls handles the command in turn. A
    /// called command-switch function with no arm naming the command is recorded in `missing`. A route back
    /// into a function already on the path handles nothing.
    static func resolve(entries: [String], switches: [CommandSwitch]) -> Routing {
        var byFunction: [String: CommandSwitch] = [:]
        for block in switches where byFunction[block.function] == nil { byFunction[block.function] = block }
        var routing = Routing()

        func handles(_ command: String, in function: String, from caller: String?, path: [String]) -> Bool {
            guard !path.contains(function), let block = byFunction[function] else { return false }
            if caller != nil, !routing.followed.contains(function) { routing.followed.append(function) }
            let candidates = block.arms.filter { $0.names.contains(command) }
            guard !candidates.isEmpty else {
                if let caller {
                    let gap = MissingArm(command: command, from: caller, to: function)
                    if !routing.missing.contains(gap) { routing.missing.append(gap) }
                }
                return false
            }
            return candidates.contains { arm in
                guard !arm.forwardsOnly else { return false }
                let targets = arm.calls.filter { $0 != function && byFunction[$0] != nil }.sorted()
                return targets.allSatisfy { handles(command, in: $0, from: function, path: path + [function]) }
            }
        }

        for entry in entries {
            guard let block = byFunction[entry] else { continue }
            for command in block.arms.flatMap(\.names) where handles(command, in: entry, from: nil, path: []) {
                routing.handled.insert(command)
            }
        }
        return routing
    }

    /// Every `switch req.cmd {` / `switch request.cmd {`, with its enclosing function name and its top-level arms.
    static func commandSwitches(in source: String) -> [CommandSwitch] {
        let b = activeBranches(Array(source.utf8))
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
                let (found, end) = arms(b, from: open + 1)
                result.append(CommandSwitch(function: function, arms: found))
                i = end
                continue
            }
            i += 1
        }
        return result
    }

    /// Commands a file intercepts with a dispatch call:
    /// - an `if` whose whole condition is `req.cmd == .x` / `request.cmd == .x`;
    /// - a classifier predicate used as the condition of a dispatch call in the same file
    ///   (`P(request) ? handler(request)` or `if P(request) { return handler(`): the commands `P` compares.
    /// A comparison inside a larger condition, a `guard`, an assignment, or a predicate that gates no dispatch
    /// call classifies without handling, and does not count.
    static func commandComparisons(in source: String) -> Set<String> {
        let code = String(decoding: codeOnly(activeBranches(Array(source.utf8))), as: UTF8.self)
        var found = Set(matches(#"\bif\s+(?:req|request)\.cmd\s*==\s*\.(\w+)\s*\{"#, in: code))
        let gates = Set(matches(#"(?:\bSelf\.)?\b(\w+)\((?:req|request)\)\s*\?\s*(?:self\.|Self\.)?\w+\("#, in: code)
            + matches(#"\bif\s+(?:Self\.)?(\w+)\((?:req|request)\)\s*\{\s*return\s+(?:self\.|Self\.)?\w+\("#, in: code))
        for gate in gates {
            guard let signature = code.range(of: "func \(gate)("),
                  let open = code[signature.upperBound...].firstIndex(of: "{") else { continue }
            let bytes = Array(code.utf8)
            let start = code.utf8.distance(from: code.utf8.startIndex, to: open) + 1
            let body = String(decoding: bytes[start..<blockEnd(bytes, from: start)], as: UTF8.self)
            found.formUnion(matches(#"\b(?:req|request)\.cmd\s*==\s*\.(\w+)"#, in: body))
        }
        return found
    }

    /// The first capture group of every match of `pattern`.
    private static func matches(_ pattern: String, in text: String) -> [String] {
        let regex = try! NSRegularExpression(pattern: pattern)
        return regex.matches(in: text, range: NSRange(text.startIndex..., in: text)).compactMap { match in
            Range(match.range(at: 1), in: text).map { String(text[$0]) }
        }
    }

    /// `(case name, raw value)` for each case of `enum <name>: String`, in declaration order. Reads the `case`
    /// declarations at the top level of the enum body as token lists, so a list continued over several lines
    /// (`case a, b,` then `c`) parses whole; nested bodies (`var`, `func`) are skipped.
    static func enumCases(named name: String, in source: String) -> [(name: String, raw: String)]? {
        guard let header = source.range(of: "enum \(name): String") else { return nil }
        let b = activeBranches(Array(source.utf8))
        var i = source.utf8.distance(from: source.utf8.startIndex, to: header.upperBound)
        while i < b.count, b[i] != UInt8(ascii: "{") { i += 1 }
        let end = blockEnd(b, from: i + 1)
        var cases: [(name: String, raw: String)] = []
        var depth = 0
        var j = i + 1
        while j < end {
            if let next = skipTrivia(b, j) { j = next; continue }
            if b[j] == UInt8(ascii: "{") { depth += 1 }
            if b[j] == UInt8(ascii: "}") { depth -= 1 }
            guard depth == 0, word(b, j, "case") else { j += 1; continue }
            j += 4
            while true {
                j = skipSpaceAndComments(b, j)
                let start = j
                while j < end, isIdentifier(b[j]) { j += 1 }
                guard j > start else { return nil }
                let caseName = String(decoding: b[start..<j], as: UTF8.self)
                var raw = caseName
                j = skipSpaceAndComments(b, j)
                if j < end, b[j] == UInt8(ascii: "=") {
                    j = skipSpaceAndComments(b, j + 1)
                    guard j < end, b[j] == UInt8(ascii: "\"") else { return nil }
                    let close = skipString(b, j)
                    guard close - 1 > j, b[close - 1] == UInt8(ascii: "\"") else { return nil }
                    raw = String(decoding: b[(j + 1)..<(close - 1)], as: UTF8.self)
                    j = skipSpaceAndComments(b, close)
                }
                cases.append((caseName, raw))
                guard j < end, b[j] == UInt8(ascii: ",") else { break }
                j += 1
            }
        }
        return cases
    }

    // MARK: - Scanner internals

    private static let space = UInt8(ascii: " ")
    private static let newline = UInt8(ascii: "\n")

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

    /// The source with every `#elseif`/`#else` branch and every directive line blanked; see `SourceScan`.
    private static func activeBranches(_ b: [UInt8]) -> [UInt8] {
        var out = b
        var reading: [Bool] = []   // one entry per open `#if`: whether its current branch is read
        func blank(_ range: Range<Int>) { for k in range where out[k] != newline { out[k] = space } }
        var lineStart = true
        var i = 0
        while i < b.count {
            if b[i] == newline { lineStart = true; i += 1; continue }
            if isSpace(b[i]) { i += 1; continue }
            if lineStart, b[i] == UInt8(ascii: "#"),
               let directive = ["#if", "#elseif", "#else", "#endif"].first(where: { word(b, i, $0) }) {
                var e = i
                while e < b.count, b[e] != newline { e += 1 }
                blank(i..<e)
                switch directive {
                case "#if": reading.append(true)
                case "#endif": _ = reading.popLast()
                default: if !reading.isEmpty { reading[reading.count - 1] = false }
                }
                i = e
                continue
            }
            lineStart = false
            let next = skipTrivia(b, i) ?? i + 1
            if !reading.allSatisfy({ $0 }) { blank(i..<next) }
            i = next
        }
        return out
    }

    /// The source with comments and string literals blanked.
    private static func codeOnly(_ b: [UInt8]) -> [UInt8] {
        var out = b
        var i = 0
        while i < b.count {
            if let next = skipTrivia(b, i) {
                for k in i..<next where out[k] != newline { out[k] = space }
                i = next
                continue
            }
            i += 1
        }
        return out
    }

    private static func skipSpaceAndComments(_ b: [UInt8], _ start: Int) -> Int {
        var j = start
        while j < b.count {
            if isSpace(b[j]) { j += 1; continue }
            if b[j] == UInt8(ascii: "/"), j + 1 < b.count,
               b[j + 1] == UInt8(ascii: "/") || b[j + 1] == UInt8(ascii: "*"), let next = skipTrivia(b, j) {
                j = next
                continue
            }
            break
        }
        return j
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

    /// One `case` arm while scanning: its command names and the byte range of its body.
    private struct ArmRange {
        var names: [String]
        var bodyStart: Int
        var bodyEnd = -1
    }

    /// Scans the arms of a switch body starting just after its `{`: returns the arms and the index after the
    /// closing `}`.
    private static func arms(_ b: [UInt8], from start: Int) -> (arms: [SwitchArm], end: Int) {
        var ranges: [ArmRange] = []
        var depth = 1
        var lineStart = true
        var j = start
        func closeArm(at index: Int) {
            if let last = ranges.indices.last, ranges[last].bodyEnd < 0 { ranges[last].bodyEnd = index }
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
                ranges.append(ArmRange(names: names, bodyStart: colon + 1))
                j = colon + 1
                lineStart = false
                continue
            }
            lineStart = false
            j += 1
        }
        closeArm(at: j)
        let parsed = ranges.map { range -> SwitchArm in
            let body = range.bodyStart..<max(range.bodyStart, range.bodyEnd)
            return SwitchArm(names: range.names, forwardsOnly: forwardsOnly(b, body), calls: calls(b, body))
        }
        return (parsed, min(j + 1, b.count))
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
    private static func forwardsOnly(_ b: [UInt8], _ body: Range<Int>) -> Bool {
        let text = String(decoding: b[body], as: UTF8.self)
        let code = text.split(separator: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty && !$0.hasPrefix("//") }
            .joined(separator: " ")
        return code == "return nil"
    }

    /// Identifiers directly followed by `(` in a body, outside comments and strings.
    private static func calls(_ b: [UInt8], _ body: Range<Int>) -> Set<String> {
        var result = Set<String>()
        var j = body.lowerBound
        while j < body.upperBound {
            if let next = skipTrivia(b, j) { j = next; continue }
            guard isIdentifier(b[j]) else { j += 1; continue }
            let start = j
            while j < body.upperBound, isIdentifier(b[j]) { j += 1 }
            if j < b.count, b[j] == UInt8(ascii: "(") { result.insert(String(decoding: b[start..<j], as: UTF8.self)) }
        }
        return result
    }
}
