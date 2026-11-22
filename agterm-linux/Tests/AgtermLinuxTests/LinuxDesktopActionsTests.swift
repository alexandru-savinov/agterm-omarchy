import Testing
@testable import AgtermLinux

@Suite("Linux desktop launcher actions")
struct LinuxDesktopActionsTests {
    @Test("all declared launcher action arguments reach the matching action")
    func actionArguments() {
        for action in LinuxDesktopAction.allCases {
            #expect(LinuxApplicationInvocation.parse(
                arguments: ["--desktop-action=\(action.rawValue)"]
            ) == .desktopAction(action))
        }
    }

    @Test("ordinary activation and open paths retain their existing command-line shapes")
    func ordinaryInvocations() {
        #expect(LinuxApplicationInvocation.parse(arguments: []) == .activate)
        #expect(LinuxApplicationInvocation.parse(arguments: ["/tmp/one", "relative/two"])
            == .open(["/tmp/one", "relative/two"]))
        #expect(LinuxApplicationInvocation.parse(arguments: ["--", "-leading-dash"])
            == .open(["-leading-dash"]))
        #expect(LinuxApplicationInvocation.parse(arguments: ["--"]) == .activate)
    }

    @Test("the Omarchy TUI launcher is accepted")
    func omarchyTUILaunch() {
        let payload = ["btop", "a b", "", "--title=literal", "--desktop-action=new-window", "$HOME", "a'b"]
        let parsed = LinuxApplicationInvocation.parse(arguments: [
            "--app-id=org.omarchy.7z", "--title=Monitor", "--working-directory=tools", "-e",
        ] + payload)
        guard case .terminal(let launch) = parsed else {
            Issue.record("Omarchy TUI rejected: \(parsed)")
            return
        }
        #expect(launch == LinuxTerminalLaunch(directory: "tools", title: "Monitor", appID: "org.omarchy.7z", command: payload))
        #expect(launch.isEphemeral)
        #expect(LinuxApplicationInvocation.parse(arguments: ["-e", "btop"]) == .terminal(.init(command: ["btop"])))
        #expect(LinuxApplicationInvocation.parse(arguments: ["--app-id=TUI.float"]) == .terminal(.init(appID: "TUI.float")))
        #expect(LinuxApplicationInvocation.parse(arguments: ["-e"]) == .invalid("-e requires a program"))
    }

    @Test func appIDWithoutCommandIsEphemeral() {
        let parsed = LinuxApplicationInvocation.parse(arguments: ["--app-id=TUI.float"])
        guard case .terminal(let launch) = parsed else {
            Issue.record("Missing app-ID launch: \(parsed)")
            return
        }
        #expect(launch.isEphemeral)
        #expect(launch.command == nil)
    }

    @Test func oddAppIDsKeepTheOmarchyPrefix() {
        let parsed = LinuxApplicationInvocation.parse(arguments: ["--app-id=org.omarchy.g++"])
        #expect(parsed == .terminal(.init(appID: "org.omarchy.g__")))
    }

    @Test func launcherDirectoryNormalizesParentComponents() {
        let launch = LinuxTerminalLaunch(directory: "sub/../target")
        let cwd = launch.workingDirectory(callerCwd: "/caller", fallback: "/home", isDirectory: { _ in true })
        #expect(cwd == "/caller/target")
    }

    @Test func launcherDirectoryUsesTheCallerAndFallsBackForMissingPaths() {
        let absolute = "/chosen"
        let caller = "/forwarded"
        let fallback = "/default"
        let valid = Set([caller, absolute, "\(caller)/relative"])
        let chosen = LinuxTerminalLaunch(directory: absolute)
        let relative = LinuxTerminalLaunch(directory: "relative", title: "Project")
        let missing = LinuxTerminalLaunch(directory: "/missing")
        #expect(!relative.isEphemeral)
        #expect(LinuxApplicationInvocation.parse(arguments: ["--working-directory=relative", "--title=Project"]) == .terminal(relative))
        #expect(chosen.workingDirectory(callerCwd: caller, fallback: fallback, isDirectory: valid.contains) == absolute)
        #expect(relative.workingDirectory(callerCwd: caller, fallback: fallback, isDirectory: valid.contains) == "\(caller)/relative")
        #expect(missing.workingDirectory(callerCwd: caller, fallback: fallback, isDirectory: valid.contains) == caller)
        #expect(missing.workingDirectory(callerCwd: nil, fallback: fallback, isDirectory: valid.contains) == fallback)
    }

    @Test("invalid launcher actions and mixed invocations fail closed")
    func invalidInvocations() {
        #expect(LinuxApplicationInvocation.parse(arguments: ["--desktop-action=unknown"])
            == .invalid("unknown desktop action: unknown"))
        #expect(LinuxApplicationInvocation.parse(
            arguments: ["--desktop-action=new-session", "/tmp/project"]
        ) == .invalid("--desktop-action must be used alone"))
        #expect(LinuxApplicationInvocation.parse(arguments: ["--unknown"])
            == .invalid("unknown option: --unknown"))
    }

    @Test("window-scoped actions are inert behind dashboard, zoom, and picker covers")
    func modalSafety() {
        let windowActions = LinuxDesktopAction.allCases.filter {
            $0 != .newWindow && $0 != .dashboard
        }
        let dashboard = LinuxDesktopActionContext(
            terminalZoomActive: false, dashboardOpen: true, pickerActive: false)
        let zoom = LinuxDesktopActionContext(
            terminalZoomActive: true, dashboardOpen: false, pickerActive: false)
        let picker = LinuxDesktopActionContext(
            terminalZoomActive: false, dashboardOpen: false, pickerActive: true)

        for action in windowActions {
            #expect(!action.isEnabled(in: dashboard), "\(action) ran through Dashboard")
            #expect(!action.isEnabled(in: zoom), "\(action) ran through Terminal Zoom")
            #expect(!action.isEnabled(in: picker), "\(action) ran through a pending picker")
        }
        #expect(!LinuxDesktopAction.dashboard.isEnabled(in: zoom))
        #expect(!LinuxDesktopAction.dashboard.isEnabled(in: picker))
    }

    @Test("Dashboard closes its own grid and New Window bypasses window covers")
    func modalExceptions() {
        let dashboard = LinuxDesktopActionContext(
            terminalZoomActive: false, dashboardOpen: true, pickerActive: false)
        let everyCover = LinuxDesktopActionContext(
            terminalZoomActive: true, dashboardOpen: true, pickerActive: true)

        #expect(LinuxDesktopAction.dashboard.isEnabled(in: dashboard))
        #expect(LinuxDesktopAction.newWindow.isEnabled(in: everyCover))
    }
}
